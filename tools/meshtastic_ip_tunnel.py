#!/usr/bin/env python3
import fcntl
import json
import os
import platform
import select
import socket
import struct
import subprocess
import threading
import time
import uuid
from pathlib import Path


DEFAULT_RUNTIME_DIR = Path(__file__).resolve().parents[1] / ".runtime" / "meshtastic"
PLUGIN_NAME = "IP_TUNNEL_APP"
DEFAULT_SOCKET_NAME = "ip_tunnel.sock"
DEFAULT_TUN_NAME = "mesh"
DEFAULT_SUBNET_PREFIX = "10.115"
DEFAULT_NETMASK = "255.255.0.0"
TUNSETIFF = 0x400454CA
IFF_TUN = 0x0001
IFF_NO_PI = 0x1000
UDP_BLACKLIST = {1900, 5353, 9001, 64512}
TCP_BLACKLIST = {5900}
PROTOCOL_BLACKLIST = {0x02, 0x80}


class MeshtasticIPTunnelClient:
    def __init__(
        self,
        *,
        socket_path: str | None = None,
        runtime_dir: str | Path = DEFAULT_RUNTIME_DIR,
        client_name: str | None = None,
        on_packet=None,
        wait_timeout: float = 5.0,
        heartbeat_interval: float = 10.0,
    ) -> None:
        self.runtime_dir = Path(runtime_dir)
        self.socket_path = str(socket_path or (self.runtime_dir / "plugins" / PLUGIN_NAME / DEFAULT_SOCKET_NAME))
        self.client_name = client_name or f"client-{uuid.uuid4().hex[:8]}"
        self.on_packet = on_packet
        self.heartbeat_interval = heartbeat_interval
        self._stop_event = threading.Event()
        self._server_lock = threading.Lock()

        self._wait_for_server(wait_timeout)

        client_dir = self.runtime_dir / "plugins" / PLUGIN_NAME / "clients"
        client_dir.mkdir(parents=True, exist_ok=True)
        self.client_path = str(client_dir / f"{self.client_name}-{uuid.uuid4().hex}.sock")
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.sock.bind(self.client_path)
        self.sock.settimeout(0.5)

        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()
        self._register()

    def _wait_for_server(self, wait_timeout: float) -> None:
        deadline = time.time() + max(wait_timeout, 0.0)
        while True:
            if Path(self.socket_path).exists():
                return
            if time.time() >= deadline:
                raise FileNotFoundError(f"IP tunnel plugin socket not found: {self.socket_path}")
            time.sleep(0.05)

    def _send_control(self, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
        with self._server_lock:
            self.sock.sendto(encoded, self.socket_path)

    def _register(self) -> None:
        self._send_control(
            {
                "client_path": self.client_path,
                "name": self.client_name,
                "op": "register",
            }
        )

    def _reader_loop(self) -> None:
        next_heartbeat_at = time.time() + self.heartbeat_interval
        while not self._stop_event.is_set():
            try:
                raw_message, _ = self.sock.recvfrom(65535)
            except socket.timeout:
                raw_message = None
            except OSError:
                break

            now_ts = time.time()
            if raw_message is not None:
                self._handle_message(raw_message)

            if now_ts >= next_heartbeat_at:
                try:
                    self._register()
                except OSError:
                    pass
                next_heartbeat_at = now_ts + self.heartbeat_interval

    def _handle_message(self, raw_message: bytes) -> None:
        try:
            message = json.loads(raw_message.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(message, dict) or message.get("op") != "packet":
            return
        payload_hex = message.get("payload_hex")
        if not isinstance(payload_hex, str):
            return
        try:
            payload = bytes.fromhex(payload_hex)
        except ValueError:
            return
        packet = dict(message)
        packet["payload"] = payload
        if callable(self.on_packet):
            self.on_packet(packet)

    def send(self, destination: int, payload: bytes, *, want_response: bool = False) -> None:
        self._send_control(
            {
                "client_path": self.client_path,
                "destination": int(destination),
                "name": self.client_name,
                "op": "send",
                "payload_hex": payload.hex(),
                "want_response": bool(want_response),
            }
        )

    def close(self) -> None:
        if self._stop_event.is_set():
            return
        self._stop_event.set()
        try:
            self._send_control({"client_path": self.client_path, "op": "unregister"})
        except OSError:
            pass
        try:
            self.sock.close()
        finally:
            try:
                Path(self.client_path).unlink()
            except FileNotFoundError:
                pass
        self._reader_thread.join(timeout=1.0)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def setup_ip_tunnel_client(
    on_packet=None,
    *,
    socket_path: str | None = None,
    runtime_dir: str | Path = DEFAULT_RUNTIME_DIR,
    client_name: str | None = None,
    wait_timeout: float = 5.0,
    heartbeat_interval: float = 10.0,
) -> MeshtasticIPTunnelClient:
    return MeshtasticIPTunnelClient(
        socket_path=socket_path,
        runtime_dir=runtime_dir,
        client_name=client_name,
        on_packet=on_packet,
        wait_timeout=wait_timeout,
        heartbeat_interval=heartbeat_interval,
    )


class LinuxTunInterface:
    def __init__(
        self,
        *,
        name: str = DEFAULT_TUN_NAME,
        address: str,
        netmask: str = DEFAULT_NETMASK,
        mtu: int = 200,
        configure_interface: bool = True,
    ) -> None:
        if platform.system() != "Linux":
            raise OSError("Linux TUN interfaces are only supported on Linux")
        self.name = name
        self.address = address
        self.netmask = netmask
        self.mtu = mtu
        self.fd = os.open("/dev/net/tun", os.O_RDWR)
        ifreq = struct.pack("16sH", name.encode("utf-8"), IFF_TUN | IFF_NO_PI)
        result = fcntl.ioctl(self.fd, TUNSETIFF, ifreq)
        actual_name = result[:16].split(b"\x00", 1)[0].decode("utf-8")
        self.name = actual_name or name
        if configure_interface:
            self.configure()

    def fileno(self) -> int:
        return self.fd

    def read(self, size: int = 65535) -> bytes:
        return os.read(self.fd, size)

    def write(self, payload: bytes) -> int:
        return os.write(self.fd, payload)

    def configure(self) -> None:
        subprocess.run(["ip", "link", "set", "dev", self.name, "up"], check=True)
        subprocess.run(["ip", "addr", "replace", f"{self.address}/16", "dev", self.name], check=True)
        subprocess.run(["ip", "link", "set", "dev", self.name, "mtu", str(self.mtu)], check=True)

    def close(self) -> None:
        os.close(self.fd)


def node_num_to_ip(node_num: int, subnet_prefix: str = DEFAULT_SUBNET_PREFIX) -> str:
    return f"{subnet_prefix}.{(int(node_num) >> 8) & 0xff}.{int(node_num) & 0xff}"


def ip_to_node_num(ip_address: str, subnet_prefix: str = DEFAULT_SUBNET_PREFIX) -> int | None:
    try:
        octets = [int(part) for part in ip_address.split(".")]
    except ValueError:
        return None
    if len(octets) != 4:
        return None
    prefix = ".".join(str(octet) for octet in octets[:2])
    if prefix != subnet_prefix:
        return None
    return (octets[2] << 8) | octets[3]


def _parse_ipv4_packet(payload: bytes) -> dict[str, object] | None:
    if len(payload) < 20:
        return None
    version = payload[0] >> 4
    if version != 4:
        return None
    ihl_bytes = (payload[0] & 0x0F) * 4
    if ihl_bytes < 20 or len(payload) < ihl_bytes:
        return None
    protocol = payload[9]
    src_ip = ".".join(str(octet) for octet in payload[12:16])
    dst_ip = ".".join(str(octet) for octet in payload[16:20])
    result = {
        "protocol": protocol,
        "src_ip": src_ip,
        "dst_ip": dst_ip,
        "header_length": ihl_bytes,
    }
    if protocol in {0x06, 0x11} and len(payload) >= ihl_bytes + 4:
        result["src_port"] = int.from_bytes(payload[ihl_bytes:ihl_bytes + 2], "big")
        result["dst_port"] = int.from_bytes(payload[ihl_bytes + 2:ihl_bytes + 4], "big")
    return result


def should_filter_ipv4_packet(payload: bytes) -> bool:
    parsed = _parse_ipv4_packet(payload)
    if parsed is None:
        return True
    protocol = int(parsed["protocol"])
    if protocol in PROTOCOL_BLACKLIST:
        return True
    if protocol == 0x11:
        return int(parsed.get("dst_port") or -1) in UDP_BLACKLIST
    if protocol == 0x06:
        return int(parsed.get("dst_port") or -1) in TCP_BLACKLIST
    return False


class MeshtasticTunBridge:
    def __init__(
        self,
        *,
        tunnel_client: MeshtasticIPTunnelClient,
        tun_interface,
        subnet_prefix: str = DEFAULT_SUBNET_PREFIX,
        filter_packets: bool = True,
    ) -> None:
        self.tunnel_client = tunnel_client
        self.tun_interface = tun_interface
        self.subnet_prefix = subnet_prefix
        self.filter_packets = filter_packets
        self._stop_event = threading.Event()
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.tunnel_client.on_packet = self._handle_tunnel_packet
        self._reader_thread.start()

    def _resolve_destination(self, payload: bytes) -> int | None:
        parsed = _parse_ipv4_packet(payload)
        if parsed is None:
            return None
        return ip_to_node_num(str(parsed["dst_ip"]), self.subnet_prefix)

    def _handle_tunnel_packet(self, packet: dict[str, object]) -> None:
        payload = packet.get("payload")
        if not isinstance(payload, (bytes, bytearray)):
            return
        if self.filter_packets and should_filter_ipv4_packet(bytes(payload)):
            return
        self.tun_interface.write(bytes(payload))

    def _forward_tun_packet(self, payload: bytes) -> bool:
        if self.filter_packets and should_filter_ipv4_packet(payload):
            return False
        destination = self._resolve_destination(payload)
        if destination is None:
            return False
        self.tunnel_client.send(destination, payload)
        return True

    def _reader_loop(self) -> None:
        while not self._stop_event.is_set():
            ready, _, _ = select.select([self.tun_interface.fileno()], [], [], 0.5)
            if not ready:
                continue
            try:
                payload = self.tun_interface.read(65535)
            except OSError:
                break
            if payload:
                self._forward_tun_packet(payload)

    def close(self) -> None:
        if self._stop_event.is_set():
            return
        self._stop_event.set()
        try:
            self.tunnel_client.close()
        finally:
            self.tun_interface.close()
        self._reader_thread.join(timeout=1.0)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def setup_linux_ip_tunnel(
    *,
    socket_path: str | None = None,
    runtime_dir: str | Path = DEFAULT_RUNTIME_DIR,
    client_name: str | None = None,
    local_node_num: int = 1,
    tun_name: str = DEFAULT_TUN_NAME,
    subnet_prefix: str = DEFAULT_SUBNET_PREFIX,
    netmask: str = DEFAULT_NETMASK,
    mtu: int = 200,
    wait_timeout: float = 5.0,
    heartbeat_interval: float = 10.0,
    configure_interface: bool = True,
    filter_packets: bool = True,
) -> MeshtasticTunBridge:
    tunnel_client = setup_ip_tunnel_client(
        socket_path=socket_path,
        runtime_dir=runtime_dir,
        client_name=client_name or f"{tun_name}-bridge",
        wait_timeout=wait_timeout,
        heartbeat_interval=heartbeat_interval,
    )
    tun_interface = LinuxTunInterface(
        name=tun_name,
        address=node_num_to_ip(local_node_num, subnet_prefix),
        netmask=netmask,
        mtu=mtu,
        configure_interface=configure_interface,
    )
    return MeshtasticTunBridge(
        tunnel_client=tunnel_client,
        tun_interface=tun_interface,
        subnet_prefix=subnet_prefix,
        filter_packets=filter_packets,
    )
