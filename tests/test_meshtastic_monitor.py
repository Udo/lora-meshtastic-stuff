import pathlib
import sys
import unittest


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
    def test_receive_text_summary_uses_originating_short_name(self) -> None:
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

        self.assertIn("from=PEER", summary)
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

        self.assertIn("from=!mystery", summary)
        self.assertIn("portnum=PRIVATE_APP", summary)


if __name__ == "__main__":
    unittest.main()
