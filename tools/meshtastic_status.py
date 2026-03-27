#!/usr/bin/env python3
import argparse
import json
import socket
import subprocess
import sys
from statistics import mean
from pathlib import Path
from threading import Event

from _meshtastic_common import (
    DEFAULT_SERIAL_PORT,
    DEFAULT_TCP_PORT,
    Palette,
    VENV_PYTHON,
    connect_interface_for_target,
    connection_error_message,
    ensure_repo_python,
    interface_target,
    resolve_meshtastic_target,
    style,
)

ensure_repo_python("MESHTASTIC_STATUS_VENV_EXEC")

try:
    from google.protobuf.descriptor import FieldDescriptor
    from google.protobuf.json_format import MessageToDict
    from meshtastic.protobuf import portnums_pb2, telemetry_pb2
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


def collect_neighbor_rows(iface) -> list[dict[str, object]]:
    local_num = iface.myInfo.my_node_num
    rows: list[dict[str, object]] = []

    for node in iface.nodes.values():
        if node.get("num") == local_num:
            continue

        snr = node.get("snr")
        try:
            snr_value = float(snr)
        except (TypeError, ValueError):
            continue

        user = node.get("user", {})
        rows.append(
            {
                "id": user.get("id", "-"),
                "name": user.get("shortName") or user.get("longName") or "-",
                "snr": snr_value,
                "hops": node.get("hopsAway"),
            }
        )

    rows.sort(key=lambda item: item["snr"], reverse=True)
    return rows


def render_neighbors(iface) -> None:
    rows = collect_neighbor_rows(iface)
    direct = [row for row in rows if row.get("hops") == 0]

    heading("Neighbor Signals")
    kv("Neighbors with SNR", len(rows))
    kv("Direct neighbors", len(direct))
    if rows:
        kv("Average SNR", f"{mean(row['snr'] for row in rows):.2f} dB")
        kv("Top 5 avg SNR", f"{mean(row['snr'] for row in rows[:5]):.2f} dB")
    if direct:
        kv("Direct avg SNR", f"{mean(row['snr'] for row in direct):.2f} dB")

    header = f"{'ID':<12} {'Name':<12} {'SNR':>8} {'Hops':>6}"
    print(style(PALETTE, PALETTE.bold, header))
    for row in rows[:15]:
        hops = row.get("hops")
        hops_display = "-" if hops is None else str(hops)
        print(f"{row['id']:<12} {str(row['name']):12.12} {row['snr']:>8.2f} {hops_display:>6}")


TELEMETRY_TYPE_MAP = {
    "device": "device_metrics",
    "environment": "environment_metrics",
    "air-quality": "air_quality_metrics",
    "power": "power_metrics",
    "local-stats": "local_stats",
}

TELEMETRY_FIELD_MAP = {
    "device_metrics": "deviceMetrics",
    "environment_metrics": "environmentMetrics",
    "air_quality_metrics": "airQualityMetrics",
    "power_metrics": "powerMetrics",
    "local_stats": "localStats",
}


def collect_proximity_candidates(iface, include_multihop: bool = False) -> list[dict[str, object]]:
    local_num = iface.myInfo.my_node_num
    candidates: list[dict[str, object]] = []

    for node in iface.nodes.values():
        if node.get("num") == local_num:
            continue

        user = node.get("user", {})
        hops = node.get("hopsAway")
        snr = node.get("snr")
        try:
            snr_value = float(snr) if snr is not None else None
        except (TypeError, ValueError):
            snr_value = None

        is_direct = hops == 0
        if not include_multihop and not is_direct:
            continue

        candidates.append(
            {
                "id": user.get("id", "-"),
                "name": user.get("shortName") or user.get("longName") or user.get("id") or "-",
                "node_num": node.get("num"),
                "snr": snr_value,
                "hops": hops,
                "is_direct": is_direct,
            }
        )

    candidates.sort(
        key=lambda item: (
            0 if item["is_direct"] else 1,
            0 if item["snr"] is not None else 1,
            -(item["snr"] or float("-inf")) if item["snr"] is not None else 0.0,
            item["hops"] if isinstance(item["hops"], int) else 999,
            str(item["name"]),
        )
    )
    return candidates


def _build_telemetry_request(telemetry_type: str):
    request = telemetry_pb2.Telemetry()
    if telemetry_type == "environment_metrics":
        request.environment_metrics.CopyFrom(telemetry_pb2.EnvironmentMetrics())
    elif telemetry_type == "air_quality_metrics":
        request.air_quality_metrics.CopyFrom(telemetry_pb2.AirQualityMetrics())
    elif telemetry_type == "power_metrics":
        request.power_metrics.CopyFrom(telemetry_pb2.PowerMetrics())
    elif telemetry_type == "local_stats":
        request.local_stats.CopyFrom(telemetry_pb2.LocalStats())
    else:
        request.device_metrics.CopyFrom(telemetry_pb2.DeviceMetrics())
    return request


def request_telemetry_from_node(iface, node_id: str, telemetry_type: str, timeout_seconds: float) -> dict[str, object]:
    completed = Event()
    response: dict[str, object] = {}

    def on_response(packet: dict[str, object]) -> None:
        response.clear()
        response.update(packet)
        completed.set()

    iface.sendData(
        _build_telemetry_request(telemetry_type),
        destinationId=node_id,
        portNum=portnums_pb2.PortNum.TELEMETRY_APP,
        wantResponse=True,
        onResponse=on_response,
    )

    if not completed.wait(timeout=max(timeout_seconds, 0.0)):
        return {"status": "timeout"}

    decoded = response.get("decoded", {})
    if not isinstance(decoded, dict):
        return {"status": "invalid-response"}

    if decoded.get("portnum") == "ROUTING_APP":
        routing = decoded.get("routing", {})
        reason = routing.get("errorReason") if isinstance(routing, dict) else None
        return {"status": "routing-error", "reason": reason or "unknown"}

    telemetry = decoded.get("telemetry")
    if not isinstance(telemetry, dict):
        return {"status": "unexpected-response"}

    return {
        "status": "ok",
        "telemetry": telemetry,
        "packet": response,
    }


