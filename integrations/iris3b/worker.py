"""External Iris-3B text-to-image worker.

The Blender process never imports torch. This script runs in the user's
CUDA/PyTorch Python, keeps the model warm between requests, and writes only
plain image files and JSON status next to the job.
"""

import argparse
import json
from pathlib import Path
import sys
import time
import traceback

try:
    from .constants import MODEL_LABELS
except ImportError:  # one-shot execution via `python worker.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from constants import MODEL_LABELS

VENDOR = Path(__file__).resolve().parent / "vendor"


def status(output, phase, **extra):
    print(phase, flush=True)
    output = Path(output)
    temporary = output / "status.partial.json"
    try:
        temporary.write_text(json.dumps({"phase": phase, **extra}), encoding="utf-8")
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


def _write_log(output, text):
    try:
        with (Path(output) / "worker.log").open("a", encoding="utf-8") as handle:
            handle.write(text.rstrip() + "\n")
    except OSError as exc:
        print(f"Worker log update skipped: {exc}", flush=True)


def _import_vendor():
    if str(VENDOR) not in sys.path:
        sys.path.insert(0, str(VENDOR))
    import torch
    from iris3b.config import load_config
    from iris3b.net.dit import IrisDiT
    from iris3b.flow.solver import FlowDPMSolver
    from iris3b.text.qwen3_vl import Qwen3VLTextEncoder
    from safetensors.torch import load_file

    return torch, load_config, IrisDiT, FlowDPMSolver, Qwen3VLTextEncoder, load_file


