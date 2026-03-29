import argparse
import json
import socket
import threading
from pathlib import Path


ANNOUNCE_CONFIG = "config.json"
ANNOUNCE_STATE = "announce.json"
ANNOUNCEMENTS_LOG = "announcements.jsonl"
CONTROL_SOCKET = "ip_tunnel.sock"
STATUS_FILE = "status.json"
CLIENT_TTL_SECONDS = 30.0
DEFAULT_ANNOUNCE_ENABLED = True
DEFAULT_ANNOUNCE_INTERVAL_SECS = 300
DEFAULT_ANNOUNCE_SECONDARY = False
GATEWAY_CONTROL_SCHEMA = "meshtastic.gateway.control"
GATEWAY_CONTROL_VERSION = 1
GATEWAY_ANNOUNCE_TYPE = "gateway_announce"
MAX_POLL_MESSAGES = 64

_CONFIG_DEFAULTS = {
    "announce_enabled": DEFAULT_ANNOUNCE_ENABLED,
    "announce_interval_secs": DEFAULT_ANNOUNCE_INTERVAL_SECS,
    "announce_secondary": DEFAULT_ANNOUNCE_SECONDARY,
}

_STATE_LOCK = threading.Lock()
_SERVER_SOCKET = None
_SERVER_SOCKET_PATH = None
_CLIENTS = {}


def _control_socket_path(api):
    return Path(api["plugin_store_path"](api["plugin_name"], CONTROL_SOCKET))


def _status_path(api):
    return Path(api["plugin_store_path"](api["plugin_name"], STATUS_FILE))


def _config_path(api):
    return Path(api["plugin_store_path"](api["plugin_name"], ANNOUNCE_CONFIG))


def _announce_state_path(api):
    return Path(api["plugin_store_path"](api["plugin_name"], ANNOUNCE_STATE))


def _announcements_path(api):
    return Path(api["plugin_store_path"](api["plugin_name"], ANNOUNCEMENTS_LOG))


def _write_json(path, payload):
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    temp_path.replace(path)


def _safe_read_json(path, api, *, context):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        api["logger"].warning("plugin %s skipped malformed %s %s: %s", api["plugin_name"], context, path, exc)
        return None


def _validated_bool(config, key, default, api, path):
    value = config.get(key, default)
    if isinstance(value, bool):
        return value
    api["logger"].warning("plugin %s skipped invalid %s in %s: %r", api["plugin_name"], key, path, value)
    return default


def _validated_positive_int(config, key, default, api, path):
    value = config.get(key, default)
    try:
        value = int(value)
    except (TypeError, ValueError):
        api["logger"].warning("plugin %s skipped invalid %s in %s: %r", api["plugin_name"], key, path, value)
        return default
    if value <= 0:
        api["logger"].warning("plugin %s skipped non-positive %s in %s: %r", api["plugin_name"], key, path, value)
        return default
    return value


def _load_config(api):
    path = _config_path(api)
    if not path.exists():
        return dict(_CONFIG_DEFAULTS)
    config = _safe_read_json(path, api, context="config")
    if not isinstance(config, dict):
        return dict(_CONFIG_DEFAULTS)
    return {
        "announce_enabled": _validated_bool(config, "announce_enabled", DEFAULT_ANNOUNCE_ENABLED, api, path),
        "announce_interval_secs": _validated_positive_int(config, "announce_interval_secs", DEFAULT_ANNOUNCE_INTERVAL_SECS, api, path),
        "announce_secondary": _validated_bool(config, "announce_secondary", DEFAULT_ANNOUNCE_SECONDARY, api, path),
    }


def _status_payload(now_ts):
    clients = []
    for client_path, record in sorted(_CLIENTS.items()):
        clients.append(
            {
                "client_path": client_path,
                "last_seen_ts": float(record.get("last_seen_ts", 0.0)),
                "name": str(record.get("name", "")),
            }
        )
    return {
        "client_count": len(_CLIENTS),
        "clients": clients,
        "socket_path": _SERVER_SOCKET_PATH,
        "updated_ts": now_ts,
    }


def _write_status(api, now_ts):
    _write_json(_status_path(api), _status_payload(now_ts))


