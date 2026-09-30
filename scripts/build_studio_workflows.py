"""Build the two small starter canvases and matching API prompts."""
import json
import argparse
from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
MODELS = {
    "diffusion_model": "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
    "text_encoder": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    "video_vae": "minimax_h3_video_vae_fp16.safetensors",
    "turbo_lora": "None",
}


def build(edit):
    slug = "H3_EDIT" if edit else "H3_GENERATE"
    prepare_type = "H3StudioEdit" if edit else "H3StudioGenerate"
    text_key = "instruction" if edit else "prompt"
    text = ("Replace the entire background with solid black. Keep the person, pose and clothing unchanged."
            if edit else "A red ceramic mug on a wooden table, a yellow lemon to its right, soft daylight, natural detailed product photography.")
    aspect = "source image" if edit else "1:1 square"
    api = {
        "1": {"class_type": "H3StudioModels", "inputs": dict(MODELS)},
        "2": {"class_type": prepare_type, "inputs": {
            "models": ["1", 0], text_key: text, "aspect_ratio": aspect, "megapixels": 3.0,
            "width": 2048, "height": 1536,
        }},
        "3": {"class_type": "H3StudioRender", "inputs": {
            "image_job": ["2", 0], "steps": 50, "seed": 42, "sampler_name": "er_sde", "scheduler": "simple",
        }},
        "4": {"class_type": "SaveImage", "inputs": {"images": ["3", 0], "filename_prefix": slug}},
    }
    if edit:
        api["5"] = {"class_type": "LoadImage", "inputs": {"image": "select_your_image.png"}}
        api["2"]["inputs"]["image"] = ["5", 0]

    specs = [
        (1, "H3StudioModels", [20, 80], [410, 260], [],
         [("models", "H3_STUDIO_MODELS")], list(MODELS.values())),
        (2, prepare_type, [500, 80], [480, 430],
         [("models", "H3_STUDIO_MODELS")] + ([("image", "IMAGE"), ("references.reference_image_2", "IMAGE")] if edit else []),
         [("image_job", "H3_STILL_JOB")], [text, aspect, 3.0, 2048, 1536]),
        (3, "H3StudioRender", [1050, 80], [350, 270], [("image_job", "H3_STILL_JOB")],
         [("image", "IMAGE"), ("run_info", "STRING")], [50, 42, "fixed", "er_sde", "simple"]),
        (4, "SaveImage", [1470, 80], [450, 550], [("images", "IMAGE")], [], [slug]),
    ]
    if edit:
        specs.append((5, "LoadImage", [20, 430], [410, 510], [],
                      [("IMAGE", "IMAGE"), ("MASK", "MASK")], ["select_your_image.png", "image"]))
    nodes = []
    for node_id, kind, pos, size, inputs, outputs, widgets in specs:
        node = {"id": node_id, "type": kind, "pos": pos, "size": size, "flags": {},
                "order": node_id-1, "mode": 0,
                "inputs": [{"name": name, "type": typ, "link": None} for name, typ in inputs],
                "outputs": [{"name": name, "type": typ, "links": [], "slot_index": i}
                            for i, (name, typ) in enumerate(outputs)],
                "properties": {"Node name for S&R": kind}, "widgets_values": widgets}
        if kind.startswith("H3Studio"):
            node["properties"].update(cnr_id="minimax-h3-image-studio", ver=VERSION)
        nodes.append(node)
    by_id = {node["id"]: node for node in nodes}
    links = []
    edges = [(1, 0, 2, 0, "H3_STUDIO_MODELS"), (2, 0, 3, 0, "H3_STILL_JOB"), (3, 0, 4, 0, "IMAGE")]
    if edit:
        edges.append((5, 0, 2, 1, "IMAGE"))
    for origin, output, target, slot, typ in edges:
        link_id = len(links) + 1
        links.append([link_id, origin, output, target, slot, typ])
        by_id[origin]["outputs"][output]["links"].append(link_id)
        by_id[target]["inputs"][slot]["link"] = link_id
    workflow = {"last_node_id": max(by_id), "last_link_id": len(links), "nodes": nodes, "links": links,
                "groups": [], "config": {}, "version": 0.4,
                "extra": {"image_studio": {"release": f"v{VERSION}", "pipeline": "single-frame"}}}
    for path, value in [(ROOT / "example_workflows" / f"{slug}.json", workflow),
                        (ROOT / "examples/api" / f"H3_STUDIO_{'EDIT' if edit else 'GENERATE'}_API.json", api)]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate-preview", type=Path)
    parser.add_argument("--edit-preview", type=Path)
    args = parser.parse_args()
    build(False)
    build(True)
    # Optional mechanical thumbnail conversion; runtime nodes need no Pillow.
    for slug, source in (("H3_GENERATE", args.generate_preview), ("H3_EDIT", args.edit_preview)):
        if source:
            from PIL import Image
            with Image.open(source) as image:
                image = image.convert("RGB")
                image.thumbnail((512, 512))
                image.save(ROOT / "example_workflows" / f"{slug}.jpg", quality=90)
