# Cloud downloads

The board worker reads project/job metadata without resolving generated-file
tickets. `Downloads` owns a queue of immutable `(project, file ID)` references.
Up to three independent, project-bound sessions resolve tickets and download
files. Uploads and board controls continue independently.

Each card reports queued, downloading, verifying, retrying, failed, or ready.
Transient failures get three attempts with 2-second and 10-second retry delays;
fresh cards run before retries. Failed cards expose **Retry Download** in their
context menu. This fetches the existing file and submits no generation job.
Project changes and sign-out cancel outstanding transfers.

The shared SDK uses a 15-second socket timeout and a 300-second body-transfer
deadline. Original bytes stream through bounded decompression into a unique
temporary file. Size, gzip integrity, and SHA-256 must pass before atomic cache
publication. Gaussian byte planes are restored before verification. Restarting
reuses verified complete cache files and restarts incomplete transfers.

IPC publication retries brief Windows reader/antivirus locks. Persistent status
write failures retain the current snapshot for a later publication attempt.
The worker publishes a heartbeat every five seconds without advancing the board
revision. After 60 seconds without a heartbeat, pending cards show a connection
error and offer reconnection. The Blender timer survives local update errors.

Validation includes real Windows deny-delete file handles, lock fault injection,
three-transfer limits, retry exhaustion/recovery, project switching, byte-exact
streaming decompression, cancellation, and Blender card-state recovery.
