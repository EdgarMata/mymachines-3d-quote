# MM-008B private ARM64 runbook — prepared, not executed

This procedure is a preparation artifact of MM-008A. It requires separate
approval of the target host, reviewed commit, maintenance window, resource
thresholds and test scope. It grants no SSH, deployment or public-exposure
authorization. Do not publish images, create DNS records or change other services.
The local assessment remains **READY WITH CONDITIONS** for private validation.

Commands below are Bash templates for an authorized operator. Supply verified
values in private notes; no address, container identity or installed version is
assumed. Stop at a failed prerequisite. Do not paste operational output into Git,
public issues or CI artifacts. Use synthetic geometry and commercial settings.

## 1. Identify the installation and establish stop criteria

On the approved host, first verify identity and the local Docker endpoint:

```sh
hostname
uname -m
docker context inspect --format '{{.Endpoints.docker.Host}}'
docker info --format '{{.Architecture}} {{.DockerRootDir}}'
docker compose version
```

Require native ARM64 (`aarch64`/`arm64`), a local Unix socket, and Compose support
for `!override`. Verify successful native CI for the approved source commit.
Record the old deployed commit from its deployment records, not from the new
checkout. If unavailable, record that fact and its immutable image IDs; do not
invent a source/image association. Identify the existing quote Compose project,
its complete original file list/environment and its two managed service IDs.
Confirm the previous installation is privately reachable and no public NPM route
can reach it. If privacy cannot be established without unapproved infrastructure
changes, stop and request a revised maintenance plan.

Set these variables to verified values, outside the checkout:

```sh
set -euo pipefail
: "${APPROVED_COMMIT:?Full reviewed commit SHA}"
: "${QUOTE_CHECKOUT:?Separate clean checkout of that commit}"
: "${QUOTE_PROJECT:?Existing quote Compose project name}"
: "${QUOTE_OPS:?New private evidence/rollback directory outside the checkout}"
: "${QUOTE_PRIVATE_PORT:?Verified unused host loopback port}"
# Populate with EVERY file used for the existing installation, in original order.
# OLD_FILES=(-f /operator/verified/base.yml -f /operator/verified/private-override.yml)
test "${#OLD_FILES[@]}" -gt 0
umask 077
mkdir -m 700 "$QUOTE_OPS"
docker compose -p "$QUOTE_PROJECT" "${OLD_FILES[@]}" config > "$QUOTE_OPS/previous.resolved.yml"
BACKEND_ID=$(docker compose -p "$QUOTE_PROJECT" "${OLD_FILES[@]}" ps -q backend)
FRONTEND_ID=$(docker compose -p "$QUOTE_PROJECT" "${OLD_FILES[@]}" ps -q frontend)
test -n "$BACKEND_ID" && test -n "$FRONTEND_ID"
docker inspect "$BACKEND_ID" "$FRONTEND_ID" > "$QUOTE_OPS/previous.containers.json"
docker inspect --format '{{.Name}} {{.Image}} {{json .Config.Labels}}' "$BACKEND_ID" "$FRONTEND_ID"
```

Check the labels identify exactly the expected quote project/services. Verify
there is only one backend worker/replica. Save the old checkout/revision, resolved
Compose, original overrides/environment, operator bind files and permissions
using the existing private backup procedure. Resolved Compose and inspect output
can themselves contain secrets. Confirm backups can be restored; keep old images
locally (no image/container/volume pruning). Preserve any previous application
data; this runbook does not authorize deleting legacy temporaries.

Capture idle observations before building or updating:

```sh
free -m
vmstat 1 5
df -h
df -i
docker ps --format '{{.ID}} {{.Names}} {{.Status}}'
docker stats --no-stream
docker inspect --format '{{json .State}}' "$BACKEND_ID" "$FRONTEND_ID"
docker inspect --format '{{json .HostConfig}}' "$BACKEND_ID" "$FRONTEND_ID"
```

Record available CPU/RAM, swap and IO pressure, Docker filesystem space/inodes,
application limits and Odoo/PostgreSQL/NPM baseline health. Use existing authorized
read-only Odoo and PostgreSQL monitoring: latency/errors, workers/connections,
lock waits, query latency and temporary-file activity. Do not print database
credentials or run a load against those services.

