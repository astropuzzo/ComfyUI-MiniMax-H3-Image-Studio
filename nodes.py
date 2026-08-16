# SPDX-License-Identifier: Unlicense
from __future__ import annotations

import math
import re
from typing import Dict, List, Optional, Sequence, Tuple

import torch
import torch.nn.functional as F

try:
    import comfy.model_management
    import comfy.model_sampling
    import comfy.samplers
    import comfy.nested_tensor
    import comfy.utils
    import node_helpers
except Exception as exc:  # pragma: no cover - only reached outside ComfyUI
    raise RuntimeError(
        "MiniMax H3 Image Studio must be installed inside ComfyUI/custom_nodes. "
        "Update ComfyUI before loading this extension."
    ) from exc


CATEGORY = "MiniMax H3/Image Studio"
CANVAS_MULTIPLE = 32
NATIVE_MAX_PIXELS = 768 * 1344
MEBIPIXEL = 1024 * 1024
REF_IMAGE_SHORT_EDGE = 2048
MAX_REFERENCE_IMAGES = 9
FPS = 24
AUDIO_LATENT_FPS = 40

ASPECT_RATIOS: Dict[str, Tuple[int, int]] = {
    "1:1 square": (1, 1),
    "4:5 portrait": (4, 5),
    "3:4 portrait": (3, 4),
    "2:3 portrait": (2, 3),
    "9:16 portrait": (9, 16),
    "16:9 landscape": (16, 9),
    "3:2 landscape": (3, 2),
    "4:3 landscape": (4, 3),
    "21:9 ultrawide": (21, 9),
}

RECOMMENDED_FRAME_PROFILE = "recommended | 5 frames"
BALANCED_FRAME_PROFILE = "extended quality | 9 frames"
HIGH_FRAME_PROFILE = "high quality | 13 frames"
MAX_QUALITY_FRAME_PROFILE = "maximum quality | 20 frames (slow)"
FRAME_PRESETS: Dict[str, int] = {
    RECOMMENDED_FRAME_PROFILE: 5,
    BALANCED_FRAME_PROFILE: 9,
    HIGH_FRAME_PROFILE: 13,
    MAX_QUALITY_FRAME_PROFILE: 20,
}

RESOLUTION_PROFILES: Dict[str, Optional[float]] = {
    "fast preview | 0.40 MP": 0.40,
    "balanced | 0.70 MP": 0.70,
    "high | 0.90 MP": 0.90,
    "native detail | 0.98 MP": 0.98,
    "high-res | 2.00 MP": 2.00,
    "ultra | 4.00 MP": 4.00,
    "ultra+ | 8.00 MP": 8.00,
    "custom megapixels": None,
}

# sampler, scheduler, steps, video sigma shift, audio sigma shift
# Distilled adapters are not interchangeable: their authors publish different
# sampler recipes. Keep each recipe explicit instead of offering a generic
# "Turbo" switch that can silently apply the wrong schedule.
SAMPLING_PROFILES: Dict[str, Tuple[str, str, int, float, float]] = {
    "base quality | RES 20 steps": ("res_multistep", "simple", 20, 12.0, 3.0),
    "base speed | RES 12 steps": ("res_multistep", "simple", 12, 12.0, 3.0),
    "LightX v0.1 | ER-SDE 4 steps": ("er_sde", "simple", 4, 12.0, 3.0),
    "LightX v0.1 | SA-Solver 4 steps": ("sa_solver", "simple", 4, 12.0, 3.0),
}

# Exact v14 strings and settings remain at the bottom of the combo so older
# workflows load without silently changing their schedule. The generic Turbo
# labels are deprecated because adapter compatibility cannot be guaranteed.
LEGACY_SAMPLING_PROFILES: Dict[str, Tuple[str, str, int, float, float]] = {
    "quality | 20 steps": SAMPLING_PROFILES["base quality | RES 20 steps"],
    "speed | 12 steps": SAMPLING_PROFILES["base speed | RES 12 steps"],
    "turbo | 8 steps (LoRA)": ("res_multistep", "simple", 8, 12.0, 4.0),
    "turbo | 4 steps (LoRA, experimental)": ("res_multistep", "simple", 4, 12.0, 4.0),
}

VIDEO_PROMPT_RE = re.compile(
    r"(?:\b(?:video|animation|timeline|storyboard|fps|seconds?)\b|"
    r"\[(?:\d+(?:\.\d+)?s?\s*[-–]\s*)?\d+(?:\.\d+)?s\]|"
    r"\b(?:camera movement|push[- ]?in|zoom|pan|dolly|cut to|hard cuts?)\b|"
    r"\b(?:overall_soundscape|non_diegetic_music|audio|soundtrack)\s*:)",
    re.IGNORECASE,
)


def _round_to_multiple(value: float, multiple: int) -> int:
    return max(multiple, int(round(value / multiple)) * multiple)


