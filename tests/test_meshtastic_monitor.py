import contextlib
import io
import pathlib
import sys
import unittest
from unittest import mock

from meshtastic.mesh_interface import MeshInterface
from meshtastic.protobuf import storeforward_pb2


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import meshtastic_monitor as monitor


class MeshtasticMonitorTests(unittest.TestCase):
    def test_packet_preview_decodes_store_forward_client_history(self) -> None:
        request = storeforward_pb2.StoreAndForward(rr=storeforward_pb2.StoreAndForward.CLIENT_HISTORY)
        request.history.history_messages = 10
        request.history.window = 60
        request.history.last_request = 0
        packet = {
            "decoded": {
                "portnum": "STORE_FORWARD_APP",
                "payload": request.SerializeToString(),
            }
        }

        self.assertEqual(
            monitor.packet_preview(packet),
            "store-forward=client-history messages=10 window=60 last_request=0",
        )

    def test_packet_preview_decodes_store_forward_router_stats(self) -> None:
        response = storeforward_pb2.StoreAndForward(rr=storeforward_pb2.StoreAndForward.ROUTER_STATS)
        response.stats.messages_total = 2
        response.stats.messages_saved = 2
        response.stats.requests = 5
        response.stats.requests_history = 3
        response.stats.heartbeat = True
        response.stats.return_max = 20
        response.stats.return_window = 60
        packet = {
            "decoded": {
                "portnum": "STORE_FORWARD_APP",
                "payload": response.SerializeToString(),
            }
        }

        self.assertEqual(
            monitor.packet_preview(packet),
            "store-forward=router-stats saved=2 total=2 requests=5 history_requests=3 heartbeat=on return_max=20 return_window=60",
        )

    def test_packet_preview_formats_routing_ack_without_error_wording(self) -> None:
        packet = {
            "toId": "!peer",
            "decoded": {
                "requestId": 1234,
                "routing": {"errorReason": "NONE"},
            },
        }

        self.assertEqual(monitor.packet_preview(packet), "ack (requestId=1234, to=!peer)")

    def test_packet_preview_formats_routing_failures_as_errors(self) -> None:
        packet = {
            "toId": "!peer",
            "decoded": {
                "requestId": 5678,
                "routing": {"errorReason": "NO_RESPONSE"},
            },
        }

        self.assertEqual(
            monitor.packet_preview(packet),
            "routing-error=NO_RESPONSE (requestId=5678, to=!peer)",
        )

    def test_connect_interface_starts_tcp_reader_without_waiting_for_full_handshake(self) -> None:
        args = monitor.build_parser().parse_args([
            "--host",
            "127.0.0.1",
            "--tcp-port",
            "4403",
        ])
        mon = monitor.Monitor(args)
        events: list[str] = []

        class FakeThread:
            def __init__(self) -> None:
                self.started = False

            def is_alive(self) -> bool:
                return self.started

            def start(self) -> None:
                self.started = True
                events.append("thread-start")

        class FakeTcpInterface:
            def __init__(self) -> None:
                self._rxThread = FakeThread()

            def _startConfig(self) -> None:
                events.append("start-config")

        fake_iface = FakeTcpInterface()

        with mock.patch.object(monitor, "connect_interface_for_target", return_value=fake_iface) as connect_mock:
            iface = mon.connect_interface()

        self.assertIs(iface, fake_iface)
        connect_mock.assert_called_once()
        _, kwargs = connect_mock.call_args
        self.assertFalse(kwargs["tcp_connect_now"])
        self.assertEqual(events, ["thread-start", "start-config"])

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
