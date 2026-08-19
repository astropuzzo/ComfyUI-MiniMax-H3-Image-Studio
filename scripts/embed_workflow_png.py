#!/usr/bin/env python3
"""Embed ComfyUI workflow and API prompt metadata into example PNG previews."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import tomllib
from pathlib import Path

from PIL import Image, PngImagePlugin


SLUGS = (
    "H3_T2I",
    "H3_T2I_SINGLE",
    "H3_I2I",
    "H3_I2I_SINGLE",
    "H3_REFERENCE_EDIT",
    "H3_REFERENCE_SINGLE",
    "H3_I2I_TURBO",
    "H3_DETAIL_REFINER",
)


def compact_json(path: Path) -> str:
    with path.open("r", encoding="utf-8") as handle:
        return json.dumps(json.load(handle), separators=(",", ":"), ensure_ascii=False)


def project_release(repo_dir: Path) -> str:
    with (repo_dir / "pyproject.toml").open("rb") as handle:
        return f"v{tomllib.load(handle)['project']['version']}"


def embed(repo_dir: Path, slug: str) -> None:
    ui_path = repo_dir / "examples" / "ui" / f"{slug}.json"
    api_path = repo_dir / "examples" / "api" / f"{slug}_API.json"
    png_path = repo_dir / "examples" / "png" / f"{slug}.png"

    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("workflow", compact_json(ui_path))
    metadata.add_text("prompt", compact_json(api_path))
    metadata.add_text("Image Studio release", project_release(repo_dir))

    with Image.open(png_path) as source:
        image = source.copy()
    with tempfile.NamedTemporaryFile(dir=png_path.parent, suffix=".png", delete=False) as temp:
        temp_path = Path(temp.name)
    try:
        image.save(temp_path, format="PNG", pnginfo=metadata, compress_level=9)
        os.replace(temp_path, png_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    with Image.open(png_path) as check:
        if "workflow" not in check.info or "prompt" not in check.info:
            raise RuntimeError(f"Metadata verification failed for {png_path}")
        print(f"embedded workflow + prompt in {png_path.relative_to(repo_dir)} ({check.width}x{check.height})")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    repo_dir = args.repo_dir.resolve()
    for slug in SLUGS:
        embed(repo_dir, slug)


if __name__ == "__main__":
    main()
