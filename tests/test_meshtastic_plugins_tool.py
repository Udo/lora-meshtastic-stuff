import contextlib
import io
import json
import pathlib
import shutil
import sys
import tempfile
import time
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from tools import meshtastic_plugins


class MeshtasticPluginsToolTests(unittest.TestCase):
    def test_ip_tunnel_status_defaults_to_framework_standard_announcements_enabled(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        try:
            base = pathlib.Path(temp_dir.name)
            plugins_dir = base / "plugin-src"
            runtime_dir = base / "runtime"
            plugins_dir.mkdir()
            shutil.copy2(REPO_ROOT / "plugins" / "IP_TUNNEL_APP.handler.py", plugins_dir / "IP_TUNNEL_APP.handler.py")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = meshtastic_plugins.main(
                    [
                        "--plugins-dir",
                        str(plugins_dir),
                        "--runtime-dir",
                        str(runtime_dir),
                        "IP_TUNNEL_APP",
                        "status",
                    ]
                )

            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            self.assertIn("announce_enabled: True", output)
            self.assertIn("announce_interval_secs: 300", output)
            self.assertIn("announce_secondary: False", output)
        finally:
            temp_dir.cleanup()

    def test_ip_tunnel_config_command_updates_announce_settings(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        try:
            base = pathlib.Path(temp_dir.name)
            plugins_dir = base / "plugin-src"
            runtime_dir = base / "runtime"
            plugins_dir.mkdir()
            shutil.copy2(REPO_ROOT / "plugins" / "IP_TUNNEL_APP.handler.py", plugins_dir / "IP_TUNNEL_APP.handler.py")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = meshtastic_plugins.main(
                    [
                        "--plugins-dir",
                        str(plugins_dir),
                        "--runtime-dir",
                        str(runtime_dir),
                        "IP_TUNNEL_APP",
                        "config",
                        "--announce",
                        "yes",
                        "--announce-interval-secs",
                        "120",
                        "--announce-secondary",
                        "yes",
                    ]
                )

            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            self.assertIn("announce_enabled: True", output)
            self.assertIn("announce_interval_secs: 120", output)
            self.assertIn("announce_secondary: True", output)
            config_path = runtime_dir / "plugins" / "IP_TUNNEL_APP" / "config.json"
            self.assertTrue(config_path.exists())
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(
                config,
                {
                    "announce_enabled": True,
                    "announce_interval_secs": 120,
                    "announce_secondary": True,
                },
            )
        finally:
            temp_dir.cleanup()

    def test_store_forward_stats_command_reads_plugin_storage(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        try:
            base = pathlib.Path(temp_dir.name)
            plugins_dir = base / "plugin-src"
            runtime_dir = base / "runtime"
            plugins_dir.mkdir()
            shutil.copy2(REPO_ROOT / "plugins" / "STORE_FORWARD_APP.handler.py", plugins_dir / "STORE_FORWARD_APP.handler.py")

            message_dir = runtime_dir / "plugins" / "TEXT_MESSAGE_APP" / "messages"
            event_dir = runtime_dir / "plugins" / "TEXT_MESSAGE_APP" / "events"
            message_dir.mkdir(parents=True)
            event_dir.mkdir(parents=True)
            now = time.time()
            record = {
                "direct": False,
                "first_seen_ts": now,
                "hash": "abc123",
                "last_seen_ts": now,
                "packet_from": 7,
                "packet_id": 22,
                "packet_to": 0,
                "payload_hex": "68656c6c6f",
                "payload_text": "hello",
                "seen_count": 1,
            }
            (message_dir / "abc123.json").write_text(json.dumps(record) + "\n", encoding="utf-8")
            today = time.strftime("%Y-%m-%d", time.gmtime(now))
            (event_dir / f"{today}.jsonl").write_text(json.dumps({"hash": "abc123", "ts": now}) + "\n", encoding="utf-8")

            request_dir = runtime_dir / "plugins" / "STORE_FORWARD_APP"
            request_dir.mkdir(parents=True)
            (request_dir / "requests.jsonl").write_text(
                json.dumps({"key": "request", "ts": now}) + "\n" + json.dumps({"key": "history", "ts": now}) + "\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = meshtastic_plugins.main(
                    [
                        "--plugins-dir",
                        str(plugins_dir),
                        "--runtime-dir",
                        str(runtime_dir),
                        "STORE_FORWARD_APP",
                        "stats",
                    ]
                )

            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            self.assertIn("plugin: STORE_FORWARD_APP", output)
            self.assertIn("retention_days: 30", output)
            self.assertIn("heartbeat_enabled: True", output)
            self.assertIn("heartbeat_interval_secs: 3600", output)
            self.assertIn("replay_duplicates: False", output)
            self.assertIn("history_events: 1", output)
            self.assertIn("requests: 1", output)
            self.assertIn("history_requests: 1", output)
        finally:
            temp_dir.cleanup()

    def test_store_forward_stats_command_skips_malformed_storage(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        try:
            base = pathlib.Path(temp_dir.name)
            plugins_dir = base / "plugin-src"
            runtime_dir = base / "runtime"
            plugins_dir.mkdir()
            shutil.copy2(REPO_ROOT / "plugins" / "STORE_FORWARD_APP.handler.py", plugins_dir / "STORE_FORWARD_APP.handler.py")

            message_dir = runtime_dir / "plugins" / "TEXT_MESSAGE_APP" / "messages"
            event_dir = runtime_dir / "plugins" / "TEXT_MESSAGE_APP" / "events"
            request_dir = runtime_dir / "plugins" / "STORE_FORWARD_APP"
            message_dir.mkdir(parents=True)
            event_dir.mkdir(parents=True)
            request_dir.mkdir(parents=True)

            (message_dir / "broken.json").write_text("{not-json\n", encoding="utf-8")
            today = time.strftime("%Y-%m-%d", time.gmtime())
            (event_dir / f"{today}.jsonl").write_text('{"hash":"broken","ts":1}\nnot-json\n', encoding="utf-8")
            now = time.time()
            (request_dir / "requests.jsonl").write_text(
                json.dumps({"key": "request", "ts": now}) + "\nnot-json\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout), self.assertLogs("meshtastic_plugins", level="WARNING") as logs:
                exit_code = meshtastic_plugins.main(
                    [
                        "--plugins-dir",
                        str(plugins_dir),
                        "--runtime-dir",
                        str(runtime_dir),
                        "STORE_FORWARD_APP",
                        "stats",
                    ]
                )

            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            self.assertIn("history_events: 0", output)
            self.assertIn("requests: 1", output)
            self.assertIn("skipped malformed", "\n".join(logs.output))
        finally:
            temp_dir.cleanup()

    def test_store_forward_config_command_updates_duplicate_replay_flag(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        try:
            base = pathlib.Path(temp_dir.name)
            plugins_dir = base / "plugin-src"
            runtime_dir = base / "runtime"
            plugins_dir.mkdir()
            shutil.copy2(REPO_ROOT / "plugins" / "STORE_FORWARD_APP.handler.py", plugins_dir / "STORE_FORWARD_APP.handler.py")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = meshtastic_plugins.main(
                    [
                        "--plugins-dir",
                        str(plugins_dir),
                        "--runtime-dir",
                        str(runtime_dir),
                        "STORE_FORWARD_APP",
                        "config",
                        "--replay-duplicates",
                        "yes",
                    ]
                )

            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            self.assertIn("replay_duplicates: True", output)
            config_path = runtime_dir / "plugins" / "STORE_FORWARD_APP" / "config.json"
            self.assertTrue(config_path.exists())
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(
                config,
                {
                    "heartbeat_enabled": True,
                    "heartbeat_interval_secs": 3600,
                    "heartbeat_secondary": False,
                    "replay_duplicates": True,
                },
            )
        finally:
            temp_dir.cleanup()

    def test_store_forward_config_command_updates_heartbeat_settings(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        try:
            base = pathlib.Path(temp_dir.name)
            plugins_dir = base / "plugin-src"
            runtime_dir = base / "runtime"
            plugins_dir.mkdir()
            shutil.copy2(REPO_ROOT / "plugins" / "STORE_FORWARD_APP.handler.py", plugins_dir / "STORE_FORWARD_APP.handler.py")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = meshtastic_plugins.main(
                    [
                        "--plugins-dir",
                        str(plugins_dir),
                        "--runtime-dir",
                        str(runtime_dir),
                        "STORE_FORWARD_APP",
                        "config",
                        "--heartbeat",
                        "yes",
                        "--heartbeat-interval-secs",
                        "600",
                        "--heartbeat-secondary",
                        "yes",
                    ]
                )

            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            self.assertIn("heartbeat_enabled: True", output)
            self.assertIn("heartbeat_interval_secs: 600", output)
            self.assertIn("heartbeat_secondary: True", output)
            config_path = runtime_dir / "plugins" / "STORE_FORWARD_APP" / "config.json"
            self.assertTrue(config_path.exists())
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(
                config,
                {
                    "heartbeat_enabled": True,
                    "heartbeat_interval_secs": 600,
                    "heartbeat_secondary": True,
                    "replay_duplicates": False,
                },
            )
        finally:
            temp_dir.cleanup()