def collect_cached_telemetry_candidates(iface, telemetry_type: str, include_multihop: bool = False) -> list[dict[str, object]]:
    telemetry_field = TELEMETRY_FIELD_MAP[telemetry_type]
    candidates = collect_proximity_candidates(iface, include_multihop=include_multihop)
    return [candidate for candidate in candidates if cached_telemetry_for_node(iface, str(candidate["id"]), telemetry_type)]


def cached_telemetry_for_node(iface, node_id: str, telemetry_type: str) -> dict[str, object] | None:
    telemetry_field = TELEMETRY_FIELD_MAP[telemetry_type]
    for node in iface.nodes.values():
        if node.get("user", {}).get("id") == node_id:
            telemetry = node.get(telemetry_field)
            return telemetry if isinstance(telemetry, dict) else None
    return None


def render_telemetry(iface, telemetry_mode: str, telemetry_type: str, limit: int, include_multihop: bool, timeout_seconds: float, json_output: bool) -> int:
    if telemetry_mode == "cached":
        candidates = collect_cached_telemetry_candidates(iface, telemetry_type, include_multihop=include_multihop)
    else:
        candidates = collect_proximity_candidates(iface, include_multihop=include_multihop)
    if not candidates and not include_multihop:
        if telemetry_mode == "cached":
            candidates = collect_cached_telemetry_candidates(iface, telemetry_type, include_multihop=True)
        else:
            candidates = collect_proximity_candidates(iface, include_multihop=True)

    if limit > 0:
        candidates = candidates[:limit]

    if not candidates:
        if telemetry_mode == "cached":
            print("No nearby nodes have cached telemetry of the requested type.", file=sys.stderr)
        else:
            print("No nearby nodes found with enough routing metadata to request telemetry.", file=sys.stderr)
        return 1

    results: list[dict[str, object]] = []
    for candidate in candidates:
        if telemetry_mode == "cached":
            telemetry = cached_telemetry_for_node(iface, str(candidate["id"]), telemetry_type)
            result = {"status": "ok", "telemetry": {TELEMETRY_FIELD_MAP[telemetry_type]: telemetry}} if telemetry else {"status": "missing"}
        else:
            result = request_telemetry_from_node(iface, str(candidate["id"]), telemetry_type, timeout_seconds)
        results.append({"node": candidate, **result})

    if json_output:
        payload = {
            "telemetry_mode": telemetry_mode,
            "telemetry_type": telemetry_type,
            "include_multihop": include_multihop,
            "results": results,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    heading(f"Telemetry {telemetry_mode.title()} ({telemetry_type})")
    kv("Candidates queried", len(results))
    kv("Selection", "direct neighbors first" + (", then multihop" if include_multihop else ""))
    for item in results:
        node = item["node"]
        label = f"{node['id']} ({node['name']})"
        hops = node.get("hops")
        snr = node.get("snr")
        proximity = []
        proximity.append("direct" if node.get("is_direct") else f"hops={hops if hops is not None else '-'}")
        if snr is not None:
            proximity.append(f"snr={snr:.2f} dB")
        print()
        print(style(PALETTE, PALETTE.bold + PALETTE.cyan, label))
        print(style(PALETTE, PALETTE.dim, ", ".join(proximity)))
        if item["status"] == "ok":
            telemetry = item.get("telemetry", {})
            print(json.dumps(telemetry, indent=2, sort_keys=True))
        elif item["status"] == "routing-error":
            print(f"routing-error: {item.get('reason', 'unknown')}")
        else:
            print(item["status"])
    return 0


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
    subparsers.add_parser("neighbors", help="Show neighbor signal quality without crashing on incomplete node records")
    telemetry_parser = subparsers.add_parser("telemetry", help="Request telemetry from the closest known nodes")
    telemetry_parser.add_argument(
        "mode",
        nargs="?",
        choices=("active", "cached"),
        default="active",
        help="Use active request/response polling or only print cached telemetry already learned by this node",
    )
    telemetry_parser.add_argument(
        "--type",
        choices=tuple(TELEMETRY_TYPE_MAP),
        default="environment",
        help="Telemetry payload to request (default: environment)",
    )
    telemetry_parser.add_argument("--limit", type=int, default=3, help="Maximum number of nearby nodes to query")
    telemetry_parser.add_argument(
        "--include-multihop",
        action="store_true",
        help="After direct neighbors, also query multihop nodes ordered by proximity signals",
    )
    telemetry_parser.add_argument("--timeout", type=float, default=10.0, help="Seconds to wait per node for a telemetry response")
    telemetry_parser.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable output")
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
        iface = connect_interface_for_target(target, serial_factory=SerialInterface, tcp_factory=TCPInterface)
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
        elif command == "neighbors":
            render_neighbors(iface)
        elif command == "telemetry":
            return render_telemetry(
                iface,
                telemetry_mode=args.mode,
                telemetry_type=TELEMETRY_TYPE_MAP[args.type],
                limit=max(args.limit, 0),
                include_multihop=args.include_multihop,
                timeout_seconds=args.timeout,
                json_output=args.json,
            )
        else:
            parser.error(f"Unknown command: {command}")
    finally:
        iface.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
