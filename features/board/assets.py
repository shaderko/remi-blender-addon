"""File-backed board assets and automatic lineage, independent of bpy objects."""

from dataclasses import asdict, dataclass, field
from copy import deepcopy
import math
from pathlib import Path
import uuid


KINDS = {"IMAGE", "SPLAT", "MESH"}
STATES = {"QUEUED", "RUNNING", "READY", "ERROR", "CANCELLED", "INTERRUPTED"}
OBJECT_ASSET_KEY = "remi_board_asset_id"
OBJECT_REVISION_KEY = "remi_board_asset_artifact"
SCENE_ASSETS_KEY = "remi_board_assets_v1"


@dataclass
class Asset:
    id: str
    kind: str
    artifact: str = ""
    parent: str = ""
    title: str = ""
    operation: str = ""
    state: str = "READY"
    status: str = ""
    progress: float = 0.0
    preview: str = ""
    preview_view: list = field(default_factory=list)
    directory: str = ""
    settings: dict = field(default_factory=dict)
    yaw: float = 0.0
    elevation: float = 20.0
    hidden: bool = False
    links: dict = field(default_factory=dict)
    revisions: list = field(default_factory=list)

    def document(self):
        return asdict(self)

    @classmethod
    def parse(cls, value):
        if not isinstance(value, dict) or value.get("kind") not in KINDS:
            raise ValueError("Invalid board asset")
        if not isinstance(value.get("id"), str) or not 1 <= len(value["id"]) <= 4096:
            raise ValueError("Invalid board asset identity")
        fields = {name: value[name] for name in cls.__dataclass_fields__ if name in value}
        asset = cls(**fields)
        for name in ("artifact", "parent", "title", "operation", "state", "status", "preview", "directory"):
            if not isinstance(getattr(asset, name), str):
                raise ValueError(f"Invalid asset {name}")
        if (asset.state not in STATES or not isinstance(asset.settings, dict)
                or not isinstance(asset.links, dict)):
            raise ValueError("Invalid asset state/settings")
        if not isinstance(asset.hidden, bool):
            raise ValueError("Invalid asset visibility")
        if (not isinstance(asset.revisions, list) or any(not isinstance(item, dict)
                or any(not isinstance(item.get(key, ""), str) for key in ("artifact", "directory", "preview"))
                for item in asset.revisions)):
            raise ValueError("Invalid asset revisions")
        if (not isinstance(asset.preview_view, list) or len(asset.preview_view) not in {0, 2}
                or any(not isinstance(value, (int, float)) or not math.isfinite(value)
                       for value in asset.preview_view)):
            raise ValueError("Invalid preview view")
        for name in ("progress", "yaw", "elevation"):
            number = getattr(asset, name)
            if not isinstance(number, (int, float)) or not math.isfinite(number):
                raise ValueError(f"Invalid asset {name}")
        # Never replay a GPU job just because someone reopened a .blend file.
        if asset.state in {"QUEUED", "RUNNING"}:
            asset.state = "INTERRUPTED"
            asset.status = "Job interrupted; source and any completed files are kept"
        return asset

    def files(self):
        """Current and retained revision files, for shared-file-safe deletion."""
        return ({self.artifact, self.preview} | {value for item in self.revisions
                for value in (item.get("artifact", ""), item.get("preview", ""))}) - {""}


class Catalog:
    def __init__(self, documents=()):
        self.assets = {}
        for document in documents:
            try:
                asset = Asset.parse(document)
                self.assets[asset.id] = asset
            except (ValueError, TypeError, KeyError):
                continue

    def image(self, path, title=""):
        path = str(Path(path).resolve())
        existing = next((asset for asset in self.assets.values()
                         if asset.kind == "IMAGE" and asset.artifact == path), None)
        if existing is not None:
            existing.title = title or existing.title
            return existing
        identifier = "image:" + uuid.uuid5(uuid.NAMESPACE_URL, Path(path).as_uri()).hex
        asset = Asset(identifier, "IMAGE", artifact=path, title=title)
        self.assets[identifier] = asset
        return asset

    def derive(self, parent, kind, operation, settings=None, title=""):
        if parent not in self.assets or kind not in KINDS:
            raise ValueError("Choose a valid source asset")
        asset = Asset("asset:" + uuid.uuid4().hex, kind, parent=parent,
                      operation=operation, settings=deepcopy(settings or {}),
                      title=title, state="QUEUED", status="Queued")
        self.assets[asset.id] = asset
        return asset

    def documents(self):
        return [asset.document() for asset in self.assets.values()]

    def ancestors(self, identifier):
        seen = set()
        while identifier in self.assets and identifier not in seen:
            seen.add(identifier)
            asset = self.assets[identifier]
            yield asset
            identifier = asset.parent
