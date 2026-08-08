# Example workflows

The v15 examples are intentionally separated by format:

- `ui/` — normal ComfyUI workflow JSON. Open or drag these into the canvas.
- `png/` — rendered workflow previews containing both `workflow` and `prompt` metadata. Drag them into ComfyUI exactly like a workflow image.
- `api/` — prompt-only JSON for API clients. These are not canvas-layout files.

Every UI workflow includes four editable `MiniMax H3 Image • Workflow Note` cards explaining quick start, model paths, setting choices and optional/experimental additions.

| Stem | Purpose |
|---|---|
| `H3_T2I` | Base FL2VA text-to-image. |
| `H3_I2I` | Base FL2VA source-anchor image editing. |
| `H3_REFERENCE_EDIT` | REF2VA ordered reference editing. |
| `H3_I2I_LIGHTX_TURBO` | Kijai/LightX v0.1 four-step I2I recipe. |

If dragging a file shows an empty canvas, check its directory: use `ui/` or `png/`, not `api/`.
