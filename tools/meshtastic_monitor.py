#!/usr/bin/env python3
import argparse
import fnmatch
import json
import socket
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

from meshtastic_messages import lookup_identity

from _meshtastic_common import (
    DEFAULT_SERIAL_PORT,
    DEFAULT_TCP_PORT,
    Palette,
    connect_interface_for_target,
    connection_error_message,
    ensure_repo_python,
    interface_target,
    resolve_meshtastic_target,
    strip_raw,
    style,
)

ensure_repo_python("MESHTASTIC_MONITOR_VENV_EXEC")

try:
    from pubsub import pub
    from meshtastic.serial_interface import SerialInterface
    from meshtastic.tcp_interface import TCPInterface
    from serial.serialutil import SerialException
except ModuleNotFoundError as exc:
    missing_module = exc.name or "required dependency"
    print(
        f"meshtastic_monitor.py could not import {missing_module}. "
        f"Run ./setup/meshtastic-python.sh bootstrap first, or use ./setup/meshtastic-python.sh monitor ...",
        file=sys.stderr,
    )
    raise SystemExit(1)
PALETTE = Palette()
ORANGE = "\033[38;5;208m" if PALETTE.reset else ""
MESSAGE_TYPE_WIDTH = 18


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def packet_sender_label(packet: dict, iface=None) -> str:
    from_id = str(packet.get("fromId") or "")
    if iface is not None:
        identity = lookup_identity(iface, node_num=packet.get("from"), node_id=from_id)
        if identity.short_name:
            return identity.short_name
    if from_id:
        return from_id
    node_num = packet.get("from")
    if node_num is not None:
        return f"#{node_num}"
    return "-"


def display_topic_name(topic_name: str) -> str:
    if topic_name.startswith("meshtastic."):
        return topic_name[len("meshtastic.") :]
    return topic_name


def packet_sender_column(packet: dict, iface=None) -> str:
    if iface is not None:
        identity = lookup_identity(iface, node_num=packet.get("from"), node_id=str(packet.get("fromId") or ""))
        if identity.short_name:
            return identity.short_name[:5].ljust(5)
    return packet_sender_label(packet)


def sender_column(topic_name: str, kwargs: dict) -> str:
    if not topic_name.startswith("meshtastic.receive"):
        return " " * 5
    packet = kwargs.get("packet", {})
    if not isinstance(packet, dict):
        return " " * 5
    return packet_sender_column(packet, kwargs.get("interface"))


def packet_preview(packet: dict, iface=None) -> str:
    decoded = packet.get("decoded", {})
    for key in ("text", "position", "user", "telemetry", "routing", "neighborInfo"):
        if key in decoded:
            value = strip_raw(decoded[key])
            return f"{key}={json.dumps(value, sort_keys=True)}"

    if decoded.get("portnum") == "TEXT_MESSAGE_APP":
        payload = decoded.get("payload")
        if isinstance(payload, bytes):
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError:
                text = None
            if text is not None:
                return f"text={json.dumps(text)}"

    if "portnum" in decoded:
        payload = decoded.get("payload")
        if isinstance(payload, bytes):
            payload_repr = f"<{len(payload)} bytes>"
        else:
            payload_repr = json.dumps(strip_raw(payload))
        return f"portnum={decoded['portnum']} payload={payload_repr}"

    summary = {
        "from": packet_sender_label(packet, iface),
        "fromId": packet.get("fromId"),
        "toId": packet.get("toId"),
        "channel": packet.get("channel"),
        "rxSnr": packet.get("rxSnr"),
        "rxRssi": packet.get("rxRssi"),
        "hopLimit": packet.get("hopLimit"),
    }
    compact = {key: value for key, value in summary.items() if value is not None}
    return json.dumps(compact, sort_keys=True)


def topic_color(topic_name: str) -> str:
    if topic_name.startswith("meshtastic.connection"):
        return PALETTE.green
    if topic_name.startswith("meshtastic.receive"):
        return PALETTE.cyan
    if topic_name.startswith("meshtastic.node"):
        return PALETTE.yellow
    if topic_name.startswith("meshtastic.log"):
        return PALETTE.magenta
    return PALETTE.blue


