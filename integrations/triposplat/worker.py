"""Image -> NCM -> native 3DGS, one-shot or as a warm local worker."""
import argparse
import json
from pathlib import Path
import sys
import time
import traceback


DEFAULT_MODEL = "rand6_100_ncm"
SHARED_COMPONENTS = (
    "ckpts/clip_vision/dino_v3_vit_h.safetensors",
    "ckpts/vae/flux2-vae.safetensors",
    "ckpts/vae/triposplat_vae_decoder_fp16.safetensors",
    "ckpts/background_removal/birefnet.safetensors",
)
MODEL_COMPONENTS = {
    "idol": ("idol/config.json", "idol/denoiser.safetensors", "idol/decoder.safetensors", "idol/decoder_gs.safetensors", "idol/birefnet.safetensors"),
    "rand6_100_ncm": ("best_weights.pt",),
    "triposplat": ("ckpts/diffusion_models/triposplat_fp16.safetensors",),
}
MODEL_LABELS = {
    "idol": "IDOL",
    "rand6_100_ncm": "Remi NCM",
    "triposplat": "TripoSplat",
}
# Backward-compatible NCM component list for older callers and tests.
COMPONENTS = MODEL_COMPONENTS[DEFAULT_MODEL] + SHARED_COMPONENTS


def components_for_model(model):
    try:
        return MODEL_COMPONENTS[model] + (SHARED_COMPONENTS[:2] if model == "idol" else SHARED_COMPONENTS)
    except KeyError:
        raise ValueError(f"Unknown generation model: {model}") from None


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

    def __init__(self, models, model=None, status_output=None):
        self.models = Path(models)
        if model is not None and model not in MODEL_COMPONENTS:
            raise ValueError(f"Unknown generation model: {model}")
        for name in SHARED_COMPONENTS + (MODEL_COMPONENTS[model] if model is not None else ()):
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
        from triposplat import load_flow_model, preprocess_image, encode_image, sample_latent
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
        self.sample_latent = sample_latent
        self.load_flow_model = load_flow_model
        self.build_model = build_model
        self.paired_noise = paired_noise
        self.save = save
        self.device = "cuda"
        self.dtype = torch.bfloat16
        self.model_id = None
        self.model = None
        self.flow_model = None
        self.step = None
        self.checkpoint_path = None
        torch.set_num_threads(4)

        if status_output is not None:
            status(status_output, "Loading image encoders and Gaussian decoder")
        self.dino = load_dinov3(str(self.models / SHARED_COMPONENTS[0]), device=self.device, dtype=self.dtype)
        self.vae = load_vae_encoder(str(self.models / SHARED_COMPONENTS[1]), device=self.device, dtype=self.dtype)
        self.decoder = load_decoder(str(self.models / SHARED_COMPONENTS[2]), device=self.device, dtype=self.dtype)
        self.rmbg = load_rmbg(str(self.models / SHARED_COMPONENTS[3]), device=self.device, dtype=torch.float16)
        for module in (self.dino, self.vae, self.rmbg, self.decoder):
            module.eval().requires_grad_(False)
        if model is not None:
            self.load_model(model, status_output)

    def load_model(self, model, status_output=None):
        """Load only the selected flow; image encoders and decoder stay warm."""
        if model == self.model_id:
            return
        if model not in MODEL_COMPONENTS:
            raise ValueError(f"Unknown generation model: {model}")
        files = MODEL_COMPONENTS[model]
        for name in files:
            if not (self.models / name).is_file():
                raise FileNotFoundError(f"Missing model component: {self.models / name}")
        torch = self.torch
        # Release the previous flow before loading another one. The shared
        # encoders and decoder remain resident for repeated generations.
        self.model = None
        self.flow_model = None
        self.model_id = None
        self.step = None
        self.checkpoint_path = None
        torch.cuda.empty_cache()
        path = self.models / files[0]
        if model == DEFAULT_MODEL:
            if status_output is not None:
                status(status_output, "Loading NCM checkpoint")
            state = torch.load(path, map_location="cpu", weights_only=True, mmap=True)
            if state.get("format") != "faithful_flow_v2_weights":
                raise ValueError(f"Unexpected checkpoint format: {state.get('format')}")
            hierarchy = self.build_model(pretrained=False, device="cpu", gradient_checkpointing=False)
            hierarchy.load_state_dict(state["model"], strict=True)
            self.step = int(state["step"])
            del state
            hierarchy.to(device=self.device, dtype=self.dtype).eval().requires_grad_(False)
            self.model = hierarchy
        else:
            if status_output is not None:
                status(status_output, "Loading TripoSplat flow model")
            flow = self.load_flow_model(str(path), device=self.device, dtype=torch.float16)
            flow.eval().requires_grad_(False)
            self.flow_model = flow
        self.checkpoint_path = path
        self.model_id = model

    def generate(self, request, output):
        started = time.perf_counter()
        model = str(request.get("model", DEFAULT_MODEL))
        self.load_model(model, status_output=output)
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
            generator = torch.Generator(device=self.device).manual_seed(seed)
            sampler = request.get("sampler") or {}
            if model == DEFAULT_MODEL:
                with torch.autocast("cuda", dtype=self.dtype):
                    status(output, "Encoding image")
                    cond = self.encode_image(prepared, self.dino, self.vae, generator=generator)
                    noise = self.paired_noise(seed, self.model.flow, device=self.device)
                    status(output, "Generating eight-level NCM latent")
                    latent = self.model.generate(cond, noise)
                    status(output, "Decoding Gaussian splats")
                    torch.manual_seed(seed)
                    gaussian = self.decoder.decode(latent.to(self.dtype), num_gaussians=count)
                sampler_info = {"kind": "ncm-hierarchy", "levels": list(range(1024, 8193, 1024))}
            else:
                steps = max(1, min(100, int(sampler.get("steps", 20))))
                guidance = max(1.0, min(20.0, float(sampler.get("guidance_scale", 3.0))))
                shift = max(0.1, min(20.0, float(sampler.get("shift", 3.0))))
                status(output, "Encoding image")
                cond = self.encode_image(prepared, self.dino, self.vae, generator=generator)
                status(output, "Sampling TripoSplat latent")

                def report_step(step, total):
                    status(output, f"Sampling TripoSplat latent - {step}/{total}")

                latent = self.sample_latent(
                    self.flow_model, cond, steps=steps, guidance_scale=guidance,
                    shift=shift, generator=generator, callback=report_step,
                )
                status(output, "Decoding Gaussian splats")
                torch.manual_seed(seed)
                gaussian = self.decoder.decode(latent["latent"].to(self.dtype), num_gaussians=count)
                sampler_info = {
                    "kind": "euler-cfg",
                    "steps": steps,
                    "guidance_scale": guidance,
                    "shift": shift,
                }
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
            "model": self.model_id,
            "model_label": MODEL_LABELS.get(self.model_id, self.model_id),
            "checkpoint_step": self.step,
            "checkpoint": str(self.checkpoint_path),
            "sampler": sampler_info,
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


