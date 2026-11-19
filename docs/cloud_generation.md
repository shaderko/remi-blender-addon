# Blender Cloud generation candidate

Implemented on `codex/cloud-generation-api`, 5 November 2026. Public PAYG generation remains disconnected in the Cloud backend. This client cannot enable an unavailable operation, create an entitlement or select a provider price. Local generation remains separate.

Use the **Remi Cloud** panel to sign in with explicit browser/device approval. Login lasts only for this Blender process; credentials stay in the isolated network worker's memory. No session or provider credential is saved in `.blend`, request files, process arguments or the child environment. Logging out revokes the session; stopping a busy worker may leave revocation unconfirmed. Review account sessions if needed.

Once the server enables generation:

1. Select image (Iris-3B Turbo) or 3D (custom `rand6_100_ncm` native Gaussians). Supply a prompt or one local PNG/JPEG/WebP, seed and supported output settings.
2. Choose an existing output folder and enter a positive EUR spending cap. There is no default price/cap. The backend validates access and inputs, sets its reservation from the accepted engine/price and owns execution and billing.
3. Generate. The private `remi-cloud-<id>/request.json` folder holds the frozen input/settings, exact request and original upload/job keys. Keep it intact. Polling checks only the accepted job; it cannot submit another generation.
4. Use **Import Cloud Result** after verified download. Images are fully decoded by Blender; 3D imports preserve native Gaussian attributes. Import is explicit and does not replace existing scene objects. Source/result files remain in the chosen folder.
5. After interruption, sign in to the original server/account, select that `request.json`, then **Resume Saved Request**. If acceptance was uncertain, this explicit action resends only the original bytes/keys. A known job resumes with reads and download, even if new compute admission is disabled.

**Stop Waiting does not cancel Cloud work.** Its reservation remains authoritative on the server. There is no client promise of a refund, release or provider termination. New submission is blocked in this panel while the displayed request is unresolved; resume it first. Closing/reloading Blender drops the local session and leaves the durable request/output folder intact.

A successful output can have pending billing: the final amount remains unknown and the original spending hold stays reserved. The client distinguishes pending, settled usage and confirmed no-charge outcomes. Missing billing from an older response stays unknown. A settled usage amount is not proof of card collection. Full provider-billed GPU allocation, including startup and idle, is the selected policy; duration evidence, retail rates and Stripe eligibility still gate activation.

## Local recovery and security boundary

The stdlib worker uses only Remi's versioned authenticated API. Signed artifact downloads receive no bearer, cookies or proxy credentials and reject redirects. Remote filenames never become local paths. Downloads are bounded, checked by size/SHA-256 and validated for the selected PNG/native format before atomic no-clobber publication. Existing different bytes and symlinks are rejected. Request facts use private files, checksum envelopes, `fsync` and no-clobber writes. Checksums detect changes; they do not authenticate an attacker-controlled local journal. POSIX permissions are enforced; Windows ACL behavior and network filesystems need deployed/platform verification. There is no automatic local cleanup or selected server retention promise.

Response validators are exact stdlib copies of `remi-cloud-app/sdks/python/src/remi_cloud/{models,errors}.py`; their SHA-256 values and SDK/API versions are recorded in `integrations/remi_cloud/contract/provenance.json` and verified during tests and archive validation. No `httpx`, keyring, Modal SDK, runtime pip installation or inference environment is needed for Cloud requests.

Real loopback HTTP/process tests cover authenticated submission, exact input/key replay, lost server and local acknowledgments, actual worker death after acceptance, account/API binding, disabled admission, credential/payment/scope failures, job/output mismatch, pending/unknown/no-charge billing, bounded/corrupt/redirected downloads, no secret persistence, immutable local output and source integrity. An actual Blender 5.2.1 regression covers image decoding, custom native import, original scene preservation, explicit recovery, local stop semantics and register/unregister reset. The full release gate passes on macOS/Blender 5.2.1, including actual scene save/reload and installed archive/operator validation. Add-on-generated image/3D wire bodies pass the backend request parser, and the frozen image passes its full decoder; this does not start a backend server or prove a deployed API.

These fixtures do not prove deployed Remi API behavior, Modal permissions/lifecycle, GPU metering, real object storage, Windows execution, Stripe enrollment/invoices, production recovery or legal/model rights. Those remain Cloud goal gates. No live provider run or payment is performed by these tests.