def _fit_area_to_ratio(area: float, ratio: float, multiple: int, cap_pixels: Optional[int]) -> Tuple[int, int]:
    """Fit an area while preserving aspect ratio on H3's resolution grid.

    ComfyUI's Resolution Selector defines one megapixel as 1024**2 pixels and
    rounds both axes independently. If that rounded pair exceeds H3's native
    area cap, search nearby grid pairs together. Reducing only one axis (the old
    implementation) distorted square and portrait canvases.
    """
    ratio = max(1e-6, float(ratio))
    area = max(float(multiple * multiple), float(area))
    target_area = min(area, float(cap_pixels)) if cap_pixels is not None else area
    ideal_w = math.sqrt(target_area * ratio)
    ideal_h = math.sqrt(target_area / ratio)
    width = _round_to_multiple(ideal_w, multiple)
    height = _round_to_multiple(ideal_h, multiple)

    if cap_pixels is None or width * height <= cap_pixels:
        return width, height

    center_w = max(1, int(round(ideal_w / multiple)))
    center_h = max(1, int(round(ideal_h / multiple)))
    candidates = []
    for wi in range(max(1, center_w - 6), center_w + 7):
        for hi in range(max(1, center_h - 6), center_h + 7):
            w = wi * multiple
            h = hi * multiple
            pixels = w * h
            if pixels > cap_pixels:
                continue
            aspect_error = abs(math.log((w / h) / ratio))
            area_error = abs(pixels - target_area) / max(1.0, target_area)
            candidates.append((3.0 * aspect_error + area_error, -pixels, w, h))

    if not candidates:
        # Extreme aspect ratios can place every nearby pair above the area cap.
        # Search the complete feasible grid instead of collapsing to multiple×multiple.
        max_cells = max(1, int(target_area // (multiple * multiple)))
        fallback = []
        for hi in range(1, max_cells + 1):
            ideal_wi = ratio * hi
            max_wi = max(1, max_cells // hi)
            width_cells = {
                max(1, min(max_wi, int(math.floor(ideal_wi)))),
                max(1, min(max_wi, int(round(ideal_wi)))),
                max(1, min(max_wi, int(math.ceil(ideal_wi)))),
                max_wi,
            }
            for wi in width_cells:
                cells = wi * hi
                if cells > max_cells:
                    continue
                w = wi * multiple
                h = hi * multiple
                pixels = w * h
                aspect_error = abs(math.log((w / h) / ratio))
                area_error = abs(pixels - target_area) / max(1.0, target_area)
                fallback.append((3.0 * aspect_error + area_error, -pixels, w, h))
        if not fallback:
            return multiple, multiple
        _, _, width, height = min(fallback)
        return width, height
    _, _, width, height = min(candidates)
    return width, height


def _resize_image(image: torch.Tensor, width: int, height: int, fit_mode: str) -> torch.Tensor:
    """Resize Comfy IMAGE [B,H,W,C] to [B,height,width,3]."""
    image = image[..., :3]
    samples = image.movedim(-1, 1)

    if fit_mode == "stretch":
        out = comfy.utils.common_upscale(samples, width, height, "lanczos", "disabled")
        return out.movedim(1, -1)

    if fit_mode == "crop_center":
        out = comfy.utils.common_upscale(samples, width, height, "lanczos", "center")
        return out.movedim(1, -1)

    # contain_pad: preserve the entire image, resize to fit, then edge-pad.
    src_h, src_w = int(samples.shape[-2]), int(samples.shape[-1])
    scale = min(width / src_w, height / src_h)
    new_w = max(1, int(round(src_w * scale)))
    new_h = max(1, int(round(src_h * scale)))
    resized = F.interpolate(samples, size=(new_h, new_w), mode="bicubic", align_corners=False, antialias=True)
    pad_l = (width - new_w) // 2
    pad_r = width - new_w - pad_l
    pad_t = (height - new_h) // 2
    pad_b = height - new_h - pad_t
    padded = F.pad(resized, (pad_l, pad_r, pad_t, pad_b), mode="replicate")
    return padded.movedim(1, -1).clamp(0.0, 1.0)


def _resolve_frame_count(frame_preset: str) -> int:
    """Resolve one of the supported still-image temporal-context profiles."""
    if frame_preset in FRAME_PRESETS:
        return FRAME_PRESETS[frame_preset]
    raise ValueError(f"Unknown H3 image quality profile: {frame_preset}")


def _decoded_frames_for_latent_t(latent_t: int) -> int:
    """Natural H3 VAE output length for a temporal latent length."""
    latent_t = max(1, int(latent_t))
    if latent_t == 1:
        return 1
    groups, remainder = divmod(latent_t - 2, 5)
    return 5 + groups * 17 + (0, 4, 8, 12, 13)[remainder]


def _latent_t_for_frame_count(frame_count: int) -> Tuple[int, int]:
    """Smallest temporal latent that decodes at least frame_count images."""
    requested = max(1, int(frame_count))
    latent_t = 1
    while _decoded_frames_for_latent_t(latent_t) < requested:
        latent_t += 1
    return latent_t, _decoded_frames_for_latent_t(latent_t)


def _first_stable_edit_frame(images: torch.Tensor, max_side: int = 256) -> Tuple[int, float]:
    """Find the earliest frame where an FL2VA edit has reached its stable plateau."""
    if images.ndim != 4 or images.shape[0] <= 1:
        return 0, 0.0

    x = images[..., :3].movedim(-1, 1).float()
    height, width = x.shape[-2:]
    scale = min(1.0, max_side / max(height, width))
    if scale < 1.0:
        x = F.interpolate(
            x,
            size=(max(16, round(height * scale)), max(16, round(width * scale))),
            mode="bilinear",
            align_corners=False,
            antialias=True,
        )

    change = (x - x[:1]).abs().mean(dim=(1, 2, 3))
    robust_peak = torch.quantile(change[1:], 0.90)
    mature = change >= robust_peak * 0.80

    # Require two consecutive mature frames so a transient spike is not
    # mistaken for the completed edit. Fall back to the strongest change.
    for index in range(1, len(change) - 1):
        if bool(mature[index]) and bool(mature[index + 1]):
            return index, float(change[index].item())
    index = int(torch.argmax(change[1:]).item()) + 1
    return index, float(change[index].item())


def _empty_h3_av_latent(
    width: int,
    height: int,
    length: int,
    batch_size: int = 1,
    output_frames: Optional[int] = None,
    output_frame_index: int = 0,
    output_strategy: str = "fixed",
):
    internal_frames = max(1, int(length))
    latent_t, natural_frames = _latent_t_for_frame_count(internal_frames)
    requested_frames = internal_frames if output_frames is None else max(1, int(output_frames))
    requested_frames = min(requested_frames, natural_frames)
    duration = natural_frames / FPS
    audio_t = max(1, round(duration * AUDIO_LATENT_FPS))
    device = comfy.model_management.intermediate_device()
    video = torch.zeros((batch_size, 24, latent_t, height // 16, width // 16), device=device)
    audio = torch.zeros((batch_size, 32, 2, audio_t), device=device)
    nested = comfy.nested_tensor.NestedTensor((video, audio))
    return {
        "samples": nested,
        "h3_requested_frames": requested_frames,
        "h3_context_frames": internal_frames,
        "h3_natural_frames": natural_frames,
        "h3_output_frame_index": max(0, int(output_frame_index)),
        "h3_output_strategy": str(output_strategy),
    }, requested_frames, natural_frames


def _normalize_prompt(
    mode: str,
    prompt: str,
    optimize_prompt: bool,
    preserve_strength: float,
    reference_count: int = 1,
) -> str:
    prompt = (prompt or "").strip()
    if not optimize_prompt:
        return prompt

    preserve_strength = float(max(0.0, min(1.0, preserve_strength)))
    if preserve_strength >= 0.8:
        preserve = "Strictly preserve identity, pose, composition, perspective, and object geometry."
    elif preserve_strength >= 0.5:
        preserve = "Preserve identity, pose, composition, perspective, and object geometry unless the edit requires a change."
    else:
        preserve = "Keep the source recognizable while applying the requested changes."

    still = (
        "Create one still image. Keep the camera, composition, and subjects fixed across all generated frames. "
        "Do not add motion, cuts, or audio."
    )

    if mode == "text_to_image (FL2VA)":
        return f"{still}\n\nTarget image description: {prompt}"
    if mode == "image_to_image (FL2VA)":
        return (
            f"<Picture 1> is the source image. Apply the requested edit. {still} {preserve}\n\n"
            f"Target edit: {prompt}"
        )
    picture_tags = ", ".join(f"<Picture {index}>" for index in range(1, reference_count + 1))
    reference_word = "reference" if reference_count == 1 else "references"
    verb = "is" if reference_count == 1 else "are"
    return (
        f"{picture_tags} {verb} the ordered {reference_word}. Use each one as specified. "
        f"{still} {preserve}\n\nTarget edit: {prompt}"
    )


def _prompt_warning(prompt: str) -> str:
    if VIDEO_PROMPT_RE.search(prompt or ""):
        return (
            " WARNING: prompt contains motion, timeline, camera, or audio terms. Describe one final still image instead."
        )
    return ""


def _reference_resize(
    image: torch.Tensor,
    generation_width: int,
    generation_height: int,
    reference_size: str,
) -> Tuple[torch.Tensor, int, int]:
    image = image[:1, ..., :3]
    h, w = int(image.shape[1]), int(image.shape[2])
    if reference_size == "max_identity_2048":
        scale = min(1.0, REF_IMAGE_SHORT_EDGE / min(w, h))
    else:
        scale = min(1.0, math.sqrt((generation_width * generation_height) / max(1, w * h)))
    tw = max(CANVAS_MULTIPLE, round(w * scale / CANVAS_MULTIPLE) * CANVAS_MULTIPLE)
    th = max(CANVAS_MULTIPLE, round(h * scale / CANVAS_MULTIPLE) * CANVAS_MULTIPLE)
    resized = _resize_image(image, tw, th, "stretch")
    return resized, tw, th


def _collect_reference_images(
    source_image: torch.Tensor,
    additional_images: Sequence[Optional[torch.Tensor]],
) -> List[torch.Tensor]:
    """Expand IMAGE batches into ordered, single-image REF2VA references."""
    references: List[torch.Tensor] = []
    for image in (source_image, *additional_images):
        if image is None:
            continue
        if not isinstance(image, torch.Tensor) or image.ndim != 4 or image.shape[0] < 1:
            raise ValueError("Every REF2VA reference must be a non-empty ComfyUI IMAGE batch [B,H,W,C].")
        for batch_index in range(int(image.shape[0])):
            references.append(image[batch_index:batch_index + 1])
            if len(references) > MAX_REFERENCE_IMAGES:
                raise ValueError(
                    f"MiniMax H3 REF2VA supports at most {MAX_REFERENCE_IMAGES} reference images. "
                    "Remove extra inputs or reduce the input IMAGE batch."
                )
    return references


def _resolve_source_ratio(
    aspect_ratio: str,
    source_image: Optional[torch.Tensor],
) -> Tuple[float, str]:
    """Resolve a preset/source aspect ratio and fail early when a source is required."""
    if aspect_ratio == "source image":
        if source_image is None:
            raise ValueError(
                'Aspect ratio "source image" requires a connected source_image. '
                "Connect an IMAGE or choose an explicit aspect ratio."
            )
        if not isinstance(source_image, torch.Tensor) or source_image.ndim != 4 or source_image.shape[0] < 1:
            raise ValueError("source_image must be a non-empty ComfyUI IMAGE batch [B,H,W,C].")
        h, w = int(source_image.shape[1]), int(source_image.shape[2])
        if h < 1 or w < 1:
            raise ValueError("source_image has invalid spatial dimensions.")
        return w / h, f"source ratio {w}:{h}"

    rw, rh = ASPECT_RATIOS[aspect_ratio]
    return rw / rh, aspect_ratio


class H3ImageResolution:
    """Calculate a custom H3 canvas size."""

    DESCRIPTION = (
        "Calculates custom dimensions on an H3-compatible grid, with an optional native-area limit."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "aspect_ratio": (
                    ["source image"] + list(ASPECT_RATIOS.keys()) + ["custom dimensions"],
                    {
                        "tooltip": (
                            'Canvas aspect ratio. "source image" requires source_image; custom dimensions uses the '
                            "custom_width/custom_height widgets."
                        )
                    },
                ),
                "megapixels": (
                    "FLOAT",
                    {
                        "default": 1.00, "min": 0.10, "max": 64.00, "step": 0.10,
                        "tooltip": "Target megapixels using ComfyUI's 1 MP = 1024² convention. Ignored for custom dimensions.",
                    },
                ),
                "multiple": (
                    [32, 64],
                    {
                        "default": 32,
                        "tooltip": "Rounds both canvas axes to this H3-compatible grid multiple.",
                    },
                ),
                "native_area_cap": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": (
                            "When enabled, caps total pixels near H3's native 768×1344 area while preserving the requested ratio."
                        ),
                    },
                ),
                "custom_width": (
                    "INT",
                    {
                        "default": 2048, "min": 32, "max": 16384, "step": 32,
                        "tooltip": 'Used only when aspect_ratio is "custom dimensions".',
                    },
                ),
                "custom_height": (
                    "INT",
                    {
                        "default": 2048, "min": 32, "max": 16384, "step": 32,
                        "tooltip": 'Used only when aspect_ratio is "custom dimensions".',
                    },
                ),
            },
            "optional": {
                "source_image": (
                    "IMAGE",
                    {
                        "tooltip": 'Required only when aspect_ratio is "source image".',
                    },
                ),
            },
        }

    RETURN_TYPES = ("INT", "INT", "STRING")
    RETURN_NAMES = ("width", "height", "resolution_info")
    OUTPUT_TOOLTIPS = (
        "Calculated canvas width in pixels, rounded to the selected H3-compatible multiple.",
        "Calculated canvas height in pixels, rounded to the selected H3-compatible multiple.",
        "Human-readable size, aspect-ratio source, pixel-area and native-cap summary.",
    )
    FUNCTION = "calculate"
    CATEGORY = CATEGORY

    def calculate(
        self,
        aspect_ratio: str,
        megapixels: float,
        multiple: int,
        native_area_cap: bool,
        custom_width: int,
        custom_height: int,
        source_image: Optional[torch.Tensor] = None,
        custom_megapixels: Optional[float] = None,
        limit_to_native_area: Optional[bool] = None,
    ):
        # custom_megapixels / limit_to_native_area were accidentally exposed by
        # earlier Advanced Resolution schemas. Keep accepting them so legacy
        # workflows with link-converted widgets remain executable.
        del custom_megapixels, limit_to_native_area

        multiple = int(multiple)
        cap = NATIVE_MAX_PIXELS if native_area_cap else None

        if aspect_ratio == "custom dimensions":
            width = _round_to_multiple(custom_width, multiple)
            height = _round_to_multiple(custom_height, multiple)
            if cap is not None and width * height > cap:
                width, height = _fit_area_to_ratio(cap, width / height, multiple, cap)
            source = "custom"
        else:
            ratio, source = _resolve_source_ratio(aspect_ratio, source_image)
            target_area = float(megapixels) * MEBIPIXEL
            if cap is not None:
                target_area = min(target_area, cap)
            width, height = _fit_area_to_ratio(target_area, ratio, multiple, cap)

        mp = width * height / MEBIPIXEL
        cap_text = "native cap on" if native_area_cap else "oversize experimental"
        oversize_note = (
            " | WARNING: H3-Base is a 768p model; direct oversize does not reproduce the unreleased H3-Regenerate-2K pipeline"
            if not native_area_cap and width * height > NATIVE_MAX_PIXELS else ""
        )
        info = f"{width}×{height} | {mp:.3f} MP (1024²) | {source} | multiple {multiple} | {cap_text}{oversize_note}"
        return width, height, info


class H3ImageResolutionPreset:
    """Calculate an H3 canvas from presets."""

    DESCRIPTION = (
        "Calculates an H3 canvas from an aspect ratio and resolution profile."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "aspect_ratio": (
                    ["source image"] + list(ASPECT_RATIOS.keys()),
                    {
                        "tooltip": 'Choose a preset ratio or use "source image" to copy the connected image ratio.',
                    },
                ),
                "resolution_profile": (
                    list(RESOLUTION_PROFILES.keys()),
                    {
                        "default": "native detail | 0.98 MP",
                        "tooltip": (
                            "Target pixel area. Higher profiles increase VRAM/RAM and decode cost; H3 learned detail "
                            "does not necessarily scale proportionally."
                        ),
                    },
                ),
            },
            "optional": {
                "source_image": (
                    "IMAGE",
                    {"tooltip": 'Required only when aspect_ratio is "source image".'},
                ),
                "custom_megapixels": (
                    "FLOAT",
                    {
                        "default": 2.0, "min": 0.10, "max": 64.0, "step": 0.10,
                        "tooltip": 'Used only when resolution_profile is "custom megapixels".',
                    },
                ),
                "limit_to_native_area": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": "Conservatively caps the result to approximately H3's native 768×1344 pixel area.",
                    },
                ),
            },
        }

    RETURN_TYPES = ("INT", "INT", "STRING")
    RETURN_NAMES = ("width", "height", "resolution_info")
    OUTPUT_TOOLTIPS = (
        "Preset-derived canvas width in pixels, rounded to H3's 32-pixel grid.",
        "Preset-derived canvas height in pixels, rounded to H3's 32-pixel grid.",
        "Human-readable profile, actual megapixels and native-area warning summary.",
    )
    FUNCTION = "calculate"
    CATEGORY = CATEGORY

    def calculate(
        self,
        aspect_ratio: str,
        resolution_profile: str,
        source_image: Optional[torch.Tensor] = None,
        custom_megapixels: float = 2.0,
        limit_to_native_area: bool = False,
    ):
        ratio, source = _resolve_source_ratio(aspect_ratio, source_image)

        target_mp = RESOLUTION_PROFILES[resolution_profile]
        if target_mp is None:
            target_mp = max(0.10, min(64.0, float(custom_megapixels)))
        cap = NATIVE_MAX_PIXELS if limit_to_native_area else None
        width, height = _fit_area_to_ratio(
            target_mp * MEBIPIXEL,
            ratio,
            CANVAS_MULTIPLE,
            cap,
        )
        actual_mp = width * height / MEBIPIXEL
        native_scale = width * height / NATIVE_MAX_PIXELS
        if limit_to_native_area:
            size_note = "native area limiter on"
        elif width * height > NATIVE_MAX_PIXELS:
            size_note = (
                f"UNLOCKED oversize (~{native_scale:.1f}× native pixel area; VRAM and attention cost rise sharply; "
                "detail gain is checkpoint-dependent)"
            )
        else:
            size_note = "within native area"
        profile_note = f"custom {target_mp:.2f} MP" if resolution_profile == "custom megapixels" else resolution_profile
        info = (
            f"{width}×{height} | {actual_mp:.3f} MP (1024²) | {source} | "
            f"{profile_note} | {size_note}"
        )
        return width, height, info


