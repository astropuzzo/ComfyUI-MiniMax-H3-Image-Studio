# SPDX-License-Identifier: GPL-3.0-or-later
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
    import folder_paths
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

MANUAL_FRAME_PROFILE = "manual frames | exact value below"
LEGACY_MANUAL_FRAME_PROFILE = "manual frames | use value below (17k+5)"
MANUAL_SAMPLING_PROFILE = "manual steps | official sampler"

FRAME_PRESETS: Dict[str, Optional[int]] = {
    "maximum speed | 5 frames (banding risk)": 5,
    "recommended | 20 frames": 20,
    "image balanced | 56 frames": 56,
    "video-trained | 124 frames (slow)": 124,
    "video-trained+ | 192 frames (very slow)": 192,
    MANUAL_FRAME_PROFILE: None,
}

# Old workflows must continue to load, but these values are deliberately not
# shown in the UI. H3's native ComfyUI implementation has a five-frame minimum.
LEGACY_FRAME_PRESETS: Dict[str, int] = {
    "image native | 5 frames (recommended)": 5,
    "image+ | 22 frames": 22,
    "1 frame | forced single frame (unsupported)": 5,
    "5 frames | minimal safe packet": 5,
    "22 frames | safer fallback": 22,
    "39 frames | quick": 39,
    "56 frames | balanced": 56,
    "73 frames | quality": 73,
    "90 frames | high quality": 90,
    "107 frames | near-native": 107,
    "124 frames | native trained minimum": 124,
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

SAMPLING_PROFILES: Dict[str, Tuple[str, str, Optional[int]]] = {
    "official quality | 20 steps": ("res_multistep", "simple", 20),
    "fast preview | 12 steps": ("res_multistep", "simple", 12),
    "reference detail | beta, 20 steps": ("res_multistep", "beta", 20),
    MANUAL_SAMPLING_PROFILE: ("res_multistep", "simple", None),
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
        return multiple, multiple
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


def _resolve_frame_count(frame_preset: str, manual_frames: int) -> Tuple[int, str]:
    """Resolve a quality preset or an exact manual output-frame request."""
    if frame_preset in (MANUAL_FRAME_PROFILE, LEGACY_MANUAL_FRAME_PROFILE):
        requested = max(1, int(manual_frames))
        return requested, f"exact manual output count {requested}"
    if frame_preset in FRAME_PRESETS:
        value = FRAME_PRESETS[frame_preset]
        if value is None:
            raise ValueError(f"Manual H3 frame profile requires manual_frames, received {manual_frames}")
        return int(value), ""
    if frame_preset in LEGACY_FRAME_PRESETS:
        return LEGACY_FRAME_PRESETS[frame_preset], ""
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


def _empty_h3_av_latent(width: int, height: int, length: int, batch_size: int = 1):
    requested_frames = max(1, int(length))
    latent_t, natural_frames = _latent_t_for_frame_count(requested_frames)
    duration = natural_frames / FPS
    audio_t = max(1, round(duration * AUDIO_LATENT_FPS))
    device = comfy.model_management.intermediate_device()
    video = torch.zeros((batch_size, 24, latent_t, height // 16, width // 16), device=device)
    audio = torch.zeros((batch_size, 32, 2, audio_t), device=device)
    nested = comfy.nested_tensor.NestedTensor((video, audio))
    return {
        "samples": nested,
        "h3_requested_frames": requested_frames,
        "h3_natural_frames": natural_frames,
    }, requested_frames, natural_frames


def _normalize_prompt(mode: str, prompt: str, optimize_prompt: bool, preserve_strength: float) -> str:
    prompt = (prompt or "").strip()
    if not optimize_prompt:
        return prompt

    preserve_strength = float(max(0.0, min(1.0, preserve_strength)))
    if preserve_strength >= 0.8:
        preserve = "Preserve the subject identity, facial structure, anatomy, pose, composition, perspective, and major object geometry very strictly."
    elif preserve_strength >= 0.5:
        preserve = "Preserve subject identity, anatomy, composition, perspective, and major geometry unless the requested change requires otherwise."
    else:
        preserve = "Keep the source recognizable while allowing substantial visual changes requested by the instruction."

    still = (
        "Image task: produce one finished, high-definition still composition. Internally, keep that completed image "
        "visually unchanged across the generated frame packet: locked camera, fixed composition, no cuts, no camera "
        "movement, no subject motion, no temporal progression, and no audio instructions. Preserve crisp fine texture, "
        "clean edges, coherent anatomy, and intentional focus where the description calls for them."
    )

    if mode == "text_to_image (FL2VA)":
        return f"{still}\n\nTarget image description: {prompt}"
    if mode == "image_to_image (FL2VA)":
        return (
            f"<Picture 1> is the source image and first-frame anchor. Apply the requested transformation immediately, "
            f"then hold the fully completed edited result as the still target. {still} {preserve}\n\n"
            f"Target edit: {prompt}"
        )
    return (
        f"<Picture 1> is the visual reference for an image-editing task, not the final output. {still} {preserve}\n\n"
        f"Target edit: {prompt}"
    )


def _prompt_warning(prompt: str) -> str:
    if VIDEO_PROMPT_RE.search(prompt or ""):
        return (
            " WARNING: the user prompt contains video/timeline/camera-motion/audio language. "
            "Rewrite it as the exact appearance of one final still; contradictory motion instructions reduce image fidelity."
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


class H3ImageResolution:
    """H3-aware resolution selector with source-ratio and native-area safeguards."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "aspect_ratio": (["source image"] + list(ASPECT_RATIOS.keys()) + ["custom dimensions"],),
                "megapixels": ("FLOAT", {"default": 1.00, "min": 0.10, "max": 64.00, "step": 0.10}),
                "multiple": ([32, 64], {"default": 32}),
                "native_area_cap": ("BOOLEAN", {"default": True}),
                "custom_width": ("INT", {"default": 2048, "min": 32, "max": 16384, "step": 32}),
                "custom_height": ("INT", {"default": 2048, "min": 32, "max": 16384, "step": 32}),
            },
            "optional": {
                "source_image": ("IMAGE",),
                "custom_megapixels": ("FLOAT", {"default": 2.0, "min": 0.10, "max": 64.0, "step": 0.10}),
                "limit_to_native_area": ("BOOLEAN", {"default": False}),
            },
        }

    RETURN_TYPES = ("INT", "INT", "STRING")
    RETURN_NAMES = ("width", "height", "resolution_info")
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
    ):
        multiple = int(multiple)
        cap = NATIVE_MAX_PIXELS if native_area_cap else None

        if aspect_ratio == "custom dimensions":
            width = _round_to_multiple(custom_width, multiple)
            height = _round_to_multiple(custom_height, multiple)
            if cap is not None and width * height > cap:
                width, height = _fit_area_to_ratio(cap, width / height, multiple, cap)
            source = "custom"
        else:
            if aspect_ratio == "source image":
                if source_image is not None:
                    h, w = int(source_image.shape[1]), int(source_image.shape[2])
                    ratio = w / max(1, h)
                    source = f"source ratio {w}:{h}"
                else:
                    ratio = 1.0
                    source = "source missing; square fallback"
            else:
                rw, rh = ASPECT_RATIOS[aspect_ratio]
                ratio = rw / rh
                source = aspect_ratio
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
    """Simple H3-native selector using the same megapixel convention as ComfyUI."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "aspect_ratio": (["source image"] + list(ASPECT_RATIOS.keys()),),
                "resolution_profile": (
                    list(RESOLUTION_PROFILES.keys()),
                    {"default": "native detail | 0.98 MP"},
                ),
            },
            "optional": {
                "source_image": ("IMAGE",),
                "custom_megapixels": ("FLOAT", {"default": 2.0, "min": 0.10, "max": 64.0, "step": 0.10}),
                "limit_to_native_area": ("BOOLEAN", {"default": False}),
            },
        }

    RETURN_TYPES = ("INT", "INT", "STRING")
    RETURN_NAMES = ("width", "height", "resolution_info")
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
        if aspect_ratio == "source image":
            if source_image is None:
                raise ValueError("source image aspect ratio requires source_image")
            h, w = int(source_image.shape[1]), int(source_image.shape[2])
            ratio = w / max(1, h)
            source = f"source ratio {w}:{h}"
        else:
            rw, rh = ASPECT_RATIOS[aspect_ratio]
            ratio = rw / rh
            source = aspect_ratio

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
    """Prepare MiniMax H3 conditioning and AV latent for still-image extraction."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "clip": ("CLIP",),
                "vae": ("VAE",),
                "mode": ([
                    "text_to_image (FL2VA)",
                    "image_to_image (FL2VA)",
                    "reference_edit (REF2VA)",
                ],),
                "prompt": ("STRING", {"multiline": True, "dynamicPrompts": True, "default": ""}),
                "width": ("INT", {"default": 1344, "min": 32, "max": 16384, "step": 32}),
                "height": ("INT", {"default": 768, "min": 32, "max": 16384, "step": 32}),
                "frame_preset": (
                    list(FRAME_PRESETS.keys()),
                    {"default": "recommended | 20 frames"},
                ),
                "optimize_prompt": ("BOOLEAN", {"default": True}),
                "preserve_strength": ("FLOAT", {"default": 0.75, "min": 0.0, "max": 1.0, "step": 0.05}),
                "source_fit": (["crop_center", "contain_pad", "stretch"], {"default": "crop_center"}),
                "reference_size": (["match_generation_area", "max_identity_2048"], {"default": "match_generation_area"}),
            },
            "optional": {
                "source_image": ("IMAGE",),
                "manual_frames": ("INT", {
                    "default": 5,
                    "min": 1,
                    "max": 4096,
                    "step": 1,
                    "tooltip": "Used only by the manual quality profile. Accepts any exact output count from 1 to 4096.",
                }),
            },
        }

    RETURN_TYPES = ("CONDITIONING", "LATENT", "IMAGE", "INT", "STRING", "STRING")
    RETURN_NAMES = ("positive", "h3_latent", "fitted_source", "requested_frames", "optimized_prompt", "run_info")
    FUNCTION = "prepare"
    CATEGORY = CATEGORY

    def prepare(
        self,
        clip,
        vae,
        mode: str,
        prompt: str,
        width: int,
        height: int,
        frame_preset: str,
        optimize_prompt: bool,
        preserve_strength: float,
        source_fit: str,
        reference_size: str,
        source_image: Optional[torch.Tensor] = None,
        manual_frames: int = 5,
    ):
        width = _round_to_multiple(width, CANVAS_MULTIPLE)
        height = _round_to_multiple(height, CANVAS_MULTIPLE)
        legacy_single_frame = frame_preset == "1 frame | forced single frame (unsupported)"
        length, manual_frame_note = _resolve_frame_count(frame_preset, manual_frames)
        latent, requested_frames, natural_frames = _empty_h3_av_latent(width, height, length)
        final_prompt = _normalize_prompt(mode, prompt, optimize_prompt, preserve_strength)

        black = torch.zeros((1, height, width, 3), dtype=torch.float32)
        fitted_source = black

        if mode == "text_to_image (FL2VA)":
            tokens = clip.tokenize(final_prompt, images=[])
            cond = clip.encode_from_tokens_scheduled(tokens)
            checkpoint_note = "Use an FL2VA checkpoint."

        elif mode == "image_to_image (FL2VA)":
            if source_image is None:
                raise ValueError("image_to_image mode requires source_image")
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
                raise ValueError("reference_edit mode requires source_image")
            fitted_source = _resize_image(source_image[:1], width, height, source_fit)
            ref_mode = "max_identity_2048" if reference_size == "max_identity_2048" else "match_generation_area"
            reference, tw, th = _reference_resize(source_image, width, height, ref_mode)
            ref_latent = vae.encode(reference)
            ref_items = [{"type": "image", "data": reference}]
            tokens = clip.tokenize(final_prompt, minimax_ref_items=ref_items)
            cond = clip.encode_from_tokens_scheduled(tokens)
            cond = node_helpers.conditioning_set_values(cond, {
                "minimax_refs": [{
                    "kind": "image",
                    "latent_h": th // 16,
                    "latent_w": tw // 16,
                    "latent": ref_latent,
                }]
            })
            checkpoint_note = "Use a REF2VA checkpoint; this is reference-guided regeneration, not native masked inpainting."

        if natural_frames > 362:
            trained_note = "beyond the documented 124-362-frame training range"
        elif natural_frames >= 124:
            trained_note = "inside the documented 124-362-frame training range"
        else:
            trained_note = "short experimental temporal packet chosen to reduce image-mode compute"
        legacy_note = " Legacy one-frame workflow was automatically upgraded to the five-frame minimum." if legacy_single_frame else ""
        manual_note = f" {manual_frame_note}." if manual_frame_note else ""
        decode_note = (
            f"exact {requested_frames}-frame output"
            if requested_frames == natural_frames
            else f"temporal latent naturally decodes {natural_frames} frames; H3 Exact Frame Decode crops to {requested_frames}"
        )
        info = (
            f"Mode: {mode} | canvas {width}×{height} | requested {requested_frames} frames | {decode_note} | "
            f"{trained_note}. {checkpoint_note} Decode only the video latent; the audio VAE is unnecessary for image output."
            f"{legacy_note}{manual_note}{_prompt_warning(prompt)}"
        )
        return cond, latent, fitted_source, requested_frames, final_prompt, info


class H3TextToImagePrepare:
    """Image-first T2I conditioning with H3's temporal packet hidden behind quality profiles."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "clip": ("CLIP",),
                "prompt": ("STRING", {"multiline": True, "dynamicPrompts": True, "default": ""}),
                "width": ("INT", {"default": 1344, "min": 32, "max": 16384, "step": 32}),
                "height": ("INT", {"default": 768, "min": 32, "max": 16384, "step": 32}),
                "quality_profile": (
                    list(FRAME_PRESETS.keys()),
                    {"default": "recommended | 20 frames"},
                ),
                "optimize_for_still": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Adds a locked-camera still-image prompt wrapper. It does not change frames, resolution, steps, sampler, or model weights.",
                }),
            },
            "optional": {
                "manual_frames": ("INT", {
                    "default": 5, "min": 1, "max": 4096, "step": 1,
                    "tooltip": "Used only when quality_profile is manual. Accepts any exact output count from 1 to 4096.",
                }),
            },
        }

    RETURN_TYPES = ("CONDITIONING", "LATENT", "INT", "STRING", "STRING")
    RETURN_NAMES = ("positive", "h3_latent", "requested_frames", "image_prompt", "run_info")
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
        manual_frames: int = 5,
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
            manual_frames=manual_frames,
        )
        return cond, latent, frames, image_prompt, info


