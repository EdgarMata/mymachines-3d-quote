# Project status — 2026-11-25

Snapshot based on maintainer-observed results. Verify current code and CI before acting.

## Completed
- Public source repository: https://github.com/EdgarMata/mymachines-3d-quote (AGPL-3.0).
- Docker builds for native AMD64 and ARM64 with verified OrcaSlicer 2.4.2 downloads.
- GitHub Actions CI for both architectures, including synthetic STL end-to-end slicing and machine/pricing regressions. The maintainer confirmed successful MM-001 CI for commit `4468b16` on AMD64 and ARM64 (20 tests).
- Automatic SPDX JSON SBOM artifacts for each image and architecture; CI #6 succeeded and all four artifacts were present.
- Project license and third-party notices included in images; OrcaSlicer license and source documentation included in backend. Vite generates `THIRD_PARTY_LICENSES.md` during production build; local build identified 15 MIT-declared JavaScript dependencies and confirmed the file inside the frontend image.
- Commit `d25bcf2` (`Generate third-party JavaScript license notices`) built natively on an ARM64 deployment host. Backend and frontend running in private test deployment; backend healthy; frontend and `/api/options` HTTP 200.
- OrcaSlicer CLI and all nine tests passed in the private ARM64 validation.
- Private browser access via SSH forwarding; `/analyze` and `/estimate` returned HTTP 200 and maintainer confirmed reference quote matched expectations.

## In progress / not yet done
- Public API hardening. MM-001 is committed and pushed as `4468b16`, but not deployed. Upload protection is implemented and tested: shared validation for all four endpoints, 50 MiB default file limit, positive integer configuration validation, safe display filenames, fixed temporary filenames, pre-parser request-body protection and a 51 MiB Nginx limit. MM-002 geometry/parameter validation is committed and pushed as `b8c7654`; the maintainer reports successful CI with 58 tests. MM-003 concurrency/slicer timeout protections are implemented and reviewed (see below). No deployment is included. Storage policy and the full response review remain pending.
- Licensing review for redistribution of Docker binaries remains incomplete. SBOMs are inventory, not compliance proof; extracted OrcaSlicer and bundled frontend dependencies require separate coverage. Verify profile/resource provenance, applicable license notices and corresponding-source obligations.
- Manual GHCR publication workflow exists, requires successful CI, but has not been executed. Public images are not yet published. Publication rebuilds images; do not claim byte-identical artifacts to CI builds.
- Private deployment uses locally built images and an untracked override. No automatic deployment or public endpoint yet.
- Real commercial pricing and physical print profile validation remain pending. Do not treat demo prices as approved sales prices.
- Odoo integration deferred.

## Known security findings
After local MM-003: heavy work runs in bounded threads with fail-fast admission and a slicer deadline, but there is no hard global request/geometry deadline. The raw `slicing` object still needs an explicit response allowlist. Geometry validation cannot guarantee topology repair, printability or bounded CPU time. Domain errors and caught processing failures no longer return exception text; public orientation results omit temporary paths. Quantity is bounded before geometry work. Inspect service implementations and frontend consumers before changes. Existing `TemporaryDirectory` cleanup is useful but not a complete storage policy.

## Local verification — 2026-11-25
- Before changes: all nine existing tests passed in the local Docker backend, including real OrcaSlicer integration.
- After changes: all 20 tests passed using read-only source/test mounts in the existing Docker backend image. This includes 11 new upload/request-limit tests. Nginx configuration syntax passed in the local frontend image.
- MM-001 final review: verified multipart temporary-file cleanup after interruption, streaming limits without or with understated Content-Length on all four endpoints, and generic validation errors. No credentials, private commercial prices or host access identifiers are included in the accompanying documentation.
- MM-001 source commit and push are authorized by the maintainer. Image publication, deployment and subsequent implementation stages remain outside this authorization.

## MM-002 — implementation and final review
- Binary/ASCII structural preflight, facet count and finite raw values checked before Trimesh loading. Shared mesh validation rejects corrupt, empty and wholly degenerate geometry; non-watertight usable surfaces remain eligible for slicing.
- Default limits: `MAX_STL_TRIANGLES=1000000`, `MAX_QUANTITY=100` (minimum 1). Both settings are validated at startup. Existing upload/request limits are unchanged.
- Finite rotations and configured material/quality pair compatibility checked before geometry processing. The validated mesh is reused for orientation, preserving Three.js XYZ transforms, machine-selection statuses and pricing formulas.
- Before changes: all 20 tests passed in the local Docker backend. After changes: all 58 tests passed in the local Docker backend (20 existing and 38 new), including real OrcaSlicer binary/ASCII estimates. Docker Compose validation and `git diff --check` passed. Boundary tests use reduced configured limits with synthetic geometry to exercise below/at/above cases without storing large fixtures.
- Final-review corrections: require valid positive print time/material mass before pricing; validate complete numeric G-code tokens; permit planar analysis in the dimension guard; use exact duplicate merging outside Trimesh's safe int64 quantization range. No new physical size cap is introduced. The existing MM-001 mock slice fixture now includes positive filament mass; its assertions are unchanged.
- Additional regressions cover real OrcaSlicer handling of a zero-volume surface, empty/malformed/non-finite slicing statistics, extreme finite input/oriented binary export and forged/multi-solid triangle counts. Original MM-001 tests remain functional.
- Commit and SSH push for the reviewed MM-002 changes are authorized. No image publication, VPS action or concurrency/timeout/rate-limit implementation is part of this step.

## MM-003 — implementation and final review
- Shared pre-multipart admission for all four heavy endpoints; default one active request, no waiting queue, HTTP 503 / Retry-After 5 when busy. Whole synchronous pipelines use the bounded executor; health/options stay accessible.
- Cancellation does not release admission while a real worker is active. Temporary directories remain owned by the worker until work and process cleanup finish.
- Default 60-second slicer subprocess deadline; typed safe HTTP 504 after process-group termination and direct/adopted descendant reaping. Linux child-subreaper support uses the standard library. Compose init is enabled and one Uvicorn worker is explicit; production CPU/RAM limits are unchanged.
- Nginx upstream read inactivity timeout is 90 seconds. UI messages handle 503/504, including HTML responses; no automatic retry was added.
- Baseline: 58 tests passed in Docker before changes. Final suite: all 84 tests passed in the source-mounted local Docker backend (58 pre-MM-003 and 26 MM-003 tests), including concurrency/cancellation/process-tree regressions and real OrcaSlicer quotes. Development/production Compose validation, Nginx syntax and `git diff --check` passed. Existing synthetic slicer tests now patch the process runner instead of `subprocess.run`; their assertions are preserved.
- Frontend build and three error-message tests pass. Full lint has a confirmed pre-existing set-state-in-effect error in `App.tsx`; new helper/test files pass targeted lint. Bundle-size warning remains pre-existing.
- Final-review improvements: bounded simultaneous stdout/stderr capture (1 MiB tail per stream), non-reaping parent-exit observation before signaling, and regressions for atomic release, parser/cleanup/launch failures, disconnects, concurrent groups and unrelated child ownership. All original 74 backend checks and three frontend checks are preserved.
- CI runs the three frontend tests with Node 24 in both existing native architecture jobs. Full frontend build passes; the existing lint error is recorded in the roadmap without suppressing the rule.
- MM-003 source commit and SSH push are authorized after final checks. No GHCR publication, VPS access, rate limiting or MM-004 work is included. Slow uploads/abuse, hard geometry deadlines, cross-process admission and real VPS load validation remain pending.
