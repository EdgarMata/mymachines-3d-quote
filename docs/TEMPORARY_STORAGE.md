# Temporary file lifecycle (MM-004)

## Audit and ownership

The audit used the current source and disposable local Linux Docker containers.
It did not access a deployment host or customer models. `PROJECT_ROOT` below is
the configured application root (`/project` in the backend image), not a client
filename. The three application buckets are `tmp/uploads`, `tmp/analysis` and
`tmp/slicer` under that root.

| Producer / data | Location | Normal cleanup | Abrupt process exit |
|---|---|---|---|
| Starlette multipart upload | In-memory `SpooledTemporaryFile`, spilling beyond 1 MiB to the system temporary directory | Framework closes form files; parser also closes files after interrupted/failed parsing | In the verified Linux runtime the spilled file is anonymous/unlinked; the kernel releases it when its final descriptor closes |
| `/upload` | Validated bytes and mesh in memory; no application STL copy | Request/worker releases memory | Process memory is released |
| Legacy `/slice` original STL | `tmp/uploads/mmquote-v1-<random>/original.stl` | Existing `TemporaryDirectory` cleanup on worker exit | Recoverable owned directory |
| `/estimate` oriented STL | `tmp/uploads/mmquote-v1-<random>/oriented.stl` | Same | Same |
| `/analyze` oriented STL | `tmp/analysis/mmquote-v1-<random>/oriented.stl` | Same | Same |
| OrcaSlicer G-code, JSON and other outputs | `tmp/slicer/mmquote-v1-<random>/` | Same, after process-group termination/reaping and statistics extraction | Same; inherited locks protect a surviving slicer |
| Slicer home/config/cache/data/runtime/system-temp writes | Private `home`, `config`, `cache`, `data`, `runtime`, `system-tmp` subdirectories of its output workspace | Removed with the slicer workspace | Same |
| Slicer stdout/stderr | Bounded in-memory tails, 1 MiB per stream from MM-003 | Released after internal handling; no new persistent log file | Process memory is released |
| Nginx request bodies | Build-configured `/var/cache/nginx/client_temp` | Nginx owns these files; `client_body_in_file_only off` keeps its nonpersistent/unlinked body-file behavior | Last open descriptor close releases unlinked bodies; no backend janitor traverses this directory |
| Nginx response buffering | Build-configured `/var/cache/nginx/proxy_temp`, if responses spill | Nginx-managed temporary files | Nginx/container ownership, outside backend recovery |
| Nginx access/error logs | Official image stdout/stderr links | Container logging policy; production log rotation already configured | Logs follow the container logging driver, not model retention |
| Orca AppImage download/extraction during build | `/tmp/OrcaSlicer.AppImage` and `/opt/orca` | Download removed in the same build step; extracted program retained intentionally | Build-time artifacts, not customer models |

A synthetic real OrcaSlicer estimate before the change produced no new files
outside its request directory in the inspected home/cache/local/system-temp
locations. The new per-child environment defensively contains future helper
writes: `cwd`, `HOME`, `XDG_CONFIG_HOME`, `XDG_CACHE_HOME`, `XDG_DATA_HOME`,
`XDG_RUNTIME_DIR`, `TMPDIR`, `TMP` and `TEMP` all point inside its workspace.
Global backend environment/tempfile settings, read-only profiles and pricing are
unchanged. Absolute profile/input paths continue to work. This is containment of
normal helper behavior, not a filesystem sandbox against arbitrary native code.

Linux multipart spill and a partially received buffered Nginx upload were checked
locally: the backing body files were already unlinked while open. These existing
framework/server mechanisms are preserved, not replaced by another `/tmp` sweep.
Recheck ownership if replacing the runtime, framework or Nginx body-file policy.

## Immediate deletion and cancellation

`backend/app/services/temp_storage.py` wraps the existing `TemporaryDirectory`
contexts; it does not replace them with age-based retention. Success, validation
or processing errors, startup failures of OrcaSlicer, and slicing timeouts unwind
the worker's contexts. Files are removed immediately after their last operation,
including statistics extraction. A cancelled/disconnected HTTP task cannot delete
an active worker's files: MM-003 still holds admission until the actual thread
finishes, and the slicer runner kills/waits/reaps its process group before the
workspace context exits. Multipart files remain framework-owned.

A failed directory cleanup emits a generic warning and leaves recovery metadata
for a later attempt. It cannot turn an existing validation error or timeout into
a different cleanup exception. A directory-cleanup failure after otherwise
successful work still produces the existing generic processing failure. Failure
to unlink metadata alone does not fail a completed request whose model is gone.
Recovery tolerates missing files and inaccessible entries and continues with
other candidates. No raw exception, path or client filename is included in these
new warning messages. The audit found no existing model content/client filename
logging requiring changes in this step; slicer diagnostic exceptions remain
internal and endpoints return generic errors. Full API response review is MM-005.

## Crash recovery protocol

Each new workspace has a versioned random name and a sibling hidden `.lease`
file, containing a format marker and the directory's device/inode identity.
Workspaces are mode 0700 and metadata is mode 0600. Metadata is outside the
workspace so partial recursive deletion cannot erase the ownership marker first.
No client value determines any of these paths.

Allocation precedes lock acquisition, but that phase is not eligible for recovery:
an absent or incomplete marker cannot authorize deletion. The workspace is only
yielded to processing after the exclusive lock is held and the complete identity
is written. A short metadata write fails initialization and unwinds the empty
workspace instead of allowing model data into an unidentifiable directory.

