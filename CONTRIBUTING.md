# Contributing

This is an experimental, AI-assisted project maintained through practical image
testing and review across the MiniMax H3/ComfyUI community.
Technical review, corrections, suggestions, bug reports, and pull requests are
welcome.

## Bug reports

Please open a GitHub issue and include:

- ComfyUI, Python, and PyTorch versions
- operating system, GPU, VRAM, and system RAM
- Image Studio release plus exact diffusion model, text encoder, VAE and adapter filenames
- T2I, I2I, or Reference Edit mode
- resolution, requested frames, steps, sampling profile, and seed
- UI workflow JSON or metadata PNG and relevant console output
- expected and actual behavior
- example images when they can be shared safely

Check that the problem still occurs after restarting ComfyUI and using the
latest `main` branch. Do not include private prompts, tokens, personal images,
or other sensitive information in a public issue.

## Pull requests

Keep changes focused and explain the observed problem, the proposed behavior,
and how the change was tested. Preserve existing workflow compatibility where
possible. Runtime tests on more GPUs, operating systems, resolutions, and model
variants are particularly useful.

Before opening a pull request, run:

```bash
python scripts/validate_release.py
python -m unittest discover -s tests -v
```

If you change an example API workflow, regenerate and verify its corresponding
UI JSON and PNG metadata artifact as part of the same pull request.
