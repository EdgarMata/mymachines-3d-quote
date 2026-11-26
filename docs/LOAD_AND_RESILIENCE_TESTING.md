# Load, security and resilience validation (MM-008A / MM-008B)

## Scope and evidence

This is an explicitly invoked local validation harness, not a production service,
benchmark certification or proof that the application has no vulnerabilities.
Use synthetic geometry and synthetic external commercial settings only. No
customer files, commercial data, actual infrastructure addresses or operational
metrics belong in this repository. Ordinary CI remains unchanged; these workloads
must not run automatically on every push.

MM-001–007 protections remain enabled. Neither pricing formulas, technical
profiles, printer selection nor production architecture are modified by this
stage. Existing unit/integration/security tests remain separate regression gates.

## Safe local execution

First inspect CPU availability, MemAvailable, swap pressure, free space on the
Docker filesystem and existing service usage. Do not stop unrelated services or
assume that a container memory limit reserves that amount of physical RAM.
Choose a maintenance window on the local workstation too. A test run must have
an explicitly local Unix-socket Docker context; TCP/SSH endpoints are rejected
before resource creation. Linux cgroup-v2 CPU/current/peak-memory telemetry must
be available. Unsupported telemetry aborts before workload execution.

The run must initially have at least the configured host memory floor plus 384 MiB initially available and
1 GiB free in its result filesystem. More headroom is preferable.
The Docker filesystem must also retain at least the application storage budget
plus a 1 GiB reserve before this run starts.

Build the reviewed local images and run, from the repository root:

```sh
docker compose build
python3 backend/tests/run_load_resilience.py \
  --output-dir /tmp/my-private-mm008a-run \
  --soak-seconds 180 --maximum-seconds 900 \
  --minimum-available-mib 512 --maximum-backend-mib 768 \
  --maximum-storage-mib 256
```

The output directory must be new and outside the checkout; it is created mode
0700. Private JSON/JSONL logs include measured operational details and image IDs.
The run snapshots its helper scripts and records SHA-256 hashes so later source
edits cannot change a workload or probe halfway through that run.
Retain them under operator access controls, not in Git, CI artifacts or public
issues. Generated large STL files are deleted; remaining synthetic settings and
metrics are intentionally retained privately for inspection. Move retained
results out of a volatile `/tmp` filesystem if evidence must survive reboot.

The operator's retained review directory contains a private `report.json` index
of run directories, raw observations, regression logs and exclusions. To relocate
the existing local evidence without printing its contents, use
`find /tmp -maxdepth 2 -path '/tmp/mm008a-review-*/report.json' -print`.
Within each indexed run, retain `environment.json`, helper snapshots/hashes,
`metrics.jsonl`, `requests.jsonl`, container logs/states and summary/analysis files.
Earlier interrupted runs may have fewer artifacts; do not reconstruct missing
measurements as facts. This index is an operator artifact, never a repository file.

The defaults are a conservative workstation test budget, **not new application
limits or an assertion that 768 MiB is enough for every admitted STL**. Adjust
only after checking host headroom. The workload budget is 15 minutes, the short soak
is 3 minutes, and maximum simultaneous clients is 20. Every network operation
has a deadline; no unbounded retry or unbounded response capture is used.
Bounded diagnostic/teardown commands can extend total wall time after the workload
stops. Required telemetry is checked before load; an unexplained monitoring gap
beyond 15 seconds aborts further work. Resource cleanup is checked explicitly.

`--phases core faults complexity` selects the complete default sequence; subsets
allow independent investigation without repeating an already completed soak.
Core subphases can also be selected separately: `baseline`, `concurrency`, `soak`,
`cancellation`, `transport`, `boundaries` and `ascii`. For a bounded targeted reproduction,
use `--phases concurrency --concurrency-levels 20 --origin-modes distinct
--concurrency-repeats 5`; the reported matrix remains explicitly partial.
`--skip-near-volume-estimate` is an explicit safety exclusion after a prior
monitored interruption, not an accepted performance result. The private summary
records selected phases, exclusions and `complete_requested_matrix`; a successful
subset must not be presented as completion of the entire requested matrix.

Containers use an internal Docker bridge without published host ports or
external egress. Names, network and temporary-storage volume contain a unique
run identifier. Only these owned resources are removed on success, failure or
ordinary termination signals. Forced host termination/SIGKILL requires operator
cleanup of the identified test resources; never use broad Docker prune commands.

