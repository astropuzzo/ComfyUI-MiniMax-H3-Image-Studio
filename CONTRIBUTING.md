# Contributing

This is an experimental, entirely AI-coded project maintained through practical
image testing rather than formal software or model-engineering expertise.
Technical review, corrections, suggestions, bug reports, and pull requests are
welcome.

## Bug reports

Please open a GitHub issue and include:

- ComfyUI, Python, and PyTorch versions
- operating system, GPU, VRAM, and system RAM
- exact diffusion model, text encoder, and VAE filenames
- T2I, I2I, or Reference Edit mode
- resolution, requested frames, steps, sampling profile, and seed
- workflow JSON and relevant console output
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
