# Deployment guide

## Development and tests
From the repository root on the development machine:

```sh
docker compose config --quiet
docker compose build
docker compose run --rm --no-deps backend /opt/orca/AppRun --help
docker compose run --rm --no-deps -v "${PWD}/backend/tests:/app/tests:ro" backend python -m unittest discover -s /app/tests -v
```

Use `docker compose up -d` for local development only, and inspect `docker compose ps`. The development Compose publishes ports; do not use it unchanged on a shared production host.

## CI and publication
`.github/workflows/ci.yml` builds and tests natively on AMD64 and ARM64 and produces four SBOM artifacts. `.github/workflows/publish.yml` is manual and gates on successful CI for the selected main-branch commit. It is not yet approved for public distribution. Inspect workflow definitions before use.

## Production principles
`docker-compose.prod.yml` references GHCR images with required `QUOTE_IMAGE_TAG`. Prefer immutable commit tags to `latest`. Backend has no host port; frontend connects to the reverse proxy network. Initial runtime limits: backend 2 CPU / 4 GiB; frontend 0.5 CPU / 256 MiB; healthcheck `/options`, bounded JSON log rotation. Runtime resource limits do not constrain all build activity. Validate available resources before building on shared infrastructure.

## Private testing without publishing images
Use a local, untracked Compose override on the deployment host to replace both image names with local `:internal` tags, add root build contexts and respective Dockerfiles, remove the frontend connection to the shared reverse proxy network using `networks: !override`, and bind frontend port 80 only to host loopback. Validate the merged configuration before building/starting. Access via SSH local forwarding. The internal application bridge is not necessarily an egress-denying network.

Example commands (only on the authorized test host, after checking context and approval):

```sh
export QUOTE_IMAGE_TAG=internal
docker compose -f docker-compose.prod.yml -f docker-compose.internal.yml config
docker compose -f docker-compose.prod.yml -f docker-compose.internal.yml build backend
docker compose -f docker-compose.prod.yml -f docker-compose.internal.yml build frontend
docker compose -f docker-compose.prod.yml -f docker-compose.internal.yml up -d --no-build --pull never
docker compose -f docker-compose.prod.yml -f docker-compose.internal.yml ps
```

Check frontend HTTP status, `/api/options`, `/analyze`, `/estimate`, logs, resource usage and a representative quote. Never run deployment commands without checking the target host. Host-specific access details belong in private operational notes, not this repository.

## Public deployment gate
Not approved as of 2026-11-25. Complete security, pricing/privacy, redistribution and operational checks. Preserve unrelated services. Record deployed commit and image digests, keep previous known-good version available, and test rollback. Do not change DNS or reverse proxy routing before explicit approval.

## Upload limits

All four upload endpoints use `backend/app/services/upload_validation.py`.
The backend environment variable `MAX_UPLOAD_SIZE_MB` defaults to `50` (MiB)
and must be a positive integer; invalid values prevent application startup.
Set it in the backend container environment when overriding the default.
The maximum complete request body is that file limit plus 1 MiB for multipart
overhead, enforced while receiving the body before multipart parsing completes.
Files exceeding the file limit and requests exceeding the body limit return 413.

`frontend/nginx.conf` sets `client_max_body_size 51m` and explicitly enables
request buffering. If changing the backend file limit, align this Nginx value
with the file limit plus 1 MiB, then rebuild the frontend image. Align any outer
reverse proxy limits before an approved deployment. Nginx rejections may use an
HTML response; clients must handle HTTP 413 without assuming JSON. These limits
apply per request; concurrency and temporary-storage policy are separate pending
gates. Local source-mounted verification does not update built or deployed images.

## Geometry and printing parameter limits (MM-002)

Backend-only environment settings, read at application startup:

| Variable | Default | Meaning |
|---|---:|---|
| `MAX_STL_TRIANGLES` | `1000000` | Maximum input facets per STL, inclusive; binary and ASCII |
| `MAX_QUANTITY` | `100` | Maximum units per automatic estimate; minimum is always 1 |

Both must be positive integers. Invalid settings fail startup instead of removing
the protection. Set them explicitly in the backend container environment when
overriding defaults, then recreate the backend only during an approved deployment.
No Nginx change is needed for these two settings. The independent MM-001 defaults
remain 50 MiB per STL and 51 MiB per HTTP request. Increasing the byte or triangle
limits increases resource exposure; these byte/geometry limits alone provide no concurrency or execution-time guarantee
(the MM-003 controls are described below). ASCII structural preflight permits lines up to 4096 bytes.

