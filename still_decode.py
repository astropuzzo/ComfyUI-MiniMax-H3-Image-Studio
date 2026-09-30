# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Neill
# Adapted from ComfyUI-Fizgig-H3-Still, commit 10d5171.
# The complete license and attribution are in THIRD_PARTY_NOTICES.md.
"""Decode one sampled H3 latent without the stock lone-token banding."""

import torch
import comfy.model_management


def decode_still(vae, samples):
    latent = samples["samples"]
    if latent.is_nested:
        latent = latent.unbind()[0]
    if latent.ndim != 5 or latent.shape[1] != 24 or latent.shape[2] != 1:
        raise ValueError("Render Image expects one H3 latent frame, not a video packet.")

    fsm = vae.first_stage_model
    required = ("_adaptive_decode", "_finalize_pixels", "latents_mean", "latents_std")
    if not all(hasattr(fsm, name) for name in required):
        raise ValueError("Use the official H3 video VAE and update ComfyUI. An image VAE is not required.")

    # Repeat only at decode time. The diffusion model sampled exactly ONE frame.
    group_size, keep = 5, 3
    shape = (1, latent.shape[1], group_size, *latent.shape[-2:])
    memory = vae.memory_used_decode(shape, vae.vae_dtype)
    comfy.model_management.load_models_gpu(
        [vae.patcher], memory_required=memory,
        force_full_load=getattr(vae, "disable_offload", False),
    )
    output = []
    with torch.no_grad():
        for index in range(latent.shape[0]):
            z = latent[index:index + 1].to(device=vae.device, dtype=vae.vae_dtype)
            mean = fsm.latents_mean.view(1, -1, 1, 1, 1).to(z)
            std = fsm.latents_std.view(1, -1, 1, 1, 1).to(z)
            group = (z * std + mean).repeat(1, 1, group_size, 1, 1)
            raw = fsm._adaptive_decode(group)
            pixels = fsm._finalize_pixels(raw[:, :, keep:keep + 1])
            # Own the output storage: a one-frame view must not retain all 20
            # decoded frames, especially when the intermediate device is GPU.
            image = pixels[:, :, 0].movedim(1, -1).to(
                device=comfy.model_management.intermediate_device(),
                dtype=torch.float32, copy=True,
            )
            output.append(image)
            del z, mean, std, group, raw, pixels
    return torch.cat(output)
