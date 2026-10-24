"""V2: preserve the released flow before adapting it to append-only blocks.

The legacy transplant and its checkpoints are intentionally left untouched.
Train this module in FP32 under bf16 autocast, not with bf16 Adam parameters.
"""
from __future__ import annotations

import sys
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.checkpoint import checkpoint

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from model import LatentSeqMMFlowModel
from triposplat import FLOW_MODEL_ARGS, load_flow_model


LEVELS = (1024, 2048, 3072, 4096, 5120, 6144, 7168, 8192)
NOISE_RECIPE = "cuda_fp32_seed_1000_plus_mode_latent_then_camera_v1"


def paired_noise(seed: int, flow: LatentSeqMMFlowModel, device="cuda"):
    """Draw using the teacher recipe; store tensors for cross-GPU exact pairing.

    A seed alone does not guarantee the same CUDA random tensor on another GPU
    or PyTorch version. Do not assume this reconstructs historical cache noise.
    """
    generator = torch.Generator(device=device).manual_seed(seed)
    state = {"latent": torch.randn(
        1, flow.q_token_length, flow.in_channels, generator=generator,
        device=device, dtype=torch.float32)}
    if flow.cam_channels is not None:
        state["camera"] = torch.randn(
            1, 1, flow.cam_channels, generator=generator,
            device=device, dtype=torch.float32)
    return state


def stored_noise(item, device="cuda"):
    if "noise" not in item:
        raise ValueError("verified pilot must store full initial latent/camera noise")
    return {key: value.to(device=device, dtype=torch.float32)
            for key, value in item["noise"].items()}


