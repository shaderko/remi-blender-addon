"""Automatic flow integration: presets, real stages, interruption and recovery."""
from pathlib import Path
import copy
import json
import sys
import tempfile
from unittest.mock import patch

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import remi
from remi.app import flow
from remi.app.application import get_application
from remi.workflow.automatic import AutomaticFlow
from remi.workflow.presets import PresetStore, capture_settings, checked_settings
from remi.workflow import session
from remi.integrations import meshlab


def clean():
    if session.runtime.state(bpy.context).active:
        session.runtime.cancel(bpy.context)
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for image in list(bpy.data.images):
        if image.users == 0:
            bpy.data.images.remove(image)


def source():
    clean()
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=.4)
    obj = bpy.context.object
    obj.name = 'AutomaticSource'
    material = bpy.data.materials.new('FlowOriginalMaterial')
    material.use_nodes = True
    material.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value = (.7, .15, .03, 1)
    obj.data.materials.append(material)
    return obj


def document(actions=None):
    bpy.context.scene.remi_flow.preset = 'DEFAULT'
    value = flow.selected_document(bpy.context)
    value['name'] = 'Regression Flow'
    value['settings'].update(voxel_size=.07, decimation_passes=2, target_percentage=.7,
                             bake_texture_size=256, bake_half_scale=False)
    if actions is not None:
        value['actions'] = actions
    return value


def run_controller(value):
    app = get_application()
    runner = AutomaticFlow(app.session, app.features)
    runner.start(bpy.context, value)
    return runner


def test_default_and_presets(directory):
    source()
    settings = bpy.context.scene.remi_settings
    settings.voxel_size = .08
    default = flow.selected_document(bpy.context)
    assert default['actions'] == ['REMESH', 'DECIMATE', 'UV', 'BAKE_ALL']
    assert default['settings']['remesh_backend'] == 'VOXEL'
    assert abs(default['settings']['voxel_size'] - .01) < 1e-6
    assert abs(settings.voxel_size - .08) < 1e-6, 'reading the default modified current settings'
    assert bpy.ops.remi.edit_flow_preset() == {'FINISHED'}
    settings.voxel_size = .06
    settings.decimation_passes = 3
    assert bpy.ops.remi.save_flow_preset(name='Sculpture Test') == {'FINISHED'}
    identifier = bpy.context.scene.remi_flow.preset
    saved = PresetStore(directory).load(identifier)
    earlier = copy.deepcopy(saved)
    earlier['name'] = 'A preset sorted before the selected one'
    other_id = flow.preset_store().save(earlier)
    assert bpy.context.scene.remi_flow.preset == identifier, 'preset selection changed after list reordering'
    flow.preset_store().delete(other_id)
    assert saved['settings']['decimation_passes'] == 3
    assert saved['actions'] == list(flow.DEFAULT_ACTIONS)
    settings.decimation_passes = 1
    assert flow.selected_document(bpy.context)['settings']['decimation_passes'] == 3
    assert bpy.ops.remi.edit_flow_preset() == {'FINISHED'}
    assert settings.decimation_passes == 3
    before = capture_settings(settings)
    bad = copy.deepcopy(saved)
    bad['settings']['voxel_size'] = 'not a number'
    try:
        flow.validate_plan(bpy.context, bad)
        raise AssertionError('Invalid settings accepted')
    except ValueError:
        pass
    assert capture_settings(settings) == before
    for actions in [['MANUAL_REPAIR'], ['INSTANT_START'], ['UV', 'UV'], ['BAKE_ALL', 'BAKE_AO'], ['BAKE_ALL', 'REMESH']]:
        bad = copy.deepcopy(saved)
        bad['actions'] = actions
        try:
            flow.validate_plan(bpy.context, bad)
            raise AssertionError('Invalid action plan accepted: '+str(actions))
        except ValueError:
            pass
    (Path(directory)/('f'*32+'.json')).write_text('{broken')
    assert len(flow.preset_store().entries()) == 1
    try:
        flow.preset_store().load('../escape')
        raise AssertionError('Path traversal accepted')
    except ValueError:
        pass
    # The chosen named preset is resolved from persistent storage on a new store.
    assert PresetStore(directory).load(identifier) == saved
    settings.decimation_passes = 4
    assert bpy.ops.remi.save_flow_preset(name='Sculpture Test', replace_existing=True) == {'FINISHED'}
    assert flow.preset_store().load(identifier)['settings']['decimation_passes'] == 4
    path = str(Path(directory)/'preset_roundtrip.blend')
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.context.scene.remi_flow.preset = 'DEFAULT'
    bpy.ops.wm.open_mainfile(filepath=path)
    assert bpy.context.scene.remi_flow.preset == identifier
    assert flow.selected_document(bpy.context)['settings']['decimation_passes'] == 4
    print('PASS full-flow defaults and persistent scalar preset round trip')


