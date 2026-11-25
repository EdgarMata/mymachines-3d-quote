# Architecture

## Components
- Frontend: React 19, TypeScript, Vite 8, Three.js / React Three Fiber and Drei. Nginx serves the production bundle and proxies `/api/` to FastAPI, stripping the prefix.
- Backend: Python 3.13 in Docker, FastAPI, Trimesh, NumPy and OrcaSlicer CLI. Main API module: `backend/app/main.py`. Services: `upload_validation.py`, `geometry_validation.py`, `input_validation.py`, `processing.py`, `slicer_process.py`, `temp_storage.py`, `orientation.py`, `machine_selector.py`, `pricing.py`, `printing_config.py`, `slicer.py`.
- Configuration: `config/` and `profiles/`; inspect their actual schemas before edits. Profiles specify printer, process and filament settings.
- Slicer: official OrcaSlicer 2.4.2 AppImage extracted into `/opt/orca`; `/opt/orca/AppRun` is the CLI entrypoint. Dockerfile selects AMD64/ARM64 assets and verifies pinned SHA-256 checksums.
- Local Docker Compose uses frontend port 8080 and backend port 8000. Production Compose uses an isolated application network for backend communication and a separate frontend connection to the reverse proxy network. Do not expose the backend directly.

## Quote flow
1. Load `/options` for materials, qualities and machines.
2. Upload STL and select orientation in viewer; `/analyze` applies rotation, measures geometry and selects a display machine without slicing.
3. `/estimate` validates parameters and geometry once, reuses the mesh for orientation, writes only the oriented STL to a temporary path, selects a compatible machine and calls the slicer.
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
The API and slicer process untrusted files. File extension alone is insufficient. TemporaryDirectory handles normal cleanup; MM-004 adds lock-aware recovery of recognized abandoned workspaces and contains helper output. Heavy synchronous pipelines run in a bounded thread executor; ASGI admission limits active uploads and work before multipart parsing. Never rely on container CPU/RAM limits alone to prevent denial of service.

Upload protection is shared across all four STL endpoints: bounded file reads (50 MiB by default), sanitized display filenames and application-owned temporary filenames. ASGI middleware caps the complete request at the file limit plus 1 MiB before multipart parsing completes, including requests without Content-Length. Nginx caps request bodies at 51 MiB and buffers them before proxying. Geometry complexity is bounded separately by MM-002; MM-003 limits admitted heavy requests.

## MM-002 — geometry and printing parameter validation

`geometry_validation.py` performs a structural preflight before calling Trimesh.
Binary STL requires an exact `84 + 50 * triangle_count` byte length. The declared
count is checked before array processing; binary headers beginning with `solid`
remain supported. Raw vertex coordinates and facet normals must be finite.
ASCII STL is scanned sequentially before geometry allocation, validating complete
facet/loop/vertex blocks and counting facets across all solids. It supports normal
line-based STL with scientific notation, whitespace/blank lines and multiple solid
blocks. Lines are limited to 4096 bytes to bound token allocation; incomplete
blocks, trailing junk and nonstandard numeric syntax are rejected. Binary files
with truncated records or trailing bytes are rejected rather than repaired.

`MAX_STL_TRIANGLES` defaults to 1,000,000 and counts input facets, including
repeated or degenerate facets, before merging vertices. Exceeding it returns 413.
The upload byte limit still applies independently. After loading, the mesh must
have usable surface area, finite coordinates, bounds, dimensions, per-face areas,
total area and volume. Coordinates must fit binary STL float32 output. The
processed mesh and transformed mesh are checked again. For large coordinates
outside Trimesh's int64 merge quantization range, exact duplicate merging avoids
integer overflow without imposing another physical size limit. The normal merge
path is unchanged for ordinary models. Geometry that cannot fit float32 after
orientation/bed placement is rejected before binary export. Invalid geometry returns
400 with application-defined English messages, never parser exception strings.

