import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest


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


class MeshtasticMessagesTests(unittest.TestCase):
    def test_resolve_peer_matches_id_short_name_and_prefix(self) -> None:
        iface = FakeInterface()

        self.assertEqual(messages.resolve_peer(iface, "!0439d098").short_name, "WO67")
        self.assertEqual(messages.resolve_peer(iface, "wo67").node_id, "!0439d098")
        self.assertEqual(messages.resolve_peer(iface, "Worms Hoch").node_id, "!0439d098")

    def test_resolve_peer_rejects_ambiguous_selector(self) -> None:
        with self.assertRaisesRegex(ValueError, "matches multiple nodes"):
            messages.resolve_peer(FakeInterface(), "worms")

    def test_record_from_public_packet_uses_text_field(self) -> None:
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
        self.assertEqual(record["scope"], "public")
        self.assertEqual(record["text"], "hello public")
        self.assertEqual(record["from_short"], "WO67")

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