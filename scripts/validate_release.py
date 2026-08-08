#!/usr/bin/env python3
"""Validate the dependency-free parts of an Image Studio release."""

from __future__ import annotations

import ast
import json
import struct
import sys
import tomllib
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path


SLUGS = (
    "H3_T2I",
    "H3_I2I",
    "H3_REFERENCE_EDIT",
    "H3_I2I_LIGHTX_TURBO",
)
LEGACY_PROFILES = {
    "quality | 20 steps",
    "speed | 12 steps",
    "turbo | 8 steps (LoRA)",
    "turbo | 4 steps (LoRA, experimental)",
}


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_python(repo: Path) -> None:
    for path in (repo / "nodes.py", repo / "__init__.py", *sorted((repo / "scripts").glob("*.py"))):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def validate_node_documentation(repo: Path) -> None:
    """Keep every public node socket documented without importing ComfyUI."""
    path = repo / "nodes.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def assignment_value(class_node: ast.ClassDef, name: str):
        for statement in class_node.body:
            if isinstance(statement, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name for target in statement.targets
            ):
                return statement.value
        return None

    def literal(value, context: str):
        try:
            return ast.literal_eval(value)
        except (ValueError, TypeError) as exc:
            raise AssertionError(f"{context}: metadata must remain statically auditable") from exc

    classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name.startswith("H3")]
    assert classes, f"{path}: no H3 node classes found"

    for class_node in classes:
        description_ast = assignment_value(class_node, "DESCRIPTION")
        assert description_ast is not None, f"{class_node.name}: missing DESCRIPTION"
        description = literal(description_ast, f"{class_node.name}.DESCRIPTION")
        assert isinstance(description, str) and description.strip(), f"{class_node.name}: empty DESCRIPTION"

        returns_ast = assignment_value(class_node, "RETURN_TYPES")
        assert returns_ast is not None, f"{class_node.name}: missing RETURN_TYPES"
        return_types = literal(returns_ast, f"{class_node.name}.RETURN_TYPES")
        tooltips_ast = assignment_value(class_node, "OUTPUT_TOOLTIPS")
        output_tooltips = () if tooltips_ast is None else literal(
            tooltips_ast, f"{class_node.name}.OUTPUT_TOOLTIPS"
        )
        assert len(output_tooltips) == len(return_types), (
            f"{class_node.name}: {len(return_types)} outputs but {len(output_tooltips)} output tooltips"
        )
        assert all(isinstance(item, str) and item.strip() for item in output_tooltips), (
            f"{class_node.name}: empty output tooltip"
        )

        input_method = next(
            (item for item in class_node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "INPUT_TYPES"),
            None,
        )
        assert input_method is not None, f"{class_node.name}: missing INPUT_TYPES"
        return_statement = next((item for item in ast.walk(input_method) if isinstance(item, ast.Return)), None)
        assert return_statement is not None and isinstance(return_statement.value, ast.Dict), (
            f"{class_node.name}.INPUT_TYPES: expected a dictionary return"
        )
        sections = {
            literal(key, f"{class_node.name}.INPUT_TYPES section"): value
            for key, value in zip(return_statement.value.keys, return_statement.value.values)
            if key is not None
        }
        for section_name in ("required", "optional"):
            section = sections.get(section_name)
            if section is None:
                continue
            assert isinstance(section, ast.Dict), f"{class_node.name}.{section_name}: expected a dictionary"
            for key, spec in zip(section.keys, section.values):
                input_name = literal(key, f"{class_node.name}.{section_name} input")
                assert isinstance(spec, ast.Tuple) and len(spec.elts) >= 2 and isinstance(spec.elts[1], ast.Dict), (
                    f"{class_node.name}.{input_name}: missing tooltip options"
                )
                options = {
                    literal(option_key, f"{class_node.name}.{input_name} option"): option_value
                    for option_key, option_value in zip(spec.elts[1].keys, spec.elts[1].values)
                    if option_key is not None
                }
                assert "tooltip" in options, f"{class_node.name}.{input_name}: missing input tooltip"
                tooltip = literal(options["tooltip"], f"{class_node.name}.{input_name}.tooltip")
                assert isinstance(tooltip, str) and tooltip.strip(), f"{class_node.name}.{input_name}: empty input tooltip"