class H3ImageToImagePrepare:
    """FL2VA source-anchor workflow presented as image-to-image."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "clip": ("CLIP",),
                "vae": ("VAE",),
                "source_image": ("IMAGE",),
                "edit_instruction": ("STRING", {"multiline": True, "dynamicPrompts": True, "default": ""}),
                "width": ("INT", {"default": 1344, "min": 32, "max": 16384, "step": 32}),
                "height": ("INT", {"default": 768, "min": 32, "max": 16384, "step": 32}),
                "quality_profile": (
                    list(FRAME_PRESETS.keys()),
                    {"default": "recommended | 20 frames"},
                ),
                "source_fidelity": ("FLOAT", {"default": 0.75, "min": 0.0, "max": 1.0, "step": 0.05}),
                "source_fit": (["crop_center", "contain_pad", "stretch"], {"default": "crop_center"}),
                "optimize_for_still": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Adds a locked-camera still-image prompt wrapper and source-preservation language. Sampling settings are unchanged.",
                }),
            },
            "optional": {
                "manual_frames": ("INT", {
                    "default": 5, "min": 1, "max": 4096, "step": 1,
                    "tooltip": "Used only when quality_profile is manual. Accepts any exact output count from 1 to 4096.",
                }),
            },
        }

    RETURN_TYPES = ("CONDITIONING", "LATENT", "IMAGE", "INT", "STRING", "STRING")
    RETURN_NAMES = ("positive", "h3_latent", "fitted_source", "requested_frames", "image_prompt", "run_info")
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
        manual_frames: int = 5,
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
            manual_frames=manual_frames,
        )


class H3ReferenceEditPrepare:
    """REF2VA reference-guided regeneration exposed as an image edit node."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "clip": ("CLIP",),
                "vae": ("VAE",),
                "source_image": ("IMAGE",),
                "edit_instruction": ("STRING", {"multiline": True, "dynamicPrompts": True, "default": ""}),
                "width": ("INT", {"default": 1344, "min": 32, "max": 16384, "step": 32}),
                "height": ("INT", {"default": 768, "min": 32, "max": 16384, "step": 32}),
                "quality_profile": (
                    list(FRAME_PRESETS.keys()),
                    {"default": "recommended | 20 frames"},
                ),
                "source_fidelity": ("FLOAT", {"default": 0.75, "min": 0.0, "max": 1.0, "step": 0.05}),
                "source_fit": (["crop_center", "contain_pad", "stretch"], {"default": "crop_center"}),
                "reference_detail": (
                    ["match_generation_area", "max_identity_2048"],
                    {"default": "match_generation_area"},
                ),
                "optimize_for_still": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Adds a locked-camera still-image prompt wrapper and reference-preservation language. Sampling settings are unchanged.",
                }),
            },
            "optional": {
                "manual_frames": ("INT", {
                    "default": 5, "min": 1, "max": 4096, "step": 1,
                    "tooltip": "Used only when quality_profile is manual. Accepts any exact output count from 1 to 4096.",
                }),
            },
        }

    RETURN_TYPES = ("CONDITIONING", "LATENT", "IMAGE", "INT", "STRING", "STRING")
    RETURN_NAMES = ("positive", "h3_latent", "fitted_source", "requested_frames", "image_prompt", "run_info")
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
        manual_frames: int = 5,
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
            manual_frames=manual_frames,
        )


