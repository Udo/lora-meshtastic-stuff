import pathlib
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from tools import _meshtastic_common as common


class ResolveMeshtasticTargetTests(unittest.TestCase):
    def test_explicit_host_wins(self) -> None:
        target = common.resolve_meshtastic_target(port="/dev/custom", host="10.0.0.5", tcp_port=5555)

        self.assertEqual(target.mode, "tcp")
        self.assertEqual(target.host, "10.0.0.5")
        self.assertEqual(target.tcp_port, 5555)
        self.assertEqual(target.source, "explicit-host")

    def test_env_host_wins_when_no_explicit_host(self) -> None:
        with mock.patch.dict("os.environ", {"MESHTASTIC_HOST": "mesh.local", "MESHTASTIC_TCP_PORT": "6600"}, clear=False):
            target = common.resolve_meshtastic_target(port="", host="", tcp_port=6600)

        self.assertEqual(target.mode, "tcp")
        self.assertEqual(target.host, "mesh.local")
        self.assertEqual(target.tcp_port, 6600)
        self.assertEqual(target.source, "env-host")

    def test_healthy_proxy_is_preferred_over_serial(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            status_path = pathlib.Path(temp_dir) / "proxy-status.json"
            status_path.write_text('{"listen_host": "127.0.0.1", "listen_port": 4403}\n', encoding="utf-8")

            with mock.patch.object(common, "PROXY_STATUS_FILE", status_path):
                with mock.patch.object(common, "tcp_endpoint_ready", return_value=True):
                    target = common.resolve_meshtastic_target(port="/dev/ttyUSB9", host="", tcp_port=4403)

        self.assertEqual(target.mode, "tcp")
        self.assertEqual(target.host, "127.0.0.1")
        self.assertEqual(target.tcp_port, 4403)
        self.assertEqual(target.source, "local-proxy")

    def test_serial_fallback_when_no_proxy_or_host(self) -> None:
        with mock.patch.object(common, "detect_proxy_target", return_value=None):
            target = common.resolve_meshtastic_target(port="/dev/ttyUSB7", host="", tcp_port=4403)

        self.assertEqual(target.mode, "serial")
        self.assertEqual(target.serial_port, "/dev/ttyUSB7")
        self.assertEqual(target.source, "serial-fallback")

    def test_explain_meshtastic_target_reports_proxy_reasoning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            status_path = pathlib.Path(temp_dir) / "proxy-status.json"
            status_path.write_text('{"listen_host": "127.0.0.1", "listen_port": 4403, "client_count": 2}\n', encoding="utf-8")

            with mock.patch.object(common, "PROXY_STATUS_FILE", status_path):
                with mock.patch.object(common, "tcp_endpoint_ready", return_value=True):
                    details = common.explain_meshtastic_target(port="/dev/ttyUSB9", host="", tcp_port=4403)

        self.assertEqual(details["selected"]["mode"], "tcp")
        self.assertEqual(details["selected"]["source"], "local-proxy")
        self.assertEqual(details["proxy_reachable"], True)
        self.assertEqual(details["proxy_snapshot"]["client_count"], 2)

    def test_explain_meshtastic_target_reports_explicit_host_reasoning(self) -> None:
        details = common.explain_meshtastic_target(port="/dev/ttyUSB5", host="mesh.example", tcp_port=4444)

        self.assertEqual(details["selected"]["mode"], "tcp")
        self.assertEqual(details["selected"]["label"], "mesh.example:4444")
        self.assertIn("explicit --host", details["checks"][0].lower())


if __name__ == "__main__":
    unittest.main()