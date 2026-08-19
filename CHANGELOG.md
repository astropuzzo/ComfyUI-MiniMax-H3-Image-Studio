# Changelog

All notable changes to MiniMax H3 Image Studio are documented here.

## [Unreleased]

## [21.0.0] - 2026-08-20

- Replaced the FLUX.2 Klein 4B refiner with a four-step Qwen Image Edit 2511 generative detail pass.
- Preserved the original H3 image dimensions instead of shrinking output to the model working canvas.
- Added a preservation-first Qwen edit prompt and full-resolution tone-lock path.

## [20.0.0] - 2026-08-20

- Added a custom mode to Sampling Preset with sampler, scheduler, steps, denoise, H3 sigma shifts, and beta-scheduler controls.
- Kept documented and legacy presets immutable so existing workflows retain their exact recipes.

## [19.0.0] - 2026-08-18

- Added an optional four-step FLUX.2 Klein 4B detail-refinement workflow for any H3 still image.
- Added Detail Tone Lock to restore H3 lighting and color while blending refined detail conservatively.
- Replaced broad restoration wording with a preservation-first prompt that protects eye state, identity, composition, and exposure.
- Added runtime-tested model, sampler, scheduler, prompt, and color-preservation guidance.

## [18.0.0] - 2026-08-18

- Added dedicated one-frame text-to-image and image-to-image workflows using the experimental H3 image VAE.
- Changed one-frame I2I to use editable REF2VA source conditioning instead of an impossible FL2VA frame-0 anchor.
- Preserved the existing FL2VA keyframe path for every multi-frame I2I profile.
- Added runtime and release validation for all three one-frame workflows.

## [17.0.2] - 2026-08-17

- Reduced the experimental single-image detail adapter from `1.0` to `0.5` after direct pose-transfer testing showed that full strength could suppress requested edits.

## [17.0.1] - 2026-08-17

- Made explicit multi-reference trait assignments override Picture 1 preservation wording.
- Removed ambiguous still-image wording that could lock the result to Picture 1's composition.
- Lowered the reference-edit preservation default to `0.60` for visible pose and composition transfers.
- Corrected the official Turbo adapter download link.

## [17.0.0] - 2026-08-17

- Added a genuine one-frame H3 latent profile without monkey-patching ComfyUI.
- Added an experimental single-image REF2VA workflow using the community hybrid checkpoint, image VAE, and published adapter stack.
- Fixed batched reference sockets shifting `<Picture N>` numbering by using exactly one picture per socket, matching upstream REF2VA behavior.
- Reworked reference prompt optimization to assign explicit picture roles without broadly freezing pose, composition, and geometry.
- Added missing-reference-role diagnostics and clearer one-frame VAE compatibility guidance.
- Changed the standard REF2VA example to retain maximum available reference detail.

## [16.0.0] - 2026-08-17

- Replaced the bundled experimental LightX v0.1 recipe with the official FL2VA Turbo v1.0 eight-step adapter and Euler schedule.
- Renamed the bundled Turbo workflow files to `H3_I2I_TURBO`.
- Added official FL2VA 768p four-step and REF2VA four-step sampling profiles.
- Kept the former LightX profile names as legacy inputs so saved workflows still import.
- Fixed short image-to-image packets selecting the unchanged source anchor as their default output.
- Added stable-quality recommendations for text-to-image and reference-edit packets.
- Documented current official model variants, SageAttention, native resolution, and experimental model limits.

## [15.0.1] - 2026-08-17

- Removed documentation-only nodes from bundled workflows.
- Added direct troubleshooting for stale node definitions, missing workflow nodes, and image inputs.
- Rewrote the README, node descriptions, prompt wrapper, and contributor guide for clarity.
- Derived generated workflow and PNG release metadata from `pyproject.toml` to prevent version drift.

## [15.0.0] - 2026-08-08

### Fixed

- Replaced API-only drag targets with proper ComfyUI schema-0.4 UI workflows, fixing the reported Empty canvas behavior.
- Preserved native `ModelSamplingAV` on current ComfyUI and added an `audio_scale` compatibility shim for older H3-capable cores.
- Removed the default one-frame skip from still selection.
- Appended decoder `recommended_index` without changing the first three decoder outputs, preserving old links.
- Connected the recommendation in every bundled workflow.
- Corrected stale 5/20-only help text to cover 5/9/13/20 profiles.

### Added

- UI, API and metadata-PNG variants for T2I, I2I, REF2VA and LightX v0.1 I2I.
- Four editable documentation cards in every UI workflow.
- Adapter-specific LightX v0.1 ER-SDE and SA-Solver four-step profiles using shifts 12/3.
- LightX workflow using the Comfy-format v0.1 LoRA at published starting strength 0.75.
- Coordinated node colors, minimum card widths, icon and banner assets.
- Complete node, input and output tooltips, enforced by the dependency-free release validator.
- Non-executing `H3WorkflowNote` documentation node.
- Release validator, runtime unit tests, GitHub Actions CI and CI-gated tag/release publishing.
- Correct Comfy Registry project version, publisher and icon metadata.
- Documentation for current KJNodes H3 optimizations, Tiny VAE preview, experimental w4a8 models and Larry Turbo separation.

### Changed

- Renamed displayed base sampling profiles to make the RES Multistep recipe explicit.
- Deprecated ambiguous generic Turbo labels while retaining their exact strings for older saved workflows.
- Made `decode_recommended` the selector default and `first` an explicit fixed strategy.
- Reorganized examples into `examples/ui`, `examples/api` and `examples/png`.

### Verification

- Loaded in ComfyUI 0.31.0 on CPU.
- Converted and rendered all workflows through the current ComfyUI frontend.
- Verified JSON structure, link integrity and PNG metadata round trips.
- Tested selector, decoder and sampling compatibility logic with PyTorch.
- Audited all 11 node definitions through ComfyUI's live `/object_info` endpoint.

## [14.0.0] - 2026-08-08

- Fixed H3 `audio_scale` failures by retaining `ModelSamplingAV` during sigma-shift patching.
- Added 5/9/13/20-frame image profiles.
- Added the first generic Turbo-oriented sampling presets and API example.
- Added full candidate-batch inspection and memory-conscious metric scoring.

## [13.0.0] - 2026-08-03

- Added ordered multi-image REF2VA input with one source plus up to eight reference images.
- Simplified first-frame profiles and fixed the 20-frame image-editing path.
- Preserved complete candidate batches for inspection and downstream selection.
- Completed a full node audit and fixed the H3 `audio_scale` sampling crash.

## [12.0.0] - 2026-08-03

- Added measured performance, memory and output-quality guidance.
- Documented the practical trade-offs of frame count and output resolution.

## [11.0.0] - 2026-08-03

- Removed untested auxiliary nodes and focused the extension on image generation and editing.
- Documented the project's experimental status.

## [10.0.0] - 2026-08-03

- Published the first repository snapshot of MiniMax H3 Image Studio.
- Added text-to-image, image-to-image and reference-edit conditioning.
- Added arbitrary frame counts, resolution controls and automatic still-frame selection.
