# Cloud authentication consolidation — 19 November 2026

Historical consolidation record. Current generation uses the existing Local/API
controls described in [generation](cloud_generation.md).

Development branch: `codex/cloud-auth-cleanup`. Base: `cleanup/5.3-only`
at `d48faeaef1b44b5978e80f9bc18ead00bad47dbc`.

The verified ancestry is linear:

```text
main 0414d84
  model-generation eab6597
    design/ui-shell 99c5432
      feature/adaptive-quad-density-ship a61fb7e
        cleanup/5.3-only d48faea
          codex/cloud-auth-cleanup
```

Using the cleanup branch retains the board, adaptive retopology, native Blender
5.3 Gaussians and removal of legacy compatibility operators. The old adapter's
source was ported onto this base; conflicts retain both the board and Cloud
registration, packaging checks and regressions. Cloud import uses the current
native Gaussian API. Local generation and the board's job queue remain local.

All original dirty work is preserved in the pushed branch
`codex/backup-cloud-adapter-20261119`: source snapshot `9a0fb88`, then native
binary backup `b43b6b9`. All 29 original dirty-file SHA-256 hashes match that
backup tree, including all untracked files. A verified local tar, patch and
manifest also exist at `/private/tmp/remi-preserve-20261119` (temporary storage).
The previous branch and existing stash were retained.

The cleanup tree's checked-in macOS Instant Meshes binary reported guide API 0,
while its source and installed smoke require API 1. Both macOS modules were
rebuilt from the current checked-in source with CPython 3.13, CMake and pybind11
3.0.1, then ad-hoc signed by `scripts/release.py native`. They reproduce the
saved local binary bytes exactly and pass the installed native/UV checks. Their
build commit is separate from the authentication source changes.

Authentication now has a board-header account popover. Browser/device approval,
profile refresh and logout are independent of generation capability discovery.
Unavailable discovery drops stale admission flags while retaining the account;
Refresh retries it. Paid generation remains gated by explicit server capabilities
and the original spending/request-recovery rules.

Current local evidence:

- 32 loopback Cloud auth/generation tests; the optional real-backend interop test
  also passes with the local `remi-cloud-app` checkout's actual Better Auth plugin
  and disabled API routes, using only an ephemeral in-memory database.
- 41 backend/frontend auth, device-page, API, paid-gate and file-validation tests.
- 16 native generation/data tests and 18 board model tests.
- Blender 5.3.0 `02f0899d78e3` on macOS arm64: account, generation/import and
  preserved mesh conversion regressions; real-window board/account rendering
  inspected; build, archive validation, clean installed extension, installed
  account regression and installed board mesh/bake regression all pass.

The new backend is not deployed. No live API, paid GPU run, payment configuration
or production deployment was used. Windows/Linux execution and the full release
suite are not established by these checks. Neither Cloud checkout was edited.
Main/release branches and tags are unchanged. The existing workflow runs on PRs,
main/model-generation pushes or explicit dispatch; these development/backup
branch pushes do not trigger it, and no release was published.
