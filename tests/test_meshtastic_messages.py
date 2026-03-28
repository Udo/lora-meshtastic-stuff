import contextlib
import io
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from meshtastic.mesh_interface import MeshInterface


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from tools import meshtastic_messages as messages


class FakeMyInfo:
    my_node_num = 123


class FakeInterface:
    def __init__(self) -> None:
        self.myInfo = FakeMyInfo()
        self.nodes = {
            "self": {
                "num": 123,
                "user": {
                    "id": "!0438ca24",
                    "longName": "udo@rolz.org (mobile)",
                    "shortName": "UDO1",
                },
            },
            "worms": {
                "num": 456,
                "user": {
                    "id": "!0439d098",
                    "longName": "Worms Hochheim",
                    "shortName": "WO67",
                },
            },
            "other": {
                "num": 789,
                "user": {
                    "id": "!0555aaaa",
                    "longName": "Worms West",
                    "shortName": "WWST",
                },
            },
        }


class FakeSendInterface(FakeInterface):
    def __init__(self) -> None:
        super().__init__()
        self.send_calls: list[dict[str, object]] = []
        self.closed = False

    def sendText(self, message, destinationId, **kwargs):
        self.send_calls.append(
            {
                "message": message,
                "destinationId": destinationId,
                **kwargs,
            }
        )
        return {"id": 12345}

    def close(self) -> None:
        self.closed = True


