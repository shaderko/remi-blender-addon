"""Filesystem-backed image gallery and Blender thumbnail previews."""

from dataclasses import dataclass
import json
from pathlib import Path

import bpy
import bpy.utils.previews


@dataclass(frozen=True)
class GalleryEntry:
    path: Path
    job: Path
    prompt: str
    negative_prompt: str
    model: str
    model_label: str
    seed: int
    width: int
    height: int
    created: float
    index: int
    count: int

    @property
    def label(self):
        text = " ".join(self.prompt.split())
        return text if len(text) <= 48 else text[:45].rstrip() + "…"


class Gallery:
    def __init__(self):
        self._previews = None
        self._icons = {}
        self._entries = []
        self._key = None

    def _collection(self):
        if self._previews is None:
            self._previews = bpy.utils.previews.new()
        return self._previews

    def icon(self, path):
        path = Path(path)
        key = str(path.resolve())
        if key in self._icons:
            return self._icons[key]
        try:
            preview = self._collection().load(key, str(path), "IMAGE")
            icon = preview.icon_id
        except (KeyError, RuntimeError, OSError, ValueError):
            icon = 0
        self._icons[key] = icon
        return icon

    def refresh(self, output, token=0, force=False):
        output = Path(bpy.path.abspath(output))
        key = (str(output), int(token))
        if not force and key == self._key:
            return self._entries
        entries = []
        if output.is_dir():
            for job in output.iterdir():
                if not job.is_dir() or not (job / "complete.json").is_file():
                    continue
                try:
                    meta = json.loads((job / "meta.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                prompt = str(meta.get("prompt", ""))
                images = meta.get("images") or []
                files = []
                for item in images:
                    name = item.get("file") if isinstance(item, dict) else item
                    if name:
                        files.append((job / name, int(item.get("seed", meta.get("seed", 0))) if isinstance(item, dict) else int(meta.get("seed", 0))))
                if not files:
                    files = [(path, int(meta.get("seed", 0))) for path in sorted(job.glob("image_*.png"))]
                created = (job / "meta.json").stat().st_mtime
                for index, (path, seed) in enumerate(files):
                    if not path.is_file():
                        continue
                    entries.append(
                        GalleryEntry(
                            path=path,
                            job=job,
                            prompt=prompt,
                            negative_prompt=str(meta.get("negative_prompt", "")),
                            model=str(meta.get("model", "")),
                            model_label=str(meta.get("model_label", meta.get("model", ""))),
                            seed=seed,
                            width=int(meta.get("width", 0) or 0),
                            height=int(meta.get("height", 0) or 0),
                            created=created,
                            index=index,
                            count=len(files),
                        )
                    )
        entries.sort(key=lambda entry: (entry.created, entry.index), reverse=True)
        self._entries = entries
        self._key = key
        return entries

    def invalidate(self):
        self._key = None

    def remove(self, entry):
        """Remove one generated image and its metadata; keep the rest of the batch."""
        try:
            entry.path.unlink(missing_ok=True)
        except OSError:
            return False
        meta_path = entry.job / "meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        images = meta.get("images")
        if isinstance(images, list):
            meta["images"] = [
                item for item in images
                if (item.get("file") if isinstance(item, dict) else item) != entry.path.name
            ]
            try:
                meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
            except OSError:
                pass
        if not any(entry.job.glob("image_*")):
            for path in entry.job.iterdir():
                try:
                    path.unlink()
                except OSError:
                    pass
            try:
                entry.job.rmdir()
            except OSError:
                pass
        self.invalidate()
        return True

    def close(self):
        if self._previews is not None:
            bpy.utils.previews.remove(self._previews)
            self._previews = None
        self._icons.clear()
        self._entries = []
        self._key = None


gallery = Gallery()