class H3ImagePrepare:
    """Prepare H3 conditioning and its audio-video latent."""

    DESCRIPTION = (
        "Prepares T2I, I2I, or REF2VA conditioning. I2I and REF2VA require a VAE and source image."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "clip": (
                    "CLIP",
                    {"tooltip": "MiniMax H3 Qwen text/vision encoder."},
                ),
                "mode": (
                    [
                        "text_to_image (FL2VA)",
                        "image_to_image (FL2VA)",
                        "reference_edit (REF2VA)",
                    ],
                    {
                        "tooltip": (
                            "Select the H3 conditioning path. T2I and I2I use FL2VA; Reference Edit uses REF2VA."
                        )
                    },
                ),
                "prompt": (
                    "STRING",
                    {
                        "multiline": True, "dynamicPrompts": True, "default": "",
                        "tooltip": "Final still description or edit instruction.",
                    },
                ),
                "width": (
                    "INT",
                    {"default": 1344, "min": 32, "max": 16384, "step": 32, "tooltip": "Output canvas width."},
                ),
                "height": (
                    "INT",
                    {"default": 768, "min": 32, "max": 16384, "step": 32, "tooltip": "Output canvas height."},
                ),
                "frame_preset": (
                    list(FRAME_PRESETS.keys()),
                    {
                        "default": RECOMMENDED_FRAME_PROFILE,
                        "tooltip": (
                            "H3 jointly denoises the entire temporal packet. The complete selected 5-, 9-, 13-, or 20-frame "
                            "profile is decoded for Single Image Output; that node normally emits one selected still "
                            "or the full batch when emit_candidate_batch is enabled."
                        ),
                    },
                ),
                "optimize_prompt": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": "Adds still-image wording and, for edit modes, source-preservation instructions.",
                    },
                ),
                "preserve_strength": (
                    "FLOAT",
                    {
                        "default": 0.75, "min": 0.0, "max": 1.0, "step": 0.05,
                        "tooltip": (
                            "Prompt-language preservation strength for I2I/REF2VA. This is NOT diffusion denoise "
                            "strength and does not change the sampler schedule."
                        ),
                    },
                ),
                "source_fit": (
                    ["crop_center", "contain_pad", "stretch"],
                    {
                        "default": "crop_center",
                        "tooltip": "How source/reference content is fitted to the generation canvas.",
                    },
                ),
                "reference_size": (
                    ["match_generation_area", "max_identity_2048"],
                    {
                        "default": "match_generation_area",
                        "tooltip": (
                            "REF2VA reference encoding size. max_identity_2048 keeps more source resolution when available "
                            "and can cost more memory."
                        ),
                    },
                ),
            },
            "optional": {
                "vae": (
                    "VAE",
                    {
                        "tooltip": (
                            "Required for Image to Image and Reference Edit because source/reference images must be "
                            "encoded. Text to Image does not use it."
                        )
                    },
                ),
                "source_image": (
                    "IMAGE",
                    {
                        "tooltip": (
                            "Required for I2I and REF2VA. If connected in T2I it is ignored and run_info reports that fact."
                        )
                    },
                ),
                "reference_image_2": ("IMAGE", {"tooltip": "Optional REF2VA <Picture 2>; ignored outside Reference Edit."}),
                "reference_image_3": ("IMAGE", {"tooltip": "Optional REF2VA <Picture 3>; ignored outside Reference Edit."}),
                "reference_image_4": ("IMAGE", {"tooltip": "Optional REF2VA <Picture 4>; ignored outside Reference Edit."}),
                "reference_image_5": ("IMAGE", {"tooltip": "Optional REF2VA <Picture 5>; ignored outside Reference Edit."}),
                "reference_image_6": ("IMAGE", {"tooltip": "Optional REF2VA <Picture 6>; ignored outside Reference Edit."}),
                "reference_image_7": ("IMAGE", {"tooltip": "Optional REF2VA <Picture 7>; ignored outside Reference Edit."}),
                "reference_image_8": ("IMAGE", {"tooltip": "Optional REF2VA <Picture 8>; ignored outside Reference Edit."}),
                "reference_image_9": ("IMAGE", {"tooltip": "Optional REF2VA <Picture 9>; ignored outside Reference Edit."}),
            },
        }

    RETURN_TYPES = ("CONDITIONING", "LATENT", "IMAGE", "INT", "STRING", "STRING")
    RETURN_NAMES = ("positive", "h3_latent", "fitted_source", "requested_frames", "optimized_prompt", "run_info")
    OUTPUT_TOOLTIPS = (
        "Positive H3 FL2VA or REF2VA conditioning for the sampler's positive input.",
        "Packed H3 audio/video latent containing the requested temporal image packet.",
        "Source image fitted to the generation canvas; useful for preview and comparison in edit modes.",
        "Number of image frames that Exact Frame Decode should preserve and decode.",
        "Final prompt after optional still-image and source-preservation optimization.",
        "Mode, temporal packet, canvas, checkpoint expectations and recommended selection strategy.",
    )
    FUNCTION = "prepare"
    CATEGORY = CATEGORY

    def prepare(
        self,
        clip,
        mode: str,
        prompt: str,
        width: int,
        height: int,
        frame_preset: str,
        optimize_prompt: bool,
        preserve_strength: float,
        source_fit: str,
        reference_size: str,
        vae=None,
        source_image: Optional[torch.Tensor] = None,
        reference_image_2: Optional[torch.Tensor] = None,
        reference_image_3: Optional[torch.Tensor] = None,
        reference_image_4: Optional[torch.Tensor] = None,
        reference_image_5: Optional[torch.Tensor] = None,
        reference_image_6: Optional[torch.Tensor] = None,
        reference_image_7: Optional[torch.Tensor] = None,
        reference_image_8: Optional[torch.Tensor] = None,
        reference_image_9: Optional[torch.Tensor] = None,
    ):
        width = _round_to_multiple(width, CANVAS_MULTIPLE)
        height = _round_to_multiple(height, CANVAS_MULTIPLE)
        internal_frames = _resolve_frame_count(frame_preset)

        # Preserve the complete selected temporal profile. Single Image Output
        # decides whether to expose one still or every generated candidate.
        output_frames = internal_frames
        dynamic_edit_selection = mode == "image_to_image (FL2VA)" and internal_frames == 20
        output_frame_index = 0
        output_strategy = "first_stable_edit" if dynamic_edit_selection else "fixed"
        latent, requested_frames, natural_frames = _empty_h3_av_latent(
            width,
            height,
            internal_frames,
            output_frames=output_frames,
            output_frame_index=output_frame_index,
            output_strategy=output_strategy,
        )

        additional_references = (
            reference_image_2, reference_image_3, reference_image_4, reference_image_5,
            reference_image_6, reference_image_7, reference_image_8, reference_image_9,
        )
        ignored_notes = []
        if mode != "reference_edit (REF2VA)" and any(image is not None for image in additional_references):
            ignored_notes.append("Additional reference_image_2..9 inputs are connected but ignored outside REF2VA mode.")
        if mode == "text_to_image (FL2VA)" and source_image is not None:
            ignored_notes.append("source_image is connected but ignored in Text to Image mode.")

        references = (
            _collect_reference_images(source_image, additional_references)
            if mode == "reference_edit (REF2VA)" and source_image is not None
            else []
        )
        final_prompt = _normalize_prompt(
            mode, prompt, optimize_prompt, preserve_strength, max(1, len(references))
        )

        black = torch.zeros((1, height, width, 3), dtype=torch.float32)
        fitted_source = black

        if mode == "text_to_image (FL2VA)":
            tokens = clip.tokenize(final_prompt, images=[])
            cond = clip.encode_from_tokens_scheduled(tokens)
            checkpoint_note = "Use an FL2VA checkpoint."

        elif mode == "image_to_image (FL2VA)":
            if source_image is None:
                raise ValueError("Image to Image mode requires source_image.")
            if vae is None:
                raise ValueError("Image to Image mode requires a VAE to encode source_image.")
            fitted_source = _resize_image(source_image[:1], width, height, source_fit)
            tokens = clip.tokenize(final_prompt, images=[fitted_source])
            cond = clip.encode_from_tokens_scheduled(tokens)
            keyframe_latent = vae.encode(fitted_source)
            cond = node_helpers.conditioning_set_values(cond, {
                "minimax_keyframes": [{"resolved_frame_index": 0, "latent": keyframe_latent}],
                "minimax_frame_count": natural_frames,
            })
            checkpoint_note = "Use an FL2VA checkpoint; frame 0 is the exact source anchor."

        else:
            if source_image is None:
                raise ValueError("Reference Edit mode requires source_image as <Picture 1>.")
            if vae is None:
                raise ValueError("Reference Edit mode requires a VAE to encode the reference image(s).")
            fitted_source = _resize_image(references[0], width, height, source_fit)
            ref_mode = "max_identity_2048" if reference_size == "max_identity_2048" else "match_generation_area"
            ref_items = []
            ref_blocks = []
            reference_sizes = []
            for reference_image in references:
                reference, tw, th = _reference_resize(reference_image, width, height, ref_mode)
                ref_items.append({"type": "image", "data": reference})
                ref_blocks.append({
                    "kind": "image",
                    "latent_h": th // 16,
                    "latent_w": tw // 16,
                    "latent": vae.encode(reference),
                })
                reference_sizes.append(f"{tw}x{th}")
            tokens = clip.tokenize(final_prompt, minimax_ref_items=ref_items)
            cond = clip.encode_from_tokens_scheduled(tokens)
            cond = node_helpers.conditioning_set_values(cond, {
                "minimax_refs": ref_blocks,
            })
            checkpoint_note = (
                f"Use a REF2VA checkpoint; {len(references)} ordered reference image(s) encoded "
                f"as {', '.join(reference_sizes)} and exposed as <Picture 1> through <Picture {len(references)}>。"
            )

        if natural_frames > 362:
            trained_note = "beyond the documented 124-362-frame training range"
        elif natural_frames >= 124:
            trained_note = "inside the documented 124-362-frame training range"
        else:
            trained_note = "short experimental temporal packet chosen to reduce image-mode compute"
        decode_note = (
            f"exact {requested_frames}-frame batch"
            if requested_frames == natural_frames
            else f"temporal latent naturally decodes {natural_frames} frames; H3 Exact Frame Decode keeps the requested {requested_frames}"
        )
        ignored_text = f" {' '.join(ignored_notes)}" if ignored_notes else ""
        info = (
            f"Mode: {mode} | temporal profile: {internal_frames} frames | canvas {width}×{height} | "
            f"internal packet {natural_frames} frames | decoded profile {requested_frames} | {decode_note} | "
            f"{trained_note}. {checkpoint_note} Decode only the video latent; the audio VAE is unnecessary for image output. "
            f"Preferred output strategy: {output_strategy}; Single Image Output receives the full decoded profile and "
            f"normally emits one selected frame unless emit_candidate_batch is enabled.{ignored_text}{_prompt_warning(prompt)}"
        )
        return cond, latent, fitted_source, requested_frames, final_prompt, info


