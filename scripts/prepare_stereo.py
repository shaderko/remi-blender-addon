"""Download the pinned, MIT-licensed DLNR baseline into an ignored model folder.

No packages are installed and no upstream code is executed by this script.
The official Middlebury checkpoint is about 230 MB. Its SHA-256 is recorded on
first download (upstream does not publish an independent SHA-256 checksum).
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "David-Zhao-1997/High-frequency-Stereo-Matching-Network"
COMMIT = "c10475fe30ff198781ecc7c712c24c781ff05096"
FILES = (
    "LICENSE", "README.md", "core/__init__.py", "core/dlnr.py",
    "core/corr.py", "core/extractor.py", "core/update.py", "core/mogrifier.py",
    "core/utils/__init__.py", "core/utils/utils.py", "nets/feature.py",
    "nets/refinement.py", "nets/warp.py",
)
CHECKPOINT = "DLNR_Middlebury.pth"
CHECKPOINT_SIZE = 229811941


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "provenance.json"
    previous = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    if previous and previous.get("commit") != COMMIT:
        raise ValueError("Existing model bundle has a different commit; use a new folder")
    records = previous.get("files", {})
    downloads = [("source/" + name,
                  f"https://raw.githubusercontent.com/{REPOSITORY}/{COMMIT}/{name}", None)
                 for name in FILES]
    downloads.append((CHECKPOINT,
                      f"https://github.com/{REPOSITORY}/releases/download/v1.0.0/{CHECKPOINT}",
                      CHECKPOINT_SIZE))
    for relative, url, size in downloads:
        target = output / relative
        if target.exists():
            record = records.get(relative)
            if not record or sha256(target) != record["sha256"]:
                raise ValueError(f"Untracked or modified file; refusing to overwrite: {target}")
            print(f"Verified {relative}", flush=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".download")
        if temporary.exists():
            raise ValueError(f"Partial download exists; inspect/remove it explicitly: {temporary}")
        print(f"Downloading {relative}", flush=True)
        request = urllib.request.Request(url, headers={"User-Agent": "Remi-stereo-research"})
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open("xb") as stream:
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                stream.write(chunk)
        if size is not None and temporary.stat().st_size != size:
            raise ValueError(f"Unexpected download size for {relative}")
        digest = sha256(temporary)
        temporary.rename(target)
        records[relative] = {"url": url, "sha256": digest, "bytes": target.stat().st_size}
        manifest_path.write_text(json.dumps({
            "schema": 1, "repository": REPOSITORY, "commit": COMMIT,
            "source_license": "MIT; see source/LICENSE",
            "checkpoint_checksum": "SHA-256 recorded locally; no independent upstream checksum",
            "files": records,
        }, indent=2), encoding="utf-8")
    print(f"Prepared DLNR: {output}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "models" / "stereo" / "dlnr")
    prepare(parser.parse_args().output)