class H3ImageDecode:
    """Decode the H3 video stream and crop partial temporal packets exactly."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "samples": ("LATENT",),
                "vae": ("VAE",),
            }
        }

    RETURN_TYPES = ("IMAGE", "INT", "STRING")
    RETURN_NAMES = ("frames", "decoded_frames", "decode_info")
    FUNCTION = "decode"
    CATEGORY = CATEGORY

    def decode(self, samples, vae):
        latent = samples["samples"]
        if latent.is_nested:
            latent = latent.unbind()[0]

        images = vae.decode(latent)
        if images.ndim == 5:
            images = images.reshape(-1, images.shape[-3], images.shape[-2], images.shape[-1])

        natural_frames = int(images.shape[0])
        requested_frames = max(1, int(samples.get("h3_requested_frames", natural_frames)))
        decoded_frames = min(requested_frames, natural_frames)
        if decoded_frames < natural_frames:
            images = images[:decoded_frames].clone()

        if natural_frames < requested_frames:
            info = f"Requested {requested_frames} frames but the VAE decoded only {natural_frames}."
        elif natural_frames == requested_frames:
            info = f"Decoded exactly {decoded_frames} frames."
        else:
            info = f"Decoded a {natural_frames}-frame partial packet and cropped it to exactly {decoded_frames}."
        return images, decoded_frames, info


class H3ImageFrameSelector:
    """Select the strongest still from a decoded H3 frame batch using GPU-friendly metrics."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "frames": ("IMAGE",),
                "strategy": ([
                    "stable_quality",
                    "balanced_edit",
                    "best_quality",
                    "most_similar_to_source",
                    "sharpest",
                    "middle",
                    "last",
                    "manual_index",
                ], {"default": "stable_quality"}),
                "manual_index": ("INT", {"default": 1, "min": 0, "max": 4096, "step": 1}),
                "skip_first_frames": ("INT", {"default": 1, "min": 0, "max": 128, "step": 1}),
                "candidate_start": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05}),
                "candidate_end": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.05}),
                "similarity_weight": ("FLOAT", {"default": 0.60, "min": 0.0, "max": 1.0, "step": 0.05}),
                "top_k": ("INT", {"default": 4, "min": 1, "max": 16, "step": 1}),
            },
            "optional": {
                "source_image": ("IMAGE",),
                "emit_candidate_batch": ("BOOLEAN", {"default": False}),
            },
        }

    RETURN_TYPES = ("IMAGE", "IMAGE", "INT", "FLOAT", "STRING")
    RETURN_NAMES = ("selected_image", "candidate_batch_debug", "selected_index", "selected_score", "score_report")
    FUNCTION = "select"
    CATEGORY = CATEGORY

    @staticmethod
    def _metric_tensor(frames: torch.Tensor, max_side: int = 512) -> torch.Tensor:
        x = frames[..., :3].movedim(-1, 1).float()
        h, w = x.shape[-2:]
        scale = min(1.0, max_side / max(h, w))
        if scale < 1.0:
            nh = max(16, int(round(h * scale)))
            nw = max(16, int(round(w * scale)))
            x = F.interpolate(x, size=(nh, nw), mode="bilinear", align_corners=False, antialias=True)
        return x.clamp(0.0, 1.0)

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
    ):
        if frames.ndim != 4 or frames.shape[0] < 1:
            raise ValueError("frames must be a non-empty ComfyUI IMAGE batch [N,H,W,C]")

        n = int(frames.shape[0])
        if strategy == "manual_index":
            selected_index = max(0, min(n - 1, int(manual_index)))
            # A slice is a view and would keep the storage for the entire decoded
            # frame batch alive in ComfyUI's output cache. Clone the one selected
            # frame so normal image output owns only its own storage.
            chosen = frames[selected_index:selected_index + 1].clone()
            debug = chosen if emit_candidate_batch else frames.new_empty((0, *frames.shape[1:]))
            return chosen, debug, selected_index, 1.0, f"Manual frame {selected_index}/{n - 1}"
        if strategy == "middle":
            selected_index = n // 2
            chosen = frames[selected_index:selected_index + 1].clone()
            debug = chosen if emit_candidate_batch else frames.new_empty((0, *frames.shape[1:]))
            return chosen, debug, selected_index, 1.0, f"Middle frame {selected_index}/{n - 1}"
        if strategy == "last":
            selected_index = n - 1
            chosen = frames[-1:].clone()
            debug = chosen if emit_candidate_batch else frames.new_empty((0, *frames.shape[1:]))
            return chosen, debug, selected_index, 1.0, f"Last frame {selected_index}/{n - 1}"

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

        if strategy == "sharpest":
            scores = sharp_n
        elif strategy == "stable_quality":
            scores = stable_quality
        elif strategy == "most_similar_to_source" and similarity is not None:
            scores = similarity
        elif strategy == "balanced_edit" and similarity is not None:
            sw = max(0.0, min(1.0, float(similarity_weight)))
            scores = sw * similarity + (1.0 - sw) * stable_quality
        else:
            scores = stable_quality if strategy == "balanced_edit" else quality

        best_local = int(torch.argmax(scores).item())
        selected_index = int(candidate_indices[best_local].item())
        selected_score = float(scores[best_local].item())
        # Detach the selected image from the full decoded batch's storage.
        selected = frames[selected_index:selected_index + 1].clone()

        if emit_candidate_batch:
            k = min(int(top_k), len(scores))
            top_local = torch.topk(scores, k=k, largest=True, sorted=True).indices
            top_global = candidate_indices[top_local].long()
            candidate_output = frames.index_select(0, top_global)
        else:
            # frames[:0] would still be a view backed by the full batch. Return a
            # genuinely independent zero-length tensor and skip all top-k copies.
            candidate_output = frames.new_empty((0, *frames.shape[1:]))

        sim_text = "n/a" if similarity is None else f"{float(similarity[best_local]):.4f}"
        report = (
            f"Selected frame {selected_index}/{n - 1} with {strategy}; score={selected_score:.4f}, "
            f"sharpness={float(sharp_n[best_local]):.4f}, quality={float(quality[best_local]):.4f}, "
            f"stability={float(stability[best_local]):.4f}, similarity={sim_text}; candidates={start}..{end - 1}; "
            f"candidate batch {'emitted' if emit_candidate_batch else 'suppressed'}."
        )
        return selected, candidate_output, selected_index, selected_score, report


