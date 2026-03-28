#!/usr/bin/env python3
import argparse
import hashlib
import json
import logging
import logging.handlers
import os
from pathlib import Path
import signal
import socket
import sys
import threading
import time
from dataclasses import dataclass, field

from _meshtastic_common import DEFAULT_SERIAL_PORT, DEFAULT_TCP_HOST, DEFAULT_TCP_PORT, ensure_repo_python
from meshtastic.protobuf import admin_pb2, mesh_pb2, portnums_pb2, storeforward_pb2
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
        plugins_dir: str | None = None,
        tick_interval: float = 1.0,
    ) -> None:
        self.serial_port = serial_port
        self.baudrate = baudrate
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.reconnect_delay = reconnect_delay
        self.status_file = status_file
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

    def _handle_radio_plugins(self, observed_frames) -> None:
        api = self.build_plugin_api()
        for observed in observed_frames:
            packet = observed.message.packet if observed.message.HasField("packet") else None
            portnum, portnum_name = self._packet_portnum(packet)
            if portnum is None:
                continue
            event = {
                "event_type": "packet",
                "direction": "radio_to_clients",
                "frame": observed.frame,
                "message": observed.message,
                "mesh_packet": packet,
                "payload": bytes(packet.decoded.payload),
                "portnum": portnum,
                "portnum_name": portnum_name,
                "plugin_origin_likely": self._is_recent_plugin_send(portnum, bytes(packet.decoded.payload), int(getattr(packet, "to", 0))),
                "ts": time.time(),
            }
            event.update(self._message_metadata(observed.message, packet, "radio_to_clients"))
            self.plugins.dispatch_packet(portnum_name, portnum, event, api)

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
            self.plugins.tick(self.build_plugin_api())

    def serial_reader_loop(self) -> None:
        while not self.stop_event.is_set():
            handle = self.open_serial()
            if handle is None:
                break

            with self.serial_lock:
                self.serial_handle = handle
                self.serial_ready.set()

            try:
                while not self.stop_event.is_set():
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
                self.serial_ready.wait()
                if self.stop_event.is_set():
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
        plugins_dir=args.plugins_dir,
        tick_interval=args.tick_interval,
    )
    return proxy.run()


if __name__ == "__main__":
    raise SystemExit(main())
