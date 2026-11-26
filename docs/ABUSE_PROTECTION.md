# Rate limiting and proxy trust (MM-006)

## Audit and boundary

Production topology: client → Nginx Proxy Manager (NPM) → frontend Nginx →
FastAPI. Internal operator testing can reach frontend Nginx directly through SSH
forwarding. No remote NPM/VPS configuration was inspected or changed in MM-006.

Previously frontend Nginx had no request/connection limits or explicit receive
timeouts. `$remote_addr` was the immediate TCP peer; behind NPM that would be NPM,
not the original client. It appended untrusted incoming X-Forwarded-For when
proxying upstream. Backend admission bounded expensive work but could not protect
frontend buffering before forwarding. CORS is not an abuse/authentication boundary.

The frontend now owns the IP-based limits. Native shared-memory zones work across
its Nginx workers; they are not shared across replicas. No Python rate limiter,
Redis, queue service, new dependency or authentication is introduced. MM-001 byte
limits, MM-002 validation, MM-003 admission/slicer deadline, MM-004 cleanup and
MM-005 response contracts remain independent protections.

## Canonical client address and deployment gate

The default image trusts **no** forwarding peer. Direct development/internal
traffic is keyed by its TCP peer, ignoring supplied X-Real-IP/X-Forwarded-For.
SSH forwarding/NAT may intentionally collapse internal users to one visible peer;
do not fix that by accepting arbitrary client headers.

Frontend Nginx uses `real_ip_header X-Real-IP` and `real_ip_recursive off`. Only
exact peer addresses listed in `/etc/nginx/trusted-proxies.conf` can replace the
client address. The immediate trusted NPM must overwrite X-Real-IP with its own
verified `$remote_addr`, never a client-supplied header. X-Forwarded-For is not a
source of trust. Frontend forwards only the canonical `$remote_addr` in both
X-Real-IP and X-Forwarded-For and removes the RFC Forwarded header. IPv4 and IPv6
are supported. Other request headers are not identity credentials. Host and
X-Forwarded-Proto retain their existing frontend behavior; this is not a TLS or
authentication redesign.

Lists, duplicate X-Real-IP fields (including an empty duplicate), embedded
whitespace and non-address characters are rejected with a controlled 400 before
body processing. This guard also applies to direct traffic; it never grants
trust. Missing/empty headers and numerically malformed literals retain the TCP
peer identity, so they share its budget rather than creating arbitrary keys.
Nginx parses valid addresses into binary keys: equivalent IPv6 spellings and
accepted IPv4 address/port representations share one address budget. NPM should
still emit one bare canonical address, without a port. X-Forwarded-For, including
duplicates, is never an identity source.

Production Compose requires `NGINX_TRUSTED_PROXIES_FILE`, an absolute path to an
operator-owned file outside the public repository. It mounts that file read-only
with host-path auto-creation disabled and sets `REQUIRE_TRUSTED_PROXY=true`.
The entrypoint validator rejects empty production trust, hostnames, shared Docker
subnets, broad CIDRs, UNIX sockets and unrelated directives. Only individual
literal addresses, optionally /32 for IPv4 or /128 for IPv6, are accepted; Nginx
also validates address syntax. Do not commit real infrastructure addresses.

Determine the actual immediate NPM egress address(es) during the later approved
installation; no address or subnet is guessed here. Each line must be:

```text
set_real_ip_from <verified-literal-immediate-proxy-IP>;
```

The startup gate prevents accidentally deploying behind NPM with the empty direct
mode defaults and silently sharing its IP budget. It cannot establish that an
operator listed the correct peer or that NPM overwrites headers correctly. Public
rollout must verify those facts with two distinct external client addresses and
spoofed-header attempts. Use a stable individual peer identity; reevaluate the
allowlist after proxy recreation/address changes. Never trust an entire shared
Docker bridge. If CDN/proxies precede NPM, NPM must itself resolve identity using
an explicitly trusted upstream policy; frontend does not trust arbitrary chains.

### Updating trust when NPM is recreated

An address change is **not discovered automatically**. Until reconfigured, the
new unlisted peer cannot supply client identity; requests collapse into its TCP
peer budget. The old address remains authorized and could be reassigned to a
different container. A successful startup only checks syntax/nonempty trust,
not whether the listed addresses still belong to NPM.

Use this procedure during a separately approved deployment:

1. Keep public application traffic closed and stop/isolate frontend before
   recreating NPM or allowing its old address to be reused. Do not leave stale
   trust live during the transition.
2. Inspect the newly running NPM's attachment and actual frontend-facing egress
   on the relevant network. Verify its effective X-Real-IP overwrite and any
   upstream real-IP policy. Record only current individual IPv4/IPv6 peers under
   operator control; never authorize the whole bridge or infer trust from a name.