def _ensure_server(api):
    global _SERVER_SOCKET, _SERVER_SOCKET_PATH

    socket_path = str(_control_socket_path(api))
    with _STATE_LOCK:
        if _SERVER_SOCKET is not None and _SERVER_SOCKET_PATH == socket_path:
            return _SERVER_SOCKET

        path = Path(socket_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()

        server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        server.bind(socket_path)
        server.setblocking(False)
        _SERVER_SOCKET = server
        _SERVER_SOCKET_PATH = socket_path
        _write_status(api, api["time"]())
        return server


def _prune_expired_clients(api, now_ts):
    expired_paths = [
        client_path
        for client_path, record in _CLIENTS.items()
        if float(record.get("last_seen_ts", 0.0)) + CLIENT_TTL_SECONDS < now_ts
    ]
    for client_path in expired_paths:
        _CLIENTS.pop(client_path, None)
    if expired_paths:
        _write_status(api, now_ts)


def _register_client(api, client_path, name, now_ts):
    _CLIENTS[client_path] = {
        "last_seen_ts": now_ts,
        "name": name or "",
    }
    _write_status(api, now_ts)


def _unregister_client(api, client_path, now_ts):
    if _CLIENTS.pop(client_path, None) is not None:
        _write_status(api, now_ts)


def _decode_local_message(raw_message):
    try:
        message = json.loads(raw_message.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return message if isinstance(message, dict) else None


def _send_mesh_payload(api, message, now_ts):
    destination = message.get("destination")
    payload_hex = message.get("payload_hex")
    want_response = bool(message.get("want_response", False))
    client_path = message.get("client_path")
    if not isinstance(client_path, str) or not client_path:
        api["logger"].warning("plugin %s ignored local send without client_path", api["plugin_name"])
        return
    if not isinstance(payload_hex, str):
        api["logger"].warning("plugin %s ignored local send without payload_hex", api["plugin_name"])
        return
    try:
        destination = int(destination)
        payload = bytes.fromhex(payload_hex)
    except (TypeError, ValueError):
        api["logger"].warning("plugin %s ignored malformed tunnel send request: %r", api["plugin_name"], message)
        return
    _register_client(api, client_path, str(message.get("name", "")), now_ts)
    api["send_app"](
        destination=destination,
        portnum=int(api["portnums_pb2"].IP_TUNNEL_APP),
        payload=payload,
        want_response=want_response,
    )


def _poll_local_socket(api):
    server = _ensure_server(api)
    now_ts = api["time"]()
    for _ in range(MAX_POLL_MESSAGES):
        try:
            raw_message, _ = server.recvfrom(65535)
        except BlockingIOError:
            break
        except OSError as exc:
            api["logger"].warning("plugin %s local tunnel socket read failed: %s", api["plugin_name"], exc)
            break

        message = _decode_local_message(raw_message)
        if message is None:
            api["logger"].warning("plugin %s ignored malformed local tunnel datagram", api["plugin_name"])
            continue
        operation = message.get("op")
        client_path = message.get("client_path")
        if operation == "register":
            if not isinstance(client_path, str) or not client_path:
                api["logger"].warning("plugin %s ignored register without client_path", api["plugin_name"])
                continue
            _register_client(api, client_path, str(message.get("name", "")), now_ts)
            continue
        if operation == "unregister":
            if isinstance(client_path, str) and client_path:
                _unregister_client(api, client_path, now_ts)
            continue
        if operation == "send":
            _send_mesh_payload(api, message, now_ts)
            continue
        api["logger"].warning("plugin %s ignored unknown local tunnel op: %r", api["plugin_name"], operation)

    _prune_expired_clients(api, now_ts)


def _deliver_to_local_clients(api, event):
    server = _ensure_server(api)
    _poll_local_socket(api)
    now_ts = api["time"]()
    payload = {
        "op": "packet",
        "packet_from": int(event.get("packet_from") or 0),
        "packet_id": int(event.get("packet_id") or 0),
        "packet_to": int(event.get("packet_to") or 0),
        "payload_hex": bytes(event.get("payload") or b"").hex(),
        "portnum": int(event.get("portnum") or 0),
        "portnum_name": str(event.get("portnum_name") or ""),
        "ts": float(event.get("ts") or now_ts),
        "want_response": bool(event.get("want_response", False)),
    }
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")

    stale_paths = []
    for client_path in sorted(_CLIENTS):
        try:
            server.sendto(encoded, client_path)
        except OSError:
            stale_paths.append(client_path)
    for client_path in stale_paths:
        _CLIENTS.pop(client_path, None)
    if stale_paths:
        _write_status(api, now_ts)


def _is_ip_payload(payload):
    if not isinstance(payload, (bytes, bytearray)) or not payload:
        return False
    version = bytes(payload)[0] >> 4
    return version in {4, 6}


def _decode_control_payload(payload):
    try:
        value = json.loads(bytes(payload).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _append_announcement(api, event, payload):
    api["plugin_store_append_jsonl"](
        api["plugin_name"],
        ANNOUNCEMENTS_LOG,
        {
            "packet_from": int(event.get("packet_from") or 0),
            "packet_id": int(event.get("packet_id") or 0),
            "ts": float(event.get("ts") or api["time"]()),
            "announcement": payload,
        },
    )


def _build_control_frame(kind, payload):
    return {
        "schema": GATEWAY_CONTROL_SCHEMA,
        "version": GATEWAY_CONTROL_VERSION,
        "kind": kind,
        "payload": payload,
    }


def _unwrap_control_frame(control):
    if not isinstance(control, dict):
        return None
    if control.get("schema") != GATEWAY_CONTROL_SCHEMA:
        return None
    if int(control.get("version") or 0) != GATEWAY_CONTROL_VERSION:
        return None
    kind = control.get("kind")
    payload = control.get("payload")
    if not isinstance(kind, str) or not isinstance(payload, dict):
        return None
    return kind, payload


def _announce_due(api):
    config = _load_config(api)
    if not config.get("announce_enabled"):
        return False, config
    state_path = _announce_state_path(api)
    last_sent_ts = 0.0
    if state_path.exists():
        state = _safe_read_json(state_path, api, context="announce state")
        if isinstance(state, dict):
            try:
                last_sent_ts = float(state.get("last_sent_ts", 0))
            except (TypeError, ValueError):
                api["logger"].warning(
                    "plugin %s skipped invalid announce state timestamp in %s: %r",
                    api["plugin_name"],
                    state_path,
                    state.get("last_sent_ts"),
                )
                last_sent_ts = 0.0
    return (api["time"]() - last_sent_ts) >= int(config["announce_interval_secs"]), config


def _announce_payload(api, config):
    snapshot = api["status_snapshot"]()
    return {
        "type": GATEWAY_ANNOUNCE_TYPE,
        "gateway_plugin": api["plugin_name"],
        "gateway_service": "ip_tunnel",
        "capabilities": ["ip_tunnel"],
        "announce_interval_secs": int(config["announce_interval_secs"]),
        "secondary": bool(config.get("announce_secondary")),
        "local_node_num": snapshot.get("local_node_num"),
        "local_short_name": snapshot.get("local_short_name"),
        "client_count": len(_CLIENTS),
        "ts": api["time"](),
        "version": 1,
    }


def _emit_announce(api, config):
    payload = _announce_payload(api, config)
    frame = _build_control_frame("announce", payload)
    api["send_app"](
        destination=0,
        portnum=int(api["portnums_pb2"].IP_TUNNEL_APP),
        payload=json.dumps(frame, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        want_response=False,
    )
    _write_json(
        _announce_state_path(api),
        {
            "last_sent_ts": api["time"](),
            "announce_interval_secs": int(config["announce_interval_secs"]),
            "secondary": bool(config.get("announce_secondary")),
            "last_payload": payload,
            "last_frame": frame,
        },
    )


def handle_packet(event, api):
    payload = event.get("payload")
    if _is_ip_payload(payload):
        _deliver_to_local_clients(api, event)
        return
    if not _load_config(api).get("announce_enabled"):
        return
    control = _decode_control_payload(payload)
    unwrapped = _unwrap_control_frame(control)
    if unwrapped is None:
        return
    kind, body = unwrapped
    if kind == "announce" and body.get("type") == GATEWAY_ANNOUNCE_TYPE:
        _append_announcement(api, event, body)


def handle_client_call(event, api):
    payload = event.get("payload")
    if _is_ip_payload(payload):
        _deliver_to_local_clients(api, event)


def tick(event, api):
    _poll_local_socket(api)
    announce_due, config = _announce_due(api)
    if announce_due:
        _emit_announce(api, config)


def plugin_command(argv, api):
    parser = argparse.ArgumentParser(prog=f"{api['plugin_name']} tool", description="IP_TUNNEL_APP plugin utilities")
    subparsers = parser.add_subparsers(dest="command", required=False)
    subparsers.add_parser("status", help="Print IP tunnel bridge status")
    recent_parser = subparsers.add_parser("recent", help="Print recently seen gateway announcements")
    recent_parser.add_argument("--limit", type=int, default=10)
    config_parser = subparsers.add_parser("config", help="Show or update IP tunnel gateway announcement config")
    config_parser.add_argument("--announce", choices=("yes", "no"), help="Whether the plugin should emit gateway announcement heartbeats")
    config_parser.add_argument("--announce-interval-secs", type=int, help="Heartbeat interval in seconds")
    config_parser.add_argument("--announce-secondary", choices=("yes", "no"), help="Whether the announcement should mark this gateway as secondary")
    args = parser.parse_args(argv or ["status"])

    if args.command == "config":
        config = _load_config(api)
        if args.announce is not None:
            config["announce_enabled"] = args.announce == "yes"
        if args.announce_interval_secs is not None:
            config["announce_interval_secs"] = max(1, int(args.announce_interval_secs))
        if args.announce_secondary is not None:
            config["announce_secondary"] = args.announce_secondary == "yes"
        path = _config_path(api)
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(path, config)
        print(f"plugin: {api['plugin_name']}")
        print(f"config_path: {path}")
        print(f"announce_enabled: {config.get('announce_enabled')}")
        print(f"announce_interval_secs: {config.get('announce_interval_secs')}")
        print(f"announce_secondary: {config.get('announce_secondary')}")
        return

    if args.command == "recent":
        records = api["plugin_store_read_jsonl"](api["plugin_name"], ANNOUNCEMENTS_LOG, max(0, int(args.limit)))
        print(f"plugin: {api['plugin_name']}")
        print(f"records: {len(records)}")
        for record in records:
            if not isinstance(record, dict):
                continue
            announcement = record.get("announcement")
            if not isinstance(announcement, dict):
                continue
            print(
                "announcement: "
                f"from={record.get('packet_from')} "
                f"service={announcement.get('gateway_service')} "
                f"node={announcement.get('local_node_num')} "
                f"short={announcement.get('local_short_name')}"
            )
        return

    status = {}
    if _status_path(api).exists():
        status = _safe_read_json(_status_path(api), api, context="status") or {}
    config = _load_config(api)
    announce_state = {}
    if _announce_state_path(api).exists():
        announce_state = _safe_read_json(_announce_state_path(api), api, context="announce state") or {}
    print(f"plugin: {api['plugin_name']}")
    print(f"socket_path: {status.get('socket_path') or _control_socket_path(api)}")
    print(f"client_count: {int(status.get('client_count') or 0)}")
    print(f"announce_enabled: {config.get('announce_enabled')}")
    print(f"announce_interval_secs: {config.get('announce_interval_secs')}")
    print(f"announce_secondary: {config.get('announce_secondary')}")
    print(f"announce_last_sent_ts: {announce_state.get('last_sent_ts')}")
    announcement_count = 0
    if _announcements_path(api).exists():
        announcement_count = len(api["plugin_store_read_jsonl"](api["plugin_name"], ANNOUNCEMENTS_LOG))
    print(f"announcement_records: {announcement_count}")
    for client in status.get("clients") or []:
        if not isinstance(client, dict):
            continue
        print(
            "client: "
            f"{client.get('name') or '-'} "
            f"{client.get('client_path') or '-'} "
            f"last_seen_ts={client.get('last_seen_ts') or 0}"
        )
