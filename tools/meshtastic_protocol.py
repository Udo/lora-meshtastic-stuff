#!/usr/bin/env python3
import argparse
import json
import signal
import socket
import sys
import time

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
from meshtastic_messages import (
    append_log_line,
    format_log_line,
    log_path_for_name,
    packet_scope,
    packet_text,
    packet_timestamp,
    resolve_log_root,
    lookup_identity,
)

ensure_repo_python("MESHTASTIC_PROTOCOL_VENV_EXEC")

try:
    from meshtastic.serial_interface import SerialInterface
    from meshtastic.tcp_interface import TCPInterface
    from pubsub import pub
    from serial.serialutil import SerialException
except ModuleNotFoundError as exc:
    missing_module = exc.name or "required dependency"
    print(
        f"meshtastic_protocol.py could not import {missing_module}. "
        "Run ./setup/meshtastic-python.sh bootstrap first, or use ./setup/meshtastic-python.sh protocol ...",
        file=sys.stderr,
    )
    raise SystemExit(1)

PALETTE = Palette()
DEFAULT_LOG_NAME = "protocol"


def event_kind_from_decoded(decoded: dict[str, object]) -> str:
    for key, kind in (
        ("text", "message"),
        ("telemetry", "telemetry"),
        ("position", "position"),
        ("user", "node-info"),
        ("routing", "routing"),
        ("neighborInfo", "neighbor-info"),
        ("admin", "admin"),
    ):
        if key in decoded:
            return kind
    portnum = decoded.get("portnum")
    return str(portnum).lower() if portnum else "packet"


def telemetry_variant(decoded: dict[str, object]) -> str:
    telemetry = decoded.get("telemetry", {})
    if not isinstance(telemetry, dict):
        return ""
    for key in telemetry:
        if key != "time":
            return key
    return ""


def packet_summary(packet: dict[str, object]) -> str:
    decoded = packet.get("decoded", {})
    if not isinstance(decoded, dict):
        return json.dumps(strip_raw(packet), sort_keys=True, separators=(",", ":"))

    for key in ("text", "telemetry", "position", "user", "routing", "neighborInfo", "admin"):
        if key in decoded:
            return json.dumps(strip_raw(decoded[key]), sort_keys=True, separators=(",", ":"))

    payload = decoded.get("payload")
    if payload is not None:
        return json.dumps(strip_raw(payload), sort_keys=True, separators=(",", ":"))
    return json.dumps(strip_raw(decoded), sort_keys=True, separators=(",", ":"))


def record_from_packet(packet: dict[str, object], iface) -> dict[str, object]:
    decoded = packet.get("decoded", {})
    if not isinstance(decoded, dict):
        decoded = {}

    from_identity = lookup_identity(iface, node_num=packet.get("from"), node_id=str(packet.get("fromId") or ""))
    to_identity = lookup_identity(iface, node_num=packet.get("to"), node_id=str(packet.get("toId") or ""))
    scope = packet_scope(packet) or "protocol"
    text = packet_text(packet)

    record: dict[str, object] = {
        "ts": packet_timestamp(packet),
        "dir": "rx",
        "scope": scope,
        "event": "packet",
        "kind": event_kind_from_decoded(decoded),
        "topic": "meshtastic.receive",
        "from_id": from_identity.node_id,
        "from_short": from_identity.short_name,
        "from_name": from_identity.long_name,
        "to_id": to_identity.node_id,
        "to_short": to_identity.short_name,
        "to_name": to_identity.long_name,
        "channel": packet.get("channel"),
        "packet_id": packet.get("id"),
        "portnum": decoded.get("portnum"),
        "rx_snr": packet.get("rxSnr"),
        "rx_rssi": packet.get("rxRssi"),
        "hop_limit": packet.get("hopLimit"),
        "summary": packet_summary(packet),
    }
    if text:
        record["text"] = text
    telemetry_type = telemetry_variant(decoded)
    if telemetry_type:
        record["telemetry_type"] = telemetry_type
    return record


