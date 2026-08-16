# Contributing

Bug reports and focused pull requests are welcome.

## Bug reports

Include:

- ComfyUI, Python, PyTorch, and Image Studio versions
- operating system, GPU, VRAM, and system RAM
- diffusion model, text encoder, VAE, and LoRA filenames
- workflow mode, resolution, frame profile, sampler, scheduler, steps, and seed
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

When an API workflow changes, rebuild its UI JSON and PNG metadata in the same pull request.