def validate_metadata(repo: Path) -> None:
    with (repo / "pyproject.toml").open("rb") as handle:
        metadata = tomllib.load(handle)
    assert metadata["project"]["version"] == "15.0.0"
    comfy = metadata["tool"]["comfy"]
    assert comfy["PublisherId"] == "astropuzzo"
    assert comfy["requires-comfyui"] == ">=0.30.0"
    assert comfy["Icon"].endswith("/assets/branding/minimax-h3-image-studio.svg")
    assert comfy["Banner"].endswith("/assets/branding/minimax-h3-banner.svg")


def validate_registry_assets(repo: Path) -> None:
    def svg_size(path: Path) -> tuple[float, float]:
        root = ET.parse(path).getroot()
        view_box = [float(value) for value in root.attrib["viewBox"].split()]
        assert len(view_box) == 4 and view_box[2] > 0 and view_box[3] > 0, f"{path}: invalid viewBox"
        width = float(root.attrib.get("width", view_box[2]))
        height = float(root.attrib.get("height", view_box[3]))
        assert width == view_box[2] and height == view_box[3], f"{path}: rendered and viewBox sizes differ"
        return width, height

    icon = repo / "assets" / "branding" / "minimax-h3-image-studio.svg"
    icon_width, icon_height = svg_size(icon)
    assert icon_width == icon_height and icon_width <= 400, f"{icon}: Registry icon must be square and at most 400px"

    banner = repo / "assets" / "branding" / "minimax-h3-banner.svg"
    banner_width, banner_height = svg_size(banner)
    assert abs((banner_width / banner_height) - (21 / 9)) < 1e-9, f"{banner}: Registry banner must be 21:9"

    workflow = (repo / ".github" / "workflows" / "publish_registry.yml").read_text(encoding="utf-8")
    assert "Comfy-Org/publish-node-action@main" in workflow
    assert "REGISTRY_ACCESS_TOKEN" in workflow


def validate_api(repo: Path, slug: str) -> dict:
    path = repo / "examples" / "api" / f"{slug}_API.json"
    prompt = load_json(path)
    assert isinstance(prompt, dict) and prompt, f"{path}: empty API workflow"

    for node_id, node in prompt.items():
        assert isinstance(node.get("class_type"), str), f"{path}: {node_id} has no class_type"
        assert isinstance(node.get("inputs"), dict), f"{path}: {node_id} has no inputs"
        for input_name, value in node["inputs"].items():
            if isinstance(value, list):
                assert len(value) == 2, f"{path}: malformed link {node_id}.{input_name}"
                origin, slot = value
                assert str(origin) in prompt, f"{path}: missing origin {origin}"
                assert isinstance(slot, int) and slot >= 0, f"{path}: invalid origin slot"

    nodes_by_type = {node["class_type"]: (node_id, node) for node_id, node in prompt.items()}
    decode_id, _ = nodes_by_type["H3ImageDecode"]
    _, selector = nodes_by_type["H3ImageFrameSelector"]
    assert selector["inputs"]["strategy"] == "decode_recommended"
    assert selector["inputs"]["skip_first_frames"] == 0
    assert selector["inputs"]["recommended_index"] == [decode_id, 3]

    _, sampling = nodes_by_type["H3ImageSamplingPreset"]
    profile = sampling["inputs"]["sampling_profile"]
    assert profile not in LEGACY_PROFILES, f"{path}: legacy sampling profile {profile}"

    if slug == "H3_I2I_LIGHTX_TURBO":
        _, lora = nodes_by_type["LoraLoaderModelOnly"]
        assert lora["inputs"]["strength_model"] == 0.75
        assert "lightx2v_turbo_4step_v0.1_comfy" in lora["inputs"]["lora_name"]
        assert profile == "LightX v0.1 | ER-SDE 4 steps"
    return prompt