def record_from_topic(topic_name: str, kwargs: dict, iface) -> dict[str, object] | None:
    if topic_name.startswith("meshtastic.receive"):
        packet = kwargs.get("packet")
        if isinstance(packet, dict):
            return record_from_packet(packet, iface)
        return None

    if topic_name == "meshtastic.connection.established":
        interface = kwargs.get("interface")
        return {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "dir": "meta",
            "scope": "housekeeping",
            "event": "connection",
            "status": "established",
            "topic": topic_name,
            "target": interface_target(interface),
        }

    if topic_name == "meshtastic.connection.lost":
        interface = kwargs.get("interface")
        return {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "dir": "meta",
            "scope": "housekeeping",
            "event": "connection",
            "status": "lost",
            "topic": topic_name,
            "target": interface_target(interface),
        }

    if topic_name == "meshtastic.node.updated":
        node = kwargs.get("node", {})
        if not isinstance(node, dict):
            node = {}
        user = node.get("user", {}) if isinstance(node.get("user"), dict) else {}
        return {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "dir": "meta",
            "scope": "housekeeping",
            "event": "node-update",
            "topic": topic_name,
            "node_id": user.get("id", "-"),
            "node_short": user.get("shortName", ""),
            "node_name": user.get("longName", ""),
            "summary": json.dumps(strip_raw(node), sort_keys=True, separators=(",", ":")),
        }

    if topic_name == "meshtastic.log.line":
        return {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "dir": "meta",
            "scope": "housekeeping",
            "event": "log-line",
            "topic": topic_name,
            "text": kwargs.get("line", ""),
        }

    if topic_name.startswith("meshtastic."):
        cleaned = {key: strip_raw(value) for key, value in kwargs.items() if key != "interface"}
        return {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "dir": "meta",
            "scope": "housekeeping",
            "event": "topic",
            "topic": topic_name,
            "summary": json.dumps(cleaned, sort_keys=True, separators=(",", ":")),
        }
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Continuous Meshtastic protocol logger")
    parser.add_argument("--port", default=DEFAULT_SERIAL_PORT, help="Serial port for direct device access if no proxy or --host is used")
    parser.add_argument("--host", default="", help="TCP host for a Meshtastic proxy or network-connected node; if omitted, a healthy local proxy is auto-detected")
    parser.add_argument("--tcp-port", type=int, default=DEFAULT_TCP_PORT, help="TCP port for --host mode or MESHTASTIC_HOST")
    parser.add_argument("--log-dir", default="", help="Override the transcript directory; defaults to MESHTASTIC_LOG_DIR or ~/.local/log/meshtastic")
    parser.add_argument("log_name", nargs="?", default=DEFAULT_LOG_NAME, help="Log file name under the transcript directory without the .log suffix")
    parser.add_argument("--timeout", type=float, default=0.0, help="Optional number of seconds to run before exiting; 0 means forever")
    parser.add_argument("--quiet", action="store_true", help="Write only to the log file and suppress stdout")
    parser.add_argument("--include-log-lines", action="store_true", help="Also persist meshtastic.log.line events")
    return parser


class ProtocolLogger:
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
        if not self.args.quiet:
            print(line, flush=True)
        append_log_line(self.log_path, line)

    def on_event(self, topic=pub.AUTO_TOPIC, **kwargs) -> None:
        topic_name = topic.getName()
        if topic_name == "meshtastic.log.line" and not self.args.include_log_lines:
            return
        record = record_from_topic(topic_name, kwargs, self.interface)
        if record is not None:
            self.emit_record(record)

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

            if not self.args.quiet:
                header = style(PALETTE, PALETTE.bold + PALETTE.green, "Logging Meshtastic protocol")
                details = style(PALETTE, PALETTE.dim, f"from {interface_target(self.interface)} into {self.log_path} (Ctrl-C to stop)")
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


def main() -> int:
    args = build_parser().parse_args()
    return ProtocolLogger(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