class H3TextToImagePrepare:
    """Prepare FL2VA text-to-image conditioning."""

    DESCRIPTION = (
        "Prepares FL2VA text conditioning and an H3 latent. Decoding requires the H3 video VAE."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "clip": ("CLIP", {"tooltip": "MiniMax H3 Qwen text/vision encoder."}),
                "prompt": (
                    "STRING",
                    {
                        "multiline": True,
                        "dynamicPrompts": True,
                        "default": "",
                        "tooltip": "Describe the final still image, including subject, composition, lighting and style.",
                    },
                ),
                "width": (
                    "INT",
                    {
                        "default": 1344,
                        "min": 32,
                        "max": 16384,
                        "step": 32,
                        "tooltip": "Output canvas width. Connect an H3 Image Resolution node for safer presets.",
                    },
                ),
                "height": (
                    "INT",
                    {
                        "default": 768,
                        "min": 32,
                        "max": 16384,
                        "step": 32,
                        "tooltip": "Output canvas height. Connect an H3 Image Resolution node for safer presets.",
                    },
                ),
                "quality_profile": (
                    list(FRAME_PRESETS.keys()),
                    {
                        "default": RECOMMENDED_FRAME_PROFILE,
                        "tooltip": (
                            "5 frames is the recommended speed/quality balance. 20 frames gives H3 more temporal "
                            "context and is much slower. The complete profile reaches Single Image Output, which "
                            "returns one selected still unless emit_candidate_batch is enabled."
                        ),
                    },
                ),
                "optimize_for_still": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Adds a locked-camera still-image prompt wrapper. It does not change frames, resolution, steps, sampler, or model weights.",
                }),
            },
        }

    RETURN_TYPES = ("CONDITIONING", "LATENT", "INT", "STRING", "STRING")
    RETURN_NAMES = ("positive", "h3_latent", "requested_frames", "image_prompt", "run_info")
    OUTPUT_TOOLTIPS = (
        "Positive FL2VA text-to-image conditioning for the sampler's positive input.",
        "Packed H3 audio/video latent containing the requested temporal image packet.",
        "Number of image frames that Exact Frame Decode should preserve and decode.",
        "Final still-image prompt after optional optimization.",
        "Temporal packet, canvas, checkpoint expectations and recommended output strategy.",
    )
    FUNCTION = "prepare"
    CATEGORY = CATEGORY

    def prepare(
        self,
        clip,
        prompt: str,
        width: int,
        height: int,
        quality_profile: str,
        optimize_for_still: bool,
    ):
        cond, latent, _source, frames, image_prompt, info = H3ImagePrepare().prepare(
            clip=clip,
            vae=None,
            mode="text_to_image (FL2VA)",
            prompt=prompt,
            width=width,
            height=height,
            frame_preset=quality_profile,
            optimize_prompt=optimize_for_still,
            preserve_strength=0.75,
            source_fit="crop_center",
            reference_size="match_generation_area",
            source_image=None,
        )
        return cond, latent, frames, image_prompt, info


