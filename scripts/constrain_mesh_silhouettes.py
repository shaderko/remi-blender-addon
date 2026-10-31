"""CLI for the packaged training-silhouette consistency gate."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integrations/splat_mesh"))
from remi_surface.silhouette import constrain, main, silhouette_support

if __name__ == "__main__":
    main()
