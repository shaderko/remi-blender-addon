"""Compact entry controls for preset-driven automatic processing."""

import textwrap

from ..application import get_application
from ..flow import selected_document, automatic_actions, DEFAULT_ACTIONS


def draw_flow_controls(layout, context):
    flow = context.scene.remi_flow
    box = layout.box()
    box.label(text="Automatic · Full Flow", icon="SEQ_SEQUENCER")
    row = box.row(align=True)
    row.prop(flow, "preset", text="")
    row.operator("remi.edit_flow_preset", text="", icon="GREASEPENCIL")
    row.operator("remi.delete_flow_preset", text="", icon="TRASH")
    try:
        document = selected_document(context)
    except (ValueError, OSError, KeyError) as exc:
        document = None
        for index, line in enumerate(textwrap.wrap(str(exc), 45)):
            box.label(text=line, icon="ERROR" if index == 0 else "NONE")
    if flow.preset == "CURRENT":
        for registered in automatic_actions():
            row = box.row(align=True)
            step = next((s for s in flow.steps if s.action_id == registered.id), None)
            if step:
                row.prop(step, "enabled", text=registered.action.name)
            else:
                row.label(text=registered.action.name, icon="CHECKBOX_HLT" if registered.id in DEFAULT_ACTIONS else "CHECKBOX_DEHLT")
            button = row.operator("remi.edit_flow_stage", text="", icon="PREFERENCES")
            button.action_id = registered.id
        box.prop(flow, "show_settings", text="Stage Settings", icon="TRIA_DOWN" if flow.show_settings else "TRIA_RIGHT", emboss=False)
        if flow.show_settings:
            action = get_application().features.action(flow.edit_action)
            if action and action.action.automatic:
                settings_box = box.box()
                settings_box.label(text=action.action.name)
                action.feature.draw_automatic_settings(settings_box, context, action.action)
    elif document:
        for index, action_id in enumerate(document["actions"], 1):
            action = get_application().features.require_action(action_id)
            label = "Voxel Remesh" if action_id == "REMESH" and document["settings"].get("remesh_backend") == "VOXEL" else action.action.name
            label = "MeshLab Decimation" if action_id == "DECIMATE" else label
            label = "UV Unwrap" if action_id == "UV" else label
            box.label(text=f"{index}. {label}")
    box.operator("remi.save_flow_preset", text="Save Current Settings as Preset", icon="ADD")
    run = box.column()
    run.enabled = document is not None
    run.scale_y = 1.5
    run.operator("remi.run_full_flow", text="Run Full Flow", icon="PLAY")
    if flow.last_result:
        for line in textwrap.wrap(flow.last_result, 48):
            box.label(text=line)
