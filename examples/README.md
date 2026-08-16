# Example workflows

- `ui/`: canvas workflow JSON
- `png/`: workflow preview with embedded UI and API metadata
- `api/`: prompt JSON for API clients

Open files from `ui/` or `png/` in ComfyUI. Files from `api/` do not contain a canvas layout.

| File stem | Workflow |
|---|---|
| `H3_T2I` | FL2VA text-to-image |
| `H3_I2I` | FL2VA image-to-image |
| `H3_REFERENCE_EDIT` | REF2VA reference editing |
| `H3_I2I_LIGHTX_TURBO` | FL2VA image-to-image with LightX v0.1 |

Image-to-image and reference-edit workflows require an image in every `Load Image` node.