def test_preflight_is_before_changes():
    obj = source()
    original_mesh = obj.data
    previous = capture_settings(bpy.context.scene.remi_settings)
    value = document()
    with patch.object(meshlab, 'ensure_pymeshlab', return_value=False):
        try:
            run_controller(value)
            raise AssertionError('Missing MeshLab passed preflight')
        except RuntimeError as exc:
            assert 'PyMeshLab' in str(exc)
    assert not session.runtime.state(bpy.context).active
    assert session.runtime.disk is None
    assert bpy.context.object == obj and obj.data == original_mesh
    assert len(bpy.context.scene.objects) == 1
    assert capture_settings(bpy.context.scene.remi_settings) == previous
    print('PASS missing dependency blocks before session creation or mesh edits')


def test_stop_and_failure_recovery():
    obj = source()
    count = len(obj.data.polygons)
    runner = run_controller(document(['REMESH', 'UV']))
    frozen_size = runner.settings['voxel_size']
    assert runner.tick(bpy.context) == 'RUNNING'  # Show queued stage before work.
    assert session.runtime.state(bpy.context).step_index == 0
    bpy.context.scene.remi_settings.voxel_size = .01
    assert runner.tick(bpy.context) == 'RUNNING'
    assert abs(bpy.context.scene.remi_settings.voxel_size-frozen_size)<1e-6
    assert session.runtime.state(bpy.context).step_index == 1
    assert len(bpy.context.scene.objects) == 1
    runner.stop_requested = True
    assert runner.tick(bpy.context) == 'STOPPED'
    assert not session.runtime.state(bpy.context).busy
    assert not session.runtime.state(bpy.context).automatic
    session.runtime.cancel(bpy.context)
    assert len(bpy.context.object.data.polygons) == count

    obj = source()
    runner = run_controller(document(['REMESH', 'UV']))
    runner.tick(bpy.context)
    runner.tick(bpy.context)
    current = session.runtime.object(bpy.context)
    mesh = current.data
    uv_feature = get_application().features.get('UV')
    with patch.object(uv_feature._service, 'generate', side_effect=RuntimeError('controlled UV failure')):
        runner.tick(bpy.context)
        assert runner.tick(bpy.context) == 'FAILED'
    assert session.runtime.state(bpy.context).stage == 'UV'
    assert session.runtime.object(bpy.context) == current and current.data == mesh
    assert len(bpy.context.scene.objects) == 1
    assert session.runtime.state(bpy.context).can_undo
    # The full-flow operator must delegate to the manual controller after failure.
    from remi.app.flow import Remi_OT_RunFullFlow
    from remi.workflow.session_operators import Remi_OT_StartSession, RemiSessionController
    assert issubclass(Remi_OT_RunFullFlow, RemiSessionController)
    assert not issubclass(Remi_OT_RunFullFlow, Remi_OT_StartSession)
    session.runtime.undo(bpy.context)
    assert len(session.runtime.object(bpy.context).data.polygons) == count
    session.runtime.cancel(bpy.context)
    print('PASS stop, failed-stage isolation, Back and source recovery')


class Layout:
    def __init__(self, events=None):
        self.events = events if events is not None else []
    def row(self, **kwargs): return Layout(self.events)
    def column(self, **kwargs): return Layout(self.events)
    def box(self): return Layout(self.events)
    def separator(self): pass
    def label(self, **kwargs): self.events.append(('label', kwargs.get('text')))
    def prop(self, value, name, **kwargs):
        assert hasattr(value,name),name
        self.events.append(('prop',name))
    def operator(self, name, **kwargs):
        from types import SimpleNamespace
        self.events.append(('operator',name))
        return SimpleNamespace()


