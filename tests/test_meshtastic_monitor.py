import pathlib
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from tools import meshtastic_monitor as monitor


class FakeInterface:
    def __init__(self) -> None:
        self.nodes = {
            "self": {
                "num": 123,
                "user": {"id": "!self", "longName": "Self Node", "shortName": "SELF"},
            },
            "peer": {
                "num": 456,
                "user": {"id": "!peer", "longName": "Peer Node", "shortName": "PEER"},
            },
        }


class MeshtasticMonitorTests(unittest.TestCase):
    def test_receive_text_summary_omits_from_prefix(self) -> None:
        summary = monitor.event_summary(
            "meshtastic.receive.text",
            {
                "interface": FakeInterface(),
                "packet": {
                    "from": 456,
                    "fromId": "!peer",
                    "toId": "!self",
                    "decoded": {"text": "hello mesh", "portnum": "TEXT_MESSAGE_APP"},
                },
            },
        )

        self.assertNotIn("from=", summary)
        self.assertIn("text=", summary)
        self.assertIn('"hello mesh"', summary)

    def test_receive_summary_falls_back_to_from_id_when_short_name_missing(self) -> None:
        summary = monitor.event_summary(
            "meshtastic.receive",
            {
                "interface": FakeInterface(),
                "packet": {
                    "from": 999,
                    "fromId": "!mystery",
                    "decoded": {"portnum": "PRIVATE_APP", "payload": b"secret"},
                },
            },
        )

        self.assertIn("portnum=PRIVATE_APP", summary)

    def test_receive_text_summary_falls_back_to_payload_decode_for_text_message_app(self) -> None:
        summary = monitor.event_summary(
            "meshtastic.receive.data.TEXT_MESSAGE_APP",
            {
                "interface": FakeInterface(),
                "packet": {
                    "from": 456,
                    "fromId": "!peer",
                    "decoded": {"portnum": "TEXT_MESSAGE_APP", "payload": b"hello payload"},
                },
            },
        )

        self.assertIn('text="hello payload"', summary)

    def test_emit_prints_padded_topic_and_shortname_column(self) -> None:
        args = monitor.build_parser().parse_args([])
        reporter = monitor.Monitor(args)
        reporter.target = type("Target", (), {"label": "test", "mode": "serial"})()
        buffer = StringIO()
        with redirect_stdout(buffer):
            reporter.emit(
                "meshtastic.receive.text",
                {
                    "interface": FakeInterface(),
                    "packet": {
                        "from": 456,
                        "fromId": "!peer",
                        "decoded": {"text": "hello mesh", "portnum": "TEXT_MESSAGE_APP"},
                    },
                },
            )

        line = buffer.getvalue().strip()
        self.assertIn("receive.text      ", line)
        self.assertIn("PEER ", line)
        self.assertIn('text="hello mesh"', line)

    def test_emit_prints_numeric_sender_when_shortname_is_missing(self) -> None:
        args = monitor.build_parser().parse_args([])
        reporter = monitor.Monitor(args)
        reporter.target = type("Target", (), {"label": "test", "mode": "serial"})()
        buffer = StringIO()
        with redirect_stdout(buffer):
            reporter.emit(
                "meshtastic.receive.position",
                {
                    "interface": FakeInterface(),
                    "packet": {
                        "from": 999,
                        "decoded": {"position": {"latitude": 49.0}, "portnum": "POSITION_APP"},
                    },
                },
            )

        line = buffer.getvalue().strip()
        self.assertIn("receive.position  ", line)
        self.assertIn("#999", line)
        self.assertIn('position={"latitude": 49.0}', line)

    def test_request_stop_closes_interface_immediately(self) -> None:
        args = monitor.build_parser().parse_args([])
        reporter = monitor.Monitor(args)

        class ClosableInterface:
            def __init__(self) -> None:
                self.closed = False

            def close(self) -> None:
                self.closed = True

        reporter.interface = ClosableInterface()
        reporter.request_stop()

        self.assertTrue(reporter.stop_requested)
        self.assertTrue(reporter.stop_event.is_set())
        self.assertTrue(reporter.interface.closed)


if __name__ == "__main__":
    unittest.main()
