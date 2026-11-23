# Local Codex images (experimental)

In Image generation settings choose **Codex (experimental)** and **Continue with ChatGPT**.
Sign in in your system browser, return to Blender, and use the normal image composer.
Select one or more ready image cards before submitting a prompt to use them as
context. Selected images appear in the selection strip; the composer shows the
context count. Deselect all image cards for text-only generation. References are
ordered by the board's card order: refer to them as image 1, image 2, and so on.
Up to 16 PNG/JPEG/WebP images totaling 20 MiB are accepted. This input path is for
the Codex provider; Iris/Remi generation keeps its existing behavior.

Each queued prompt retains its submitted selection. The worker checks that the
files have not changed, freezes their bytes before sign-in/generation, and uses
the native image-edit endpoint with ordered inline image data. Missing or changed
files cause an explicit failure. Every image in a batch uses the same frozen inputs.
The output links to the first reference on the board; all reference IDs and local
snapshots are recorded in its job metadata. Normal project upload sync shares the
completed result and its first-source board connection.

Remi remembers this connection across Blender restarts using macOS Keychain or
Windows user-bound DPAPI encryption. Disconnect ChatGPT removes the saved login.
Generation opens sign-in only if no usable saved connection exists. Blender's Allow Online Access must be enabled.
No separate Python, Node installation, model download, API key, or Codex CLI is needed.

Image requests run on this computer in an isolated worker using the prototype's native
Codex image endpoint and public Codex browser registration. This is an experimental
integration: OpenAI's documented third-party Sign in with ChatGPT flow currently
lists image generation as unsupported. The prototype succeeded with Codex's
registration and returned 401 with its own OSS registration. Being local and open
source does not establish supported third-party image access. Availability can change.
See [OpenAI preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations).

OAuth tokens and refresh tokens stay in the isolated worker and OS-protected
credential storage. They never appear in a .blend file, image metadata, IPC
status file, log, or Remi server request. Refresh-token rotations are serialized
across Blender processes; temporary outages preserve saved credentials. The worker verifies
the ID-token signature and identity, nonce and PKCE state. It binds the sign-in
callback to loopback port 1455; another sign-in using that port must finish first.
Cancel terminates the local worker but keeps the saved login. Disconnect removes it. An upstream image
request may already have consumed plan usage; cancellation does not refund it.

Completed PNG files are validated and enter the selected board as ordinary images.
On a shared project, existing workspace uploads and WebSocket synchronization share
them with the browser and team. Project switching is blocked during generation;
if the account/board changes externally, completed files stay local rather than being
inserted into another project's board. Remi Cloud only receives completed image files.
The local board keeps images local. Blender scene transforms are not synchronized.

Quality, count and requested size are supported controls; actual dimensions from the
PNG are recorded, because the native endpoint can ignore requested dimensions.
Iris-specific seed, negative prompt and model controls do not apply to Codex.
3D generation continues using the existing independent Remi Local/Cloud backend.

Validation uses synthetic OAuth signatures, a real loopback callback, mocked upstream
image responses, a real isolated subprocess, and Blender board insertion/upload tests.
No automated test spends ChatGPT plan usage or signs into a real account.
