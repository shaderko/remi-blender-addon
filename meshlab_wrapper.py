"""
PyMeshLab integration for Remi.
Handles installation of pymeshlab into Blender's Python environment
and running quadric edge collapse decimation.
"""

import os
import sys
import subprocess
import importlib
import bpy

# ---------------------------------------------------------------------------
# PyMeshLab Installation Management
# ---------------------------------------------------------------------------


def _try_add_user_site_packages():
    """Try adding the user site-packages directory to sys.path.
    This helps when pymeshlab was pip-installed globally but Blender
    only looks at its bundled site-packages."""
    import site
    try:
        user_sp = site.getusersitepackages()
        if user_sp and user_sp not in sys.path:
            sys.path.insert(0, user_sp)
            return True
    except Exception:
        pass
    return False


def _install_to_blender_python() -> bool:
    """Install pymeshlab into Blender's bundled Python site-packages."""
    blender_python = sys.executable
    try:
        subprocess.check_call(
            [blender_python, "-m", "pip", "install", "pymeshlab", "--quiet"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        importlib.invalidate_caches()
        return True
    except Exception as e:
        print(f"Remi: pip install failed: {e}")
        return False


def ensure_pymeshlab() -> bool:
    """Ensure PyMeshLab is importable in Blender's Python environment.
    Returns True if available (already installed or just installed)."""
    # Attempt 1: Direct import
    try:
        import pymeshlab  # noqa: F401
        return True
    except ImportError:
        pass

    # Attempt 2: Add user site-packages and retry
    _try_add_user_site_packages()
    try:
        import pymeshlab  # noqa: F401
        return True
    except ImportError:
        pass

    # Attempt 3: Install via pip into Blender's Python
    if _install_to_blender_python():
        try:
            import pymeshlab  # noqa: F401
            return True
        except ImportError:
            pass

    return False


# ---------------------------------------------------------------------------
# Decimation
# ---------------------------------------------------------------------------

def run_quadric_decimation(
    input_path: str,
    output_path: str,
    target_percentage: float = 0.5,
    preserve_boundary: bool = False,
    preserve_normal: bool = False,
    preserve_detail: bool = False,
    preserve_texture: bool = False,
    optimal_placement: bool = True,
    autoclean: bool = True,
    output_format: str = "ply",
) -> dict:
    """Run a single pass of Quadric Edge Collapse Decimation via PyMeshLab.

    IMPORTANT: PyMeshLab OBJ export can be extremely slow for large meshes.
    Use PLY format (binary) for intermediate files whenever possible.

    Args:
        input_path: Path to input mesh file (OBJ or PLY).
        output_path: Path to write decimated file.
        target_percentage: Fraction of faces to keep (0.01-0.99).
        preserve_boundary: Whether to preserve mesh boundaries.
        preserve_normal: Whether to avoid face flipping.
        optimal_placement: Whether to place vertices at optimal positions.
        autoclean: Whether to clean up after simplification.
        preserve_texture: Use MeshLab's texture-aware decimation filter. The
                          input and output must be OBJ files so UVs and texture
                          references can be retained.
        output_format: Output format ("ply" or "obj"). Default "ply" is
                       significantly faster.

    Returns:
        dict with keys: success (bool), input_faces (int), output_faces (int),
                        error (str, optional)
    """
    try:
        import pymeshlab
    except ImportError:
        return {"success": False, "error": "PyMeshLab not installed"}

    result = {"success": True, "error": None}

    if preserve_texture and output_format.lower() != "obj":
        return {
            "success": False,
            "error": "Texture-preserving decimation requires OBJ input/output",
        }

    try:
        ms = pymeshlab.MeshSet()
        ms.load_new_mesh(input_path)

        # Record input face count
        input_faces = ms.current_mesh().face_number()
        result["input_faces"] = input_faces

        # The texture-aware filter preserves UV parametrization. Unlike the
        # regular filter, it does not accept the ``autoclean`` parameter.
        filter_name = (
            "meshing_decimation_quadric_edge_collapse_with_texture"
            if preserve_texture
            else "meshing_decimation_quadric_edge_collapse"
        )
        filter_args = {
            "targetperc": target_percentage,
            "preserveboundary": preserve_boundary,
            "preservenormal": preserve_normal or preserve_detail,
            "planarquadric": preserve_detail,
            "optimalplacement": optimal_placement,
        }
        if not preserve_texture:
            filter_args["autoclean"] = autoclean
        ms.apply_filter(filter_name, **filter_args)

        # Record output face count
        output_faces = ms.current_mesh().face_number()
        result["output_faces"] = output_faces

        # ``binary`` is a PLY-only save parameter. Passing it while writing an
        # OBJ makes PyMeshLab reject the export.
        if output_format.lower() == "ply":
            ms.save_current_mesh(output_path, binary=True)
        else:
            ms.save_current_mesh(output_path)

    except Exception as e:
        result["success"] = False
        result["error"] = str(e)

    return result


def run_multi_pass_decimation(
    input_path: str,
    output_path: str,
    passes: int = 1,
    target_percentage: float = 0.5,
    preserve_boundary: bool = False,
    preserve_normal: bool = False,
    preserve_detail: bool = False,
    preserve_texture: bool = False,
    optimal_placement: bool = True,
    autoclean: bool = True,
) -> list:
    """Run multiple sequential passes of quadric edge collapse decimation.

    Each pass reads from the previous pass's output, reducing by target_percentage
    each time. So 3 passes at 50% = 12.5% of original.

    Uses PLY format for intermediate files (fast I/O).
    The final output format is inferred from output_path extension.

    Args:
        input_path: Path to input mesh file.
        output_path: Final output path.
        passes: Number of sequential decimation passes.
        target_percentage: Fraction for EACH pass.

    Returns:
        list of dict results, one per pass.
    """
    # Textured meshes need OBJ/MTL throughout: PLY cannot store the UV/image
    # references required by MeshLab's texture-aware decimator.
    output_ext = os.path.splitext(output_path)[1].lower()
    final_format = "obj" if output_ext == ".obj" else "ply"
    if preserve_texture and final_format != "obj":
        return [{
            "success": False,
            "error": "Texture-preserving decimation requires an OBJ output path",
            "pass": 1,
        }]
    intermediate_format = "obj" if preserve_texture else "ply"

    results = []
    current_input = input_path

    for i in range(passes):
        if i == passes - 1:
            # Last pass → final output
            current_output = output_path
        else:
            # Intermediate passes must retain texture data when requested.
            base, _ = os.path.splitext(output_path)
            current_output = f"{base}_pass{i+1:02d}.{intermediate_format}"

        pass_result = run_quadric_decimation(
            input_path=current_input,
            output_path=current_output,
            target_percentage=target_percentage,
            preserve_boundary=preserve_boundary,
            preserve_normal=preserve_normal,
            preserve_detail=preserve_detail,
            preserve_texture=preserve_texture,
            optimal_placement=optimal_placement,
            autoclean=autoclean,
            output_format=final_format if i == passes - 1 else intermediate_format,
        )
        pass_result["pass"] = i + 1
        results.append(pass_result)

        # If pass failed, stop
        if not pass_result["success"]:
            break

        # The next pass has loaded this generated file already, so earlier
        # intermediates are no longer needed. OBJ's sidecar MTL is safe to
        # remove too; texture images are referenced, not copied here.
        if i > 0:
            try:
                os.remove(current_input)
            except OSError:
                pass
            if current_input.lower().endswith(".obj"):
                try:
                    os.remove(os.path.splitext(current_input)[0] + ".mtl")
                except OSError:
                    pass

        # Next input = this pass's output
        current_input = current_output

    return results
