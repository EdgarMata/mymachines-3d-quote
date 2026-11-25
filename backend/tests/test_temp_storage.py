"""Isolated synthetic workspaces, crash recovery and actual Linux lock lifetime."""

import asyncio
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from app.services import slicer_process, temp_storage as storage
from app.services.input_validation import positive_integer_setting
from test_processing import VALID_STATS, eventually_available, request


def lease_for(path):
    return path.with_name(f".{path.name}.lease")


def age(path):
    old = time.time() - storage.TEMP_FILE_RETENTION_SECONDS - 10
    os.utime(lease_for(path), (old, old))


def orphan(root, bucket="uploads"):
    # A real abrupt exit: no Python context manager/finalizer can perform cleanup.
    code = """
import os, sys
from pathlib import Path
from app.services.temp_storage import workspace
with workspace(Path(sys.argv[1]), sys.argv[2]) as path:
    (path / 'synthetic.stl').write_bytes(b'synthetic test geometry')
    print(path, flush=True)
    os._exit(0)
"""
    result = subprocess.run([sys.executable, "-c", code, str(root), bucket],
                            capture_output=True, text=True, check=True, timeout=5)
    return Path(result.stdout.strip())


class TestTempStorage(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_normal_and_exception_cleanup_is_immediate(self):
        for bucket in storage.BUCKETS:
            for fail in (False, True):
                with self.subTest(bucket=bucket, fail=fail):
                    try:
                        with storage.workspace(self.root, bucket) as path:
                            (path / "synthetic.stl").write_bytes(b"synthetic")
                            self.assertTrue(lease_for(path).is_file())
                            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
                            self.assertEqual(lease_for(path).stat().st_mode & 0o777, 0o600)
                            self.assertEqual(len(storage.active_workspace_fds()), 1)
                            if fail:
                                raise ValueError("Synthetic processing failure")
                    except ValueError:
                        self.assertTrue(fail)
                    self.assertFalse(path.exists())
                    self.assertFalse(lease_for(path).exists())
                    self.assertEqual(storage.active_workspace_fds(), ())

    def test_nested_workspaces_keep_both_locks_until_their_cleanup(self):
        with storage.workspace(self.root, "uploads") as source:
            first = storage.active_workspace_fds()
            with storage.workspace(self.root, "slicer") as output:
                self.assertEqual(storage.active_workspace_fds()[:1], first)
                self.assertEqual(len(storage.active_workspace_fds()), 2)
            self.assertFalse(output.exists())
            self.assertEqual(storage.active_workspace_fds(), first)
            self.assertTrue(source.exists())
        self.assertEqual(storage.active_workspace_fds(), ())

    def test_orphans_in_all_buckets_are_recovered_once(self):
        paths = [orphan(self.root, bucket) for bucket in storage.BUCKETS]
        for path in paths:
            age(path)
        self.assertEqual(storage.recover_abandoned(self.root), 3)
        self.assertEqual(storage.recover_abandoned(self.root), 0)
        for path in paths:
            self.assertFalse(path.exists())
            self.assertFalse(lease_for(path).exists())

    def test_recent_orphan_is_preserved_even_if_directory_mtime_is_old(self):
        path = orphan(self.root)
        os.utime(path, (0, 0))
        self.assertEqual(storage.recover_abandoned(self.root), 0)
        self.assertTrue((path / "synthetic.stl").exists())

    def test_retention_boundary_is_inclusive(self):
        path = orphan(self.root)
        os.utime(lease_for(path), (100, 100))
        boundary = 100 + storage.TEMP_FILE_RETENTION_SECONDS
        with patch.object(storage.time, "time", return_value=boundary - 0.001):
            self.assertEqual(storage.recover_abandoned(self.root), 0)
        with patch.object(storage.time, "time", return_value=boundary):
            self.assertEqual(storage.recover_abandoned(self.root), 1)

    def test_active_old_workspace_is_preserved_by_other_threads(self):
        with storage.workspace(self.root, "analysis") as path:
            (path / "synthetic.stl").write_bytes(b"synthetic")
            age(path)
            with ThreadPoolExecutor(max_workers=3) as executor:
                results = list(executor.map(lambda _: storage.recover_abandoned(self.root), range(12)))
            self.assertEqual(results, [0] * 12)
            self.assertTrue((path / "synthetic.stl").exists())
        self.assertFalse(path.exists())

    def test_unknown_legacy_unmarked_and_invalid_metadata_are_preserved(self):
        path = orphan(self.root)
        age(path)
        lease_for(path).write_bytes(b"not application metadata")
        age(path)
        root = path.parent
        legacy = root / "tmp_legacy"
        legacy.mkdir()
        unmarked = root / "mmquote-v1-abcdefgh"
        unmarked.mkdir()
        unknown = root / "unrelated.txt"
        unknown.write_text("synthetic")
        self.assertEqual(storage.recover_abandoned(self.root), 0)
        self.assertTrue(all(item.exists() for item in (path, legacy, unmarked, unknown)))

    def test_replaced_directory_inode_is_not_deleted(self):
        path = orphan(self.root)
        age(path)
        original = path.with_name("preserved-original")
        path.rename(original)
        path.mkdir()
        self.assertEqual(storage.recover_abandoned(self.root), 0)
        self.assertTrue(original.exists())
        self.assertTrue(path.exists())

    def test_replaced_metadata_between_open_and_lock_is_not_used(self):
        path = orphan(self.root)
        age(path)
        marker = lease_for(path)
        original = storage.fcntl.flock
        replaced = False

        def replace_before_lock(fd, operation):
            nonlocal replaced
            if not replaced:
                replaced = True
                marker.rename(marker.with_name("preserved-old-metadata"))
                marker.write_bytes(b"replacement must not authorize deletion")
            return original(fd, operation)

        with patch.object(storage.fcntl, "flock", side_effect=replace_before_lock):
            self.assertEqual(storage.recover_abandoned(self.root), 0)
        self.assertTrue((path / "synthetic.stl").exists())
        self.assertTrue(marker.exists())

    def test_creation_before_marker_and_before_lock_is_not_recoverable(self):
        original = storage.os.open
        observed = []

        def inspect_creation(name, flags, *args, **kwargs):
            if flags & os.O_CREAT and str(name).endswith(".lease"):
                # Allocation exists, but no ownership marker exists yet.
                observed.append(storage.recover_abandoned(self.root))
                fd = original(name, flags, *args, **kwargs)
                os.utime(fd, (0, 0))
                # Even a very old pre-lock marker is empty and cannot authorize
                # deletion. Processing cannot begin until locked and initialized.
                observed.append(storage.recover_abandoned(self.root))
                return fd
            return original(name, flags, *args, **kwargs)

        with patch.object(storage.os, "open", side_effect=inspect_creation):
            with storage.workspace(self.root, "uploads") as path:
                self.assertTrue(path.exists())
                self.assertEqual(storage.recover_abandoned(self.root), 0)
        self.assertEqual(observed, [0, 0])
        self.assertFalse(path.exists())

    def test_short_metadata_write_never_exposes_workspace_for_processing(self):
        original = storage.os.write

        def short_write(fd, data):
            return original(fd, data[:3])

        with patch.object(storage.os, "write", side_effect=short_write):
            with self.assertRaises(OSError):
                with storage.workspace(self.root, "uploads"):
                    self.fail("Incomplete ownership metadata was accepted")
        self.assertEqual(storage.active_workspace_fds(), ())
        self.assertEqual(list((self.root / "tmp" / "uploads").iterdir()), [])

    def test_workspace_file_descriptors_close_on_success_and_failure(self):
        before = len(os.listdir("/proc/self/fd"))
        for fail in (False, True):
            try:
                with storage.workspace(self.root, "uploads"):
                    with storage.workspace(self.root, "slicer"):
                        if fail:
                            raise ValueError("Synthetic failure")
            except ValueError:
                self.assertTrue(fail)
        self.assertEqual(len(os.listdir("/proc/self/fd")), before)

    def test_symlink_workspace_and_symlink_metadata_are_never_followed(self):
        for replace_directory in (False, True):
            with self.subTest(replace_directory=replace_directory):
                path = orphan(self.root)
                age(path)
                if replace_directory:
                    path.rename(path.with_name("saved-" + path.name))
                    path.symlink_to(self.root, target_is_directory=True)
                else:
                    marker = lease_for(path)
                    saved = marker.with_name("saved-" + marker.name)
                    marker.rename(saved)
                    marker.symlink_to(saved)
                with self.assertLogs(storage.logger, level="WARNING"):
                    self.assertEqual(storage.recover_abandoned(self.root), 0)
                self.assertTrue(path.exists())

    def test_internal_symlink_is_unlinked_without_removing_external_target(self):
        outside = self.root / "outside"
        outside.mkdir()
        sentinel = outside / "preserve.txt"
        sentinel.write_text("synthetic outside data")
        path = orphan(self.root)
        (path / "external-link").symlink_to(outside, target_is_directory=True)
        age(path)
        self.assertEqual(storage.recover_abandoned(self.root), 1)
        self.assertEqual(sentinel.read_text(), "synthetic outside data")

    def test_root_and_bucket_symlinks_cannot_redirect_creation_or_recovery(self):
        outside = self.root / "outside"
        outside.mkdir()
        sentinel = outside / "preserve.txt"
        sentinel.write_text("synthetic")
        for bucket_link in (False, True):
            with self.subTest(bucket_link=bucket_link):
                project = self.root / str(bucket_link)
                project.mkdir()
                if bucket_link:
                    (project / "tmp").mkdir()
                    (project / "tmp" / "uploads").symlink_to(outside, target_is_directory=True)
                else:
                    (project / "tmp").symlink_to(outside, target_is_directory=True)
                with self.assertLogs(storage.logger, level="WARNING"):
                    self.assertEqual(storage.recover_abandoned(project), 0)
                with self.assertRaises(OSError):
                    with storage.workspace(project, "uploads"):
                        self.fail("Symlink root was accepted")
                self.assertTrue(sentinel.exists())
        with self.assertRaises(ValueError):
            with storage.workspace(self.root, "../outside"):
                self.fail("Unknown bucket was accepted")

    def test_hardlinked_metadata_is_not_accepted(self):
        path = orphan(self.root)
        age(path)
        os.link(lease_for(path), self.root / "another-link")
        self.assertEqual(storage.recover_abandoned(self.root), 0)
        self.assertTrue(path.exists())

    def test_missing_workspace_only_removes_old_valid_metadata(self):
        path = orphan(self.root)
        age(path)
        (path / "synthetic.stl").unlink()
        path.rmdir()
        self.assertEqual(storage.recover_abandoned(self.root), 0)
        self.assertFalse(lease_for(path).exists())

    def test_permission_failure_does_not_stop_recovery_or_log_paths(self):
        first, second = orphan(self.root), orphan(self.root)
        age(first)
        age(second)
        original = storage.shutil.rmtree

        def fail_one(name, **kwargs):
            if name == first.name:
                raise PermissionError("Private client name and filesystem path")
            return original(name, **kwargs)

        with patch.object(storage.shutil, "rmtree", side_effect=fail_one) as mocked:
            mocked.avoids_symlink_attacks = True
            with self.assertLogs(storage.logger, level="WARNING") as logs:
                self.assertEqual(storage.recover_abandoned(self.root), 1)
        self.assertTrue(first.exists())
        self.assertFalse(second.exists())
        self.assertNotIn(str(self.root), " ".join(logs.output))
        self.assertNotIn("Private client", " ".join(logs.output))
        self.assertEqual(storage.recover_abandoned(self.root), 1)

    def test_partial_cleanup_failure_keeps_metadata_and_preserves_original_error(self):
        original = tempfile.TemporaryDirectory

        @contextmanager
        def failing_directory(**kwargs):
            directory = original(**kwargs)
            directory._finalizer.detach()  # Simulate a real failure, not a GC retry.
            try:
                yield directory.name
            finally:
                raise PermissionError("Synthetic cleanup failure")

        with self.assertLogs(storage.logger, level="WARNING"):
            with self.assertRaisesRegex(ValueError, "Original validation failure"):
                with storage.workspace(self.root, "uploads", directory_factory=failing_directory) as path:
                    (path / "synthetic.stl").write_bytes(b"synthetic")
                    raise ValueError("Original validation failure")
        self.assertTrue(lease_for(path).exists())
        self.assertEqual(storage.recover_abandoned(self.root), 0)  # Retention restarted at work end.
        age(path)
        self.assertEqual(storage.recover_abandoned(self.root), 1)

    def test_metadata_unlink_failure_does_not_fail_completed_request(self):
        original = storage.os.unlink

        def fail_marker(name, **kwargs):
            if str(name).endswith(".lease"):
                raise PermissionError("Synthetic metadata failure")
            return original(name, **kwargs)

        with patch.object(storage.os, "unlink", side_effect=fail_marker), self.assertLogs(storage.logger, level="WARNING"):
            with storage.workspace(self.root, "uploads") as path:
                (path / "synthetic.stl").write_bytes(b"synthetic")
        self.assertFalse(path.exists())
        self.assertTrue(lease_for(path).exists())
        age(path)
        self.assertEqual(storage.recover_abandoned(self.root), 0)
        self.assertFalse(lease_for(path).exists())

    def test_simultaneous_recovery_removes_orphan_exactly_once(self):
        path = orphan(self.root)
        age(path)
        barrier = threading.Barrier(4)

        def recover():
            barrier.wait(timeout=3)
            return storage.recover_abandoned(self.root)

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = [executor.submit(recover) for _ in range(4)]
            self.assertEqual(sum(result.result(timeout=3) for result in results), 1)
        self.assertFalse(path.exists())

    def test_recovery_races_with_workspace_creation_and_normal_cleanup(self):
        stop, ready = threading.Event(), threading.Event()

        def sweep():
            ready.set()
            while not stop.is_set():
                storage.recover_abandoned(self.root)

        with ThreadPoolExecutor(max_workers=1) as executor:
            scanner = executor.submit(sweep)
            self.assertTrue(ready.wait(1))
            try:
                for _ in range(30):
                    with storage.workspace(self.root, "analysis") as path:
                        age(path)
                        model = path / "synthetic.stl"
                        model.write_bytes(b"synthetic")
                        self.assertEqual(model.read_bytes(), b"synthetic")
            finally:
                stop.set()
                scanner.result(timeout=3)
        self.assertEqual(list((self.root / "tmp" / "analysis").iterdir()), [])

    def test_slicer_environment_contains_generated_files_without_mutating_global_environment(self):
        before = os.environ.copy()
        with storage.workspace(self.root, "slicer") as path:
            environment = storage.slicer_environment(path)
            for key in ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
                        "XDG_RUNTIME_DIR", "TMPDIR", "TMP", "TEMP"):
                target = Path(environment[key])
                self.assertTrue(target.is_relative_to(path))
                self.assertEqual(target.stat().st_mode & 0o777, 0o700)
                (target / "synthetic.json").write_text("{}")
        self.assertFalse(path.exists())
        self.assertEqual(dict(os.environ), before)

    def test_invalid_retention_and_interval_prevent_startup(self):
        for name in ("TEMP_FILE_RETENTION_SECONDS", "TEMP_CLEANUP_INTERVAL_SECONDS"):
            for value in ("0", "-1", "NaN", "Infinity", "1.5", ""):
                with self.subTest(name=name, value=value), patch.dict(os.environ, {name: value}):
                    with self.assertRaises(ValueError):
                        positive_integer_setting(name, 1)
            result = subprocess.run([sys.executable, "-c", "import app.main"],
                                    env={**os.environ, name: "0"}, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(name + " must be a positive integer", result.stderr)

    def test_inherited_slicer_lock_survives_abrupt_backend_exit(self):
        # Become the adopter so this test can reap its own synthetic orphan.
        slicer_process._enable_child_subreaper()
        pid_file = self.root / "synthetic-pid"
        child_code = (
            "import os,time; from pathlib import Path; "
            f"Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(30)"
        )
        owner_code = f"""
import os, sys, threading, time
from pathlib import Path
from app.services.temp_storage import workspace
from app.services.slicer_process import run_slicer_process
with workspace(Path(sys.argv[1]), 'uploads') as path:
    (path / 'synthetic.stl').write_bytes(b'synthetic')
    # The production runner runs on the workspace-owning worker thread.
    def crash_when_child_is_ready():
        for _ in range(300):
            if Path({str(pid_file)!r}).exists():
                print(path, flush=True)
                os._exit(0)
            time.sleep(0.01)
        os._exit(2)
    threading.Thread(target=crash_when_child_is_ready, daemon=True).start()
    run_slicer_process([sys.executable, '-c', {child_code!r}])
"""
        pid, reaped = None, False
        try:
            result = subprocess.run([sys.executable, "-c", owner_code, str(self.root)],
                                    capture_output=True, text=True, check=True, timeout=5)
            pid = int(pid_file.read_text())
            path = Path(result.stdout.strip())
            age(path)
            self.assertEqual(storage.recover_abandoned(self.root), 0)
            self.assertTrue((path / "synthetic.stl").exists())
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            reaped = True
            self.assertEqual(storage.recover_abandoned(self.root), 1)
        finally:
            if not reaped and pid is None and pid_file.exists():
                pid = int(pid_file.read_text())
            if not reaped and pid is not None:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    os.waitpid(pid, 0)
                except ChildProcessError:
                    pass


class TestTempStorageLifecycle(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        patcher = patch("app.main.PROJECT_ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_startup_and_periodic_recovery_with_clean_shutdown(self):
        first = await asyncio.to_thread(orphan, self.root)
        age(first)
        with patch.object(storage, "TEMP_CLEANUP_INTERVAL_SECONDS", 0.02):
            # Exercise the app's actual lifespan hook, not just the helper.
            from app.main import app
            async with app.router.lifespan_context(app):
                self.assertFalse(first.exists())
                second = await asyncio.to_thread(orphan, self.root)
                age(second)
                for _ in range(100):
                    if not second.exists():
                        break
                    await asyncio.sleep(0.01)
                self.assertFalse(second.exists())
                self.assertEqual((await request("/health", "GET"))[0], 200)
                self.assertEqual((await request("/options", "GET"))[0], 200)
        self.assertFalse(any(thread.name == "quote-temp-recovery" for thread in threading.enumerate()))

    async def test_all_endpoints_remove_models_outputs_and_metadata(self):
        with patch("app.main.slice_stl", return_value=VALID_STATS):
            for endpoint in ("/upload", "/analyze", "/slice", "/estimate"):
                self.assertEqual((await request(endpoint))[0], 200)
                self.assertFalse(any(path.is_file() for path in (self.root / "tmp").rglob("*")))
                self.assertFalse(any(path.name.startswith(storage.PREFIX) for path in (self.root / "tmp").rglob("*")))

    async def test_periodic_recovery_is_off_event_loop_and_lifespans_do_not_leak_threads(self):
        from app.main import app
        loop_thread = threading.get_ident()
        for _ in range(2):
            started, finish = threading.Event(), threading.Event()
            threads = []

            def blocked_sweep(root):
                threads.append(threading.get_ident())
                if len(threads) > 1:  # Startup completes; the periodic pass stalls.
                    started.set()
                    if not finish.wait(3):
                        raise AssertionError("Synthetic sweep was not released")
                return 0

            with patch.object(storage, "recover_abandoned", side_effect=blocked_sweep), patch.object(
                storage, "TEMP_CLEANUP_INTERVAL_SECONDS", 0.01
            ):
                async with app.router.lifespan_context(app):
                    try:
                        self.assertTrue(await asyncio.to_thread(started.wait, 1))
                        self.assertEqual(sum(thread.name == "quote-temp-recovery"
                                             for thread in threading.enumerate()), 1)
                        for endpoint in ("/health", "/options"):
                            self.assertEqual((await asyncio.wait_for(request(endpoint, "GET"), 0.5))[0], 200)
                        self.assertNotIn(loop_thread, threads)
                    finally:
                        finish.set()
            self.assertFalse(any(thread.name == "quote-temp-recovery" for thread in threading.enumerate()))

    async def test_recovery_permission_failure_does_not_block_startup_or_requests(self):
        from app.main import app
        path = await asyncio.to_thread(orphan, self.root)
        age(path)
        with patch.object(storage, "_recover_entry", side_effect=PermissionError("Synthetic failure")), self.assertLogs(
            storage.logger, level="WARNING"
        ):
            async with app.router.lifespan_context(app):
                for endpoint in ("/health", "/options"):
                    self.assertEqual((await request(endpoint, "GET"))[0], 200)
                self.assertEqual((await request("/upload"))[0], 200)
                self.assertTrue(path.exists())
        self.assertEqual(storage.recover_abandoned(self.root), 1)

    async def test_real_runner_generated_files_are_removed_on_success_and_error(self):
        for fail in (False, True):
            code = """
import os, sys
from pathlib import Path
output = Path(sys.argv[sys.argv.index('--outputdir') + 1])
assert Path.cwd() == output
for key in ('HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME', 'XDG_RUNTIME_DIR', 'TMPDIR'):
    target = Path(os.environ[key])
    assert target.is_relative_to(output)
    (target / 'synthetic.log').write_text('synthetic diagnostics')
(output / 'synthetic.json').write_text('{}')
(output / 'synthetic.gcode').write_text(
    '; total filament used [g] = 1\\n; total filament cost = 0.02\\n'
    '; estimated printing time (normal mode) = 1m 0s\\n')
""" + ("raise SystemExit(1)" if fail else "")
            with self.subTest(fail=fail), patch("app.services.slicer.PROJECT_ROOT", self.root), patch(
                "app.services.slicer.ORCA_COMMAND", [sys.executable, "-c", code]
            ):
                status, _, _ = await request("/estimate")
                self.assertEqual(status, 500 if fail else 200)
                for bucket in storage.BUCKETS:
                    root = self.root / "tmp" / bucket
                    self.assertFalse(root.exists() and any(root.iterdir()))

    async def test_cancelled_worker_cannot_be_recovered_until_actual_completion(self):
        started, finish = threading.Event(), threading.Event()
        paths = []

        def blocked_slice(path, **kwargs):
            paths.append(path.parent)
            started.set()
            if not finish.wait(3):
                raise AssertionError("Synthetic worker was not released")
            return VALID_STATS

        with patch("app.main.slice_stl", side_effect=blocked_slice):
            task = asyncio.create_task(request("/estimate"))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                age(paths[0])
                self.assertEqual(await asyncio.to_thread(storage.recover_abandoned, self.root), 0)
                self.assertTrue(paths[0].exists())
                self.assertEqual((await request("/upload"))[0], 503)
            finally:
                finish.set()
                if not task.done():
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                await eventually_available()
            self.assertFalse(paths[0].exists())
            self.assertFalse(lease_for(paths[0]).exists())
