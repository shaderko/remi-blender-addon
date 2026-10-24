#!/usr/bin/env python3
"""Exercise the same local worker as Blender, without starting Blender."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "integrations"))
from triposplat.client import GenerationJob


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--models", type=Path, default=ROOT / "models" / "triposplat")
    parser.add_argument("--output", type=Path, default=ROOT / "generations")
    parser.add_argument("--gaussians", type=int, default=65536, choices=(32768, 65536, 131072, 262144))
    parser.add_argument("--seed", type=int, default=20261023)
    args = parser.parse_args()
    job = GenerationJob.start(python=sys.executable, models=args.models, image=args.image,
                              output=args.output, gaussians=args.gaussians, seed=args.seed)
    print(f"Output and live worker.log: {job.directory}", flush=True)
    try:
        job.process.wait()
        print(job.result(), flush=True)
    finally:
        job.cancel()


if __name__ == "__main__":
    main()
