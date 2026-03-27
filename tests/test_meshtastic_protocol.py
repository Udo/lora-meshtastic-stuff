import pathlib
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from tools import meshtastic_protocol as protocol


class FakeMyInfo:
    my_node_num = 123


class FakeInterface:
    def __init__(self) -> None:
        self.myInfo = FakeMyInfo()
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


class MeshtasticProtocolTests(unittest.TestCase):
    def test_record_from_packet_logs_telemetry_details(self) -> None:
        record = protocol.record_from_packet(
            {
                "id": 55,
                "from": 456,
                "fromId": "!peer",
                "to": 123,
                "toId": "!self",
                "channel": 0,
                "rxSnr": 7.5,
                "rxRssi": -101,
                "hopLimit": 2,
                "decoded": {
                    "portnum": "TELEMETRY_APP",
                    "telemetry": {"environmentMetrics": {"temperature": 19.5}},
                },
            },
            FakeInterface(),
        )

        self.assertEqual(record["event"], "packet")
        self.assertEqual(record["kind"], "telemetry")
        self.assertEqual(record["telemetry_type"], "environmentMetrics")
        self.assertEqual(record["from_id"], "!peer")
        self.assertEqual(record["to_id"], "!self")

    def test_record_from_topic_logs_connection_event(self) -> None:
        class FakeConnectedInterface:
            hostname = "127.0.0.1"
            portNumber = 4403

        record = protocol.record_from_topic(
            "meshtastic.connection.established",
            {"interface": FakeConnectedInterface()},
            FakeInterface(),
        )

        self.assertEqual(record["event"], "connection")
        self.assertEqual(record["status"], "established")
        self.assertEqual(record["target"], "127.0.0.1:4403")

    def test_record_from_topic_logs_node_update(self) -> None:
        record = protocol.record_from_topic(
            "meshtastic.node.updated",
            {
                "node": {
                    "num": 456,
                    "user": {"id": "!peer", "longName": "Peer Node", "shortName": "PEER"},
                }
            },
            FakeInterface(),
        )

        self.assertEqual(record["event"], "node-update")
        self.assertEqual(record["node_id"], "!peer")
        self.assertIn("Peer Node", record["summary"])


if __name__ == "__main__":
    unittest.main()
