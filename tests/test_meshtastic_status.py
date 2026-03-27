import contextlib
import io
import pathlib
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from tools import meshtastic_status as status


class FakeMyInfo:
    my_node_num = 123


class FakeInterface:
    def __init__(self) -> None:
        self.myInfo = FakeMyInfo()
        self.nodes = {
            "self": {"num": 123, "user": {"id": "!self", "shortName": "SELF"}},
            "good-direct": {
                "num": 456,
                "snr": 6.0,
                "hopsAway": 0,
                "environmentMetrics": {"temperature": 22.0},
                "user": {"id": "!good", "shortName": "GOOD"},
            },
            "good-multihop": {
                "num": 789,
                "snr": "4.5",
                "hopsAway": 2,
                "environmentMetrics": {"temperature": 18.5},
                "user": {"id": "!multi", "shortName": "MULTI"},
            },
            "missing-snr": {
                "num": 999,
                "hopsAway": 1,
                "user": {"id": "!nosnr", "shortName": "NOSNR"},
            },
            "bad-snr": {
                "num": 1000,
                "snr": {"broken": True},
                "hopsAway": 3,
                "user": {"id": "!bad", "shortName": "BAD"},
            },
        }


class MeshtasticStatusNeighborsTests(unittest.TestCase):
    def test_collect_neighbor_rows_skips_invalid_snr_records(self) -> None:
        rows = status.collect_neighbor_rows(FakeInterface())

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["id"], "!good")
        self.assertEqual(rows[1]["id"], "!multi")

    def test_render_neighbors_reports_summary_without_crashing(self) -> None:
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            status.render_neighbors(FakeInterface())

        rendered = output.getvalue()
        self.assertIn("Neighbor Signals", rendered)
        self.assertIn("Neighbors with SNR", rendered)
        self.assertIn("Direct neighbors", rendered)
        self.assertIn("!good", rendered)
        self.assertIn("!multi", rendered)

    def test_collect_proximity_candidates_prefers_direct_neighbors(self) -> None:
        rows = status.collect_proximity_candidates(FakeInterface(), include_multihop=True)

        self.assertEqual(rows[0]["id"], "!good")
        self.assertTrue(rows[0]["is_direct"])
        self.assertEqual(rows[1]["id"], "!multi")


class FakeTelemetryInterface(FakeInterface):
    def __init__(self, response_packet) -> None:
        super().__init__()
        self.response_packet = response_packet

    def sendData(self, _data, destinationId, portNum, wantResponse, onResponse) -> None:
        self.last_destination = destinationId
        self.last_portnum = portNum
        self.last_want_response = wantResponse
        onResponse(self.response_packet)


class MeshtasticStatusTelemetryTests(unittest.TestCase):
    def test_collect_cached_telemetry_candidates_prefers_direct_neighbors(self) -> None:
        rows = status.collect_cached_telemetry_candidates(FakeInterface(), "environment_metrics", include_multihop=True)

        self.assertEqual(rows[0]["id"], "!good")
        self.assertEqual(rows[1]["id"], "!multi")

    def test_request_telemetry_from_node_returns_telemetry_payload(self) -> None:
        iface = FakeTelemetryInterface(
            {
                "decoded": {
                    "portnum": "TELEMETRY_APP",
                    "telemetry": {"environmentMetrics": {"temperature": 21.5}},
                }
            }
        )

        result = status.request_telemetry_from_node(iface, "!good", "environment_metrics", timeout_seconds=0.1)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["telemetry"]["environmentMetrics"]["temperature"], 21.5)
        self.assertEqual(iface.last_destination, "!good")

    def test_request_telemetry_from_node_reports_routing_error(self) -> None:
        iface = FakeTelemetryInterface(
            {
                "decoded": {
                    "portnum": "ROUTING_APP",
                    "routing": {"errorReason": "NO_RESPONSE"},
                }
            }
        )

        result = status.request_telemetry_from_node(iface, "!good", "environment_metrics", timeout_seconds=0.1)

        self.assertEqual(result["status"], "routing-error")
        self.assertEqual(result["reason"], "NO_RESPONSE")

    def test_render_cached_telemetry_uses_cached_values_without_requests(self) -> None:
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            exit_code = status.render_telemetry(
                FakeInterface(),
                telemetry_mode="cached",
                telemetry_type="environment_metrics",
                limit=1,
                include_multihop=False,
                timeout_seconds=0.1,
                json_output=False,
            )

        self.assertEqual(exit_code, 0)
        rendered = output.getvalue()
        self.assertIn("Telemetry Cached", rendered)
        self.assertIn("environmentMetrics", rendered)
        self.assertIn("22.0", rendered)


if __name__ == "__main__":
    unittest.main()
