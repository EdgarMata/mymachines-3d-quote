"""Owned workspaces: immediate standard cleanup plus lock-aware crash recovery."""

import asyncio
import fcntl
import logging
import os
import re
import shutil
import stat
import sys
import tempfile
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from app.services.input_validation import positive_integer_setting

TEMP_FILE_RETENTION_SECONDS = positive_integer_setting("TEMP_FILE_RETENTION_SECONDS", 3600)
TEMP_CLEANUP_INTERVAL_SECONDS = positive_integer_setting("TEMP_CLEANUP_INTERVAL_SECONDS", 300)
BUCKETS = ("uploads", "analysis", "slicer")
PREFIX = "mmquote-v1-"
LEASE_NAME = re.compile(r"^\.(mmquote-v1-[a-z0-9_]{8})\.lease$")
MAGIC = b"MYMACHINES-TEMP-V1\n"
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
logger = logging.getLogger(__name__)
_active = threading.local()


@contextmanager
def _root(project_root: Path, bucket: str, create: bool = False):
    if bucket not in BUCKETS:
        raise ValueError("Unknown application temporary directory.")
    fd = os.open(project_root, DIRECTORY_FLAGS)
    try:
        for name in ("tmp", bucket):
            if create:
                try:
                    os.mkdir(name, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(name, DIRECTORY_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd, Path(project_root) / "tmp" / bucket
    finally:
        os.close(fd)


def active_workspace_fds() -> tuple[int, ...]:
    """Pass active locks to the slicer, protecting an orphan still using files."""
    return tuple(getattr(_active, "fds", ()))


def _same_entry(root_fd: int, name: str, expected) -> bool:
    try:
        current = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return (current.st_dev, current.st_ino) == (expected.st_dev, expected.st_ino)


@contextmanager
def workspace(project_root: Path, bucket: str, directory_factory=None):
    """Reuse TemporaryDirectory cleanup while preserving ownership after failure.

    Metadata lives beside the workspace, so a partial rmtree cannot erase the
    recovery marker first. The flock spans metadata initialization, all work and
    normal cleanup; an uninitialized workspace is never eligible for recovery.
    """
    factory = directory_factory or tempfile.TemporaryDirectory
    with _root(project_root, bucket, create=True) as (root_fd, root_path):
        # Anchor allocation/cleanup to the already-open, no-follow root inode.
        directory = factory(prefix=PREFIX, dir=f"/proc/self/fd/{root_fd}")
        entered, lock_fd, lease_name, lock_stat = False, None, None, None
        try:
            name = Path(directory.__enter__()).name
            entered = True
            if LEASE_NAME.fullmatch(f".{name}.lease") is None:
                raise ValueError("Unexpected temporary workspace name.")
            lease_name = f".{name}.lease"
            lock_fd = os.open(lease_name, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
                              0o600, dir_fd=root_fd)
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            lock_stat = os.fstat(lock_fd)
            info = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode):
                raise OSError("Temporary workspace is not a directory.")
            metadata = MAGIC + f"{info.st_dev}:{info.st_ino}\n".encode("ascii")
            if os.write(lock_fd, metadata) != len(metadata):
                # Do not expose a workspace to model processing unless its
                # recovery identity was written completely (e.g. disk full).
                raise OSError("Unable to initialize temporary recovery metadata.")
            previous = active_workspace_fds()
            _active.fds = (*previous, lock_fd)
            try:
                yield root_path / name
            finally:
                _active.fds = previous
        finally:
            original_error = sys.exc_info()
            try:
                if entered:
                    if lock_fd is not None:
                        # Failure recovery ages from the end of work, not from
                        # the last write by a child or from an old directory mtime.
                        try:
                            os.utime(lock_fd, None)
                        except OSError:
                            logger.warning("Unable to refresh temporary recovery metadata.")
                    try:
                        directory.__exit__(*original_error)
                    except OSError:
                        logger.warning("Temporary workspace cleanup failed; recovery will retry.")
                        if original_error[0] is None:
                            raise
            finally:
                if lock_fd is not None:
                    try:
                        # Remove only this exact lease, and only if its directory
                        # really went away. Partial cleanup leaves retry metadata.
                        try:
                            os.stat(name, dir_fd=root_fd, follow_symlinks=False)
                        except FileNotFoundError:
                            if _same_entry(root_fd, lease_name, lock_stat):
                                os.unlink(lease_name, dir_fd=root_fd)
                    except OSError:
                        # The model is already gone; leftover metadata can be
                        # recovered later without masking the request outcome.
                        logger.warning("Unable to remove temporary recovery metadata.")
                    finally:
                        os.close(lock_fd)


def _recover_entry(root_fd: int, lease_name: str, now: float) -> bool:
    match = LEASE_NAME.fullmatch(lease_name)
    if match is None:
        return False
    fd = os.open(lease_name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=root_fd)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        info = os.fstat(fd)  # The last worker may have refreshed it before unlock.
        if info.st_nlink != 1 or not _same_entry(root_fd, lease_name, info):
            return False  # The opened marker was removed/replaced before locking.
        if now - info.st_mtime < TEMP_FILE_RETENTION_SECONDS:
            return False
        content = os.read(fd, 256)
        if not content.startswith(MAGIC):
            return False
        identity = content[len(MAGIC):].strip().split(b":")
        if len(identity) != 2 or not all(part.isdigit() for part in identity):
            return False
        name = match[1]
        try:
            directory_info = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            directory_info = None
        if directory_info is not None:
            if (not stat.S_ISDIR(directory_info.st_mode)
                    or (directory_info.st_dev, directory_info.st_ino) != tuple(map(int, identity))):
                return False
            # The Linux/Python fd-based implementation unlinks internal symlinks
            # rather than traversing them. Refuse platforms without that guarantee.
            if not shutil.rmtree.avoids_symlink_attacks:
                raise OSError("Safe directory cleanup is unavailable.")
            shutil.rmtree(name, dir_fd=root_fd)
        if _same_entry(root_fd, lease_name, info):
            os.unlink(lease_name, dir_fd=root_fd)
        return directory_info is not None
    finally:
        os.close(fd)


def recover_abandoned(project_root: Path) -> int:
    removed = 0
    for bucket in BUCKETS:
        try:
            with _root(project_root, bucket) as (root_fd, _):
                for name in os.listdir(root_fd):
                    try:
                        removed += _recover_entry(root_fd, name, time.time())
                    except FileNotFoundError:
                        pass  # Another cleanup already handled it.
                    except OSError:
                        # No exception text, filenames, model content or paths.
                        logger.warning("Temporary recovery skipped an inaccessible entry.")
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("Temporary recovery skipped an inaccessible application root.")
    return removed


@asynccontextmanager
async def cleanup_lifecycle(project_root: Path):
    # Startup sweep and periodic work stay off the ASGI event loop and do not
    # consume processing admission. flock serializes recovery with active jobs.
    await asyncio.to_thread(recover_abandoned, project_root)
    stop = threading.Event()

    def run():
        while not stop.wait(TEMP_CLEANUP_INTERVAL_SECONDS):
            recover_abandoned(project_root)

    cleaner = threading.Thread(target=run, name="quote-temp-recovery", daemon=True)
    cleaner.start()
    try:
        yield
    finally:
        stop.set()
        await asyncio.to_thread(cleaner.join)


def slicer_environment(output_dir: Path) -> dict[str, str]:
    """Keep slicer home, caches, logs and system-temp output inside its workspace."""
    paths = {
        "HOME": "home", "XDG_CONFIG_HOME": "config", "XDG_CACHE_HOME": "cache",
        "XDG_DATA_HOME": "data", "XDG_RUNTIME_DIR": "runtime", "TMPDIR": "system-tmp",
    }
    environment = os.environ.copy()
    for variable, name in paths.items():
        path = output_dir / name
        path.mkdir(mode=0o700)
        environment[variable] = str(path)
    environment["TMP"] = environment["TEMP"] = environment["TMPDIR"]
    return environment
