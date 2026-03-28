import json
import os
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
    def test_windows_platform_uses_scripts_python_and_com_default(self) -> None:
        with mock.patch("platform.system", return_value="Windows"):
            self.assertEqual(common.venv_python_path(), REPO_ROOT / ".venv" / "Scripts" / "python.exe")
            self.assertEqual(common.default_serial_port(), "COM3")

    def test_macos_platform_uses_usbmodem_default(self) -> None:
        with mock.patch("platform.system", return_value="Darwin"):
            self.assertEqual(common.default_serial_port(), "/dev/tty.usbmodem1")

    def test_linux_platform_prefers_existing_ttyacm_port(self) -> None:
        def fake_exists(path: pathlib.Path) -> bool:
            return str(path) == "/dev/ttyACM0"

        with mock.patch("platform.system", return_value="Linux"):
            with mock.patch.object(pathlib.Path, "exists", fake_exists):
                self.assertEqual(common.default_serial_port(), "/dev/ttyACM0")

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

    def test_summarize_proxy_runtime_reports_loaded_config_and_connection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            status_path = pathlib.Path(temp_dir) / "proxy-status.json"
            manager_status_path = pathlib.Path(temp_dir) / "runtime-manager-status.json"
            service_config_path = pathlib.Path(temp_dir) / "service.env"
            service_config_path.write_text("MESHTASTIC_PORT=/dev/ttyUSB0\n", encoding="utf-8")
            status_path.write_text(
                '{"listen_host": "127.0.0.1", "listen_port": 4403, "pid": ' + str(os.getpid()) + ', "serial_connected": true, "config_file": ' + json.dumps(str(service_config_path)) + '}\n',
                encoding="utf-8",
            )
            manager_status_path.write_text(
                '{"manager_pid": 999, "proxy": {"running": true, "pid": 111, "restart_count": 0}, "protocol": {"running": true, "pid": 222, "restart_count": 3}}\n',
                encoding="utf-8",
            )

            with mock.patch.object(common, "tcp_endpoint_ready", return_value=True):
                summary = common.summarize_proxy_runtime(status_path, service_config_path, manager_status_path)

        self.assertEqual(summary["running"], True)
        self.assertEqual(summary["reachable"], True)
        self.assertEqual(summary["connection_status"], "connected")
        self.assertEqual(summary["config_file"], str(service_config_path))
        self.assertEqual(summary["config_file_loaded"], True)
        self.assertEqual(summary["manager_snapshot"]["manager_pid"], 999)
        self.assertEqual(summary["manager_snapshot"]["protocol"]["restart_count"], 3)

    def test_summarize_proxy_runtime_reports_stopped_without_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            status_path = pathlib.Path(temp_dir) / "proxy-status.json"
            service_config_path = pathlib.Path(temp_dir) / "service.env"
            service_config_path.write_text("MESHTASTIC_PORT=/dev/ttyUSB0\n", encoding="utf-8")

            with mock.patch.object(common, "tcp_endpoint_ready", return_value=False):
                summary = common.summarize_proxy_runtime(status_path, service_config_path)

        self.assertEqual(summary["running"], False)
        self.assertEqual(summary["connection_status"], "stopped")
        self.assertEqual(summary["config_file_loaded"], False)
        self.assertEqual(summary["persistent_config_file"], str(service_config_path))

    def test_connect_interface_for_tcp_seeds_node_caches_before_connect(self) -> None:
        events: list[str] = []

        class FakeTcpInterface:
            def __init__(self, host: str, *, portNumber: int, connectNow: bool) -> None:
                self.host = host
                self.portNumber = portNumber
                self.connectNow = connectNow
                self.socket = None
                self.nodes = None
                self.nodesByNum = None

            def myConnect(self) -> None:
                events.append(f"myConnect nodes={self.nodes!r} nodesByNum={self.nodesByNum!r}")
                self.socket = object()

            def connect(self) -> None:
                events.append(f"connect nodes={self.nodes!r} nodesByNum={self.nodesByNum!r}")

        target = common.MeshtasticTarget(mode="tcp", source="test", host="127.0.0.1", tcp_port=4403)

        iface = common.connect_interface_for_target(target, tcp_factory=FakeTcpInterface)

        self.assertIsInstance(iface.nodes, dict)
        self.assertIsInstance(iface.nodesByNum, dict)
        self.assertEqual(
            events,
            [
                "myConnect nodes={} nodesByNum={}",
                "connect nodes={} nodesByNum={}",
            ],
        )

    def test_connect_interface_for_serial_seeds_node_caches_before_manual_connect(self) -> None:
        class FakeSerialInterface:
            def __init__(self, dev_path: str, *, connectNow: bool) -> None:
                self.dev_path = dev_path
                self.connectNow = connectNow
                self.nodes = None
                self.nodesByNum = None
                self.connect_calls = 0

            def connect(self) -> None:
                self.connect_calls += 1

        target = common.MeshtasticTarget(mode="serial", source="test", serial_port="/dev/ttyUSB9")

        iface = common.connect_interface_for_target(
            target,
            serial_factory=FakeSerialInterface,
            serial_connect_now=False,
        )

        self.assertIsInstance(iface.nodes, dict)
        self.assertIsInstance(iface.nodesByNum, dict)
        self.assertEqual(iface.connect_calls, 0)


if __name__ == "__main__":
    unittest.main()
