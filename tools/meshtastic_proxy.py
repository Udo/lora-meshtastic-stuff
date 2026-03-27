#!/usr/bin/env python3
import argparse
import json
import logging
import os
import signal
import socket
import sys
import threading
from dataclasses import dataclass, field

from _meshtastic_common import DEFAULT_SERIAL_PORT, DEFAULT_TCP_HOST, DEFAULT_TCP_PORT, ensure_repo_python
from meshtastic_broker import MeshtasticBroker

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
    def __init__(self, serial_port: str, baudrate: int, listen_host: str, listen_port: int, reconnect_delay: float, status_file: str | None = None) -> None:
        self.serial_port = serial_port
        self.baudrate = baudrate
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.reconnect_delay = reconnect_delay
        self.status_file = status_file
        self.stop_event = threading.Event()
        self.server_socket: socket.socket | None = None
        self.serial_handle = None
        self.serial_lock = threading.Lock()
        self.serial_ready = threading.Event()
        self.clients: set[ClientConnection] = set()
        self.clients_lock = threading.Lock()
        self.client_counter = 0
        self.broker = MeshtasticBroker(LOGGER)

    def status_snapshot(self) -> dict[str, object]:
        snapshot = self.broker.snapshot()
        snapshot.update(
            {
                "listen_host": self.listen_host,
                "listen_port": self.listen_port,
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
                        self.broker.observe_radio_bytes(chunk)
                        self.write_status()
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
                data = sock.recv(512)
                if not data:
                    break
                decision = self.broker.handle_client_bytes(client.client_id, data)
                self.write_status()
                for direct_chunk in decision.direct_chunks:
                    client.send(direct_chunk)
                if not decision.serial_chunks:
                    continue
                self.serial_ready.wait()
                if self.stop_event.is_set():
                    break
                for serial_chunk in decision.serial_chunks:
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

            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
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
        serial_thread.start()

        try:
            self.accept_loop()
        finally:
            self.stop()
            serial_thread.join(timeout=2.0)
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Meshtastic serial-to-TCP proxy")
    parser.add_argument("--serial-port", default=DEFAULT_SERIAL_PORT, help="Serial port to own, default: /dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=115200, help="Serial baud rate")
    parser.add_argument("--listen-host", default=DEFAULT_TCP_HOST, help="TCP host to bind")
    parser.add_argument("--listen-port", type=int, default=DEFAULT_TCP_PORT, help="TCP port to bind")
    parser.add_argument("--reconnect-delay", type=float, default=2.0, help="Seconds between serial reconnect attempts")
    parser.add_argument("--status-file", help="Write proxy and broker status JSON to this file")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    proxy = MeshtasticProxy(
        serial_port=args.serial_port,
        baudrate=args.baud,
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        reconnect_delay=args.reconnect_delay,
        status_file=args.status_file,
    )
    return proxy.run()


if __name__ == "__main__":
    raise SystemExit(main())