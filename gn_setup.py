"""
Geometry Nodes setup for Remi.
Creates a node group that does: Mesh → SDF Grid → (optional ops) → Grid → Mesh
"""

import bpy
from mathutils import Vector


def ensure_remi_node_group() -> bpy.types.GeometryNodeTree:
    """Create or return the shared Remi geometry node group.

    The node group exposes inputs via modifier interface:
      - Voxel Size      (float)
      - Grid Threshold  (float)
      - Hole Close Distance (float, optional)
      - Detail Recovery Distance (float, optional)
      - Fillet Radius   (float, optional)
      - Smooth Iterations (int, optional)

    Internal flow:
      Group Input
        → MeshToSDFGrid  (voxel_size)
        → [optional] SDFGridOffset(+distance) → SDFGridOffset(-distance)
        → [optional] SDFGridFillet (radius)
        → [optional] SDFGridLaplacian (iterations)
        → GridToMesh (threshold)
        → [optional] confidence-weighted projection to the input surface
        → Group Output
    """
    tree_name = "Remi_SDF_Remesh_v4"

    if tree_name in bpy.data.node_groups:
        return bpy.data.node_groups[tree_name]

    group = bpy.data.node_groups.new(name=tree_name, type="GeometryNodeTree")

    # --- Interface (Blender 5.1+ API using group.interface) ---
    # Inputs
    geo_in = group.interface.new_socket(
        name="Geometry", socket_type="NodeSocketGeometry", in_out="INPUT"
    )
    geo_in.description = "Input mesh geometry"

    voxel = group.interface.new_socket(
        name="Voxel Size", socket_type="NodeSocketFloat", in_out="INPUT"
    )
    voxel.default_value = 0.02
    voxel.min_value = 0.001
    voxel.max_value = 1.0
    voxel.subtype = "DISTANCE"
    voxel.description = "Voxel size for Mesh to SDF Grid"

    threshold = group.interface.new_socket(
        name="Grid Threshold", socket_type="NodeSocketFloat", in_out="INPUT"
    )
    threshold.default_value = 0.02
    threshold.min_value = -1.0
    threshold.max_value = 1.0
    threshold.description = "Isosurface threshold for Grid to Mesh"

    hole_close = group.interface.new_socket(
        name="Hole Close Distance", socket_type="NodeSocketFloat", in_out="INPUT"
    )
    hole_close.default_value = 0.0
    hole_close.min_value = 0.0
    hole_close.max_value = 10.0
    hole_close.subtype = "DISTANCE"
    hole_close.description = "SDF morphological closing distance used to bridge cracks"

    detail_recovery = group.interface.new_socket(
        name="Detail Recovery Distance", socket_type="NodeSocketFloat", in_out="INPUT"
    )
    detail_recovery.default_value = 0.0
    detail_recovery.min_value = 0.0
    detail_recovery.max_value = 10.0
    detail_recovery.subtype = "DISTANCE"
    detail_recovery.description = "Maximum distance for projecting repaired vertices back to the input surface"

    fillet_radius = group.interface.new_socket(
        name="Fillet Radius", socket_type="NodeSocketFloat", in_out="INPUT"
    )
    fillet_radius.default_value = 0.0
    fillet_radius.min_value = 0.0
    fillet_radius.max_value = 10.0
    fillet_radius.description = "Radius for SDF Grid Fillet (0 = disabled)"

    smooth_iters = group.interface.new_socket(
        name="Smooth Iterations", socket_type="NodeSocketInt", in_out="INPUT"
    )
    smooth_iters.default_value = 0
    smooth_iters.min_value = 0
    smooth_iters.max_value = 10
    smooth_iters.description = "SDF Laplacian smoothing iterations (0 = disabled)"

    # Output
    geo_out = group.interface.new_socket(
        name="Geometry", socket_type="NodeSocketGeometry", in_out="OUTPUT"
    )
    geo_out.description = "Output remeshed geometry"

    # --- Create nodes ---
    nodes = group.nodes
    links = group.links

    # Position constants
    X_START = -800
    X_STEP = 250
    Y_CENTER = 0

    # 1. Group Input
    node_input = nodes.new("NodeGroupInput")
    node_input.location = Vector((X_START, Y_CENTER))
    node_input.name = "Input"
    node_input.label = "Input"

    # 2. Mesh to SDF Grid
    node_m2sdf = nodes.new("GeometryNodeMeshToSDFGrid")
    node_m2sdf.location = Vector((X_START + X_STEP, Y_CENTER))
    node_m2sdf.name = "Mesh to SDF Grid"
    node_m2sdf.label = "Mesh to SDF Grid"

    # 3. SDF morphological closing: dilate, then erode by the same distance.
    node_dilate = nodes.new("GeometryNodeSDFGridOffset")
    node_dilate.location = Vector((X_START + 2 * X_STEP, Y_CENTER + 100))
    node_dilate.name = "SDF Hole Dilate"
    node_dilate.label = "SDF Hole Dilate"
    node_dilate.mute = True

    node_negate = nodes.new("ShaderNodeMath")
    node_negate.operation = "MULTIPLY"
    node_negate.inputs[1].default_value = -1.0
    node_negate.location = Vector((X_START + 2 * X_STEP, Y_CENTER - 130))
    node_negate.name = "Negate Hole Distance"
    node_negate.label = "Negate Hole Distance"

    node_erode = nodes.new("GeometryNodeSDFGridOffset")
    node_erode.location = Vector((X_START + 3 * X_STEP, Y_CENTER + 100))
    node_erode.name = "SDF Hole Erode"
    node_erode.label = "SDF Hole Erode"
    node_erode.mute = True

    # 4. SDF Grid Fillet (optional, muted by default)
    node_fillet = nodes.new("GeometryNodeSDFGridFillet")
    node_fillet.location = Vector((X_START + 4 * X_STEP, Y_CENTER + 80))
    node_fillet.name = "SDF Fillet"
    node_fillet.label = "SDF Fillet"
    node_fillet.mute = True

    # 5. SDF Grid Laplacian (optional, muted by default)
    node_smooth = nodes.new("GeometryNodeSDFGridLaplacian")
    node_smooth.location = Vector((X_START + 4 * X_STEP, Y_CENTER - 80))
    node_smooth.name = "SDF Smooth"
    node_smooth.label = "SDF Smooth"
    node_smooth.mute = True

    # 6. Grid to Mesh
    node_g2m = nodes.new("GeometryNodeGridToMesh")
    node_g2m.location = Vector((X_START + 5 * X_STEP, Y_CENTER))
    node_g2m.name = "Grid to Mesh"
    node_g2m.label = "Grid to Mesh"

    # 7. Detail recovery: softly project only nearby reconstructed vertices.
    node_position = nodes.new("GeometryNodeInputPosition")
    node_position.location = Vector((X_START + 6 * X_STEP, Y_CENTER - 260))
    node_position.name = "Repaired Position"

    node_proximity = nodes.new("GeometryNodeProximity")
    node_proximity.target_element = "FACES"
    node_proximity.location = Vector((X_START + 6 * X_STEP, Y_CENTER + 180))
    node_proximity.name = "Original Surface Proximity"
    node_proximity.label = "Original Surface Proximity"

    node_falloff = nodes.new("ShaderNodeMapRange")
    node_falloff.interpolation_type = "SMOOTHERSTEP"
    node_falloff.clamp = True
    node_falloff.inputs[1].default_value = 0.0
    node_falloff.inputs[3].default_value = 1.0
    node_falloff.inputs[4].default_value = 0.0
    node_falloff.location = Vector((X_START + 7 * X_STEP, Y_CENTER + 180))
    node_falloff.name = "Detail Confidence"
    node_falloff.label = "Detail Confidence"

    node_mix = nodes.new("ShaderNodeMix")
    node_mix.data_type = "VECTOR"
    node_mix.location = Vector((X_START + 8 * X_STEP, Y_CENTER + 60))
    node_mix.name = "Blend Recovered Detail"
    node_mix.label = "Blend Recovered Detail"

    node_set_position = nodes.new("GeometryNodeSetPosition")
    node_set_position.location = Vector((X_START + 9 * X_STEP, Y_CENTER))
    node_set_position.name = "Detail Recovery"
    node_set_position.label = "Detail Recovery"
    node_set_position.mute = True

    # 8. Group Output
    node_output = nodes.new("NodeGroupOutput")
    node_output.location = Vector((X_START + 10 * X_STEP, Y_CENTER))
    node_output.name = "Output"
    node_output.label = "Output"

    # --- Wire connections ---
    # Input → MeshToSDFGrid
    links.new(node_input.outputs["Geometry"], node_m2sdf.inputs["Mesh"])
    links.new(node_input.outputs["Voxel Size"], node_m2sdf.inputs["Voxel Size"])

    # MeshToSDFGrid → closing offsets → Fillet.
    links.new(node_m2sdf.outputs["SDF Grid"], node_dilate.inputs["Grid"])
    links.new(node_input.outputs["Hole Close Distance"], node_dilate.inputs["Distance"])
    links.new(node_dilate.outputs["Grid"], node_erode.inputs["Grid"])
    links.new(node_input.outputs["Hole Close Distance"], node_negate.inputs[0])
    links.new(node_negate.outputs[0], node_erode.inputs["Distance"])
    links.new(node_erode.outputs["Grid"], node_fillet.inputs["Grid"])
    links.new(node_input.outputs["Fillet Radius"], node_fillet.inputs["Iterations"])

    # Fillet → Smooth (both use "Grid" socket name)
    links.new(node_fillet.outputs["Grid"], node_smooth.inputs["Grid"])
    links.new(node_input.outputs["Smooth Iterations"], node_smooth.inputs["Iterations"])

    # Smooth → GridToMesh
    links.new(node_smooth.outputs["Grid"], node_g2m.inputs["Grid"])
    links.new(node_input.outputs["Grid Threshold"], node_g2m.inputs["Threshold"])

    # GridToMesh → confidence-weighted nearest-surface detail recovery.
    links.new(node_input.outputs["Geometry"], node_proximity.inputs["Geometry"])
    links.new(node_position.outputs["Position"], node_proximity.inputs["Sample Position"])
    links.new(node_proximity.outputs["Distance"], node_falloff.inputs["Value"])
    links.new(node_input.outputs["Detail Recovery Distance"], node_falloff.inputs["From Max"])
    links.new(node_falloff.outputs["Result"], node_mix.inputs["Factor"])
    links.new(node_position.outputs["Position"], node_mix.inputs[4])
    links.new(node_proximity.outputs["Position"], node_mix.inputs[5])
    links.new(node_g2m.outputs["Mesh"], node_set_position.inputs["Geometry"])
    links.new(node_proximity.outputs["Is Valid"], node_set_position.inputs["Selection"])
    links.new(node_mix.outputs[1], node_set_position.inputs["Position"])
    links.new(node_set_position.outputs["Geometry"], node_output.inputs["Geometry"])

    # Clean up node placement (deselect all)
    for node in group.nodes:
        node.select = False

    return group