Backend settings reproduce 2 CPUs / 4 GiB, one Uvicorn worker, one admitted heavy
request and the 60-second slicer timeout. Frontend has 0.5 CPU / 256 MiB. The
client is separately limited to 0.5 CPU / 384 MiB; container swap allowance does
not exceed its memory limit. Test-only PID bounds, system-temp/cache tmpfs bounds
and a measured application-volume ceiling protect the workstation. They do not
implement a production storage quota. A host-memory floor, backend observation
ceiling, application temporary-storage ceiling or total deadline stops the test
and records it as incomplete, never as a pass. Safety observations are sampled;
they cannot prevent every transient allocation or replace cgroup limits.

The frontend uses a synthetic exact trusted loopback peer inside its private
network namespace. Requests normally originate from **untrusted actual TCP peer
addresses**, not spoofed forwarding headers. Multiple loopback source addresses
represent distinct identities at Nginx; same-IP groups bind the same address.
This tests identity/budget separation without 20 additional containers. It does
not model independent Internet paths, physical clients, NAT or real NPM buffering.
Native Nginx regression tests separately exercise trusted/untrusted proxies,
IPv4/IPv6, invalid/duplicate headers and normalized/encoded paths.

## Scenarios and acceptance criteria

1. Baseline: small box, medium box, tessellated curved model, box near the enabled
   printer volume and incompatible box. Analyze and estimate each. A review or
   incompatibility response is valid HTTP 200 with no automatic price, not a
   processing error. Real OrcaSlicer computes ordinary estimates.
2. Concurrency: 1, 2, 5, 10 and 20 simultaneous requests, separately for same-IP
   and distinct-TCP-origin groups. Start with a barrier, retain unchanged Nginx
   rates/bursts and allow its global bucket to drain between groups. Expected
   outcomes are HTTP 200, Nginx 429 and backend 503. Reject unexpected errors,
   unbounded waits, health unavailability or failure of subsequent admission.
   More than one success over a burst does not imply simultaneous heavy work;
   operations can finish sequentially. Inspect sampled slicer groups/resources
   and retain the existing atomic-admission unit tests as complementary evidence.
3. Short soak: rotate bounded synthetic origins through analysis, complete real
   quotations and deliberately invalid STL. Compare repeated cycles, warmed
   versus late server RSS/FD/thread counts, CPU and response-time distributions.
   Retained Python/native allocator pages are not automatically a memory leak.
   The short default soak cannot establish day-long stability.
4. Transport: incomplete/interrupted uploads, backend pre-parser admission,
   five active frontend uploads from one IP, health during saturation, an inactive
   upload and a continuously progressing upload beyond the 30-second inactivity
   allowance. Deliberately close the progressing upload. This demonstrates why
   inactivity timeouts do not provide an absolute upload duration guarantee.
   Nginx can close an inactive body without transmitting a 408 response; require
   its private access log to confirm 408 rather than assuming a response body.
5. Boundaries: invalid/degenerate and extreme finite geometry, quantities 0/1/100/
   101, malformed multipart, declared HTTP bodies above the proxy/backend limit,
   and a valid binary structure just below 50 MiB whose facet count exceeds the
   limit. The last must return 413 before loading the full geometry. Valid ASCII
   box models padded with parser-compliant blank lines are also analyzed at
   progressive 10/25/near-50 MiB sizes to separate byte limits from facet count;
   these are byte-budget tests, not high-topology ASCII benchmarks.
6. Complexity: progressively stream nondegenerate tessellated binary grids with
   10,000, 100,000, 250,000, 500,000 and 1,000,000 facets. Analyze only; open planar
   geometry is intentionally accepted. Abort on safety thresholds rather than
   pushing the workstation into resource exhaustion. Report the last completed
   case and any unexecuted levels. Existing regression tests cover ASCII and
   exact configured facet boundaries; a binary grid is not a general benchmark
   for all topologies at the same face count.
7. Faults: a test-only command replaces the CLI for deterministic nonzero exit,
   a non-responsive process group and backend crash injection. The full configured
   60-second deadline must give HTTP 504 through unchanged Nginx while health stays available and heavy
   admission stays busy. This **simulates a hung slicer**, not an observed hang
   of real OrcaSlicer. Restore the real command and verify a real quote.
   A separate test-only 128 KiB application tmpfs deliberately exhausts temporary
   storage during a larger analysis; require a controlled 500, immediate cleanup
   and a successful subsequent small analysis. This exercises error recovery,
   not a new production quota or a guarantee that the host cannot fill its disk.
8. Lifecycle: kill only the disposable backend while its injected job owns
   workspaces. Restart with the same private test volume: fresh orphans must
   remain before 3600-second eligibility. Age only unlocked markers in that test
   volume to exercise startup cleanup without waiting an hour, then restart and
   verify removal. This is an accelerated eligibility test, not a one-hour soak.
   Verify no remaining slicers/zombies and no owned workspaces after recovery.