class FaithfulFlowHierarchy(nn.Module):
    def __init__(self, flow: LatentSeqMMFlowModel, levels=LEVELS,
                 gradient_checkpointing=False):
        super().__init__()
        levels = tuple(int(x) for x in levels)
        if not levels or levels[-1] != flow.q_token_length:
            raise ValueError("levels must end at the flow token count")
        if any(b <= a for a, b in zip((0,) + levels[:-1], levels)):
            raise ValueError("levels must be strictly increasing and positive")
        self.flow = flow
        self.levels = levels
        self.gradient_checkpointing = gradient_checkpointing
        # Zero initialization preserves the copied computation at construction.
        self.role_embed = nn.Embedding(2, flow.model_channels)
        self.level_embed = nn.Embedding(len(levels), flow.model_channels)
        nn.init.zeros_(self.role_embed.weight)
        nn.init.zeros_(self.level_embed.weight)

    @property
    def device(self):
        return self.flow.device

    @property
    def dtype(self):
        return self.flow.dtype

    def _block(self, block, repo, h, mod):
        def run(x, modulation):
            return block(x, mod=modulation, rotary_emb=repo(x))

        if self.gradient_checkpointing and self.training and torch.is_grad_enabled():
            return checkpoint(run, h, mod, use_reentrant=False)
        return run(h, mod)

    def encode_context(self, cond):
        d = self.flow.dtype
        h = self.flow.cond_embedder(cond["feature1"].to(d))
        if self.flow.cond_embedder2 is not None:
            h = h + self.flow.cond_embedder2(cond["feature2"].to(d))
        for block, repo in zip(self.flow.context_refiner, self.flow.context_repo_layers):
            h = self._block(block, repo, h, None)
        return h

    def velocity(self, x_t, t, cond, *, level=None, prefix_length=0,
                 context=None):
        """Teacher-compatible v(x,t,c), including camera and final modulation.

        t uses the released model's [0,1000] units. With level=None and a full
        latent sequence this must match LatentSeqMMFlowModel.forward.
        """
        flow = self.flow
        d = flow.dtype
        z = x_t["latent"].to(d)
        length = z.shape[1]
        if not 0 < length <= flow.q_token_length:
            raise ValueError("invalid latent length")
        # flow.pos_pe is intentionally a plain FP32 tensor, not a buffer that
        # Module.bfloat16() could round before the high-frequency Fourier map.
        positions = flow.pos_pe[:, :length].to(device=z.device, dtype=torch.float32)
        h_x = flow.input_layer(z)
        h_cond = self.encode_context(cond) if context is None else context
        t_emb = flow.t_embedder(t)
        t_mod = flow.adaLN_modulation(t_emb) if flow.share_mod else t_emb
        h_x = h_x + flow.pos_embedder(positions).to(d)
        if level is not None:
            if not 0 <= prefix_length < length:
                raise ValueError("prefix must leave at least one query token")
            role = (torch.arange(length, device=z.device) >= prefix_length).long()
            h_x = h_x + self.role_embed(role)[None].to(d)
            h_x = h_x + self.level_embed.weight[level][None, None].to(d)

        for block, repo in zip(flow.noise_refiner, flow.noise_repo_layers):
            h_x = self._block(block, repo, h_x, t_mod)
        if flow.cam_channels is not None:
            h_cam = flow.cam_refiner(x_t["camera"].to(d))
        h = torch.cat([h_x, h_cond], dim=1)
        if flow.cam_channels is not None:
            h = torch.cat([h, h_cam], dim=1)
        for block, repo in zip(flow.blocks, flow.repo_layers):
            h = self._block(block, repo, h, t_mod)

        h_x = F.layer_norm(h[:, :length].float(), h.shape[-1:]).to(d)
        if flow.cam_channels is not None:
            h_cam = F.layer_norm(h[:, -x_t["camera"].shape[1]:].float(),
                                 h.shape[-1:]).to(d)
        if flow.use_shift_table:
            shift, scale = (flow.shift_table + t_emb.unsqueeze(1)).chunk(2, dim=1)
            h_x = h_x * (1 + scale) + shift
            if flow.cam_channels is not None:
                h_cam = h_cam * (1 + scale) + shift
        result = {"latent": flow.out_layer(h_x)}
        if flow.cam_channels is not None:
            result["camera"] = flow.cam_out_layer(h_cam)
        return result

    def forward(self, x_t, t, cond):
        """Allows the unmodified released Euler sampler to check parity."""
        return self.velocity(x_t, t, cond)

    def predict(self, cond, noise, *, level=None, prefix=None, context=None):
        """One learned endpoint prediction, either full latent or next block.

        x - v(x,1000,c) preserves the velocity head's parameterization. Before
        distillation it is only a denoised estimate, NOT an exact ODE solution.
        Hierarchical mode is a new task: clean prefix + paired Gaussian queries.
        """
        if level is None:
            if prefix is not None:
                raise ValueError("full-latent prediction does not take a prefix")
            start, end = 0, self.flow.q_token_length
            latent = noise["latent"]
        else:
            start = 0 if level == 0 else self.levels[level - 1]
            end = self.levels[level]
            if start and (prefix is None or prefix.shape[1] != start):
                raise ValueError("incorrect prefix length")
            if not start and prefix is not None:
                raise ValueError("the first level cannot take a prefix")
            query = noise["latent"][:, start:end]
            latent = torch.cat([prefix.float(), query], dim=1) if start else query
        state = {"latent": latent}
        if self.flow.cam_channels is not None:
            state["camera"] = noise["camera"]
        t = torch.full((latent.shape[0],), 1000.0, device=latent.device)
        velocity = self.velocity(state, t, cond, level=level,
                                 prefix_length=start, context=context)
        return noise["latent"][:, start:end].float() - velocity["latent"][:, start:end].float()

    @torch.no_grad()
    def generate(self, cond, noise, hierarchy=True):
        context = self.encode_context(cond)
        if not hierarchy:
            return self.predict(cond, noise, context=context)
        prefix = None
        for k in range(len(self.levels)):
            pred = self.predict(cond, noise, level=k, prefix=prefix, context=context)
            prefix = pred if prefix is None else torch.cat([prefix, pred], dim=1)
        return prefix


def build_model(checkpoint_path=None, *, pretrained=True, device="cpu",
                gradient_checkpointing=True, levels=LEVELS):
    if pretrained:
        if checkpoint_path is None:
            raise ValueError("a pretrained checkpoint is required")
        flow = load_flow_model(str(checkpoint_path), device="cpu", dtype=torch.float32)
    else:
        flow = LatentSeqMMFlowModel(**FLOW_MODEL_ARGS)
    return FaithfulFlowHierarchy(flow, levels=levels,
                                 gradient_checkpointing=gradient_checkpointing).to(device)