class H3ImageMaskedComposite:
    """Composite an H3 edit over the fitted source. This is post-compositing, not mask-aware H3 denoising."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "original": ("IMAGE",),
                "edited": ("IMAGE",),
                "mask": ("MASK",),
                "feather_pixels": ("INT", {"default": 12, "min": 0, "max": 256, "step": 1}),
                "invert_mask": ("BOOLEAN", {"default": False}),
                "edited_fit": (["crop_center", "contain_pad", "stretch"], {"default": "stretch"}),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("composited_image",)
    FUNCTION = "composite"
    CATEGORY = CATEGORY

    def composite(
        self,
        original: torch.Tensor,
        edited: torch.Tensor,
        mask: torch.Tensor,
        feather_pixels: int,
        invert_mask: bool,
        edited_fit: str,
    ):
        original = original[:1, ..., :3]
        h, w = int(original.shape[1]), int(original.shape[2])
        edited = _resize_image(edited[:1], w, h, edited_fit)

        if mask.ndim == 2:
            mask = mask.unsqueeze(0)
        m = mask[:1].unsqueeze(1).to(device=edited.device, dtype=edited.dtype)
        m = F.interpolate(m, size=(h, w), mode="bilinear", align_corners=False)
        if invert_mask:
            m = 1.0 - m
        feather = int(feather_pixels)
        if feather > 0:
            kernel = feather * 2 + 1
            m = F.avg_pool2d(m, kernel_size=kernel, stride=1, padding=feather)
        m = m.clamp(0.0, 1.0).movedim(1, -1)
        output = original.to(edited.device, edited.dtype) * (1.0 - m) + edited * m
        return (output.clamp(0.0, 1.0),)



class H3SamplingSettings:
    """Combined H3 sampler, scheduler and sigma-shift selector.

    Sampler and scheduler options are populated dynamically from the installed
    ComfyUI version, so newly added core samplers/schedulers appear automatically.
    """

    @classmethod
    def INPUT_TYPES(cls):
        scheduler_options = list(comfy.samplers.SCHEDULER_NAMES)
        if "beta_custom" not in scheduler_options:
            scheduler_options.append("beta_custom")
        return {
            "required": {
                "model": ("MODEL",),
                "sampler_name": (list(comfy.samplers.SAMPLER_NAMES), {"default": "res_multistep" if "res_multistep" in comfy.samplers.SAMPLER_NAMES else comfy.samplers.SAMPLER_NAMES[0]}),
                "scheduler": (scheduler_options, {"default": "simple" if "simple" in scheduler_options else scheduler_options[0]}),
                "steps": ("INT", {"default": 20, "min": 1, "max": 10000, "step": 1}),
                "denoise": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01}),
                "shift_video": ("FLOAT", {"default": 12.0, "min": 0.01, "max": 100.0, "step": 0.01}),
                "shift_audio": ("FLOAT", {"default": 3.0, "min": 0.01, "max": 100.0, "step": 0.01}),
                "beta_alpha": ("FLOAT", {"default": 0.6, "min": 0.01, "max": 50.0, "step": 0.01}),
                "beta_beta": ("FLOAT", {"default": 0.6, "min": 0.01, "max": 50.0, "step": 0.01}),
            }
        }

    RETURN_TYPES = ("MODEL", "SAMPLER", "SIGMAS", "STRING")
    RETURN_NAMES = ("shifted_model", "sampler", "sigmas", "sampling_info")
    FUNCTION = "build"
    CATEGORY = CATEGORY

    @staticmethod
    def _apply_h3_shift(model, shift_video: float, shift_audio: float):
        m = model.clone()

        class ModelSamplingAdvanced(comfy.model_sampling.ModelSamplingDiscreteFlow, comfy.model_sampling.CONST):
            pass

        original = m.get_model_object("model_sampling")
        model_sampling = ModelSamplingAdvanced(m.model.model_config)
        model_sampling.set_parameters(shift=float(shift_video))
        if hasattr(original, "noise_scale"):
            model_sampling.set_noise_scale(original.noise_scale)
        m.add_object_patch("model_sampling", model_sampling)

        transformer_options = m.model_options.get("transformer_options", {}).copy()
        transformer_options["minimax_h3_sigma_shift_video"] = float(shift_video)
        transformer_options["minimax_h3_sigma_shift_audio"] = float(shift_audio)
        m.model_options["transformer_options"] = transformer_options
        return m

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
        shifted_model = self._apply_h3_shift(model, shift_video, shift_audio)
        sampler = comfy.samplers.sampler_object(sampler_name)

        model_sampling = shifted_model.get_model_object("model_sampling")
        steps = int(steps)
        denoise = float(denoise)

        if scheduler == "beta_custom":
            # BetaSamplingScheduler equivalent with user-controlled alpha/beta.
            sigmas = comfy.samplers.beta_scheduler(
                model_sampling,
                steps,
                alpha=float(beta_alpha),
                beta=float(beta_beta),
            ).cpu()
        else:
            total_steps = steps
            if denoise < 1.0:
                if denoise <= 0.0:
                    sigmas = torch.FloatTensor([])
                    info = (
                        f"sampler={sampler_name} | scheduler={scheduler} | steps={steps} | "
                        f"denoise=0 | shift_video={shift_video:g} | shift_audio={shift_audio:g}"
                    )
                    return shifted_model, sampler, sigmas, info
                total_steps = max(steps, int(steps / denoise))
            sigmas = comfy.samplers.calculate_sigmas(model_sampling, scheduler, total_steps).cpu()
            sigmas = sigmas[-(steps + 1):]

        beta_note = f" | beta alpha={beta_alpha:g}, beta={beta_beta:g}" if scheduler == "beta_custom" else ""
        info = (
            f"sampler={sampler_name} | scheduler={scheduler} | steps={steps} | denoise={denoise:g} | "
            f"shift_video={shift_video:g} | shift_audio={shift_audio:g}{beta_note}"
        )
        return shifted_model, sampler, sigmas, info


class H3ImageSamplingPreset:
    """Small, safe image-mode sampling UI built from official H3 settings."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL",),
                "sampling_profile": (
                    list(SAMPLING_PROFILES.keys()),
                    {"default": "official quality | 20 steps"},
                ),
            },
            "optional": {
                "manual_steps": ("INT", {
                    "default": 20,
                    "min": 1,
                    "max": 10000,
                    "step": 1,
                    "tooltip": "Used only by the manual-steps profile. It keeps the official res_multistep + simple sampling path.",
                }),
            },
        }

    RETURN_TYPES = ("MODEL", "SAMPLER", "SIGMAS", "STRING")
    RETURN_NAMES = ("model", "sampler", "sigmas", "sampling_info")
    FUNCTION = "build"
    CATEGORY = CATEGORY

    def build(self, model, sampling_profile: str, manual_steps: int = 20):
        sampler_name, scheduler, steps = SAMPLING_PROFILES[sampling_profile]
        if steps is None:
            steps = max(1, int(manual_steps))
        shifted_model, sampler, sigmas, info = H3SamplingSettings().build(
            model=model,
            sampler_name=sampler_name,
            scheduler=scheduler,
            steps=steps,
            denoise=1.0,
            shift_video=12.0,
            shift_audio=3.0,
            beta_alpha=0.6,
            beta_beta=0.6,
        )
        manual_note = f" | manual_steps={steps}" if sampling_profile == MANUAL_SAMPLING_PROFILE else ""
        return shifted_model, sampler, sigmas, f"profile={sampling_profile}{manual_note} | {info}"


