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

MM-006 also requires `NGINX_TRUSTED_PROXIES_FILE`: an absolute path to an
operator-owned exact-IP allowlist outside this public repository. Production
mounts it read-only and sets `REQUIRE_TRUSTED_PROXY=true`; empty trust prevents
startup. Do not guess the NPM peer or trust a shared Docker subnet. See
[Abuse protection](ABUSE_PROTECTION.md) before using production Compose.

MM-007 also requires `PRICING_CONFIG_HOST_PATH`, the operator's complete private
commercial JSON outside the checkout. Backend mounts it read-only at
`/run/mymachines/pricing.json`, selects it with PRICING_CONFIG_PATH and refuses
missing/invalid/bundled-demo configuration. Existing CPU/RAM limits and one worker
remain unchanged. See [Commercial configuration](COMMERCIAL_CONFIGURATION.md).

## Private testing without publishing images
For the prepared MM-008B sequence, use the
[private ARM64 runbook](MM008B_PRIVATE_VALIDATION.md): installed-version capture,
shared-service baseline, preserved images/configuration, native local builds,
quote-only recreation, SSH/browser checks, monitored high-cost cases and rollback.
It is documentation only; deployment and its test window require separate approval.

Use a local, untracked Compose override on the deployment host to replace both image names with local `:internal` tags, add root build contexts and respective Dockerfiles, remove the frontend connection to the shared reverse proxy network using `networks: !override`, and bind frontend port 80 only to host loopback. Validate the merged configuration before building/starting. Access via SSH local forwarding. The internal application bridge is not necessarily an egress-denying network.

For this **direct SSH-only** mode, the override must explicitly set the frontend
environment `REQUIRE_TRUSTED_PROXY: "false"`. Use the empty/comment-only default
trust file as below. This deliberately trusts no forwarded headers; users may
share the tunnel's visible peer budget. Never reuse this exception unchanged
behind public NPM.

Example commands (only on the authorized test host, after checking context and approval):

```sh
export QUOTE_IMAGE_TAG=internal
export NGINX_TRUSTED_PROXIES_FILE="${PWD}/frontend/nginx-trusted-proxies.conf"
export PRICING_CONFIG_HOST_PATH=/srv/my-instant-quote-private/pricing.json
docker compose -f docker-compose.prod.yml -f docker-compose.internal.yml config
docker compose -f docker-compose.prod.yml -f docker-compose.internal.yml build backend
docker compose -f docker-compose.prod.yml -f docker-compose.internal.yml build frontend
docker compose -f docker-compose.prod.yml -f docker-compose.internal.yml up -d --no-build --pull never
docker compose -f docker-compose.prod.yml -f docker-compose.internal.yml ps
```

Check frontend HTTP status, `/api/options`, `/analyze`, `/estimate`, logs, resource usage and a representative quote. Never run deployment commands without checking the target host. Host-specific access details belong in private operational notes, not this repository.

The pricing path above is an illustrative operator location, not an existing VPS
file. Internal production-mode testing still requires an explicitly selected
external fixture marked private; use synthetic data for tests and follow the
commercial-configuration creation/validation procedure before startup.

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
apply per request; concurrency and temporary-storage controls are described below.
Local source-mounted verification does not update built or deployed images.

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
limits remain necessary; MM-006 adds ingress limits and MM-004 adds the
temporary-storage policy below. Neither provides a total upload deadline.

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

Pending operational validation: real-edge slow-upload/abuse behavior, a hard overall
geometry deadline, process-local admission (one Uvicorn worker/replica), and
representative load tests on the shared ARM64 deployment host. No VPS load test
or deployment is implied by local test results.

## Temporary storage (MM-004)

| Backend environment variable | Default | Constraint |
|---|---:|---|
| `TEMP_FILE_RETENTION_SECONDS` | `3600` | Positive integer; minimum age for recognized unlocked orphan workspaces |
| `TEMP_CLEANUP_INTERVAL_SECONDS` | `300` | Positive integer; periodic sweep interval in seconds |

Invalid settings prevent startup. Set overrides explicitly in the backend
container environment and recreate only during an approved deployment. Source
mounts used by local tests do not update built/running images. Roots under
`MYMACHINES_PROJECT_ROOT/tmp/{uploads,analysis,slicer}` must be application-owned,
accessible and free of symlink redirections; the configured project root must
exist. No Compose volume, tmpfs, CPU/memory or init-setting changes are needed.

Normal requests remove files immediately after actual worker/process completion.
The one-hour setting only controls eligibility of recognized abandoned directories;
startup or the next periodic sweep removes them when unlocked and accessible.
Permissions, stopped containers and active children can delay removal. Changing
this setting does not change upload bytes, concurrency, slicer/proxy timeouts or
provide a hard disk quota. Monitor writable-layer and proxy buffering consumption
during an approved deployment/load test.

The same container keeps named files across restart, allowing recovery. A newly
recreated container does not inherit its predecessor's writable-layer files; old
stopped containers/layers need the operator's normal lifecycle management. No
persistent temporary volume exists in either current Compose file. Do not add
one or run generic `/tmp` deletion as part of deploying this change.

Slicer `cwd`, home/XDG and temporary environment paths are isolated inside its
workspace; immutable profiles stay outside. `client_body_in_file_only off` is
explicit in Nginx, preserving automatic disposal of upload body files. Backend
recovery does not touch `/var/cache/nginx` or OS multipart temporary files.

Before an approved rollout, review any legacy/unrecognized application temporary
directories separately; the janitor deliberately cannot establish ownership of
those files. See [Temporary storage](TEMPORARY_STORAGE.md) for the inventory,
lock/recovery protocol, privacy policy, small metadata-creation crash window and
limitations. Deleting files does not imply physical secure erasure. Native ARM64
CI and representative deployment resource tests remain separate verification.

