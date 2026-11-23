# IDOL and local/Cloud 3D generation

The Generate 3D settings choose model and execution together:

- **IDOL · Cloud** uses the signed-in project's server. The connection must
  advertise `modelGeneration.model=Idol`; a different engine is rejected before
  creating a job. Seed and Gaussian count go through the same Cloud workflow.
- **IDOL · Local** uses the configured external CUDA/PyTorch Python. Install the
  additional requirements from `integrations/idol/requirements.txt` into that
  environment, then use Download Models. It verifies pinned IDOL weights and
  reuses the existing DINOv3/FLUX.2 encoders. It needs CUDA with bfloat16 support.
- **NCM · Local** retains the existing eight-level NCM runtime and downloads.
- **TripoSplat · Local** retains the existing compatibility path.

The 3D choice is separate from the image provider and shared project. Local
results on a Cloud project still enter the board's normal shared-asset upload
path. Existing saved scenes keep their model choice; old Cloud backend selection
resolves to the advertised IDOL model, rather than pretending Cloud runs NCM.
New local settings default to IDOL; entering a shared project on a fresh profile
defaults to IDOL Cloud. An explicit local choice survives project changes.

IDOL sources match Cloud's Apache-2.0 SDK commit
`8db700349a5570aac390e4780fa32e3eed6b587a`; source and model manifests are packaged,
weights are not. The local SDK uses eager execution to avoid a compilation
requirement on a fresh Windows machine. This means it does not include Modal's
GPU snapshot startup optimization. Inputs, steps, seed and Gaussian count are
explicit. The default 20 steps matches the current Cloud deployment. Output arrays retain the Cloud SDK's Z-up frame and WXYZ rotations.

The shared-file cache uses 32 hexadecimal content-key characters instead of a
UUID plus full digest. Full size and SHA-256 verification still happen on every
new download. This keeps typical Windows Blender profiles below MAX_PATH without
requiring a Windows registry change. Existing account/project scope folders,
outboxes and upload receipts are preserved; missing cache files redownload.

Validation includes a simulated Windows MAX_PATH write rejection, shared SDK
capability/recovery tests, pinned IDOL download/reuse tests, synthetic IDOL output
array checks, and Blender engine/queue routing tests. Real local IDOL GPU inference
has not been run on the development Mac; model-dependent output tests use a
synthetic pipeline. Real Windows verification still requires a Windows machine.