class H3ImageToImagePrepare:
    """Prepare FL2VA image-to-image conditioning."""

    DESCRIPTION = (
        "Encodes the source at frame 0 and prepares FL2VA editing. Source Fidelity changes prompt text, not denoise."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "clip": ("CLIP", {"tooltip": "MiniMax H3 Qwen text/vision encoder."}),
                "vae": ("VAE", {"tooltip": "MiniMax H3 video VAE used to encode the source frame."}),
                "source_image": ("IMAGE", {"tooltip": "Source image used as FL2VA's frame-0 anchor."}),
                "edit_instruction": (
                    "STRING",
                    {
                        "multiline": True,
                        "dynamicPrompts": True,
                        "default": "",
                        "tooltip": "Describe the desired final image and the changes to apply to the source.",
                    },
                ),
                "width": (
                    "INT",
                    {
                        "default": 1344,
                        "min": 32,
                        "max": 16384,
                        "step": 32,
                        "tooltip": "Generation canvas width; the source is fitted to this canvas before encoding.",
                    },
                ),
                "height": (
                    "INT",
                    {
                        "default": 768,
                        "min": 32,
                        "max": 16384,
                        "step": 32,
                        "tooltip": "Generation canvas height; the source is fitted to this canvas before encoding.",
                    },
                ),
                "quality_profile": (
                    list(FRAME_PRESETS.keys()),
                    {
                        "default": RECOMMENDED_FRAME_PROFILE,
                        "tooltip": (
                            "5 frames is the recommended edit profile. With 20 frames, FL2VA may retain source-like "
                            "transition frames near the start. Exact Frame Decode now preserves the complete selected "
                            "profile; Single Image Output scores it and normally emits one still."
                        ),
                    },
                ),
                "source_fidelity": (
                    "FLOAT",
                    {
                        "default": 0.75, "min": 0.0, "max": 1.0, "step": 0.05,
                        "tooltip": (
                            "Controls how strongly the still prompt asks H3 to preserve identity, pose, composition and "
                            "geometry. This is NOT diffusion denoise strength and does not alter the sigma schedule."
                        ),
                    },
                ),
                "source_fit": (
                    ["crop_center", "contain_pad", "stretch"],
                    {
                        "default": "crop_center",
                        "tooltip": "How the source is fitted to the generation canvas before VAE encoding.",
                    },
                ),
                "optimize_for_still": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Adds a locked-camera still-image prompt wrapper and source-preservation language. Sampling settings are unchanged.",
                }),
            },
        }

    RETURN_TYPES = ("CONDITIONING", "LATENT", "IMAGE", "INT", "STRING", "STRING")
    RETURN_NAMES = ("positive", "h3_latent", "fitted_source", "requested_frames", "image_prompt", "run_info")
    OUTPUT_TOOLTIPS = (
        "Positive FL2VA image-to-image conditioning for the sampler's positive input.",
        "Packed H3 audio/video latent with the fitted source encoded as frame-0 anchor.",
        "Source image after the selected crop, pad or stretch operation.",
        "Number of image frames that Exact Frame Decode should preserve and decode.",
        "Final edit prompt after optional still-image and source-preservation optimization.",
        "Temporal packet, source-fit, checkpoint expectations and recommended selection strategy.",
    )
    FUNCTION = "prepare"
    CATEGORY = CATEGORY

    def prepare(
        self,
        clip,
        vae,
        source_image: torch.Tensor,
        edit_instruction: str,
        width: int,
        height: int,
        quality_profile: str,
        source_fidelity: float,
        source_fit: str,
        optimize_for_still: bool,
    ):
        return H3ImagePrepare().prepare(
            clip=clip,
            vae=vae,
            mode="image_to_image (FL2VA)",
            prompt=edit_instruction,
            width=width,
            height=height,
            frame_preset=quality_profile,
            optimize_prompt=optimize_for_still,
            preserve_strength=source_fidelity,
            source_fit=source_fit,
            reference_size="match_generation_area",
            source_image=source_image,
        )


class H3ReferenceEditPrepare:
    """Prepare REF2VA reference-edit conditioning."""

    DESCRIPTION = (
        "Prepares REF2VA editing with up to nine ordered references. Source Fidelity changes prompt text, not denoise."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "clip": ("CLIP", {"tooltip": "MiniMax H3 Qwen text/vision encoder."}),
                "vae": ("VAE", {"tooltip": "MiniMax H3 video VAE used to encode every ordered reference image."}),
                "source_image": ("IMAGE", {"tooltip": "Primary REF2VA reference, addressed as <Picture 1> in the prompt."}),
                "edit_instruction": (
                    "STRING",
                    {
                        "multiline": True,
                        "dynamicPrompts": True,
                        "default": "",
                        "tooltip": "Describe the final image and refer to inputs explicitly as <Picture 1>, <Picture 2>, and so on.",
                    },
                ),
                "width": (
                    "INT",
                    {
                        "default": 1344,
                        "min": 32,
                        "max": 16384,
                        "step": 32,
                        "tooltip": "Generation canvas width; references retain their own aspect ratio before encoding.",
                    },
                ),
                "height": (
                    "INT",
                    {
                        "default": 768,
                        "min": 32,
                        "max": 16384,
                        "step": 32,
                        "tooltip": "Generation canvas height; references retain their own aspect ratio before encoding.",
                    },
                ),
                "quality_profile": (
                    list(FRAME_PRESETS.keys()),
                    {
                        "default": RECOMMENDED_FRAME_PROFILE,
                        "tooltip": (
                            "5 frames is the recommended speed/quality balance. 20 frames gives REF2VA more temporal "
                            "context and is much slower. The complete profile is available to Single Image Output."
                        ),
                    },
                ),
                "source_fidelity": (
                    "FLOAT",
                    {
                        "default": 0.75, "min": 0.0, "max": 1.0, "step": 0.05,
                        "tooltip": (
                            "Controls how strongly the still prompt asks H3 to preserve identity, pose, composition and "
                            "geometry. This is NOT diffusion denoise strength and does not alter the sigma schedule."
                        ),
                    },
                ),
                "source_fit": (
                    ["crop_center", "contain_pad", "stretch"],
                    {
                        "default": "crop_center",
                        "tooltip": "How the primary source preview is fitted to the generation canvas.",
                    },
                ),
                "reference_detail": (
                    ["match_generation_area", "max_identity_2048"],
                    {
                        "default": "match_generation_area",
                        "tooltip": (
                            "How much source resolution each REF2VA reference keeps before VAE encoding. "
                            "max_identity_2048 may preserve more identity detail at higher memory cost."
                        ),
                    },
                ),
                "optimize_for_still": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Adds a locked-camera still-image prompt wrapper and reference-preservation language. Sampling settings are unchanged.",
                }),
            },
            "optional": {
                "reference_image_2": ("IMAGE", {"tooltip": "Optional <Picture 2> reference. Different dimensions are supported."}),
                "reference_image_3": ("IMAGE", {"tooltip": "Optional <Picture 3> reference."}),
                "reference_image_4": ("IMAGE", {"tooltip": "Optional <Picture 4> reference."}),
                "reference_image_5": ("IMAGE", {"tooltip": "Optional <Picture 5> reference."}),
                "reference_image_6": ("IMAGE", {"tooltip": "Optional <Picture 6> reference."}),
                "reference_image_7": ("IMAGE", {"tooltip": "Optional <Picture 7> reference."}),
                "reference_image_8": ("IMAGE", {"tooltip": "Optional <Picture 8> reference."}),
                "reference_image_9": ("IMAGE", {"tooltip": "Optional <Picture 9> reference."}),
            },
        }

    RETURN_TYPES = ("CONDITIONING", "LATENT", "IMAGE", "INT", "STRING", "STRING")
    RETURN_NAMES = ("positive", "h3_latent", "fitted_source", "requested_frames", "image_prompt", "run_info")
    OUTPUT_TOOLTIPS = (
        "Positive REF2VA reference-edit conditioning for the sampler's positive input.",
        "Packed H3 audio/video latent prepared for reference-guided regeneration.",
        "Primary source fitted to the output canvas for preview and comparison.",
        "Number of image frames that Exact Frame Decode should preserve and decode.",
        "Final ordered-reference prompt after optional preservation optimization.",
        "Reference count, temporal packet, checkpoint expectations and recommended selection strategy.",
    )
    FUNCTION = "prepare"
    CATEGORY = CATEGORY

    def prepare(
        self,
        clip,
        vae,
        source_image: torch.Tensor,
        edit_instruction: str,
        width: int,
        height: int,
        quality_profile: str,
        source_fidelity: float,
        source_fit: str,
        reference_detail: str,
        optimize_for_still: bool,
        reference_image_2: Optional[torch.Tensor] = None,
        reference_image_3: Optional[torch.Tensor] = None,
        reference_image_4: Optional[torch.Tensor] = None,
        reference_image_5: Optional[torch.Tensor] = None,
        reference_image_6: Optional[torch.Tensor] = None,
        reference_image_7: Optional[torch.Tensor] = None,
        reference_image_8: Optional[torch.Tensor] = None,
        reference_image_9: Optional[torch.Tensor] = None,
    ):
        return H3ImagePrepare().prepare(
            clip=clip,
            vae=vae,
            mode="reference_edit (REF2VA)",
            prompt=edit_instruction,
            width=width,
            height=height,
            frame_preset=quality_profile,
            optimize_prompt=optimize_for_still,
            preserve_strength=source_fidelity,
            source_fit=source_fit,
            reference_size=reference_detail,
            source_image=source_image,
            reference_image_2=reference_image_2,
            reference_image_3=reference_image_3,
            reference_image_4=reference_image_4,
            reference_image_5=reference_image_5,
            reference_image_6=reference_image_6,
            reference_image_7=reference_image_7,
            reference_image_8=reference_image_8,
            reference_image_9=reference_image_9,
        )


