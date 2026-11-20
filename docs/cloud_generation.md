# Generation through Local or API

Beta.13 uses the existing image and 3D generation controls for both backends.
Open the existing settings gear and choose **Local** or **API** under **Run
Generation**. The selection applies to image generation and the board's 3D
queue. Model, seed, Gaussian count, image size, image count and output settings
stay in their existing places. Switching backends preserves those settings.
Local Python/model downloads and cache controls appear only in Local mode.

For API generation, sign in through the board header's **Account** menu using
`https://remi3d.app/api/v1`, then use the normal **Create Image** or **Generate
3D** action. There is no separate Cloud generation form. Results use the same
image gallery and ready 3D cards/native import path as local results.

The currently deployed API runs Iris-3B Turbo and the custom Remi NCM model.
Image requests currently accept up to 1024 pixels per side. Unsupported model
or size choices fail before submission rather than silently changing the model
or dimensions. Multiple images submit sequentially with incrementing seeds.
The per-job EUR ceiling defaults to 1.00; the server owns actual reservations
and billing. It is a spending limit, not a quoted generation price.

**Stop Waiting does not cancel an accepted API job.** Work continues on the
server. Its durable request folder retains frozen inputs and original request
keys. In Account, select the saved `request.json` and use **Resume API Request**
to recover interrupted work. Completed results and source files stay on disk.
No automatic deletion is introduced.

The add-on delegates authentication, HTTP, request identity, upload, polling,
recovery and verified downloads to the shared Python SDK snapshot at
`integrations/remi_cloud/sdk`. The canonical source is the Cloud repository's
`sdks/python/src/remi_cloud`; `tools/dev/sync_addon_sdk.py /path/to/remi --check`
checks exact byte identity. Release validation checks its provenance hashes.

Device credentials stay in the isolated helper process's memory, outside
`.blend`, request journals, logs and process arguments. Blender needs no Modal
or Stripe credential and no inference Python for API requests. On macOS,
embedded Python without default certificate roots loads the trusted host CA
bundle while retaining certificate and hostname verification.

Validation covers the original image action, image batches/gallery metadata,
the board 3D queue, unchanged local configuration, unsupported model rejection,
shared SDK authentication/recovery, and installed extension checks. Integration
fixtures use loopback HTTP, not paid GPU execution. These checks do not establish
production PAYG, Stripe collection, model rights or legal readiness.
