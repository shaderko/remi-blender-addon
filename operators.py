"""
Blender operators for the Remi pipeline.
"""

import os
import subprocess
import tempfile
from pathlib import Path
import bpy
from bpy.types import Operator
from bpy.props import BoolProperty

from . import gn_setup
from . import meshlab_wrapper as mlw
from . import autoremesher as arm
from . import baking


# ============================================================
# Utility helpers
# ============================================================

def _get_temp_dir() -> str:
    """Return a temp directory for intermediate files."""
    temp_dir = os.path.join(tempfile.gettempdir(), "autoremesh")
    os.makedirs(temp_dir, exist_ok=True)
    return temp_dir


def _duplicate_object(obj: bpy.types.Object, suffix: str = "_copy") -> bpy.types.Object:
    """Create a duplicate of an object (for processing, keeping original intact)."""
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.duplicate()
    dup = bpy.context.view_layer.objects.active
    dup.name = obj.name + suffix
    obj.select_set(False)
    return dup


def _export_ply(obj: bpy.types.Object, filepath: str) -> bool:
    """Export a single object as PLY (no axis conversion — PyMeshLab compatible).

    We use PLY instead of OBJ because PyMeshLab applies a Y-up↔Z-up axis
    conversion when reading OBJ files, which silently swaps Y/Z coordinates.
    PLY is a raw vertex format that passes through without transformation.
    """
    prev_active = bpy.context.view_layer.objects.active
    prev_selected = bpy.context.selected_objects.copy()

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    try:
        bpy.ops.wm.ply_export(
            filepath=filepath,
            export_selected_objects=True,
            apply_modifiers=True,
        )
        success = True
    except Exception as e:
        print(f"Remi: PLY export failed: {e}")
        success = False

    bpy.ops.object.select_all(action="DESELECT")
    if prev_active:
        prev_active.select_set(True)
        bpy.context.view_layer.objects.active = prev_active
    for o in prev_selected:
        if o != prev_active:
            o.select_set(True)

    return success


def _export_obj_for_tool(obj: bpy.types.Object, filepath: str) -> bool:
    """Export as OBJ for external tool consumption (AutoRemesher)."""
    prev_active = bpy.context.view_layer.objects.active
    prev_selected = bpy.context.selected_objects.copy()
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    try:
        bpy.ops.wm.obj_export(
            filepath=filepath,
            export_selected_objects=True,
            apply_modifiers=True,
            forward_axis="NEGATIVE_Z",
            up_axis="Y",
            export_materials=False,
        )
        success = True
    except Exception:
        success = False
    bpy.ops.object.select_all(action="DESELECT")
    if prev_active:
        prev_active.select_set(True)
        bpy.context.view_layer.objects.active = prev_active
    for o in prev_selected:
        if o != prev_active:
            o.select_set(True)
    return success


def _import_obj_result(filepath: str) -> bpy.types.Object:
    """Import an OBJ result from an external tool, restoring selection."""
    prev_selected = bpy.context.selected_objects.copy()
    prev_active = bpy.context.view_layer.objects.active
    bpy.ops.wm.obj_import(
        filepath=filepath,
        use_split_objects=False,
        use_split_groups=False,
        forward_axis="NEGATIVE_Z",
        up_axis="Y",
    )
    imported = [o for o in bpy.context.selected_objects if o not in prev_selected]
    mesh_objs = [o for o in imported if o.type == "MESH"]
    bpy.ops.object.select_all(action="DESELECT")
    for o in prev_selected:
        o.select_set(True)
    if prev_active:
        bpy.context.view_layer.objects.active = prev_active
    return mesh_objs[0] if mesh_objs else None


def _import_ply(filepath: str) -> bpy.types.Object:
    """Import a PLY file and return the first mesh object."""
    prev_selected = bpy.context.selected_objects.copy()
    prev_active = bpy.context.view_layer.objects.active

    bpy.ops.wm.ply_import(filepath=filepath)

    # Find newly imported objects
    imported = [o for o in bpy.context.selected_objects if o not in prev_selected]
    mesh_objs = [o for o in imported if o.type == "MESH"]

    # Restore selection
    bpy.ops.object.select_all(action="DESELECT")
    for o in prev_selected:
        o.select_set(True)
    if prev_active:
        bpy.context.view_layer.objects.active = prev_active

    return mesh_objs[0] if mesh_objs else None


