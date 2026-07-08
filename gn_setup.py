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
      - Fillet Radius   (float, optional)
      - Smooth Iterations (int, optional)

    Internal flow:
      Group Input
        → MeshToSDFGrid  (voxel_size)
        → [optional] SDFGridFillet (radius)
        → [optional] SDFGridLaplacian (iterations)
        → GridToMesh (threshold)
        → Group Output
    """
    tree_name = "Remi_SDF_Remesh"

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
    threshold.default_value = 0.0
    threshold.min_value = -1.0
    threshold.max_value = 1.0
    threshold.description = "Isosurface threshold for Grid to Mesh"

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

    # 3. SDF Grid Fillet (optional, muted by default)
    node_fillet = nodes.new("GeometryNodeSDFGridFillet")
    node_fillet.location = Vector((X_START + 2 * X_STEP, Y_CENTER + 80))
    node_fillet.name = "SDF Fillet"
    node_fillet.label = "SDF Fillet"
    node_fillet.mute = True

    # 4. SDF Grid Laplacian (optional, muted by default)
    node_smooth = nodes.new("GeometryNodeSDFGridLaplacian")
    node_smooth.location = Vector((X_START + 2 * X_STEP, Y_CENTER - 80))
    node_smooth.name = "SDF Smooth"
    node_smooth.label = "SDF Smooth"
    node_smooth.mute = True

    # 5. Grid to Mesh
    node_g2m = nodes.new("GeometryNodeGridToMesh")
    node_g2m.location = Vector((X_START + 3 * X_STEP, Y_CENTER))
    node_g2m.name = "Grid to Mesh"
    node_g2m.label = "Grid to Mesh"

    # 6. Group Output
    node_output = nodes.new("NodeGroupOutput")
    node_output.location = Vector((X_START + 4 * X_STEP, Y_CENTER))
    node_output.name = "Output"
    node_output.label = "Output"

    # --- Wire connections ---
    # Input → MeshToSDFGrid
    links.new(node_input.outputs["Geometry"], node_m2sdf.inputs["Mesh"])
    links.new(node_input.outputs["Voxel Size"], node_m2sdf.inputs["Voxel Size"])

    # MeshToSDFGrid → Fillet (output "SDF Grid" → input "Grid")
    links.new(node_m2sdf.outputs["SDF Grid"], node_fillet.inputs["Grid"])
    links.new(node_input.outputs["Fillet Radius"], node_fillet.inputs["Iterations"])

    # Fillet → Smooth (both use "Grid" socket name)
    links.new(node_fillet.outputs["Grid"], node_smooth.inputs["Grid"])
    links.new(node_input.outputs["Smooth Iterations"], node_smooth.inputs["Iterations"])

    # Smooth → GridToMesh
    links.new(node_smooth.outputs["Grid"], node_g2m.inputs["Grid"])
    links.new(node_input.outputs["Grid Threshold"], node_g2m.inputs["Threshold"])

    # GridToMesh → Output
    links.new(node_g2m.outputs["Mesh"], node_output.inputs["Geometry"])

    # Clean up node placement (deselect all)
    for node in group.nodes:
        node.select = False

    return group


def apply_remi_modifier(
    obj: bpy.types.Object,
    voxel_size: float = 0.02,
    grid_threshold: float = 0.0,
    fillet_radius: float = 0.0,
    smooth_iterations: int = 0,
) -> bpy.types.Modifier:
    """Apply the Remi geometry nodes modifier to an object."""
    group = ensure_remi_node_group()

    for mod in obj.modifiers:
        if mod.type == "NODES" and mod.node_group == group:
            obj.modifiers.remove(mod)

    mod = obj.modifiers.new(name="Remi_SDF_Remesh", type="NODES")
    mod.node_group = group

    # --- Set modifier parameter values ---
    # In Blender 5.1, the modifier stores socket values under internal
    # identifiers like "Socket_0", "Socket_1" etc.  Setting via display
    # name ("Voxel Size") silently fails, so we map names -> identifiers
    # from the node group interface.
    param_map = {
        "Voxel Size": voxel_size,
        "Grid Threshold": grid_threshold,
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
    _set_node_mute(group, "SDF Fillet", fillet_radius <= 0)
    _set_node_mute(group, "SDF Smooth", smooth_iterations <= 0)

    return mod


def _set_node_mute(group: bpy.types.GeometryNodeTree, node_name: str, mute: bool):
    """Set the mute state of a node in the geometry node group."""
    for node in group.nodes:
        if node.name == node_name or node.label == node_name:
            node.mute = mute
            return