An exclusive Linux `flock` covers metadata initialization, processing and normal
cleanup. The input and nested slicer locks are passed to the slicer with
`Popen(pass_fds=...)`; a surviving child retains the locks after abrupt backend
exit. Normal MM-003 group termination/reaping closes those child references before
worker cleanup. Recovery requires a nonblocking exclusive lock: a locked active
directory is always skipped, even when its timestamp is very old.
These are kernel locks on open file descriptions, not PID files. Backend restarts
and PID reuse cannot make a surviving child appear unlocked; the final descriptor
close releases the lock. After acquiring a recovery lock, the cleaner refreshes
the marker's status and verifies it is still singly linked and the current entry
at that name, so a removed/replaced marker opened earlier is not used to authorize
deletion. Directory identity is checked independently.

The application lifespan runs one startup sweep off the event loop, then one
periodic cleaner thread. Shutdown wakes/joins that thread; it does not delete
active work. Sweeps only inspect the three allowlisted buckets. They require the
exact versioned metadata/name format, a regular singly linked metadata file owned
by the current user, a valid identity, sufficient age and an unlocked lease.
A replaced directory inode or a symlink candidate is preserved. Concurrent sweeps
and normal cleanup use the same per-workspace lock; missing entries are harmless.

Root traversal uses directory descriptors and `O_NOFOLLOW`, including the final
project root, `tmp` and bucket entries. Creation and standard cleanup are anchored
to the opened bucket descriptor through Linux `/proc/self/fd`. Recovery uses
Python's symlink-resistant, descriptor-based `shutil.rmtree`; internal symlinks
are unlinked without following their targets. Recovery refuses a runtime lacking
that implementation. It never scans or deletes arbitrary system `/tmp`, Nginx
storage, unrelated project directories or unrecognized legacy temporary folders.
The application roots must remain trusted and writable only by the application
and administrators; hostile filesystem mutation by another process with the same
OS identity is outside this ownership protocol's security boundary.

## Retention settings

| Backend environment variable | Default | Meaning |
|---|---:|---|
| `TEMP_FILE_RETENTION_SECONDS` | `3600` | Minimum age before an unlocked, recognized orphan is eligible for recovery |
| `TEMP_CLEANUP_INTERVAL_SECONDS` | `300` | Time between periodic sweeps after the startup sweep |

Both must be positive integers; invalid configuration prevents application import
and startup. Values are read once at startup. Age comes from the lease timestamp,
initially allocation time, refreshed when work ends before normal cleanup. After
a failed normal cleanup, retention therefore starts again from the end of work.
An abrupt crash leaves the last recorded timestamp; live locks still override age.

Eligibility is not the actual deletion time. A recognized inactive orphan is
removed at startup or a subsequent sweep after becoming eligible, subject to
permissions and I/O. With defaults and uninterrupted operation this is normally
one hour plus up to the next five-minute sweep, not a strict maximum. Active
jobs, stopped containers and failed deletions can exceed that interval. Completed
jobs are never intentionally kept for an hour. There is no model/G-code archive.

## Containers and remaining limits

Both Compose files keep these directories in the backend container's writable
layer; neither mounts them as persistent volumes. Restarting the same container
preserves named leftovers for startup/periodic recovery. Abruptly killing the
backend process may leave both directories and a live slicer; inherited locks
preserve those files until the last owner exits. Stopping the entire container
ends its processes, releases locks and anonymous files, but retains named files
in that same container's writable layer. Recreating/removing the container does
not migrate that layer's temporary files into the new container; a stopped old
container can still retain its old layer until removed. Normal unlink/container
removal is not a claim of physical secure erasure, including on layered storage,
snapshots and backups. No volume, tmpfs, dependency or resource-limit change is
introduced. Production CPU/memory limits and both Compose init settings remain.

Unknown/legacy directories are deliberately preserved for explicit operator review
during an approved deployment. A crash between directory allocation and completed
metadata creation can leave an unrecognized empty directory; the context has not
yet yielded it to model processing. Recovery prefers leaving it untouched over
guessing ownership. Metadata whose ownership/inode no longer
matches is also preserved. Permission failures can require operator action.
No automated migration or generic disk cleanup is included.

This is not a hard disk quota: active G-code, logs and JSON can grow, and many
buffered proxy uploads can consume space before backend admission. Slow uploads,
abuse/rate limiting, hard total geometry deadlines, single-process admission and
real deployment load/storage validation remain pending. Inherited descriptors
depend on child processes preserving them; deliberately escaped process groups,
closed locks, compromised native processes or uninterruptible I/O are not solved
by this policy. Native AMD64/ARM64 CI remains necessary; local tests are not a
deployment benchmark.

Regression tests use isolated synthetic geometry/files and cover immediate
cleanup, errors/timeouts/cancellation, real crash orphans, startup/periodic recovery,
age boundaries, concurrent creation/recovery, active leases, inherited child
locks, invalid configuration, symlinks, replaced identities and cleanup failures.
Final-review tests also exercise pre-marker/pre-lock creation, short metadata
writes, marker replacement before locking, descriptor release, repeated app
lifespans, event-loop responsiveness during a stalled sweep and startup recovery
permission failures. These tests preserve the 109 pre-review backend checks.