def parse_filters(raw_value: str) -> set[str]:
    if not raw_value:
        return set()
    return {item.strip().lower() for item in raw_value.split(",") if item.strip()}


def topic_tags(topic_name: str, kwargs: dict) -> set[str]:
    tags = {topic_name.lower()}
    if topic_name.startswith("meshtastic."):
        suffix = topic_name[len("meshtastic.") :].lower()
        tags.add(suffix)
        parts = suffix.split(".")
        if parts:
            tags.add(parts[0])
        for index in range(1, len(parts) + 1):
            tags.add(".".join(parts[:index]))

    if topic_name.startswith("meshtastic.receive"):
        tags.add("receive")
        decoded = kwargs.get("packet", {}).get("decoded", {}) if isinstance(kwargs.get("packet"), dict) else {}
        for key in ("text", "position", "user", "telemetry", "routing", "neighborInfo"):
            if key in decoded:
                tags.add(key.lower())
                tags.add(f"receive.{key.lower()}")
        portnum = decoded.get("portnum")
        if portnum:
            lowered = str(portnum).lower()
            tags.add(lowered)
            tags.add(f"receive.{lowered}")
            if lowered == "text_message_app":
                tags.add("text")
                tags.add("receive.text")

    return tags


def filter_matches(filters: set[str], topic_name: str, kwargs: dict) -> bool:
    if not filters:
        return True
    tags = topic_tags(topic_name, kwargs)
    for pattern in filters:
        if any(fnmatch.fnmatch(tag, pattern) for tag in tags):
            return True
    return False


