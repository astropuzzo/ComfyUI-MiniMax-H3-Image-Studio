# Contributing

Bug reports and focused pull requests are welcome.

## Bug reports

Include:

- ComfyUI, Python, PyTorch, and Image Studio versions
- operating system, GPU, VRAM, and system RAM
- diffusion model, text encoder, VAE, and LoRA filenames
- workflow mode, resolution, sampler, scheduler, steps and seed (also frame profile for historical workflows)
- workflow JSON or metadata PNG
- complete console traceback
- expected and actual behavior

Restart ComfyUI and test the latest `main` branch before reporting a bug. Do not post private prompts, tokens, or personal images.

## Pull requests

Keep each change focused. Explain the problem, the change, and the validation performed.

Run:

```bash
python scripts/validate_release.py
python -m unittest discover -s tests -v
```

Rebuild the two starter UI/API workflows with `python scripts/build_studio_workflows.py`. Check them in the current ComfyUI frontend. Historical examples keep their original PNG metadata and version labels.
