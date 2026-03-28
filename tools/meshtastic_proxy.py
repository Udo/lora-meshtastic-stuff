#!/usr/bin/env python3
import argparse
import hashlib
import json
import logging
import logging.handlers
import os
from pathlib import Path
import re
import signal
import socket
import sys
import threading
import time
from dataclasses import dataclass, field

from _meshtastic_common import DEFAULT_SERIAL_PORT, DEFAULT_TCP_HOST, DEFAULT_TCP_PORT, ensure_repo_python
from meshtastic.protobuf import admin_pb2, channel_pb2, mesh_pb2, portnums_pb2, storeforward_pb2
from meshtastic_broker import MeshtasticBroker, decode_fromradio_frame, decode_toradio_frame, encode_frame
from meshtastic_plugins import MeshtasticPluginManager

ensure_repo_python("MESHTASTIC_PROXY_VENV_EXEC")

try:
    import serial
    from serial.serialutil import SerialException
except ModuleNotFoundError as exc:
    missing_module = exc.name or "required dependency"
    print(
        f"meshtastic_proxy.py could not import {missing_module}. "
        "Run ./setup/meshtastic-python.sh bootstrap first, or use ./setup/meshtastic-python.sh proxy-start.",
        file=sys.stderr,
    )
    raise SystemExit(1)


LOGGER = logging.getLogger("meshtastic_proxy")
PLUGIN_LOOP_GUARD_TTL_SECONDS = 10.0
CLIENT_SOCKET_TIMEOUT_SECONDS = 10.0
CLIENT_KEEPALIVE_IDLE_SECONDS = 60
CLIENT_KEEPALIVE_INTERVAL_SECONDS = 15
CLIENT_KEEPALIVE_PROBES = 4
DM_PLUGIN_DIRNAME = "DM"
SERIAL_WAIT_SLICE_SECONDS = 0.25
DM_MODE_RE = re.compile(r"^[A-Za-z0-9_-]+$")
CHANNEL_REFRESH_INTERVAL_SECONDS = 300.0
MAX_CHANNELS = 8


@dataclass(eq=False)
class ClientConnection:
    client_id: str
    sock: socket.socket
    address: tuple[str, int]
    send_lock: threading.Lock = field(default_factory=threading.Lock)

    def send(self, data: bytes) -> None:
        with self.send_lock:
            self.sock.sendall(data)


