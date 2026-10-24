#!/usr/bin/env python3
"""Snapshot the read-only research inference sources and weights into Remi."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("model.py", "triposplat.py", "flow_transplant/faithful_model.py", "LICENSE")
WEIGHTS = (
    ("runs/rand6_100_ncm/best_weights.pt", "best_weights.pt"),
    ("ckpts/clip_vision/dino_v3_vit_h.safetensors", "ckpts/clip_vision/dino_v3_vit_h.safetensors"),
    ("ckpts/vae/flux2-vae.safetensors", "ckpts/vae/flux2-vae.safetensors"),
    ("ckpts/vae/triposplat_vae_decoder_fp16.safetensors", "ckpts/vae/triposplat_vae_decoder_fp16.safetensors"),
    ("ckpts/background_removal/birefnet.safetensors", "ckpts/background_removal/birefnet.safetensors"),
)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def copy_verified(source, destination):
    checksum = digest(source)
    if destination.exists():
        if digest(destination) != checksum:
            raise RuntimeError(f"Refusing to overwrite different file: {destination}")
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".partial")
        if temporary.exists():
            raise RuntimeError(f"Remove or inspect incomplete copy first: {temporary}")
        shutil.copyfile(source, temporary)
        if digest(temporary) != checksum:
            raise RuntimeError(f"Copy verification failed: {temporary}")
        temporary.replace(destination)
    return {"sha256": checksum, "bytes": source.stat().st_size}


def prepare(source, output):
    source, output = source.resolve(), output.resolve()
    vendor = ROOT / "integrations" / "triposplat" / "vendor"
    if output.is_relative_to(source) or vendor.is_relative_to(source):
        raise ValueError("Destinations must be outside the read-only source tree")
    for name in SOURCES + tuple(name for name, _ in WEIGHTS):
        if not (source / name).is_file():
            raise FileNotFoundError(source / name)
    sources = {}
    for name in SOURCES:
        print(f"Snapshot source: {name}", flush=True)
        sources[name] = copy_verified(source / name, vendor / name)
    (vendor / "provenance.json").write_text(json.dumps({
        "upstream": "https://github.com/VAST-AI-Research/TripoSplat",
        "fork": "local NCM flow-transplant, rand6_100_ncm (step 600)",
        "files": sources,
    }, indent=2) + "\n", encoding="utf-8")
    weights = {}
    for original, name in WEIGHTS:
        print(f"Snapshot weights: {name}", flush=True)
        weights[name] = copy_verified(source / original, output / name)
    (output / "bundle.json").write_text(json.dumps({
        "schema": 1, "model": "rand6_100_ncm", "files": weights,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"Ready. Blender Model Folder: {output}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Read-only TripoSplat repository")
    parser.add_argument("--output", type=Path, default=ROOT / "models" / "triposplat")
    args = parser.parse_args()
    prepare(args.source, args.output)
