"""Research compatibility imports for the packaged reconstruction geometry."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "integrations/splat_mesh"))
from remi_surface.geometry import archive_bounds, look_at, make_rig, stereo_cameras
