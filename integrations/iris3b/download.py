"""Download pinned Iris-3B and Qwen3-VL weights with Hugging Face Hub."""

import argparse
import json
from pathlib import Path
import threading
import time
import traceback

try:
    from .constants import (
        MODEL_BYTES,
        MODEL_FILES,
        MODEL_REPO,
        MODEL_REVISION,
        TEXT_ENCODER_BYTES,
        TEXT_ENCODER_FILES,
        TEXT_ENCODER_REPO,
        TEXT_ENCODER_REVISION,
    )
except ImportError:  # one-shot execution via `python download.py`
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from constants import (
        MODEL_BYTES,
        MODEL_FILES,
        MODEL_REPO,
        MODEL_REVISION,
        TEXT_ENCODER_BYTES,
        TEXT_ENCODER_FILES,
        TEXT_ENCODER_REPO,
        TEXT_ENCODER_REVISION,
    )


def status(output, phase, **extra):
    print(phase, flush=True)
    output = Path(output)
    temporary = output / "status.partial.json"
    temporary.write_text(json.dumps({"phase": phase, **extra}), encoding="utf-8")
    for attempt in range(20):
        try:
            temporary.replace(output / "status.json")
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.05)


def _size(root, variant, text_encoder):
    total = 0
    repo = Path(root) / "iris-3b"
    names = MODEL_FILES[variant]
    if variant == "base":
        targets = [repo / name for name in names]
    else:
        targets = [repo / "turbo" / Path(name).name for name in names]
    for target in targets:
        try:
            for path in target.parent.glob(target.name + "*"):
                if path.is_file() and not path.name.endswith(".partial"):
                    total += path.stat().st_size
        except OSError:
            pass
    if text_encoder:
        encoder = Path(root) / "Qwen3-VL-4B-Instruct"
        try:
            for path in encoder.rglob("*"):
                if path.is_file() and not path.name.endswith(".partial"):
                    total += path.stat().st_size
        except OSError:
            pass
    return total


def _expected(variant, text_encoder):
    return MODEL_BYTES[variant] + (TEXT_ENCODER_BYTES if text_encoder else 0)


def download(models, variant, text_encoder, output):
    models = Path(models)
    models.mkdir(parents=True, exist_ok=True)
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise RuntimeError(
            "The selected Python is missing huggingface_hub; install requirements-iris.txt"
        ) from exc

    stop = threading.Event()
    expected = _expected(variant, text_encoder)

    def report():
        while not stop.wait(1.0):
            done = _size(models, variant, text_encoder)
            percent = min(100, int(done * 100 / max(expected, 1)))
            status(output, f"Downloading models · {percent}%", percent=percent)

    monitor = threading.Thread(target=report, daemon=True)
    monitor.start()
    try:
        if variant == "base":
            status(output, "Downloading Iris-3B base (about 12 GB)…")
            snapshot_download(
                repo_id=MODEL_REPO,
                revision=MODEL_REVISION,
                local_dir=models / "iris-3b",
                allow_patterns=list(MODEL_FILES["base"]),
            )
        else:
            status(output, "Downloading Iris-3B Turbo (about 6 GB)…")
            snapshot_download(
                repo_id=MODEL_REPO,
                revision=MODEL_REVISION,
                local_dir=models / "iris-3b",
                allow_patterns=list(MODEL_FILES["turbo"]),
            )
        if text_encoder:
            status(output, "Downloading Qwen3-VL text encoder (about 9 GB)…")
            snapshot_download(
                repo_id=TEXT_ENCODER_REPO,
                revision=TEXT_ENCODER_REVISION,
                local_dir=models / "Qwen3-VL-4B-Instruct",
                allow_patterns=list(TEXT_ENCODER_FILES),
            )
    finally:
        stop.set()
        monitor.join(timeout=2)

    required = [models / "iris-3b" / name for name in MODEL_FILES[variant]]
    if text_encoder:
        required += [models / "Qwen3-VL-4B-Instruct" / name for name in TEXT_ENCODER_FILES]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError("Download incomplete: " + ", ".join(missing[:3]))
    status(output, "Models downloaded and verified", complete=True)
    (Path(output) / "complete.json").write_text(
        json.dumps({"schema": 1, "complete": True}), encoding="utf-8"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--variant", choices=("base", "turbo"), required=True)
    parser.add_argument("--text-encoder", action="store_true")
    parser.add_argument("--status-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        download(args.models, args.variant, args.text_encoder, args.status_dir)
    except Exception as exc:
        traceback.print_exc()
        status(args.status_dir, "Download failed", error=str(exc))
        raise


if __name__ == "__main__":
    main()