class H3ImageDecode:
    """Decode the requested H3 frame profile."""

    DESCRIPTION = (
        "Decodes the requested frame profile and returns the recommended still index."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "samples": (
                    "LATENT",
                    {
                        "tooltip": (
                            "Sampled H3 latent. Image Studio metadata specifies whether each batch item keeps a 5-, 9-, 13-, or "
                            "20-frame temporal profile."
                        )
                    },
                ),
                "vae": (
                    "VAE",
                    {"tooltip": "MiniMax H3 video VAE used to decode the video latent into an IMAGE batch."},
                ),
            }
        }

    RETURN_TYPES = ("IMAGE", "INT", "STRING", "INT")
    RETURN_NAMES = ("frames", "decoded_frames", "decode_info", "recommended_index")
    OUTPUT_TOOLTIPS = (
        "Complete decoded profile(s), flattened batch-major for standard ComfyUI IMAGE output.",
        "Total number of emitted images across all batch items.",
        "Natural packet size, kept profile size and preferred-frame diagnostic information per batch item.",
        "Preferred zero-based still index for the first batch item. Connect it to Single Image Output.",
    )
    FUNCTION = "decode"
    CATEGORY = CATEGORY

    def decode(self, samples, vae):
        latent = samples["samples"]
        if latent.is_nested:
            latent = latent.unbind()[0]

        latent_batch = int(latent.shape[0]) if hasattr(latent, "shape") and len(latent.shape) > 0 else 1
        images = vae.decode(latent)

        profile_frames = max(
            1,
            int(samples.get("h3_context_frames", samples.get("h3_requested_frames", 1))),
        )
        output_strategy = str(samples.get("h3_output_strategy", "fixed"))

        # Prefer an explicit [B,T,H,W,C] decode. If a VAE returns a flattened
        # [B*T,H,W,C] tensor, recover B/T only when the latent batch makes that
        # interpretation unambiguous; otherwise preserve historical single-batch behavior.
        if images.ndim == 5:
            batched = images
        elif images.ndim == 4 and latent_batch > 1 and int(images.shape[0]) % latent_batch == 0:
            frames_per_item = int(images.shape[0]) // latent_batch
            batched = images.reshape(latent_batch, frames_per_item, *images.shape[-3:])
        else:
            batched = images.unsqueeze(0)

        batch_size = int(batched.shape[0])
        natural_frames = int(batched.shape[1])
        kept_frames = min(profile_frames, natural_frames)
        kept = batched[:, :kept_frames]

        preferred_indices = []
        change_scores = []
        fixed_index = max(0, int(samples.get("h3_output_frame_index", 0)))
        for batch_index in range(batch_size):
            item = kept[batch_index]
            if output_strategy == "first_stable_edit":
                preferred_index, change_score = _first_stable_edit_frame(item)
            else:
                preferred_index, change_score = fixed_index, 0.0
            preferred_index = min(max(0, int(preferred_index)), kept_frames - 1)
            preferred_indices.append(preferred_index)
            change_scores.append(float(change_score))

        # Standard ComfyUI IMAGE is [N,H,W,C], so flatten batch-major after each
        # batch item has been cropped independently. Clone only when cropping or
        # reshaping would otherwise retain surplus packet storage.
        images_out = kept.reshape(-1, *kept.shape[-3:]).clone()
        decoded_frames = int(images_out.shape[0])

        if natural_frames == kept_frames:
            packet_note = f"Decoded the complete natural {natural_frames}-frame packet per batch item."
        else:
            packet_note = (
                f"The temporal latent naturally decoded {natural_frames} frames per batch item; kept the requested "
                f"{kept_frames}-frame profile for each item."
            )

        preferred_text = ", ".join(
            f"b{index}:frame {preferred_indices[index]} (change {change_scores[index]:.4f})"
            for index in range(batch_size)
        )
        info = (
            f"{packet_note} Batch items={batch_size}; emitted images={decoded_frames}. "
            f"Preferred still(s) via {output_strategy}: {preferred_text}. "
            "No requested profile frames were discarded before Single Image Output."
        )
        return images_out, decoded_frames, info, preferred_indices[0]


