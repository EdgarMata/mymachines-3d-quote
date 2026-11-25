"""Linux process-group timeout, bounded pipe capture and descendant reaping."""

import ctypes
import os
import selectors
import signal
import subprocess
import time

from app.services.input_validation import positive_integer_setting
from app.services.temp_storage import active_workspace_fds

SLICER_TIMEOUT_SECONDS = positive_integer_setting("SLICER_TIMEOUT_SECONDS", 60)
# Internal diagnostics only: keep the last 1 MiB per stream, draining the rest.
# Bound memory independently of the amount OrcaSlicer writes before its deadline.
MAX_CAPTURED_OUTPUT_BYTES = 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024


class SlicerTimeoutError(TimeoutError):
    pass


def _enable_child_subreaper():
    # Adopt orphaned grandchildren so we can reap this job's process group, even
    # if container PID 1 would otherwise leave zombies. Linux AMD64/ARM64 only.
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [ctypes.c_int] + [ctypes.c_ulong] * 4
    libc.prctl.restype = ctypes.c_int
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), "Unable to enable child process reaping.")


def _kill_group(pgid: int):
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _reap_group(pgid: int):
    # Never use waitpid(-1): other application children must remain waitable.
    while True:
        try:
            os.waitpid(-pgid, 0)
        except ChildProcessError:
            return
        except InterruptedError:
            continue


def _capture_output(process: subprocess.Popen) -> tuple[str, str]:
    deadline = time.monotonic() + SLICER_TIMEOUT_SECONDS
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    truncated = set()

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise subprocess.TimeoutExpired(process.args, SLICER_TIMEOUT_SECONDS)
        return value

    with selectors.DefaultSelector() as selector:
        for name in buffers:
            stream = getattr(process, name)
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        while selector.get_map():
            for key, _ in selector.select(timeout=remaining()):
                try:
                    chunk = os.read(key.fd, READ_CHUNK_BYTES)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                buffer = buffers[key.data]
                buffer.extend(chunk)
                if len(buffer) > MAX_CAPTURED_OUTPUT_BYTES:
                    del buffer[:-MAX_CAPTURED_OUTPUT_BYTES]
                    truncated.add(key.data)
            remaining()

    # Do not poll()/wait() yet: keep the leader's PID reserved until its group
    # has been killed. That also prevents signaling a reused PGID on early exit.
    while os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None:
        time.sleep(min(0.01, remaining()))

    return tuple(
        ("[earlier output omitted]\n" if name in truncated else "")
        + data.decode("utf-8", errors="replace")
        for name, data in buffers.items()
    )


def run_slicer_process(command, *, cwd=None, env=None) -> subprocess.CompletedProcess:
    _enable_child_subreaper()
    with subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True, cwd=cwd, env=env, pass_fds=active_workspace_fds(),
    ) as process:
        try:
            stdout, stderr = _capture_output(process)
        except subprocess.TimeoutExpired as exc:
            raise SlicerTimeoutError("Slicing exceeded the allowed processing time.") from exc
        finally:
            # Run on success, timeout and pipe failures. No unbounded communicate
            # in cleanup: stop writers, reap them, and let the context close pipes.
            _kill_group(process.pid)
            process.wait()
            _reap_group(process.pid)
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
