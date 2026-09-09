"""Lifecycle-owned disk storage for one Remi session."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import bpy


SESSION_ID_KEY = "_remi_session_id"
CHECKPOINT_MATERIALS_KEY = "_remi_checkpoint_materials"
CHECKPOINT_TARGET_KEY = "_remi_checkpoint_target"


class SessionDiskService:
    """Own every temporary file created for one Remi session."""

    SLOTS = ("source", "previous", "pending", "redo")
    DIRECTORY_PREFIX = "remi-session-"
    OWNER_FILENAME = "owner.json"
    OWNER_VERSION = 1
    STARTUP_GRACE_SECONDS = 300.0

    def __init__(self, directory: Path, session_id: str, root: Path):
        self._directory = directory
        self._session_id = session_id
        self._root = root
        self._closed = False
        self._workspace_serial = 0

    @property
    def directory(self) -> Path:
        """Expose the owned directory for diagnostics, never for session logic."""
        return self._directory

    @classmethod
    def create(cls, session_id: str, *, root=None):
        """Create an owned session directory and persist its process identity."""
        root_path = Path(root or tempfile.gettempdir()).resolve()
        root_path.mkdir(parents=True, exist_ok=True)
        directory = Path(
            tempfile.mkdtemp(
                prefix=f"{cls.DIRECTORY_PREFIX}{session_id[:8]}-",
                dir=str(root_path),
            )
        ).resolve()
        service = cls(directory, session_id, root_path)
        try:
            service._write_owner()
        except Exception:
            service.close()
            raise
        return service

    @staticmethod
    def _pid_is_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    @classmethod
    def _read_owner(cls, directory: Path):
        try:
            owner = json.loads(
                (directory / cls.OWNER_FILENAME).read_text(encoding="utf-8")
            )
        except (OSError, TypeError, ValueError):
            return None
        if not isinstance(owner, dict) or owner.get("version") != cls.OWNER_VERSION:
            return None
        try:
            pid = int(owner["pid"])
        except (KeyError, TypeError, ValueError):
            return None
        if pid <= 0:
            return None
        owner["pid"] = pid
        return owner

    @classmethod
    def _safe_remove_directory(cls, root: Path, directory: Path):
        """Delete only a direct child carrying Remi's private directory prefix."""
        root = root.resolve()
        directory = directory.resolve()
        if directory.parent != root or not directory.name.startswith(cls.DIRECTORY_PREFIX):
            raise RuntimeError(f"Refusing to remove non-Remi directory: {directory}")
        shutil.rmtree(directory)

    @classmethod
    def cleanup_abandoned(
        cls,
        *,
        root=None,
        preserve=(),
        grace_seconds=None,
        current_pid=None,
        now=None,
    ):
        """Remove sessions whose Blender process died, while preserving live owners.

        Ownerless or corrupt directories receive a grace period so another Blender
        process cannot lose a directory in the small window before its owner marker
        is written. Directories owned by this process are leftovers from an add-on
        reload and are safe to remove unless explicitly preserved.
        """
        root_path = Path(root or tempfile.gettempdir()).resolve()
        if not root_path.is_dir():
            return []
        grace = (
            cls.STARTUP_GRACE_SECONDS
            if grace_seconds is None
            else max(0.0, float(grace_seconds))
        )
        process_id = os.getpid() if current_pid is None else int(current_pid)
        current_time = time.time() if now is None else float(now)
        preserved = {Path(path).resolve() for path in preserve}
        removed = []

        try:
            candidates = list(root_path.iterdir())
        except OSError:
            return removed

        for candidate in candidates:
            if not candidate.name.startswith(cls.DIRECTORY_PREFIX):
                continue
            try:
                resolved = candidate.resolve()
                if resolved in preserved or not candidate.is_dir():
                    continue
                if resolved.parent != root_path:
                    continue
                owner = cls._read_owner(resolved)
                if owner is not None:
                    owner_pid = owner["pid"]
                    abandoned = owner_pid == process_id or not cls._pid_is_alive(owner_pid)
                else:
                    age = max(0.0, current_time - resolved.stat().st_mtime)
                    abandoned = age >= grace
                if not abandoned:
                    continue
                cls._safe_remove_directory(root_path, resolved)
                removed.append(str(resolved))
            except OSError:
                # A later Blender start can retry folders that are locked or racing.
                continue
        return removed

    def _ensure_open(self):
        if self._closed or not self._directory.is_dir():
            raise RuntimeError("The Remi session disk service is closed")

    def _write_owner(self):
        self._ensure_open()
        destination = self._directory / self.OWNER_FILENAME
        temporary = self._directory / f"{self.OWNER_FILENAME}.writing"
        payload = {
            "version": self.OWNER_VERSION,
            "pid": os.getpid(),
            "session_id": self._session_id,
            "created_at": time.time(),
        }
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            try:
                temporary.unlink()
            except OSError:
                pass

    def path(self, slot: str) -> str:
        """Return a slot path for diagnostics and disk-service tests."""
        self._ensure_open()
        if slot not in self.SLOTS:
            raise ValueError(f"Unknown Remi checkpoint slot: {slot}")
        return str(self._directory / f"{slot}.blend")

    def create_workspace(self, label: str) -> Path:
        """Allocate an operation directory owned by this service's lifecycle."""
        self._ensure_open()
        safe_label = "".join(
            character if character.isalnum() or character in {"-", "_"} else "-"
            for character in str(label).lower()
        ).strip("-")[:48] or "operation"
        self._workspace_serial += 1
        workspace = self._directory / f"work-{self._workspace_serial:03d}-{safe_label}"
        workspace.mkdir()
        return workspace

    def release_workspace(self, workspace) -> bool:
        """Release one owned workspace without permitting arbitrary deletion."""
        candidate = Path(workspace).resolve()
        if candidate.parent != self._directory or not candidate.name.startswith("work-"):
            raise RuntimeError(f"Refusing to remove non-workspace directory: {candidate}")
        try:
            shutil.rmtree(candidate)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return True

    @contextmanager
    def workspace(self, label: str):
        workspace = self.create_workspace(label)
        try:
            yield workspace
        finally:
            self.release_workspace(workspace)

    @classmethod
    @contextmanager
    def operation_workspace(cls, service, label: str):
        """Use a session service, or own a short-lived standalone service."""
        owns_service = service is None
        active_service = service or cls.create(f"standalone-{label}")
        try:
            with active_service.workspace(label) as workspace:
                yield workspace
        finally:
            if owns_service:
                active_service.close()

    @staticmethod
    def _metadata_path(path) -> Path:
        return Path(path).with_suffix(".json")

    @staticmethod
    def _restore_property(obj, name, previous, missing):
        obj.pop(name, None)
        if previous is not missing:
            obj[name] = previous

    @staticmethod
    def _validate_fragment(path, object_name: str, mesh_name: str):
        with bpy.data.libraries.load(str(path), link=False) as (data_from, _data_to):
            if object_name not in data_from.objects:
                raise RuntimeError(
                    f"Remi could not verify '{object_name}' in the recovery checkpoint"
                )
            if mesh_name not in data_from.meshes:
                raise RuntimeError(
                    f"Remi could not verify mesh '{mesh_name}' in the recovery checkpoint"
                )

    def write(self, obj, slot: str):
        """Atomically save and validate the exact working mesh plus a manifest."""
        if not obj or obj.type != "MESH" or obj.data is None:
            raise RuntimeError("Remi can only checkpoint a mesh object")

        destination = Path(self.path(slot))
        temporary = destination.with_name(destination.stem + ".writing.blend")
        metadata = self._metadata_path(destination)
        temporary_metadata = metadata.with_name(metadata.stem + ".writing.json")
        missing = object()
        previous_materials = obj.get(CHECKPOINT_MATERIALS_KEY, missing)
        previous_target = obj.get(CHECKPOINT_TARGET_KEY, missing)
        material_names = [material.name if material else "" for material in obj.data.materials]
        manifest = {
            "version": 1,
            "object_name": obj.name,
            "mesh_name": obj.data.name,
            "object_type": obj.type,
            "session_id": str(obj.get(SESSION_ID_KEY, "")),
            "parent_name": obj.parent.name if obj.parent else "",
        }

        obj[CHECKPOINT_MATERIALS_KEY] = json.dumps(material_names)
        obj[CHECKPOINT_TARGET_KEY] = True
        try:
            for candidate in (temporary, temporary_metadata):
                if candidate.exists():
                    candidate.unlink()
            bpy.data.libraries.write(
                str(temporary),
                {obj},
                path_remap="ABSOLUTE",
                fake_user=False,
                compress=True,
            )
            self._validate_fragment(temporary, obj.name, obj.data.name)
            with temporary_metadata.open("w", encoding="utf-8") as handle:
                json.dump(manifest, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
            os.replace(temporary_metadata, metadata)
        finally:
            self._restore_property(
                obj,
                CHECKPOINT_MATERIALS_KEY,
                previous_materials,
                missing,
            )
            self._restore_property(obj, CHECKPOINT_TARGET_KEY, previous_target, missing)
            for candidate in (temporary, temporary_metadata):
                try:
                    candidate.unlink()
                except OSError:
                    pass

    def _manifest(self, path: str) -> dict:
        metadata = self._metadata_path(path)
        if not metadata.is_file():
            return {}
        try:
            value = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def load(self, slot: str, *, expected_name: str = ""):
        """Load the recorded working object, never the first dependency object."""
        path = self.path(slot)
        if not Path(path).is_file():
            raise RuntimeError("The Remi recovery checkpoint is missing")

        manifest = self._manifest(path)
        recorded_name = str(manifest.get("object_name", ""))
        with bpy.data.libraries.load(path, link=False) as (data_from, data_to):
            available = list(data_from.objects)
            target_name = next(
                (
                    name
                    for name in (recorded_name, expected_name)
                    if name and name in available
                ),
                "",
            )
            if not target_name and len(available) == 1:
                target_name = available[0]
            if not target_name:
                detail = ", ".join(available[:8]) or "none"
                raise RuntimeError(
                    "The Remi recovery checkpoint does not identify its working mesh "
                    f"(objects: {detail})"
                )
            data_to.objects = [target_name]

        restored = data_to.objects[0]
        if restored is None or restored.type != "MESH" or restored.data is None:
            raise RuntimeError(
                f"The recorded Remi recovery object '{target_name}' contains no mesh"
            )
        if manifest and manifest.get("object_type") != "MESH":
            raise RuntimeError("The Remi recovery manifest does not describe a mesh")
        return restored, manifest

    def exists(self, slot: str) -> bool:
        return Path(self.path(slot)).is_file()

    def discard(self, slot: str):
        path = self.path(slot)
        for candidate in (Path(path), self._metadata_path(path)):
            try:
                candidate.unlink()
            except OSError:
                pass

    def promote(self, source_slot: str, destination_slot: str):
        source = Path(self.path(source_slot))
        destination = Path(self.path(destination_slot))
        if not source.is_file():
            raise RuntimeError(f"The Remi {source_slot} checkpoint is missing")
        self.discard(destination_slot)
        os.replace(source, destination)
        source_metadata = self._metadata_path(source)
        if source_metadata.is_file():
            os.replace(source_metadata, self._metadata_path(destination))

    def total_megabytes(self) -> float:
        self._ensure_open()
        total = 0
        for slot in self.SLOTS:
            path = self._directory / f"{slot}.blend"
            for candidate in (path, self._metadata_path(path)):
                try:
                    total += candidate.stat().st_size
                except OSError:
                    pass
        return total / (1024.0 * 1024.0)

    def close(self) -> bool:
        """Release every owned file; abandoned failures are retried on next start."""
        if self._closed:
            return True
        removed = False
        try:
            self._safe_remove_directory(self._root, self._directory)
            removed = True
        except OSError:
            pass
        finally:
            self._closed = True
        return removed
