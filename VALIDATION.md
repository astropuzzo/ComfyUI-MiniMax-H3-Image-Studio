# Validation

The current report is [v24: simplified single-frame generation and editing](docs/validation-v24.md), including measured timings and known artifacts.

The [v23 decoder and model comparisons](docs/validation-v23.md) describe the older workflows.

The v22 report below is historical and does not describe the complete v23 test coverage.

## v22 validation — 12 September 2026

Test machine: RTX 4090 24 GB, Windows, PyTorch 2.13.0+cu130, ComfyUI 0.34.0 (`12d52794`), default PyTorch attention. Only Image Studio was enabled as a custom extension. No refiner, upscaler, or cache-acceleration node was used.

The new workflows use five frames, Euler/simple, and the matching LightX2V eight-step 768p adapter. Generation uses FL2VA with video/audio shift 6/3; editing uses REF2VA with 12/3. Models are the official pruned INT8 ConvRot checkpoints, NVFP4 Qwen encoder, and FP16 video VAE.

## Local image tests

Generated a red mug on a wooden table as a non-personal test source. Tested two seeds per reference mode:

| Task | Canvas | Native references, seeds 42 / 43 | Semantic references, seeds 42 / 43 |
|---|---|---|---|
| Turn mug blue; add a yellow lemon on its right | 640×640 | 16.14 / 6.11 s | 10.08 / 4.08 s |
| Remove handle; add a green apple on its left | 864×864 | 10.06 / 6.05 s | 12.17 / 8.06 s |

These are local API wall times, including polling at two-second intervals. The first run in each pair encodes the changed prompt; the second changes only the seed and reuses cached conditioning. Initial text-to-image generation took 22.16 seconds including model loading. Times are individual observations, not averages or a claim of speedup over v21.

Visual inspection found the requested color/object changes in all four first-task outputs and the removed handle/apple in all four second-task outputs. Preservation was imperfect: object sizes, reflections, framing, and table details changed; some lemons were cropped or sliced. These simple product-image tests do not establish portrait identity, pose-transfer, typography, or general editing reliability. Semantic mode was not consistently faster at the larger canvas and remains optional.

Reproduce with `scripts/benchmark_image_edit.py --output <results.json>` against a local ComfyUI server on port 8191. Use `--resolution "balanced | 0.70 MP"`, `--source`, and `--instruction` for the second case. The script does not upload images to an external service.

## Compatibility checks

- 21 automated tests pass, including reference ordering, absence of a source keyframe in REF2VA, semantic mode skipping VAE encoding, and distinct Turbo sigma shifts.
- All ten API workflows were imported into the ComfyUI frontend, saved as UI JSON, reopened, and converted back to prompts with matching node counts.
- All ten PNG previews contain their matching workflow and API metadata.
- No ComfyUI core files or model methods were patched.

## Research decisions

- [REF2VA eight-step 768p release](https://huggingface.co/lightx2v/Minimax-h3-Turbo/discussions/51): use the author's Euler/12/3 recipe, not FL2VA's 6/3 schedule.
- [FL2VA Turbo recipes](https://github.com/ModelTC/Minimax-H3-Turbo): keep adapter and schedule paired. Eight sampling steps do not make the model a native still-image generator.
- [Vision-only references in ComfyUI](https://github.com/Comfy-Org/ComfyUI/commit/1aec3a1351): implemented as an explicit experimental transport, retaining native references by default.
- [FL2VA four-step v1.2](https://huggingface.co/lightx2v/Minimax-h3-Turbo/discussions/52): the author describes primarily audio improvements. Not promoted as a proven image-quality upgrade.
- [500K single-frame VAE](https://huggingface.co/iamkaikai/MiniMax-H3-Single-Frame-VAE-500K): experimental decoder fine-tuning, not a fix for edit conditioning or a general replacement for video encoding. Not made the default without direct compatibility and quality tests.
