#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import random
import re
import time
from pathlib import Path
from urllib.request import urlopen

REPO = "Comfy-Org/MiniMax-H3"
BASE = f"https://huggingface.co/{REPO}/resolve/main"

FL2VA_MODELS = {
    "none": None,
    "pruned_int8_convrot": ("diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors", "diffusion_models"),
    "int8_convrot": ("diffusion_models/minimax_h3_fl2va_int8_convrot.safetensors", "diffusion_models"),
    "bf16": ("diffusion_models/minimax_h3_fl2va_bf16.safetensors", "diffusion_models"),
}

REF2VA_MODELS = {
    "none": None,
    "pruned_int8_convrot": ("diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors", "diffusion_models"),
    "int8_convrot": ("diffusion_models/minimax_h3_ref2va_int8_convrot.safetensors", "diffusion_models"),
    "bf16": ("diffusion_models/minimax_h3_ref2va_bf16.safetensors", "diffusion_models"),
}

TEXT_ENCODERS = {
    "none": None,
    "nvfp4_awq": ("text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", "text_encoders"),
    "int8_convrot": ("text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors", "text_encoders"),
    "bf16": ("text_encoders/qwen3vl_32b_minimax_h3_bf16.safetensors", "text_encoders"),
}

VIDEO_VAE = ("vae/minimax_h3_video_vae_fp16.safetensors", "vae")
AUDIO_VAE = ("vae/minimax_h3_audio_vae_fp32.safetensors", "vae")

# Backward-compatible defaults used by the batch scripts.
CORE_FILES = [
    FL2VA_MODELS["pruned_int8_convrot"],
    TEXT_ENCODERS["nvfp4_awq"],
    VIDEO_VAE,
]
REF_FILE = REF2VA_MODELS["pruned_int8_convrot"]