Local verification with the existing Docker image and current source/test mounts:

```sh
docker compose run --rm --no-deps \
  -v "${PWD}/backend/app:/app/app:ro" \
  -v "${PWD}/backend/tests:/app/tests:ro" \
  backend python -m unittest discover -s /app/tests -v
```

This runs real OrcaSlicer integration tests as well as synthetic validation tests;
it does not update a running service or image. ARM64 validation remains the native
CI/deployment check, not an assertion derived from a local AMD64 test run.

## Processing admission and slicer timeout (MM-003)

| Backend environment variable | Default | Constraint |
|---|---:|---|
| `MAX_CONCURRENT_PROCESSING_JOBS` | `1` | Positive integer; shared by all four heavy endpoints in one backend process |
| `SLICER_TIMEOUT_SECONDS` | `60` | Positive integer; OrcaSlicer process/pipe communication deadline |

Invalid values prevent application import/startup. Set overrides in the backend
container environment. Keep one Uvicorn worker (explicit in the Dockerfile), and
one replica: this in-memory gate is not a host-wide or distributed limit. Do not
raise concurrency on shared infrastructure without a separate resource assessment.
The existing production 2 CPU / 4 GiB backend limits are unchanged. Both Compose
files enable `init: true`; the service itself also adopts/reaps slicer descendants
using Linux child-subreaper support. No extra Python package is required.

`frontend/nginx.conf` sets `proxy_read_timeout 90s`, compared with the default
60-second slicer timeout. Increase the proxy allowance coherently if changing the
slicer deadline; allow for upload, geometry and cleanup as well. Rebuild/recreate
only during an approved deployment. An outer reverse proxy must also allow this
interval. The 90-second value is an inactivity timeout, not a total HTTP deadline.
A request that spends a long time in geometry may outlive a proxy connection;
admission stays occupied until the real worker finishes. No global request timer
or upload admission deadline is provided in this step.

Busy requests receive 503 and `Retry-After: 5`; retry manually after a short wait.
Slicer timeouts receive 504 after process cleanup. There is no application queue
and no automatic retry loop. Backend admission happens before multipart parsing;
Nginx request buffering still consumes proxy resources before forwarding uploads.
Slow accepted uploads can occupy admission before geometry starts. Existing byte
limits remain necessary, and rate limits/storage policy are separate gates.

MM-003 verification (source-mounted Docker backend command above) includes actual
short synthetic subprocess trees and real OrcaSlicer binary/ASCII quotes. UI error
handling can be checked locally with Node 24, without installing test dependencies:

```sh
cd frontend
npm test
npm run build
npm run lint
```

The current full lint reports an existing `react-hooks/set-state-in-effect` error
in the model-analysis effect of `App.tsx`; the same error is reproducible from
`HEAD` before MM-003. New error-handler/test files pass targeted lint. Build also
retains Vite's existing large-bundle warning. These are not changes to the pricing
or viewer flows in MM-003. Native ARM64 CI remains necessary after review; local
AMD64 tests do not establish ARM64 execution results.

Final MM-003 review bounds stdout and stderr capture to the last 1 MiB per stream,
using simultaneous nonblocking reads to avoid full-pipe deadlocks. The limit is
an internal diagnostic-retention budget, not a new model or request limit. G-code
file parsing/storage remains a separate follow-up. An exited session leader is
not reaped before group signaling, avoiding a reused-PGID signal race.

GitHub Actions now executes `npm test` in `node:24-alpine` with the frontend
source mounted read-only on each existing AMD64/ARM64 matrix runner. This uses
the same Node major as the frontend build and requires no test dependencies or
extra services. Builds and real OrcaSlicer backend checks remain in the matrix.
`calculateQuote` clears its busy state in `finally`; after a 503/504 the selected
file remains and the Calculate quote button is available for a manual retry.

Pending operational validation: slow-upload/abuse protection, a hard overall
geometry deadline, process-local admission (one Uvicorn worker/replica), and
representative load tests on the shared ARM64 deployment host. No VPS load test
or deployment is implied by local test results.
