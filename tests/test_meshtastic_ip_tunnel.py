import json
import pathlib
import socket
import sys
import tempfile
import time
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.meshtastic_ip_tunnel import (
    MeshtasticTunBridge,
    ip_to_node_num,
    node_num_to_ip,
    setup_ip_tunnel_client,
    should_filter_ipv4_packet,
)


def wait_until(predicate, timeout: float = 2.0, interval: float = 0.01) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError("condition not met before timeout")


class MeshtasticIPTunnelClientTests(unittest.TestCase):
    def test_setup_client_registers_sends_and_receives_packets(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        try:
            runtime_dir = pathlib.Path(temp_dir.name)
            plugin_dir = runtime_dir / "plugins" / "IP_TUNNEL_APP"
            plugin_dir.mkdir(parents=True)
            server_path = plugin_dir / "ip_tunnel.sock"
            server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            server.bind(str(server_path))
            server.settimeout(1.0)

            received_packets = []
            client = setup_ip_tunnel_client(
                runtime_dir=runtime_dir,
                client_name="helper-test",
                on_packet=received_packets.append,
                heartbeat_interval=60.0,
            )
            try:
                register_message, _ = server.recvfrom(65535)
                register_payload = json.loads(register_message.decode("utf-8"))
                self.assertEqual(register_payload["op"], "register")
                client_path = register_payload["client_path"]

                client.send(42, b"hello", want_response=True)
                send_message, _ = server.recvfrom(65535)
                send_payload = json.loads(send_message.decode("utf-8"))
                self.assertEqual(send_payload["op"], "send")
                self.assertEqual(send_payload["destination"], 42)
                self.assertEqual(send_payload["payload_hex"], "68656c6c6f")
                self.assertTrue(send_payload["want_response"])

                server.sendto(
                    json.dumps({"op": "packet", "packet_from": 7, "payload_hex": "776f726c64"}).encode("utf-8"),
                    client_path,
                )
                wait_until(lambda: received_packets and received_packets[-1]["payload"] == b"world")
            finally:
                client.close()
                server.close()
        finally:
            temp_dir.cleanup()

    def test_address_mapping_helpers_round_trip(self) -> None:
        self.assertEqual(node_num_to_ip(0x1234), "10.115.18.52")
        self.assertEqual(ip_to_node_num("10.115.18.52"), 0x1234)
        self.assertIsNone(ip_to_node_num("10.116.18.52"))

    def test_ipv4_filter_blocks_known_noisy_udp_ports(self) -> None:
        payload = bytes(
            [
                0x45, 0x00, 0x00, 0x1c, 0x00, 0x00, 0x00, 0x00, 0x40, 0x11,
                0x00, 0x00, 10, 115, 0, 1, 10, 115, 0, 2,
                0x12, 0x34, 0x14, 0xe9, 0x00, 0x08, 0x00, 0x00,
            ]
        )
        self.assertTrue(should_filter_ipv4_packet(payload))

    def test_bridge_forwards_between_tun_and_client(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.sent = []
                self.closed = False
                self.on_packet = None

            def send(self, destination: int, payload: bytes, *, want_response: bool = False) -> None:
                self.sent.append((destination, payload, want_response))

            def close(self) -> None:
                self.closed = True

        class FakeTun:
            def __init__(self) -> None:
                self.payloads = []
                self.closed = False
                self.read_fd, self.write_fd = socket.socketpair()
                self.read_fd.setblocking(False)

            def fileno(self) -> int:
                return self.read_fd.fileno()

            def write(self, payload: bytes) -> int:
                self.payloads.append(payload)
                return len(payload)

            def read(self, size: int = 65535) -> bytes:
                return self.read_fd.recv(size)

            def inject(self, payload: bytes) -> None:
                self.write_fd.send(payload)

            def close(self) -> None:
                self.closed = True
                self.read_fd.close()
                self.write_fd.close()

        tun_payload = bytes(
            [
                0x45, 0x00, 0x00, 0x1c, 0x00, 0x00, 0x00, 0x00, 0x40, 0x11,
                0x00, 0x00, 10, 115, 0, 1, 10, 115, 0x12, 0x34,
                0x12, 0x34, 0x12, 0x35, 0x00, 0x08, 0x00, 0x00,
            ]
        )

        client = FakeClient()
        tun = FakeTun()
        bridge = MeshtasticTunBridge(tunnel_client=client, tun_interface=tun, filter_packets=False)
        try:
            tun.inject(tun_payload)
            wait_until(lambda: client.sent)
            self.assertEqual(client.sent[0][0], 0x1234)
            self.assertEqual(client.sent[0][1], tun_payload)

            client.on_packet({"payload": b"inbound"})
            wait_until(lambda: tun.payloads and tun.payloads[-1] == b"inbound")
        finally:
            bridge.close()
        self.assertTrue(client.closed)
        self.assertTrue(tun.closed)
