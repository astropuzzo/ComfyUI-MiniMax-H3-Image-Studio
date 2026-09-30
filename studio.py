# SPDX-License-Identifier: Unlicense
"""The small, single-frame public interface. Old nodes live in nodes.py."""
from __future__ import annotations

import math
import re
import time

import torch
import comfy.model_management
import comfy.nested_tensor
import comfy.samplers
import folder_paths
import node_helpers
import nodes as core_nodes
from comfy_api.latest import io
from comfy_extras.nodes_custom_sampler import BasicGuider, Noise_RandomNoise, SamplerCustomAdvanced

from .nodes import ASPECT_RATIOS, _fit_area_to_ratio, _reference_resize
from .still_decode import decode_still


CATEGORY = "MiniMax H3/Image Studio"
Models = io.Custom("H3_STUDIO_MODELS")
Job = io.Custom("H3_STILL_JOB")
REFERENCE_NAMES = [f"reference_image_{index}" for index in range(2, 10)]


def model_files(category, predicate):
    return [name for name in folder_paths.get_filename_list(category) if predicate(name.lower())]


def model_default(files, preferred):
    return next((name for name in files if name.replace("\\", "/").split("/")[-1] == preferred),
                files[0] if files else None)


def canvas(aspect_ratio, megapixels, source_image=None, width=2048, height=1536):
    if aspect_ratio == "custom pixels":
        if not 64 <= width <= 16384 or not 64 <= height <= 16384:
            raise ValueError("Custom width and height must be between 64 and 16384 pixels.")
        return max(64, round(width / 32) * 32), max(64, round(height / 32) * 32)
    if not math.isfinite(megapixels) or not 0.1 <= megapixels <= 32:
        raise ValueError("Megapixels must be between 0.1 and 32. There is no native 1 MP cap.")
    if aspect_ratio == "source image":
        if source_image is None:
            raise ValueError("Source aspect ratio requires an image.")
        ratio = source_image.shape[2] / source_image.shape[1]
    else:
        if aspect_ratio not in ASPECT_RATIOS:
            raise ValueError("Choose a listed aspect ratio or custom pixels.")
        a, b = ASPECT_RATIOS[aspect_ratio]
        ratio = a / b
    return _fit_area_to_ratio(megapixels * 1024 ** 2, ratio, 32, None)


def one_image(image):
    if not isinstance(image, torch.Tensor) or image.ndim != 4 or image.shape[0] < 1 or image.shape[-1] < 3:
        raise ValueError("Connect a non-empty ComfyUI IMAGE [B,H,W,C] to every used reference.")
    if image.shape[1] < 1 or image.shape[2] < 1:
        raise ValueError("A reference image cannot have empty dimensions.")
    return image[:1, :, :, :3]


