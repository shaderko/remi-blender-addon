"""Load a source checkout as `remi` regardless of its directory name.

Run before an older regression script with Blender's --python option. No
registration or user preferences are changed by this test-only bootstrap.
"""

import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if "remi" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["remi"] = module
    spec.loader.exec_module(module)
