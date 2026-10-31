"""Research compatibility imports for shared geometry diagnostics."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "integrations/splat_mesh"))
from remi_surface.metrics import image_metrics, topology_metrics