Before continuing, write approved **numeric** thresholds in private notes:
host MemAvailable minimum, swap/IO ceilings, disk/inode reserve, application
memory stop below its 4 GiB cap, temporary-storage ceiling, request duration,
overall test window, and acceptable Odoo/PostgreSQL latency/error/lock changes.
Derive them from the baseline and shared-service requirements; a container limit
does not reserve physical RAM. If headroom is insufficient, defer the test.

## 2. Preserve rollback and prepare a private candidate

Tag the two existing image IDs so recreation does not remove the rollback reference:

```sh
RUN_ID=$(date -u +%Y%m%dT%H%M%SZ)
OLD_BACKEND_IMAGE=$(docker inspect --format '{{.Image}}' "$BACKEND_ID")
OLD_FRONTEND_IMAGE=$(docker inspect --format '{{.Image}}' "$FRONTEND_ID")
docker image tag "$OLD_BACKEND_IMAGE" "mm008b-backend-rollback:$RUN_ID"
docker image tag "$OLD_FRONTEND_IMAGE" "mm008b-frontend-rollback:$RUN_ID"
cat > "$QUOTE_OPS/rollback.images.yml" <<EOF
services:
  backend:
    image: mm008b-backend-rollback:$RUN_ID
  frontend:
    image: mm008b-frontend-rollback:$RUN_ID
EOF
docker compose -p "$QUOTE_PROJECT" -f "$QUOTE_OPS/previous.resolved.yml" \
  -f "$QUOTE_OPS/rollback.images.yml" config --quiet
```

Keep the previous private-only access arrangement in the resolved configuration;
review it before relying on rollback. Bind-file paths must remain available with
their original contents. A rollback image alone does not restore configuration.

In the separate candidate checkout, confirm the exact revision and clean tree:

```sh
cd "$QUOTE_CHECKOUT"
test "$(git rev-parse HEAD)" = "$APPROVED_COMMIT"
test -z "$(git status --porcelain)"
export QUOTE_IMAGE_TAG="private-$APPROVED_COMMIT"
export QUOTE_CHECKOUT QUOTE_PRIVATE_PORT
export QUOTE_BACKEND_IMAGE="mymachines-quote-backend:$QUOTE_IMAGE_TAG"
export QUOTE_FRONTEND_IMAGE="mymachines-quote-frontend:$QUOTE_IMAGE_TAG"
export PRICING_CONFIG_HOST_PATH="$QUOTE_OPS/synthetic.private.json"
export NGINX_TRUSTED_PROXIES_FILE="$QUOTE_OPS/direct-trust.conf"
python3 - <<'PY'
import json, os
from pathlib import Path
data = json.loads(Path('config/pricing.demo.json').read_text())
data['configuration_kind'] = 'private'
Path(os.environ['PRICING_CONFIG_HOST_PATH']).write_text(json.dumps(data))
Path(os.environ['NGINX_TRUSTED_PROXIES_FILE']).write_text('# Direct SSH-only: trust no forwarded headers.\n')
PY
chmod 600 "$PRICING_CONFIG_HOST_PATH" "$NGINX_TRUSTED_PROXIES_FILE"
```

The `private` marker permits production-mode testing; these remain synthetic
demo values and must never be presented as approved commercial prices.

Create an operator-only override, outside Git:

```sh
cat > "$QUOTE_OPS/candidate.yml" <<'YAML'
services:
  backend:
    image: ${QUOTE_BACKEND_IMAGE:?}
    build:
      context: ${QUOTE_CHECKOUT:?}
      dockerfile: backend/Dockerfile
  frontend:
    image: ${QUOTE_FRONTEND_IMAGE:?}
    build:
      context: ${QUOTE_CHECKOUT:?}
      dockerfile: frontend/Dockerfile
    environment:
      REQUIRE_TRUSTED_PROXY: "false"
    networks: !override
      - quote_internal
    ports: !override
      - "127.0.0.1:${QUOTE_PRIVATE_PORT:?}:80"
YAML
quote() {
  docker compose -p "$QUOTE_PROJECT" -f "$QUOTE_CHECKOUT/docker-compose.prod.yml" \
    -f "$QUOTE_OPS/candidate.yml" "$@"
}
quote config > "$QUOTE_OPS/candidate.resolved.yml"
quote config --quiet
```

