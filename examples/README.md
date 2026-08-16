# Example workflows

- `ui/`: canvas workflow JSON
- `png/`: workflow preview with embedded UI and API metadata
- `api/`: prompt JSON for API clients

Open files from `ui/` or `png/` in ComfyUI. Files from `api/` do not contain a canvas layout.

These workflows require MiniMax H3 Image Studio v16 or newer. Restart ComfyUI and reopen the workflow after updating the node package.

| File stem | Workflow |
|---|---|
| `H3_T2I` | FL2VA text-to-image |
| `H3_I2I` | FL2VA image-to-image |
| `H3_REFERENCE_EDIT` | REF2VA reference editing |
| `H3_I2I_TURBO` | FL2VA image-to-image with the official Turbo v1.0 eight-step adapter |

Image-to-image and reference-edit workflows require an image in every `Load Image` node.
