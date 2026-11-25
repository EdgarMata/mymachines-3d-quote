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