def _apply_modifiers(obj: bpy.types.Object):
    """Apply all modifiers on an object (makes them permanent)."""
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    for mod in obj.modifiers:
        try:
            bpy.ops.object.modifier_apply(modifier=mod.name)
        except Exception as e:
            print(f"Remi: Could not apply modifier '{mod.name}': {e}")


# ============================================================
# Operators
# ============================================================

class Remi_OT_ImportGLB(Operator):
    """Import a GLB file into the scene."""
    bl_idname = "remi.import_glb"
    bl_label = "Import GLB"
    bl_description = "Import a GLB/glTF file into the scene"
    bl_options = {"REGISTER", "UNDO"}

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")  # type: ignore

    def execute(self, context):
        settings = context.scene.remi_settings

        if self.filepath:
            filepath = self.filepath
        elif settings.import_glb_path:
            filepath = settings.import_glb_path
        else:
            self.report({"ERROR"}, "No GLB file specified")
            return {"CANCELLED"}

        if not os.path.exists(filepath):
            self.report({"ERROR"}, f"File not found: {filepath}")
            return {"CANCELLED"}

        # Import GLB/glTF
        prev_objects = set(bpy.context.scene.objects)
        try:
            bpy.ops.import_scene.gltf(filepath=filepath)
        except Exception as e:
            self.report({"ERROR"}, f"Failed to import GLB: {e}")
            return {"CANCELLED"}

        # Find imported objects
        new_objs = [o for o in bpy.context.scene.objects if o not in prev_objects]
        if not new_objs:
            self.report({"WARNING"}, "No objects imported (file may be empty)")
            return {"CANCELLED"}

        # Select the first imported mesh
        for obj in new_objs:
            if obj.type == "MESH":
                bpy.context.view_layer.objects.active = obj
                obj.select_set(True)
                break

        self.report({"INFO"}, f"Imported {len(new_objs)} object(s) from GLB")
        return {"FINISHED"}

    def invoke(self, context, event):
        settings = context.scene.remi_settings
        if settings.import_glb_path:
            self.filepath = settings.import_glb_path
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}


class Remi_OT_SDFRemesh(Operator):
    """Apply SDF voxel remesh to selected object (via geometry nodes on a copy)."""
    bl_idname = "remi.sdf_remesh"
    bl_label = "SDF Voxel Remesh"
    bl_description = "Duplicate selected object and apply SDF grid remesh via Geometry Nodes"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.remi_settings

        obj = bpy.context.view_layer.objects.active
        if not obj or obj.type != "MESH":
            self.report({"ERROR"}, "Select a mesh object first")
            return {"CANCELLED"}

        # Duplicate the object (never touch the original)
        dup = _duplicate_object(obj, "_remesh")
        dup.select_set(True)
        bpy.context.view_layer.objects.active = dup

        # Apply the SDF geometry nodes modifier
        gn_setup.apply_remi_modifier(
            obj=dup,
            detail=settings.detail,
            fillet_radius=settings.fillet_radius if settings.use_sdf_fillet else 0.0,
            smooth_iterations=settings.smoothing_iterations if settings.use_sdf_smoothing else 0,
        )

        self.report({"INFO"}, f"Applied SDF remesh to '{dup.name}'")
        return {"FINISHED"}


class Remi_OT_ApplyRemesh(Operator):
    """Apply the SDF remesh modifier, converting it to real geometry."""
    bl_idname = "remi.apply_remesh"
    bl_label = "Apply Remesh"
    bl_description = "Apply the geometry nodes modifier to bake the remeshed geometry"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = bpy.context.view_layer.objects.active
        if not obj or obj.type != "MESH":
            self.report({"ERROR"}, "Select a mesh object first")
            return {"CANCELLED"}

        # Find and apply AR modifier
        group = gn_setup.ensure_remi_node_group()
        found = False
        for mod in obj.modifiers:
            if mod.type == "NODES" and mod.node_group == group:
                _apply_modifiers(obj)
                found = True
                break

        if not found:
            self.report({"ERROR"}, "No AR_SDF_Remesh modifier found on active object")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Applied remesh on '{obj.name}'")
        return {"FINISHED"}


