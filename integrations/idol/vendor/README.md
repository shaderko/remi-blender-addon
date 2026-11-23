# Idol SDK

Source: https://github.com/speridlabs/idol-sdk
Commit: 8db700349a5570aac390e4780fa32e3eed6b587a

The `idol/` Python package is copied unchanged. Apache-2.0 license is retained
in `LICENSE`. Source bytes are pinned in `../source-lock.json` and the runtime
manifest. The surrounding adapter supplies the existing verified offline image
encoders through the SDK's loaded-model API and emits Remi's native format.

Idol builds on TripoSplat. Its upstream acknowledgement also credits DINOv3,
FLUX.2 VAE, and BiRefNet. Existing encoder weights and notices remain in the
immutable base image.
