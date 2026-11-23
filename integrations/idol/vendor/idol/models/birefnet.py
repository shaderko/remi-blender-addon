import math

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torchvision.ops import deform_conv2d


# -- Swin Transformer (Swin-Large preset) -------------------------------------

class _SwinMlp(nn.Module):
    def __init__(self, in_features, hidden_features=None, out_features=None):
        super().__init__()
        hidden_features = hidden_features or in_features
        out_features = out_features or in_features
        self.fc1 = nn.Linear(in_features, hidden_features)
        self.act = nn.GELU()
        self.fc2 = nn.Linear(hidden_features, out_features)

    def forward(self, x):
        return self.fc2(self.act(self.fc1(x)))


def _window_partition(x, window_size):
    B, H, W, C = x.shape
    x = x.view(B, H // window_size, window_size, W // window_size, window_size, C)
    return x.permute(0, 1, 3, 2, 4, 5).contiguous().view(-1, window_size, window_size, C)


def _window_reverse(windows, window_size, H, W):
    B = int(windows.shape[0] / (H * W / window_size / window_size))
    x = windows.view(B, H // window_size, W // window_size, window_size, window_size, -1)
    return x.permute(0, 1, 3, 2, 4, 5).contiguous().view(B, H, W, -1)


class _WindowAttention(nn.Module):
    def __init__(self, dim, window_size, num_heads):
        super().__init__()
        self.dim = dim
        self.window_size = window_size
        self.num_heads = num_heads
        head_dim = dim // num_heads
        self.scale = head_dim ** -0.5

        self.relative_position_bias_table = nn.Parameter(
            torch.zeros((2 * window_size[0] - 1) * (2 * window_size[1] - 1), num_heads))
        coords_h = torch.arange(window_size[0])
        coords_w = torch.arange(window_size[1])
        coords = torch.stack(torch.meshgrid([coords_h, coords_w], indexing="ij"))
        coords_flatten = torch.flatten(coords, 1)
        relative_coords = coords_flatten[:, :, None] - coords_flatten[:, None, :]
        relative_coords = relative_coords.permute(1, 2, 0).contiguous()
        relative_coords[:, :, 0] += window_size[0] - 1
        relative_coords[:, :, 1] += window_size[1] - 1
        relative_coords[:, :, 0] *= 2 * window_size[1] - 1
        self.register_buffer("relative_position_index", relative_coords.sum(-1))

        self.qkv = nn.Linear(dim, dim * 3, bias=True)
        self.proj = nn.Linear(dim, dim)

    def forward(self, x, mask=None):
        B_, N, C = x.shape
        qkv = self.qkv(x).reshape(B_, N, 3, self.num_heads, C // self.num_heads).permute(2, 0, 3, 1, 4)
        q, k, v = qkv[0], qkv[1], qkv[2]
        q = q * self.scale
        attn = q @ k.transpose(-2, -1)
        bias = self.relative_position_bias_table[self.relative_position_index.view(-1)].view(
            self.window_size[0] * self.window_size[1],
            self.window_size[0] * self.window_size[1], -1)
        attn = attn + bias.permute(2, 0, 1).contiguous().unsqueeze(0)
        if mask is not None:
            nW = mask.shape[0]
            attn = attn.view(B_ // nW, nW, self.num_heads, N, N) + mask.unsqueeze(1).unsqueeze(0)
            attn = attn.view(-1, self.num_heads, N, N)
        attn = attn.softmax(dim=-1)
        x = (attn @ v).transpose(1, 2).reshape(B_, N, C)
        return self.proj(x)


class _SwinBlock(nn.Module):
    def __init__(self, dim, num_heads, window_size, shift_size, mlp_ratio=4.0):
        super().__init__()
        self.dim = dim
        self.window_size = window_size
        self.shift_size = shift_size
        self.norm1 = nn.LayerNorm(dim)
        self.attn = _WindowAttention(dim, (window_size, window_size), num_heads)
        self.norm2 = nn.LayerNorm(dim)
        self.mlp = _SwinMlp(dim, int(dim * mlp_ratio))
        self.H = None
        self.W = None

    def forward(self, x, mask_matrix):
        B, L, C = x.shape
        H, W = self.H, self.W
        shortcut = x
        x = self.norm1(x).view(B, H, W, C)
        pad_r = (self.window_size - W % self.window_size) % self.window_size
        pad_b = (self.window_size - H % self.window_size) % self.window_size
        x = F.pad(x, (0, 0, 0, pad_r, 0, pad_b))
        _, Hp, Wp, _ = x.shape
        if self.shift_size > 0:
            shifted_x = torch.roll(x, shifts=(-self.shift_size, -self.shift_size), dims=(1, 2))
            attn_mask = mask_matrix
        else:
            shifted_x = x
            attn_mask = None
        x_windows = _window_partition(shifted_x, self.window_size).view(
            -1, self.window_size * self.window_size, C)
        attn_windows = self.attn(x_windows, mask=attn_mask).view(
            -1, self.window_size, self.window_size, C)
        shifted_x = _window_reverse(attn_windows, self.window_size, Hp, Wp)
        if self.shift_size > 0:
            x = torch.roll(shifted_x, shifts=(self.shift_size, self.shift_size), dims=(1, 2))
        else:
            x = shifted_x
        if pad_r > 0 or pad_b > 0:
            x = x[:, :H, :W, :].contiguous()
        x = x.view(B, H * W, C)
        x = shortcut + x
        x = x + self.mlp(self.norm2(x))
        return x


class _PatchMerging(nn.Module):
    def __init__(self, dim):
        super().__init__()
        self.dim = dim
        self.reduction = nn.Linear(4 * dim, 2 * dim, bias=False)
        self.norm = nn.LayerNorm(4 * dim)

    def forward(self, x, H, W):
        B, L, C = x.shape
        x = x.view(B, H, W, C)
        x0 = x[:, 0::2, 0::2, :]
        x1 = x[:, 1::2, 0::2, :]
        x2 = x[:, 0::2, 1::2, :]
        x3 = x[:, 1::2, 1::2, :]
        x = torch.cat([x0, x1, x2, x3], -1).view(B, -1, 4 * C)
        return self.reduction(self.norm(x))


class _SwinBasicLayer(nn.Module):
    def __init__(self, dim, depth, num_heads, window_size, mlp_ratio=4.0, downsample=True):
        super().__init__()
        self.window_size = window_size
        self.shift_size = window_size // 2
        self.depth = depth
        self.blocks = nn.ModuleList([
            _SwinBlock(dim=dim, num_heads=num_heads, window_size=window_size,
                       shift_size=0 if (i % 2 == 0) else window_size // 2,
                       mlp_ratio=mlp_ratio)
            for i in range(depth)
        ])
        self.downsample = _PatchMerging(dim) if downsample else None

    def forward(self, x, H, W):
        Hp = int(math.ceil(H / self.window_size)) * self.window_size
        Wp = int(math.ceil(W / self.window_size)) * self.window_size
        img_mask = torch.zeros((1, Hp, Wp, 1), device=x.device)
        h_slices = (slice(0, -self.window_size),
                    slice(-self.window_size, -self.shift_size),
                    slice(-self.shift_size, None))
        w_slices = (slice(0, -self.window_size),
                    slice(-self.window_size, -self.shift_size),
                    slice(-self.shift_size, None))
        cnt = 0
        for h in h_slices:
            for w in w_slices:
                img_mask[:, h, w, :] = cnt
                cnt += 1
        mask_windows = _window_partition(img_mask, self.window_size).view(-1, self.window_size ** 2)
        attn_mask = mask_windows.unsqueeze(1) - mask_windows.unsqueeze(2)
        attn_mask = attn_mask.masked_fill(attn_mask != 0, float(-100.0)) \
                             .masked_fill(attn_mask == 0, float(0.0)).to(x.dtype)
        for blk in self.blocks:
            blk.H, blk.W = H, W
            x = blk(x, attn_mask)
        if self.downsample is not None:
            x_down = self.downsample(x, H, W)
            Wh, Ww = (H + 1) // 2, (W + 1) // 2
            return x, H, W, x_down, Wh, Ww
        return x, H, W, x, H, W


class _SwinPatchEmbed(nn.Module):
    def __init__(self, patch_size=4, in_channels=3, embed_dim=192):
        super().__init__()
        self.patch_size = (patch_size, patch_size)
        self.proj = nn.Conv2d(in_channels, embed_dim, kernel_size=patch_size, stride=patch_size)
        self.norm = nn.LayerNorm(embed_dim)
        self.embed_dim = embed_dim

    def forward(self, x):
        x = self.proj(x)
        Wh, Ww = x.size(2), x.size(3)
        x = x.flatten(2).transpose(1, 2)
        x = self.norm(x)
        return x.transpose(1, 2).view(-1, self.embed_dim, Wh, Ww)


class _SwinLarge(nn.Module):
    def __init__(self):
        super().__init__()
        embed_dim = 192
        depths = [2, 2, 18, 2]
        num_heads = [6, 12, 24, 48]
        window_size = 12
        self.num_layers = len(depths)
        self.embed_dim = embed_dim
        self.patch_embed = _SwinPatchEmbed(patch_size=4, in_channels=3, embed_dim=embed_dim)
        self.layers = nn.ModuleList([
            _SwinBasicLayer(
                dim=int(embed_dim * 2 ** i),
                depth=depths[i],
                num_heads=num_heads[i],
                window_size=window_size,
                downsample=(i < self.num_layers - 1),
            ) for i in range(self.num_layers)
        ])
        num_features = [int(embed_dim * 2 ** i) for i in range(self.num_layers)]
        self.num_features = num_features
        for i in range(self.num_layers):
            self.add_module(f"norm{i}", nn.LayerNorm(num_features[i]))

    def forward(self, x):
        x = self.patch_embed(x)
        Wh, Ww = x.size(2), x.size(3)
        x = x.flatten(2).transpose(1, 2)
        outs = []
        for i in range(self.num_layers):
            x_out, H, W, x, Wh, Ww = self.layers[i](x, Wh, Ww)
            norm_layer = getattr(self, f"norm{i}")
            x_out = norm_layer(x_out)
            out = x_out.view(-1, H, W, self.num_features[i]).permute(0, 3, 1, 2).contiguous()
            outs.append(out)
        return tuple(outs)


# -- ASPP-Deformable -----------------------------------------------------------

class _DeformableConv2d(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size=3, stride=1, padding=1, bias=False):
        super().__init__()
        if isinstance(kernel_size, int):
            kernel_size = (kernel_size, kernel_size)
        self.stride = (stride, stride) if isinstance(stride, int) else stride
        self.padding = padding
        self.offset_conv = nn.Conv2d(in_channels, 2 * kernel_size[0] * kernel_size[1],
                                     kernel_size=kernel_size, stride=stride, padding=padding, bias=True)
        self.modulator_conv = nn.Conv2d(in_channels, 1 * kernel_size[0] * kernel_size[1],
                                        kernel_size=kernel_size, stride=stride, padding=padding, bias=True)
        self.regular_conv = nn.Conv2d(in_channels, out_channels,
                                      kernel_size=kernel_size, stride=stride, padding=padding, bias=bias)

    def forward(self, x):
        offset = self.offset_conv(x)
        modulator = 2.0 * torch.sigmoid(self.modulator_conv(x))
        return deform_conv2d(
            input=x, offset=offset,
            weight=self.regular_conv.weight, bias=self.regular_conv.bias,
            padding=self.padding, mask=modulator, stride=self.stride,
        )


class _ASPPModuleDeformable(nn.Module):
    def __init__(self, in_channels, planes, kernel_size, padding):
        super().__init__()
        self.atrous_conv = _DeformableConv2d(in_channels, planes, kernel_size=kernel_size,
                                             stride=1, padding=padding, bias=False)
        self.bn = nn.BatchNorm2d(planes)
        self.relu = nn.ReLU(inplace=True)

    def forward(self, x):
        return self.relu(self.bn(self.atrous_conv(x)))


class _ASPPDeformable(nn.Module):
    def __init__(self, in_channels, out_channels=None, parallel_block_sizes=(1, 3, 7)):
        super().__init__()
        if out_channels is None:
            out_channels = in_channels
        inter = 256
        self.aspp1 = _ASPPModuleDeformable(in_channels, inter, 1, padding=0)
        self.aspp_deforms = nn.ModuleList([
            _ASPPModuleDeformable(in_channels, inter, k, padding=k // 2)
            for k in parallel_block_sizes
        ])
        self.global_avg_pool = nn.Sequential(
            nn.AdaptiveAvgPool2d((1, 1)),
            nn.Conv2d(in_channels, inter, 1, stride=1, bias=False),
            nn.BatchNorm2d(inter),
            nn.ReLU(inplace=True),
        )
        self.conv1 = nn.Conv2d(inter * (2 + len(self.aspp_deforms)), out_channels, 1, bias=False)
        self.bn1 = nn.BatchNorm2d(out_channels)
        self.relu = nn.ReLU(inplace=True)

    def forward(self, x):
        x1 = self.aspp1(x)
        x_aspp_deforms = [m(x) for m in self.aspp_deforms]
        x5 = self.global_avg_pool(x)
        x5 = F.interpolate(x5, size=x1.size()[2:], mode='bilinear', align_corners=True)
        y = torch.cat((x1, *x_aspp_deforms, x5), dim=1)
        return self.relu(self.bn1(self.conv1(y)))


# -- Decoder blocks ------------------------------------------------------------

class _BasicDecBlk(nn.Module):
    def __init__(self, in_channels, out_channels, inter_channels=64):
        super().__init__()
        self.conv_in = nn.Conv2d(in_channels, inter_channels, 3, 1, padding=1)
        self.bn_in = nn.BatchNorm2d(inter_channels)
        self.relu_in = nn.ReLU(inplace=True)
        self.dec_att = _ASPPDeformable(in_channels=inter_channels)
        self.conv_out = nn.Conv2d(inter_channels, out_channels, 3, 1, padding=1)
        self.bn_out = nn.BatchNorm2d(out_channels)

    def forward(self, x):
        x = self.relu_in(self.bn_in(self.conv_in(x)))
        x = self.dec_att(x)
        x = self.bn_out(self.conv_out(x))
        return x


class _BasicLatBlk(nn.Module):
    def __init__(self, in_channels, out_channels):
        super().__init__()
        self.conv = nn.Conv2d(in_channels, out_channels, 1, 1, 0)

    def forward(self, x):
        return self.conv(x)


class _SimpleConvs(nn.Module):
    def __init__(self, in_channels, out_channels, inter_channels=64):
        super().__init__()
        self.conv1 = nn.Conv2d(in_channels, inter_channels, 3, 1, 1)
        self.conv_out = nn.Conv2d(inter_channels, out_channels, 3, 1, 1)

    def forward(self, x):
        return self.conv_out(self.conv1(x))


# -- Image → patch-stack helper -----------------------------------------------

def _image2patches(image, patch_ref):
    """einops 'b c (hg h) (wg w) -> b (c hg wg) h w' without einops."""
    b, c, h_full, w_full = image.shape
    hg, wg = h_full // patch_ref.shape[-2], w_full // patch_ref.shape[-1]
    h, w = h_full // hg, w_full // wg
    # (b, c, hg*h, wg*w) -> (b, c, hg, h, wg, w) -> (b, c, hg, wg, h, w) -> (b, c*hg*wg, h, w)
    return image.view(b, c, hg, h, wg, w).permute(0, 1, 2, 4, 3, 5).reshape(b, c * hg * wg, h, w)


# -- Decoder + top-level BiRefNet ---------------------------------------------

class _BiRefNetDecoder(nn.Module):
    def __init__(self, channels=(3072, 1536, 768, 384)):
        super().__init__()
        c = channels
        self.ipt_blk5 = _SimpleConvs(2 ** 10 * 3, c[0] // 8, inter_channels=64)
        self.ipt_blk4 = _SimpleConvs(2 ** 8 * 3,  c[0] // 8, inter_channels=64)
        self.ipt_blk3 = _SimpleConvs(2 ** 6 * 3,  c[1] // 8, inter_channels=64)
        self.ipt_blk2 = _SimpleConvs(2 ** 4 * 3,  c[2] // 8, inter_channels=64)
        self.ipt_blk1 = _SimpleConvs(2 ** 0 * 3,  c[3] // 8, inter_channels=64)

        self.decoder_block4 = _BasicDecBlk(c[0] + c[0] // 8, c[1])
        self.decoder_block3 = _BasicDecBlk(c[1] + c[0] // 8, c[2])
        self.decoder_block2 = _BasicDecBlk(c[2] + c[1] // 8, c[3])
        self.decoder_block1 = _BasicDecBlk(c[3] + c[2] // 8, c[3] // 2)
        self.conv_out1 = nn.Sequential(nn.Conv2d(c[3] // 2 + c[3] // 8, 1, 1, 1, 0))

        self.lateral_block4 = _BasicLatBlk(c[1], c[1])
        self.lateral_block3 = _BasicLatBlk(c[2], c[2])
        self.lateral_block2 = _BasicLatBlk(c[3], c[3])

        # gradient-decoder-triggering (gdt) attention: used at inference to
        # gate p4/p3/p2.
        _N = 16
        def _gdt_branch(in_c):
            return nn.Sequential(nn.Conv2d(in_c, _N, 3, 1, 1), nn.BatchNorm2d(_N), nn.ReLU(inplace=True))
        self.gdt_convs_4 = _gdt_branch(c[1])
        self.gdt_convs_3 = _gdt_branch(c[2])
        self.gdt_convs_2 = _gdt_branch(c[3])

        def _head_1x1():
            return nn.Sequential(nn.Conv2d(_N, 1, 1, 1, 0))
        self.gdt_convs_attn_4 = _head_1x1()
        self.gdt_convs_attn_3 = _head_1x1()
        self.gdt_convs_attn_2 = _head_1x1()

    def forward(self, x, x1, x2, x3, x4):
        x4 = torch.cat((x4, self.ipt_blk5(_image2patches(x, x4))), 1)
        p4 = self.decoder_block4(x4)
        p4 = p4 * self.gdt_convs_attn_4(self.gdt_convs_4(p4)).sigmoid()
        _p4 = F.interpolate(p4, size=x3.shape[2:], mode='bilinear', align_corners=True)
        _p3 = _p4 + self.lateral_block4(x3)

        _p3 = torch.cat((_p3, self.ipt_blk4(_image2patches(x, _p3))), 1)
        p3 = self.decoder_block3(_p3)
        p3 = p3 * self.gdt_convs_attn_3(self.gdt_convs_3(p3)).sigmoid()
        _p3 = F.interpolate(p3, size=x2.shape[2:], mode='bilinear', align_corners=True)
        _p2 = _p3 + self.lateral_block3(x2)

        _p2 = torch.cat((_p2, self.ipt_blk3(_image2patches(x, _p2))), 1)
        p2 = self.decoder_block2(_p2)
        p2 = p2 * self.gdt_convs_attn_2(self.gdt_convs_2(p2)).sigmoid()
        _p2 = F.interpolate(p2, size=x1.shape[2:], mode='bilinear', align_corners=True)
        _p1 = _p2 + self.lateral_block2(x1)

        _p1 = torch.cat((_p1, self.ipt_blk2(_image2patches(x, _p1))), 1)
        _p1 = self.decoder_block1(_p1)
        _p1 = F.interpolate(_p1, size=x.shape[2:], mode='bilinear', align_corners=True)

        _p1 = torch.cat((_p1, self.ipt_blk1(_image2patches(x, _p1))), 1)
        return self.conv_out1(_p1)


class BiRefNet(nn.Module):
    """BiRefNet (ZhengPeng7/BiRefNet) with the Swin-L backbone: forward(x) returns the alpha map in [0, 1],
    remove_background(image) returns the RGBA PIL image."""

    INPUT_SIZE = (1024, 1024)
    # backbone channel counts post mul_scl_ipt='cat' (doubled from raw Swin-L)
    _CHANNELS = (3072, 1536, 768, 384)
    _NORM_MEAN = (0.485, 0.456, 0.406)
    _NORM_STD  = (0.229, 0.224, 0.225)

    def __init__(self):
        super().__init__()
        self.bb = _SwinLarge()
        cxt = list(self._CHANNELS[1:][::-1][-3:])  # = [384, 768, 1536]
        self.squeeze_module = nn.Sequential(
            _BasicDecBlk(self._CHANNELS[0] + sum(cxt), self._CHANNELS[0])
        )
        self.decoder = _BiRefNetDecoder(channels=self._CHANNELS)

    @property
    def device(self):
        return next(self.parameters()).device

    @property
    def dtype(self):
        return next(self.parameters()).dtype

    def _forward_enc(self, x):
        x1, x2, x3, x4 = self.bb(x)
        # mul_scl_ipt='cat': re-run backbone at half resolution, concat features
        B, C, H, W = x.shape
        x1_, x2_, x3_, x4_ = self.bb(F.interpolate(x, size=(H // 2, W // 2),
                                                   mode='bilinear', align_corners=True))
        x1 = torch.cat([x1, F.interpolate(x1_, size=x1.shape[2:], mode='bilinear', align_corners=True)], 1)
        x2 = torch.cat([x2, F.interpolate(x2_, size=x2.shape[2:], mode='bilinear', align_corners=True)], 1)
        x3 = torch.cat([x3, F.interpolate(x3_, size=x3.shape[2:], mode='bilinear', align_corners=True)], 1)
        x4 = torch.cat([x4, F.interpolate(x4_, size=x4.shape[2:], mode='bilinear', align_corners=True)], 1)
        # cxt: upsample x1/x2/x3 to x4 spatial and concat for the squeeze input
        x4 = torch.cat([
            F.interpolate(x1, size=x4.shape[2:], mode='bilinear', align_corners=True),
            F.interpolate(x2, size=x4.shape[2:], mode='bilinear', align_corners=True),
            F.interpolate(x3, size=x4.shape[2:], mode='bilinear', align_corners=True),
            x4,
        ], 1)
        return x1, x2, x3, x4

    def forward(self, x):
        x1, x2, x3, x4 = self._forward_enc(x)
        x4 = self.squeeze_module(x4)
        logits = self.decoder(x, x1, x2, x3, x4)
        return torch.sigmoid(logits)

    @torch.no_grad()
    def remove_background(self, image) -> "Image.Image":
        from PIL import Image
        W, H = image.size
        arr = np.array(image, dtype=np.float32) / 255.0
        t = torch.from_numpy(arr).permute(2, 0, 1).unsqueeze(0)
        t = F.interpolate(t, size=self.INPUT_SIZE, mode='bilinear', align_corners=True)
        mean = torch.tensor(self._NORM_MEAN).view(1, 3, 1, 1)
        std  = torch.tensor(self._NORM_STD).view(1, 3, 1, 1)
        t = ((t - mean) / std).to(device=self.device, dtype=self.dtype)
        alpha = self.forward(t)
        alpha = F.interpolate(alpha.float(), size=(H, W), mode='bilinear', align_corners=True)[0, 0]
        a = (alpha.clamp(0, 1) * 255).to(torch.uint8).cpu().numpy()
        rgba = image.copy()
        rgba.putalpha(Image.fromarray(a, mode="L"))
        return rgba