class Remi_OT_Decimate(Operator):
    """Export to OBJ and run PyMeshLab quadric edge collapse decimation."""
    bl_idname = "remi.decimate"
    bl_label = "Decimate (MeshLab)"
    bl_description = "Export active object to OBJ and run MeshLab quadric edge collapse"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.remi_settings

        obj = bpy.context.view_layer.objects.active
        if not obj or obj.type != "MESH":
            self.report({"ERROR"}, "Select a mesh object first")
            return {"CANCELLED"}

        # Check PyMeshLab
        if not mlw.ensure_pymeshlab():
            self.report({"ERROR"}, "PyMeshLab is not installed and could not be installed. "
                                    "Please run Blender with admin/sudo and it will auto-install.")
            return {"CANCELLED"}

        # Setup temp paths
        temp_dir = _get_temp_dir()
        base_name = bpy.path.clean_name(obj.name)
        input_ply = os.path.join(temp_dir, f"{base_name}_input.ply")
        output_ply = os.path.join(temp_dir, f"{base_name}_decimated.ply")

        # Export to PLY (no axis conversion — PyMeshLab compatible)
        self.report({"INFO"}, "Exporting to PLY...")
        if not _export_ply(obj, input_ply):
            self.report({"ERROR"}, "PLY export failed")
            return {"CANCELLED"}

        # Run decimation
        self.report({"INFO"}, f"Running {settings.decimation_passes} decimation pass(es)...")
        results = mlw.run_multi_pass_decimation(
            input_path=input_ply,
            output_path=output_ply,
            passes=settings.decimation_passes,
            target_percentage=settings.target_percentage,
        )

        # Check results
        for r in results:
            if not r["success"]:
                self.report({"ERROR"}, f"Decimation pass {r['pass']} failed: {r.get('error')}")
                return {"CANCELLED"}
            print(f"Remi: Pass {r['pass']}: {r.get('input_faces', '?')} → {r.get('output_faces', '?')} faces")

        # Import result back (PLY import)
        self.report({"INFO"}, "Importing decimated result...")
        new_obj = _import_ply(output_ply)
        if new_obj:
            new_obj.name = obj.name + settings.output_name_suffix
            # Vertices are already at world-space coords (baked during export),
            # so the object sits at origin with correct geometry.
            self.report({"INFO"}, f"Decimated model imported as '{new_obj.name}'")
        else:
            self.report({"ERROR"}, "Failed to import decimated PLY")
            return {"CANCELLED"}

        # Cleanup temp files
        try:
            os.remove(input_ply)
            os.remove(output_ply)
        except OSError:
            pass

        return {"FINISHED"}