def event_summary(topic_name: str, kwargs: dict) -> str:
    if topic_name == "meshtastic.connection.established":
        interface = kwargs.get("interface")
        return f"connected target={interface_target(interface)}"

    if topic_name == "meshtastic.connection.lost":
        interface = kwargs.get("interface")
        return f"lost target={interface_target(interface)}"

    if topic_name == "meshtastic.node.updated":
        node = kwargs.get("node", {})
        user = node.get("user", {})
        return (
            f"node={user.get('id', '-')} long={user.get('longName', '-')} "
            f"short={user.get('shortName', '-')}"
        )

    if topic_name == "meshtastic.log.line":
        return kwargs.get("line", "")

    if topic_name.startswith("meshtastic.receive"):
        packet = kwargs.get("packet", {})
        return packet_preview(packet, kwargs.get("interface"))

    cleaned = {key: strip_raw(value) for key, value in kwargs.items() if key != "interface"}
    return json.dumps(cleaned, sort_keys=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Continuous Meshtastic event monitor")
    parser.add_argument("--port", default=DEFAULT_SERIAL_PORT, help="Serial port for direct device access if no proxy or --host is used")
    parser.add_argument("--host", default="", help="TCP host for a Meshtastic proxy or network-connected node; if omitted, a healthy local proxy is auto-detected")
    parser.add_argument("--tcp-port", type=int, default=DEFAULT_TCP_PORT, help="TCP port for --host mode or MESHTASTIC_HOST")
    parser.add_argument(
        "--topic-prefix",
        default="meshtastic",
        help="Only print topics starting with this prefix (default: meshtastic)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit newline-delimited JSON events instead of ANSI-colored text",
    )
    parser.add_argument(
        "--include-log-lines",
        action="store_true",
        help="Include meshtastic.log.line events in the output",
    )
    parser.add_argument(
        "--only",
        default="",
        help="Comma-separated event filters to include, e.g. connection,node,receive.text,position",
    )
    parser.add_argument(
        "--exclude",
        default="",
        help="Comma-separated event filters to suppress after inclusion filtering, e.g. log,receive.routing",
    )
    parser.add_argument(
        "--log-file",
        default="",
        help="Optional file to append monitor output to while printing to stdout",
    )
    return parser


class Monitor:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.target = resolve_meshtastic_target(args.port, args.host, args.tcp_port)
        self.stop_requested = False
        self.stop_event = Event()
        self.interface = None
        self.include_filters = parse_filters(args.only)
        self.exclude_filters = parse_filters(args.exclude)
        self.log_handle = None

    def request_stop(self, _signum=None, _frame=None) -> None:
        self.stop_requested = True
        self.stop_event.set()
        interface = self.interface
        if interface is not None:
            try:
                interface.close()
            except Exception:
                pass

    def should_emit(self, topic_name: str, kwargs: dict) -> bool:
        if self.args.topic_prefix and not topic_name.startswith(self.args.topic_prefix):
            return False
        if topic_name == "meshtastic.log.line" and not self.args.include_log_lines:
            return False
        if not filter_matches(self.include_filters, topic_name, kwargs):
            return False
        if self.exclude_filters and filter_matches(self.exclude_filters, topic_name, kwargs):
            return False
        return True

    def write_log_line(self, line: str) -> None:
        if self.log_handle is not None:
            self.log_handle.write(f"{line}\n")
            self.log_handle.flush()

    def emit(self, topic_name: str, kwargs: dict) -> None:
        if not self.should_emit(topic_name, kwargs):
            return

        timestamp = utc_timestamp()
        summary = event_summary(topic_name, kwargs)
        if self.args.json:
            payload = {
                "ts": timestamp,
                "topic": topic_name,
                "summary": summary,
            }
            if "packet" in kwargs:
                payload["packet"] = strip_raw(kwargs["packet"])
            if "node" in kwargs:
                payload["node"] = strip_raw(kwargs["node"])
            line = json.dumps(payload, sort_keys=True)
            print(line, flush=True)
            self.write_log_line(line)
            return

        topic_label = display_topic_name(topic_name)
        topic_cell = f"{topic_label:<{MESSAGE_TYPE_WIDTH}}"
        sender = sender_column(topic_name, kwargs)
        line = f"{timestamp}  {topic_cell}  {sender}  {summary}"
        topic_text = style(PALETTE, topic_color(topic_name) + PALETTE.bold, topic_cell)
        sender_text = style(PALETTE, ORANGE + PALETTE.bold, sender)
        print(
            f"{style(PALETTE, PALETTE.dim, timestamp)}  {topic_text}  {sender_text}  {summary}",
            flush=True,
        )
        self.write_log_line(line)

    def on_event(self, topic=pub.AUTO_TOPIC, **kwargs) -> None:
        self.emit(topic.getName(), kwargs)

    def target_label(self) -> str:
        return self.target.label

    def connect_interface(self):
        return connect_interface_for_target(
            self.target,
            serial_factory=SerialInterface,
            tcp_factory=TCPInterface,
            serial_connect_now=False,
            tcp_connect_now=True,
        )

    def run(self) -> int:
        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGTERM, self.request_stop)

        pub.subscribe(self.on_event, pub.ALL_TOPICS)
        try:
            if self.args.log_file:
                log_path = Path(self.args.log_file)
                log_path.parent.mkdir(parents=True, exist_ok=True)
                self.log_handle = log_path.open("a", encoding="utf-8")
            try:
                self.interface = self.connect_interface()
            except (SerialException, OSError, socket.error) as exc:
                print(connection_error_message(self.target, exc), file=sys.stderr)
                return 1
            if self.target.mode != "tcp":
                self.interface.connect()

            if not self.args.json:
                print(
                    f"{style(PALETTE, PALETTE.bold + PALETTE.green, 'Monitoring Meshtastic events')} "
                    f"{style(PALETTE, PALETTE.dim, f'on {self.target_label()} (Ctrl-C to stop)')}",
                    flush=True,
                )

            while not self.stop_event.wait(0.25):
                continue
        finally:
            pub.unsubscribe(self.on_event, pub.ALL_TOPICS)
            if self.interface is not None:
                try:
                    self.interface.close()
                except Exception:
                    pass
            if self.log_handle is not None:
                self.log_handle.close()
        return 0


def main() -> int:
    args = build_parser().parse_args()
    return Monitor(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
