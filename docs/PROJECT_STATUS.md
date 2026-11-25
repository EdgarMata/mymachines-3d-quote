# Project status — 2026-11-25

Snapshot based on maintainer-observed results. Verify current code and CI before acting.

## Completed
- Public source repository: https://github.com/EdgarMata/mymachines-3d-quote (AGPL-3.0).
- Docker builds for native AMD64 and ARM64 with verified OrcaSlicer 2.4.2 downloads.
- GitHub Actions CI for both architectures; nine tests per architecture, including synthetic STL end-to-end slicing and machine/pricing regressions.
- Automatic SPDX JSON SBOM artifacts for each image and architecture; CI #6 succeeded and all four artifacts were present.
- Project license and third-party notices included in images; OrcaSlicer license and source documentation included in backend. Vite generates `THIRD_PARTY_LICENSES.md` during production build; local build identified 15 MIT-declared JavaScript dependencies and confirmed the file inside the frontend image.
- Commit `d25bcf2` (`Generate third-party JavaScript license notices`) built natively on an ARM64 deployment host. Backend and frontend running in private test deployment; backend healthy; frontend and `/api/options` HTTP 200.
- OrcaSlicer CLI and all nine tests passed in the private ARM64 validation.
- Private browser access via SSH forwarding; `/analyze` and `/estimate` returned HTTP 200 and maintainer confirmed reference quote matched expectations.

## In progress / not yet done
- Public API hardening. Local upload protection is implemented and tested: shared validation for all four endpoints, 50 MiB default file limit, positive integer configuration validation, safe display filenames, fixed temporary filenames, pre-parser request-body protection and a 51 MiB Nginx limit. Not deployed. Geometry validation, processing bounds and response review remain pending.
- Licensing review for redistribution of Docker binaries remains incomplete. SBOMs are inventory, not compliance proof; extracted OrcaSlicer and bundled frontend dependencies require separate coverage. Verify profile/resource provenance, applicable license notices and corresponding-source obligations.
- Manual GHCR publication workflow exists, requires successful CI, but has not been executed. Public images are not yet published. Publication rebuilds images; do not claim byte-identical artifacts to CI builds.
- Private deployment uses locally built images and an untracked override. No automatic deployment or public endpoint yet.
- Real commercial pricing and physical print profile validation remain pending. Do not treat demo prices as approved sales prices.
- Odoo integration deferred.

## Known security findings
After local upload hardening: malformed/empty/non-finite geometry still needs consistent validation; exception strings can be returned to clients; synchronous heavy work inside async handlers; raw `slicing` object is returned and orientation paths may leak in `/estimate`. Quantity is not bounded at endpoint level. Inspect service implementations and frontend consumers before changes. Existing `TemporaryDirectory` cleanup is useful but not a complete storage policy.

## Local verification — 2026-11-25
- Before changes: all nine existing tests passed in the local Docker backend, including real OrcaSlicer integration.
- After changes: all 20 tests passed using read-only source/test mounts in the existing Docker backend image. This includes 11 new upload/request-limit tests. Nginx configuration syntax passed in the local frontend image.
- MM-001 final review: verified multipart temporary-file cleanup after interruption, streaming limits without or with understated Content-Length on all four endpoints, and generic validation errors. No credentials, private commercial prices or host access identifiers are included in the accompanying documentation.
- MM-001 source commit and push are authorized by the maintainer. Image publication, deployment and subsequent implementation stages remain outside this authorization.