class Remi_OT_AutoRemesher(Operator):
    """Run AutoRemesher external tool on the active mesh."""
    bl_idname = "remi.autoremesher"
    bl_label = "AutoRemesher (External)"
    bl_description = "Run the external AutoRemesher executable on the active mesh"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.remi_settings
        obj = bpy.context.view_layer.objects.active
        if not obj or obj.type != "MESH":
            self.report({"ERROR"}, "Select a mesh object first")
            return {"CANCELLED"}

        executable = arm.resolve_executable(settings.autoremesher_executable)
        error = arm.validate_executable(executable)
        if error:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}

        temp_dir = _get_temp_dir()
        base_name = bpy.path.clean_name(obj.name)
        input_obj = os.path.join(temp_dir, f"{base_name}_ar_input.obj")
        output_obj = os.path.join(temp_dir, f"{base_name}_ar_output.obj")
        report_path = os.path.join(temp_dir, f"{base_name}_ar_report.txt")

        self.report({"INFO"}, "Exporting to OBJ for AutoRemesher...")
        if not _export_obj_for_tool(obj, input_obj):
            self.report({"ERROR"}, "OBJ export failed")
            return {"CANCELLED"}

        command = arm.build_command(
            executable,
            Path(input_obj),
            Path(output_obj),
            Path(report_path),
            target_quads=settings.ar_target_quads,
            edge_scaling=settings.ar_edge_scaling,
            sharp_edge=settings.ar_sharp_edge,
            smooth_normal=settings.ar_smooth_normal,
            adaptivity=settings.ar_adaptivity,
        )

        self.report({"INFO"}, "Running AutoRemesher...")
        result = subprocess.run(
            command,
            cwd=str(executable.parent),
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            msg = result.stderr.strip() or result.stdout.strip()
            self.report({"ERROR"}, msg or "AutoRemesher failed")
            return {"CANCELLED"}

        if not os.path.isfile(output_obj):
            self.report({"ERROR"}, "AutoRemesher did not produce output file")
            return {"CANCELLED"}

        self.report({"INFO"}, "Importing AutoRemesher result...")
        new_obj = _import_obj_result(output_obj)
        if new_obj:
            new_obj.name = obj.name + "_autoremesh"
            if settings.ar_hide_original:
                obj.hide_set(True)
            bpy.context.view_layer.objects.active = new_obj
            new_obj.select_set(True)
            self.report({"INFO"}, f"AutoRemesher result imported as '{new_obj.name}'")
        else:
            self.report({"ERROR"}, "Failed to import AutoRemesher result")
            return {"CANCELLED"}

        # Cleanup
        for f in (input_obj, output_obj, report_path):
            try:
                os.remove(f)
            except OSError:
                pass

        return {"FINISHED"}


class Remi_OT_BakeTextures(Operator):
    """Bake diffuse/roughness/normal/metallic from original to result."""
    bl_idname = "remi.bake_textures"
    bl_label = "Bake Textures"
    bl_description = "Bake textures from the original active mesh to the selected mesh"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (
            context.active_object
            and context.active_object.type == "MESH"
            and len(context.selected_objects) >= 2
        )

    def execute(self, context):
        target = context.active_object
        sources = [o for o in context.selected_objects if o != target and o.type == "MESH"]
        if not sources:
            self.report({"ERROR"}, "Select the original as source, then the result as active target")
            return {"CANCELLED"}
        source = sources[0]

        s = context.scene.remi_settings
        result = baking.bake_textures(
            source, target,
            texture_size=s.bake_texture_size,
            uv_method=s.bake_uv_method,
            uv_island_margin=s.bake_uv_island_margin,
        )
        if result["success"]:
            self.report({"INFO"}, f"Baked textures: {', '.join(result['images'])}")
        else:
            self.report({"ERROR"}, "Baking failed")
            return {"CANCELLED"}
        return {"FINISHED"}


class Remi_OT_FullPipeline(Operator):
    """Run the full Remi pipeline on the selected object."""
    bl_idname = "remi.full_pipeline"
    bl_label = "Run Full Pipeline"
    bl_description = "SDF Remesh → Decimate → [AutoRemesher] → [Bake Textures]"
    bl_options = {"REGISTER", "UNDO"}

    def _total_steps(self, settings):
        n = 3  # SDF + decimate + naming
        if settings.use_autoremesher:
            n += 1
        if settings.use_baking:
            n += 1
        return n

    def execute(self, context):
        settings = context.scene.remi_settings
        step = 0
        total = self._total_steps(settings)

        # --- Get the active object (source) ---
        obj = bpy.context.view_layer.objects.active
        if not obj or obj.type != "MESH":
            self.report({"ERROR"}, "Select a mesh object")
            return {"CANCELLED"}

        step += 1
        self.report({"INFO"}, f"Step {step}/{total}: SDF remeshing...")

        # --- Step 1: Duplicate and SDF Remesh ---
        dup = _duplicate_object(obj, "_remesh")
        bpy.context.view_layer.objects.active = dup
        dup.select_set(True)
        gn_setup.apply_remi_modifier(
            obj=dup,
            detail=settings.detail,
            fillet_radius=settings.fillet_radius if settings.use_sdf_fillet else 0.0,
            smooth_iterations=settings.smoothing_iterations if settings.use_sdf_smoothing else 0,
        )
        _apply_modifiers(dup)
        self.report({"INFO"}, f"Step {step}/{total}: SDF remesh done")

        step += 1
        self.report({"INFO"}, f"Step {step}/{total}: Decimating...")

        # --- Step 2: MeshLab Decimation ---
        if not mlw.ensure_pymeshlab():
            self.report({"ERROR"}, "PyMeshLab not available")
            return {"CANCELLED"}

        temp_dir = _get_temp_dir()
        base_name = bpy.path.clean_name(dup.name)
        input_ply = os.path.join(temp_dir, f"{base_name}_input.ply")
        output_ply = os.path.join(temp_dir, f"{base_name}_decimated.ply")

        if not _export_ply(dup, input_ply):
            self.report({"ERROR"}, "PLY export failed")
            return {"CANCELLED"}

        results = mlw.run_multi_pass_decimation(
            input_path=input_ply,
            output_path=output_ply,
            passes=settings.decimation_passes,
            target_percentage=settings.target_percentage,
        )
        for r in results:
            if not r["success"]:
                self.report({"ERROR"}, f"Decimation pass {r['pass']} failed: {r.get('error')}")
                return {"CANCELLED"}

        current = _import_ply(output_ply)
        if not current:
            self.report({"ERROR"}, "Failed to import decimated PLY")
            return {"CANCELLED"}
        current.name = dup.name
        bpy.data.objects.remove(dup, do_unlink=True)

        for f in (input_ply, output_ply):
            try:
                os.remove(f)
            except OSError:
                pass

        step += 1
        self.report({"INFO"}, f"Step {step}/{total}: Decimated ({len(current.data.vertices)}v)")

        # --- Step 3: Optional AutoRemesher ---
        if settings.use_autoremesher:
            step += 1
            self.report({"INFO"}, f"Step {step}/{total}: AutoRemesher...")

            executable = arm.resolve_executable(settings.autoremesher_executable)
            exe_error = arm.validate_executable(executable)
            if exe_error:
                self.report({"ERROR"}, exe_error)
                return {"CANCELLED"}

            temp_dir = _get_temp_dir()
            base_name = bpy.path.clean_name(current.name)
            ar_in = os.path.join(temp_dir, f"{base_name}_ar_in.obj")
            ar_out = os.path.join(temp_dir, f"{base_name}_ar_out.obj")
            ar_report = os.path.join(temp_dir, f"{base_name}_ar_report.txt")

            if not _export_obj_for_tool(current, ar_in):
                self.report({"ERROR"}, "OBJ export for AutoRemesher failed")
                return {"CANCELLED"}

            cmd = arm.build_command(
                executable, Path(ar_in), Path(ar_out), Path(ar_report),
                target_quads=settings.ar_target_quads,
                edge_scaling=settings.ar_edge_scaling,
                sharp_edge=settings.ar_sharp_edge,
                smooth_normal=settings.ar_smooth_normal,
                adaptivity=settings.ar_adaptivity,
            )

            proc = subprocess.run(cmd, cwd=str(executable.parent), capture_output=True, text=True, check=False)
            if proc.returncode != 0:
                self.report({"ERROR"}, proc.stderr.strip() or "AutoRemesher failed")
                return {"CANCELLED"}
            if not os.path.isfile(ar_out):
                self.report({"ERROR"}, "AutoRemesher produced no output")
                return {"CANCELLED"}

            rem = current.name
            bpy.data.objects.remove(current, do_unlink=True)
            current = _import_obj_result(ar_out)
            if not current:
                self.report({"ERROR"}, "Failed to import AutoRemesher result")
                return {"CANCELLED"}
            current.name = rem

            for f in (ar_in, ar_out, ar_report):
                try:
                    os.remove(f)
                except OSError:
                    pass

            self.report({"INFO"}, f"Step {step}/{total}: AutoRemesher done ({len(current.data.vertices)}v)")

        # --- Step 4: Optional Baking ---
        if settings.use_baking:
            step += 1
            self.report({"INFO"}, f"Step {step}/{total}: Baking textures...")

            final_name = obj.name + settings.output_name_suffix
            result = baking.bake_textures(
                obj, current,
                texture_size=settings.bake_texture_size,
                final_name=final_name,
                uv_method=settings.bake_uv_method,
                uv_island_margin=settings.bake_uv_island_margin,
            )
            if result["success"]:
                self.report({"INFO"}, f"Step {step}/{total}: Baked {', '.join(result['images'])}")
            else:
                self.report({"WARNING"}, "Baking failed, continuing")

        # --- Final naming ---
        current.name = obj.name + settings.output_name_suffix
        self.report({"INFO"}, "Remi pipeline complete!")
        return {"FINISHED"}


# ============================================================
# Registration
# ============================================================

classes = [
    Remi_OT_ImportGLB,
    Remi_OT_SDFRemesh,
    Remi_OT_ApplyRemesh,
    Remi_OT_Decimate,
    Remi_OT_AutoRemesher,
    Remi_OT_BakeTextures,
    Remi_OT_FullPipeline,
]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
