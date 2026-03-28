import contextlib
import io
import pathlib
import sys
import unittest
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from tools import meshtastic_status as status


class FakeMyInfo:
    my_node_num = 123


class FakeMetadata:
    hwModel = "HELTEC_V3"
    firmwareVersion = "2.7.13"


class FakeLocalConfigSection:
    def __init__(self, **values) -> None:
        self._values = values
        self.DESCRIPTOR = type("Descriptor", (), {"fields_by_name": {}})()

    def __getattr__(self, name: str):
        return self._values.get(name)


class FakeLocalConfig:
    def __init__(self) -> None:
        self.device = FakeLocalConfigSection(role="CLIENT")
        self.lora = FakeLocalConfigSection(modem_preset="LONG_FAST", tx_power=20)
        self.network = FakeLocalConfigSection(wifi_enabled=True, wifi_ssid="mesh")
        self.position = FakeLocalConfigSection(fixed_position=False)
        self.bluetooth = FakeLocalConfigSection(enabled=True)


class FakeLocalNode:
    def __init__(self) -> None:
        self.localConfig = FakeLocalConfig()
        self.channels = []

    def getURL(self) -> str:
        return "https://mesh.example/channel"

    def get_channels_with_hash(self):
        return [
            {"index": 0, "role": "PRIMARY", "name": "", "hash": 2},
            {"index": 1, "role": "SECONDARY", "name": "Friends", "hash": 15},
            {"index": 2, "role": "DISABLED", "name": "", "hash": None},
        ]


class FakeInterface:
    def __init__(self) -> None:
        self.metadata = FakeMetadata()
        self.myInfo = FakeMyInfo()
        self.localNode = FakeLocalNode()
        self.nodes = {
            "self": {
                "num": 123,
                "user": {"id": "!self", "shortName": "SELF", "longName": "Self Node"},
                "deviceMetrics": {"batteryLevel": 87, "voltage": 4.05, "uptimeSeconds": 7200},
                "position": {"latitude": 52.52, "longitude": 13.405, "altitude": 35},
            },
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


class MeshtasticStatusSummaryTests(unittest.TestCase):
    def test_render_summary_includes_proxy_runtime_and_config_source(self) -> None:
        output = io.StringIO()

        with mock.patch.object(status, "to_dict") as to_dict_mock:
            to_dict_mock.side_effect = [
                {"hwModel": "HELTEC_V3", "firmwareVersion": "2.7.13"},
                {"pioEnv": "heltec-v3", "rebootCount": 2},
                {
                    "device": {"role": "CLIENT"},
                    "lora": {"region": "EU_868", "modemPreset": "LONG_FAST", "txPower": 20},
                    "network": {"wifiEnabled": True, "wifiSsid": "mesh"},
                    "position": {"fixedPosition": False},
                    "bluetooth": {"enabled": True},
                },
            ]
            with mock.patch.object(
                status,
                "summarize_proxy_runtime",
                return_value={
                    "running": True,
                    "reachable": True,
                    "connection_status": "connected",
                    "host": "127.0.0.1",
                    "tcp_port": 4403,
                    "snapshot": {
                        "dropped_radio_bytes": 17,
                        "ignored_serial_debug_bytes": 64,
                        "invalid_radio_frames": 3,
                    },
                    "manager_snapshot": {
                        "manager_pid": 444,
                        "proxy": {"running": True, "pid": 111, "restart_count": 0},
                        "protocol": {"running": True, "pid": 222, "restart_count": 2},
                    },
                    "config_file_loaded": True,
                    "config_file": "/tmp/meshtastic/service.env",
                    "persistent_config_file": "/tmp/meshtastic/service.env",
                },
            ):
                with contextlib.redirect_stdout(output):
                    status.render_summary(FakeInterface())

        rendered = output.getvalue()
        self.assertIn("Meshtastic Summary", rendered)
        self.assertIn("Proxy/broker", rendered)
        self.assertIn("Channels active", rendered)
        self.assertIn("Primary channel", rendered)
        self.assertIn("unnamed 0x02", rendered)
        self.assertIn("Secondary channels", rendered)
        self.assertIn("Friends 0x0F", rendered)
        self.assertIn("running", rendered)
        self.assertIn("Proxy endpoint", rendered)
        self.assertIn("127.0.0.1:4403 reachable", rendered)
        self.assertIn("Proxy connection", rendered)
        self.assertIn("connected", rendered)
        self.assertIn("Dropped radio bytes", rendered)
        self.assertIn("17", rendered)
        self.assertIn("Ignored serial debug bytes", rendered)
        self.assertIn("64", rendered)
        self.assertIn("Invalid radio frames", rendered)
        self.assertIn("3", rendered)
        self.assertIn("Runtime manager pid", rendered)
        self.assertIn("444", rendered)
        self.assertIn("Runtime proxy child", rendered)
        self.assertIn("Runtime protocol child", rendered)
        self.assertIn("Proxy config loaded", rendered)
        self.assertIn("/tmp/meshtastic/service.env", rendered)

    def test_render_channels_lists_configured_channels(self) -> None:
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            status.render_channels(FakeInterface())

        rendered = output.getvalue()
        self.assertIn("Configured Channels", rendered)
        self.assertIn("Configured entries", rendered)
        self.assertIn("unnamed 0x02", rendered)
        self.assertIn("Friends 0x0F", rendered)
        self.assertIn("PRIMARY", rendered)
        self.assertIn("SECONDARY", rendered)

    def test_render_summary_tolerates_partial_interface_state(self) -> None:
        partial_iface = type(
            "PartialInterface",
            (),
            {
                "metadata": None,
                "myInfo": None,
                "localNode": None,
                "nodes": None,
            },
        )()
        output = io.StringIO()

        with mock.patch.object(
            status,
            "summarize_proxy_runtime",
            return_value={
                "running": False,
                "reachable": False,
                "connection_status": "stopped",
                "host": "127.0.0.1",
                "tcp_port": 4403,
                "snapshot": {},
                "config_file_loaded": False,
                "config_file": None,
                "persistent_config_file": None,
            },
        ):
            with contextlib.redirect_stdout(output):
                status.render_summary(partial_iface)

        rendered = output.getvalue()
        self.assertIn("Meshtastic Summary", rendered)
        self.assertIn("Target", rendered)
        self.assertIn("Node ID", rendered)

    def test_render_config_tolerates_missing_local_node(self) -> None:
        partial_iface = type("PartialInterface", (), {"localNode": None})()
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            status.render_config(partial_iface, [])

        rendered = output.getvalue()
        self.assertIn('"local": {}', rendered)
        self.assertIn('"module": {}', rendered)


if __name__ == "__main__":
    unittest.main()
