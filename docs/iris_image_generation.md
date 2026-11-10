# Local image generation with Iris-3B

The **Remi Board** generates concept images locally with
[Iris-3B](https://github.com/speridlabs/iris-3b), then generates 3D sources from
separate linked image, 3D, and mesh cards. The old sidebar Generate tab is removed.

```text
Remi Board (image → Gaussian splats → mesh) → Edit in Blender → Apply to Card
```

## What Iris-3B can and cannot do

Iris-3B is a 3-billion-parameter pixel-space diffusion transformer. It generates
about one megapixel directly in RGB with no VAE. The release provides two
sampling variants from the same Hugging Face repository:

| Variant | Size | Steps | Notes |
|---|---:|---:|---|
| Iris-3B | 12 GB FP32 | 100, CFG 3 | More prompt detail and seed variety; negative prompt and step count apply |
| Iris-3B Turbo | 6 GB bf16 | 4, no CFG | Much faster iteration; fixed schedule, no negative prompt |

**It is text-to-image only.** The released pipeline accepts a prompt,
resolution, seed, and CFG. Its frozen Qwen3-VL text encoder consumes text
tokens only; no image features enter the diffusion model. Training prompts were
also text-only. Reference-image conditioning, identity preservation, and image
editing are **not supported** by this integration. Use a dedicated edit model if
that becomes a requirement.

## Requirements

- NVIDIA CUDA GPU with bfloat16 support.
- Python 3.11+ with PyTorch 2.7.1+ (CUDA build), `transformers>=4.57`,
  `safetensors`, `huggingface_hub`, `Pillow`, `numpy`, and `PyYAML`.
- Windows x64, macOS, or Linux for the Blender add-on. The external Python
  environment, not Blender's bundled Python, performs inference.
- About 32 GB system RAM is recommended when both networks are offloaded.
- Disk space: about 15 GB for Turbo + text encoder, or about 21 GB for base +
  text encoder. Downloading both variants adds another 6–12 GB.

The add-on does not install Python packages at runtime. Open **Remi Board → gear**
and point **Python** at a CUDA/PyTorch environment that has these packages. The
same external Python is used by 3D generation and Texture.

## GPU memory

The text encoder is about 9 GB and the diffusion model is 6 GB (Turbo) or 12 GB
(base, FP32).

- **Auto** keeps both networks on the GPU when at least 28 GB is available.
- Below that, the worker uses low-memory mode: it loads the model in bfloat16,
  moves the text encoder to the GPU only while encoding, then moves the
  diffusion model back for sampling. This works on a 12 GB RTX 5070; expect a
  few seconds of transfer time per request.
- Base mode on a 12–24 GB card uses bfloat16 weights. This is a memory
  trade-off; use **Full GPU** only when the card can hold both FP32 networks.

The warm worker keeps the loaded networks in system RAM and reuses them between
requests. **Keep Model Loaded** controls the idle timeout; `0` reloads every
run.

Measured locally on an RTX 5070 12 GB with Turbo at 1024×1024: about 84 seconds
for a cold request (including model and text-encoder load), and about 6 seconds
for a warm 512×512 request.

## Download

Use **Download Model** in **Remi Board → gear**. The downloader is pinned to:

- `speridlabs/iris-3b` revision `adc05b909fb05d4276d26aae5a52b36406b2ef26`
- `Qwen/Qwen3-VL-4B-Instruct` revision `ebb281ec70b05090aa6165b016eac8ec08e71b17`

Both the Iris-3B weights/code and the Qwen encoder are Apache-2.0. Weights are
downloaded from Hugging Face; the add-on uploads no prompts or images. The
selected variant is stored under `Models/iris-3b`, and the encoder under
`Models/Qwen3-VL-4B-Instruct`.

## Workflow

1. Click **Remi Board** in the 3D Viewport header.
2. Write a plain descriptive prompt in the composer and submit it. Use the gear
   for model (Turbo/Base), native resolution, seed, image count, steps, CFG,
   negative prompt, downloads, and runtime settings.
3. Finished images appear as thumbnail cards. Image-generation progress is
   shown on the pending card.
4. Select an image and choose **Generate 3D**. A separate 3D card shows progress
   while the worker runs. Drop external PNG/JPG/WebP files onto the board to add
   image cards.
5. Select a 3D-ready card and use **Create Mesh** (when the optional surface
   tool is installed), then **Edit in Blender**. The existing **Optimize** and
   **Texture** tools are optional; **Apply to Card** updates the same mesh card.

Each request stores a job folder with `image_000.png`, `meta.json`,
`request.json`, `status.json`, and `complete.json`. The gallery scans the output
folder, so images survive Blender restarts. **Refresh** rescans it; **Load
Settings** restores a card's prompt, model, size, seed, steps, and CFG;
**Delete** removes that image and keeps the rest of its batch. The folder icon
next to **Images** opens the output directory.

## Limitations

- Text-to-image only; no reference-image or image-editing conditioning.
- About one megapixel. The listed buckets match Turbo's distillation sizes;
  other multiples of 16 are accepted by the worker but are not the model's
  native training resolutions.
- Base uses 100 steps by default and is much slower than Turbo. `steps` and
  `cfg_scale` are ignored by Turbo because its schedule and guidance are baked
  into the weights.
- Prompt text rendering and exact object counts can be unreliable.
- Like every image generator, outputs may be inaccurate, biased, or unsafe.
  Review generated images before using them.

## Troubleshooting

- **“Missing model files”**: click **Download Model**. Existing verified files
  are reused.
- **“Choose the external CUDA/PyTorch Python”**: set the Python path in
  Generate settings to the environment that already runs 3D generation or
  Texture.
- **CUDA/bfloat16 error**: use a CUDA build of PyTorch on an NVIDIA GPU; Iris
  does not run in Blender's bundled Python.
- **Out of memory**: use Turbo, choose **Auto** or **Low VRAM**, reduce the
  resolution, and generate one image at a time.
- **Slow first request**: the first run loads about 15 GB and builds the text
  encoder. Later requests reuse the warm worker until the configured timeout.
- Worker logs are in the generated job folder (`worker.log`); the warm server
  log is in the per-session cache directory under the system temporary folder.
