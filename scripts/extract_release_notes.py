#!/usr/bin/env python3
"""Print one version section from CHANGELOG.md for the Comfy Registry."""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def project_version(pyproject: Path) -> str:
    with pyproject.open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def extract_release_notes(changelog: str, version: str) -> str:
    heading = re.compile(
        rf"^## \[{re.escape(version)}\](?:[ \t]+-[ \t]+[^\n]*)?[ \t]*$",
        re.MULTILINE,
    )
    match = heading.search(changelog)
    if match is None:
        raise ValueError(f"CHANGELOG.md has no section for {version}")

    next_heading = re.search(r"^## \[", changelog[match.end():], re.MULTILINE)
    end = match.end() + next_heading.start() if next_heading else len(changelog)
    notes = changelog[match.end():end].strip()
    if not notes:
        raise ValueError(f"CHANGELOG.md section {version} is empty")
    return notes


def main() -> None:
    version = sys.argv[1] if len(sys.argv) > 1 else project_version(REPO / "pyproject.toml")
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    print(extract_release_notes(changelog, version))


if __name__ == "__main__":
    main()