3. Replace the private operator file with the verified complete list, removing
   obsolete addresses. Validate the deployment Compose variables and startup
   guard/configuration before opening traffic. Keep this file outside Git/build
   context; do not place actual addresses in public docs.
4. **Recreate frontend** to run the guard and load the new list. A read-only
   single-file bind mount may retain the old inode after an atomic host-file
   replacement; nginx reload alone is insufficient in that case. The recreated
   container reads the current file and old workers/trust are gone. Do not reload
   arbitrary edited rules without rerunning validation.
5. Confirm actual canonical addresses privately in frontend access logs, separate
   budgets for two external clients, spoof rejection/overwrite and a real quote.
   Check health/options and resource usage, then reopen application traffic.
   If validation fails, keep traffic closed and repair the exact list; never
   fall back to a broad CIDR or trust arbitrary headers.

A stable address managed by the operator can reduce churn but does not replace
these checks. Recreating frontend also resets its in-memory rate state. No such
deployment, address discovery or remote file update is performed by this change.

Backend receives normalized headers, but IP limiting does not depend on FastAPI
or Uvicorn interpreting them. Current Uvicorn proxy-header trust defaults to its
local trusted peers; Docker frontend traffic need not appear as the client in
backend access logs. No application rate decision uses those logs or raw headers.
Keep the production backend unexposed; direct backend access bypasses Nginx limits.

## Policy

All values live in `frontend/nginx.conf` and require configuration validation and
image rebuild/reload during an approved deployment when changed.

| Pool | Sustained rate | Excess burst | Immediate fresh allowance |
|---|---:|---:|---:|
| `/api/analyze` | 30/minute/IP | 5 | 6 |
| `/api/estimate`, legacy `/upload` and `/slice` | 10/minute/IP, shared | 2 | 3 |
| `/api/options` | 60/minute/IP | 10 | 11 |
| Other API requests | 60/minute/IP | 5 | 6 |
| Global API, across IPs in this instance | 10/second | 20 | 21 |

Normalized paths, trailing slashes and escaped equivalents select the same pool.
Health exceptions are described below. Legacy routes still remain disabled in
production by MM-005; if explicitly enabled, they cannot evade the quotation pool.

Every pool uses `nodelay`: excess requests are rejected, not placed into a waiting
queue. Nginx uses leaky-bucket accounting, not a reset-on-minute counter. Bursts
spend future credits; they do not increase sustained throughput. Estimate credit
recovers at one request per six seconds, analysis at one per two seconds, options
at one per second. Clearing a full burst takes longer. Rejected requests, other
active requests and global limits can still prevent a later attempt.

Analysis burst 5 permits an upload and several successive orientation clicks
without allowing unlimited repeated geometry work. Estimate burst 2 accommodates
short manual retries/configuration changes; options burst 10 tolerates refreshes.
There is no UI auto-retry. Requests already received may still count after an
AbortController cancellation. Rapid orientation changes can therefore yield 429;
the existing latest-analysis/error/finally logic recovers and the next manual
orientation change can submit again. These are provisional engineering budgets,
not a measured production capacity or commercial policy.

Active API requests are limited to **5 per IP and 20 globally** in this frontend
instance. The global cap helps contain distributed buffering before backend
admission; 20 maximum-size bodies are roughly 1 GiB of possible payload, not a
storage quota. Global rate 10/s is intentionally above an individual customer's
budgets and sheds multi-IP bursts. Both global limits require real-load tuning.

Only bodyless GET/HEAD health probes (Content-Length absent/zero, no
Transfer-Encoding) bypass these counters so monitoring survives heavy API use.
Other methods or bodies on `/health` retain ordinary API protection; changing the
path is not an unlimited-upload loophole. Static files are outside API counters.
Limit_conn counts requests after complete headers, not idle TCP connections or
incomplete headers; HTTP/2/3 concurrent requests count separately.

Frequency/connection rejections use HTTP **429**, application/json, with only:

```json
{"detail":"Too many requests. Please wait a moment and try again."}
```

No Retry-After is guessed for 429: different pools drain at different rates and
connection availability depends on live requests. Backend busy 503 still retains
Retry-After: 5, and upstream 503/504 are not intercepted/reclassified. Frontend
options, analysis and quotation requests use the existing safe message helper,
including HTML 429 from an outer proxy. Calculation/analysis busy state is cleared
by existing finally blocks; selected input remains available for manual retry.
Options loading can be retried by reloading. No design change is included.

## Receive timeouts, buffering and NPM requirements

- `client_body_timeout 30s`: maximum inactivity between body reads, **not** an
  absolute upload deadline or minimum byte rate.
- `client_header_timeout 10s`: complete-header receive timeout. Incomplete headers
  are not yet counted by limit_conn.
- `keepalive_timeout 15s`: idle keep-alive connection timeout.
- Backend file/request limits remain 50/51 MiB; Nginx remains 51 MiB.
- `proxy_request_buffering on` and nonpersistent/unlinked body files remain.
  Large bodies spill to Nginx's own temporary directory, not the backend janitor.
