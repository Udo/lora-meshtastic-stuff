import contextlib
import io
import pathlib
import sys
import unittest
from unittest import mock

from meshtastic.mesh_interface import MeshInterface


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import meshtastic_monitor as monitor


class MeshtasticMonitorTests(unittest.TestCase):
    def test_run_reports_connection_timeout_without_traceback(self) -> None:
        args = monitor.build_parser().parse_args([
            "--host",
            "127.0.0.1",
            "--tcp-port",
            "4403",
        ])
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            with mock.patch.object(monitor.signal, "signal"):
                with mock.patch.object(monitor.pub, "subscribe"):
                    with mock.patch.object(monitor.pub, "unsubscribe"):
                        with mock.patch.object(
                            monitor.Monitor,
                            "connect_interface",
                            side_effect=MeshInterface.MeshInterfaceError("Timed out waiting for connection completion"),
                        ):
                            result = monitor.Monitor(args).run()

        self.assertEqual(result, 1)
        self.assertIn("Could not connect to 127.0.0.1:4403", stderr.getvalue())
        self.assertIn("Timed out waiting for connection completion", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