class H3ImageFrameSelector:
    """Select one decoded frame or return the full batch."""

    DESCRIPTION = (
        "Selects a decoded frame by index or score. Enable emit_candidate_batch to return every frame."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "frames": (
                    "IMAGE",
                    {
                        "tooltip": (
                            "Decoded H3 IMAGE batch. With the supplied Exact Frame Decode node this contains the "
                            "complete selected 5-, 9-, 13-, or 20-frame profile."
                        )
                    },
                ),
                "strategy": ([
                    "decode_recommended",
                    "first",
                    "stable_quality",
                    "balanced_edit",
                    "best_quality",
                    "most_similar_to_source",
                    "sharpest",
                    "middle",
                    "last",
                    "manual_index",
                ], {
                    "default": "decode_recommended",
                    "tooltip": (
                        "decode_recommended uses Exact Frame Decode's mode-aware recommendation (connect its index). "
                        "first selects frame 0. stable_quality favors sharp, clean and temporally "
                        "stable frames. balanced_edit combines source similarity with stable quality. best_quality "
                        "uses sharpness, contrast and exposure. most_similar_to_source requires source_image. sharpest "
                        "uses edge detail only. middle, last and manual_index select a fixed frame without scoring."
                    ),
                }),
                "manual_index": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 4096,
                        "step": 1,
                        "tooltip": "Zero-based frame index used only when strategy is manual_index.",
                    },
                ),
                "skip_first_frames": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 128,
                        "step": 1,
                        "tooltip": (
                            "Excludes this many initial frames from metric-based scoring. Leave at 0 unless a specific "
                            "generation shows an unstable opening frame. Ignored by fixed-index strategies."
                        ),
                    },
                ),
                "candidate_start": (
                    "FLOAT",
                    {
                        "default": 0.0,
                        "min": 0.0,
                        "max": 1.0,
                        "step": 0.05,
                        "tooltip": (
                            "Fractional start of the automatic scoring range. 0.0 begins at the first frame and 0.5 "
                            "begins halfway through. skip_first_frames can move the effective start later."
                        ),
                    },
                ),
                "candidate_end": (
                    "FLOAT",
                    {
                        "default": 1.0,
                        "min": 0.0,
                        "max": 1.0,
                        "step": 0.05,
                        "tooltip": (
                            "Fractional end of the automatic scoring range. 1.0 includes the end of the decoded batch."
                        ),
                    },
                ),
                "similarity_weight": (
                    "FLOAT",
                    {
                        "default": 0.60,
                        "min": 0.0,
                        "max": 1.0,
                        "step": 0.05,
                        "tooltip": (
                            "Used only by balanced_edit when source_image is connected. Higher values favor source "
                            "similarity; lower values favor sharpness, exposure, contrast and temporal stability."
                        ),
                    },
                ),
                "top_k": (
                    "INT",
                    {
                        "default": 4,
                        "min": 1,
                        "max": 16,
                        "step": 1,
                        "tooltip": (
                            "Maximum number of highest-scoring frames returned by candidate_batch_debug for automatic "
                            "strategies. It does not limit selected_image when emit_candidate_batch is enabled: that "
                            "main output contains every decoded frame. Fixed strategies return their chosen frame on "
                            "candidate_batch_debug because they do not calculate a ranking."
                        ),
                    },
                ),
            },
            "optional": {
                "source_image": (
                    "IMAGE",
                    {
                        "tooltip": (
                            "Optional comparison image for most_similar_to_source and balanced_edit. Only the first "
                            "image in the connected batch is used as the reference."
                        )
                    },
                ),
                "emit_candidate_batch": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": (
                            "OFF: selected_image contains only the picked still. ON: selected_image contains the entire "
                            "decoded 5-, 9-, 13-, or 20-frame batch, so an already-connected Preview Image or Save Image node "
                            "shows or saves every generated image. candidate_batch_debug remains the ranked top-k "
                            "subset. Enabling this intentionally retains more RAM/VRAM."
                        ),
                    },
                ),
                "recommended_index": (
                    "INT",
                    {
                        "forceInput": True,
                        "tooltip": (
                            "Connect recommended_index from Exact Frame Decode. Used by decode_recommended; if left "
                            "unconnected, frame 0 is selected."
                        ),
                    },
                ),
            },
        }

    RETURN_TYPES = ("IMAGE", "IMAGE", "INT", "FLOAT", "STRING")
    RETURN_NAMES = ("selected_image", "candidate_batch_debug", "selected_index", "selected_score", "score_report")
    OUTPUT_TOOLTIPS = (
        "Single selected still when emit_candidate_batch is off; complete decoded batch when it is on.",
        "Ranked top-k candidates for automatic strategies, or the fixed chosen frame for fixed strategies.",
        "Zero-based index of the preferred still inside the original decoded batch.",
        "Score assigned to the preferred still; fixed strategies return 1.0.",
        "Human-readable scoring and emitted-batch report.",
    )
    FUNCTION = "select"
    CATEGORY = CATEGORY

    @staticmethod
    def _metric_tensor(frames: torch.Tensor, max_side: int = 512, chunk_size: int = 4) -> torch.Tensor:
        # Keep the source packet in its native dtype and only upcast small chunks.
        # Upcasting an entire 22-frame 8 MP fp16 decode before downsampling can
        # otherwise create a multi-gigabyte transient allocation.
        samples = frames[..., :3].movedim(-1, 1)
        h, w = samples.shape[-2:]
        scale = min(1.0, max_side / max(h, w))
        if scale >= 1.0:
            return samples.float().clamp(0.0, 1.0)

        nh = max(16, int(round(h * scale)))
        nw = max(16, int(round(w * scale)))
        chunks = []
        for chunk in samples.split(max(1, int(chunk_size)), dim=0):
            chunk = chunk.float()
            chunk = F.interpolate(
                chunk,
                size=(nh, nw),
                mode="bilinear",
                align_corners=False,
                antialias=True,
            )
            chunks.append(chunk)
        return torch.cat(chunks, dim=0).clamp(0.0, 1.0)

    @staticmethod
    def _minmax(values: torch.Tensor) -> torch.Tensor:
        lo = values.min()
        hi = values.max()
        if float((hi - lo).abs()) < 1e-8:
            return torch.ones_like(values)
        return (values - lo) / (hi - lo)

    @staticmethod
    def _quality_metrics(x: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        gray = 0.2126 * x[:, 0:1] + 0.7152 * x[:, 1:2] + 0.0722 * x[:, 2:3]
        lap_kernel = torch.tensor(
            [[0.0, 1.0, 0.0], [1.0, -4.0, 1.0], [0.0, 1.0, 0.0]],
            device=x.device,
            dtype=x.dtype,
        ).view(1, 1, 3, 3)
        lap = F.conv2d(gray, lap_kernel, padding=1)
        sharpness = torch.log1p(lap.var(dim=(1, 2, 3)) * 1000.0)
        contrast = gray.std(dim=(1, 2, 3))
        clipped = ((x < 0.01) | (x > 0.99)).float().mean(dim=(1, 2, 3))
        exposure = (1.0 - clipped * 3.0).clamp(0.0, 1.0)
        return sharpness, contrast, exposure

    @staticmethod
    def _similarity(x: torch.Tensor, source_image: torch.Tensor) -> torch.Tensor:
        ref = source_image[:1, ..., :3].movedim(-1, 1).to(device=x.device, dtype=x.dtype)
        ref = F.interpolate(ref, size=x.shape[-2:], mode="bilinear", align_corners=False, antialias=True).clamp(0.0, 1.0)
        ref = ref.expand(x.shape[0], -1, -1, -1)
        color_error = (x - ref).abs().mean(dim=(1, 2, 3))

        def gradients(t: torch.Tensor):
            gx = t[..., :, 1:] - t[..., :, :-1]
            gy = t[..., 1:, :] - t[..., :-1, :]
            return gx, gy

        gx, gy = gradients(x)
        rgx, rgy = gradients(ref)
        edge_error = 0.5 * (gx - rgx).abs().mean(dim=(1, 2, 3)) + 0.5 * (gy - rgy).abs().mean(dim=(1, 2, 3))
        return (1.0 - (0.75 * color_error + 0.25 * edge_error)).clamp(0.0, 1.0)

    @staticmethod
    def _empty_debug(frames: torch.Tensor) -> torch.Tensor:
        # frames[:0] would remain backed by the complete decoded batch.
        return frames.new_empty((0, *frames.shape[1:]))

    @staticmethod
    def _primary_output(frames: torch.Tensor, selected: torch.Tensor, emit_candidate_batch: bool) -> torch.Tensor:
        # Clone so ComfyUI output caching owns independent storage rather than a
        # view into another node's tensor.
        return frames.clone() if emit_candidate_batch else selected

    def select(
        self,
        frames: torch.Tensor,
        strategy: str,
        manual_index: int,
        skip_first_frames: int,
        candidate_start: float,
        candidate_end: float,
        similarity_weight: float,
        top_k: int,
        source_image: Optional[torch.Tensor] = None,
        emit_candidate_batch: bool = False,
        recommended_index: Optional[int] = None,
    ):
        if frames.ndim != 4 or frames.shape[0] < 1:
            raise ValueError("frames must be a non-empty ComfyUI IMAGE batch [N,H,W,C]")

        n = int(frames.shape[0])
        fixed_indices = {
            "decode_recommended": 0 if recommended_index is None else int(recommended_index),
            "first": 0,
            "manual_index": int(manual_index),
            "middle": n // 2,
            "last": n - 1,
        }
        if strategy in fixed_indices:
            selected_index = max(0, min(n - 1, fixed_indices[strategy]))
            chosen = frames[selected_index:selected_index + 1].clone()
            primary = self._primary_output(frames, chosen, emit_candidate_batch)
            debug = chosen.clone() if emit_candidate_batch else self._empty_debug(frames)
            report = f"Fixed strategy={strategy}; frame {selected_index}/{n - 1}."
            if strategy == "decode_recommended" and recommended_index is None:
                report += " recommended_index was not connected, so frame 0 was used."
            if emit_candidate_batch:
                report += f" selected_image emits the complete {n}-frame batch; candidate_batch_debug contains the chosen frame."
            return primary, debug, selected_index, 1.0, report

        start = max(int(skip_first_frames), int(math.floor(max(0.0, min(1.0, candidate_start)) * n)))
        start = min(n - 1, start)
        end = int(math.ceil(max(0.0, min(1.0, candidate_end)) * n))
        end = max(start + 1, min(n, end))
        candidate_indices = torch.arange(start, end, device=frames.device)
        candidate_frames = frames[start:end]
        x = self._metric_tensor(candidate_frames)

        sharpness, contrast, exposure = self._quality_metrics(x)
        sharp_n = self._minmax(sharpness)
        contrast_n = self._minmax(contrast)
        quality = 0.70 * sharp_n + 0.20 * contrast_n + 0.10 * exposure

        if x.shape[0] > 1:
            temporal_delta = torch.empty(x.shape[0], device=x.device, dtype=x.dtype)
            temporal_delta[0] = (x[0] - x[1]).abs().mean()
            temporal_delta[-1] = (x[-1] - x[-2]).abs().mean()
            if x.shape[0] > 2:
                temporal_delta[1:-1] = 0.5 * (x[1:-1] - x[:-2]).abs().mean(dim=(1, 2, 3))
                temporal_delta[1:-1] += 0.5 * (x[1:-1] - x[2:]).abs().mean(dim=(1, 2, 3))
            stability = 1.0 - self._minmax(temporal_delta)
        else:
            stability = torch.ones_like(quality)
        stable_quality = 0.80 * quality + 0.20 * stability

        similarity = None
        if source_image is not None:
            similarity = self._similarity(x, source_image)

        effective_strategy = strategy
        strategy_warning = ""
        if strategy == "sharpest":
            scores = sharp_n
        elif strategy == "stable_quality":
            scores = stable_quality
        elif strategy == "most_similar_to_source":
            if similarity is None:
                scores = quality
                effective_strategy = "best_quality"
                strategy_warning = (
                    " WARNING: most_similar_to_source requires source_image; fell back to best_quality."
                )
            else:
                scores = similarity
        elif strategy == "balanced_edit":
            if similarity is None:
                scores = stable_quality
                effective_strategy = "stable_quality"
                strategy_warning = (
                    " WARNING: balanced_edit requires source_image; fell back to stable_quality."
                )
            else:
                sw = max(0.0, min(1.0, float(similarity_weight)))
                scores = sw * similarity + (1.0 - sw) * stable_quality
        else:
            scores = quality

        best_local = int(torch.argmax(scores).item())
        selected_index = int(candidate_indices[best_local].item())
        selected_score = float(scores[best_local].item())
        selected = frames[selected_index:selected_index + 1].clone()

        if emit_candidate_batch:
            k = min(max(1, int(top_k)), len(scores))
            top_local = torch.topk(scores, k=k, largest=True, sorted=True).indices
            top_global = candidate_indices[top_local].long()
            candidate_output = frames.index_select(0, top_global).clone()
            primary_output = frames.clone()
        else:
            candidate_output = self._empty_debug(frames)
            primary_output = selected

        sim_text = "n/a" if similarity is None else f"{float(similarity[best_local]):.4f}"
        report = (
            f"Selected frame {selected_index}/{n - 1}; requested_strategy={strategy}; "
            f"effective_strategy={effective_strategy}; score={selected_score:.4f}, "
            f"sharpness={float(sharp_n[best_local]):.4f}, quality={float(quality[best_local]):.4f}, "
            f"stability={float(stability[best_local]):.4f}, similarity={sim_text}; candidates={start}..{end - 1}."
            f"{strategy_warning}"
        )
        if emit_candidate_batch:
            report += (
                f" selected_image emits the complete {n}-frame decoded batch; "
                f"candidate_batch_debug emits the best {int(candidate_output.shape[0])} candidate(s), limited by top_k."
            )
        else:
            report += " Candidate batch suppressed."
        return primary_output, candidate_output, selected_index, selected_score, report


class H3SamplingSettings:
    """Configure H3 sampling and sigma shifts."""

    DESCRIPTION = (
        "Configures the sampler, scheduler, denoise value, and H3 video/audio sigma shifts."
    )

    @classmethod
    def INPUT_TYPES(cls):
        scheduler_options = list(comfy.samplers.SCHEDULER_NAMES)
        if "beta_custom" not in scheduler_options:
            scheduler_options.append("beta_custom")
        return {
            "required": {
                "model": ("MODEL", {"tooltip": "Loaded MiniMax H3 diffusion model."}),
                "sampler_name": (
                    list(comfy.samplers.SAMPLER_NAMES),
                    {
                        "default": "res_multistep" if "res_multistep" in comfy.samplers.SAMPLER_NAMES else comfy.samplers.SAMPLER_NAMES[0],
                        "tooltip": "ComfyUI sampler implementation. res_multistep is the H3 baseline.",
                    },
                ),
                "scheduler": (
                    scheduler_options,
                    {
                        "default": "simple" if "simple" in scheduler_options else scheduler_options[0],
                        "tooltip": "Sigma schedule. beta_custom exposes alpha/beta below and now honors denoise identically to other schedulers.",
                    },
                ),
                "steps": (
                    "INT",
                    {"default": 20, "min": 1, "max": 10000, "step": 1, "tooltip": "Number of sampling steps actually executed."},
                ),
                "denoise": (
                    "FLOAT",
                    {
                        "default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01,
                        "tooltip": (
                            "ComfyUI-style denoise strength. Below 1 builds a longer schedule and keeps only its final "
                            "steps; 0 returns empty sigmas. This affects both beta_custom and standard schedulers."
                        ),
                    },
                ),
                "shift_video": (
                    "FLOAT",
                    {
                        "default": 12.0, "min": 0.01, "max": 100.0, "step": 0.01,
                        "tooltip": "Flow sigma shift for H3 video/image latent sampling.",
                    },
                ),
                "shift_audio": (
                    "FLOAT",
                    {
                        "default": 3.0, "min": 0.01, "max": 100.0, "step": 0.01,
                        "tooltip": "H3 audio sigma shift metadata. Image Studio does not decode the audio VAE.",
                    },
                ),
                "beta_alpha": (
                    "FLOAT",
                    {
                        "default": 0.6, "min": 0.01, "max": 50.0, "step": 0.01,
                        "tooltip": "Alpha parameter used only when scheduler is beta_custom.",
                    },
                ),
                "beta_beta": (
                    "FLOAT",
                    {
                        "default": 0.6, "min": 0.01, "max": 50.0, "step": 0.01,
                        "tooltip": "Beta parameter used only when scheduler is beta_custom.",
                    },
                ),
            }
        }

    RETURN_TYPES = ("MODEL", "SAMPLER", "SIGMAS", "STRING")
    RETURN_NAMES = ("shifted_model", "sampler", "sigmas", "sampling_info")
    OUTPUT_TOOLTIPS = (
        "Cloned model patched with H3 video/audio flow-sampling shifts.",
        "Configured ComfyUI sampler object for SamplerCustomAdvanced.",
        "Sigma schedule after applying steps, scheduler and denoise semantics.",
        "Resolved sampler, scheduler, step count, shifts and AV sampling backend.",
    )
    FUNCTION = "build"
    CATEGORY = CATEGORY

    @staticmethod
    def _apply_h3_shift(model, shift_video: float, shift_audio: float):
        m = model.clone()

        # MiniMax H3 is a FLOW_AV model in ComfyUI. Its sampling object must
        # retain ModelSamplingAV semantics even for image-only output because the
        # packed latent still contains an audio stream during denoising. In
        # particular, H3 sampling reads model_sampling.audio_scale.
        av_sampling = getattr(comfy.model_sampling, "ModelSamplingAV", None)
        sampling_base = av_sampling or comfy.model_sampling.ModelSamplingDiscreteFlow

        class ModelSamplingAdvanced(sampling_base, comfy.model_sampling.CONST):
            if av_sampling is None:
                audio_shift = None

                @property
                def audio_scale(self):
                    if self.audio_shift is None:
                        return 1.0
                    return self.shift / self.audio_shift

        original = m.get_model_object("model_sampling")
        model_sampling = ModelSamplingAdvanced(m.model.model_config)
        multiplier = getattr(original, "multiplier", 1000)
        if av_sampling is not None:
            model_sampling.set_parameters(
                shift=float(shift_video),
                audio_shift=float(shift_audio),
                multiplier=multiplier,
            )
            sampling_backend = "ModelSamplingAV"
        else:
            model_sampling.set_parameters(shift=float(shift_video), multiplier=multiplier)
            model_sampling.audio_shift = float(shift_audio)
            sampling_backend = "ModelSamplingAV compatibility shim"
        if hasattr(original, "noise_scale"):
            model_sampling.set_noise_scale(original.noise_scale)
        m.add_object_patch("model_sampling", model_sampling)

        transformer_options = m.model_options.get("transformer_options", {}).copy()
        transformer_options["minimax_h3_sigma_shift_video"] = float(shift_video)
        transformer_options["minimax_h3_sigma_shift_audio"] = float(shift_audio)
        m.model_options["transformer_options"] = transformer_options
        return m, sampling_backend

    def build(
        self,
        model,
        sampler_name: str,
        scheduler: str,
        steps: int,
        denoise: float,
        shift_video: float,
        shift_audio: float,
        beta_alpha: float,
        beta_beta: float,
    ):
        shifted_model, sampling_backend = self._apply_h3_shift(model, shift_video, shift_audio)
        sampler = comfy.samplers.sampler_object(sampler_name)

        model_sampling = shifted_model.get_model_object("model_sampling")
        steps = max(1, int(steps))
        denoise = max(0.0, min(1.0, float(denoise)))

        if denoise <= 0.0:
            sigmas = torch.FloatTensor([])
            beta_note = f" | beta alpha={beta_alpha:g}, beta={beta_beta:g}" if scheduler == "beta_custom" else ""
            info = (
                f"sampler={sampler_name} | scheduler={scheduler} | steps={steps} | denoise=0 | "
                f"schedule_steps=0 | shift_video={shift_video:g} | shift_audio={shift_audio:g} | "
                f"backend={sampling_backend}{beta_note}"
            )
            return shifted_model, sampler, sigmas, info

        total_steps = steps if denoise >= 1.0 else max(steps, int(steps / denoise))

        if scheduler == "beta_custom":
            # BetaSamplingScheduler equivalent with user-controlled alpha/beta.
            sigmas = comfy.samplers.beta_scheduler(
                model_sampling,
                total_steps,
                alpha=float(beta_alpha),
                beta=float(beta_beta),
            ).cpu()
        else:
            sigmas = comfy.samplers.calculate_sigmas(
                model_sampling,
                scheduler,
                total_steps,
            ).cpu()

        # BasicScheduler-style denoise semantics: build the full schedule then
        # keep the final steps+1 sigmas used by SamplerCustomAdvanced.
        sigmas = sigmas[-(steps + 1):]

        beta_note = f" | beta alpha={beta_alpha:g}, beta={beta_beta:g}" if scheduler == "beta_custom" else ""
        info = (
            f"sampler={sampler_name} | scheduler={scheduler} | steps={steps} | denoise={denoise:g} | "
            f"schedule_steps={total_steps} | shift_video={shift_video:g} | shift_audio={shift_audio:g} | "
            f"backend={sampling_backend}{beta_note}"
        )
        return shifted_model, sampler, sigmas, info


class H3ImageSamplingPreset:
    """Apply an H3 image sampling preset."""

    DESCRIPTION = (
        "Applies a base or LightX v0.1 sampling profile. Load the LightX LoRA upstream."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL", {"tooltip": "Loaded MiniMax H3 diffusion model."}),
                "sampling_profile": (
                    list(SAMPLING_PROFILES.keys()) + list(LEGACY_SAMPLING_PROFILES.keys()),
                    {
                        "default": "base quality | RES 20 steps",
                        "tooltip": (
                            "Base profiles use RES Multistep. LightX v0.1 profiles reproduce Kijai's published "
                            "four-step ER-SDE or SA-Solver recipe and expect the matching LightX LoRA upstream."
                        ),
                    },
                ),
            },
        }

    RETURN_TYPES = ("MODEL", "SAMPLER", "SIGMAS", "STRING")
    RETURN_NAMES = ("model", "sampler", "sigmas", "sampling_info")
    OUTPUT_TOOLTIPS = (
        "Cloned model patched with the selected H3 recipe's video/audio shifts.",
        "Sampler required by the selected base or LightX v0.1 recipe.",
        "Complete sigma schedule for the selected recipe.",
        "Resolved profile, sampler, scheduler, step count, shifts and AV sampling backend.",
    )
    FUNCTION = "build"
    CATEGORY = CATEGORY

    def build(self, model, sampling_profile: str):
        profiles = {**LEGACY_SAMPLING_PROFILES, **SAMPLING_PROFILES}
        if sampling_profile not in profiles:
            raise ValueError(f"Unknown H3 sampling profile: {sampling_profile}")
        sampler_name, scheduler, steps, shift_video, shift_audio = profiles[sampling_profile]
        shifted_model, sampler, sigmas, info = H3SamplingSettings().build(
            model=model,
            sampler_name=sampler_name,
            scheduler=scheduler,
            steps=steps,
            denoise=1.0,
            shift_video=shift_video,
            shift_audio=shift_audio,
            beta_alpha=0.6,
            beta_beta=0.6,
        )
        return shifted_model, sampler, sigmas, f"profile={sampling_profile} | {info}"


