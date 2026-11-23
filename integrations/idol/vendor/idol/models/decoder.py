from typing import List

import torch
import torch.nn as nn
import torch.nn.functional as F

from ..gaussian import Gaussian
from .layers import (ModulatedTransformerCrossOnlyBlock, PcdAbsolutePositionEmbedderv2, SinusoidalEmbedder,
                     TransformerCrossBlock, convert_module_to_f16)


class OctreeProbabilityFixedlenDecoder(nn.Module):
    def __init__(self, model_channels: int, cond_channels: int, num_blocks: int, num_heads: int,
                 mlp_ratio: float = 4.0):
        super().__init__()
        self.dtype = torch.float16
        self.in_proj = nn.Linear(3, model_channels)
        self.pos_embedder = PcdAbsolutePositionEmbedderv2(channels=model_channels, in_channels=3)
        self.input_layer = nn.Linear(model_channels, model_channels)
        self.l_embedder = SinusoidalEmbedder(model_channels, max_period=1024, two_pi=True)
        self.adaLN_modulation = nn.Sequential(nn.SiLU(), nn.Linear(model_channels, 6 * model_channels, bias=True))
        self.blocks = nn.ModuleList([
            ModulatedTransformerCrossOnlyBlock(model_channels, ctx_channels=cond_channels, num_heads=num_heads,
                                               mlp_ratio=mlp_ratio)
            for _ in range(num_blocks)
        ])
        self.out_proj = nn.Linear(model_channels, 2 ** 3)
        self.blocks.apply(convert_module_to_f16)

    def forward(self, x: torch.Tensor, level: torch.Tensor, cond: torch.Tensor) -> torch.Tensor:
        """x: cell centres [B, L, 3] in [0, 1]; level: the cells' octree resolution [B]; cond: latent [B, Lc, C].
        Returns the logits [B, L, 8] of how each cell's points split among its 8 children."""
        B, L, _ = x.shape
        h = self.in_proj(x) + self.pos_embedder(x.view(-1, 3)).view(B, L, -1)
        h = self.input_layer(h)
        level_emb = self.adaLN_modulation(self.l_embedder(level.type(h.dtype))).type(self.dtype)
        out = h.type(self.dtype)
        context = cond.type(self.dtype)
        for block in self.blocks:
            out = block(out, level_emb, context)
        out = out.type(h.dtype).type(cond.dtype)
        return self.out_proj(F.layer_norm(out, out.shape[-1:]))

    @staticmethod
    @torch.no_grad()
    def sample(model, cond: torch.Tensor, num_points: int, level: int) -> torch.Tensor:
        """Place num_points anchor points [B, num_points, 3] in [0, 1]^3, descending the octree to ``level``."""
        B = cond.shape[0]
        device = cond.device
        child_offset = torch.tensor([[i, j, k] for k in [0, 1] for j in [0, 1] for i in [0, 1]], dtype=torch.long,
                                    device=device)
        prev_coords_int = torch.zeros(B, 1, 3, dtype=torch.long, device=device)
        prev_counts = torch.full((B, 1), num_points, dtype=torch.long, device=device)
        batch_indices_range = torch.arange(B, device=device).unsqueeze(1)

        for l in range(1, level + 1):
            res_p = 1 << (l - 1)
            parent_coords_norm = (prev_coords_int.to(torch.float32) + 0.5) / res_p
            res_tensor = torch.full((B,), 1 << l, dtype=torch.long, device=device)
            pred_probs = torch.softmax(model(parent_coords_norm, res_tensor, cond), dim=-1)    # [B, Np, 8]
            sampled = systematic_sample(pred_probs, prev_counts).flatten(1, 2)                   # [B, Np*8]
            child_coords_int = (prev_coords_int[:, :, None, :] * 2 + child_offset[None, None, :, :]).flatten(1, 2)

            # keep the children that received points, packed to the front
            mask = sampled > 0
            max_valid = mask.sum(dim=1).max().item()
            valid_scatter_indices = (mask.cumsum(dim=1) - 1)[mask]
            valid_batch_indices = batch_indices_range.expand_as(mask)[mask]
            prev_coords_int = torch.zeros(B, max_valid, 3, dtype=child_coords_int.dtype, device=device)
            prev_coords_int[valid_batch_indices, valid_scatter_indices] = child_coords_int[mask]
            prev_counts = torch.zeros(B, max_valid, dtype=sampled.dtype, device=device)
            prev_counts[valid_batch_indices, valid_scatter_indices] = sampled[mask]

        # each point lands at a uniformly random position inside its leaf cell
        coords_int = torch.repeat_interleave(prev_coords_int.flatten(0, 1), prev_counts.flatten(0, 1),
                                             dim=0).reshape(B, num_points, -1)
        return (coords_int.to(torch.float32) + torch.rand_like(coords_int, dtype=torch.float32)) / (1 << level)