class H3ModelDownloader:
    """Download only the official Comfy-Org MiniMax H3 variants selected by the user."""

    FL2VA_OPTIONS = {
        "none": None,
        "pruned INT8 ConvRot | recommended / smallest": "diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors",
        "INT8 ConvRot | full": "diffusion_models/minimax_h3_fl2va_int8_convrot.safetensors",
        "BF16 | maximum size": "diffusion_models/minimax_h3_fl2va_bf16.safetensors",
    }
    REF2VA_OPTIONS = {
        "none": None,
        "pruned INT8 ConvRot | recommended / smallest": "diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors",
        "INT8 ConvRot | full": "diffusion_models/minimax_h3_ref2va_int8_convrot.safetensors",
        "BF16 | maximum size": "diffusion_models/minimax_h3_ref2va_bf16.safetensors",
    }
    TEXT_OPTIONS = {
        "none": None,
        "NVFP4 AWQ | recommended / smallest": "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        "INT8 ConvRot": "text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
        "BF16 | maximum size": "text_encoders/qwen3vl_32b_minimax_h3_bf16.safetensors",
    }
    VIDEO_VAE_PATH = "vae/minimax_h3_video_vae_fp16.safetensors"
    AUDIO_VAE_PATH = "vae/minimax_h3_audio_vae_fp32.safetensors"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "fl2va_model": (list(cls.FL2VA_OPTIONS.keys()), {"default": "pruned INT8 ConvRot | recommended / smallest"}),
                "ref2va_model": (list(cls.REF2VA_OPTIONS.keys()), {"default": "none"}),
                "text_encoder": (list(cls.TEXT_OPTIONS.keys()), {"default": "NVFP4 AWQ | recommended / smallest"}),
                "video_vae": ("BOOLEAN", {"default": True}),
                "audio_vae": ("BOOLEAN", {"default": False}),
                "action": (["download missing", "check only", "force redownload"], {"default": "download missing"}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = (
        "fl2va_filename",
        "ref2va_filename",
        "text_encoder_filename",
        "video_vae_filename",
        "audio_vae_filename",
        "status",
    )
    FUNCTION = "download_selected"
    CATEGORY = CATEGORY
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    @staticmethod
    def _folder_for_remote(remote_path: str) -> str:
        return remote_path.split("/", 1)[0]

    @staticmethod
    def _destination(remote_path: str) -> "Path":
        from pathlib import Path

        folder_key = H3ModelDownloader._folder_for_remote(remote_path)
        configured = folder_paths.get_folder_paths(folder_key)
        if not configured:
            raise RuntimeError(f"ComfyUI has no configured model path for {folder_key}")
        destination = Path(configured[0]) / Path(remote_path).name
        destination.parent.mkdir(parents=True, exist_ok=True)
        return destination

    def download_selected(
        self,
        fl2va_model: str,
        ref2va_model: str,
        text_encoder: str,
        video_vae: bool,
        audio_vae: bool,
        action: str,
    ):
        from pathlib import Path
        from .scripts.download_models import BASE, download_stream

        selected = [
            self.FL2VA_OPTIONS[fl2va_model],
            self.REF2VA_OPTIONS[ref2va_model],
            self.TEXT_OPTIONS[text_encoder],
            self.VIDEO_VAE_PATH if video_vae else None,
            self.AUDIO_VAE_PATH if audio_vae else None,
        ]
        selected = [item for item in selected if item]
        if not selected:
            raise ValueError("No MiniMax H3 model file selected")

        lines = []
        force = action == "force redownload"
        for remote_path in selected:
            destination = self._destination(remote_path)
            if destination.exists() and not force:
                lines.append(f"EXISTS: {destination}")
                continue
            if action == "check only":
                lines.append(f"MISSING: {destination}")
                continue
            lines.append(f"DOWNLOADING: {destination.name}")
            download_stream(f"{BASE}/{remote_path}?download=true", destination)
            lines.append(f"DONE: {destination}")

        # Touch ComfyUI's model lists after downloads. Existing loader widgets may still
        # need a browser refresh, but new loader nodes will see the files immediately.
        for key in ("diffusion_models", "text_encoders", "vae"):
            try:
                folder_paths.get_filename_list(key)
            except Exception:
                pass

        def filename(value):
            return "none" if value is None else Path(value).name

        report = "\n".join(lines)
        return (
            filename(self.FL2VA_OPTIONS[fl2va_model]),
            filename(self.REF2VA_OPTIONS[ref2va_model]),
            filename(self.TEXT_OPTIONS[text_encoder]),
            Path(self.VIDEO_VAE_PATH).name if video_vae else "none",
            Path(self.AUDIO_VAE_PATH).name if audio_vae else "none",
            report,
        )


NODE_CLASS_MAPPINGS = {
    "H3ModelDownloader": H3ModelDownloader,
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
    "H3ImageMaskedComposite": H3ImageMaskedComposite,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "H3ModelDownloader": "MiniMax H3 • Select & Auto-Download Models",
    "H3ImageSamplingPreset": "MiniMax H3 Image • Sampling Preset",
    "H3SamplingSettings": "MiniMax H3 Image • Advanced Sampling",
    "H3ImageResolutionPreset": "MiniMax H3 Image • Resolution Preset",
    "H3ImageResolution": "MiniMax H3 Image • Advanced Resolution",
    "H3TextToImagePrepare": "MiniMax H3 Image • Text to Image",
    "H3ImageToImagePrepare": "MiniMax H3 Image • Image to Image",
    "H3ReferenceEditPrepare": "MiniMax H3 Image • Reference Edit",
    "H3ImagePrepare": "MiniMax H3 Image • Legacy Combined Prepare",
    "H3ImageDecode": "MiniMax H3 Image • Exact Frame Decode",
    "H3ImageFrameSelector": "MiniMax H3 Image • Single Image Output",
    "H3ImageMaskedComposite": "MiniMax H3 • Masked Edit Composite",
}
