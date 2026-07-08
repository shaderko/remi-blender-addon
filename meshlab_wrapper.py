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

    try:
        ms = pymeshlab.MeshSet()
        ms.load_new_mesh(input_path)

        # Record input face count
        input_faces = ms.current_mesh().face_number()
        result["input_faces"] = input_faces

        # Apply decimation
        ms.apply_filter(
            "meshing_decimation_quadric_edge_collapse",
            targetperc=target_percentage,
            preserveboundary=preserve_boundary,
            preservenormal=preserve_normal,
            optimalplacement=optimal_placement,
            autoclean=autoclean,
        )

        # Record output face count
        output_faces = ms.current_mesh().face_number()
        result["output_faces"] = output_faces

        # Save — use binary=True for PLY (dramatically faster than OBJ)
        use_binary = output_format.lower() == "ply"
        ms.save_current_mesh(output_path, binary=use_binary)

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
    # Determine intermediate format from output_path extension
    output_ext = os.path.splitext(output_path)[1].lower()
    final_format = "ply" if output_ext == ".ply" else "ply"  # default PLY

    results = []
    current_input = input_path

    for i in range(passes):
        if i == passes - 1:
            # Last pass → final output
            current_output = output_path
        else:
            # Intermediate pass → temp PLY file
            base, _ = os.path.splitext(output_path)
            current_output = f"{base}_pass{i+1:02d}.ply"

        pass_result = run_quadric_decimation(
            input_path=current_input,
            output_path=current_output,
            target_percentage=target_percentage,
            preserve_boundary=preserve_boundary,
            preserve_normal=preserve_normal,
            optimal_placement=optimal_placement,
            autoclean=autoclean,
            output_format="ply",
        )
        pass_result["pass"] = i + 1
        results.append(pass_result)

        # If pass failed, stop
        if not pass_result["success"]:
            break

        # Next input = this pass's output
        current_input = current_output

    return results
