# Roadmap — MVP release gates

## Now: secure the private MVP
Work locally, use small tested changes, then request approval for commit/push and private deployment updates.

1. MM-001 — Secure STL Upload Validation: implemented and locally verified. Shared extension, empty-file and bounded-read checks protect all four endpoints. The default file limit is 50 MiB with validated configuration; the complete request limit is 51 MiB, aligned with Nginx and enforced during reception even without Content-Length. Client filenames are display metadata only. Tests cover exact boundaries, oversize rejection, malicious names and multipart cleanup after interrupted reception. MM-002 adds geometry/parameter checks and generic processing errors; a full response allowlist review remains pending. No deployment is implied by completion of this source change.
2. MM-002 — Geometry and Printing Parameter Validation: implemented, reviewed and tested; not deployed. Shared binary/ASCII structural preflight and finite usable geometry checks; configurable 1,000,000-facet limit; finite rotations; configurable quantity maximum (default 100, minimum 1); material/quality pair checks. A validated mesh is reused for orientation. Non-watertight models with usable surface remain allowed; automatic pricing requires valid positive slicing time and material mass. Extreme finite coordinates use overflow-safe merging and checked binary export. Synthetic regression tests and real binary/ASCII OrcaSlicer estimates cover the new path. See architecture for acceptance policy and known limits.
3. Concurrency and timeout: inspect `slicer.py` first. Proposed one active expensive job per backend instance, 60 s slicing timeout; bound waiting/queue length and avoid blocking the event loop. Ensure timeouts terminate the actual child process, release admission permits, and clean up. Define explicit busy/error responses. Test concurrent requests, health responsiveness, cancellation and timeout recovery. Consider total request deadline and multi-worker implications.
4. Temporary storage: inspect all outputs and logs; retain files only as needed, clean after normal completion and failure, recover abandoned directories safely. Proposed maximum abandoned-file retention one hour; never delete active jobs. Bound disk usage and inspect G-code retention decision.
5. Public API: use explicit allowlists for response fields, remove internal filesystem paths and cost details, log detailed errors server-side without secrets or customer geometry. Check machine selection and frontend field dependencies. Restrict legacy endpoints and API docs if not needed. Rate-limit expensive requests at the trusted proxy boundary (initial proposal: 10 estimate requests/minute/IP, validate before release). Prevent bypass via forged forwarded headers. Add tests.
6. Private commercial configuration: mount outside repository and build context; validate schema and defaults; do not leak via API, logs or published images. Keep demo config for reproducible tests.

All numerical limits above are provisional engineering proposals, not approved commercial policy. Validate representative real models and simultaneous requests; adjust with maintainer approval.

## Next: release and deployment
- Resolve applicable third-party license, source availability and resource provenance requirements. Public source availability alone is not a complete binary distribution audit.
- Publish approved multi-architecture images to GHCR, verify manifests and immutable commit tags. Improve traceability between tested and released images as needed.
- Automate deployment with least-privilege credentials, verified SSH host keys, serialized releases, health checks and rollback by immutable tag. Docker access is privileged. Never expose deploy secrets to untrusted PR jobs.
- Validate private ARM64 deployment, resource usage under realistic load, service isolation and restart/recovery. Confirm no adverse effect on other hosted services.
- Configure public DNS, reverse proxy, TLS, upload/timeouts and rate limits only after maintainer approval. Verify externally; provide a visible source/license link as applicable.

## Later / backlog
Odoo CRM and sales integration; customer/order portal; validated commercial prices and physical print qualification; frontend bundle splitting; new materials/machines/features. Do not expand scope until current release gates are addressed.
