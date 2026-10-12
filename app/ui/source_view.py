"""Entry view shown before a Remi editing session starts."""

from .flow_controls import draw_flow_controls


def draw_source(layout, context):
    obj = context.view_layer.objects.active

    layout.label(text="REMI", icon="MOD_REMESH")
    layout.label(text="One mesh from repair to bake")
    layout.separator()

    if not obj or obj.type != "MESH" or context.mode != "OBJECT":
        notice = layout.box()
        notice.label(text="Select one mesh in Object Mode", icon="INFO")
        return

    layout.label(text=obj.name, icon="OBJECT_DATA")
    row = layout.row(align=True)
    row.label(text=f"{len(obj.data.vertices):,} vertices")
    row.label(text=f"{len(obj.data.polygons):,} faces")
    layout.separator()
    start = layout.column()
    start.scale_y = 1.5
    start.operator("remi.start_session", text="Start Remi", icon="PLAY")
    layout.label(text="Locked until Finish or Cancel", icon="LOCKED")
    layout.separator()
    draw_flow_controls(layout, context)
