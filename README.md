# ComfyUI MiniMax H3 Image Studio v14

> [!WARNING]
> **Experimental, AI-coded project.** The extension and documentation are AI-assisted and guided by hands-on image testing. MiniMax H3 and ComfyUI support are evolving quickly; regressions and hardware-specific behavior are possible. Bug reports and code review are welcome.

Image-first ComfyUI nodes for using MiniMax H3 as a practical **text-to-image, image-to-image, and reference-edit** generator, with multi-frame still selection, full candidate-batch inspection, high-resolution controls, and **Turbo LoRA presets**.

Built around:

- [MiniMax H3](https://huggingface.co/MiniMaxAI/MiniMax-H3)
- [ComfyUI's H3 implementation](https://docs.comfy.org/tutorials/video/minimax/minimax-h3)
- [Comfy-Org MiniMax H3 weights](https://huggingface.co/Comfy-Org/MiniMax-H3)

## What's new in v14

- Fixes the H3 `audio_scale` / `SamplerCustomAdvanced` crash caused by replacing ComfyUI's AV sampler with a plain flow sampler.
- The fix now lives **directly in `nodes.py`**: H3 keeps `ModelSamplingAV`, `audio_shift`, `multiplier`, and `noise_scale` correctly.
- Adds **Turbo LoRA sampling presets**:
  - `turbo | 8 steps (LoRA)` — recommended starting point for compatible Turbo adapters.
  - `turbo | 4 steps (LoRA, experimental)` — aggressive distilled target; quality/compatibility can vary.
- Adds intermediate temporal profiles:
  - `recommended | 5 frames`
  - `extended quality | 9 frames`
  - `high quality | 13 frames`
  - `maximum quality | 20 frames (slow)`
- Adds a Turbo LoRA API workflow example using ComfyUI's standard `LoraLoaderModelOnly`.
- Documents optional SageAttention acceleration.

## Installation

**Requires ComfyUI 0.30.0 or newer.**

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/astropuzzo/ComfyUI-MiniMax-H3-Image-Studio.git
```

Restart ComfyUI after installing or updating the node pack.

## Image nodes

- `MiniMax H3 Image • Text to Image` — FL2VA text-to-image preparation.
- `MiniMax H3 Image • Image to Image` — FL2VA source-frame anchor editing.
- `MiniMax H3 Image • Reference Edit` — REF2VA with up to nine ordered references.
- `MiniMax H3 Image • Resolution Preset` — common aspect/size profiles.
- `MiniMax H3 Image • Sampling Preset` — base and Turbo-oriented presets.
- `MiniMax H3 Image • Exact Frame Decode` — preserves the requested temporal profile.
- `MiniMax H3 Image • Single Image Output` — scores/selects one still or exposes the full decoded batch.
- `MiniMax H3 Image • Advanced Resolution` — manual canvas/grid controls.
- `MiniMax H3 Image • Advanced Sampling` — manual sampler, scheduler, denoise and H3 sigma shifts.
- `MiniMax H3 Image • Advanced Combined Prepare` — combined T2I/I2I/REF2VA preparation.

All nodes include `DESCRIPTION` metadata and tooltips for non-obvious controls.

## Important: H3 is internally audio-video even for image output

MiniMax H3 uses a packed audio-video latent internally. Image Studio only decodes the **video/image** stream, but ComfyUI still needs H3's AV sampling contract during denoising.

That means the sampling object must retain `ModelSamplingAV` and its `audio_scale` behavior. v14 does this directly in `nodes.py` while preserving:

- video sigma shift;
- audio sigma shift;
- the model sampling `multiplier`;
- `noise_scale`.

The audio VAE is **not** required for Image Studio output and no audio is saved.

## Frame profiles and still selection

H3 denoises the entire temporal packet jointly. The frame setting is therefore temporal context for generation, not a post-generation frame counter.

| Profile | Requested frames | Notes |
|---|---:|---|
| `recommended | 5 frames` | 5 | Default, best tested speed/quality balance |
| `extended quality | 9 frames` | 9 | Intermediate temporal context |
| `high quality | 13 frames` | 13 | Higher temporal context |
| `maximum quality | 20 frames (slow)` | 20 | Maximum exposed profile; significantly more compute |

The 9- and 13-frame options correspond to exact intermediate decode lengths produced by H3's temporal token pattern, so they avoid decoding a larger packet only to discard most of it. ComfyUI's official video node exposes the standard `17k+5` duration grid instead, so these intermediate image-oriented profiles should be treated as additional Image Studio tuning options rather than official MiniMax presets. The existing 5- and 20-frame profile names remain unchanged for saved workflows.

### `Single Image Output`

- `emit_candidate_batch = false` — `selected_image` contains only the selected still.
- `emit_candidate_batch = true` — `selected_image` contains the **complete decoded frame profile**.
- `candidate_batch_debug` contains the ranked subset controlled by `top_k`.
- `top_k` does **not** limit the complete batch emitted through `selected_image`.

Source-dependent strategies report their effective fallback if `source_image` is missing.

## Sampling presets

| Preset | Sampler | Scheduler | Steps | Video shift | Audio shift | Intended use |
|---|---|---|---:|---:|---:|---|
| `quality | 20 steps` | `res_multistep` | `simple` | 20 | 12 | 3 | Base H3 quality |
| `speed | 12 steps` | `res_multistep` | `simple` | 12 | 12 | 3 | Faster base H3 |
| `turbo | 8 steps (LoRA)` | `res_multistep` | `simple` | 8 | 12 | 4 | Recommended starting point with compatible Turbo LoRA |
| `turbo | 4 steps (LoRA, experimental)` | `res_multistep` | `simple` | 4 | 12 | 4 | Aggressive Turbo target; experimental in this pack |

The Turbo presets do not automatically load a LoRA. Load the compatible Turbo adapter **upstream** using ComfyUI's standard `LoraLoaderModelOnly`, then feed the patched model into `MiniMax H3 Image • Sampling Preset`.

## Turbo LoRA support

Turbo adapters are third-party and can target different H3 checkpoint variants or training recipes. Image Studio therefore does not auto-detect, auto-download, or auto-load a particular adapter. The included presets use `res_multistep`, video shift `12`, audio shift `4`, and either 8 or 4 sampling steps. **Start with the 8-step preset** and follow the adapter author's recommended strength/settings; the 4-step preset is deliberately marked experimental.

Place compatible LoRA files in:

```text
ComfyUI/models/loras/
```

Then connect:

```text
UNETLoader
   ↓
LoraLoaderModelOnly
   ↓
MiniMax H3 Image • Sampling Preset
   ↓
BasicGuider / SamplerCustomAdvanced
```

The included example workflow starts at LoRA strength `1.0`. Treat that as a neutral starting point rather than a universal recommendation, and follow the adapter model card when it specifies a different range.

The Turbo adapter is not bundled, downloaded, or maintained by Image Studio.

## Optional SageAttention acceleration

SageAttention is optional. ComfyUI has SageAttention support when a compatible package/build is installed, and H3 Turbo community workflows may also use it as an optional accelerator.

Image Studio does **not** install or force SageAttention because compatibility and the fastest backend depend on GPU generation, CUDA/PyTorch build and operating system. If you already use a compatible SageAttention setup, it can be placed upstream as a model patch without changing Image Studio's sampling presets.

For some systems, workflow-level SageAttention patches may be preferable to a global launch flag. Test on your own hardware and compare against the default ComfyUI attention backend.

## Resolution

The preset selector uses ComfyUI's megapixel convention (`1 MP = 1024² pixels`) and H3's 32-pixel grid. It includes low-resolution previews, native-area profiles, 2/4/8 MP profiles and a custom 0.1–64 MP range.

Selecting `source image` without a connected image raises an actionable error rather than silently creating a square canvas.

Direct high-resolution generation is allowed, but H3-Base is not the unreleased H3-Regenerate-2K pipeline. Higher resolution can dramatically increase VRAM/RAM and attention cost without proportional learned detail.

## Advanced Sampling

Advanced Sampling supports:

- ComfyUI sampler selection;
- standard schedulers plus `beta_custom`;
- ComfyUI-style `denoise` semantics for every scheduler;
- video/audio sigma shifts;
- custom beta alpha/beta.

`denoise = 0` returns an empty sigma schedule. Values below 1 build a longer schedule and keep its final `steps + 1` sigmas, matching ComfyUI's BasicScheduler-style behavior.

## Optimize for still / Source Fidelity

`optimize_for_still` modifies only the prompt sent to H3. It does not change frames, resolution, steps, sampler, scheduler or model weights.

`source_fidelity` controls preservation language for identity, pose, composition, perspective and geometry. **It is not a denoise slider.**

## Example workflows

The `examples/` directory contains API-format workflows.

- `H3_T2I_API.json` — base T2I.
- `H3_I2I_API.json` — base FL2VA I2I.
- `H3_REFERENCE_EDIT_API.json` — base REF2VA reference edit.
- `H3_I2I_TURBO_LORA_API.json` — Turbo LoRA example with `LoraLoaderModelOnly` and the 8-step Turbo preset.

The Turbo example uses a model-only LoRA because the H3 Turbo adapter targets the diffusion model, not the Qwen text encoder.

## Models

Follow the [official ComfyUI MiniMax H3 guide](https://docs.comfy.org/tutorials/video/minimax/minimax-h3) and download the required H3 files from [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3).

Standard model locations:

- diffusion checkpoints → `ComfyUI/models/diffusion_models/`
- Qwen text encoder → `ComfyUI/models/text_encoders/`
- video VAE → `ComfyUI/models/vae/`
- Turbo LoRA adapters → `ComfyUI/models/loras/`

Example 24 GB VRAM files used by the base workflows:

- FL2VA: `minimax_h3_fl2va_pruned_int8_convrot.safetensors`
- REF2VA: `minimax_h3_ref2va_pruned_int8_convrot.safetensors`
- text encoder: `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`
- video VAE: `minimax_h3_video_vae_fp16.safetensors`

The audio VAE is not needed for Image Studio output.

## Performance notes

Historical local measurements used a 24 GB RTX 4090 setup with SageAttention and the base H3 checkpoints. They predate the Turbo LoRA presets and should not be used as Turbo benchmarks.

| Resolution | Requested frames | Natural packet | 20-step time |
|---:|---:|---:|---:|
| 1.99 MP | 5 | 5 | 27.8 s |
| 1.99 MP | 20 | 22 | 50.1 s |
| 3.96 MP | 5 | 5 | 38.9 s |
| 3.96 MP | 20 | 22 | 138.6 s |
| 8.02 MP | 5 | 5 | 84.0 s |
| 8.02 MP | 20 | 22 | 401.8 s |

Actual performance depends heavily on resolution, frame profile, attention backend, checkpoint format, LoRA strength, GPU, VRAM and system memory.

## Memory behavior

High-resolution multi-frame decode can consume tens of gigabytes of combined VRAM, RAM and Windows commit/pagefile space. Metric scoring downsamples candidates in small fp32 chunks instead of first upcasting the complete high-resolution packet.

Enabling `emit_candidate_batch` intentionally retains the complete decoded batch and therefore uses more memory.

## Feedback and contributions

Please use [GitHub Issues](https://github.com/astropuzzo/ComfyUI-MiniMax-H3-Image-Studio/issues). Include:

- ComfyUI version / commit;
- GPU and VRAM;
- system RAM;
- checkpoint and LoRA filenames;
- LoRA strength;
- mode (T2I/I2I/REF2VA);
- resolution and frame profile;
- sampling preset or sampler/scheduler/steps/shifts;
- workflow JSON;
- full console traceback.

## Registry metadata

`pyproject.toml` contains ComfyUI Registry metadata and declares `requires-comfyui = ">=0.30.0"`.

## License

Image Studio is released under [The Unlicense](LICENSE). MiniMax H3, ComfyUI, Turbo LoRAs and other third-party components retain their own licences and attribution requirements.
