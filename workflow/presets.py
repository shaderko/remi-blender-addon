"""Data-only flow presets with bounded parsing and atomic user-file writes."""

import json
import math
import os
from pathlib import Path
import re
import uuid


class PresetStore:
    def __init__(self, directory):
        self.directory = Path(directory)

    def _path(self, identifier):
        if not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise ValueError("Invalid preset identifier")
        return self.directory / (identifier + ".json")

    def load(self, identifier):
        path = self._path(identifier)
        if path.stat().st_size > 128 * 1024:
            raise ValueError("Preset file is too large")
        value = json.loads(path.read_text(encoding="utf-8"))
        validate_document(value)
        return value

    def entries(self):
        entries = []
        if self.directory.exists():
            for path in sorted(self.directory.glob("*.json")):
                try:
                    value = self.load(path.stem)
                    entries.append((path.stem, value["name"]))
                except (ValueError, OSError, KeyError, TypeError):
                    continue
        return sorted(entries, key=lambda item: (item[1].casefold(), item[0]))

    def save(self, value, identifier=None):
        validate_document(value)
        identifier = identifier or uuid.uuid4().hex
        path = self._path(identifier)
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.directory / ("." + uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return identifier

    def delete(self, identifier):
        self._path(identifier).unlink()


def validate_document(value):
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("Unsupported flow preset version")
    if not isinstance(value.get("name"), str) or not 1 <= len(value["name"].strip()) <= 80:
        raise ValueError("Preset name must contain 1–80 characters")
    actions = value.get("actions")
    if not isinstance(actions, list) or not 1 <= len(actions) <= 12:
        raise ValueError("Choose between 1 and 12 flow stages")
    if any(not isinstance(action, str) for action in actions) or len(set(actions)) != len(actions):
        raise ValueError("Flow stages must be unique action identifiers")
    settings = value.get("settings")
    if not isinstance(settings, dict) or len(settings) > 200:
        raise ValueError("Invalid flow settings")
    for key, setting in settings.items():
        if not isinstance(key, str) or not isinstance(setting, (str, bool, int, float)):
            raise ValueError("Flow settings must be scalar values")
        if isinstance(setting, float) and not math.isfinite(setting):
            raise ValueError("Flow settings must be finite")


def capture_settings(settings, *, defaults=False):
    result = {}
    for prop in settings.bl_rna.properties:
        if prop.identifier == "rna_type" or prop.is_readonly or prop.type not in {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}:
            continue
        result[prop.identifier] = prop.default if defaults else getattr(settings, prop.identifier)
    return result


def checked_settings(settings, values):
    """Validate all fields before assigning any; Blender otherwise silently clamps."""
    result = capture_settings(settings, defaults=True)
    for name, value in values.items():
        prop = settings.bl_rna.properties.get(name)
        if name not in result or prop is None:
            raise ValueError(f"Unknown preset setting: {name}")
        kind = prop.type
        if kind == "BOOLEAN":
            valid = type(value) is bool
        elif kind in {"INT", "FLOAT"}:
            valid = type(value) in ({int} if kind == "INT" else {float, int}) and math.isfinite(value) and prop.hard_min <= value <= prop.hard_max
        elif kind == "ENUM":
            valid = isinstance(value, str) and value in {item.identifier for item in prop.enum_items}
        else:
            valid = isinstance(value, str) and len(value) <= 4096
        if not valid:
            raise ValueError(f"Invalid preset value for {prop.name}")
        result[name] = value
    return result


def apply_settings(settings, values):
    checked = checked_settings(settings, values)
    for key, value in checked.items():
        setattr(settings, key, value)


def validate_actions(features, actions):
    if not actions or len(actions) > 12 or len(set(actions)) != len(actions):
        raise ValueError("Choose 1–12 distinct automatic stages")
    for identifier in actions:
        registered = features.action(identifier)
        if registered is None or not registered.action.automatic or registered.action.mode.value != "ATOMIC":
            raise ValueError(f"{identifier} cannot run automatically")
