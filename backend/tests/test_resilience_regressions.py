"""Resource-failure regressions found by the opt-in MM-008A workload."""

import errno
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import trimesh

from app.services.orientation import orient_mesh


class TestExportFailureCleanup(unittest.TestCase):
    def test_failed_stl_write_closes_the_file_without_waiting_for_garbage_collection(self):
        class FullDisk(io.BytesIO):
            def write(self, data):
                raise OSError(errno.ENOSPC, 'Synthetic full temporary filesystem')

        stream = FullDisk()
        retained_error = None
        mesh = trimesh.creation.box()
        native_open = io.open
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'synthetic.stl'

            def open_output(file, mode='r', *args, **kwargs):
                if isinstance(file, (str, Path)) and Path(file) == target and mode == 'wb':
                    return stream
                return native_open(file, mode, *args, **kwargs)

            # Trimesh's filename export opens with builtins.open; Path.open uses
            # io.open. Inject the same write failure at either ownership boundary.
            with patch('builtins.open', side_effect=open_output), patch('io.open', side_effect=open_output):
                try:
                    orient_mesh(mesh, target)
                except OSError as error:
                    retained_error = error
        self.assertIsNotNone(retained_error)
        self.assertEqual(retained_error.errno, errno.ENOSPC)
        # Retain the traceback deliberately: cleanup must not depend on cyclic GC.
        self.assertTrue(stream.closed)
