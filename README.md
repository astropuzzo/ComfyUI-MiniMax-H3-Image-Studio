# ComfyUI MiniMax H3 Image Studio v13

> [!WARNING]
> **Experimental, AI-coded project.** The extension and its documentation were
> produced with AI-assisted coding, guided by repeated hands-on image tests.
> Bugs, incorrect assumptions, and hardware-specific behavior are possible.
> Code review, corrections, bug reports, and pull requests are welcome.

Image-first ComfyUI nodes for using the open MiniMax H3 weights as a practical
text-to-image, image-to-image, and reference-edit generator.

Built around the open [MiniMax H3 model](https://huggingface.co/MiniMaxAI/MiniMax-H3),
ComfyUI's [official H3 implementation](https://docs.comfy.org/tutorials/video/minimax/minimax-h3),
and the [Comfy-Org converted weights](https://huggingface.co/Comfy-Org/MiniMax-H3).

The extension provides image-oriented conditioning, validated 5/20-frame temporal
profiles, resolution controls up to 64 MP, preset and advanced sampling controls,
automatic still-frame scoring, reference editing, and optional full candidate-batch
inspection.

## Installation

**Requires ComfyUI 0.30.0 or newer.**

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/astropuzzo/ComfyUI-MiniMax-H3-Image-Studio.git
```

Restart ComfyUI. No extra Python packages are required beyond the current
ComfyUI installation. Models are not included or downloaded automatically; see
[Models](#models).

## Feedback and contributions

Please use [GitHub Issues](https://github.com/astropuzzo/ComfyUI-MiniMax-H3-Image-Studio/issues)
for bugs and suggestions. For a useful report, include the ComfyUI version,
GPU/VRAM, system RAM, checkpoint filenames, workflow mode, resolution, frame
profile, steps, sampler/scheduler, workflow JSON, console output, and expected
versus actual behavior.

## Important technical limit

H3-Base is a video/audio model, not a natively trained one-frame diffusion
checkpoint. Its temporal VAE maps latent tokens to pixel-frame spans of
`1,4,4,4,4` in a repeating pattern. A requested 20-frame context therefore uses
the smallest covering latent packet, which naturally decodes **22 frames**.

H3 denoises the complete temporal packet jointly, not frame by frame. A 20-frame
run cannot stop after one frame and preserve the same result. Image Studio keeps
the requested 5- or 20-frame profile after VAE decode, then selects the preferred
still downstream.

## Image nodes

- `MiniMax H3 Image • Text to Image` — use the **FL2VA** diffusion model.
- `MiniMax H3 Image • Image to Image` — use **FL2VA** with a source-frame anchor.
- `MiniMax H3 Image • Reference Edit` — use **REF2VA**, with up to nine ordered
  reference images.
- `MiniMax H3 Image • Resolution Preset` — common aspect/size profiles.
- `MiniMax H3 Image • Sampling Preset` — 20-step quality or 12-step speed.
- `MiniMax H3 Image • Exact Frame Decode` — decodes the temporal packet, removes
  only natural VAE packet surplus, and preserves the complete requested profile.
- `MiniMax H3 Image • Single Image Output` — scores the decoded profile and
  normally emits one still; it can also expose the complete candidate batch.
- `MiniMax H3 Image • Advanced Resolution` — manual canvas/grid controls.
- `MiniMax H3 Image • Advanced Sampling` — manual sampler, scheduler, denoise,
  H3 shifts, and custom beta controls.
- `MiniMax H3 Image • Advanced Combined Prepare` — combined T2I/I2I/REF2VA
  preparation for experimentation.

All 10 nodes include `DESCRIPTION` metadata and in-UI tooltips for non-obvious
controls.

## Frames and final selection

All three image workflows expose two validated choices:

- `recommended | 5 frames` — best observed speed/quality balance and default.
- `maximum quality | 20 frames (slow)` — more temporal context and much higher
  compute/memory cost.

Exact Frame Decode preserves the requested profile rather than destroying the
other generated frames before selection. For a 20-frame request, the natural
22-frame VAE packet is cropped to 20 **per batch item**. In 20-frame FL2VA I2I,
the first stable edit index is also measured independently per batch item.

`Single Image Output` behavior:

- `emit_candidate_batch = false` — `selected_image` contains only the selected
  still and `candidate_batch_debug` is empty.
- `emit_candidate_batch = true` — `selected_image` contains the **complete decoded
  profile**, so an already-connected Preview/Save node can show or save every
  generated candidate.
- `top_k` limits only `candidate_batch_debug`, which contains the highest-scoring
  candidates. It does **not** limit the complete batch on `selected_image`.

Source-dependent strategies (`balanced_edit`, `most_similar_to_source`) report
an explicit fallback when `source_image` is missing instead of silently claiming
the requested strategy ran.

### Recommended combinations

| Goal | Frames | Steps | Result |
|---|---:|---:|---|
| Recommended speed/quality | 5 | 20 | Default frame profile with quality sampling |
| Faster generation | 5 | 12 | Lower latency with possible quality reduction |
| Maximum quality | 20 | 20 | More temporal context; much slower |

Frames above 20 and steps above 20 did not improve a still consistently enough
to justify their cost in the tested image workflows, so they are not exposed by
the simple presets.

## Resolution

The preset selector uses ComfyUI's megapixel convention (`1 MP = 1024² pixels`)
and rounds to H3's 32-pixel grid. It includes 2, 4, and 8 MP presets plus a
custom 0.1–64 MP range. For 16:9, `native detail | 0.98 MP` resolves to
`1344 × 768`.

`Resolution Preset` exposes `limit_to_native_area`; `Advanced Resolution` uses
`native_area_cap`. Selecting `source image` without a connected image now raises
an actionable error instead of silently producing a square canvas.

The Advanced Resolution node no longer exposes the stale `custom_megapixels` and
`limit_to_native_area` widgets that previously caused frontend execution errors.
Its Python signature still accepts those old kwargs as ignored compatibility
inputs so legacy workflows with link-converted widgets can continue to execute.

Extreme aspect-ratio fitting now searches the feasible capped grid rather than
collapsing to `32×32` when the nearby search has no valid pair.

Direct 2K/4K/8 MP generation is allowed, but H3-Base does not reproduce MiniMax's
unreleased H3-Regenerate-2K stage. High resolutions can consume dramatically more
VRAM/RAM without proportional learned detail.

## Sampling

The validated quality baseline uses:

- sampler: `res_multistep`
- scheduler: `simple`
- steps: `20`
- video/audio sigma shifts: `12 / 3`
- CFG: none (`BasicGuider`; H3 checkpoints are CFG-distilled)

Choose `speed | 12 steps` for a faster preset. For manual controls use
`MiniMax H3 Image • Advanced Sampling`.

Advanced Sampling applies the same ComfyUI-style denoise semantics to all
schedulers, including `beta_custom`: values below 1 build a longer schedule and
keep the final `steps + 1` sigmas, while `denoise = 0` returns empty sigmas. The
H3 sampling patch also preserves the model config's existing `multiplier` when
changing sigma shift.

## Optimize for still / Source Fidelity

`optimize_for_still` changes only the text sent to the H3 encoder. It adds a
locked-camera still-image wrapper and, for edit modes, preservation language.
It does **not** change resolution, frame count, sampler, scheduler, sigmas, or
model weights.

`source_fidelity` / Source Fidelity likewise controls preservation language for
identity, pose, perspective, composition, and geometry. **It is not a denoise
slider.**

## Advanced Combined Prepare

The VAE input is optional in `Advanced Combined Prepare` because T2I does not
encode a source image. I2I and REF2VA require the VAE and raise a clear per-mode
error if it is missing.

Reference images connected outside REF2VA mode are ignored and reported in
`run_info`; a `source_image` connected in T2I is also reported as ignored.

## Prompting for images

Describe the exact final frame: subject, pose, composition, lens/focus, lighting,
background, materials, and style. Avoid timelines, cuts, camera motion, audio,
or second-by-second video instructions in image mode. The still wrapper reports
a warning when it detects conflicting video-language patterns.

## Performance notes

The measurements below are historical end-to-end wall-clock times from the local
test setup. They include conditioning, sampling, VAE decode, selection, and
saving. The original measurements predate the final user-facing `20 frames`
label: a 20-frame request uses the same natural 22-frame VAE packet, so the core
compute comparison remains applicable.

Test setup used for these historical runs:

- Windows 11 Pro with Stability Matrix
- Intel Core i9-13900KF
- NVIDIA GeForce RTX 4090, 24 GB VRAM
- 64 GB system RAM
- ComfyUI 0.30.0
- Python 3.12.10
- PyTorch 2.13.0 + CUDA 13.0
- `cudaMallocAsync` and SageAttention
- INT8 FL2VA/REF2VA, NVFP4/AWQ Qwen encoder, FP16 video VAE

### Text to Image — resolution/frame sweep, 20 steps

| Resolution | Canvas | Requested | Natural | Time |
|---:|---:|---:|---:|---:|
| 1.99 MP | 1184×1760 | 5 | 5 | 27.8 s |
| 1.99 MP | 1184×1760 | 20 | 22 | 50.1 s |
| 3.96 MP | 1664×2496 | 5 | 5 | 38.9 s |
| 3.96 MP | 1664×2496 | 20 | 22 | 138.6 s |
| 8.02 MP | 2368×3552 | 5 | 5 | 84.0 s |
| 8.02 MP | 2368×3552 | 20 | 22 | 401.8 s |

At 8 MP, moving from 5 to 20 requested frames increased the measured run from
84 seconds to about 6 minutes 42 seconds without a proportional fidelity gain.

### Historical manual beta tests

Rows previously labelled `reference detail / beta` were **manual historical
FL2VA I2I beta-scheduler tests**, not a current Sampling Preset and not REF2VA
Reference Edit. They are retained only as historical measurements.

| Resolution | Frames / steps | Natural | Historical scheduler | Median/time |
|---:|---|---:|---|---:|
| 0.99 MP | 5 / 20 | 5 | manual beta | 30.1 s |
| 0.99 MP | 20 / 20 | 22 | manual beta | 32.3 s |
| 1.99 MP | 20 / 20 | 22 | manual beta | 80.7 s |

Results remain model-, prompt-, source-, and memory-state-dependent.

## Memory behavior

High-resolution multi-frame decode can consume tens of gigabytes of combined
VRAM, RAM, and Windows commit/pagefile space. Metric scoring downsamples the
candidate packet in small fp32 chunks instead of first upcasting the entire
full-resolution packet, avoiding a very large transient allocation on high-MP
20-frame decodes.

When `emit_candidate_batch` is disabled, Single Image Output clones only the
selected still for its primary output and emits an independent empty debug
tensor. When enabled, retaining the complete decoded batch is intentional and
therefore uses more memory.

## Example workflows

The `examples/` directory contains API-format JSON graphs; the input names are
unchanged, so existing example workflows remain compatible. See
`examples/WORKFLOW_BUILD_GUIDE.txt` for manual canvas wiring.

### Text to Image — `H3_T2I_API.json`

Uses FL2VA without a source image. Resolution Preset creates the canvas, Text to
Image builds still-oriented conditioning and the H3 temporal latent, and
SamplerCustomAdvanced samples with BasicGuider.

### Image to Image — `H3_I2I_API.json`

Uses FL2VA with the loaded image encoded as the frame-0 anchor. Connect
`fitted_source` to `Single Image Output.source_image` when using source-aware
selection such as `balanced_edit`.

### Reference Edit — `H3_REFERENCE_EDIT_API.json`

Uses REF2VA. `source_image` is `<Picture 1>` and optional
`reference_image_2`…`reference_image_9` are encoded independently, so different
input dimensions/aspect ratios are supported. IMAGE batches are expanded into
ordered references up to the same nine-image model limit.

References keep their order and can be named in prompts as `<Picture 1>`,
`<Picture 2>`, etc.

### Image edit examples

Environment replacement prompt:
`Replace the moss and trees with ashes and burning lava flowing everywhere.`

| Before | After |
|---|---|
| <img src="assets/image-edit-examples/robot-moss-before.png" alt="Moss-covered robot before the edit"> | <img src="assets/image-edit-examples/robot-lava-after.png" alt="Burning robot surrounded by ashes and lava after the edit"> |

Subject replacement prompt: `Replace the woman with a clown.`

| Before | After |
|---|---|
| <img src="assets/image-edit-examples/woman-car-before.png" alt="Woman standing in front of a car before the edit"> | <img src="assets/image-edit-examples/clown-car-after.png" alt="Clown standing in front of the same car after the edit"> |

## Graph

1. Load the correct H3 diffusion model with `UNETLoader`.
2. Load the H3 Qwen encoder with `CLIPLoader` (`type=minimax`).
3. Load `minimax_h3_video_vae_fp16.safetensors` with `VAELoader`.
4. Connect Resolution Preset to the chosen T2I/I2I/Edit node.
5. Connect the diffusion model to Sampling Preset.
6. Connect Sampling Preset's model to `BasicGuider`.
7. Connect `RandomNoise`, `BasicGuider`, sampler/sigmas, and H3 latent to
   `SamplerCustomAdvanced`.
8. Decode with `MiniMax H3 Image • Exact Frame Decode` and the H3 video VAE.
9. Send decoded frames to `Single Image Output`, then `SaveImage`.

## Models

This extension intentionally does not contain a model downloader. Follow the
[official ComfyUI MiniMax H3 guide](https://docs.comfy.org/tutorials/video/minimax/minimax-h3)
and download the required files from
[Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3).

Place files in the standard ComfyUI model folders:

- diffusion checkpoints → `ComfyUI/models/diffusion_models/`
- Qwen text encoder → `ComfyUI/models/text_encoders/`
- video VAE → `ComfyUI/models/vae/`

Recommended 24 GB VRAM filenames used by the examples:

- FL2VA: `minimax_h3_fl2va_pruned_int8_convrot.safetensors`
- REF2VA: `minimax_h3_ref2va_pruned_int8_convrot.safetensors`
- text encoder: `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`
- video VAE: `minimax_h3_video_vae_fp16.safetensors`

The audio VAE is not used for image output. Restart ComfyUI after updating the
extension so the new node schemas replace cached definitions.

## Registry metadata

`pyproject.toml` is included for ComfyUI Registry publishing and declares
`requires-comfyui = ">=0.30.0"`. Replace `PublisherId = "FILL_ME_IN"` with your
actual Comfy Registry publisher ID before running `comfy node publish`.

## License

Released under [The Unlicense](LICENSE). The MiniMax models, ComfyUI, and other
third-party components retain their own licenses.