class IrisGenerator:
    """Loaded Iris-3B model with optional CPU offload for small GPUs."""

    def __init__(self, models, variant, memory_mode, output=None):
        (
            torch,
            load_config,
            IrisDiT,
            FlowDPMSolver,
            Qwen3VLTextEncoder,
            load_file,
        ) = _import_vendor()
        self.torch = torch
        self.FlowDPMSolver = FlowDPMSolver
        self.variant = variant
        self.models = Path(models)
        self.repo = self.models / "iris-3b"
        self.text_encoder_path = self.models / "Qwen3-VL-4B-Instruct"
        config_path = self.repo / ("config.yaml" if variant == "base" else "turbo/config.yaml")
        weights_path = self.repo / ("model.safetensors" if variant == "base" else "turbo/model.safetensors")
        if not config_path.is_file() or not weights_path.is_file():
            raise FileNotFoundError(
                f"Missing Iris-3B {MODEL_LABELS.get(variant, variant)} files under {self.repo}. "
                "Use Download Model in the Images tab."
            )
        if not self.text_encoder_path.is_dir():
            raise FileNotFoundError(
                f"Missing Qwen3-VL text encoder under {self.text_encoder_path}. "
                "Use Download Model in the Images tab."
            )
        if not torch.cuda.is_available():
            raise RuntimeError("Iris-3B requires an NVIDIA CUDA GPU")
        if not torch.cuda.is_bf16_supported():
            raise RuntimeError("Iris-3B requires a CUDA GPU with bfloat16 support")

        self.device = torch.device("cuda")
        total = torch.cuda.get_device_properties(0).total_memory
        if memory_mode == "low":
            self.low_vram = True
        elif memory_mode == "high":
            self.low_vram = False
        else:
            self.low_vram = total < 28 * 1024**3
        self.weight_dtype = torch.bfloat16 if self.low_vram else torch.float32
        self.config = load_config(config_path)
        self.config.text_encoder.pretrained = str(self.text_encoder_path)
        self.config.text_encoder.null_embed_dir = str(self.models / "cache")
        self.sigmas = None
        if variant == "turbo":
            import yaml

            payload = yaml.safe_load((self.repo / "turbo/distill.yaml").read_text(encoding="utf-8"))
            self.sigmas = [float(value) for value in payload["dmd"]["sigmas"]]

        if output is not None:
            status(output, "Loading text encoder")
        cpu = "cpu"
        self.text_encoder = Qwen3VLTextEncoder(
            self.config.text_encoder, device=cpu if self.low_vram else self.device
        )

        if output is not None:
            status(output, f"Loading {MODEL_LABELS.get(variant, variant)} weights")
        with torch.device("meta"):
            model = IrisDiT(self.config.model)
        state = load_file(str(weights_path))
        model.load_state_dict(state, strict=True, assign=True)
        del state
        model = model.eval().requires_grad_(False)
        self.model_device = torch.device(cpu) if self.low_vram else self.device
        self.model = model.to(device=self.model_device, dtype=self.weight_dtype)
        self.model_dtype = next(self.model.parameters()).dtype
        if output is not None:
            status(
                output,
                f"{MODEL_LABELS.get(variant, variant)} ready · "
                f"{total / 1024**3:.1f} GB GPU · {'low' if self.low_vram else 'full'} memory",
            )

    def _move_model(self, device):
        current = next(self.model.parameters()).device
        if current.type != device.type or (device.type == "cuda" and current.index != device.index):
            self.model.to(device)
        self.torch.cuda.empty_cache()

    def _move_encoder(self, device):
        if self.text_encoder.device.type != device.type or (
            device.type == "cuda" and self.text_encoder.device.index != device.index
        ):
            self.text_encoder.to(device)
        self.torch.cuda.empty_cache()

    def _encode(self, prompts, negative):
        """Return text embeddings on CPU/GPU, releasing the encoder afterward."""
        torch = self.torch
        if self.low_vram:
            self._move_model(torch.device("cpu"))
            self._move_encoder(self.device)
        encoding = self.text_encoder.encode(prompts)
        null = None
        if self.variant == "base":
            null = self.text_encoder.null(negative)
        result = {
            "embeddings": encoding.embeddings.cpu(),
            "mask": encoding.mask.cpu(),
            "null_embeddings": null.embeddings.cpu() if null is not None else None,
            "null_mask": null.mask.cpu() if null is not None else None,
        }
        if self.low_vram:
            self._move_encoder(torch.device("cpu"))
        return result

    def _sample_base(self, encoded, height, width, steps, cfg_scale, cfg_interval, shift, seed):
        torch = self.torch
        self._move_model(self.device)
        embeddings = encoded["embeddings"].to(self.device, dtype=self.model_dtype)
        mask = encoded["mask"].to(self.device)
        null_embeddings = encoded["null_embeddings"]
        uncond = None
        cfg_mask = None
        if cfg_scale != 1.0 and null_embeddings is not None:
            uncond = null_embeddings.to(self.device, dtype=self.model_dtype).expand_as(embeddings)
            cfg_mask = torch.cat(
                [encoded["null_mask"].to(self.device).expand(mask.shape[0], -1), mask], dim=0
            )
        generator = torch.Generator(device=self.device).manual_seed(int(seed))
        noise = torch.randn(
            1, self.config.model.in_channels, int(height), int(width),
            generator=generator, device=self.device,
        )
        cond = embeddings

        def model_fn(x, t_model, y):
            out = self.model(
                x.to(self.model_dtype), t_model, y,
                y_mask=mask if y is cond else cfg_mask,
            )
            return out.x.float()

        solver = self.FlowDPMSolver(
            model_fn,
            num_timesteps=int(self.config.flow.num_train_timesteps),
            cfg_scale=float(cfg_scale),
            cfg_interval=tuple(cfg_interval),
            prediction=self.config.flow.prediction,
        )
        sample = solver.sample(
            noise, cond, uncond, steps=int(steps), order=2, shift=float(shift)
        )
        return sample.clamp(-1, 1)

    def _sample_turbo(self, encoded, height, width, seed):
        torch = self.torch
        self._move_model(self.device)
        embeddings = encoded["embeddings"].to(self.device, dtype=self.model_dtype)
        mask = encoded["mask"].to(self.device)
        generator = torch.Generator(device=self.device).manual_seed(int(seed))
        x = torch.randn(
            1, self.config.model.in_channels, int(height), int(width),
            generator=generator, device=self.device,
        )
        sigma = torch.full(
            (1, 1, 1, 1), self.sigmas[0], device=self.device, dtype=torch.float32
        )
        clean = x
        for index, value in enumerate(self.sigmas):
            out = self.model(
                x.to(self.model_dtype),
                sigma.flatten() * int(self.config.flow.num_train_timesteps),
                embeddings,
                y_mask=mask,
            ).x.float()
            clean = out if self.config.flow.prediction == "x" else x - sigma * out
            if index + 1 < len(self.sigmas):
                implied_noise = (x - (1 - sigma) * clean) / sigma
                sigma = torch.full_like(sigma, self.sigmas[index + 1])
                x = (1 - sigma) * clean + sigma * implied_noise
        return clean.clamp(-1, 1)

    def generate(self, request, output):
        started = time.perf_counter()
        output = Path(output)
        prompt = str(request["prompt"]).strip()
        if not prompt:
            raise ValueError("Enter a prompt")
        count = int(request.get("count", 1))
        if not 1 <= count <= 8:
            raise ValueError("Image count must be between 1 and 8")
        height, width = int(request["height"]), int(request["width"])
        if not 256 <= height <= 1536 or not 256 <= width <= 1536:
            raise ValueError("Image size must be between 256 and 1536 pixels")
        if height % 16 or width % 16:
            raise ValueError("Image size must be a multiple of 16 pixels")
        seed = int(request.get("seed", 0))
        if not 0 <= seed <= 2147483647:
            raise ValueError("Seed must be between 0 and 2147483647")

        status(output, "Encoding prompt")
        encoded = self._encode([prompt], str(request.get("negative_prompt", "")))
        images = []
        for index in range(count):
            image_seed = (seed + index) % 2147483648
            status(output, f"Generating image {index + 1}/{count}")
            if self.variant == "turbo":
                image = self._sample_turbo(encoded, height, width, image_seed)
            else:
                image = self._sample_base(
                    encoded,
                    height,
                    width,
                    int(request.get("steps", 100)),
                    float(request.get("cfg_scale", 3.0)),
                    tuple(self.config.sample.cfg_interval),
                    float(request.get("shift", self.config.flow.shift)),
                    image_seed,
                )
            path = output / f"image_{index:03d}.png"
            self._save(image[0], path)
            images.append({"file": path.name, "seed": image_seed})

        metadata = {
            "schema": 1,
            "model": self.variant,
            "model_label": MODEL_LABELS.get(self.variant, self.variant),
            "prompt": prompt,
            "negative_prompt": str(request.get("negative_prompt", "")),
            "width": width,
            "height": height,
            "steps": int(request.get("steps", 100)) if self.variant == "base" else 4,
            "cfg_scale": float(request.get("cfg_scale", 3.0)) if self.variant == "base" else 1.0,
            "seed": seed,
            "count": count,
            "images": images,
            "elapsed_seconds": time.perf_counter() - started,
            "device": self.torch.cuda.get_device_name(),
            "torch": self.torch.__version__,
            "low_vram": self.low_vram,
        }
        (output / "meta.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        status(output, "Complete", complete=True, images=len(images))
        (output / "complete.json").write_text(
            json.dumps({"schema": 1, "complete": True}), encoding="utf-8"
        )

    @staticmethod
    def _save(image, path):
        import numpy as np
        from PIL import Image

        array = image.detach().float().clamp(-1, 1).permute(1, 2, 0).cpu().numpy()
        array = ((array + 1.0) * 127.5).round().astype(np.uint8)
        Image.fromarray(array, mode="RGB").save(path)


def generate(request, output):
    generator = IrisGenerator(
        request["models"], request["variant"],
        request.get("memory_mode", "auto"), output=output,
    )
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


def serve(cache_dir, models, idle_timeout, memory_mode):
    cache_dir = Path(cache_dir)
    queue = cache_dir / "queue"
    queue.mkdir(parents=True, exist_ok=True)
    server_log = cache_dir / "server.log"
    generators = {}
    last_activity = time.monotonic()
    print(f"Iris worker ready; idle timeout {idle_timeout:.0f}s", flush=True)
    while True:
        job = _take_job(queue)
        if job:
            request_path = Path(job["request"])
            output = request_path.parent
            if not (output / "worker.log").is_file():
                _write_log(output, f"Persistent Iris worker\nServer log: {server_log}")
            try:
                request = json.loads(request_path.read_text(encoding="utf-8"))
                variant = request.get("variant", "turbo")
                key = (variant, request.get("memory_mode", memory_mode))
                generator = generators.get(key)
                if generator is None:
                    status(output, f"Loading {MODEL_LABELS.get(variant, variant)}")
                    generator = IrisGenerator(models, variant, key[1], output=output)
                    generators[key] = generator
                generator.generate(request, output)
                last_activity = time.monotonic()
            except Exception as exc:
                traceback.print_exc()
                status(output, "Generation failed", error=str(exc))
            continue
        if idle_timeout > 0 and time.monotonic() - last_activity >= idle_timeout:
            print("Iris worker idle timeout", flush=True)
            return
        time.sleep(0.2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--variant", choices=("base", "turbo"), default="turbo")
    parser.add_argument("--memory-mode", choices=("auto", "low", "high"), default="auto")
    parser.add_argument("--idle-timeout", type=float, default=0.0)
    args = parser.parse_args()
    try:
        if args.serve:
            if args.cache_dir is None:
                raise ValueError("--serve requires --cache-dir")
            serve(args.cache_dir, args.models, args.idle_timeout, args.memory_mode)
        else:
            if args.request is None:
                raise ValueError("--request is required")
            request = json.loads(args.request.read_text(encoding="utf-8"))
            generate(request, args.request.parent)
    except Exception as exc:
        traceback.print_exc()
        if args.request is not None:
            status(args.request.parent, "Generation failed", error=str(exc))
        raise


if __name__ == "__main__":
    main()