def create_generator(models, model, output):
    if model == "idol":
        # Direct worker launch has no package context under isolated Python.
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from integrations.idol.runtime import IdolGenerator
        return IdolGenerator(models, status_output=output)
    return Generator(models, model=model, status_output=output)


def generate(request, output):
    """One-shot compatibility path: load once and generate once."""
    generator = create_generator(request["models"], request.get("model", DEFAULT_MODEL), output)
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


def serve(cache_dir, models, idle_timeout, default_model=DEFAULT_MODEL):
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
                request = json.loads(request_path.read_text(encoding="utf-8"))
                model = str(request.get("model", default_model))
                if generator is not None and ((generator.model_id == "idol") != (model == "idol")):
                    generator = None
                    import gc
                    gc.collect()
                    import torch
                    torch.cuda.empty_cache()
                if generator is None:
                    status(output, "Loading model")
                    generator = create_generator(models, model, output)
                else:
                    status(output, "Using cached model")
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
    parser.add_argument("--model", default=DEFAULT_MODEL, choices=tuple(MODEL_COMPONENTS))
    args = parser.parse_args()
    if args.serve:
        if args.cache_dir is None or args.models is None:
            parser.error("--serve requires --cache-dir and --models")
        return serve(args.cache_dir, args.models, max(0.0, float(args.idle_timeout)),
                     default_model=args.model)
    if args.request is None:
        parser.error("--request is required unless --serve is used")
    output = args.request.resolve().parent
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        request.setdefault("model", args.model)
        generate(request, output)
    except Exception as exc:
        traceback.print_exc()
        _write_job_log(output, traceback.format_exc())
        status(output, "Failed", error=str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
