![MiniMax H3 Image Studio](assets/branding/minimax-h3-banner.svg)

# MiniMax H3 Image Studio

ComfyUI nodes and workflows for MiniMax H3 text-to-image, image-to-image, and REF2VA reference editing.

MiniMax H3 is an audio-video model. These nodes generate a short frame packet, decode it, and select one still image.

## Requirements

- ComfyUI 0.30.0 or newer
- MiniMax H3 diffusion model
- MiniMax H3 Qwen text encoder
- MiniMax H3 video VAE
- LightX v0.1 LoRA only for the LightX workflow

Use the [official ComfyUI MiniMax H3 guide](https://docs.comfy.org/tutorials/video/minimax/minimax-h3) for model downloads and installation.

| Model | Folder |
|---|---|
| FL2VA or REF2VA diffusion model | `ComfyUI/models/diffusion_models/` |
| Qwen text encoder | `ComfyUI/models/text_encoders/` |
| H3 video VAE | `ComfyUI/models/vae/` |
| LightX v0.1 LoRA | `ComfyUI/models/loras/` |

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
| Image to Image | [Open](examples/ui/H3_I2I.json) | [Open](examples/png/H3_I2I.png) | [API](examples/api/H3_I2I_API.json) |
| Reference Edit | [Open](examples/ui/H3_REFERENCE_EDIT.json) | [Open](examples/png/H3_REFERENCE_EDIT.png) | [API](examples/api/H3_REFERENCE_EDIT_API.json) |
| Image to Image, LightX v0.1 | [Open](examples/ui/H3_I2I_LIGHTX_TURBO.json) | [Open](examples/png/H3_I2I_LIGHTX_TURBO.png) | [API](examples/api/H3_I2I_LIGHTX_TURBO_API.json) |

Files in `examples/api/` are prompt JSON for API clients. They do not contain a canvas layout.

For image-to-image and reference-edit workflows, select an image in every `Load Image` node before running the workflow.

## Nodes

| Node | Function |
|---|---|
| `Text to Image` | Prepares FL2VA text conditioning and the H3 latent. |
| `Image to Image` | Prepares FL2VA editing with the source image at frame 0. |
| `Reference Edit` | Prepares REF2VA editing with up to nine ordered references. |
| `Resolution Preset` | Calculates common H3 canvas sizes. |
| `Sampling Preset` | Configures base or LightX v0.1 sampling. |
| `Exact Frame Decode` | Decodes the requested frame profile. |
| `Single Image Output` | Selects one frame or returns the decoded batch. |
| `Advanced Resolution` | Calculates custom canvas sizes. |
| `Advanced Sampling` | Exposes sampler, scheduler, denoise, and sigma shifts. |
| `Advanced Combined Prepare` | Combines all preparation modes in one node. |

## Frame profiles

H3 processes multiple frames even when the output is one image.

| Profile | Frames | Notes |
|---|---:|---|
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
| LightX v0.1 ER-SDE | `er_sde` | `simple` | 4 | 12/3 |
| LightX v0.1 SA-Solver | `sa_solver` | `simple` | 4 | 12/3 |

The LightX profiles require the matching Comfy-format v0.1 LoRA. `Sampling Preset` does not load the LoRA.

[Larryvrh/ComfyUI-MiniMax-H3-Turbo](https://github.com/Larryvrh/ComfyUI-MiniMax-H3-Turbo) uses a different loader, sampler, and adapter. Do not use its settings with a LightX workflow.

## Reference editing

REF2VA rebuilds an image from ordered references. Refer to each input by number:

```text
Keep the subject, pose, framing, and background from <Picture 1>.
Replace only [feature] with the corresponding feature from <Picture 2>.
```

`source_fidelity` changes the preservation text added to the prompt. It is not denoise strength.

## Resolution and memory

Resolution is rounded to a 32-pixel grid. `1 MP` follows ComfyUI's `1024²` convention.

Larger images and longer frame profiles increase VRAM, RAM, and runtime. H3-Base was trained near 768×1344; a larger canvas does not guarantee more detail.

## Troubleshooting

### `H3WorkflowNote` is reported as missing

This node was added in v15. Update MiniMax H3 Image Studio, restart ComfyUI, and refresh the browser. Current bundled workflows no longer depend on documentation-note nodes.

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