def validate_ui(repo: Path, slug: str, prompt: dict) -> dict:
    path = repo / "examples" / "ui" / f"{slug}.json"
    workflow = load_json(path)
    assert workflow.get("version") == 0.4, f"{path}: expected workflow schema 0.4"
    nodes = workflow.get("nodes")
    links = workflow.get("links")
    assert isinstance(nodes, list) and isinstance(links, list)
    assert len(nodes) == len(prompt) + 4, f"{path}: expected four documentation cards"
    assert sum(node.get("type") == "H3WorkflowNote" for node in nodes) == 4
    assert len({node["id"] for node in nodes}) == len(nodes), f"{path}: duplicate node ids"

    node_ids = {node["id"] for node in nodes}
    link_ids = set()
    for link in links:
        assert len(link) >= 6, f"{path}: malformed link"
        link_id, origin, origin_slot, target, target_slot, _link_type = link[:6]
        assert link_id not in link_ids, f"{path}: duplicate link id {link_id}"
        link_ids.add(link_id)
        assert origin in node_ids and target in node_ids, f"{path}: dangling link {link_id}"
        assert origin_slot >= 0 and target_slot >= 0

    decode = next(node for node in nodes if node["type"] == "H3ImageDecode")
    selector = next(node for node in nodes if node["type"] == "H3ImageFrameSelector")
    assert any(link[1] == decode["id"] and link[2] == 3 and link[3] == selector["id"] for link in links), (
        f"{path}: decoder recommendation is not connected"
    )
    assert workflow.get("extra", {}).get("image_studio", {}).get("release") == "v15.0.0"
    return workflow


def read_png(path: Path) -> tuple[int, int, dict[str, str]]:
    raw = path.read_bytes()
    assert raw.startswith(b"\x89PNG\r\n\x1a\n"), f"{path}: invalid PNG signature"
    cursor = 8
    width = height = 0
    text: dict[str, str] = {}
    while cursor < len(raw):
        length = struct.unpack(">I", raw[cursor:cursor + 4])[0]
        chunk_type = raw[cursor + 4:cursor + 8]
        data = raw[cursor + 8:cursor + 8 + length]
        cursor += 12 + length
        if chunk_type == b"IHDR":
            width, height = struct.unpack(">II", data[:8])
        elif chunk_type == b"tEXt":
            key, value = data.split(b"\0", 1)
            text[key.decode("latin-1")] = value.decode("latin-1")
        elif chunk_type == b"zTXt":
            key, payload = data.split(b"\0", 1)
            text[key.decode("latin-1")] = zlib.decompress(payload[1:]).decode("latin-1")
        elif chunk_type == b"iTXt":
            key, payload = data.split(b"\0", 1)
            compression_flag, _compression_method = payload[:2]
            payload = payload[2:]
            _language, payload = payload.split(b"\0", 1)
            _translated_keyword, value = payload.split(b"\0", 1)
            if compression_flag:
                value = zlib.decompress(value)
            text[key.decode("latin-1")] = value.decode("utf-8")
        elif chunk_type == b"IEND":
            break
    return width, height, text


def validate_png(repo: Path, slug: str, prompt: dict, workflow: dict) -> None:
    path = repo / "examples" / "png" / f"{slug}.png"
    width, height, text = read_png(path)
    assert width >= 2400 and height >= 1300, f"{path}: preview is too small"
    assert json.loads(text["prompt"]) == prompt, f"{path}: embedded API prompt differs"
    assert json.loads(text["workflow"]) == workflow, f"{path}: embedded UI workflow differs"
    assert text.get("Image Studio release") == "v15.0.0"


def validate_repo(repo: Path) -> None:
    validate_python(repo)
    validate_node_documentation(repo)
    validate_metadata(repo)
    validate_registry_assets(repo)
    for slug in SLUGS:
        prompt = validate_api(repo, slug)
        workflow = validate_ui(repo, slug, prompt)
        validate_png(repo, slug, prompt, workflow)


def main() -> None:
    repo = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]).resolve()
    validate_repo(repo)
    print(f"release validation passed: {len(SLUGS)} API + UI + metadata PNG workflow sets")


if __name__ == "__main__":
    main()
