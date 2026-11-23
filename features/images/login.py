"""Restore the saved local image account without blocking Blender's UI."""
import bpy
from .operators import _configure_codex_credentials, runtime
from ...integrations.codex_images.client import session

_attempted=False
_job=None


def tick():
    global _attempted,_job
    if bpy.app.background:
        return None
    if not bpy.app.online_access:
        return 1.0
    if _job is not None:
        if session.pending is not _job:
            _job=None
            return None
        if _job.poll() is None:return .25
        try:_job.result()
        except RuntimeError as error:
            bpy.context.window_manager.remi_image.status=str(error)
        finally:_job.cancel();_job=None
        return None
    if not _attempted and runtime.job is None and session.pending is None:
        _attempted=True
        try:
            _configure_codex_credentials()
            _job=session.start(action="restore",output="")
            return .25
        except (RuntimeError,OSError) as error:
            bpy.context.window_manager.remi_image.status=str(error)
    return None


def register():
    global _attempted
    _attempted=False
    if not bpy.app.background and not bpy.app.timers.is_registered(tick):
        bpy.app.timers.register(tick,first_interval=1.0)


def unregister():
    global _attempted,_job
    if bpy.app.timers.is_registered(tick):bpy.app.timers.unregister(tick)
    if _job is not None:_job.cancel()
    _attempted=False;_job=None
