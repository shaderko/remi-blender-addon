"""Blender account controls, missing generation API, revocation and lifecycle.

Only loopback fixtures; no browser account, production service or paid work.
"""
import importlib
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_cloud_auth import CloudAuthTests, TOKEN, DEVICE

module_name = os.environ.get("REMI_AUTH_INSTALLED_MODULE", "remi")
if module_name == "remi":
    spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    remi = importlib.util.module_from_spec(spec)
    sys.modules["remi"] = remi
    spec.loader.exec_module(remi)
    remi.register()
else:
    assert bpy.ops.preferences.addon_enable(module=module_name) == {"FINISHED"}
    remi = importlib.import_module(module_name)
cloud = importlib.import_module(module_name + ".features.cloud")
canvas = importlib.import_module(module_name + ".features.board.canvas")
runtime = importlib.import_module(module_name + ".integrations.remi_cloud.client").runtime
fixture = CloudAuthTests()
fixture.setUp()


def wait(predicate):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        cloud.tick()
        if predicate(): return
        time.sleep(.02)
    raise AssertionError(runtime.status())


class Layout:
    """Record the actual native draw functions without requiring GPU rendering."""
    def __init__(self): self.actions = []; self.labels = []; self.panels = []
    def row(self, **_kwargs): return self
    def column(self, **_kwargs): return self
    def label(self, **kwargs): self.labels.append(kwargs.get("text", ""))
    def operator(self, name, **_kwargs): self.actions.append(name)
    def popover(self, **kwargs): self.panels.append(kwargs)
    def prop(self, *_args, **_kwargs): pass
    def separator(self): pass


try:
    state = bpy.context.window_manager.remi_cloud
    state.base_url = fixture.api
    original_scene = (set(bpy.data.objects.keys()), set(bpy.data.workspaces.keys()))
    layout = Layout()
    with patch.object(canvas, "BOARD", SimpleNamespace()), patch.object(canvas, "board_area", return_value=bpy.context.area):
        canvas.draw_board_header(SimpleNamespace(layout=layout), bpy.context)
    assert any(p["panel"] == "Remi_PT_BoardCloud" and p["text"] == "Sign In" for p in layout.panels)
    cloud.Remi_PT_BoardCloud.draw(SimpleNamespace(layout=layout), bpy.context)
    assert "remi.cloud_login" in layout.actions and "remi.cloud_generate" not in layout.actions
    assert bpy.ops.remi.cloud_login() == {"FINISHED"}
    wait(lambda: bool(state.user_code))
    layout = Layout()
    cloud.Remi_PT_BoardCloud.draw(SimpleNamespace(layout=layout), bpy.context)
    assert "remi.cloud_open_approval" in layout.actions
    with patch.object(cloud.webbrowser, "open") as opened:
        assert bpy.ops.remi.cloud_open_approval() == {"FINISHED"}
        opened.assert_called_once_with(state.verification_url, new=2)
    wait(lambda: state.signed_in and not state.busy)
    assert state.email == "test@example.test" and not state.generation_enabled
    assert not state.user_code and not state.verification_url
    for fault in ("missing", "html", "malformed"):
        fixture.server.capabilities_fault = fault
        assert bpy.ops.remi.cloud_refresh() == {"FINISHED"}
        wait(lambda: not state.busy)
        assert state.signed_in and not state.generation_enabled
        assert "could not be checked" in state.message
    layout = Layout()
    cloud.Remi_PT_BoardCloud.draw(SimpleNamespace(layout=layout), bpy.context)
    assert {"remi.cloud_refresh", "remi.cloud_logout"} <= set(layout.actions)
    assert not any("generate" in action for action in layout.actions)
    fixture.server.capabilities_enabled = True
    fixture.server.capabilities_fault = None
    assert bpy.ops.remi.cloud_refresh() == {"FINISHED"}
    wait(lambda: not state.busy)
    assert state.generation_enabled
    layout = Layout()
    cloud.Remi_PT_BoardCloud.draw(SimpleNamespace(layout=layout), bpy.context)
    assert "remi.cloud_generate" not in layout.actions
    layout = Layout()
    with patch.object(canvas, "BOARD", SimpleNamespace()), patch.object(canvas, "board_area", return_value=bpy.context.area):
        canvas.draw_board_header(SimpleNamespace(layout=layout), bpy.context)
    assert any(p["panel"] == "Remi_PT_BoardCloud" and p["text"] == "Account" for p in layout.panels)
    fixture.server.revoked = True
    assert bpy.ops.remi.cloud_refresh() == {"FINISHED"}
    wait(lambda: not state.busy)
    assert not state.signed_in and not state.email
    fixture.server.revoked = False
    fixture.server.capabilities_fault = "missing"
    assert bpy.ops.remi.cloud_login() == {"FINISHED"}
    wait(lambda: state.signed_in and not state.busy)
    with tempfile.TemporaryDirectory(prefix="remi-auth-blender-") as temporary:
        blend = Path(temporary) / "account.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(blend))
        assert TOKEN.encode() not in blend.read_bytes() and DEVICE.encode() not in blend.read_bytes()
        bpy.ops.wm.open_mainfile(filepath=str(blend))
        cloud.tick()
        assert not bpy.context.window_manager.remi_cloud.signed_in and runtime.process is None
    wait(lambda: fixture.server.revoked)
    state = bpy.context.window_manager.remi_cloud
    state.base_url = fixture.api
    fixture.server.revoked = False
    assert bpy.ops.remi.cloud_login() == {"FINISHED"}
    wait(lambda: state.signed_in and not state.busy)
    assert bpy.ops.remi.cloud_logout() == {"FINISHED"}
    wait(lambda: not state.signed_in and not state.busy)
    assert fixture.server.revoked
    assert original_scene == (set(bpy.data.objects.keys()), set(bpy.data.workspaces.keys()))
    assert not any(path.endswith("/jobs") for path, _headers, _body in fixture.server.requests)
    print("REMI_CLOUD_AUTH_REGRESSION_OK: board controls, approval, missing API, revocation, logout and save/load reset")
finally:
    runtime.cleanup()
    fixture.tearDown()
    if module_name == "remi": remi.unregister()
    else: bpy.ops.preferences.addon_disable(module=module_name)