def apply_remi_modifier(
    obj: bpy.types.Object,
    voxel_size: float = 0.02,
    hole_close_distance: float = 0.0,
    detail_recovery_distance: float = 0.0,
    fillet_radius: float = 0.0,
    smooth_iterations: int = 0,
) -> bpy.types.Modifier:
    """Apply the Remi geometry nodes modifier to an object.

    ``voxel_size`` controls sampling resolution and restores Remi's original
    one-voxel Grid to Mesh threshold, which consolidates thin/open geometry.
    """
    group = ensure_remi_node_group()

    for mod in obj.modifiers:
        if mod.type == "NODES" and mod.node_group == group:
            obj.modifiers.remove(mod)

    mod = obj.modifiers.new(name="Remi_SDF_Remesh", type="NODES")
    mod.node_group = group

    # Shared value for both parameters
    param_map = {
        "Voxel Size": voxel_size,
        "Grid Threshold": voxel_size,
        "Hole Close Distance": hole_close_distance,
        "Detail Recovery Distance": detail_recovery_distance,
        "Fillet Radius": fillet_radius,
        "Smooth Iterations": smooth_iterations,
    }
    # Build {display_name: identifier} from the interface
    name_to_id = {}
    for item in group.interface.items_tree:
        if hasattr(item, "socket_type") and hasattr(item, "identifier"):
            name_to_id[item.name] = item.identifier

    for name, value in param_map.items():
        if name in name_to_id:
            mod[name_to_id[name]] = value
        elif name in mod:
            mod[name] = value

    # Enable/disable optional nodes based on parameters
    _set_node_mute(group, "SDF Hole Dilate", hole_close_distance <= 0)
    _set_node_mute(group, "SDF Hole Erode", hole_close_distance <= 0)
    _set_node_mute(group, "Detail Recovery", detail_recovery_distance <= 0)
    _set_node_mute(group, "SDF Fillet", fillet_radius <= 0)
    _set_node_mute(group, "SDF Smooth", smooth_iterations <= 0)

    return mod


def _set_node_mute(group: bpy.types.GeometryNodeTree, node_name: str, mute: bool):
    """Set the mute state of a node in the geometry node group."""
    for node in group.nodes:
        if node.name == node_name or node.label == node_name:
            node.mute = mute
            return
