# Vendored Iris-3B inference code

Source: <https://github.com/speridlabs/iris-3b> at commit
`adc05b909fb05d4276d26aae5a52b36406b2ef26`, licensed under Apache-2.0
(see `LICENSE` and `NOTICE`).

Only the inference path is retained: configuration dataclasses, the diffusion
transformer, flow solver, Qwen3-VL language-stack text encoder, and sampling
helpers. Training, data, distillation, and downstream fine-tune code are not
vendored.

Local changes:

- `iris3b/config.py` loads the exported YAML with PyYAML instead of the
  training stack's OmegaConf dependency.
- The upstream `iris3b.models` package is named `iris3b.net` here because the
  Blender extension manifest excludes any directory named `models` (the add-on
  uses that pattern for downloaded weights).
- `iris3b/text/qwen3_vl.py` sanitizes a local checkpoint path for the
  machine-local null-embedding cache filename.

Everything else follows the upstream inference implementation.