9. Configuration: rerun MM-007 production startup, private-file exclusion and
   same-image update/rollback checks. Missing/invalid/demo configuration must
   prevent startup/health and never silently enable demonstration prices.

Exit 0 means completed assertions passed. Exit 1 means a functional/test failure;
exit 2 means a safety/preflight refusal or monitoring interruption (invalid CLI arguments also use argparse exit 2). A partly executed run is
incomplete regardless of the success of its earlier cases. Inspect private logs
before classifying a harness failure as an application defect.

## Reading measurements

Request records contain wall-clock start/end, HTTP status, uploaded byte count,
response size and, when returned, estimated **physical printing time**. Request
duration is elapsed network/parsing/geometry/slicing/pricing time; it is not the
OrcaSlicer estimate of how long the printer will run. No new public processing
timing field is introduced. Container CPU accounting provides an approximate
computational-cost measure and includes background/observation overhead.

The private sampler reads backend/frontend cgroup-v2 CPU usage, current/peak memory and OOM
counters, server/process RSS/FD/thread counts, owned workspace bytes/files and
Nginx temporary storage. Filesystem usage also counts anonymous/unlinked multipart
files on the test system-temp tmpfs. Sampling is approximately half-second plus probe cost;
short peaks can be missed. `memory.peak` is cumulative within a container, not a
per-request peak; recreation resets it. Cgroup memory includes file cache and
tmpfs, whereas server RSS alone omits child-process memory. Do not equate either
with a Python heap leak. Probe processes and Docker healthchecks perturb results.

Correlate request timestamps with samples before/during/after each case. Compare
idle warmed/late snapshots over successive soak cycles. HTTP totals intentionally
exclude unrecorded repeated readiness/admission polls; concurrency members are
aggregated once. No statistical production capacity, Internet latency, physical
printing qualification or absolute security guarantee follows from this test.

Generate a private derived report using the same standard-library tools:

```sh
python3 backend/tests/summarize_load_resilience.py /tmp/my-private-mm008a-run
```

`analysis.json` brackets requests with samples for CPU/memory comparisons,
separates physical print estimates, lists concurrency outcomes and compares
early/late soak RSS/FD/thread observations. Its sampled resource peaks are not
exact per-request maxima. CPU attribution is omitted for requests shorter than
the useful sampling resolution. Keep this report private alongside the raw evidence.

## Regression gate

Run all existing backend tests in Docker with real OrcaSlicer, four frontend
tests, frontend build, both image builds, 18 native Nginx tests and four startup
checks, nine production HTTP checks, six context-exclusion canaries and commercial
startup/update/rollback checks. Validate Nginx and both Compose files and run Git
diff/whitespace/status checks. Native AMD64/ARM64 CI is separate from local load
measurements. No new production dependency or always-on CI load job is required.

## Review findings and proposed release gates

Detailed timings, resource measurements and machine-specific observations remain
in the private run directories. The following conclusions concern implementation
boundaries, not a published capacity benchmark.

- **Fixed, medium availability impact:** an ENOSPC failure during Trimesh filename
  export retained a deleted file descriptor and its occupied storage until cyclic
  GC. A small following analysis could fail despite no visible workspace files.
  Orientation now owns/closes the output stream explicitly. The regression fails
  on the original code and passes after correction; real limited-filesystem
  injection confirms immediate space recovery and a successful following request.
  Geometry, quotation formulas, profiles and selection rules are unchanged.
- **Public availability gate:** progressing uploads can outlive receive inactivity
  timeouts and hold scarce request slots. The configured per-IP/global limits
  behave as designed; they do not provide an absolute duration budget or protect
  buffering performed earlier by NPM. Private access and public exposure have
  different risk profiles.
- **Capacity not yet qualified:** a low-facet near-volume solid can be expensive
  for the slicer, independently of STL complexity. Progressive large-grid analysis
  can also reach workstation safety stops. An interrupted case is not evidence
  of success under the production 4 GiB cap, nor proof that every equal-facet model
  has the same cost. Do not lower/raise MAX_STL_TRIANGLES arbitrarily from a single
  topology or increase worker count to hide rejected requests.
- **Gateway investigation:** an initial 20-origin burst observed an unexpected
  502; the complete controlled rerun and five further 20-origin repetitions
  passed. Its cause is not established. Preserve correlated private proxy/backend logs and repeat the
  targeted case; do not claim an unverified transport explanation as a fix.
- **Limited soak evidence:** compare FD/thread stability, temporary cleanup and
  changing RSS across cycles. A short run cannot establish absence of leaks,
  long-term allocator stability or shared-VPS service isolation.

No additional architectural protection is implemented by this validation stage.
Proposals require a separately reviewed scope and explicit approval:

