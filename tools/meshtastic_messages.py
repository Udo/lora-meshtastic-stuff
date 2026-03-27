#!/usr/bin/env python3
import argparse
import os
import json
import re
import shlex
import signal
import socket
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from _meshtastic_common import (
    DEFAULT_SERIAL_PORT,
    DEFAULT_TCP_PORT,
    Palette,
    connect_interface_for_target,
    connection_error_message,
    ensure_repo_python,
    interface_target,
    resolve_meshtastic_target,
    style,
)

ensure_repo_python("MESHTASTIC_MESSAGES_VENV_EXEC")

try:
    from meshtastic.protobuf import portnums_pb2
    from meshtastic.serial_interface import SerialInterface
    from meshtastic.tcp_interface import TCPInterface
    from pubsub import pub
    from serial.serialutil import SerialException
except ModuleNotFoundError as exc:
    missing_module = exc.name or "required dependency"
    print(
        f"meshtastic_messages.py could not import {missing_module}. "
        "Run ./setup/meshtastic-python.sh bootstrap first, or use ./setup/meshtastic-python.sh messages ...",
        file=sys.stderr,
    )
    raise SystemExit(1)

PALETTE = Palette()
DEFAULT_LOG_NAME = "messages"
VALID_LOG_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class NodeIdentity:
    node_id: str
    node_num: int | None
    long_name: str
    short_name: str

    @property
    def best_name(self) -> str:
        return self.short_name or self.long_name or self.node_id or "-"


def utc_timestamp(epoch_seconds: float | None = None) -> str:
    if epoch_seconds is None:
        dt = datetime.now(timezone.utc)
    else:
        dt = datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def default_log_root() -> Path:
    return Path.home() / ".local" / "log" / "meshtastic"


def resolve_log_root(explicit_root: str = "") -> Path:
    if explicit_root:
        return Path(explicit_root).expanduser()

    env_root = os.environ.get("MESHTASTIC_LOG_DIR", "")
    if env_root:
        return Path(env_root).expanduser()

    return default_log_root()


def validate_log_name(log_name: str) -> str:
    if not VALID_LOG_NAME.fullmatch(log_name):
        raise ValueError("log names may only contain letters, numbers, dot, underscore, and dash")
    return log_name


def log_path_for_name(log_name: str, root: Path | None = None) -> Path:
    return (root or default_log_root()) / f"{validate_log_name(log_name)}.log"


def read_log_lines(log_path: Path) -> list[str]:
    if not log_path.exists():
        raise FileNotFoundError(f"Log file not found: {log_path}")
    return log_path.read_text(encoding="utf-8").splitlines()


def list_log_files(log_root: Path) -> list[Path]:
    if not log_root.exists():
        return []
    return sorted(path for path in log_root.glob("*.log") if path.is_file())


def tail_lines(lines: list[str], count: int) -> list[str]:
    if count <= 0:
        return []
    return lines[-count:]


def grep_lines(lines: list[str], pattern: str, *, ignore_case: bool = False, regex: bool = False) -> list[str]:
    if regex:
        flags = re.IGNORECASE if ignore_case else 0
        compiled = re.compile(pattern, flags)
        return [line for line in lines if compiled.search(line)]

    if ignore_case:
        lowered = pattern.lower()
        return [line for line in lines if lowered in line.lower()]
    return [line for line in lines if pattern in line]


def parse_log_line(line: str) -> dict[str, str]:
    record: dict[str, str] = {}
    try:
        tokens = shlex.split(line)
    except ValueError:
        return {"_raw": line, "_parse_error": "invalid shell-style quoting"}

    for token in tokens:
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        record[key] = value
    return record