Watertightness is informational, not an admission requirement. Open shells,
non-manifold edges, zero volume and some degenerate faces are allowed when a
usable finite surface remains. All-degenerate geometry is rejected. No topology
repair or manufacturability guarantee is introduced; OrcaSlicer may still reject
an admitted mesh. Zero thickness is accepted by the dimension validator so planar
surfaces can be analyzed; machine fit, eligibility and preference rules do not
change. Automatic pricing additionally requires finite positive slicing time and
filament mass. Optional length, volume and layer statistics must also be positive
when present; material cost must be finite and non-negative (it may round to zero).
Missing, zero, negative or non-finite print statistics produce a generic 422,
never an empty automatic quote. Numeric G-code fields are parsed as complete
tokens, not positive prefixes of invalid values. The same result validator runs
in the slicer service and before pricing. OrcaSlicer failures still produce a
public-safe 500 and no price. These checks establish a minimum useful slicing
result, not production toolpath certification. The legacy `/slice` retains its default printer/material/quality
behaviour; `/analyze` and `/estimate` retain existing machine selection and
technical-review/oversized statuses. Models larger than printer volumes are not
invalid geometry solely because of their size.

`input_validation.py` checks finite rotations, quantity in `1..MAX_QUANTITY`
(default 100), and material/quality membership and pair compatibility from
`config/printing.json`. Checks precede Trimesh work. Rotation values have no
arbitrary range bound; whole turns are reduced only for the matrix calculation,
while the response preserves the requested values. The Three.js XYZ order is
unchanged. The slicer service also checks material/quality compatibility before
invoking OrcaSlicer. Request type errors remain FastAPI 422 responses; domain
validation errors use 400. Processing failures use generic 500 messages.

Each API request loads one mesh; orientation copies it in memory instead of
loading the STL again. Original geometry summaries retain existing fields and
rounding. Public orientation responses omit internal output paths. No pricing,
profile, selection algorithm or dependency changes are made by MM-002. MM-003 adds the processing protections below.

## MM-003 — processing admission and slicer timeout

`processing.py` uses an in-memory non-blocking semaphore and one lease per heavy
HTTP request. Before reading the request body or entering FastAPI's multipart
parser, POST `/upload`, `/analyze`, `/slice` and `/estimate` acquire admission.
When all slots are occupied, the API returns JSON HTTP 503 with `Retry-After: 5`
without calling the request receive function. There is no application waiting
queue. Trailing-slash variants use the same admission; light endpoints and CORS
preflight requests do not acquire slots. CORS wraps admission/body-limit errors.

`MAX_CONCURRENT_PROCESSING_JOBS` defaults to 1 and must be a positive integer at
startup. The executor has the same maximum worker count; each lease submits at
most one complete synchronous pipeline, including geometry, orientation, slicing,
statistics and pricing. Async endpoint wrappers retain bounded upload reads and
cheap numeric checks. Config file reads and CPU/subprocess work run in the worker,
keeping the event loop free; `/health` and `/options` remain accessible.

The HTTP task awaits a shielded adapter to a `concurrent.futures.Future`. The
lease tracks the actual concurrent future and request completion separately with
a thread-safe lock. If the HTTP task is cancelled, its wait may stop immediately,
but an active thread is not cancelled or abandoned for accounting purposes. The
lease is released only after both the request and actual worker have finished.
The worker owns temporary directories and cleanup; no new admitted heavy request
can overlap a cancelled job that is still running. Late exceptions are observed.
Cancellation during upload reception before worker submission releases admission
when the parser/request exits. A disconnected client does not necessarily cause
an ASGI task cancellation; any already-running work still retains its lease until
completion. Cancellation is not an immediate thread termination mechanism.