@torch.no_grad()
def systematic_sample(probs: torch.Tensor, counts: torch.Tensor) -> torch.Tensor:
    """Split counts[...] items among the P bins of probs[..., P] by systematic resampling -> [..., P] counts."""
    batch_shape = counts.shape
    B = counts.numel()
    P = probs.size(-1)
    device = probs.device
    probs = probs.view(B, P)
    counts = counts.view(B)

    # normalise, with a uniform fallback for zero-sum rows
    probs = probs.to(torch.float32).clamp_min_(0)
    row_sums = probs.sum(1, keepdim=True)
    zero_mask = row_sums.eq(0)
    probs = probs / row_sums.clamp_min_(1)
    if zero_mask.any():
        probs = probs.clone()
        probs[zero_mask.expand_as(probs)] = 1.0 / P

    counts = counts.to(device=device, dtype=torch.long)
    out = torch.zeros(B, P, dtype=torch.long, device=device)
    cdf = probs.cumsum(dim=1).clamp(max=1.0 - 1e-12)
    unique_n, inv = counts.unique(sorted=False, return_inverse=True)
    for index, n in enumerate(unique_n.tolist()):
        if n == 0:
            continue
        rows = (inv == index).nonzero(as_tuple=False).squeeze(1)
        r = rows.numel()
        u0 = torch.rand(r, 1, device=device) / float(n)                                   # random offset in [0, 1/n)
        grid = torch.arange(n, device=device, dtype=torch.float32)[None, :] / float(n)    # stratified points
        us = (u0 + grid).clamp(max=1.0 - 1e-12)
        idx = torch.searchsorted(cdf.index_select(0, rows), us).clamp_max(P - 1)
        buf = torch.zeros(r, P, dtype=torch.float32, device=device)
        buf.scatter_add_(1, idx, torch.ones_like(idx, dtype=buf.dtype))
        out.index_copy_(0, rows, buf.to(torch.long))
    return out.view(*batch_shape, P)


class GaussianFixedlenDecoder(nn.Module):
    def __init__(self, in_channels: int, model_channels: int, cond_channels: int, num_blocks: int, num_heads: int,
                 representation_config: dict, mlp_ratio: float = 4.0):
        super().__init__()
        self.dtype = torch.float16
        self.rep_config = representation_config
        per_anchor = representation_config['num_gaussians']
        # output channels per anchor: offset (3), colour (3), scale (3), rotation (4), opacity (1), offset scale (1)
        self.layout = {
            '_xyz': (3,),
            '_features_dc': (1, 3),
            '_scaling': (3,),
            '_rotation': (4,),
            '_opacity': (1,),
            '_offset_scale': (1,),
        }
        start = 0
        self.ranges = {}
        for name, shape in self.layout.items():
            size = per_anchor * int(torch.tensor(shape).prod())
            self.ranges[name] = (start, start + size)
            start += size

        self.in_proj = nn.Linear(in_channels, model_channels)
        self.pos_embedder = PcdAbsolutePositionEmbedderv2(channels=model_channels, in_channels=3)
        self.input_layer = nn.Linear(model_channels, model_channels)
        self.blocks = nn.ModuleList([
            TransformerCrossBlock(model_channels, ctx_channels=cond_channels, num_heads=num_heads, mlp_ratio=mlp_ratio)
            for _ in range(num_blocks)
        ])
        self.out_proj = nn.Linear(model_channels, start)
        # fixed per-Gaussian offset pattern and the base offset scale, stored with the weights
        self.register_buffer('points_offset_perturbation', torch.zeros(per_anchor, 3))
        self.register_buffer('base_offset_scale', torch.zeros(()))
        self.blocks.apply(convert_module_to_f16)

    def forward(self, points: torch.Tensor, cond: torch.Tensor) -> torch.Tensor:
        """points: anchors [B, L, 3]; cond: latent [B, Lc, C] -> per-anchor outputs [B, L, 32 x 15]"""
        B, L, C = points.shape
        h = self.in_proj(points) + self.pos_embedder(points.view(-1, C)).view(B, L, -1)
        h = self.input_layer(h)
        out = h.type(self.dtype)
        context = cond.type(self.dtype)
        for block in self.blocks:
            out = block(out, context)
        out = out.type(h.dtype).type(cond.dtype)
        return self.out_proj(F.layer_norm(out, out.shape[-1:]))

    def _slice(self, features, name):
        start, end = self.ranges[name]
        return features[:, :, start:end].reshape(features.shape[0], -1, self.rep_config['num_gaussians'],
                                                 *self.layout[name])

    def to_representation(self, points: torch.Tensor, features: torch.Tensor) -> List[Gaussian]:
        config = self.rep_config
        offset_scale = F.softplus(self._slice(features, '_offset_scale') + self.base_offset_scale)
        offset = self._slice(features, '_xyz') * config['lr']['_xyz'] + self.points_offset_perturbation
        offset = torch.tanh(offset) * 0.5 * config['perturbe_size'] * offset_scale

        objects = []
        for i in range(features.shape[0]):
            raw = {name: (self._slice(features[i:i + 1], name)[0].flatten(0, 1) * config['lr'][name])
                   for name in ('_features_dc', '_scaling', '_rotation', '_opacity')}
            objects.append(Gaussian(
                raw_xyz=(offset[i] + points[i, :, None, :]).flatten(0, 1),
                features_dc=raw['_features_dc'], raw_scaling=raw['_scaling'], raw_rotation=raw['_rotation'],
                raw_opacity=raw['_opacity'], scaling_bias=config['scaling_bias'],
                opacity_bias=config['opacity_bias'], minimum_kernel_size=config['3d_filter_kernel_size']))
        return objects
