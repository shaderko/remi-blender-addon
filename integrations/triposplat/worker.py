"""Image -> NCM -> native 3DGS, one-shot or as a warm local worker."""
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


def _write_job_log(output, text):
    try:
        with (output / "worker.log").open("a", encoding="utf-8") as handle:
            handle.write(text.rstrip() + "\n")
    except OSError as exc:
        print(f"Worker log update skipped: {exc}", flush=True)


class Generator:
    """Loaded inference stack that can generate repeatedly without reloading."""

    def __init__(self, models, status_output=None):
        self.models = Path(models)
        for name in COMPONENTS:
            if not (self.models / name).is_file():
                raise FileNotFoundError(f"Missing model component: {self.models / name}")
        vendor = Path(__file__).resolve().parent / "vendor"
        if not (vendor / "flow_transplant" / "faithful_model.py").is_file():
            raise RuntimeError("Missing vendored inference sources; run scripts/prepare_triposplat.py")
        # Only this isolated process exposes upstream's flat module imports.
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
        self.torch = torch
        self.np = np
        self.Image = Image
        self.ImageOps = ImageOps
        self.preprocess_image = preprocess_image
        self.encode_image = encode_image
        self.build_model = build_model
        self.paired_noise = paired_noise
        self.save = save
        self.device = "cuda"
        self.dtype = torch.bfloat16
        torch.set_num_threads(4)

        if status_output is not None:
            status(status_output, "Loading NCM checkpoint")
        state = torch.load(
            self.models / "best_weights.pt",
            map_location="cpu",
            weights_only=True,
            mmap=True,
        )
        if state.get("format") != "faithful_flow_v2_weights":
            raise ValueError(f"Unexpected checkpoint format: {state.get('format')}")
        model = build_model(pretrained=False, device="cpu", gradient_checkpointing=False)
        model.load_state_dict(state["model"], strict=True)
        self.step = int(state["step"])
        del state
        model.to(device=self.device, dtype=self.dtype).eval().requires_grad_(False)
        if status_output is not None:
            status(status_output, "Loading image encoders and Gaussian decoder")
        self.model = model
        self.dino = load_dinov3(str(self.models / COMPONENTS[1]), device=self.device, dtype=self.dtype)
        self.vae = load_vae_encoder(str(self.models / COMPONENTS[2]), device=self.device, dtype=self.dtype)
        self.rmbg = load_rmbg(str(self.models / COMPONENTS[4]), device=self.device, dtype=torch.float16)
        self.decoder = load_decoder(str(self.models / COMPONENTS[3]), device=self.device, dtype=self.dtype)
        for module in (self.dino, self.vae, self.rmbg, self.decoder):
            module.eval().requires_grad_(False)

    def generate(self, request, output):
        started = time.perf_counter()
        count, seed = int(request["gaussians"]), int(request["seed"])
        if count not in (32768, 65536, 131072, 262144) or not 0 <= seed <= 2147483647:
            raise ValueError("Unsupported Gaussian count or seed")
        torch = self.torch
        np = self.np
        with torch.inference_mode():
            status(output, "Removing background and preparing image")
            with self.Image.open(request["image"]) as original:
                original = self.ImageOps.exif_transpose(original)
                original.load()
                if "A" in original.getbands() or "transparency" in original.info:
                    original = original.convert("RGBA")
                if original.mode == "RGBA" and not original.getchannel("A").getbbox():
                    raise ValueError("The input image is fully transparent")
                prepared = self.preprocess_image(original, self.rmbg)
            prepared.save(output / "input.png")
            with torch.autocast("cuda", dtype=self.dtype):
                status(output, "Encoding image")
                generator = torch.Generator(device=self.device).manual_seed(seed)
                cond = self.encode_image(prepared, self.dino, self.vae, generator=generator)
                noise = self.paired_noise(seed, self.model.flow, device=self.device)
                status(output, "Generating eight-level NCM latent")
                latent = self.model.generate(cond, noise)
                status(output, "Decoding Gaussian splats")
                torch.manual_seed(seed)
                gaussian = self.decoder.decode(latent.to(self.dtype), num_gaussians=count)
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
            self.save(output / "splats.npz", arrays)
            gaussian.save_ply(str(output / "model.ply"), transform=transform)
        metadata = {
            "schema": 1,
            "model": "rand6_100_ncm",
            "checkpoint_step": self.step,
            "checkpoint": str(self.models / "best_weights.pt"),
            "image": request["image"],
            "seed": seed,
            "gaussians": len(xyz),
            "frame": "PLY Z-up, WXYZ quaternions",
            "elapsed_seconds": time.perf_counter() - started,
            "device": torch.cuda.get_device_name(),
            "torch": torch.__version__,
            "representation": "3D Gaussian splats; display selected by Blender importer",
        }
        (output / "meta.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        status(output, "Complete", complete=True, gaussians=len(xyz))
        # Immutable completion marker, checked only after the request has
        # finished. Unlike the frequently read progress file, this cannot race
        # a Windows status reader.
        (output / "complete.json").write_text(
            json.dumps({"schema": 1, "complete": True}), encoding="utf-8"
        )


def generate(request, output):
    """One-shot compatibility path: load once and generate once."""
    generator = Generator(request["models"], status_output=output)
    generator.generate(request, output)


def _take_job(queue):
    for path in sorted(queue.glob("*.json")):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            path.unlink(missing_ok=True)
            continue
        try:
            path.unlink()
        except OSError:
            continue
        return job
    return None


def serve(cache_dir, models, idle_timeout):
    """Keep the model loaded and serve queued generation requests."""
    cache_dir = Path(cache_dir)
    queue = cache_dir / "queue"
    queue.mkdir(parents=True, exist_ok=True)
    server_log = cache_dir / "server.log"
    generator = None
    last_activity = time.monotonic()
    print(f"Generation worker ready; idle timeout {idle_timeout:.0f}s", flush=True)
    while True:
        job = _take_job(queue)
        if job:
            request_path = Path(job["request"])
            output = request_path.parent
            if not (output / "worker.log").is_file():
                _write_job_log(
                    output, f"Persistent generation worker\nServer log: {server_log}"
                )
            try:
                if generator is None:
                    status(output, "Loading model")
                    generator = Generator(models, status_output=output)
                else:
                    status(output, "Using cached model")
                request = json.loads(request_path.read_text(encoding="utf-8"))
                generator.generate(request, output)
            except Exception as exc:
                traceback.print_exc()
                _write_job_log(output, traceback.format_exc())
                status(output, "Failed", error=str(exc))
                if generator is None:
                    # A failed model load cannot become warm; exit and let the
                    # next request start a fresh process.
                    break
            last_activity = time.monotonic()
            continue
        if idle_timeout <= 0:
            break
        if time.monotonic() - last_activity >= idle_timeout:
            break
        time.sleep(0.2)
    print("Generation worker released its model", flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--models", type=Path)
    parser.add_argument("--idle-timeout", type=float, default=300.0)
    args = parser.parse_args()
    if args.serve:
        if args.cache_dir is None or args.models is None:
            parser.error("--serve requires --cache-dir and --models")
        return serve(args.cache_dir, args.models, max(0.0, float(args.idle_timeout)))
    if args.request is None:
        parser.error("--request is required unless --serve is used")
    output = args.request.resolve().parent
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        generate(request, output)
    except Exception as exc:
        traceback.print_exc()
        _write_job_log(output, traceback.format_exc())
        status(output, "Failed", error=str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