def aggregate_log_records(log_paths: list[Path]) -> dict[str, object]:
    dir_counts: Counter[str] = Counter()
    scope_counts: Counter[str] = Counter()
    peer_counts: Counter[str] = Counter()
    per_log_counts: dict[str, int] = {}
    first_ts: str | None = None
    last_ts: str | None = None
    malformed_lines = 0
    total_lines = 0

    for log_path in log_paths:
        lines = read_log_lines(log_path)
        per_log_counts[log_path.name] = len(lines)
        total_lines += len(lines)

        for line in lines:
            record = parse_log_line(line)
            if "_parse_error" in record:
                malformed_lines += 1
                continue

            direction = record.get("dir") or "unknown"
            scope = record.get("scope") or "unknown"
            dir_counts[direction] += 1
            scope_counts[scope] += 1

            ts = record.get("ts")
            if ts:
                if first_ts is None or ts < first_ts:
                    first_ts = ts
                if last_ts is None or ts > last_ts:
                    last_ts = ts

            if direction == "tx":
                peer = record.get("to_id")
            elif direction == "rx":
                peer = record.get("from_id")
            else:
                peer = record.get("to_id") or record.get("from_id")

            if peer and peer != "-":
                peer_counts[peer] += 1

    return {
        "log_count": len(log_paths),
        "line_count": total_lines,
        "dir_counts": dict(dir_counts),
        "scope_counts": dict(scope_counts),
        "unique_peers": len(peer_counts),
        "top_peers": peer_counts.most_common(5),
        "first_ts": first_ts or "-",
        "last_ts": last_ts or "-",
        "malformed_lines": malformed_lines,
        "per_log_counts": per_log_counts,
    }


def print_stats_summary(summary: dict[str, object]) -> None:
    print("Transcript Stats")
    print(f"Logs: {summary['log_count']}")
    print(f"Lines: {summary['line_count']}")
    print(f"First entry: {summary['first_ts']}")
    print(f"Last entry: {summary['last_ts']}")
    if summary["malformed_lines"]:
        print(f"Malformed lines skipped: {summary['malformed_lines']}")

    dir_counts = summary["dir_counts"]
    scope_counts = summary["scope_counts"]
    print(
        "Directions: "
        f"tx={dir_counts.get('tx', 0)} rx={dir_counts.get('rx', 0)} other={dir_counts.get('unknown', 0)}"
    )
    print(
        "Scopes: "
        f"public={scope_counts.get('public', 0)} private={scope_counts.get('private', 0)} other={scope_counts.get('unknown', 0)}"
    )
    print(f"Unique peers: {summary['unique_peers']}")

    top_peers = summary["top_peers"]
    if top_peers:
        print("Top peers:")
        for peer, count in top_peers:
            print(f"  {peer}: {count}")

    per_log_counts = summary["per_log_counts"]
    if per_log_counts:
        print("Per log:")
        for log_name, count in sorted(per_log_counts.items()):
            print(f"  {log_name}: {count}")


def follow_log(
    log_path: Path,
    emit_line: Callable[[str], None],
    *,
    start_offset: int | None = None,
    poll_interval: float = 0.25,
    deadline: float | None = None,
) -> None:
    if not log_path.exists():
        raise FileNotFoundError(f"Log file not found: {log_path}")

    with log_path.open("r", encoding="utf-8") as handle:
        if start_offset is None:
            handle.seek(0, os.SEEK_END)
        else:
            handle.seek(start_offset)

        while True:
            line = handle.readline()
            if line:
                emit_line(line.rstrip("\n"))
                continue

            if deadline is not None and time.monotonic() >= deadline:
                break
            time.sleep(poll_interval)


def prune_log_files(log_root: Path, older_than_seconds: float, now: float | None = None) -> list[Path]:
    cutoff = (now if now is not None else time.time()) - older_than_seconds
    removed: list[Path] = []
    for path in list_log_files(log_root):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed.append(path)
        except FileNotFoundError:
            continue
    return removed


def render_field(value: object) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.2f}"
    return json.dumps(str(value), ensure_ascii=True)


def format_log_line(record: dict[str, object]) -> str:
    field_order = [
        "ts",
        "dir",
        "scope",
        "from_id",
        "from_short",
        "from_name",
        "to_id",
        "to_short",
        "to_name",
        "channel",
        "packet_id",
        "status",
        "rx_snr",
        "rx_rssi",
        "hop_limit",
        "text",
    ]
    parts: list[str] = []
    for key in field_order:
        if key not in record:
            continue
        parts.append(f"{key}={render_field(record[key])}")
    for key in sorted(record):
        if key in field_order:
            continue
        parts.append(f"{key}={render_field(record[key])}")
    return " ".join(parts)


