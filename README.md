# ComfyUI MiniMax H3 Image Studio v13

> [!WARNING]
> **Experimental, AI-coded project.** The entire extension and its documentation
> were produced with AI-assisted coding, guided by repeated hands-on image tests.
> The maintainer has no formal programming or model-engineering knowledge. Bugs,
> incorrect assumptions, and hardware-specific behavior are possible. Code
> review, corrections, suggestions, bug reports, and pull requests are very
> welcome.

Image-first ComfyUI nodes for using the open MiniMax H3 weights as a practical
text-to-image, image-to-image, and reference-edit generator.

Built around the open [MiniMax H3 model](https://huggingface.co/MiniMaxAI/MiniMax-H3),
ComfyUI's [official H3 implementation](https://docs.comfy.org/tutorials/video/minimax/minimax-h3),
and the [Comfy-Org converted weights](https://huggingface.co/Comfy-Org/MiniMax-H3).

The extension provides image-oriented conditioning, arbitrary frame counts,
resolution controls up to 64 MP, official and manual sampling profiles,
automatic still-frame scoring, reference editing, and single-image output that
does not pin the decoded frame packet in memory.

## Installation

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/astropuzzo/ComfyUI-MiniMax-H3-Image-Studio.git
```

Restart ComfyUI. No extra Python packages are required beyond the current
ComfyUI installation. Models are not included or downloaded automatically; see
the [Models](#models) section.

## Feedback and contributions

Please use [GitHub Issues](https://github.com/astropuzzo/ComfyUI-MiniMax-H3-Image-Studio/issues)
for bugs and suggestions. Because the project is experimental and AI-coded,
independent technical review is especially valuable. Pull requests are welcome.

For a useful bug report, include the ComfyUI version, GPU/VRAM, system RAM,
checkpoint filenames, workflow mode, resolution, frames, steps, sampler profile,
workflow JSON, relevant console output, and a description of expected versus
actual behavior. Images demonstrating artifacts are helpful when they can be
shared safely.

## Important technical limit

H3-Base is a video/audio model, not a natively trained one-frame diffusion
checkpoint. Its temporal VAE naturally maps latent tokens to pixel-frame spans
of `1,4,4,4,4` in a repeating pattern; the standard complete packets therefore
land on `17k+5`. Manual mode may now request any count from 1 to 4096. It uses
the smallest temporal latent that covers the request, and Exact Frame Decode
crops the small partial-packet surplus to the precise requested count.

One-frame generation is available but remains experimental and can reproduce
the soft, low-definition output seen in earlier tests. Five frames is the
maximum-speed preset; 20 frames is the currently recommended T2I balance.

## Image nodes

- `MiniMax H3 Image • Text to Image` — use the **FL2VA** diffusion model.
- `MiniMax H3 Image • Image to Image` — use **FL2VA** and a source-frame anchor.
- `MiniMax H3 Image • Reference Edit` — use the separate **REF2VA** model, with
  up to nine ordered reference images.
- `MiniMax H3 Image • Resolution Preset` — safe H3-native aspect/size selection.
- `MiniMax H3 Image • Sampling Preset` — official baseline or a reference preset.
- `MiniMax H3 Image • Exact Frame Decode` — replaces core `VAEDecode` and crops
  partial temporal packets to the exact manual frame count.
- `MiniMax H3 Image • Single Image Output` — selects a sharp, stable still. Its
  candidate batch is suppressed by default so image-feed extensions receive
  only the selected image. The selected frame owns independent storage, so the
  output cache does not retain the complete decoded packet; enable
  `emit_candidate_batch` only for debugging.
Advanced resolution, sampling, and the legacy combined prepare nodes remain
available for experimentation.

## Quality profiles

| Profile | Frames | Use |
|---|---:|---|
| maximum speed | 5 | Fastest result; greater risk of banding, grid artifacts, or a weak frame |
| recommended | 20 | Best observed T2I balance; default |
| image balanced | 56 | Slower experiment with more temporal drift risk |
| video-trained | 124 | Inside the trained duration; very slow for one still |
| video-trained+ | 192 | Longer video-range experiment |
| manual frames | 1–4096 | Exact requested output count; partial packet cropped after decode |

### Tested T2I combinations

These settings produced the most useful trade-offs during repeated local tests:

| Goal | Frames | Steps | Result |
|---|---:|---:|---|
| Maximum speed | 5 | 12 | Fastest, but banding and other temporal/VAE artifacts are more likely |
| Recommended speed | 20 | 12 | Recommended balance of time, stability, and candidate quality |
| Maximum quality | 20 | 20 | Best observed quality; slower than the recommended-speed setup |

Increasing frames beyond 20 or steps beyond 20 did not consistently improve a
single still enough to justify the additional time and memory in these tests.
The result remains model- and prompt-dependent; no profile can remove H3's
underlying softness, blockiness, banding, or grid artifacts in every image.

Choose `manual frames | exact value below` to use the adjacent `manual_frames`
control. Every integer from 1 to 4096 is accepted without alignment. Some
requests need a slightly longer natural VAE packet—for example 6 uses 9→6 and
10 uses 13→10—but the minimum covering latent is used, not the next `17k+5`
packet. Counts above 362 are outside the documented training range and can be
extremely expensive.

## Resolution

The simple selector uses ComfyUI's megapixel convention (`1 MP = 1024² pixels`)
and rounds to H3's 32-pixel grid. It includes 2, 4, and 8 MP presets plus a
custom range from 0.1 to 64 MP. Oversize generation is unlocked by default;
enable `limit_to_native_area` when you want the conservative `768 × 1344` area
cap. For 16:9, `native detail | 0.98 MP` resolves to `1344 × 768`.

Direct 2K/4K generation is allowed, but H3-Base does not reproduce MiniMax's
official H3-Regenerate-2K stage, whose weights are not currently open-sourced.
High resolutions can consume dramatically more VRAM and may add pixels without
adding equivalent learned detail. Use the unlocked sizes experimentally or use
a dedicated image upscaler after native generation.

## Sampling

The maximum-quality baseline uses:

- sampler: `res_multistep`
- scheduler: `simple`
- steps: `20`
- video/audio sigma shifts: `12 / 3`
- CFG: none (`BasicGuider`; H3 checkpoints are CFG-distilled)

This is the official ComfyUI-style baseline and is the default sampling preset.
For the recommended faster T2I setup, select `fast preview | 12 steps` together
with `recommended | 20 frames`. Use 5 frames and 12 steps only when minimum
generation time matters more than the increased artifact risk.
For REF2VA-heavy edits, the `reference detail` preset uses
`res_multistep + beta + 20`, following ComfyUI's reference-workflow guidance.
Choose `manual steps | official sampler` to activate `manual_steps`; this keeps
the official `res_multistep + simple` path and changes only the number of steps.
For fully manual sampler, scheduler, shifts, denoise, and steps, use the advanced
`MiniMax H3 Sampling Settings` node.

## Optimize for still

`optimize_for_still` changes only the text sent to the H3 encoder. It adds a
still-image wrapper asking for a locked camera, fixed composition, no cuts,
motion, temporal progression, or audio, and crisp image detail. In I2I and
Reference Edit it also adds source-preservation language based on
`source_fidelity`. It does **not** change resolution, frames, steps, sampler,
sigmas, or model weights. Disable it when you want the prompt passed through
unchanged or when the preservation language is making an edit too conservative.

## Prompting for images

Describe the exact final frame: subject, pose, composition, lens/focus, lighting,
background, materials, and style. Do not paste a timeline, camera move, cuts,
soundscape, music, or second-by-second video prompt into image mode. The node
adds a still-image wrapper and reports a warning when it detects conflicting
video language, but it cannot infer which moment of a long video prompt you
intended as the final still.

## Test setup

The recommendations above were obtained on:

- Windows 11 Pro with Stability Matrix
- Intel Core i9-13900KF
- NVIDIA GeForce RTX 4090, 24 GB VRAM
- 64 GB system RAM
- ComfyUI 0.30.0
- Python 3.12.10
- PyTorch 2.13.0 + CUDA 13.0
- `cudaMallocAsync` and SageAttention
- INT8 FL2VA/REF2VA model, NVFP4/AWQ Qwen encoder, and FP16 video VAE

Generation time and memory use will differ on other systems. High-resolution
multi-frame decode can use tens of gigabytes of combined VRAM, RAM, and Windows
commit/pagefile space.

## Measured local performance

These are end-to-end wall-clock times reported by ComfyUI as `Prompt executed
in`, not estimates. They include conditioning, sampling, VAE decode, still
selection, and saving. The median is more representative than a single run
because model/cache state, source image, aspect ratio, and accumulated memory
pressure can change the result.

The measurements were made before the final preset label changed from 22 to 20
requested frames. This does not invalidate the comparison: a 20-frame request
uses the same minimum natural 22-frame H3/VAE packet and crops it to exactly 20
after decoding, so its main compute cost is effectively the same. The table
therefore shows the current requested count and the natural packet in
parentheses.

### Text to Image — FL2VA

#### Resolution and frame sweep — 20 steps

This earlier controlled sweep is the missing 1 / 2 / 4 / 8 MP comparison. The
2, 4, and 8 MP pairs used the same prompt and seed; the 0.99 MP results are
representative runs from the same test session.

| Resolution | Canvas | Requested frames | Natural frames | Runs | Median/time | Observed range |
|---:|---:|---:|---:|---:|---:|---:|
| 0.99 MP | 832 x 1248 | 20 | 22 | 2 | 28.7 s | 24.2–33.2 s |
| 1.99 MP | 1184 x 1760 | 5 | 5 | 1 | 27.8 s | single run |
| 1.99 MP | 1184 x 1760 | 20 | 22 | 1 | 50.1 s | single run |
| 3.96 MP | 1664 x 2496 | 5 | 5 | 1 | 38.9 s | single run |
| 3.96 MP | 1664 x 2496 | 20 | 22 | 1 | 138.6 s | single run |
| 8.02 MP | 2368 x 3552 | 5 | 5 | 1 | 84.0 s | single run |
| 8.02 MP | 2368 x 3552 | 20 | 22 | 1 | 401.8 s | single run |

The 8 MP result demonstrates the cost ceiling especially clearly: increasing
from 5 to 20 requested frames raised the measured time from 84 seconds to about
6 minutes 42 seconds without a proportional fidelity improvement.

#### Profile comparison — 1664 x 2496 (3.96 MP)

| Current setting | Natural frames | Runs | Median | Observed range |
|---|---:|---:|---:|---:|
| 5 frames / 12 steps — maximum speed | 5 | 6 | 36.2 s | 28.2–43.7 s |
| 20 frames / 12 steps — recommended | 22 | 7 | 90.0 s | 83.9–133.1 s |
| 20 frames / 20 steps — maximum observed quality | 22 | 11 | 141.4 s | 136.1–164.0 s |

Two additional manual T2I checks at the same resolution took 41.8 seconds for
5 frames / 20 steps and 79.4 seconds for 10 requested frames / 20 steps. Each
was a single run, so treat those values as indicative only.

### Image-to-Image edit — FL2VA

#### Recommended-speed edit tests — 12 steps

| Resolution | Current setting | Natural frames | Runs | Median | Observed range |
|---|---|---:|---:|---:|---:|
| about 2.0 MP | 20 frames / 12 steps | 22 | 16 | 54.0 s | 32.2–93.1 s |
| 1.99 MP | 20 frames / 20 steps | 22 | 1 | 104.0 s | single run |
| 3.01 MP | 20 frames / 12 steps | 22 | 1 | 87.6 s | single run |
| 4.01 MP | 20 frames / 12 steps | 22 | 1 | 120.9 s | single run |

The broad 2 MP range combines different portrait aspect ratios, source images,
edit instructions, and cache/memory states. For a cleaner repeated comparison,
the two latest 1184 x 1760 tests with the same 20-frame / 12-step setup took
59.05 and 61.79 seconds, averaging 60.42 seconds.

#### Earlier 20-step edit sweep

These additional results include the missing native-resolution tests. The
`reference detail` rows use the beta scheduler while remaining FL2VA
Image-to-Image runs; they are not REF2VA Reference Edit.

| Resolution | Current setting | Natural frames | Sampling profile | Runs | Median/time | Observed range |
|---:|---|---:|---|---:|---:|---:|
| 0.99 MP | 5 frames / 20 steps | 5 | reference detail / beta | 3 | 30.1 s | 21.8–31.9 s |
| 0.99 MP | 20 frames / 20 steps | 22 | reference detail / beta | 3 | 32.3 s | 32.2–42.8 s |
| 0.99 MP | 5 frames / 20 steps | 5 | official quality / simple | 1 | 25.4 s | single run |
| 1.99 MP | 5 frames / 20 steps | 5 | official quality / simple | 1 | 43.7 s | single run |
| 1.99 MP | 20 frames / 20 steps | 22 | reference detail / beta | 2 | 80.7 s | 75.2–86.2 s |
| 3.96 MP | 5 frames / 20 steps | 5 | official quality / simple | 1 | 75.1 s | single run |

These edit timings cover the FL2VA Image-to-Image workflow. REF2VA Reference
Edit was tested and works, but its matching timestamped console log was no
longer retained when this benchmark table was assembled, so no speculative
REF2VA speed figure is published here.

## Known limitations

- H3 is fundamentally a video/audio model. This project adapts it for stills;
  it does not turn the checkpoint into a native image diffusion model.
- One-frame mode is available but often has lower definition and stronger
  artifacts than a short multi-frame packet.
- Direct 2–8 MP generation increases canvas size and memory far more reliably
  than it increases learned fine detail. The unreleased Regenerate-2K stage is
  not reproduced here.
- Softness, blockiness, color banding, and grid-like artifacts can remain even
  when sampling succeeds.
- Arbitrary frame counts use the minimum temporal latent that covers the
  request, then crop with Exact Frame Decode. The core ComfyUI `VAEDecode` does
  not perform this final exact-count crop.
- The selected still and disabled debug output no longer retain the complete
  decoded packet. ComfyUI and PyTorch may still reserve reusable memory, and
  generating many high-resolution frames remains inherently expensive.

## Example workflows

The `examples/` directory contains API-format JSON graphs. They are intended for
ComfyUI's API/prompt format; `WORKFLOW_BUILD_GUIDE.txt` gives the equivalent
manual canvas wiring. Change the model filenames if your installed variants use
different names.

### Text to Image — `H3_T2I_API.json`

Uses the FL2VA checkpoint without a source image. Resolution Preset creates the
canvas, Text to Image builds the H3 audio/video latent and still-oriented text
conditioning, and SamplerCustomAdvanced performs sampling with BasicGuider. The
published example uses the recommended-speed combination of 20 frames and 12
steps. Switch Sampling Preset to `official quality | 20 steps` for the observed
maximum-quality setup, or use 5 frames with 12 steps for maximum speed.

### Image to Image — `H3_I2I_API.json`

Uses FL2VA with the loaded picture encoded as the first-frame anchor. Source
Fidelity controls how strongly the prompt wrapper asks H3 to preserve identity,
pose, perspective, composition, and geometry; it is not a diffusion denoise
slider. The fitted source is also passed to Single Image Output so
`balanced_edit` can combine visual similarity with sharpness and temporal
stability when selecting the final still.

### Image Edit examples

These are real FL2VA Image-to-Image results produced with the custom nodes. The
examples show that broad semantic replacement can work while much of the source
composition, camera position, and subject placement remains recognizable.

#### Environment replacement

Prompt: `Replace the moss and trees with ashes and burning lava flowing everywhere.`

| Before | After |
|---|---|
| <img src="assets/image-edit-examples/robot-moss-before.png" alt="Moss-covered robot before the edit"> | <img src="assets/image-edit-examples/robot-lava-after.png" alt="Burning robot surrounded by ashes and lava after the edit"> |

Settings: 1472 x 2144, 3.01 MP, 20 requested / 22 natural frames, 12 steps,
87.57 seconds.

#### Subject replacement

Prompt: `Replace the woman with a clown.`

| Before | After |
|---|---|
| <img src="assets/image-edit-examples/woman-car-before.png" alt="Woman standing in front of a car before the edit"> | <img src="assets/image-edit-examples/clown-car-after.png" alt="Clown standing in front of the same car after the edit"> |

Settings: 1184 x 1760, 1.99 MP, 20 requested / 22 natural frames, 12 steps,
58.41 seconds. The source was resized from its original 1664 x 2496 canvas.

### Reference Edit — `H3_REFERENCE_EDIT_API.json`

Uses the separate REF2VA checkpoint. The source is encoded as a visual reference
rather than an exact first-frame anchor, making this workflow more suitable for
changing clothing, materials, style, environment, or other semantic details.
The node accepts `source_image` plus optional `reference_image_2` through
`reference_image_9`; each image is resized and VAE-encoded independently, so
different aspect ratios and dimensions are supported. An IMAGE batch connected
to any reference socket is also expanded into individual references, up to the
same nine-image model limit.

References keep their input order and can be named directly in the edit prompt
as `<Picture 1>`, `<Picture 2>`, and so on. For example: “keep the person and
composition from `<Picture 1>`, but use the jacket from `<Picture 2>` and the
lighting style from `<Picture 3>`.” The bundled API workflow demonstrates two
Load Image nodes. Existing one-reference workflows remain valid without changes.
The example uses the `reference detail | beta, 20 steps` sampling profile and
`balanced_edit` selection. Increase Source Fidelity for stricter identity and
composition preservation; reduce it when the requested change is being resisted.

### Exact decode and final selection

All three examples send the sampled latent to Exact Frame Decode, not core
`VAEDecode`. This preserves arbitrary manual frame counts and crops partial H3
temporal packets precisely. Single Image Output then scores only the decoded
candidate range and emits one independent image. Keep `emit_candidate_batch`
disabled for ordinary use so image-feed extensions and output caches receive
only the selected still.

## Graph

1. Load the correct H3 diffusion model with `UNETLoader`.
2. Load the H3 Qwen encoder with `CLIPLoader` (`type=minimax`).
3. Load `minimax_h3_video_vae_fp16.safetensors` with `VAELoader`.
4. Connect Resolution Preset to the chosen T2I/I2I/Edit node.
5. Connect the diffusion model to Sampling Preset.
6. Connect Sampling Preset's model to `BasicGuider`.
7. Connect `RandomNoise`, `BasicGuider`, the preset sampler/sigmas, and the H3
   latent to `SamplerCustomAdvanced`.
8. Decode with `MiniMax H3 Image • Exact Frame Decode` and the H3 video VAE.
9. Send decoded frames to Single Image Output, then `SaveImage`.

API-format examples are in `examples/`.

## Models

This extension intentionally does not contain a model downloader. Follow the
[official ComfyUI MiniMax H3 guide](https://docs.comfy.org/tutorials/video/minimax/minimax-h3)
and download the required files from
[Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3).

Place the selected files in the standard ComfyUI model folders:

- diffusion checkpoints → `ComfyUI/models/diffusion_models/`
- Qwen text encoder → `ComfyUI/models/text_encoders/`
- video VAE → `ComfyUI/models/vae/`

Recommended 24 GB VRAM filenames used by the included examples:

- FL2VA: `minimax_h3_fl2va_pruned_int8_convrot.safetensors`
- REF2VA: `minimax_h3_ref2va_pruned_int8_convrot.safetensors`
- text encoder: `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`
- video VAE: `minimax_h3_video_vae_fp16.safetensors`

The audio VAE is not used for image output. Restart ComfyUI after updating the
extension so the new node schemas replace the cached definitions.

## License

Released under [The Unlicense](LICENSE): public-domain dedication with permission
to copy, modify, publish, use, compile, sell, or distribute the project for any
commercial or non-commercial purpose. The MiniMax models, ComfyUI, and other
third-party components retain their own licenses and are not relicensed here.
