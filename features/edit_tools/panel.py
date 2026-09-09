"""Sidebar panel for edit-mode topology selection tools."""

from bpy.types import Panel


class Remi_PT_EditToolsPanel(Panel):
    bl_label = "Remi Selection Tools"
    bl_idname = "Remi_PT_EditToolsPanel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Remi"
    bl_context = "mesh_edit"

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="Fused Parts", icon="AUTOMERGE_ON")
        box.label(text="Select the connected region first")
        column = box.column(align=True)
        column.operator("remi.smart_select_object", icon="RESTRICT_SELECT_OFF")
        column.operator("remi.detect_bridge", icon="MOD_EDGESPLIT")
        column.operator("remi.select_split_part", icon="RESTRICT_SELECT_OFF")
        column.operator("remi.split_by_bridge", icon="MOD_BOOLEAN")

        box = layout.box()
        box.label(text="Double Shell", icon="MOD_SOLIDIFY")
        box.label(text="Scans the entire visible mesh")
        column = box.column(align=True)
        column.operator("remi.select_inner_shell", icon="RESTRICT_SELECT_OFF")
        column.operator("remi.remove_inner_shell", icon="TRASH")
