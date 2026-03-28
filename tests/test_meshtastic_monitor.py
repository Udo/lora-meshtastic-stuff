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
    def test_should_emit_suppresses_duplicate_node_updates(self) -> None:
        args = monitor.build_parser().parse_args([])
        mon = monitor.Monitor(args)
        kwargs = {
            "node": {
                "num": 456,
                "user": {"id": "!peer", "longName": "Peer Node", "shortName": "PEER"},
            }
        }

        mon.connection_established = True
        self.assertTrue(mon.should_emit("meshtastic.node.updated", kwargs))
        self.assertFalse(mon.should_emit("meshtastic.node.updated", kwargs))

    def test_should_emit_allows_changed_node_updates(self) -> None:
        args = monitor.build_parser().parse_args([])
        mon = monitor.Monitor(args)
        mon.connection_established = True
        first = {
            "node": {
                "num": 456,
                "user": {"id": "!peer", "longName": "Peer Node", "shortName": "PEER"},
            }
        }
        second = {
            "node": {
                "num": 456,
                "user": {"id": "!peer", "longName": "Peer Node Renamed", "shortName": "PEER"},
            }
        }

        self.assertTrue(mon.should_emit("meshtastic.node.updated", first))
        self.assertTrue(mon.should_emit("meshtastic.node.updated", second))

    def test_should_emit_suppresses_initial_node_snapshot_until_connected(self) -> None:
        args = monitor.build_parser().parse_args([])
        mon = monitor.Monitor(args)
        kwargs = {
            "node": {
                "num": 456,
                "user": {"id": "!peer", "longName": "Peer Node", "shortName": "PEER"},
            }
        }

        self.assertFalse(mon.should_emit("meshtastic.node.updated", kwargs))
        self.assertTrue(mon.should_emit("meshtastic.connection.established", {"interface": object()}))
        self.assertFalse(mon.should_emit("meshtastic.node.updated", kwargs))

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