_CONTENT_RANGE_RE = re.compile(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", re.IGNORECASE)
_UNSATISFIED_RANGE_RE = re.compile(r"bytes\s+\*/(\d+)", re.IGNORECASE)


def find_comfy_root(explicit: str | None) -> Path:
    if explicit:
        root = Path(explicit).expanduser().resolve()
    else:
        # script lives custom_nodes/<package>/scripts/download_models.py
        root = Path(__file__).resolve().parents[3]
    if not (root / "models").exists():
        raise SystemExit(f"Could not find ComfyUI models folder under: {root}")
    return root


def _parse_total_size(headers, resume_at: int) -> int | None:
    content_range = headers.get("content-range")
    if content_range:
        match = _CONTENT_RANGE_RE.fullmatch(content_range.strip())
        if match and match.group(3) != "*":
            return int(match.group(3))
    content_length = headers.get("content-length")
    if content_length and content_length.isdigit():
        return int(content_length) + resume_at
    return None


def _print_progress(done: int, total: int | None) -> None:
    if total and total > 0:
        percent = min(100.0, done * 100.0 / total)
        print(
            f"\r  {done / 1e9:.2f}/{total / 1e9:.2f} GB ({percent:.1f}%)",
            end="",
            flush=True,
        )
    else:
        print(f"\r  {done / 1e9:.2f} GB", end="", flush=True)


def download_stream(
    url: str,
    destination: Path,
    *,
    max_retries: int = 50,
    chunk_size: int = 8 * 1024 * 1024,
) -> None:
    """Download a large file with persistent .part resume and automatic retries.

    A transient disconnect never deletes the partial file. Each retry sends a new
    HTTP Range request starting from the exact current .part size.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")

    try:
        import requests
        from requests import exceptions as request_exceptions
    except ImportError:
        requests = None
        request_exceptions = None

    if requests is None:
        # Minimal fallback for environments without requests. It cannot resume
        # reliably, so the normal ComfyUI path should use requests instead.
        partial.unlink(missing_ok=True)
        with urlopen(url, timeout=120) as response, partial.open("wb") as handle:
            total_header = response.headers.get("content-length", "")
            total = int(total_header) if total_header.isdigit() else None
            done = 0
            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                handle.write(chunk)
                done += len(chunk)
                _print_progress(done, total)
        print()
        if total is not None and partial.stat().st_size != total:
            raise RuntimeError(
                f"Incomplete download without requests: {partial.stat().st_size} of {total} bytes"
            )
        partial.replace(destination)
        return

    session = requests.Session()
    session.headers.update({"Accept-Encoding": "identity"})

    attempt = 0
    expected_total: int | None = None

    while True:
        resume_at = partial.stat().st_size if partial.exists() else 0
        headers = {"Range": f"bytes={resume_at}-"} if resume_at else {}

        try:
            with session.get(
                url,
                headers=headers,
                stream=True,
                timeout=(30, 180),
                allow_redirects=True,
            ) as response:
                # A completed .part can produce 416 when retried after a disconnect
                # that happened after the last byte was written.
                if response.status_code == 416:
                    value = response.headers.get("content-range", "")
                    match = _UNSATISFIED_RANGE_RE.fullmatch(value.strip())
                    server_total = int(match.group(1)) if match else None
                    if server_total is not None and resume_at == server_total:
                        expected_total = server_total
                        break
                    raise RuntimeError(
                        f"Server rejected resume range at {resume_at} bytes; Content-Range={value!r}"
                    )

                if response.status_code not in (200, 206):
                    response.raise_for_status()

                if resume_at and response.status_code == 200:
                    # The endpoint ignored Range. Restart safely instead of
                    # appending the full file to an existing partial file.
                    print(
                        "\n  Server ignored resume request; restarting this file from byte 0.",
                        flush=True,
                    )
                    partial.unlink(missing_ok=True)
                    resume_at = 0

                if response.status_code == 206:
                    content_range = response.headers.get("content-range", "")
                    match = _CONTENT_RANGE_RE.fullmatch(content_range.strip())
                    if not match:
                        raise RuntimeError(
                            f"Resume response has invalid Content-Range: {content_range!r}"
                        )
                    returned_start = int(match.group(1))
                    if returned_start != resume_at:
                        raise RuntimeError(
                            f"Resume mismatch: requested byte {resume_at}, server started at {returned_start}"
                        )

                expected_total = _parse_total_size(response.headers, resume_at)
                mode = "ab" if resume_at else "wb"
                done = resume_at

                with partial.open(mode) as handle:
                    for chunk in response.iter_content(chunk_size=chunk_size):
                        if not chunk:
                            continue
                        handle.write(chunk)
                        done += len(chunk)
                        _print_progress(done, expected_total)
                    handle.flush()
                    os.fsync(handle.fileno())

            actual = partial.stat().st_size
            if expected_total is None:
                # The HTTP stream ended normally. Without a size header, accept it.
                break
            if actual == expected_total:
                break
            if actual > expected_total:
                raise RuntimeError(
                    f"Partial file is larger than expected: {actual} > {expected_total} bytes"
                )
            raise request_exceptions.ChunkedEncodingError(
                f"Connection ended early: {actual} of {expected_total} bytes"
            )

        except KeyboardInterrupt:
            print("\n  Download interrupted by user. Partial file kept for resume.")
            raise
        except Exception as exc:
            attempt += 1
            current = partial.stat().st_size if partial.exists() else 0
            if attempt > max_retries:
                print()
                raise RuntimeError(
                    f"Download failed after {max_retries} automatic retries. "
                    f"Partial file kept at {current} bytes: {partial}"
                ) from exc

            delay = min(60.0, 2.0 ** min(attempt - 1, 5)) + random.uniform(0.0, 1.0)
            total_text = f"/{expected_total / 1e9:.2f} GB" if expected_total else ""
            print(
                f"\n  Connection interrupted ({type(exc).__name__}: {exc}). "
                f"Keeping {current / 1e9:.2f}{total_text} and retrying in {delay:.1f}s "
                f"[{attempt}/{max_retries}]...",
                flush=True,
            )
            time.sleep(delay)

    final_size = partial.stat().st_size
    if expected_total is not None and final_size != expected_total:
        raise RuntimeError(
            f"Final size verification failed: {final_size} != {expected_total} bytes"
        )
    print()
    partial.replace(destination)
    print(f"  VERIFIED: {destination.name} ({final_size / 1e9:.2f} GB)")


def selection_files(
    fl2va: str,
    ref2va: str,
    text_encoder: str,
    video_vae: bool,
    audio_vae: bool,
) -> list[tuple[str, str]]:
    files: list[tuple[str, str]] = []
    for selected, manifest in (
        (fl2va, FL2VA_MODELS),
        (ref2va, REF2VA_MODELS),
        (text_encoder, TEXT_ENCODERS),
    ):
        item = manifest[selected]
        if item is not None:
            files.append(item)
    if video_vae:
        files.append(VIDEO_VAE)
    if audio_vae:
        files.append(AUDIO_VAE)
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description="Download selected official Comfy-Org MiniMax H3 model variants.")
    parser.add_argument("--comfy-path", help="Path to the ComfyUI folder. Usually auto-detected.")
    parser.add_argument("--fl2va", choices=FL2VA_MODELS, default="pruned_int8_convrot")
    parser.add_argument("--ref2va", choices=REF2VA_MODELS, default="none")
    parser.add_argument("--text-encoder", choices=TEXT_ENCODERS, default="nvfp4_awq")
    parser.add_argument("--no-video-vae", action="store_true")
    parser.add_argument("--audio-vae", action="store_true")
    parser.add_argument("--force", action="store_true", help="Redownload files that already exist.")
    args = parser.parse_args()

    root = find_comfy_root(args.comfy_path)
    files = selection_files(
        args.fl2va,
        args.ref2va,
        args.text_encoder,
        not args.no_video_vae,
        args.audio_vae,
    )
    if not files:
        print("No model selected.")
        return 0

    print(f"ComfyUI root: {root}")
    for remote_path, local_folder in files:
        destination = root / "models" / local_folder / Path(remote_path).name
        if destination.exists() and not args.force:
            print(f"SKIP {destination.name} (already exists)")
            continue
        print(f"DOWNLOAD {destination.name}")
        download_stream(f"{BASE}/{remote_path}?download=true", destination)

    print("Done. Refresh model lists or restart ComfyUI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