`slicer_process.py` starts OrcaSlicer with `Popen(start_new_session=True)` and
applies a monotonic `SLICER_TIMEOUT_SECONDS` deadline (default 60, positive integer)
to execution, both output pipes and parent completion. A selector drains stdout
and stderr together in 64 KiB reads, retaining only the last 1 MiB per stream.
Excess diagnostics are discarded with a truncation marker; captured memory does
not grow with emitted output. Closed pipes do not disable the process deadline.
Parent exit is observed using `waitid(..., WNOWAIT)` until group termination, so
the leader PID cannot be reused before signaling its group.
On timeout it kills the process group with SIGKILL, waits for the direct process,
and reaps adopted descendants in that group before returning a typed timeout.
The Linux child-subreaper setting (`prctl(PR_SET_CHILD_SUBREAPER)`, via standard
library ctypes) lets the backend reap orphaned grandchildren, without relying on
PID 1 to do it. Subreaper registration is process-wide; other application
components must still manage their own children/orphaned descendants. Targeted `waitpid(-pgid, ...)` does not collect other jobs' children.
The same cleanup runs on exceptions and removes descendants left by an early
parent exit. Pipes are closed even on read failures. This requires the Linux
AMD64/ARM64 container runtime and direct local OrcaSlicer CLI, not a host-delegating
Flatpak wrapper. Descendants that deliberately escape the process group are not
an isolation guarantee of this mechanism; this is not a general process sandbox.

The public timeout response is HTTP 504 with an application-defined English
message, without commands, paths or raw exceptions. Existing directory context
managers run only after process termination/reaping. A cancelled request may keep
running until its thread returns or the slicer hits its deadline. A slow geometry
operation, upload or statistics parse does not acquire a new global deadline:
60 seconds bounds the slicer subprocess phase, not the whole HTTP request.

The Docker command explicitly uses one Uvicorn worker. The semaphore is not
shared across workers, replicas or independent backend processes; increasing any
of those multiplies total concurrency. Compose enables the Docker init process
as a further safeguard and preserves existing production CPU/RAM limits.

Nginx `proxy_read_timeout 90s` leaves margin above the default slicer deadline for
cleanup/other work; it is an upstream inactivity timeout, not a global request
limit. Nginx still buffers uploads, so pre-parser backend admission does not avoid
proxy-side buffering. Proxy aborts do not release an active worker early. The
frontend explicitly explains 503 and 504, including HTML proxy responses, and
does not introduce automatic retries. Repeated requests remain subject to admission.

Remaining limits: geometry is bounded by bytes/triangles but cannot be forcibly
stopped as a Python thread; G-code file parsing and proxy temporary storage are
not globally bounded by this change. OS-level uninterruptible I/O can delay process
reaping. There is no per-IP rate limit, abandoned-directory recovery, distributed
queue or hard total request deadline in MM-003.

## MM-004 — temporary file lifecycle

`temp_storage.py` shares the existing immediate directory cleanup across analysis,
uploads and slicing. Versioned random workspaces have sibling ownership/inode
metadata and exclusive Linux file locks. Startup and periodic recovery only
remove recognized, old, unlocked directories in those three application roots;
active jobs are never removed based on age. Input/output locks are inherited by
the slicer to protect files if the backend exits abruptly while its child lives.
MM-003 admission, cancellation, group termination and reaping remain unchanged.

Recovery works off the event loop, anchors traversal to no-follow directory
descriptors and uses symlink-resistant recursive deletion. Slicer working/home,
XDG and temporary directories live inside its output workspace. Framework-owned
anonymous multipart files and Nginx's nonpersistent body files retain their own
cleanup; the backend never sweeps system or proxy temporary directories.

Defaults: orphan eligibility after 3600 seconds, startup sweep and then a sweep
every 300 seconds. Eligibility is not a deletion deadline and never delays normal
completed-job cleanup. Unknown legacy folders and incomplete/mismatched ownership
metadata are preserved for explicit review. No disk quota, secure erasure,
permanent model storage or new dependency is introduced. See
[Temporary storage](TEMPORARY_STORAGE.md) for the audit, full protocol, container
restart/recreation behavior and remaining risks, and
[Deployment](DEPLOYMENT.md#temporary-storage-mm-004) for settings.
