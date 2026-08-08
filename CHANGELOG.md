# Changelog

All notable changes to MiniMax H3 Image Studio are documented here.

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

## [0.14.0]

- Fixed H3 `audio_scale` failures by retaining `ModelSamplingAV` during sigma-shift patching.
- Added 5/9/13/20-frame image profiles.
- Added the first generic Turbo-oriented sampling presets and API example.
- Added full candidate-batch inspection and memory-conscious metric scoring.
