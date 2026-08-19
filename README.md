![MiniMax H3 Image Studio](assets/branding/minimax-h3-banner.svg)

# MiniMax H3 Image Studio

ComfyUI nodes and workflows for MiniMax H3 text-to-image, image-to-image, and REF2VA reference editing.

MiniMax H3 is an audio-video model. These nodes generate a short frame packet, decode it, and select one still image.

## Requirements

- ComfyUI 0.30.0 or newer
- MiniMax H3 diffusion model
- MiniMax H3 Qwen text encoder
- MiniMax H3 video VAE
- Matching Turbo adapter only for a Turbo workflow

Use the [official ComfyUI MiniMax H3 guide](https://docs.comfy.org/tutorials/video/minimax/minimax-h3) for model downloads and installation.

| Recommended official model | Folder |
|---|---|
| `minimax_h3_fl2va_pruned_int8_convrot.safetensors` or `minimax_h3_ref2va_pruned_int8_convrot.safetensors` | `ComfyUI/models/diffusion_models/` |
| `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | `ComfyUI/models/text_encoders/` |
| `minimax_h3_video_vae_fp16.safetensors` | `ComfyUI/models/vae/` |
| Exact Turbo adapter named in the sampling table | `ComfyUI/models/loras/` |

The audio VAE is not required for image output.

## Installation

### ComfyUI Manager

Search for **MiniMax H3 Image Studio**, or run:

```bash
comfy node install minimax-h3-image-studio
```

### Git

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/astropuzzo/ComfyUI-MiniMax-H3-Image-Studio.git
```

Restart ComfyUI after installing or updating.

To update a Git installation:

```bash
git -C ComfyUI/custom_nodes/ComfyUI-MiniMax-H3-Image-Studio pull --ff-only
```

## Workflows

Open a file from `examples/ui/`, or drag a file from `examples/png/` onto the canvas.

| Workflow | UI JSON | PNG | API JSON |
|---|---|---|---|
| Text to Image | [Open](examples/ui/H3_T2I.json) | [Open](examples/png/H3_T2I.png) | [API](examples/api/H3_T2I_API.json) |
| Text to Image, single frame (experimental) | [Open](examples/ui/H3_T2I_SINGLE.json) | [Open](examples/png/H3_T2I_SINGLE.png) | [API](examples/api/H3_T2I_SINGLE_API.json) |
| Image to Image | [Open](examples/ui/H3_I2I.json) | [Open](examples/png/H3_I2I.png) | [API](examples/api/H3_I2I_API.json) |
| Image to Image, single frame (experimental) | [Open](examples/ui/H3_I2I_SINGLE.json) | [Open](examples/png/H3_I2I_SINGLE.png) | [API](examples/api/H3_I2I_SINGLE_API.json) |
| Reference Edit | [Open](examples/ui/H3_REFERENCE_EDIT.json) | [Open](examples/png/H3_REFERENCE_EDIT.png) | [API](examples/api/H3_REFERENCE_EDIT_API.json) |
| Reference Edit, single image (experimental) | [Open](examples/ui/H3_REFERENCE_SINGLE.json) | [Open](examples/png/H3_REFERENCE_SINGLE.png) | [API](examples/api/H3_REFERENCE_SINGLE_API.json) |
| Image to Image, Turbo v1.0 | [Open](examples/ui/H3_I2I_TURBO.json) | [Open](examples/png/H3_I2I_TURBO.png) | [API](examples/api/H3_I2I_TURBO_API.json) |
| Fast Detail Refiner | [Open](examples/ui/H3_FAST_REFINER.json) | [Open](examples/png/H3_FAST_REFINER.png) | [API](examples/api/H3_FAST_REFINER_API.json) |

Files in `examples/api/` are prompt JSON for API clients. They do not contain a canvas layout.

For image-to-image and reference-edit workflows, select an image in every `Load Image` node before running the workflow.

## Nodes

| Node | Function |
|---|---|
| `Text to Image` | Prepares FL2VA text conditioning for multi-frame or one-frame output. |
| `Image to Image` | Uses an FL2VA frame-0 anchor for multi-frame editing and REF2VA source conditioning for editable one-frame output. |
| `Reference Edit` | Prepares REF2VA editing with up to nine ordered references. |
| `Resolution Preset` | Calculates common H3 canvas sizes. |
| `Sampling Preset` | Configures documented recipes or a complete custom sampler setup. |
| `Exact Frame Decode` | Decodes the requested frame profile. |
| `Single Image Output` | Selects one frame or returns the decoded batch. |
| `Advanced Resolution` | Calculates custom canvas sizes. |
| `Advanced Sampling` | Exposes sampler, scheduler, denoise, and sigma shifts. |
| `Advanced Combined Prepare` | Combines all preparation modes in one node. |
| `Detail Tone Lock` | Blends refined detail while restoring the H3 image's broad lighting and color. |

## Optional detail refinement

`H3_FAST_REFINER` is a separate second pass. Load a finished H3 image, or connect any `Single Image Output` directly to `Scale Image to Total Pixels`. The H3 workflow remains unchanged when the refiner is not used.

The default stack follows ComfyUI's native FLUX.2 Klein 4B distilled image-edit recipe:

| Component | File | Folder |
|---|---|---|
| Diffusion model | `flux-2-klein-4b-fp8.safetensors` | `ComfyUI/models/diffusion_models/` |
| Text encoder | `qwen_3_4b.safetensors` | `ComfyUI/models/text_encoders/` |
| VAE | `flux2-vae.safetensors` | `ComfyUI/models/vae/` |

Downloads and model terms are listed in the [official ComfyUI FLUX.2 Klein guide](https://docs.comfy.org/tutorials/flux/flux-2-klein). The 4B checkpoint is the default because its four-step distilled edit path fits interactive use and has an Apache-2.0 model license.

The workflow uses Euler, `Flux2Scheduler`, four steps, CFG 1, and a one-megapixel working image. On the tested RTX 4090 it took 2.1-2.6 seconds with models warm and about 7 seconds after a clean ComfyUI start.

The prompt is intentionally generic and preservation-first. Do not append the complete H3 generation prompt by default: scene descriptions can encourage the edit model to reconstruct the composition. If stronger repair is worth more drift, edit the refiner prompt and raise `refinement_strength`; the shipped `0.45` default favors identity and geometry preservation.

`Detail Tone Lock` uses frequency separation rather than a mask: FLUX supplies fine detail, while H3 supplies broad lighting and color. `tone_lock=0.85`, `refinement_strength=0.45`, and `detail_radius=16` are the tested general defaults. This also reduces the brightening seen with broad restoration prompts.

SeedVR2 remains useful when restoration matters more than latency. Direct tests on the same RTX 4090 took about 9.5 seconds for 3B FP8 and 15.9 seconds for 7B FP8 at 1024 square. Qwen Image Edit and FLUX.2 Klein 9B are not defaults because their larger model stacks conflict with the fast general-purpose target. Identity LoRAs are also excluded from the general workflow because they add subject-specific behavior instead of a universal restoration rule.

## Frame profiles

H3 processes multiple frames even when the output is one image.

| Profile | Frames | Notes |
|---|---:|---|
| Single image | 1 | T2I, I2I, or REF2VA. Use the experimental image VAE and hybrid checkpoint. |
| Recommended | 5 | Default. |
| Extended | 9 | More temporal context. |
| High | 13 | Higher memory and runtime. |
| Maximum | 20 | Highest memory and runtime. |

`Exact Frame Decode` returns the selected profile and a `recommended_index`. Connect that index to `Single Image Output`.

`Single Image Output` uses the recommended index by default. Its scoring modes are optional diagnostics; they cannot correct weak conditioning or an unclear edit prompt.

## Sampling

| Profile | Sampler | Scheduler | Steps | Video/audio shift |
|---|---|---|---:|---:|
| Base quality | `res_multistep` | `simple` | 20 | 12/3 |
| Base speed | `res_multistep` | `simple` | 12 | 12/3 |
| FL2VA Turbo v1.0 | `euler` | `simple` | 8 | 12/3 |
| FL2VA Turbo v1.0 768p | `euler` | `simple` | 4 | 6/3 |
| REF2VA Turbo v0.1 | `euler` | `simple` | 4 | 12/3 |
| Hybrid single image | `er_sde` | `sgm_uniform` | 8 | 12/3 |

Turbo profiles require the exact matching adapter below. `Sampling Preset` configures sampling but does not load a LoRA.

Choose `custom | use controls below` in `Sampling Preset` to select any installed sampler and scheduler and set steps, denoise, H3 video/audio shifts, and beta-scheduler parameters directly. The custom controls are ignored while a documented preset is selected, so loading an existing workflow preserves its exact recipe.

| Profile | Required adapter |
|---|---|
| FL2VA Turbo v1.0, 8 steps | `minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors` |
| FL2VA Turbo v1.0 768p, 4 steps | `minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors` |
| REF2VA Turbo v0.1, 4 steps | `minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors` |

Do not mix FL2VA and REF2VA adapters or reuse one adapter's shifts with another. The older LightX v0.1 profile names remain available only to load existing workflows.

The hybrid single-image profile reproduces the linked community workflow. It is experimental and expects the model stack listed below; it is not an official MiniMax recipe.

## Reference editing

REF2VA rebuilds an image from ordered references. Refer to each input by number:

```text
Keep the identity, face, hair, clothing, camera, and environment from <Picture 1>.
Use the body pose and limb positions from <Picture 2>. The final pose must visibly match <Picture 2>.
```

`source_fidelity` changes preservation wording for traits that the instruction does not mention. Explicit assignments such as "pose from `<Picture 2>`" take priority. It is not denoise strength. For large pose, framing, or composition transfers, start around `0.50-0.60`; higher values favor an unchanged `<Picture 1>`.

Each reference socket represents exactly one picture. If an upstream node sends an IMAGE batch, only its first image is used so later sockets keep stable `<Picture N>` numbers. State the role of every connected picture explicitly in the target instructions.

### Experimental one-frame workflows

`H3_T2I_SINGLE`, `H3_I2I_SINGLE`, and `H3_REFERENCE_SINGLE` generate a true `T=1` H3 latent directly. They do not patch ComfyUI or route around Image Studio's conditioning output. Their model stack follows the community workflow:

| Component | File |
|---|---|
| Hybrid diffusion model | `minimax_h3_hybrid_fl2va_ref2va_b25-49-int8.safetensors` |
| Image VAE | `minimax_h3_t1_image_vae_step1597.safetensors` |
| Turbo adapter | `minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors`, strength 0.75 |
| Detail adapter | `MaxiMin-HHH-R2V-ThisIsFine_LoRA_V0_1.safetensors`, strength 0.5 |

The image VAE is intended only for one-frame output. Keep `minimax_h3_video_vae_fp16.safetensors` for multi-frame workflows. The hybrid checkpoint, image VAE, and detail adapter are community experiments and inherit their source-model licenses.

The detail adapter is intentionally set to `0.5`. In direct pose-transfer testing, `1.0` over-preserved Picture 1 and suppressed the requested pose, while `0.5` retained its identity and environment and allowed Picture 2's pose to transfer.

One-frame T2I uses the hybrid checkpoint's FL2VA base without an image reference. One-frame I2I cannot use FL2VA's exact frame-0 keyframe because that keyframe would occupy the only output frame; Image Studio automatically switches that case to Picture 1 reference conditioning. Multi-frame I2I continues to use the original FL2VA keyframe path.

Downloads: [hybrid checkpoint](https://huggingface.co/smhfacct/Minimax-H3-fl2va-ref2va-hybrid-models), [single-image VAE](https://huggingface.co/Mamad8/MiniMax-H3-Image-VAE), [ThisIsFine adapter](https://huggingface.co/Mamad8/MaxiMin-HHH-R2V-ThisIsFine), and [Turbo adapter](https://huggingface.co/Comfy-Org/MiniMax-H3/tree/main/loras).

The approach was prompted by the [single-image community workflow](https://www.reddit.com/r/StableDiffusion/comments/1vqka28/h3_singleimage_no_more_monkey_patching_also_no/). ComfyUI main subsequently added conversion from a regular empty image latent in [commit `0696f61`](https://github.com/Comfy-Org/ComfyUI/commit/0696f61dced6340086cdca64a96200c50f306c66). Image Studio builds the correct nested H3 video/audio latent itself, so its one-frame profile also works on ComfyUI 0.33.1 without that core commit.

## Resolution and memory

Resolution is rounded to a 32-pixel grid. `1 MP` follows ComfyUI's `1024²` convention.

The native H3 canvas is about 1344×768, or one megapixel. Start with `native detail | 0.98 MP`. A 2 MP canvas can help small or distant details in some images, but increases memory and runtime and is not a general quality upgrade.

## Performance

- Prefer the official pruned INT8 ConvRot diffusion model and NVFP4 text encoder listed above. The Comfy model card recommends the INT8 ConvRot model on current CUDA/PyTorch builds and FP8 only as a fallback.
- Use the base 20-step profile as the quality reference. The official Turbo v1.0 eight-step profile is the practical speed/quality default; the 768p four-step profile favors speed.
- SageAttention is optional. ComfyUI's H3 guide reports roughly double sampling speed with minimal quality loss. Enable it globally with ComfyUI's `--use-sage-attention` option or a compatible attention node, not both.
- Match FL2VA source images to the generation canvas for lower preprocessing cost. In REF2VA, `match` is faster; the 2048-short-edge option can strengthen identity at higher cost.
- Change one optimization at a time and compare with the same seed. Stacking unrelated caches, attention patches, and distilled adapters can reduce detail or introduce incompatibilities.

Experimental W4A8 diffusion and INT8 ConvRot VAE variants require ComfyUI 0.31 or newer. They are not workflow defaults because hardware support and output behavior vary.

## Troubleshooting

### `H3WorkflowNote` is reported as missing

This node was added in v15. Update MiniMax H3 Image Studio, restart ComfyUI, and refresh the browser. Current bundled workflows no longer depend on documentation-note nodes.

### A sampling profile or selector strategy is not in the list

Errors mentioning `Turbo v1.0 | 8 steps`, `base quality | RES 20 steps`, or `decode_recommended` mean that a current workflow reached an older backend. Updating files without restarting ComfyUI does not replace the node definitions already loaded in memory.

1. Update MiniMax H3 Image Studio.
2. Stop every running ComfyUI process.
3. Start ComfyUI again.
4. Reload the browser page.
5. Reopen the workflow from `examples/ui/` or `examples/png/`.

Do not repair this by changing only the rejected values. The current decoder also provides the `recommended_index` output used by the workflow.

### `Load Image - image` is missing

Select an input file in each `Load Image` node. This is required for image-to-image and reference editing.

### The canvas is empty

Use a file from `examples/ui/` or `examples/png/`. Files in `examples/api/` are not canvas workflows.

### ComfyUI reports deprecated frontend imports

This extension imports only ComfyUI's supported `scripts/app.js` frontend module. Update the other custom nodes named in the warning or disable them one at a time to identify the source.

### Reporting a generation error

Open a [GitHub issue](https://github.com/astropuzzo/ComfyUI-MiniMax-H3-Image-Studio/issues) and include:

- complete console traceback
- ComfyUI and Image Studio versions
- operating system, GPU, VRAM, and system RAM
- model and LoRA filenames
- workflow JSON or metadata PNG
- resolution, frame profile, sampler, scheduler, and steps

Do not post private prompts, tokens, or personal images.

## Development

```bash
python scripts/validate_release.py
python -m unittest discover -s tests -v
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [CHANGELOG.md](CHANGELOG.md).

## License

[The Unlicense](LICENSE). Models, ComfyUI, and third-party nodes keep their own licenses.