def test_entry_ui_and_manual_handoff():
    from types import SimpleNamespace
    from remi.app.ui.source_view import draw_source
    from remi.app.ui.main_panel import draw_session
    from remi.app.flow import Remi_OT_RunFullFlow
    from remi.workflow.session_operators import RemiSessionController
    source()
    bpy.context.scene.remi_flow.preset='DEFAULT'
    layout=Layout()
    draw_source(layout,bpy.context)
    assert ('operator','remi.start_session') in layout.events
    assert ('operator','remi.run_full_flow') in layout.events
    assert ('label','1. Voxel Remesh') in layout.events
    bpy.ops.remi.edit_flow_preset()
    layout=Layout()
    draw_source(layout,bpy.context)
    assert ('prop','enabled') in layout.events and ('prop','voxel_size') in layout.events
    runner=run_controller(document(['REMESH','UV']))
    layout=Layout()
    draw_session(layout,bpy.context)
    assert ('operator','remi.stop_full_flow') in layout.events
    assert ('operator','remi.session_command') not in layout.events
    operator=SimpleNamespace(_flow=runner)
    event=SimpleNamespace(type='ESC',value='PRESS',ctrl=False)
    assert Remi_OT_RunFullFlow.modal(operator,bpy.context,event)=={'RUNNING_MODAL'}
    assert session.runtime.state(bpy.context).flow_stop_requested
    assert runner.tick(bpy.context)=='STOPPED'
    with patch.object(RemiSessionController,'modal',return_value={'RUNNING_MODAL'}) as manual:
        assert Remi_OT_RunFullFlow.modal(operator,bpy.context,event)=={'RUNNING_MODAL'}
        manual.assert_called_once()
    session.runtime.cancel(bpy.context)
    print('PASS manual/full-flow entry UI, editable controls, stop and modal handoff')


def test_both_registered_entry_operators():
    """Direct Python class polls do not catch detached Blender RNA callbacks."""
    source()
    for _cycle in range(2):
        assert bpy.ops.remi.start_session.poll(), 'Start Remi disabled after full-flow registration'
        assert bpy.ops.remi.run_full_flow.poll(), 'Full Flow disabled with a selected mesh'
        assert bpy.ops.remi.start_session() == {'FINISHED'}
        assert session.runtime.state(bpy.context).active
        assert not session.runtime.state(bpy.context).automatic
        assert not bpy.ops.remi.start_session.poll()
        assert not bpy.ops.remi.run_full_flow.poll()
        session.runtime.cancel(bpy.context)
    # Unregister/re-register must restore both distinct RNA callbacks as well.
    remi.unregister()
    remi.register()
    assert bpy.ops.remi.start_session.poll()
    assert bpy.ops.remi.run_full_flow.poll()
    print('PASS both Blender entry operators and re-registration')


def test_actual_full_flow():
    if not meshlab.ensure_pymeshlab():
        raise AssertionError('Install PyMeshLab to run the real automatic flow integration test')
    obj = source()
    original_faces = len(obj.data.polygons)
    original_color = tuple(obj.data.materials[0].node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value)
    value = document()
    identifier = flow.preset_store().save(value)
    flow.preset_items(None, bpy.context)
    bpy.context.scene.remi_flow.preset = identifier
    # Real operator executes the persisted plan, all four stages, and exits Remi.
    assert bpy.ops.remi.run_full_flow() == {'FINISHED'}
    result = bpy.context.object
    assert result.name == 'AutomaticSource'
    assert len(bpy.context.scene.objects) == 1
    assert not session.runtime.state(bpy.context).active
    assert session.runtime.disk is None
    assert not result.get('_remi_session_id')
    assert result.data.uv_layers.active is not None
    assert result.get('remi_uv_overlap_pairs') == 0
    assert len(result.data.polygons) != original_faces
    assert '4 stages' in bpy.context.scene.remi_flow.last_result
    for suffix in ('diffuse','roughness','normal','ao'):
        image = bpy.data.images.get('AutomaticSource_'+suffix)
        assert image is not None and image.size[:] == (256,256), suffix
        assert image.has_data, suffix
    # Albedo must come from the ORIGINAL checkpoint, whose material was lost
    # by untextured MeshLab decimation. Check actual baked pixels, not just files.
    import numpy as np
    image = bpy.data.images['AutomaticSource_diffuse']
    pixels = np.empty(len(image.pixels),dtype=np.float32)
    image.pixels.foreach_get(pixels)
    colors = pixels.reshape(-1,4)[:,:3]
    assert np.max(colors[:,0]) > .4
    assert np.count_nonzero(colors[:,0] > colors[:,1]*2) > 100
    assert original_color[0] > original_color[1]*2
    print('PASS real voxel → MeshLab → UV → all-map bake with original-source pixels')


def main():
    remi.register()
    try:
        with tempfile.TemporaryDirectory(prefix='remi-flow-regression-') as directory:
            flow._store_override = PresetStore(directory)
            test_default_and_presets(directory)
            test_preflight_is_before_changes()
            test_stop_and_failure_recovery()
            test_entry_ui_and_manual_handoff()
            test_both_registered_entry_operators()
            test_actual_full_flow()
        print('REMI_AUTOMATIC_FLOW_REGRESSION_OK')
    finally:
        if session.runtime.state(bpy.context).active:
            session.runtime.cancel(bpy.context)
        flow._store_override = None
        remi.unregister()


if __name__ == '__main__':
    main()