class MeshtasticProxy:
    def __init__(
        self,
        serial_port: str,
        baudrate: int,
        listen_host: str,
        listen_port: int,
        reconnect_delay: float,
        status_file: str | None = None,
        config_file: str | None = None,
        plugins_dir: str | None = None,
        tick_interval: float = 1.0,
    ) -> None:
        self.serial_port = serial_port
        self.baudrate = baudrate
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.reconnect_delay = reconnect_delay
        self.status_file = status_file
        self.config_file = config_file
        self.plugins_dir = plugins_dir or str(Path(__file__).resolve().parents[1] / "plugins")
        self.tick_interval = tick_interval
        self.runtime_dir = Path(status_file).resolve().parent if status_file else Path(__file__).resolve().parents[1] / ".runtime" / "meshtastic"
        self.plugin_state_dir = self.runtime_dir / "plugins"
        self.stop_event = threading.Event()
        self.server_socket: socket.socket | None = None
        self.serial_handle = None
        self.serial_lock = threading.Lock()
        self.serial_ready = threading.Event()
        self.clients: set[ClientConnection] = set()
        self.clients_lock = threading.Lock()
        self.client_counter = 0
        self.broker = MeshtasticBroker(LOGGER)
        self.plugins = MeshtasticPluginManager(self.plugins_dir, LOGGER)
        self._plugin_loop_guard: dict[str, float] = {}
        self._plugin_loop_guard_lock = threading.Lock()
        self._node_short_names: dict[int, str] = {}
        self._local_short_name: str | None = None
        self._channel_names_by_num: dict[int, str] = {}
        self._channel_details_by_num: dict[int, dict[str, object]] = {}
        self._channel_refresh_due_at = 0.0

    def configure_client_socket(self, sock: socket.socket) -> None:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(CLIENT_SOCKET_TIMEOUT_SECONDS)

        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:
            return

        keepalive_options = (
            ("TCP_KEEPIDLE", CLIENT_KEEPALIVE_IDLE_SECONDS),
            ("TCP_KEEPINTVL", CLIENT_KEEPALIVE_INTERVAL_SECONDS),
            ("TCP_KEEPCNT", CLIENT_KEEPALIVE_PROBES),
        )
        for option_name, option_value in keepalive_options:
            option = getattr(socket, option_name, None)
            if option is None:
                continue
            try:
                sock.setsockopt(socket.IPPROTO_TCP, option, option_value)
            except OSError:
                LOGGER.debug("could not enable %s on client socket", option_name)

    def status_snapshot(self) -> dict[str, object]:
        snapshot = self.broker.snapshot()
        snapshot.update(
            {
                "listen_host": self.listen_host,
                "listen_port": self.listen_port,
                "config_file": self.config_file,
                "dm_mode": self._dm_mode(),
                "plugins_dir": self.plugins_dir,
                "plugin_state_dir": str(self.plugin_state_dir),
                "plugins_loaded": self.plugins.plugin_names(),
                "serial_port": self.serial_port,
                "serial_connected": self.serial_ready.is_set(),
                "pid": os.getpid(),
            }
        )
        return snapshot

    def write_status(self) -> None:
        if not self.status_file:
            return
        try:
            os.makedirs(os.path.dirname(self.status_file), exist_ok=True)
            temp_file = f"{self.status_file}.tmp"
            with open(temp_file, "w", encoding="utf-8") as handle:
                json.dump(self.status_snapshot(), handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(temp_file, self.status_file)
        except OSError as exc:
            LOGGER.debug("status file update failed for %s: %s", self.status_file, exc)

    def open_serial(self):
        while not self.stop_event.is_set():
            try:
                handle = serial.Serial(
                    self.serial_port,
                    baudrate=self.baudrate,
                    timeout=0.25,
                    exclusive=True,
                )
                LOGGER.info("serial connected: %s @ %s", self.serial_port, self.baudrate)
                self.write_status()
                return handle
            except (SerialException, OSError) as exc:
                LOGGER.warning("serial open failed for %s: %s", self.serial_port, exc)
                if self.stop_event.wait(self.reconnect_delay):
                    break
        return None

    def close_serial(self) -> None:
        with self.serial_lock:
            handle = self.serial_handle
            self.serial_handle = None
            self.serial_ready.clear()
        self.write_status()
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass

    def start_server(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.listen_host, self.listen_port))
        server.listen()
        server.settimeout(0.5)
        self.server_socket = server
        LOGGER.info("listening on %s:%s", self.listen_host, self.listen_port)
        self.write_status()

    def stop_server(self) -> None:
        server = self.server_socket
        self.server_socket = None
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
        self.write_status()

    def register_client(self, sock: socket.socket, address: tuple[str, int]) -> ClientConnection:
        self.client_counter += 1
        client = ClientConnection(
            client_id=f"client-{self.client_counter}",
            sock=sock,
            address=address,
        )
        with self.clients_lock:
            self.clients.add(client)
        self.broker.register_client(client.client_id, f"{address[0]}:{address[1]}")
        LOGGER.info("client connected: %s:%s", address[0], address[1])
        self.write_status()
        return client

    def drop_client(self, client: ClientConnection) -> None:
        with self.clients_lock:
            if client not in self.clients:
                return
            self.clients.remove(client)
        self.broker.unregister_client(client.client_id)
        try:
            client.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            client.sock.close()
        except OSError:
            pass
        LOGGER.info("client disconnected: %s:%s", client.address[0], client.address[1])
        self.write_status()

    def broadcast(self, data: bytes) -> None:
        with self.clients_lock:
            clients = list(self.clients)
        for client in clients:
            try:
                client.send(data)
            except OSError:
                self.drop_client(client)

    def write_serial(self, data: bytes) -> None:
        with self.serial_lock:
            handle = self.serial_handle
            if handle is None:
                raise SerialException("serial device is not connected")
            handle.write(data)
            handle.flush()

    def send_client(self, client_id: str, data: bytes) -> bool:
        with self.clients_lock:
            clients = list(self.clients)
        for client in clients:
            if client.client_id != client_id:
                continue
            try:
                client.send(data)
                return True
            except OSError:
                self.drop_client(client)
                return False
        return False

    def send_toradio(self, message: mesh_pb2.ToRadio) -> None:
        self.write_serial(encode_frame(message.SerializeToString()))

    def send_mesh_packet(self, packet: mesh_pb2.MeshPacket) -> None:
        to_radio = mesh_pb2.ToRadio()
        to_radio.packet.CopyFrom(packet)
        self.send_toradio(to_radio)

    def send_fromradio(self, client_id: str, message: mesh_pb2.FromRadio) -> bool:
        return self.send_client(client_id, encode_frame(message.SerializeToString()))

    def _plugin_storage_path(self, plugin_name: str, relative_path: str) -> Path:
        plugin_dir = self.plugin_state_dir / plugin_name
        plugin_dir.mkdir(parents=True, exist_ok=True)
        target = (plugin_dir / relative_path).resolve()
        if plugin_dir.resolve() not in target.parents and target != plugin_dir.resolve():
            raise ValueError(f"plugin storage path escapes plugin directory: {relative_path}")
        return target

    def plugin_store_append_jsonl(self, plugin_name: str, relative_path: str, record: dict[str, object]) -> str:
        path = self._plugin_storage_path(plugin_name, relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True))
            handle.write("\n")
        return str(path)

    def plugin_store_read_jsonl(self, plugin_name: str, relative_path: str, limit: int | None = None) -> list[dict[str, object]]:
        path = self._plugin_storage_path(plugin_name, relative_path)
        if not path.exists():
            return []
        records: list[dict[str, object]] = []
        with open(path, encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    LOGGER.warning(
                        "plugin storage skipped malformed jsonl line %s:%s: %s",
                        path,
                        line_number,
                        exc,
                    )
                    continue
                if not isinstance(record, dict):
                    LOGGER.warning(
                        "plugin storage skipped non-object jsonl record %s:%s",
                        path,
                        line_number,
                    )
                    continue
                records.append(record)
        if limit is not None and limit >= 0:
            return records[-limit:]
        return records

    def plugin_store_path(self, plugin_name: str, relative_path: str = "") -> str:
        path = self._plugin_storage_path(plugin_name, relative_path or ".")
        return str(path)

    def _record_plugin_send(self, portnum: int, payload: bytes, destination: int | None) -> None:
        digest = hashlib.sha256(payload).hexdigest()
        key = f"{portnum}:{destination}:{digest}"
        expires_at = time.time() + PLUGIN_LOOP_GUARD_TTL_SECONDS
        with self._plugin_loop_guard_lock:
            self._plugin_loop_guard[key] = expires_at
            expired = [existing for existing, expiry in self._plugin_loop_guard.items() if expiry < time.time()]
            for existing in expired:
                self._plugin_loop_guard.pop(existing, None)

    def _is_recent_plugin_send(self, portnum: int | None, payload: bytes, destination: int | None) -> bool:
        if portnum is None:
            return False
        digest = hashlib.sha256(payload).hexdigest()
        key = f"{portnum}:{destination}:{digest}"
        now = time.time()
        with self._plugin_loop_guard_lock:
            expiry = self._plugin_loop_guard.get(key)
            if expiry is None:
                return False
            if expiry < now:
                self._plugin_loop_guard.pop(key, None)
                return False
            return True

    def send_app(
        self,
        *,
        destination: int,
        portnum: int,
        payload: bytes,
        want_response: bool = False,
    ) -> None:
        packet = mesh_pb2.MeshPacket()
        packet.to = destination
        packet.decoded.portnum = portnum
        packet.decoded.payload = payload
        packet.decoded.want_response = want_response
        self._record_plugin_send(portnum, payload, destination)
        self.send_mesh_packet(packet)

    def reply_app(self, event: dict[str, object], *, payload: bytes, portnum: int | None = None, want_response: bool = False) -> bool | None:
        target_portnum = portnum if portnum is not None else int(event.get("portnum") or 0)
        if event["event_type"] == "client_call":
            from_radio = mesh_pb2.FromRadio()
            from_radio.packet.decoded.portnum = target_portnum
            from_radio.packet.decoded.payload = payload
            return self.send_fromradio(event["client_id"], from_radio)
        destination = event.get("packet_from")
        if not destination:
            return False
        self.send_app(destination=int(destination), portnum=target_portnum, payload=payload, want_response=want_response)
        return True

    def _message_metadata(self, message, packet, direction: str) -> dict[str, object]:
        metadata: dict[str, object] = {
            "direction": direction,
            "has_packet": packet is not None,
            "message_variant": message.WhichOneof("payload_variant"),
        }
        if packet is None:
            return metadata
        metadata.update(
            {
                "packet_id": int(getattr(packet, "id", 0)),
                "packet_from": int(getattr(packet, "from", 0)),
                "packet_to": int(getattr(packet, "to", 0)),
                "want_ack": bool(getattr(packet, "want_ack", False)),
                "want_response": bool(getattr(packet.decoded, "want_response", False)) if packet.HasField("decoded") else False,
            }
        )
        return metadata

    def build_plugin_api(self) -> dict[str, object]:
        def list_clients() -> list[dict[str, object]]:
            with self.clients_lock:
                clients = list(self.clients)
            return [
                {
                    "client_id": client.client_id,
                    "address": client.address[0],
                    "port": client.address[1],
                    "label": f"{client.address[0]}:{client.address[1]}",
                }
                for client in clients
            ]

        return {
            "admin_pb2": admin_pb2,
            "broadcast_bytes": self.broadcast,
            "decode_fromradio_frame": decode_fromradio_frame,
            "decode_toradio_frame": decode_toradio_frame,
            "encode_frame": encode_frame,
            "list_clients": list_clients,
            "logger": LOGGER,
            "mesh_pb2": mesh_pb2,
            "portnums_pb2": portnums_pb2,
            "storeforward_pb2": storeforward_pb2,
            "plugin_store_append_jsonl": self.plugin_store_append_jsonl,
            "plugin_store_path": self.plugin_store_path,
            "plugin_store_read_jsonl": self.plugin_store_read_jsonl,
            "proxy": self,
            "reply_app": self.reply_app,
            "send_app": self.send_app,
            "send_client": self.send_client,
            "send_fromradio": self.send_fromradio,
            "send_mesh_packet": self.send_mesh_packet,
            "send_toradio": self.send_toradio,
            "status_snapshot": self.status_snapshot,
            "time": time.time,
        }

    def _portnum_name(self, portnum: int | None) -> str | None:
        if portnum is None:
            return None
        try:
            return portnums_pb2.PortNum.Name(portnum)
        except ValueError:
            return None

    def _packet_portnum(self, packet) -> tuple[int | None, str | None]:
        if packet is None or not packet.HasField("decoded"):
            return None, None
        portnum = int(packet.decoded.portnum)
        return portnum, self._portnum_name(portnum)

    def _load_proxy_config(self) -> dict[str, str]:
        if not self.config_file:
            return {}
        path = Path(self.config_file)
        if not path.exists():
            return {}
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            LOGGER.debug("could not read proxy config file %s: %s", path, exc)
            return {}

        values: dict[str, str] = {}
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'").strip('"')
            if key:
                values[key] = value
        return values

    def _config_flag(self, *keys: str, default: bool = False) -> bool:
        config = self._load_proxy_config()
        for key in keys:
            raw = config.get(key)
            if raw is None:
                continue
            value = raw.strip().lower()
            if value in {"1", "true", "yes", "on"}:
                return True
            if value in {"0", "false", "no", "off"}:
                return False
        return default

    def _dm_mode(self) -> str | None:
        config = self._load_proxy_config()
        raw_value = (
            config.get("dm_mode")
            or config.get("DM_MODE")
            or config.get("MESHTASTIC_DM_MODE")
        )
        if not raw_value:
            return None
        value = raw_value.strip()
        if not DM_MODE_RE.fullmatch(value):
            LOGGER.warning("ignoring invalid dm_mode from %s: %r", self.config_file, raw_value)
            return None
        return value

    def _remember_node_short_name(self, packet, portnum_name: str | None) -> None:
        if packet is None or portnum_name != "NODEINFO_APP":
            return
        node_num = int(getattr(packet, "from", 0))
        if not node_num:
            return
        user = mesh_pb2.User()
        try:
            user.ParseFromString(packet.decoded.payload)
        except Exception:
            LOGGER.debug("could not parse NODEINFO_APP payload for node %s", node_num, exc_info=True)
            return
        short_name = str(getattr(user, "short_name", "") or "").strip()
        if short_name:
            self._node_short_names[node_num] = short_name

    def _remember_admin_state(self, packet, portnum_name: str | None) -> None:
        if packet is None or portnum_name != "ADMIN_APP":
            return
        admin_message = admin_pb2.AdminMessage()
        try:
            admin_message.ParseFromString(packet.decoded.payload)
        except Exception:
            LOGGER.debug("could not parse ADMIN_APP payload", exc_info=True)
            return

        owner = getattr(admin_message, "get_owner_response", None)
        owner_short_name = str(getattr(owner, "short_name", "") or "").strip()
        if owner_short_name:
            self._local_short_name = owner_short_name

        channel = getattr(admin_message, "get_channel_response", None)
        channel_name = str(getattr(getattr(channel, "settings", None), "name", "") or "")
        try:
            channel_num = int(getattr(getattr(channel, "settings", None), "channel_num", 0) or 0)
        except (TypeError, ValueError):
            channel_num = 0
        if channel_num:
            self._channel_names_by_num[channel_num] = channel_name
            psk = bytes(getattr(getattr(channel, "settings", None), "psk", b""))
            role = channel_pb2.Channel.Role.Name(int(getattr(channel, "role", channel_pb2.Channel.Role.DISABLED)))
            self._channel_details_by_num[channel_num] = {
                "channel_name": channel_name,
                "channel_num": channel_num,
                "index": int(getattr(channel, "index", 0)),
                "role": role,
                "psk": psk,
            }

    def _send_admin_probe(self, admin_message: admin_pb2.AdminMessage) -> None:
        to_radio = mesh_pb2.ToRadio()
        to_radio.packet.decoded.portnum = portnums_pb2.ADMIN_APP
        to_radio.packet.decoded.payload = admin_message.SerializeToString()
        to_radio.packet.decoded.want_response = True
        self.send_toradio(to_radio)

    def _refresh_local_metadata(self, *, force: bool = False) -> None:
        now = time.time()
        if not force and now < self._channel_refresh_due_at:
            return
        if not self.serial_ready.is_set():
            return
        self._channel_refresh_due_at = now + CHANNEL_REFRESH_INTERVAL_SECONDS

        owner_request = admin_pb2.AdminMessage()
        owner_request.get_owner_request = True
        self._send_admin_probe(owner_request)

        for channel_index in range(MAX_CHANNELS):
            channel_request = admin_pb2.AdminMessage()
            channel_request.get_channel_request = channel_index + 1
            self._send_admin_probe(channel_request)

    def _channel_plugins_allow_public_primary(self) -> bool:
        return self._config_flag(
            "channel_plugins_allow_public_primary",
            "CHANNEL_PLUGINS_ALLOW_PUBLIC_PRIMARY",
            "MESHTASTIC_CHANNEL_PLUGINS_ALLOW_PUBLIC_PRIMARY",
            default=False,
        )

    def _is_public_primary_channel(self, channel_detail: dict[str, object] | None) -> bool:
        if not isinstance(channel_detail, dict):
            return False
        if channel_detail.get("role") != "PRIMARY":
            return False
        psk = channel_detail.get("psk")
        if not isinstance(psk, (bytes, bytearray)):
            return False
        psk_bytes = bytes(psk)
        if len(psk_bytes) == 0:
            return True
        if len(psk_bytes) == 1 and psk_bytes[0] in {0, 1}:
            return True
        return False

    def _direct_message_first_word(self, payload: bytes) -> str | None:
        try:
            text = payload.decode("utf-8").strip()
        except UnicodeDecodeError:
            return None
        if not text:
            return None
        first_word = text.split(maxsplit=1)[0]
        if not first_word or "/" in first_word or "\\" in first_word:
            return None
        return first_word

    def _is_direct_message_event(self, event: dict[str, object]) -> bool:
        if event.get("event_type") != "packet":
            return False
        if event.get("portnum_name") != "TEXT_MESSAGE_APP":
            return False
        packet_to = int(event.get("packet_to") or 0)
        return packet_to != 0

    def _channel_namespace(self, channel_name: str | None) -> str | None:
        if not channel_name:
            return None
        if "/" in channel_name or "\\" in channel_name:
            return None
        return f"CHAN_{channel_name}"

    def _text_payload(self, event: dict[str, object]) -> str | None:
        payload = event.get("payload")
        if not isinstance(payload, (bytes, bytearray)):
            return None
        try:
            return bytes(payload).decode("utf-8")
        except UnicodeDecodeError:
            return None

    def _parse_local_channel_command(self, event: dict[str, object]) -> tuple[bool, str | None]:
        text = self._text_payload(event)
        if text is None:
            return False, None

        local_short_name = str(self._local_short_name or "").strip()
        if not local_short_name:
            return False, None

        trimmed = text.lstrip()
        candidates = (local_short_name, f"@{local_short_name}")
        matched_prefix = None
        for candidate in candidates:
            if trimmed.lower().startswith(candidate.lower()):
                matched_prefix = candidate
                break
        if matched_prefix is None:
            return False, None

        remainder = trimmed[len(matched_prefix):]
        remainder = remainder.lstrip(" \t:,-")
        if not remainder:
            return True, None
        command = remainder.split(maxsplit=1)[0]
        if not command or "/" in command or "\\" in command:
            return True, None
        return True, command

    def _dispatch_channel_plugins(self, event: dict[str, object], api: dict[str, object]) -> bool:
        if event.get("event_type") != "packet" or event.get("portnum_name") != "TEXT_MESSAGE_APP":
            return False

        try:
            channel_num = int(event.get("packet_channel") or 0)
        except (TypeError, ValueError):
            channel_num = 0
        channel_detail = self._channel_details_by_num.get(channel_num, {})
        channel_name = self._channel_names_by_num.get(channel_num)
        namespace = self._channel_namespace(channel_name)
        if namespace is None:
            return False
        if self._is_public_primary_channel(channel_detail) and not self._channel_plugins_allow_public_primary():
            LOGGER.debug("skipping channel plugins for public primary channel %s", channel_name or channel_num)
            return False

        current_event = dict(event)
        current_event["channel_name"] = channel_name
        current_event["channel_num"] = channel_num
        current_event["channel_role"] = channel_detail.get("role")
        current_event["local_short_name"] = self._local_short_name
        addressed_to_local, command = self._parse_local_channel_command(current_event)
        current_event["channel_addressed_to_local"] = addressed_to_local
        current_event["channel_command"] = command

        handled = False
        result = self.plugins.call_relative(f"{namespace}/handler_alltraffic.py", "handle_packet", current_event, api)
        if result is not None or (Path(self.plugins_dir) / f"{namespace}/handler_alltraffic.py").exists():
            handled = True
            continue_chain, current_event = self._apply_dm_handler_result(result, current_event)
            if not continue_chain:
                return handled
            current_event["channel_name"] = channel_name
            current_event["channel_num"] = channel_num
            current_event["channel_role"] = channel_detail.get("role")
            current_event["local_short_name"] = self._local_short_name
            addressed_to_local, command = self._parse_local_channel_command(current_event)
            current_event["channel_addressed_to_local"] = addressed_to_local
            current_event["channel_command"] = command

        if not current_event.get("channel_addressed_to_local"):
            return handled

        candidate_paths = [f"{namespace}/handler_first.py"]
        if current_event.get("channel_command"):
            candidate_paths.append(f"{namespace}/{current_event['channel_command']}.handler.py")
        candidate_paths.append(f"{namespace}/handler.py")

        for relative_path in candidate_paths:
            result = self.plugins.call_relative(relative_path, "handle_packet", current_event, api)
            if result is None and not (Path(self.plugins_dir) / relative_path).exists():
                continue
            handled = True
            continue_chain, current_event = self._apply_dm_handler_result(result, current_event)
            if not continue_chain:
                return handled
            current_event["channel_name"] = channel_name
            current_event["channel_num"] = channel_num
            current_event["channel_role"] = channel_detail.get("role")
            current_event["local_short_name"] = self._local_short_name
            addressed_to_local, command = self._parse_local_channel_command(current_event)
            current_event["channel_addressed_to_local"] = addressed_to_local
            current_event["channel_command"] = command
        return handled

    def _dm_namespace_paths(self, namespace: str, event: dict[str, object]) -> list[str]:
        candidate_paths = [f"{namespace}/handler_first.py"]
        first_word = self._direct_message_first_word(bytes(event.get("payload") or b""))
        if first_word:
            candidate_paths.append(f"{namespace}/{first_word}.handler.py")
        sender_short_name = self._node_short_names.get(int(event.get("packet_from") or 0))
        if sender_short_name:
            candidate_paths.append(f"{namespace}/{sender_short_name}.handler.py")
        candidate_paths.append(f"{namespace}/handler.py")
        return candidate_paths

    def _event_from_message(self, message: mesh_pb2.FromRadio, frame: bytes | None = None) -> dict[str, object] | None:
        packet = message.packet if message.HasField("packet") else None
        portnum, portnum_name = self._packet_portnum(packet)
        if packet is None or portnum is None:
            return None
        event = {
            "event_type": "packet",
            "direction": "radio_to_clients",
            "frame": frame if frame is not None else encode_frame(message.SerializeToString()),
            "message": message,
            "mesh_packet": packet,
            "payload": bytes(packet.decoded.payload),
            "packet_channel": int(getattr(packet, "channel", 0)),
            "portnum": portnum,
            "portnum_name": portnum_name,
            "plugin_origin_likely": self._is_recent_plugin_send(portnum, bytes(packet.decoded.payload), int(getattr(packet, "to", 0))),
            "ts": time.time(),
        }
        event.update(self._message_metadata(message, packet, "radio_to_clients"))
        return event

    def _apply_dm_handler_result(self, result: object, event: dict[str, object]) -> tuple[bool, dict[str, object]]:
        if not isinstance(result, dict):
            return False, event

        continue_chain = bool(result.get("continue_chain") or result.get("continue"))
        next_event = event
        message = result.get("message")
        if message is not None:
            if isinstance(message, mesh_pb2.FromRadio):
                rebuilt_event = self._event_from_message(message)
                if rebuilt_event is not None:
                    next_event = rebuilt_event
                else:
                    LOGGER.warning("dm plugin returned FromRadio without decoded packet; ignoring message rewrite")
            else:
                LOGGER.warning("dm plugin returned unsupported message type: %s", type(message).__name__)
        return continue_chain, next_event

    def _dispatch_dm_plugins(self, event: dict[str, object], api: dict[str, object]) -> bool:
        if not self._is_direct_message_event(event):
            return False

        handled = False
        current_event = dict(event)
        namespaces = [DM_PLUGIN_DIRNAME]
        dm_mode = self._dm_mode()
        if dm_mode:
            namespaces.append(f"{DM_PLUGIN_DIRNAME}_{dm_mode}")

        for namespace in namespaces:
            for relative_path in self._dm_namespace_paths(namespace, current_event):
                current_event["direct_message"] = True
                current_event["dm_command"] = self._direct_message_first_word(bytes(current_event.get("payload") or b""))
                current_event["sender_short_name"] = self._node_short_names.get(int(current_event.get("packet_from") or 0))
                result = self.plugins.call_relative(relative_path, "handle_packet", current_event, api)
                if result is None and not (Path(self.plugins_dir) / relative_path).exists():
                    continue
                if result is None and (Path(self.plugins_dir) / relative_path).exists():
                    handled = True
                    return handled
                handled = True
                continue_chain, current_event = self._apply_dm_handler_result(result, current_event)
                if not continue_chain:
                    return handled
        return handled

    def _handle_radio_plugins(self, observed_frames) -> None:
        api = self.build_plugin_api()
        for observed in observed_frames:
            packet = observed.message.packet if observed.message.HasField("packet") else None
            portnum, portnum_name = self._packet_portnum(packet)
            if portnum is None:
                continue
            self._remember_node_short_name(packet, portnum_name)
            self._remember_admin_state(packet, portnum_name)
            event = {
                "event_type": "packet",
                "direction": "radio_to_clients",
                "frame": observed.frame,
                "message": observed.message,
                "mesh_packet": packet,
                "payload": bytes(packet.decoded.payload),
                "packet_channel": int(getattr(packet, "channel", 0)),
                "portnum": portnum,
                "portnum_name": portnum_name,
                "plugin_origin_likely": self._is_recent_plugin_send(portnum, bytes(packet.decoded.payload), int(getattr(packet, "to", 0))),
                "ts": time.time(),
            }
            event.update(self._message_metadata(observed.message, packet, "radio_to_clients"))
            self.plugins.dispatch_packet(portnum_name, portnum, event, api)
            self._dispatch_channel_plugins(event, api)
            self._dispatch_dm_plugins(event, api)

    def _handle_client_plugins(self, client: ClientConnection, forwarded_frames: list[bytes]) -> list[bytes]:
        if not forwarded_frames:
            return []
        api = self.build_plugin_api()
        remaining_frames: list[bytes] = []
        for frame in forwarded_frames:
            try:
                message = decode_toradio_frame(frame)
            except Exception as exc:
                LOGGER.debug("plugin skipped undecodable client frame from %s: %s", client.client_id, exc)
                remaining_frames.append(frame)
                continue
            packet = message.packet if message.HasField("packet") else None
            portnum, portnum_name = self._packet_portnum(packet)
            if portnum is None:
                remaining_frames.append(frame)
                continue
            event = {
                "event_type": "client_call",
                "consume": False,
                "direction": "client_to_radio",
                "client_address": f"{client.address[0]}:{client.address[1]}",
                "client_id": client.client_id,
                "frame": frame,
                "message": message,
                "mesh_packet": packet,
                "payload": bytes(packet.decoded.payload),
                "portnum": portnum,
                "portnum_name": portnum_name,
                "ts": time.time(),
            }
            event.update(self._message_metadata(message, packet, "client_to_radio"))
            result = self.plugins.dispatch_client_call(portnum_name, portnum, event, api)
            if not result.get("consume"):
                remaining_frames.append(frame)
        return remaining_frames

    def plugin_tick_loop(self) -> None:
        while not self.stop_event.wait(self.tick_interval):
            self._refresh_local_metadata()
            self.plugins.tick(self.build_plugin_api())

    def serial_reader_loop(self) -> None:
        while not self.stop_event.is_set():
            handle = self.open_serial()
            if handle is None:
                break

            with self.serial_lock:
                self.serial_handle = handle
                self.serial_ready.set()
            self._refresh_local_metadata(force=True)

            try:
                while not self.stop_event.is_set():
                    self._refresh_local_metadata()
                    chunk = handle.read(512)
                    if chunk:
                        observed_frames = self.broker.observe_radio_bytes(chunk)
                        self.write_status()
                        self._handle_radio_plugins(observed_frames)
                        self.broadcast(chunk)
            except (SerialException, OSError) as exc:
                LOGGER.warning("serial read failed: %s", exc)
            except Exception as exc:  # pragma: no cover - defensive recovery for pyserial edge cases
                if self.stop_event.is_set():
                    LOGGER.debug("serial reader stopped during shutdown: %s", exc)
                else:
                    LOGGER.exception("unexpected serial reader failure, reconnecting: %s", exc)
            finally:
                self.close_serial()

    def client_reader_loop(self, client: ClientConnection) -> None:
        sock = client.sock
        try:
            while not self.stop_event.is_set():
                try:
                    data = sock.recv(512)
                except socket.timeout:
                    continue
                if not data:
                    break
                decision = self.broker.handle_client_bytes(client.client_id, data)
                self.write_status()
                for direct_chunk in decision.direct_chunks:
                    client.send(direct_chunk)
                if not decision.serial_chunks:
                    continue
                forwarded_frames = self._handle_client_plugins(client, decision.forwarded_frames)
                while (
                    not self.stop_event.is_set()
                    and client in self.clients
                    and not self.serial_ready.wait(timeout=SERIAL_WAIT_SLICE_SECONDS)
                ):
                    continue
                if self.stop_event.is_set() or client not in self.clients:
                    break
                serial_chunks = [chunk for chunk in decision.serial_chunks if chunk not in decision.forwarded_frames]
                serial_chunks.extend(forwarded_frames)
                for serial_chunk in serial_chunks:
                    self.write_serial(serial_chunk)
        except (OSError, SerialException) as exc:
            LOGGER.debug("client forwarding stopped for %s:%s: %s", client.address[0], client.address[1], exc)
        finally:
            self.drop_client(client)

    def accept_loop(self) -> None:
        assert self.server_socket is not None
        while not self.stop_event.is_set():
            try:
                sock, address = self.server_socket.accept()
            except socket.timeout:
                continue
            except OSError:
                if not self.stop_event.is_set():
                    LOGGER.exception("accept loop failed")
                break

            self.configure_client_socket(sock)
            client = self.register_client(sock, address)
            thread = threading.Thread(target=self.client_reader_loop, args=(client,), daemon=True)
            thread.start()

    def stop(self, _signum=None, _frame=None) -> None:
        self.stop_event.set()
        self.stop_server()
        self.close_serial()
        with self.clients_lock:
            clients = list(self.clients)
        for client in clients:
            self.drop_client(client)

    def run(self) -> int:
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)

        try:
            self.start_server()
        except OSError as exc:
            LOGGER.error("could not listen on %s:%s: %s", self.listen_host, self.listen_port, exc)
            return 1

        serial_thread = threading.Thread(target=self.serial_reader_loop, daemon=True)
        tick_thread = threading.Thread(target=self.plugin_tick_loop, daemon=True)
        serial_thread.start()
        tick_thread.start()

        try:
            self.accept_loop()
        finally:
            self.stop()
            serial_thread.join(timeout=2.0)
            tick_thread.join(timeout=2.0)
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Meshtastic serial-to-TCP proxy")
    parser.add_argument("--serial-port", default=DEFAULT_SERIAL_PORT, help="Serial port to own; defaults to the OS-specific Meshtastic serial port")
    parser.add_argument("--baud", type=int, default=115200, help="Serial baud rate")
    parser.add_argument("--listen-host", default=DEFAULT_TCP_HOST, help="TCP host to bind")
    parser.add_argument("--listen-port", type=int, default=DEFAULT_TCP_PORT, help="TCP port to bind")
    parser.add_argument("--reconnect-delay", type=float, default=2.0, help="Seconds between serial reconnect attempts")
    parser.add_argument("--status-file", help="Write proxy and broker status JSON to this file")
    parser.add_argument("--config-file", help="Config file path loaded by the caller before launching the proxy")
    parser.add_argument("--plugins-dir", help="Directory containing *.handler.py proxy plugins")
    parser.add_argument("--tick-interval", type=float, default=1.0, help="Seconds between plugin tick callbacks")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser


def maybe_add_syslog_handler() -> None:
    if not os.path.exists("/dev/log"):
        return
    root_logger = logging.getLogger()
    if any(isinstance(handler, logging.handlers.SysLogHandler) for handler in root_logger.handlers):
        return
    try:
        handler = logging.handlers.SysLogHandler(address="/dev/log")
    except OSError:
        return
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(name)s[%(process)d]: %(levelname)s %(message)s"))
    root_logger.addHandler(handler)


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    maybe_add_syslog_handler()

    proxy = MeshtasticProxy(
        serial_port=args.serial_port,
        baudrate=args.baud,
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        reconnect_delay=args.reconnect_delay,
        status_file=args.status_file,
        config_file=args.config_file,
        plugins_dir=args.plugins_dir,
        tick_interval=args.tick_interval,
    )
    return proxy.run()


if __name__ == "__main__":
    raise SystemExit(main())