## Public API settings (MM-005)

Production Compose now explicitly sets `APP_ENV: production`. This removes
`/upload` and `/slice` registration and disables `/docs`, `/docs/oauth2-redirect`,
`/redoc` and `/openapi.json` by default. The browser's `/options`, `/analyze` and
`/estimate` paths, Nginx prefix handling and health checks are preserved. Both
legacy routes remain enabled and tested in the default development environment.

| Backend variable | Default / override |
|---|---|
| `APP_ENV` | `development` if unset; production Compose explicitly uses `production` |
| `ENABLE_LEGACY_ENDPOINTS` | `true` in development, `false` in production |
| `ENABLE_API_DOCS` | `true` in development, `false` in production |
| `CORS_ALLOWED_ORIGINS` | Local Vite origins in development; empty in production |

Boolean overrides accept only true/false, ignoring case. CORS overrides are a
comma-separated list of explicit HTTP(S) origins (no wildcard, credentials,
query, fragment or non-root path). CORS credentials are disabled; cross-origin
GET/POST and Content-Type/Accept suffice for the existing browser. Same-origin
requests through Nginx work with the empty production list. Invalid settings
prevent startup without echoing configured values. Apply overrides only in the
backend container environment during an approved deployment.

Explicitly re-enabling docs or legacy routes is possible for controlled testing;
their business responses still exclude internal information. Hiding a schema and
blocking browser cross-origin reads do not protect enabled endpoints from direct
clients. No authentication or MM-006 rate limiting is added here. Source changes
and local tests do not update existing images or deployments.

See [API contracts](API_CONTRACTS.md) for the full response/error allowlists and
diagnostic policy. New API diagnostics omit payloads and raw exception text;
standard server/proxy access logs can still include client IPs/query strings.
Review access-log retention/redaction separately before public deployment. Private
commercial configuration is handled separately by
[MM-007](COMMERCIAL_CONFIGURATION.md), not by public API settings.

MM-005 final review includes nine opt-in local HTTP checks through Nginx using
`backend/tests/nginx_smoke.py`, covering real slicing and production route defaults.
See API_CONTRACTS.md for setup constraints and manual browser steps. Browser-control
access was unavailable in that review, so visual/interactive functional approval
remains pending. Public prices can still support inference of internal costs or
margins; response allowlists prevent direct disclosure, not that inference.

## Ingress limits and proxy identity (MM-006)

Production is client → NPM → frontend Nginx → FastAPI. Configure NPM to
**overwrite** X-Real-IP with its verified client address; mount only its actual
immediate individual egress IPv4/IPv6 peers as `set_real_ip_from` directives.
Frontend ignores untrusted header claims and replaces forwarded IP headers with
the canonical address. The startup guard accepts individual literal IPs only
(optional /32 or /128), not hostnames or broad networks. Trust also depends on
the operator verifying that those peers and NPM header behavior are correct.
Do not commit actual infrastructure addresses or access details.

Lists/duplicate/ambiguous X-Real-IP values receive controlled 400; absent or
invalid numeric literals keep the TCP peer budget. A changed NPM address is not
automatically trusted: it collapses users into that peer budget, while a stale
old allowlist entry could authorize a different container after address reuse.
During approved recreation, keep public traffic closed and stop/isolate frontend,
discover and verify the new exact peers, replace the private file removing old
entries, then recreate frontend to rerun validation and load the list. Recreating
is necessary after atomic single-file replacement because a live bind mount can
retain the old inode. Verify two real clients and spoof handling before reopening
traffic. See the full [trust-update procedure](ABUSE_PROTECTION.md#updating-trust-when-npm-is-recreated).

`frontend/nginx.conf` is the policy source: analyze 30/min/IP burst 5, estimate
10/min/IP burst 2 (shared with legacy upload/slice), options 60/min/IP burst 10,
other API 60/min/IP burst 5; global 10/s burst 20. All use nodelay. Active complete
API requests are capped at 5/IP and 20 globally; bodyless GET/HEAD health probes
are exempt. Controlled JSON 429 is distinct from backend 503/Retry-After 5 and
504. UI retries are manual. Change policy via a reviewed config rebuild/reload,
not a backend environment variable.

Body inactivity timeout is 30s, header timeout 10s and idle keepalive 15s. Keep
51 MiB request buffering and automatic temporary-file removal enabled; 50 MiB
STL, backend admission and slicer/proxy deadlines remain unchanged. NPM must align
its body allowance, receive controls and upstream read timeout. If NPM buffers
first, its own edge limits must protect resources before frontend sees uploads.
Continuously slow uploads can remain active indefinitely; these settings are not
an absolute deadline, disk quota, authentication or complete DDoS protection.

After local image build, run the opt-in isolated native tests:

```sh
backend/tests/run_nginx_abuse_tests.sh
```

CI runs these on both existing native architecture runners. They include actual
production-rate recovery plus separate shortened test-only timeouts/rates. Native
startup cases cover valid/invalid IPv6, broad trust and directive injection.
The runner names and removes both containers on ordinary failure/signals, preserves
test exit codes and pulls no images; no new CI secrets/privileged modes are needed.
The nine real-Orca Nginx smoke checks remain opt-in and use bounded test-only pacing
on 429. Two external clients, IPv4/IPv6, spoof attempts, NAT, real NPM buffering
and representative resource use still require later approved deployment tests.
See [Abuse protection](ABUSE_PROTECTION.md) for the full audit and rollout checklist.
