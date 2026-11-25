"""Real, short-lived synthetic process trees; no customer files or long sleeps."""

import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import slicer_process
from test_processing import VALID_STATS, eventually_available, request


class TestSlicerProcess(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def assert_reaped(self, pid):
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        with self.assertRaises(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)

    def test_normal_process(self):
        result = slicer_process.run_slicer_process([sys.executable, "-c", "print('synthetic success')"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "synthetic success")

    def test_nonzero_exit_is_collected(self):
        result = slicer_process.run_slicer_process([sys.executable, "-c", "raise SystemExit(7)"])
        self.assertEqual(result.returncode, 7)

    def test_timeout_kills_and_reaps_children_and_grandchildren(self):
        grandchild = "import time; time.sleep(30)"
        child = (
            "import os,subprocess,sys,time,signal; from pathlib import Path; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            f"p=subprocess.Popen([sys.executable,'-c',{grandchild!r}]); "
            f"Path({str(self.root / 'descendants')!r}).write_text(str(os.getpid())+' '+str(p.pid)); "
            "time.sleep(30)"
        )
        parent = (
            "import os,subprocess,sys,time; from pathlib import Path; "
            f"Path({str(self.root / 'parent')!r}).write_text(str(os.getpid())); "
            f"subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(30)"
        )
        started = time.monotonic()
        with patch.object(slicer_process, "SLICER_TIMEOUT_SECONDS", 0.5):
            with self.assertRaises(slicer_process.SlicerTimeoutError):
                slicer_process.run_slicer_process([sys.executable, "-c", parent])
        self.assertLess(time.monotonic() - started, 3)
        pids = (self.root / "parent").read_text().split() + (self.root / "descendants").read_text().split()
        self.assertEqual(len(pids), 3)
        for pid in pids:
            self.assert_reaped(int(pid))

    def test_early_parent_exit_does_not_leave_background_child(self):
        child = (
            "import os,time; from pathlib import Path; "
            f"Path({str(self.root / 'child')!r}).write_text(str(os.getpid())); time.sleep(30)"
        )
        parent = (
            "import subprocess,sys,time; from pathlib import Path; "
            f"subprocess.Popen([sys.executable,'-c',{child!r}],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
            f"p=Path({str(self.root / 'child')!r}); "
            "exec('for _ in range(100):\\n if p.exists(): break\\n time.sleep(0.01)')"
        )
        result = slicer_process.run_slicer_process([sys.executable, "-c", parent])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_reaped(int((self.root / "child").read_text()))

    def test_cleanup_after_pipe_capture_exception(self):
        real_popen = slicer_process.subprocess.Popen
        processes = []

        def tracked_popen(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        with patch.object(slicer_process.subprocess, "Popen", side_effect=tracked_popen), patch.object(
            slicer_process, "_capture_output", side_effect=RuntimeError("Synthetic pipe failure")
        ):
            with self.assertRaises(RuntimeError):
                slicer_process.run_slicer_process([sys.executable, "-c", "import time; time.sleep(30)"])
        self.assert_reaped(processes[0].pid)


    def test_log_capture_is_bounded_and_drains_both_streams(self):
        maximum = 4096
        for size in (maximum - 1, maximum, maximum + 1, maximum * 64):
            code = (
                "import os; "
                f"os.write(1, b'o' * {size}); os.write(2, b'e' * {size})"
            )
            with self.subTest(size=size), patch.object(slicer_process, "MAX_CAPTURED_OUTPUT_BYTES", maximum):
                result = slicer_process.run_slicer_process([sys.executable, "-c", code])
            self.assertEqual(result.returncode, 0)
            for captured, letter in ((result.stdout, "o"), (result.stderr, "e")):
                if size > maximum:
                    self.assertEqual(captured, "[earlier output omitted]\n" + letter * maximum)
                else:
                    self.assertEqual(captured, letter * size)

    def test_closed_pipes_do_not_disable_the_process_deadline(self):
        command = [sys.executable, "-c", "import os,time; os.close(1); os.close(2); time.sleep(30)"]
        with patch.object(slicer_process, "SLICER_TIMEOUT_SECONDS", 0.1):
            with self.assertRaises(slicer_process.SlicerTimeoutError):
                slicer_process.run_slicer_process(command)

    def test_unrelated_application_child_remains_alive_and_waitable(self):
        import subprocess
        with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"]) as unrelated:
            try:
                with patch.object(slicer_process, "SLICER_TIMEOUT_SECONDS", 0.1):
                    with self.assertRaises(slicer_process.SlicerTimeoutError):
                        slicer_process.run_slicer_process([sys.executable, "-c", "import time; time.sleep(30)"])
                self.assertIsNone(unrelated.poll())
                os.kill(unrelated.pid, 0)
            finally:
                unrelated.kill()
                unrelated.wait()

    def test_concurrent_process_groups_do_not_reap_one_another(self):
        from concurrent.futures import ThreadPoolExecutor
        marker = self.root / "concurrent-pid"
        stalled_code = (
            "import os,time; from pathlib import Path; "
            f"Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(30)"
        )
        with ThreadPoolExecutor(max_workers=2) as executor, patch.object(slicer_process, "SLICER_TIMEOUT_SECONDS", 1):
            stalled = executor.submit(slicer_process.run_slicer_process, [sys.executable, "-c", stalled_code])
            for _ in range(100):
                if marker.exists():
                    break
                time.sleep(0.01)
            self.assertTrue(marker.exists())
            time.sleep(0.65)
            healthy = executor.submit(slicer_process.run_slicer_process, [
                sys.executable, "-c", "import time; time.sleep(0.5); print('healthy peer')"
            ])
            with self.assertRaises(slicer_process.SlicerTimeoutError):
                stalled.result(timeout=3)
            result = healthy.result(timeout=3)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), "healthy peer")
        self.assert_reaped(int(marker.read_text()))


class TestSlicerTimeoutEndpoints(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        # Actual slicer invocation, but a controlled Python process produces an
        # incomplete output and stalls. Both input/output request dirs must go.
        script = (
            "import os,sys,time; from pathlib import Path; "
            "output=Path(sys.argv[sys.argv.index('--outputdir')+1]); "
            "(output/'partial.gcode').write_text('; incomplete'); "
            "(output/'partial.json').write_text('{}'); "
            "(Path(os.environ['HOME'])/'synthetic.log').write_text('synthetic'); "
            "(Path(os.environ['TMPDIR'])/'synthetic.tmp').write_text('synthetic'); "
            f"Path({str(self.root / 'pid')!r}).write_text(str(os.getpid())); time.sleep(30)"
        )
        for name, value in (
            ("app.main.PROJECT_ROOT", self.root),
            ("app.services.slicer.PROJECT_ROOT", self.root),
            ("app.services.slicer.ORCA_COMMAND", [sys.executable, "-c", script]),
            ("app.services.slicer_process.SLICER_TIMEOUT_SECONDS", 0.4),
        ):
            patcher = patch(name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def assert_clean(self):
        pid = int((self.root / "pid").read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assertFalse(any((self.root / "tmp").rglob("*.stl")))
        self.assertFalse(any((self.root / "tmp").rglob("*.gcode")))
        self.assertFalse(any(path.is_dir() and path.name.startswith("tmp")
                             for path in (self.root / "tmp").rglob("*")))
        for bucket in ("uploads", "analysis", "slicer"):
            root = self.root / "tmp" / bucket
            self.assertFalse(root.exists() and any(root.iterdir()))

    async def test_timeout_504_cleanup_and_next_request(self):
        for path in ("/slice", "/estimate"):
            status, _, result = await request(path)
            self.assertEqual(status, 504, result)
            self.assertEqual(result, {"detail": "Slicing exceeded the allowed processing time. Please try again."})
            self.assert_clean()
            with patch("app.main.slice_stl", return_value=VALID_STATS):
                self.assertEqual((await request("/estimate"))[0], 200)
            self.assertEqual((await request("/health", "GET"))[0], 200)

    async def test_cancellation_during_slicer_waits_for_timeout_and_cleanup(self):
        task = asyncio.create_task(request("/estimate"))
        try:
            for _ in range(100):
                if (self.root / "pid").exists():
                    break
                await asyncio.sleep(0.01)
            self.assertTrue((self.root / "pid").exists())
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual((await request("/upload"))[0], 503)
            pid = int((self.root / "pid").read_text())
            os.kill(pid, 0)  # Actual process is still active: admission must stay held.
            self.assertTrue(any((self.root / "tmp").rglob("*.gcode")))
        finally:
            if not task.done():
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            await eventually_available()
        self.assert_clean()
        self.assertEqual((await request("/upload"))[0], 200)


    async def test_launch_failure_releases_capacity_and_removes_input(self):
        with patch("app.services.slicer.ORCA_COMMAND", ["/missing-mm003-slicer-executable"]):
            status, _, body = await request("/estimate")
        self.assertEqual(status, 500)
        self.assertEqual(body, {"detail": "Slicing failed."})
        self.assertFalse(any((self.root / "tmp").rglob("*.stl")))
        self.assertFalse(any((self.root / "tmp").rglob("*.gcode")))
        self.assertEqual((await request("/upload"))[0], 200)