def append_log_line(log_path: Path, line: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{line}\n")


def node_identity_from_node(node: dict[str, object]) -> NodeIdentity:
    user = node.get("user", {}) if isinstance(node, dict) else {}
    return NodeIdentity(
        node_id=str(user.get("id") or "-"),
        node_num=node.get("num") if isinstance(node, dict) else None,
        long_name=str(user.get("longName") or ""),
        short_name=str(user.get("shortName") or ""),
    )


def known_nodes(iface) -> list[NodeIdentity]:
    return [node_identity_from_node(node) for node in iface.nodes.values()]


def find_local_identity(iface) -> NodeIdentity:
    local_num = iface.myInfo.my_node_num
    for node in iface.nodes.values():
        if node.get("num") == local_num:
            return node_identity_from_node(node)
    return NodeIdentity(node_id="-", node_num=local_num, long_name="", short_name="")


def lookup_identity(iface, *, node_num: int | None = None, node_id: str = "") -> NodeIdentity:
    for node in iface.nodes.values():
        if node_num is not None and node.get("num") == node_num:
            return node_identity_from_node(node)
        user = node.get("user", {})
        if node_id and user.get("id") == node_id:
            return node_identity_from_node(node)
    if node_id:
        return NodeIdentity(node_id=node_id, node_num=node_num, long_name="", short_name="")
    return NodeIdentity(node_id="-", node_num=node_num, long_name="", short_name="")


def _identity_matches_exact(identity: NodeIdentity, selector: str) -> bool:
    lowered = selector.lower()
    return lowered in {
        identity.node_id.lower(),
        str(identity.node_num).lower() if identity.node_num is not None else "",
        identity.long_name.lower(),
        identity.short_name.lower(),
    }


def _identity_matches_prefix(identity: NodeIdentity, selector: str) -> bool:
    lowered = selector.lower()
    candidates = [identity.node_id, identity.long_name, identity.short_name]
    return any(candidate and candidate.lower().startswith(lowered) for candidate in candidates)


def _identity_matches_contains(identity: NodeIdentity, selector: str) -> bool:
    lowered = selector.lower()
    candidates = [identity.long_name, identity.short_name]
    return any(candidate and lowered in candidate.lower() for candidate in candidates)


def resolve_peer(iface, selector: str) -> NodeIdentity:
    selector = selector.strip()
    if not selector:
        raise ValueError("peer selector must not be empty")

    nodes = known_nodes(iface)
    exact_matches = [identity for identity in nodes if _identity_matches_exact(identity, selector)]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        labels = ", ".join(identity.best_name for identity in exact_matches)
        raise ValueError(f"peer selector {selector!r} is ambiguous: {labels}")

    prefix_matches = [identity for identity in nodes if _identity_matches_prefix(identity, selector)]
    if len(prefix_matches) == 1:
        return prefix_matches[0]
    if len(prefix_matches) > 1:
        labels = ", ".join(identity.best_name for identity in prefix_matches)
        raise ValueError(f"peer selector {selector!r} matches multiple nodes: {labels}")

    contains_matches = [identity for identity in nodes if _identity_matches_contains(identity, selector)]
    if len(contains_matches) == 1:
        return contains_matches[0]
    if len(contains_matches) > 1:
        labels = ", ".join(identity.best_name for identity in contains_matches)
        raise ValueError(f"peer selector {selector!r} matches multiple nodes: {labels}")

    raise ValueError(f"peer selector {selector!r} did not match any known node")


def packet_scope(packet: dict[str, object]) -> str | None:
    decoded = packet.get("decoded", {})
    if not isinstance(decoded, dict):
        return None

    portnum = decoded.get("portnum")
    if portnum in ("TEXT_MESSAGE_APP", portnums_pb2.PortNum.TEXT_MESSAGE_APP):
        return "public"
    if portnum in ("PRIVATE_APP", portnums_pb2.PortNum.PRIVATE_APP):
        return "private"
    return None


def packet_text(packet: dict[str, object]) -> str:
    decoded = packet.get("decoded", {})
    if not isinstance(decoded, dict):
        return ""

    text = decoded.get("text")
    if isinstance(text, str) and text:
        return text

    payload = decoded.get("payload")
    if isinstance(payload, bytes):
        return payload.decode("utf-8", errors="replace")
    if isinstance(payload, str):
        return payload
    return ""


def packet_timestamp(packet: dict[str, object]) -> str:
    rx_time = packet.get("rxTime")
    if isinstance(rx_time, (int, float)) and rx_time > 0:
        return utc_timestamp(float(rx_time))
    return utc_timestamp()


def record_from_packet(packet: dict[str, object], iface) -> dict[str, object] | None:
    scope = packet_scope(packet)
    if scope is None:
        return None

    text = packet_text(packet)
    if not text:
        return None

    from_identity = lookup_identity(iface, node_num=packet.get("from"), node_id=str(packet.get("fromId") or ""))
    to_identity = lookup_identity(iface, node_num=packet.get("to"), node_id=str(packet.get("toId") or ""))

    record: dict[str, object] = {
        "ts": packet_timestamp(packet),
        "dir": "rx",
        "scope": scope,
        "from_id": from_identity.node_id,
        "from_short": from_identity.short_name,
        "from_name": from_identity.long_name,
        "to_id": to_identity.node_id,
        "to_short": to_identity.short_name,
        "to_name": to_identity.long_name,
        "channel": packet.get("channel"),
        "packet_id": packet.get("id"),
        "rx_snr": packet.get("rxSnr"),
        "rx_rssi": packet.get("rxRssi"),
        "hop_limit": packet.get("hopLimit"),
        "text": text,
    }
    return record


def send_record(local_identity: NodeIdentity, peer_identity: NodeIdentity, message: str, packet_id: int | None, channel_index: int, status: str) -> dict[str, object]:
    return {
        "ts": utc_timestamp(),
        "dir": "tx",
        "scope": "private",
        "from_id": local_identity.node_id,
        "from_short": local_identity.short_name,
        "from_name": local_identity.long_name,
        "to_id": peer_identity.node_id,
        "to_short": peer_identity.short_name,
        "to_name": peer_identity.long_name,
        "channel": channel_index,
        "packet_id": packet_id,
        "status": status,
        "text": message,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Meshtastic message send and transcript logger")
    parser.add_argument("--port", default=DEFAULT_SERIAL_PORT, help="Serial port for direct device access if no proxy or --host is used")
    parser.add_argument("--host", default="", help="TCP host for a Meshtastic proxy or network-connected node; if omitted, a healthy local proxy is auto-detected")
    parser.add_argument("--tcp-port", type=int, default=DEFAULT_TCP_PORT, help="TCP port for --host mode or MESHTASTIC_HOST")
    parser.add_argument("--log-dir", default="", help="Override the transcript directory; defaults to MESHTASTIC_LOG_DIR or ~/.local/log/meshtastic")

    subparsers = parser.add_subparsers(dest="command", required=True)

    send_parser = subparsers.add_parser("send", help="Send a private text message to a known node by ID or name")
    send_parser.add_argument("peer", help="Peer selector: node ID, short name, long name, or unique prefix")
    send_parser.add_argument("message", nargs="+", help="Message text to send")
    send_parser.add_argument("--channel-index", type=int, default=0, help="Meshtastic channel index to use for the send")
    send_parser.add_argument("--log-name", default=DEFAULT_LOG_NAME, help="Log file name under ~/.local/log/meshtastic without the .log suffix")
    send_parser.add_argument("--ack-wait-seconds", type=float, default=10.0, help="Seconds to wait for an ack or nak before logging timeout")
    send_parser.add_argument("--no-wait-for-ack", action="store_true", help="Do not wait for routing ack or nak before returning")

    sync_parser = subparsers.add_parser("sync", help="Append live public and private messages to ~/.local/log/meshtastic/<logname>.log")
    sync_parser.add_argument("log_name", nargs="?", default=DEFAULT_LOG_NAME, help="Log file name under ~/.local/log/meshtastic without the .log suffix")
    sync_parser.add_argument("--scope", choices=("all", "public", "private"), default="all", help="Which message scope to record")
    sync_parser.add_argument("--timeout", type=float, default=0.0, help="Optional number of seconds to run before exiting; 0 means forever")

    tail_parser = subparsers.add_parser("tail", help="Print the last lines from a message transcript log")
    tail_parser.add_argument("log_name", nargs="?", default=DEFAULT_LOG_NAME, help="Log file name under the transcript directory without the .log suffix")
    tail_parser.add_argument("--lines", type=int, default=40, help="Number of lines to print from the end of the log")
    tail_parser.add_argument("--follow", action="store_true", help="Continue streaming appended lines like tail -f")
    tail_parser.add_argument("--follow-seconds", type=float, default=0.0, help="Optional maximum follow duration in seconds; 0 means follow until interrupted")

    grep_parser = subparsers.add_parser("grep", help="Search a message transcript log for matching lines")
    grep_parser.add_argument("log_name", help="Log file name under the transcript directory without the .log suffix")
    grep_parser.add_argument("pattern", help="Pattern to search for in the transcript log")
    grep_parser.add_argument("--ignore-case", action="store_true", help="Perform a case-insensitive search")
    grep_parser.add_argument("--regex", action="store_true", help="Treat the pattern as a regular expression")
    grep_parser.add_argument("--count", action="store_true", help="Print only the number of matching lines")

    stats_parser = subparsers.add_parser("stats", help="Print a small summary over transcript logs")
    stats_parser.add_argument("log_name", nargs="?", default="", help="Optional single log name to summarize; defaults to all transcript logs")

    prune_parser = subparsers.add_parser("prune", help="Delete old transcript logs from the transcript directory")
    prune_parser.add_argument("--days", type=float, default=30.0, help="Delete logs older than this many days")
    prune_parser.add_argument("--dry-run", action="store_true", help="Show which logs would be removed without deleting them")

    return parser


class MessageSync:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.target = resolve_meshtastic_target(args.port, args.host, args.tcp_port)
        self.log_root = resolve_log_root(args.log_dir)
        self.log_path = log_path_for_name(args.log_name, self.log_root)
        self.interface = None
        self.stop_requested = False

    def request_stop(self, _signum=None, _frame=None) -> None:
        self.stop_requested = True

    def emit_record(self, record: dict[str, object]) -> None:
        line = format_log_line(record)
        print(line, flush=True)
        append_log_line(self.log_path, line)

    def handle_packet(self, packet: dict[str, object]) -> None:
        record = record_from_packet(packet, self.interface)
        if record is None:
            return
        if self.args.scope != "all" and record.get("scope") != self.args.scope:
            return
        self.emit_record(record)

    def on_event(self, topic=pub.AUTO_TOPIC, **kwargs) -> None:
        topic_name = topic.getName()
        if not topic_name.startswith("meshtastic.receive"):
            return
        packet = kwargs.get("packet")
        if isinstance(packet, dict):
            self.handle_packet(packet)

    def run(self) -> int:
        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGTERM, self.request_stop)
        pub.subscribe(self.on_event, pub.ALL_TOPICS)
        try:
            try:
                self.interface = connect_interface_for_target(
                    self.target,
                    serial_factory=SerialInterface,
                    tcp_factory=TCPInterface,
                    serial_connect_now=False,
                    tcp_connect_now=True,
                )
            except (SerialException, OSError, socket.error) as exc:
                print(connection_error_message(self.target, exc), file=sys.stderr)
                return 1
            if self.target.mode != "tcp":
                self.interface.connect()

            header = style(PALETTE, PALETTE.bold + PALETTE.green, "Syncing Meshtastic messages")
            details = style(
                PALETTE,
                PALETTE.dim,
                f"from {interface_target(self.interface)} into {self.log_path} (Ctrl-C to stop)",
            )
            print(f"{header} {details}", flush=True)

            deadline = time.monotonic() + self.args.timeout if self.args.timeout > 0 else None
            while not self.stop_requested:
                if deadline is not None and time.monotonic() >= deadline:
                    break
                time.sleep(0.25)
        finally:
            pub.unsubscribe(self.on_event, pub.ALL_TOPICS)
            if self.interface is not None:
                self.interface.close()
        return 0


def send_private_message(args: argparse.Namespace) -> int:
    target = resolve_meshtastic_target(args.port, args.host, args.tcp_port)
    log_path = log_path_for_name(args.log_name, resolve_log_root(args.log_dir))
    message_text = " ".join(args.message)

    try:
        iface = connect_interface_for_target(
            target,
            serial_factory=SerialInterface,
            tcp_factory=TCPInterface,
            serial_connect_now=False,
            tcp_connect_now=True,
        )
    except (SerialException, OSError, socket.error) as exc:
        print(connection_error_message(target, exc), file=sys.stderr)
        return 1

    try:
        if target.mode != "tcp":
            iface.connect()

        local_identity = find_local_identity(iface)
        peer_identity = resolve_peer(iface, args.peer)

        ack_packet: dict[str, object] = {}

        def on_response(packet: dict[str, object]) -> None:
            ack_packet.clear()
            ack_packet.update(packet)

        sent_packet = iface.sendText(
            message_text,
            peer_identity.node_id,
            wantAck=not args.no_wait_for_ack,
            channelIndex=args.channel_index,
            onResponse=None if args.no_wait_for_ack else on_response,
            portNum=portnums_pb2.PortNum.PRIVATE_APP,
        )

        status = "queued"
        if not args.no_wait_for_ack:
            deadline = time.monotonic() + max(args.ack_wait_seconds, 0.0)
            while time.monotonic() < deadline and not ack_packet:
                time.sleep(0.1)
            status = "ack" if ack_packet else "timeout"

        record = send_record(
            local_identity=local_identity,
            peer_identity=peer_identity,
            message=message_text,
            packet_id=sent_packet.get("id") if isinstance(sent_packet, dict) else None,
            channel_index=args.channel_index,
            status=status,
        )
        line = format_log_line(record)
        print(line)
        append_log_line(log_path, line)
        print(f"Logged to {log_path}", file=sys.stderr)
        return 0
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        iface.close()


def tail_log(args: argparse.Namespace) -> int:
    log_path = log_path_for_name(args.log_name, resolve_log_root(args.log_dir))
    try:
        lines = read_log_lines(log_path)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    for line in tail_lines(lines, args.lines):
        print(line)

    if args.follow:
        deadline = time.monotonic() + args.follow_seconds if args.follow_seconds > 0 else None
        try:
            follow_log(log_path, print, deadline=deadline)
        except KeyboardInterrupt:
            return 0
    return 0


def grep_log(args: argparse.Namespace) -> int:
    log_path = log_path_for_name(args.log_name, resolve_log_root(args.log_dir))
    try:
        lines = read_log_lines(log_path)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        matches = grep_lines(lines, args.pattern, ignore_case=args.ignore_case, regex=args.regex)
    except re.error as exc:
        print(f"Invalid regular expression: {exc}", file=sys.stderr)
        return 1

    if args.count:
        print(len(matches))
        return 0

    for line in matches:
        print(line)
    return 0


def stats_logs(args: argparse.Namespace) -> int:
    log_root = resolve_log_root(args.log_dir)
    if args.log_name:
        log_paths = [log_path_for_name(args.log_name, log_root)]
    else:
        log_paths = list_log_files(log_root)
        if not log_paths:
            print(f"No transcript logs found in {log_root}", file=sys.stderr)
            return 1

    try:
        summary = aggregate_log_records(log_paths)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print_stats_summary(summary)
    return 0


def prune_logs(args: argparse.Namespace) -> int:
    log_root = resolve_log_root(args.log_dir)
    older_than_seconds = max(args.days, 0.0) * 86400.0

    if args.dry_run:
        cutoff = time.time() - older_than_seconds
        removable = []
        for path in list_log_files(log_root):
            try:
                if path.stat().st_mtime < cutoff:
                    removable.append(path)
            except FileNotFoundError:
                continue
        for path in removable:
            print(path)
        return 0

    removed = prune_log_files(log_root, older_than_seconds)
    for path in removed:
        print(path)
    return 0


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "send":
        return send_private_message(args)
    if args.command == "sync":
        return MessageSync(args).run()
    if args.command == "tail":
        return tail_log(args)
    if args.command == "grep":
        return grep_log(args)
    if args.command == "stats":
        return stats_logs(args)
    if args.command == "prune":
        return prune_logs(args)
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