| Priority / decision | Evidence and proposed acceptance criterion |
|---|---|
| P1 before public exposure: temporary-storage budget | ENOSPC recovery is controlled, but the application has no production hard quota. Approve an isolated storage budget/reserve covering backend, multipart, frontend and outer proxy; exhaustion must not fill storage used by other services, leak data, retain capacity or prevent later recovery. Determine values from ARM64 measurements. |
| P1 before public exposure: absolute upload/edge policy | Progressing uploads retain slots beyond inactivity timeouts. Validate a supported outer-edge duration/resource policy and NPM buffering limits; slow clients must not monopolize the global budget indefinitely. Lowering only the inactivity timer does not solve this. |
| P1 qualification: actual CPU/RAM and facet budget | Complete representative high-cost cases with the intended caps and measured shared-service headroom. No OOM, continuing storage growth or collateral Odoo/PostgreSQL degradation; preserve safe technical-review/incompatibility outcomes. Keep one worker/one heavy job until this gate passes. |
| P2 design review: total geometry/processing deadline | No hard global deadline exists; Python threads are not forcibly cancellable. If worst-case admitted inputs exceed the approved time/resource budget, propose a terminable isolated job boundary before implementing it. Capacity must remain held until actual termination and cleanup. No new duration is justified here. |
| P2 investigation before public exposure: sporadic gateway status | Reproduce large-body concurrency with correlated logs on the actual proxy chain. Expected overload results remain 429/503 with health/recovery; investigate any recurring 502 rather than masking it as success. |
| Keep provisional rates/timeouts; validate usage | Verify same-IP/NAT browser traffic, orientation bursts, distinct clients, real NPM behavior and load. The 60-second CLI timeout is coherent with Nginx's 90-second inactivity allowance, but neither is a total geometry/request deadline. Adjust only from measured evidence. |

## MM-008B: future approved private ARM64 validation

The command-by-command [private ARM64 runbook](MM008B_PRIVATE_VALIDATION.md)
expands this checklist and is linked from [Deployment](DEPLOYMENT.md).
It includes preserving the installed revision/configuration/images, a loopback-only
Compose override, synthetic external configuration, ARM64 builds, quote-only
updates, SSH access, high-cost case reproduction and explicit rollback. No step
has been executed on the VPS as part of MM-008A.

Do not execute this procedure until the maintainer approves a specific deployment
and test window. It does not authorize SSH, VPS changes, public routing or image
publication. Keep actual commands, service names, addresses, metrics and private
commercial files in operator-only notes.

1. Preconditions: verify reviewed commit/image digests, successful native ARM64
   CI, validated synthetic external pricing, private access only, backup/rollback
   availability, free RAM/disk/inodes and resource caps. Identify Odoo/PostgreSQL
   services and a rollback operator. Record an idle baseline before any load.
2. Monitor all affected services: application and NPM CPU/memory/temporary disk,
   host MemAvailable/swap/IO pressure, Odoo latency/errors/workers and PostgreSQL
   active connections, lock waits, query latency and temporary-file activity.
   Use existing authorized monitoring; do not expose database credentials or
   install an unrelated monitoring stack for this stage.
3. Begin with health/options and one small synthetic analysis/quote. Check real
   browser WebGL/file picker, rotation, dimensions, price/time/weight and manual
   retry; compare with HTTP. Validate actual NPM exact peer identities, header
   overwrite, IPv4/IPv6 paths, body limits, buffering and timeouts. Recreated NPM
   addresses require the approved exact-peer refresh procedure, not subnet trust.
4. Progress through representative models, two clients, then five. Only proceed
   to 10/20 clients and higher facet levels if all services retain agreed headroom.
   Repeat same-IP and distinct-origin tests deliberately, followed by a bounded
   longer soak. Do not run crash/fault injection against a shared live service;
   use a separately approved isolated application instance for destructive cases.
5. Before starting, agree absolute memory/disk floors and maximum acceptable
   Odoo/PostgreSQL latency/error changes relative to their idle baseline. Stop
   immediately on approaching these floors, unexpected OOM, persistent busy/5xx,
   DB lock/latency degradation, growing temporary storage or loss of monitoring.
   Do not invent numeric service SLOs without operational evidence.
6. Stop traffic and verify health, real quote, admission, process/FD/thread
   recovery, temporary cleanup and stable Odoo/PostgreSQL behavior. Document any
   allocator/cache retention separately from a continuing growth trend. Exercise
   approved rollback only inside its maintenance window.
7. Public-exposure gate: close all unresolved high-impact findings, approve real
   pricing and profiles privately, decide storage/global deadline/edge upload
   policies from measured evidence, pass browser/NPM validation and confirm no
   collateral effect on shared services. Private validation approval alone does
   not authorize public exposure.
