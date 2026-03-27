#!/usr/bin/env python3
import argparse
import json
import socket
import subprocess
import sys
from pathlib import Path

from _meshtastic_common import (
    DEFAULT_SERIAL_PORT,
    DEFAULT_TCP_PORT,
    Palette,
    VENV_PYTHON,
    ensure_repo_python,
    interface_target,
    resolve_meshtastic_target,
    style,
)

ensure_repo_python("MESHTASTIC_STATUS_VENV_EXEC")

try:
    from google.protobuf.descriptor import FieldDescriptor
    from google.protobuf.json_format import MessageToDict
    from meshtastic.serial_interface import SerialInterface
    from meshtastic.tcp_interface import TCPInterface
    from serial.serialutil import SerialException
except ModuleNotFoundError as exc:
    missing_module = exc.name or "required dependency"
    print(
        f"meshtastic_status.py could not import {missing_module}. "
        f"Run ./setup/meshtastic-python.sh bootstrap first, or use ./setup/meshtastic-python.sh status ...",
        file=sys.stderr,
    )
    raise SystemExit(1)
PALETTE = Palette()


def heading(title: str) -> None:
    print(style(PALETTE, PALETTE.bold + PALETTE.cyan, title))


def kv(label: str, value: object) -> None:
    rendered = "-" if value in (None, "") else str(value)
    print(f"{style(PALETTE, PALETTE.dim, label + ':'):18} {rendered}")


def to_dict(message) -> dict:
    return MessageToDict(message) if message is not None else {}


def protobuf_to_plain(message) -> dict:
    result: dict[str, object] = {}
    for field in message.DESCRIPTOR.fields:
        value = getattr(message, field.name)
        if field.is_repeated:
            if field.type == FieldDescriptor.TYPE_MESSAGE:
                result[field.name] = [protobuf_to_plain(item) for item in value]
            elif field.type == FieldDescriptor.TYPE_ENUM:
                result[field.name] = [field.enum_type.values_by_number[int(item)].name for item in value]
            else:
                result[field.name] = list(value)
            continue

        if field.type == FieldDescriptor.TYPE_MESSAGE:
            nested = protobuf_to_plain(value)
            if nested:
                result[field.name] = nested
            continue

        if field.type == FieldDescriptor.TYPE_ENUM:
            result[field.name] = field.enum_type.values_by_number[int(value)].name
            continue

        result[field.name] = value
    return result


def enum_name(enum_descriptor, value: int) -> str:
    return enum_descriptor.values_by_number[int(value)].name


def config_scalar(section: dict, key: str, raw_section, raw_field: str):
    value = section.get(key)
    if value not in (None, ""):
        return value
    return getattr(raw_section, raw_field)


def config_enum(section: dict, key: str, raw_section, raw_field: str) -> str:
    value = section.get(key)
    if value not in (None, ""):
        return str(value)
    field = raw_section.DESCRIPTOR.fields_by_name[raw_field]
    return enum_name(field.enum_type, getattr(raw_section, raw_field))


def format_fixed_position(local_node: dict, fixed_position: object) -> str:
    if not fixed_position:
        return "disabled"

    position = local_node.get("position", {})
    latitude = position.get("latitude")
    longitude = position.get("longitude")
    altitude = position.get("altitude")
    if latitude is None or longitude is None:
        return "enabled"

    rendered = f"enabled ({latitude:.5f}, {longitude:.5f}"
    if altitude is not None:
        rendered += f", {altitude} m"
    return rendered + ")"


def find_local_node(iface) -> dict:
    return next((node for node in iface.nodes.values() if node.get("num") == iface.myInfo.my_node_num), {})


def render_summary(iface) -> None:
    local_node = find_local_node(iface)
    user = local_node.get("user", {})
    metrics = local_node.get("deviceMetrics", {})
    metadata = to_dict(iface.metadata)
    my_info = to_dict(iface.myInfo)
    local_config = to_dict(iface.localNode.localConfig)
    local_config_raw = iface.localNode.localConfig
    device = local_config.get("device", {})
    lora = local_config.get("lora", {})
    network = local_config.get("network", {})
    position = local_config.get("position", {})
    bluetooth = local_config.get("bluetooth", {})
    preset_value = config_enum(lora, "modemPreset", local_config_raw.lora, "modem_preset")
    role_value = config_enum(device, "role", local_config_raw.device, "role")
    fixed_position = config_scalar(position, "fixedPosition", local_config_raw.position, "fixed_position")

    heading("Meshtastic Summary")
    kv("Target", interface_target(iface))
    kv("Node ID", user.get("id"))
    kv("Long name", user.get("longName"))
    kv("Short name", user.get("shortName"))
    kv("Node number", local_node.get("num"))
    kv("Device role", role_value)
    kv("Hardware", metadata.get("hwModel") or user.get("hwModel"))
    kv("Firmware", metadata.get("firmwareVersion"))
    kv("PIO env", my_info.get("pioEnv"))
    kv("Reboot count", my_info.get("rebootCount"))
    kv("Battery", metrics.get("batteryLevel"))
    kv("Voltage", metrics.get("voltage"))
    kv("Uptime seconds", metrics.get("uptimeSeconds"))
    kv("Fixed position", format_fixed_position(local_node, fixed_position))
    kv("Region", lora.get("region"))
    kv("Preset", preset_value)
    kv("Tx power", config_scalar(lora, "txPower", local_config_raw.lora, "tx_power"))
    kv("WiFi enabled", config_scalar(network, "wifiEnabled", local_config_raw.network, "wifi_enabled"))
    kv("WiFi SSID", config_scalar(network, "wifiSsid", local_config_raw.network, "wifi_ssid"))
    kv("Bluetooth enabled", config_scalar(bluetooth, "enabled", local_config_raw.bluetooth, "enabled"))
    kv("Primary channel URL", iface.localNode.getURL())


