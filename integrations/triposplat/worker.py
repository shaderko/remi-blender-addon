"""One-shot image -> NCM -> native 3DGS. Runs in external Python, never Blender."""
import argparse
import json
from pathlib import Path
import sys
import time
import traceback


COMPONENTS = (
    "best_weights.pt",
    "ckpts/clip_vision/dino_v3_vit_h.safetensors",
    "ckpts/vae/flux2-vae.safetensors",
    "ckpts/vae/triposplat_vae_decoder_fp16.safetensors",
    "ckpts/background_removal/birefnet.safetensors",
)


def status(output, phase, **extra):
    print(phase, flush=True)
    temporary = output / "status.partial.json"
    try:
        temporary.write_text(json.dumps({"phase": phase, **extra}), encoding="utf-8")
        # Windows readers/antivirus can briefly hold status.json without delete
        # sharing. A progress update must never fail an otherwise valid model.
        for attempt in range(20):
            try:
                temporary.replace(output / "status.json")
                return True
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)
    except OSError as exc:
        print(f"Progress update skipped: {exc}", flush=True)
        return False


def generate(request, output):
    started = time.perf_counter()
    count, seed = int(request["gaussians"]), int(request["seed"])
    if count not in (32768, 65536, 131072, 262144) or not 0 <= seed <= 2147483647:
        raise ValueError("Unsupported Gaussian count or seed")
    models = Path(request["models"])
    for name in COMPONENTS:
        if not (models / name).is_file():
            raise FileNotFoundError(f"Missing model component: {models / name}")
    vendor = Path(__file__).resolve().parent / "vendor"
    if not (vendor / "flow_transplant" / "faithful_model.py").is_file():
        raise RuntimeError("Missing vendored inference sources; run scripts/prepare_triposplat.py")
    # Only this isolated subprocess exposes upstream's flat module imports.
    sys.path.insert(0, str(vendor))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import torch
    import numpy as np
    from PIL import Image, ImageOps
    from triposplat import load_dinov3, load_vae_encoder, load_rmbg, load_decoder
    from triposplat import preprocess_image, encode_image
    from flow_transplant.faithful_model import build_model, paired_noise
    from splat_data import save

    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
        raise RuntimeError("This experimental checkpoint requires a CUDA GPU with bfloat16 support")
    device, dtype = "cuda", torch.bfloat16
    torch.set_num_threads(4)
    status(output, "Loading NCM checkpoint")
    state = torch.load(models / "best_weights.pt", map_location="cpu", weights_only=True, mmap=True)
    if state.get("format") != "faithful_flow_v2_weights":
        raise ValueError(f"Unexpected checkpoint format: {state.get('format')}")
    model = build_model(pretrained=False, device="cpu", gradient_checkpointing=False)
    model.load_state_dict(state["model"], strict=True)
    step = int(state["step"])
    del state
    model.to(device=device, dtype=dtype).eval().requires_grad_(False)
    status(output, "Loading image encoders and Gaussian decoder")
    dino = load_dinov3(str(models / COMPONENTS[1]), device=device, dtype=dtype)
    vae = load_vae_encoder(str(models / COMPONENTS[2]), device=device, dtype=dtype)
    rmbg = load_rmbg(str(models / COMPONENTS[4]), device=device, dtype=torch.float16)
    decoder = load_decoder(str(models / COMPONENTS[3]), device=device, dtype=dtype)
    for module in (dino, vae, rmbg, decoder):
        module.eval().requires_grad_(False)
    with torch.inference_mode():
        status(output, "Removing background and preparing image")
        with Image.open(request["image"]) as original:
            original = ImageOps.exif_transpose(original)
            original.load()
            if "A" in original.getbands() or "transparency" in original.info:
                original = original.convert("RGBA")
            if original.mode == "RGBA" and not original.getchannel("A").getbbox():
                raise ValueError("The input image is fully transparent")
            prepared = preprocess_image(original, rmbg)
        prepared.save(output / "input.png")
        with torch.autocast("cuda", dtype=dtype):
            status(output, "Encoding image")
            generator = torch.Generator(device=device).manual_seed(seed)
            cond = encode_image(prepared, dino, vae, generator=generator)
            noise = paired_noise(seed, model.flow, device=device)
            status(output, "Generating eight-level NCM latent")
            latent = model.generate(cond, noise)
            status(output, "Decoding Gaussian splats")
            torch.manual_seed(seed)
            gaussian = decoder.decode(latent.to(dtype), num_gaussians=count)
        status(output, "Saving native Gaussian splats")
        for attr in ("_xyz", "_features_dc", "_opacity", "_scaling", "_rotation"):
            setattr(gaussian, attr, getattr(gaussian, attr).float())
        # Model coordinates are already Blender Z-up. Upstream's default PLY
        # transform is viewer-oriented (Y-down), so bypass it for BOTH files.
        transform = np.eye(3, dtype=np.float32)
        xyz, rotation = gaussian._transformed_xyz_rot(transform=transform)
        arrays = {
            "positions": xyz,
            "rotations": rotation.astype(np.float32),
            "scales": gaussian.get_scaling.detach().cpu().numpy(),
            "opacities": gaussian.get_opacity[:, 0].detach().cpu().numpy(),
            "sh_dc": gaussian._features_dc[:, 0].detach().cpu().numpy(),
        }
        save(output / "splats.npz", arrays)
        gaussian.save_ply(str(output / "model.ply"), transform=transform)
    metadata = {
        "schema": 1, "model": "rand6_100_ncm", "checkpoint_step": step,
        "checkpoint": str(models / "best_weights.pt"), "image": request["image"],
        "seed": seed, "gaussians": len(xyz), "frame": "PLY Z-up, WXYZ quaternions",
        "elapsed_seconds": time.perf_counter() - started,
        "device": torch.cuda.get_device_name(),
        "torch": torch.__version__, "representation": "3D Gaussian splats; display selected by Blender importer",
    }
    (output / "meta.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    status(output, "Complete", complete=True, gaussians=len(xyz))
    # Immutable completion marker, checked only AFTER process exit. Unlike the
    # frequently read progress file, this cannot race a Windows status reader.
    (output / "complete.json").write_text(json.dumps({"schema": 1, "complete": True}), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    output = args.request.resolve().parent
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        generate(request, output)
    except Exception as exc:
        traceback.print_exc()
        status(output, "Failed", error=str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