class H3WorkflowNote:
    """Display a note in a saved workflow."""

    DESCRIPTION = (
        "Displays text without affecting generation. Kept for compatibility with v15 workflows."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "section": (
                    ["quick start", "models", "settings", "optional / experimental", "troubleshooting"],
                    {"default": "quick start", "tooltip": "Visual section label for this workflow note."},
                ),
                "text": (
                    "STRING",
                    {
                        "default": "Workflow guidance goes here.",
                        "multiline": True,
                        "dynamicPrompts": False,
                        "tooltip": "Plain-text workflow instructions. This text is never sent to MiniMax H3.",
                    },
                ),
            }
        }

    RETURN_TYPES = ()
    FUNCTION = "display"
    CATEGORY = f"{CATEGORY}/Documentation"

    @staticmethod
    def display(section: str, text: str):
        return ()


NODE_CLASS_MAPPINGS = {
    "H3ImageSamplingPreset": H3ImageSamplingPreset,
    "H3SamplingSettings": H3SamplingSettings,
    "H3ImageResolutionPreset": H3ImageResolutionPreset,
    "H3ImageResolution": H3ImageResolution,
    "H3TextToImagePrepare": H3TextToImagePrepare,
    "H3ImageToImagePrepare": H3ImageToImagePrepare,
    "H3ReferenceEditPrepare": H3ReferenceEditPrepare,
    "H3ImagePrepare": H3ImagePrepare,
    "H3ImageDecode": H3ImageDecode,
    "H3ImageFrameSelector": H3ImageFrameSelector,
    "H3WorkflowNote": H3WorkflowNote,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "H3ImageSamplingPreset": "MiniMax H3 Image • Sampling Preset",
    "H3SamplingSettings": "MiniMax H3 Image • Advanced Sampling",
    "H3ImageResolutionPreset": "MiniMax H3 Image • Resolution Preset",
    "H3ImageResolution": "MiniMax H3 Image • Advanced Resolution",
    "H3TextToImagePrepare": "MiniMax H3 Image • Text to Image",
    "H3ImageToImagePrepare": "MiniMax H3 Image • Image to Image",
    "H3ReferenceEditPrepare": "MiniMax H3 Image • Reference Edit",
    "H3ImagePrepare": "MiniMax H3 Image • Advanced Combined Prepare",
    "H3ImageDecode": "MiniMax H3 Image • Exact Frame Decode",
    "H3ImageFrameSelector": "MiniMax H3 Image • Single Image Output",
    "H3WorkflowNote": "MiniMax H3 Image • Workflow Note",
}