def render_config(iface, sections: list[str]) -> None:
    local_config = protobuf_to_plain(iface.localNode.localConfig)
    module_config = protobuf_to_plain(iface.localNode.moduleConfig)
    combined = {"local": local_config, "module": module_config}

    heading("Configuration")
    if not sections:
        print(json.dumps(combined, indent=2, sort_keys=True))
        return

    selected: dict[str, dict] = {}
    for section in sections:
        if section in local_config:
            selected[section] = local_config[section]
        elif section in module_config:
            selected[section] = module_config[section]
        else:
            selected[section] = {"error": "unknown section"}
    print(json.dumps(selected, indent=2, sort_keys=True))


def render_nodes(iface) -> None:
    heading("Known Nodes")
    header = f"{'ID':<12} {'Long Name':<24} {'Short':<8} {'Model':<14} {'Bat%':>5} {'Volt':>6} {'Uptime':>8}"
    print(style(PALETTE, PALETTE.bold, header))
    for node in sorted(iface.nodes.values(), key=lambda item: item.get("num", 0)):
        user = node.get("user", {})
        metrics = node.get("deviceMetrics", {})
        print(
            f"{user.get('id', '-'):12} "
            f"{user.get('longName', '-'):24.24} "
            f"{user.get('shortName', '-'):8.8} "
            f"{user.get('hwModel', '-'):14.14} "
            f"{str(metrics.get('batteryLevel', '-')):>5} "
            f"{str(metrics.get('voltage', '-')):>6} "
            f"{str(metrics.get('uptimeSeconds', '-')):>8}"
        )


def run_cli(args: list[str]) -> int:
    python_exe = str(VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable))
    command = [python_exe, "-m", "meshtastic", *args]
    result = subprocess.run(command, check=False)
    return result.returncode


def render_raw_info(target) -> int:
    heading("Raw Meshtastic Info")
    if target.mode == "tcp":
        if target.tcp_port != DEFAULT_TCP_PORT:
            print("Custom TCP ports are not supported by the Meshtastic CLI wrapper for raw-info.", file=sys.stderr)
            return 1
        return run_cli(["--host", target.host, "--info"])
    return run_cli(["--port", target.serial_port, "--info"])


def render_traceroute(target, dest: str) -> int:
    heading(f"Traceroute to {dest}")
    if target.mode == "tcp":
        if target.tcp_port != DEFAULT_TCP_PORT:
            print("Custom TCP ports are not supported by the Meshtastic CLI wrapper for traceroute.", file=sys.stderr)
            return 1
        return run_cli(["--host", target.host, "--traceroute", dest, "--timeout", "60", "--no-nodes"])
    return run_cli(["--port", target.serial_port, "--traceroute", dest, "--timeout", "60", "--no-nodes"])


def connect_interface(target):
    if target.mode == "tcp":
        return TCPInterface(target.host, portNumber=target.tcp_port)
    return SerialInterface(target.serial_port)


def connection_error_message(target, exc: Exception) -> str:
    if target.mode == "tcp":
        return f"Could not connect to {target.host}:{target.tcp_port}: {exc}."
    return (
        f"Could not open {target.serial_port}: {exc}. "
        "Another process is probably using the Meshtastic serial port."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pretty Meshtastic status tool")
    parser.add_argument("--port", default=DEFAULT_SERIAL_PORT, help="Serial port for direct device access if no proxy or --host is used")
    parser.add_argument("--host", default="", help="TCP host for a Meshtastic proxy or network-connected node; if omitted, a healthy local proxy is auto-detected")
    parser.add_argument("--tcp-port", type=int, default=DEFAULT_TCP_PORT, help="TCP port for --host mode or MESHTASTIC_HOST")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("summary", help="Show a concise summary of the local node")

    config_parser = subparsers.add_parser("config", help="Show full or selected config sections")
    config_parser.add_argument("sections", nargs="*", help="Optional config sections such as lora, network, telemetry")

    subparsers.add_parser("nodes", help="Show known nodes in a compact table")
    subparsers.add_parser("raw-info", help="Show raw CLI info output")

    traceroute_parser = subparsers.add_parser("traceroute", help="Run a traceroute via Meshtastic CLI")
    traceroute_parser.add_argument("dest", help="Destination node ID, e.g. !0438ca24")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    command = args.command or "summary"
    target = resolve_meshtastic_target(args.port, args.host, args.tcp_port)

    if command == "raw-info":
        return render_raw_info(target)
    if command == "traceroute":
        return render_traceroute(target, args.dest)

    try:
        iface = connect_interface(target)
    except (SerialException, OSError, socket.error) as exc:
        print(connection_error_message(target, exc), file=sys.stderr)
        return 1
    try:
        if command == "summary":
            render_summary(iface)
        elif command == "config":
            render_config(iface, args.sections)
        elif command == "nodes":
            render_nodes(iface)
        else:
            parser.error(f"Unknown command: {command}")
    finally:
        iface.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())