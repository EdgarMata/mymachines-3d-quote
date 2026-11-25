# Architecture

## Components
- Frontend: React 19, TypeScript, Vite 8, Three.js / React Three Fiber and Drei. Nginx serves the production bundle and proxies `/api/` to FastAPI, stripping the prefix.
- Backend: Python 3.13 in Docker, FastAPI, Trimesh, NumPy and OrcaSlicer CLI. Main API module: `backend/app/main.py`. Services: `upload_validation.py`, `orientation.py`, `machine_selector.py`, `pricing.py`, `printing_config.py`, `slicer.py`.
- Configuration: `config/` and `profiles/`; inspect their actual schemas before edits. Profiles specify printer, process and filament settings.
- Slicer: official OrcaSlicer 2.4.2 AppImage extracted into `/opt/orca`; `/opt/orca/AppRun` is the CLI entrypoint. Dockerfile selects AMD64/ARM64 assets and verifies pinned SHA-256 checksums.
- Local Docker Compose uses frontend port 8080 and backend port 8000. Production Compose uses an isolated application network for backend communication and a separate frontend connection to the reverse proxy network. Do not expose the backend directly.

## Quote flow
1. Load `/options` for materials, qualities and machines.
2. Upload STL and select orientation in viewer; `/analyze` applies rotation, measures geometry and selects a display machine without slicing.
3. `/estimate` validates inputs, analyzes geometry, writes original and oriented STL to temporary paths, selects a compatible machine and calls the slicer.
4. Calculate price using actual slicer material cost and print time plus the selected machine's pricing configuration. Return customer-facing quote and useful manufacturing estimates.
5. Oversized or unavailable compatible machines require technical review rather than an automatic price.

Legacy `/upload` and `/slice` endpoints also accept STL. Review whether they need to be exposed in production; do not remove without checking consumers and tests.

## Machine selection
Select the smallest eligible compatible machine, using oriented dimensions and the existing configuration criteria (`quoting_enabled`, `profile_ready`, `operational`). Preserve the actual implementation's ordering and regression tests.

| Machine | Build volume (mm) | Nozzle | Quoting enabled at snapshot |
|---|---|---|---|
| Prusa MK3S | 250 × 210 × 210 | 0.4 mm | Yes |
| MyCloner | 235 × 235 × 220 | 0.4 mm | Yes |
| MyMini | 180 × 180 × 180 | 0.4 mm | No |
| MyCoreXY | 400 × 400 × 400 | 0.4 mm | No |
| MyGiga1M3 | 1000 × 1000 × 1000 | 0.8 mm | No |

Materials: PLA, PETG. Quality: Draft 0.30 mm, Standard 0.20 mm, Fine 0.12 mm. Defaults: Prusa MK3S, PLA, Standard. Enabled in configuration is not proof of physical availability or production qualification.

## Pricing
The existing pricing module is the authoritative calculation; do not infer or change its formulas from this document. Existing tests cover reference pricing, setup/post-processing labour and failure-exposed costs. Public pricing currently includes machine, quantity, selling price and minimum-order flag. Review the rest of the response for leaks. Commercial pricing must be loaded from private configuration outside the public repository.

## Security boundary
The API and slicer process untrusted files. File extension alone is insufficient. TemporaryDirectory handles normal cleanup in several endpoints, but slicer outputs and crash recovery need inspection. Current async endpoints run synchronous computational work; protect event-loop responsiveness and constrain active work. Never rely on container CPU/RAM limits alone to prevent denial of service.

Upload protection is shared across all four STL endpoints: bounded file reads (50 MiB by default), sanitized display filenames and application-owned temporary filenames. ASGI middleware caps the complete request at the file limit plus 1 MiB before multipart parsing completes, including requests without Content-Length. Nginx caps request bodies at 51 MiB and buffers them before proxying. These byte limits do not yet bound geometry complexity or concurrent work.