- `proxy_read_timeout 90s` and the backend's 60-second slicer deadline remain;
  neither is a total upload/geometry/request deadline. Receive timeouts may close
  incomplete requests without sending an HTTP response body.

During the later approved NPM installation:

1. Inspect the effective NPM configuration, actual peer addressing and any
   upstream CDN/real-IP settings. Configure only verified exact peers at frontend.
2. Ensure NPM overwrites X-Real-IP with its verified address. Do not forward the
   caller's X-Real-IP unchanged. Spoofed X-Forwarded-For must not affect identity.
3. Align the outer 51 MiB body allowance, receive/header/idle policies and upstream
   read timeout with this application. Validate the effective per-host NPM config
   rather than assuming a GUI setting applies at the right Nginx context.
4. Assess NPM buffering: if it buffers first, slow uploads consume **NPM** resources
   before frontend counters see them. Add appropriate native edge controls at
   their supported scope; do not disable buffering blindly at either hop.
5. Verify distinct IPv4/IPv6 clients, NAT behavior, spoof attempts, normal quotes,
   429 JSON/UI behavior, health and disk/CPU usage before public exposure.

Development Compose keeps direct access with no trusted peers. Production Compose
requires the operator file and gate. Internal SSH-only overrides must deliberately
set REQUIRE_TRUSTED_PROXY=false and mount the empty default file; they must not be
reused unchanged for public NPM exposure. The direct development backend port is
not suitable for a public deployment.

## Verification and remaining limits

`backend/tests/run_nginx_abuse_tests.sh` creates a disposable frontend with no
external network and a synthetic Python HTTP upstream in its network namespace.
The configuration is derived from the real Nginx source: port 80 retains actual
rates; another test listener uses shorter intervals/timeouts. Real loopback peers
represent clients and trusted/untrusted proxies; no VPS addresses are modeled as
facts. Tests cover actual production credit recovery, independent pools, header
spoofing, per-client and IPv6 identity, normalized paths, five/global twenty active
requests, global request rate, receive/keepalive timeouts, continuously slow
bodies, startup trust validation and 413/503/504 preservation. CI runs the suite
on both existing native AMD64/ARM64 runners after image build.

Final review expands the original 14 tests to 18, preserving their objectives
and adding ambiguous/duplicate/missing/invalid address headers, equivalent IP
representations and direct IPv6 spoofing. Normalized repeated slashes, dot
segments, encoded slashes, queries and redirect responses consume the same pool.
Upload tests wait for Nginx's 100 Continue after admission instead of sleeping to
guess when headers were parsed; readiness/release use bounded polling. Rate and
receive-timeout checks necessarily still use monotonic time and test-only short
intervals; these are functional checks, not latency benchmarks.

The runner also checks the shipped entrypoint/Alpine validator/native Nginx parser
with valid IPv6, invalid IPv6, broad trust and directive injection. Both named
containers and the private generated fixture are removed by an exit trap, on
success or ordinary failure/signals; the test exit code is preserved. Images are
local-only (`--pull never`); the frontend has no external network and the runner
joins only that namespace. No privileged mode, Docker socket mount, host network,
additional CI secret or capability grant is used. Host Docker access is the same
existing authority needed for builds. SIGKILL/host failure cannot run shell traps;
the disposable CI host or an operator must handle that exceptional teardown.

The nine real-Orca Nginx smoke checks remain separate. Their test driver paces
ordinary calls with at most three 429 retries, six seconds apart; concurrent busy
checks accept proxy 429 alongside backend 503. That bounded **test-only** pacing
is not automatic retry in the product UI.

Limits are per visible IP: NAT users share budgets, IPv6 address rotation can
evade individual counters, and replicas multiply budgets. Clients can keep bodies
alive indefinitely by sending often enough; active-request caps bound their count
but do not prevent starvation. Partial-header attacks still need connection/edge
capacity controls. Rate state resets on restart; it is not persistent accounting.
An attack can fill global budgets and affect legitimate clients. Health probes
remain an intentional light exception. Outer buffering, disk limits, bandwidth,
and real NPM/VPS load validation remain separate concerns. This is neither
authentication nor absolute DDoS protection. Browser visual validation remains
pending from MM-005; no browser test is claimed here. Subsequent private
commercial configuration is documented in [MM-007](COMMERCIAL_CONFIGURATION.md);
it does not change these ingress controls.

Native semantics: [limit_req](https://nginx.org/en/docs/http/ngx_http_limit_req_module.html),
[limit_conn](https://nginx.org/en/docs/http/ngx_http_limit_conn_module.html),
[realip](https://nginx.org/en/docs/http/ngx_http_realip_module.html),
[receive timeouts](https://nginx.org/en/docs/http/ngx_http_core_module.html#client_body_timeout).
