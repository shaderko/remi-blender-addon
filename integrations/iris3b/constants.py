"""Pinned Iris-3B release metadata shared by the add-on and the worker."""

MODEL_REPO = "speridlabs/iris-3b"
MODEL_REVISION = "adc05b909fb05d4276d26aae5a52b36406b2ef26"
TEXT_ENCODER_REPO = "Qwen/Qwen3-VL-4B-Instruct"
TEXT_ENCODER_REVISION = "ebb281ec70b05090aa6165b016eac8ec08e71b17"

MODEL_FILES = {
    "base": ("config.yaml", "model.safetensors"),
    "turbo": ("turbo/config.yaml", "turbo/distill.yaml", "turbo/model.safetensors"),
}
MODEL_BYTES = {
    "base": 11_950_112_680,
    "turbo": 5_975_089_200,
}
TEXT_ENCODER_FILES = (
    "chat_template.json",
    "config.json",
    "generation_config.json",
    "merges.txt",
    "model-00001-of-00002.safetensors",
    "model-00002-of-00002.safetensors",
    "model.safetensors.index.json",
    "preprocessor_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "video_preprocessor_config.json",
    "vocab.json",
)
TEXT_ENCODER_BYTES = 8_875_719_344

MODEL_LABELS = {
    "base": "Iris-3B",
    "turbo": "Iris-3B Turbo",
}

SIZE_BUCKETS = {
    "SQUARE": (1024, 1024),
    "PORTRAIT": (896, 1200),
    "TALL": (896, 1344),
    "WIDE": (1200, 896),
    "WIDE_TALL": (1344, 896),
    "CINEMA": (752, 1344),
    "CINEMA_WIDE": (1344, 752),
}
