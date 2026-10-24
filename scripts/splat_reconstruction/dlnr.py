"""Adapter for an explicitly supplied DLNR source snapshot and checkpoint.

Uses its regular PyTorch correlation, not the optional compiled CUDA sampler.
Keep this in an external process: upstream uses generic 'core'/'nets' modules.
"""
from __future__ import annotations

from argparse import Namespace
from pathlib import Path
import sys

import numpy as np


class DLNRStereo:
    def __init__(self, source, checkpoint, device="cuda", iterations=16):
        source, checkpoint = Path(source).resolve(), Path(checkpoint).resolve()
        if not (source / "core" / "dlnr.py").is_file() or not checkpoint.is_file():
            raise ValueError("DLNR source/checkpoint missing; run scripts/prepare_stereo.py first")
        if iterations < 1:
            raise ValueError("Iterations must be positive")
        import torch
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA requested but not available")
        if "core" in sys.modules or "nets" in sys.modules:
            raise RuntimeError("DLNR must run in a fresh external process (module name collision)")
        sys.path.insert(0, str(source))
        from core.dlnr import DLNR
        from core.utils.utils import InputPadder
        args = Namespace(corr_implementation="reg", corr_levels=4, corr_radius=4,
                         hidden_dims=[128, 128, 128], mixed_precision=False,
                         n_downsample=2, n_gru_layers=3, shared_backbone=False,
                         slow_fast_gru=False)
        self.model = DLNR(args)
        # Tensor-only loading; never fall back to unrestricted pickle loading.
        state = torch.load(checkpoint, map_location="cpu", weights_only=True)
        state = {key.removeprefix("module."): value for key, value in state.items()}
        self.model.load_state_dict(state, strict=True)
        self.model.to(device).eval()
        self.device, self.iterations, self.padder = device, iterations, InputPadder

    def _predict(self, left, right):
        import torch
        images = [torch.from_numpy(np.ascontiguousarray(image)).permute(2, 0, 1)
                  .float()[None].to(self.device) for image in (left, right)]
        padder = self.padder(images[0].shape, divis_by=32)
        with torch.inference_mode():
            _, flow = self.model(*padder.pad(*images), iters=self.iterations, test_mode=True)
        return -padder.unpad(flow)[0, 0].cpu().numpy()

    def predict(self, left, right):
        if left.shape != right.shape or left.ndim != 3 or left.shape[2] != 3:
            raise ValueError("Expected matching HxWx3 RGB images")
        forward = self._predict(left, right)
        # DLNR expects left-to-right geometry: mirror and swap for right depth.
        backward = self._predict(right[:, ::-1], left[:, ::-1])[:, ::-1].copy()
        return forward, backward
