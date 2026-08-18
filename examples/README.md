# Example workflows

- `ui/`: canvas workflow JSON
- `png/`: workflow preview with embedded UI and API metadata
- `api/`: prompt JSON for API clients

Open files from `ui/` or `png/` in ComfyUI. Files from `api/` do not contain a canvas layout.

These workflows require MiniMax H3 Image Studio v19 or newer. Restart ComfyUI and reopen the workflow after updating the node package.

| File stem | Workflow |
|---|---|
| `H3_T2I` | FL2VA text-to-image |
| `H3_T2I_SINGLE` | Experimental one-frame text-to-image with the H3 image VAE |
| `H3_I2I` | FL2VA image-to-image |
| `H3_I2I_SINGLE` | Experimental one-frame image-to-image through reference conditioning |
| `H3_REFERENCE_EDIT` | REF2VA reference editing |
| `H3_REFERENCE_SINGLE` | Experimental true one-frame reference generation |
| `H3_I2I_TURBO` | FL2VA image-to-image with the official Turbo v1.0 eight-step adapter |
| `H3_FAST_REFINER` | Optional four-step FLUX.2 Klein 4B detail pass for any finished H3 image |

Image-to-image and reference-edit workflows require an image in every `Load Image` node.
The refiner also requires a finished image. Its `Load Image` can be replaced with the output of any H3 `Single Image Output` node.