def prepare(models, prompt, width, height, references=()):
    if not prompt or not prompt.strip():
        raise ValueError("Write an image description or edit instruction.")
    clip, vae = models["clip"], models["vae"]
    prompt = prompt.strip()
    items, blocks = [], []
    for reference in references:
        resized, rw, rh = _reference_resize(one_image(reference), width, height, "match_generation_area")
        items.append({"type": "image", "data": resized})
        blocks.append({"kind": "image", "latent_h": rh // 16, "latent_w": rw // 16,
                       "latent": vae.encode(resized)})
    # Anchor conditioning would occupy the ONLY output frame and prevent editing.
    # References remain separate from the generated latent, as in Fizgig's edit graph.
    if items:
        if len(items) == 1 and not re.search(r"<picture\s+1>", prompt, re.I):
            prompt = "<Picture 1>. " + prompt
        tokens = clip.tokenize(prompt, minimax_ref_items=items)
    else:
        tokens = clip.tokenize(prompt, images=[])
    positive = clip.encode_from_tokens_scheduled(tokens)
    if blocks:
        positive = node_helpers.conditioning_set_values(positive, {"minimax_refs": blocks})

    device = comfy.model_management.intermediate_device()
    video = torch.zeros((1, 24, 1, height // 16, width // 16), device=device)
    audio = torch.zeros((1, 32, 2, 2), device=device)
    latent = {"samples": comfy.nested_tensor.NestedTensor((video, audio))}
    return {"models": models, "positive": positive, "latent": latent,
            "width": width, "height": height, "prompt": prompt, "references": len(items)}


def resolution_inputs(edit=False):
    options = (["source image"] if edit else []) + list(ASPECT_RATIOS) + ["custom pixels"]
    return [
        io.Combo.Input("aspect_ratio", options=options, default="source image" if edit else "1:1 square",
                       tooltip="Choose the canvas shape. Source image keeps Picture 1's ratio; custom pixels uses the advanced width/height controls."),
        io.Float.Input("megapixels", default=3.0, min=0.1, max=32.0, step=0.1,
                       tooltip="Target image size, rounded to a 32-pixel grid. Start at 3 MP; 1 MP is a preview, 4–8 MP costs more. Ignored in custom pixels mode."),
        io.Int.Input("width", default=2048, min=64, max=16384, step=32, optional=True, advanced=True,
                     tooltip="Exact pixel width, only when aspect_ratio is custom pixels."),
        io.Int.Input("height", default=1536, min=64, max=16384, step=32, optional=True, advanced=True,
                     tooltip="Exact pixel height, only when aspect_ratio is custom pixels."),
    ]


class H3StudioModels(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        diffusion = model_files("diffusion_models", lambda name: "h3" in name and "minimax" in name)
        encoders = model_files("text_encoders", lambda name: "qwen" in name and "minimax" in name)
        vaes = model_files("vae", lambda name: "minimax_h3_video_vae" in name)
        turbo = ["None"] + model_files("loras", lambda name: "minimax_h3_turbo_v4_step600_ema" in name)
        return io.Schema(
            node_id="H3StudioModels", display_name="H3 • Models", category=CATEGORY,
            description="Load one shared H3 model, Qwen encoder and VIDEO VAE for generation and editing. No image VAE, hybrid model, audio VAE or refiner is required.",
            inputs=[
                io.Combo.Input("diffusion_model", options=diffusion,
                               default=model_default(diffusion, "minimax_h3_fl2va_pruned_int8_convrot.safetensors"),
                               tooltip="Official FL2VA is the single starting model for both workflows, following Fizgig. REF2VA can also be selected for edit comparisons."),
                io.Combo.Input("text_encoder", options=encoders,
                               default=model_default(encoders, "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"),
                               tooltip="MiniMax H3 Qwen text/vision encoder; shared by both workflows."),
                io.Combo.Input("video_vae", options=vaes,
                               default=model_default(vaes, "minimax_h3_video_vae_int8_convrot.safetensors"),
                               tooltip="Official H3 video VAE, INT8 ConvRot or FP16. The still decoder is included."),
                io.Combo.Input("turbo_lora", options=turbo, default="None", optional=True, advanced=True,
                               tooltip="Optional Larry v4 step600 EMA adapter. Fizgig's STILL recipe uses strength 0.38 and 20 steps. None requires no extra download; start at 50 steps."),
            ],
            outputs=[Models.Output(display_name="models", tooltip="Shared H3 models. Connect to Text to Image or Image Edit.")],
        )

    @classmethod
    def execute(cls, diffusion_model, text_encoder, video_vae, turbo_lora="None"):
        # Native loaders retain ComfyUI's quantization, offloading and model cache.
        model = core_nodes.UNETLoader().load_unet(diffusion_model, "default")[0]
        clip = core_nodes.CLIPLoader().load_clip(text_encoder, "minimax", "default")[0]
        vae = core_nodes.VAELoader().load_vae(video_vae)[0]
        if turbo_lora != "None":
            if "minimax_h3_turbo_v4_step600_ema" not in turbo_lora.lower():
                raise ValueError("This optional still recipe supports Larry v4 step600 EMA only.")
            model = core_nodes.LoraLoaderModelOnly().load_lora_model_only(model, turbo_lora, 0.38)[0]
        return io.NodeOutput({"model": model, "clip": clip, "vae": vae,
                              "diffusion_model": diffusion_model, "turbo_lora": turbo_lora})


class H3StudioGenerate(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="H3StudioGenerate", display_name="H3 • Text to Image", category=CATEGORY,
            description="Describe one image and choose its size. Prepares exactly one H3 latent frame; no frame count or selection is needed.",
            inputs=[Models.Input("models", tooltip="Connect H3 Models."),
                    io.String.Input("prompt", multiline=True, dynamic_prompts=True, default="",
                                    tooltip="Describe the final image. Your prompt is used directly without hidden preservation wording."),
                    *resolution_inputs()],
            outputs=[Job.Output(display_name="image_job", tooltip="Prepared still and cached prompt conditioning. Connect to Render Image.")],
        )

    @classmethod
    def execute(cls, models, prompt, aspect_ratio, megapixels, width=2048, height=1536):
        w, h = canvas(aspect_ratio, megapixels, width=width, height=height)
        return io.NodeOutput(prepare(models, prompt, w, h))


class H3StudioEdit(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="H3StudioEdit", display_name="H3 • Image Edit", category=CATEGORY,
            description="Image-to-image and reference editing in one node. Picture 1 is the source; additional picture sockets appear as needed, up to nine total. Output is a newly sampled still, never a locked source frame.",
            inputs=[Models.Input("models", tooltip="Connect the same H3 Models used for generation."),
                    io.Image.Input("image", tooltip="Source image, <Picture 1>. Only the first item of a connected batch is used."),
                    io.String.Input("instruction", multiline=True, dynamic_prompts=True, default="",
                                    tooltip="Describe the change. With multiple references explicitly assign roles to <Picture 1>, <Picture 2>, etc. There is no hidden fidelity prompt or denoise slider."),
                    *resolution_inputs(edit=True),
                    io.Autogrow.Input("references", optional=True,
                        template=io.Autogrow.TemplateNames(
                            input=io.Image.Input("reference", tooltip="One additional picture per socket, in displayed order. Use <Picture N> in the instruction."),
                            names=REFERENCE_NAMES, min=0),
                        tooltip="Optional additional reference pictures. Add only what you need; eight extra pictures maximum.")],
            outputs=[Job.Output(display_name="image_job", tooltip="Editable reference conditioning and one-frame latent. Connect to Render Image.")],
        )

    @classmethod
    def execute(cls, models, image, instruction, aspect_ratio, megapixels, width=2048, height=1536, references=None):
        source = one_image(image)
        extras = references or {}
        if set(extras) - set(REFERENCE_NAMES):
            raise ValueError("Unknown reference socket; use the additional image inputs on Image Edit.")
        ordered = [source] + [one_image(extras[name]) for name in REFERENCE_NAMES if extras.get(name) is not None]
        w, h = canvas(aspect_ratio, megapixels, source, width, height)
        return io.NodeOutput(prepare(models, instruction, w, h, ordered))


class H3StudioRender(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="H3StudioRender", display_name="H3 • Render Image", category=CATEGORY,
            description="Sample one H3 latent frame and decode one image using Fizgig's temporal-group technique. Prompt/reference preparation stays cached when only seed or steps changes. No video packet is sampled.",
            inputs=[Job.Input("image_job", tooltip="Connect Text to Image or Image Edit."),
                    io.Int.Input("steps", default=50, min=1, max=200, step=1,
                                 tooltip="Always manual. Start at 50 without a LoRA, or 20 with the optional Larry v4 adapter at 0.38 (Fizgig's still recipe). Fewer steps trade quality for speed."),
                    io.Int.Input("seed", default=42, min=0, max=0xffffffffffffffff, control_after_generate=True,
                                 tooltip="Seed for repeatable sampling. Changing only seed or steps reuses cached prompt/reference encoding."),
                    io.Combo.Input("sampler_name", options=list(comfy.samplers.SAMPLER_NAMES), default="er_sde",
                                   optional=True, advanced=True, tooltip="ER-SDE is the Fizgig still starting recipe. Change only for controlled comparisons."),
                    io.Combo.Input("scheduler", options=list(comfy.samplers.SCHEDULER_NAMES), default="simple",
                                   optional=True, advanced=True, tooltip="Simple is the starting schedule. Uses the loaded H3 model's native flow shifts.")],
            outputs=[io.Image.Output(display_name="image", tooltip="Exactly one image. Connect directly to Save Image or Preview Image."),
                     io.String.Output(display_name="run_info", tooltip="Actual canvas, steps, seed, sampler, reference count and sampling/decode seconds; excludes model loading and cached preparation.")],
        )

    @classmethod
    def execute(cls, image_job, steps, seed, sampler_name="er_sde", scheduler="simple"):
        if not 1 <= steps <= 200:
            raise ValueError("Steps must be between 1 and 200.")
        started = time.perf_counter()
        models = image_job["models"]
        model = models["model"]
        sampler = comfy.samplers.sampler_object(sampler_name)
        sigmas = comfy.samplers.calculate_sigmas(model.get_model_object("model_sampling"), scheduler, steps).cpu()
        guider = BasicGuider.execute(model, image_job["positive"])[0]
        result = SamplerCustomAdvanced.execute(Noise_RandomNoise(seed), guider, sampler, sigmas, image_job["latent"])[0]
        sampled = time.perf_counter()
        images = decode_still(models["vae"], result)
        finished = time.perf_counter()
        info = (
            f"{image_job['width']}x{image_job['height']} | one sampled latent frame | {steps} steps | seed {seed} | "
            f"{sampler_name}/{scheduler} | {image_job['references']} reference(s) | "
            f"sample {sampled-started:.2f}s, decode {finished-sampled:.2f}s | "
            f"model {models['diffusion_model']} | LoRA {models['turbo_lora']}"
        )
        return io.NodeOutput(images, info)


NODE_CLASS_MAPPINGS = {cls.__name__: cls for cls in (H3StudioModels, H3StudioGenerate, H3StudioEdit, H3StudioRender)}
NODE_DISPLAY_NAME_MAPPINGS = {name: cls.GET_SCHEMA().display_name for name, cls in NODE_CLASS_MAPPINGS.items()}
