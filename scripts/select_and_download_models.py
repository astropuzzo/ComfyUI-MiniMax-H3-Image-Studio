#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

SCRIPT = Path(__file__).with_name("download_models.py")


def main():
    root = tk.Tk()
    root.title("MiniMax H3 Model Selector")
    root.geometry("650x420")
    root.resizable(False, False)

    fl = tk.StringVar(value="pruned_int8_convrot")
    ref = tk.StringVar(value="none")
    te = tk.StringVar(value="nvfp4_awq")
    video = tk.BooleanVar(value=True)
    audio = tk.BooleanVar(value=False)
    force = tk.BooleanVar(value=False)

    choices = {
        "FL2VA (T2I / I2I)": (fl, ["none", "pruned_int8_convrot", "int8_convrot", "bf16"]),
        "REF2VA (reference edit)": (ref, ["none", "pruned_int8_convrot", "int8_convrot", "bf16"]),
        "Text encoder": (te, ["none", "nvfp4_awq", "int8_convrot", "bf16"]),
    }

    ttk.Label(root, text="Choose the official Comfy-Org MiniMax H3 files to download", font=("Segoe UI", 14, "bold")).pack(pady=(18, 12))
    form = ttk.Frame(root, padding=16)
    form.pack(fill="x")
    for row, (label, (var, values)) in enumerate(choices.items()):
        ttk.Label(form, text=label, width=25).grid(row=row, column=0, sticky="w", pady=8)
        ttk.Combobox(form, textvariable=var, values=values, state="readonly", width=32).grid(row=row, column=1, sticky="w", pady=8)

    ttk.Checkbutton(form, text="Download video VAE (required for images)", variable=video).grid(row=3, column=0, columnspan=2, sticky="w", pady=6)
    ttk.Checkbutton(form, text="Download audio VAE (not needed for image mode)", variable=audio).grid(row=4, column=0, columnspan=2, sticky="w", pady=6)
    ttk.Checkbutton(form, text="Force redownload existing files", variable=force).grid(row=5, column=0, columnspan=2, sticky="w", pady=6)

    note = (
        "Recommended for 24 GB VRAM: FL2VA pruned_int8_convrot + NVFP4 AWQ + video VAE.\n"
        "Add REF2VA only when you need reference editing. BF16 files are much larger."
    )
    ttk.Label(root, text=note, justify="left").pack(padx=24, pady=10, anchor="w")

    def start():
        args = [sys.executable, str(SCRIPT), "--fl2va", fl.get(), "--ref2va", ref.get(), "--text-encoder", te.get()]
        if not video.get():
            args.append("--no-video-vae")
        if audio.get():
            args.append("--audio-vae")
        if force.get():
            args.append("--force")
        root.destroy()
        raise SystemExit(subprocess.call(args))

    ttk.Button(root, text="Download selected models", command=start).pack(pady=12)
    root.mainloop()


if __name__ == "__main__":
    main()