Inspect the merged file: frontend only on the application network and host
loopback, backend with no published port, no connection to the shared NPM network,
2 CPU/4 GiB backend, 0.5 CPU/256 MiB frontend, `init`, one worker/job, 60-second
slicer and 90-second Nginx read inactivity settings. The unused external network
declaration may remain; verify it already exists rather than creating/changing
shared infrastructure. Do not use development Compose on this host.

This is **direct SSH mode**, trusting no forwarding headers. It does not validate
the real NPM peer/header/buffering configuration. A later separately approved NPM
exercise must use the exact-peer discovery/refresh procedure in
[Abuse protection](ABUSE_PROTECTION.md#updating-trust-when-npm-is-recreated), revoke
stale addresses and test real header replacement and IPv4/IPv6. Never trust the
whole shared Docker subnet or enable a public route for this validation.

## 3. Build and update only the quote application

Keep monitoring shared services while building; runtime CPU/RAM caps do not bound
BuildKit, npm or image extraction. Stop the build if agreed headroom is lost.
Use a separate approved build host/import plan if an on-host native build is unsafe.

```sh
quote build backend frontend > "$QUOTE_OPS/build.log" 2>&1
docker image inspect --format '{{.Id}} {{.Architecture}}' \
  "$QUOTE_BACKEND_IMAGE" "$QUOTE_FRONTEND_IMAGE" > "$QUOTE_OPS/candidate.images.txt"
```

Require both images to report ARM64; preserve the pinned Orca checksums/build
output and reviewed source association. Do not run the publication workflow.
Drain quote traffic and verify no active job remains. Stop only the verified
old quote frontend and backend; never run `down`, `prune` or `--remove-orphans`.

```sh
docker stop "$FRONTEND_ID" "$BACKEND_ID"
quote up -d --no-build --pull never --no-deps backend
quote ps
quote exec -T backend curl --max-time 5 -fsS http://localhost:8000/health
quote exec -T backend curl --max-time 5 -fsS http://localhost:8000/options
```

Poll health with an explicit bounded operator deadline; require healthy before
starting frontend. A failure triggers investigation/rollback, not endless retries.

```sh
quote up -d --no-build --pull never --no-deps frontend
quote exec -T frontend nginx -t
quote ps
docker port "$(quote ps -q frontend)"
curl --max-time 5 -fsS "http://127.0.0.1:$QUOTE_PRIVATE_PORT/api/health"
curl --max-time 5 -fsS "http://127.0.0.1:$QUOTE_PRIVATE_PORT/api/options"
```

Verify actual container image IDs, resource limits, mounts, restart/health state
and network membership against the reviewed configuration. Verify backend has no
host port and frontend is only on loopback. Configuration failure must not fall
back to demonstration mode. Capture bounded private logs (`quote logs --tail 200`).

## 4. Tunnel, browser and progressive private validation

From the authorized workstation, using the approved existing SSH identity:

```sh
: "${APPROVED_SSH_TARGET:?Existing authorized SSH host alias}"
: "${LOCAL_TUNNEL_PORT:?Unused local port}"
: "${QUOTE_PRIVATE_PORT:?Same verified remote loopback port}"
ssh -N -o ExitOnForwardFailure=yes \
  -L "127.0.0.1:$LOCAL_TUNNEL_PORT:127.0.0.1:$QUOTE_PRIVATE_PORT" "$APPROVED_SSH_TARGET"
```

In a second local terminal, check `/api/health` and `/api/options` with bounded
curl, then open `http://127.0.0.1:<LOCAL_TUNNEL_PORT>` in the browser. Upload one
small synthetic binary STL and one ASCII STL; inspect WebGL, rotation/lay-on-face,
dimensions, machine/status, quantity, price, physical print time and weight.
Verify invalid input, oversized/incompatible geometry, error display and manual
retry. Keep existing quotation and machine-selection expectations. Do not confuse
physical print time with API execution time. Tunnel users share a peer budget.

Run only the agreed bounded sequence, recording start/end, statuses and resource
observations for every case:

1. Small/medium/curved models, one client; verify clean recovery between requests.
2. Two then five clients; same-IP rejection must be controlled 429/503 while
   health remains responsive and a later manual quote succeeds. Progress to
   10/20 only if agreed shared-service headroom remains. Distinct-origin/NPM
   identity testing requires a separately approved private arrangement; tunnel
   peers and spoofed headers are not independent clients.
3. Bounded soak for the approved duration; compare warmed/late RSS, FD/thread
   counts, application/multipart/Nginx storage and request latency. Stop at the
   agreed deadline; a short soak cannot certify absence of leaks.
4. Repeat the **two unfinished Fedora cases separately**, with continuous external
   observation: progressive binary-grid `/analyze` through 500,000 then 1,000,000
   facets, and a 245 × 205 × 205 mm box `/estimate` after smaller solid boxes.
   Keep the intended 4 GiB/2 CPU cap and one job. Start neither while the other
   runs. If a guard interrupts either, mark it incomplete and retain logs; do not
   declare it qualified or retry with raised limits automatically.

For step 4, the following workstation example generates only synthetic files in
a new private directory. Set `CASE_MAX_SECONDS` to the approved per-case watchdog
and monitor the host/services from the separate operator session before sending.
Run one request at a time, evaluating the stop criteria before each command:

```sh
: "${CASE_MAX_SECONDS:?Approved finite per-case request watchdog}"
: "${CASE_DIR:?New private workstation directory outside the checkout}"
mkdir -m 700 "$CASE_DIR"
export CASE_DIR
python3 - <<'PY'
import os, struct
from pathlib import Path
root = Path(os.environ['CASE_DIR'])
for count in (500_000, 1_000_000):
    with (root / f'grid-{count}.stl').open('xb') as stream:
        stream.write(b'Synthetic MM008B grid'.ljust(80, b' ') + struct.pack('<I', count))
        for number in range(count):
            cell, half = divmod(number, 2)
            x, y = cell % 1000, cell // 1000
            vertices = ((x, y, 0, x+1, y, 0, x, y+1, 0) if not half else
                        (x+1, y, 0, x+1, y+1, 0, x, y+1, 0))
            stream.write(struct.pack('<12fH', 0, 0, 1, *vertices, 0))
# A closed binary box, independent of workstation Trimesh installation.
vertices = [(x, y, z) for z in (0, 205) for y in (0, 205) for x in (0, 245)]
faces = [(0,2,1),(1,2,3),(4,5,6),(5,7,6),(0,1,4),(1,5,4),
         (2,6,3),(3,6,7),(0,4,2),(2,4,6),(1,3,5),(3,7,5)]
with (root / 'near-volume.stl').open('xb') as stream:
    stream.write(b'Synthetic MM008B box'.ljust(80, b' ') + struct.pack('<I', len(faces)))
    for face in faces:
        coordinates = [value for index in face for value in vertices[index]]
        stream.write(struct.pack('<12fH', 0, 0, 0, *coordinates, 0))
PY
curl --max-time "$CASE_MAX_SECONDS" --silent --show-error --fail-with-body \
  -F "file=@$CASE_DIR/grid-500000.stl" \
  "http://127.0.0.1:$LOCAL_TUNNEL_PORT/api/analyze" -o "$CASE_DIR/grid-500000.response.json"
# Continue ONLY after checking metrics, recovery and agreed headroom.
curl --max-time "$CASE_MAX_SECONDS" --silent --show-error --fail-with-body \
  -F "file=@$CASE_DIR/grid-1000000.stl" \
  "http://127.0.0.1:$LOCAL_TUNNEL_PORT/api/analyze" -o "$CASE_DIR/grid-1000000.response.json"
# Run the dense solid separately, after successful smaller-solid checks/recovery.
curl --max-time "$CASE_MAX_SECONDS" --silent --show-error --fail-with-body \
  -F "file=@$CASE_DIR/near-volume.stl" \
  "http://127.0.0.1:$LOCAL_TUNNEL_PORT/api/estimate" -o "$CASE_DIR/near-volume.response.json"
```

Require the documented public status/fields, not just curl exit 0. A curl deadline
disconnects the client; it does not terminate a geometry thread. Apply the recovery
plan below if safe headroom is lost. Remove only these known generated fixtures
after investigation; retain responses/metrics privately, never in the checkout.

The local `run_load_resilience.py` creates its own disposable instance; it does
**not** target the installed service. Running it on an approved ARM64 host is a
separate isolated-instance test, not evidence for the tunnel/NPM installation.
For controlled case reproduction there, build its expected local image aliases
from the approved IDs, then use its documented bounded phases (`complexity`,
`baseline`) with explicitly approved safety budgets. Baseline includes the dense
box unless explicitly skipped. Generated grids/boxes come from
`load_resilience_client.py`; do not supply customer files. Do not invoke `faults`
or crash/ENOSPC injection against this shared installation.

## 5. Observe, interrupt and recover

Use a second operator session and existing monitoring throughout builds and
requests. Record `docker stats --no-stream`, host `free`, `vmstat`, disk/inodes,
application temporary growth and NPM health, plus Odoo/PostgreSQL indicators.
On the backend, read cgroup-v2 `memory.current`, `memory.max`, `memory.peak`,
`memory.events` and `cpu.stat`; record counter deltas and Docker `OOMKilled` state.
For example, in the authorized host's quote shell:

```sh
quote exec -T backend cat /sys/fs/cgroup/memory.current /sys/fs/cgroup/memory.max \
  /sys/fs/cgroup/memory.peak /sys/fs/cgroup/memory.events /sys/fs/cgroup/cpu.stat
docker inspect --format '{{json .State}}' "$(quote ps -q backend)"
quote exec -T backend sh -c 'du -sk /project/tmp /tmp; df -Pk /project/tmp /tmp'
quote exec -T frontend sh -c 'du -sk /var/cache/nginx; df -Pk /var/cache/nginx'
```

Sample at the privately agreed interval; an observation gap is a stop condition.
Named-file `du` omits unlinked multipart data: use filesystem/cgroup observations
too. NPM may buffer earlier even though direct SSH mode bypasses it; do not claim
its edge/storage behavior passed. No hard storage quota or geometry deadline is
introduced by this runbook.

Stop generating requests immediately on any agreed threshold crossing, OOM event,
unexpected/repeated 5xx (including 502), unavailable health, failure to recover
admission, increasing storage without cleanup, monitoring loss or deterioration
of Odoo/PostgreSQL. Client disconnect does not stop a geometry thread. If recovery
cannot safely be awaited, stop **only** the verified quote containers under the
approved plan; do not repeatedly retry, raise caps or restart shared services.
Preserve bounded logs, image IDs and counters before recreation where safe.

After traffic stops, verify health and one real small quote, no remaining slicers
or zombies, admission recovery, temporary cleanup and shared-service return to
baseline. Restart retains the container's temporary layer; recreation does not
inherit it. Recognized unlocked orphans are only eligible after 3600 seconds and
swept every 300 seconds. Do not delete unknown directories or promise secure erasure.

If rollback is required, stop the candidate quote services, restore the original
operator bind-file contents/permissions where changed, and use the preserved
configuration/images, keeping private access closed until checks pass:

```sh
quote stop frontend backend
rollback() {
  docker compose -p "$QUOTE_PROJECT" -f "$QUOTE_OPS/previous.resolved.yml" \
    -f "$QUOTE_OPS/rollback.images.yml" "$@"
}
rollback config --quiet
rollback up -d --no-build --pull never --no-deps backend
# Require bounded healthy/functional checks before restoring frontend.
rollback up -d --no-build --pull never --no-deps frontend
rollback ps
```

Recheck old image IDs, private listener/network policy, quote behavior and all
shared services. A failed rollback requires operator escalation, never broad
Docker cleanup. Retain old images/records until the maintainer accepts the result.

Record pass/fail/incomplete per case and the deployed revision privately.
MM-008A's isolated unexplained 502, unfinished high-cost qualification, upload
inactivity limitations, no hard storage quota/global geometry deadline and pending
real-browser/NPM/shared-host approval remain open until separately evidenced.
Private validation does not authorize public exposure, real prices or new DNS.