class MeshtasticMessagesTests(unittest.TestCase):
    def test_resolve_peer_matches_id_short_name_and_prefix(self) -> None:
        iface = FakeInterface()

        self.assertEqual(messages.resolve_peer(iface, "!0439d098").short_name, "WO67")
        self.assertEqual(messages.resolve_peer(iface, "wo67").node_id, "!0439d098")
        self.assertEqual(messages.resolve_peer(iface, "Worms Hoch").node_id, "!0439d098")

    def test_resolve_peer_rejects_ambiguous_selector(self) -> None:
        with self.assertRaisesRegex(ValueError, "matches multiple nodes"):
            messages.resolve_peer(FakeInterface(), "worms")

    def test_record_from_direct_text_packet_uses_private_scope(self) -> None:
        record = messages.record_from_packet(
            {
                "id": 77,
                "from": 456,
                "fromId": "!0439d098",
                "to": 123,
                "toId": "!0438ca24",
                "channel": 0,
                "rxSnr": 6.0,
                "rxRssi": -102,
                "hopLimit": 3,
                "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "hello public"},
            },
            FakeInterface(),
        )

        self.assertIsNotNone(record)
        self.assertEqual(record["scope"], "private")
        self.assertEqual(record["text"], "hello public")
        self.assertEqual(record["from_short"], "WO67")

    def test_record_from_broadcast_text_packet_uses_public_scope(self) -> None:
        record = messages.record_from_packet(
            {
                "id": 78,
                "from": 456,
                "fromId": "!0439d098",
                "to": 0xFFFFFFFF,
                "toId": "^all",
                "channel": 0,
                "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "hello mesh"},
            },
            FakeInterface(),
        )

        self.assertIsNotNone(record)
        self.assertEqual(record["scope"], "public")
        self.assertEqual(record["text"], "hello mesh")

    def test_record_from_private_packet_decodes_payload_bytes(self) -> None:
        record = messages.record_from_packet(
            {
                "id": 88,
                "from": 456,
                "fromId": "!0439d098",
                "to": 123,
                "toId": "!0438ca24",
                "channel": 0,
                "decoded": {"portnum": "PRIVATE_APP", "payload": b"secret hello"},
            },
            FakeInterface(),
        )

        self.assertIsNotNone(record)
        self.assertEqual(record["scope"], "private")
        self.assertEqual(record["text"], "secret hello")

    def test_send_private_message_uses_direct_text_message_port(self) -> None:
        iface = FakeSendInterface()
        args = messages.build_parser().parse_args(
            [
                "--host",
                "127.0.0.1",
                "--tcp-port",
                "4403",
                "send",
                "WO67",
                "hello",
                "--no-wait-for-ack",
            ]
        )

        with mock.patch.object(messages, "connect_interface_for_target", return_value=iface):
            result = messages.send_private_message(args)

        self.assertEqual(result, 0)
        self.assertEqual(len(iface.send_calls), 1)
        self.assertEqual(iface.send_calls[0]["message"], "hello")
        self.assertEqual(iface.send_calls[0]["destinationId"], "!0439d098")
        self.assertEqual(
            iface.send_calls[0]["portNum"],
            messages.portnums_pb2.PortNum.TEXT_MESSAGE_APP,
        )
        self.assertTrue(iface.closed)

    def test_send_private_message_reports_connection_timeout_cleanly(self) -> None:
        args = messages.build_parser().parse_args(
            [
                "--host",
                "127.0.0.1",
                "--tcp-port",
                "4403",
                "send",
                "WO67",
                "hello",
                "--no-wait-for-ack",
            ]
        )
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            with mock.patch.object(
                messages,
                "connect_interface_for_target",
                side_effect=MeshInterface.MeshInterfaceError("Timed out waiting for connection completion"),
            ):
                result = messages.send_private_message(args)

        self.assertEqual(result, 1)
        self.assertIn("Could not connect to 127.0.0.1:4403", stderr.getvalue())
        self.assertIn("Timed out waiting for connection completion", stderr.getvalue())

    def test_lookup_identity_tolerates_missing_node_cache(self) -> None:
        iface = type("PartialInterface", (), {"nodes": None, "myInfo": None})()

        identity = messages.lookup_identity(iface, node_num=456, node_id="!0439d098")

        self.assertEqual(identity.node_id, "!0439d098")
        self.assertEqual(identity.node_num, 456)

    def test_find_local_identity_tolerates_missing_myinfo(self) -> None:
        iface = type("PartialInterface", (), {"nodes": None, "myInfo": None})()

        identity = messages.find_local_identity(iface)

        self.assertEqual(identity.node_id, "-")
        self.assertIsNone(identity.node_num)

    def test_resolve_peer_reports_empty_node_db_cleanly(self) -> None:
        iface = type("PartialInterface", (), {"nodes": None, "myInfo": None})()

        with self.assertRaisesRegex(ValueError, "no known nodes"):
            messages.resolve_peer(iface, "!0439d098")

    def test_format_log_line_is_grep_friendly(self) -> None:
        line = messages.format_log_line(
            {
                "ts": "2026-03-27T12:00:00Z",
                "dir": "rx",
                "scope": "private",
                "from_id": "!0439d098",
                "text": "hello world",
            }
        )

        self.assertIn("scope=\"private\"", line)
        self.assertIn("from_id=\"!0439d098\"", line)
        self.assertIn("text=\"hello world\"", line)

    def test_log_path_for_name_rejects_path_traversal(self) -> None:
        with self.assertRaisesRegex(ValueError, "log names"):
            messages.log_path_for_name("../escape")

    def test_resolve_log_root_prefers_explicit_then_env(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            explicit = pathlib.Path(temp_dir) / "explicit"
            env_root = pathlib.Path(temp_dir) / "env"

            original = messages.os.environ.get("MESHTASTIC_LOG_DIR")
            try:
                messages.os.environ["MESHTASTIC_LOG_DIR"] = str(env_root)
                self.assertEqual(messages.resolve_log_root(), env_root)
                self.assertEqual(messages.resolve_log_root(str(explicit)), explicit)
            finally:
                if original is None:
                    messages.os.environ.pop("MESHTASTIC_LOG_DIR", None)
                else:
                    messages.os.environ["MESHTASTIC_LOG_DIR"] = original

    def test_tail_lines_returns_requested_suffix(self) -> None:
        lines = ["one", "two", "three"]

        self.assertEqual(messages.tail_lines(lines, 2), ["two", "three"])
        self.assertEqual(messages.tail_lines(lines, 0), [])

    def test_grep_lines_supports_substring_and_regex(self) -> None:
        lines = ["scope=\"public\" text=\"hello\"", "scope=\"private\" text=\"Secret\""]

        self.assertEqual(messages.grep_lines(lines, "public"), [lines[0]])
        self.assertEqual(messages.grep_lines(lines, "secret", ignore_case=True), [lines[1]])
        self.assertEqual(messages.grep_lines(lines, 'scope="p.*?"', regex=True), lines)

    def test_read_log_lines_requires_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = pathlib.Path(temp_dir) / "missing.log"

            with self.assertRaisesRegex(FileNotFoundError, "Log file not found"):
                messages.read_log_lines(missing)

    def test_list_log_files_returns_only_logs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            (root / "a.log").write_text("a\n", encoding="utf-8")
            (root / "b.txt").write_text("b\n", encoding="utf-8")
            (root / "c.log").write_text("c\n", encoding="utf-8")

            self.assertEqual([path.name for path in messages.list_log_files(root)], ["a.log", "c.log"])

    def test_parse_log_line_handles_quoted_values(self) -> None:
        record = messages.parse_log_line('ts="2026-03-27T12:00:00Z" dir="rx" scope="private" text="hello world"')

        self.assertEqual(record["ts"], "2026-03-27T12:00:00Z")
        self.assertEqual(record["dir"], "rx")
        self.assertEqual(record["text"], "hello world")

    def test_parse_log_line_marks_malformed_lines(self) -> None:
        record = messages.parse_log_line('ts="2026-03-27T12:00:00Z" text="unterminated')

        self.assertEqual(record["_parse_error"], "invalid shell-style quoting")
        self.assertIn("_raw", record)

    def test_aggregate_log_records_summarizes_logs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            first = root / "first.log"
            second = root / "second.log"
            first.write_text(
                'ts="2026-03-27T12:00:00Z" dir="tx" scope="private" to_id="!a" text="hello"\n'
                'ts="2026-03-27T12:01:00Z" dir="rx" scope="public" from_id="!b" text="world"\n',
                encoding="utf-8",
            )
            second.write_text(
                'ts="2026-03-27T12:02:00Z" dir="rx" scope="private" from_id="!a" text="again"\n',
                encoding="utf-8",
            )

            summary = messages.aggregate_log_records([first, second])

            self.assertEqual(summary["log_count"], 2)
            self.assertEqual(summary["line_count"], 3)
            self.assertEqual(summary["dir_counts"]["rx"], 2)
            self.assertEqual(summary["scope_counts"]["private"], 2)
            self.assertEqual(summary["unique_peers"], 2)
            self.assertEqual(summary["first_ts"], "2026-03-27T12:00:00Z")
            self.assertEqual(summary["last_ts"], "2026-03-27T12:02:00Z")
            self.assertEqual(summary["malformed_lines"], 0)
            self.assertEqual(summary["top_peers"][0], ("!a", 2))

    def test_aggregate_log_records_skips_malformed_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            log_path = root / "messages.log"
            log_path.write_text(
                'ts="2026-03-27T12:00:00Z" dir="rx" scope="private" from_id="!a" text="ok"\n'
                'ts="2026-03-27T12:01:00Z" text="unterminated\n',
                encoding="utf-8",
            )

            summary = messages.aggregate_log_records([log_path])

            self.assertEqual(summary["line_count"], 2)
            self.assertEqual(summary["malformed_lines"], 1)
            self.assertEqual(summary["dir_counts"]["rx"], 1)

    def test_follow_log_emits_newly_appended_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = pathlib.Path(temp_dir) / "follow.log"
            log_path.write_text("one\n", encoding="utf-8")
            observed: list[str] = []

            def writer() -> None:
                time.sleep(0.05)
                with log_path.open("a", encoding="utf-8") as handle:
                    handle.write("two\nthree\n")

            thread = threading.Thread(target=writer)
            thread.start()
            try:
                messages.follow_log(log_path, observed.append, start_offset=log_path.stat().st_size, poll_interval=0.01, deadline=time.monotonic() + 0.3)
            finally:
                thread.join()

            self.assertEqual(observed, ["two", "three"])

    def test_prune_log_files_removes_old_logs_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            old_log = root / "old.log"
            new_log = root / "new.log"
            old_log.write_text("old\n", encoding="utf-8")
            new_log.write_text("new\n", encoding="utf-8")
            now = time.time()
            old_time = now - 10 * 86400
            os.utime(old_log, (old_time, old_time))

            removed = messages.prune_log_files(root, older_than_seconds=5 * 86400, now=now)

            self.assertEqual([path.name for path in removed], ["old.log"])
            self.assertFalse(old_log.exists())
            self.assertTrue(new_log.exists())


if __name__ == "__main__":
    unittest.main()
