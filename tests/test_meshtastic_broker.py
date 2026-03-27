import json
import pathlib
import queue
import socket
import sys
import tempfile
import threading
import time
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from meshtastic.protobuf import admin_pb2, mesh_pb2, portnums_pb2

from tools.meshtastic_broker import FrameParser, MeshtasticBroker, encode_frame
from tools.meshtastic_proxy import MeshtasticProxy


class FakeClock:
    def __init__(self, initial: float = 100.0) -> None:
        self.value = initial

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def make_frame(message: mesh_pb2.ToRadio) -> bytes:
    return encode_frame(message.SerializeToString())


def make_admin_write_frame() -> bytes:
    admin_message = admin_pb2.AdminMessage()
    admin_message.set_owner.long_name = "Broker Test"

    to_radio = mesh_pb2.ToRadio()
    to_radio.packet.decoded.portnum = portnums_pb2.ADMIN_APP
    to_radio.packet.decoded.payload = admin_message.SerializeToString()
    return make_frame(to_radio)


def make_admin_read_frame() -> bytes:
    admin_message = admin_pb2.AdminMessage()
    admin_message.get_config_request = admin_pb2.AdminMessage.DEVICE_CONFIG

    to_radio = mesh_pb2.ToRadio()
    to_radio.packet.decoded.portnum = portnums_pb2.ADMIN_APP
    to_radio.packet.decoded.payload = admin_message.SerializeToString()
    return make_frame(to_radio)


def make_text_frame() -> bytes:
    to_radio = mesh_pb2.ToRadio()
    to_radio.packet.decoded.portnum = portnums_pb2.TEXT_MESSAGE_APP
    to_radio.packet.decoded.payload = b"hello"
    return make_frame(to_radio)


def make_fromradio_admin_response_frame(session_passkey: bytes = b"\x01\x02\x03\x04") -> bytes:
    admin_message = admin_pb2.AdminMessage()
    admin_message.session_passkey = session_passkey
    admin_message.get_owner_response.long_name = "Broker Test"

    from_radio = mesh_pb2.FromRadio()
    from_radio.packet.decoded.portnum = portnums_pb2.ADMIN_APP
    from_radio.packet.decoded.payload = admin_message.SerializeToString()
    return encode_frame(from_radio.SerializeToString())


def wait_until(predicate, timeout: float = 2.0, interval: float = 0.01) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError("condition was not met before timeout")


class FakeSerialHandle:
    def __init__(self) -> None:
        self.read_queue: queue.Queue[bytes] = queue.Queue()
        self.writes: list[bytes] = []
        self.closed = False
        self.write_lock = threading.Lock()

    def write(self, data: bytes) -> None:
        with self.write_lock:
            self.writes.append(data)

    def flush(self) -> None:
        return None

    def read(self, _size: int) -> bytes:
        if self.closed:
            return b""
        try:
            return self.read_queue.get(timeout=0.05)
        except queue.Empty:
            return b""

    def inject_read(self, data: bytes) -> None:
        self.read_queue.put(data)

    def close(self) -> None:
        self.closed = True


class LoopbackMeshtasticProxy(MeshtasticProxy):
    def __init__(self, fake_serial: FakeSerialHandle, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fake_serial = fake_serial
        self.open_count = 0

    def open_serial(self):
        if self.open_count == 0:
            self.open_count += 1
            return self.fake_serial
        self.stop_event.wait(0.05)
        return None


class FrameParserTests(unittest.TestCase):
    def test_parser_keeps_partial_frames_and_emits_raw_prefix(self) -> None:
        parser = FrameParser()
        frame = make_text_frame()

        result = parser.feed(b"abc" + frame[:2])

        self.assertEqual(result.raw_chunks, [b"abc"])
        self.assertEqual(result.frames, [])

        result = parser.feed(frame[2:])

        self.assertEqual(result.raw_chunks, [])
        self.assertEqual(result.frames, [frame])

    def test_parser_resyncs_after_oversize_header(self) -> None:
        parser = FrameParser()
        oversize_header = bytes([0x94, 0xC3, 0x02, 0x01])
        frame = make_text_frame()

        result = parser.feed(oversize_header + frame)

        self.assertEqual(result.frames, [frame])


class MeshtasticBrokerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.broker = MeshtasticBroker(clock=self.clock, provisional_control_timeout=5.0, admin_session_timeout=20.0)
        self.broker.register_client("client-a", "127.0.0.1:5001")
        self.broker.register_client("client-b", "127.0.0.1:5002")

    def test_first_control_writer_claims_session(self) -> None:
        result = self.broker.handle_client_bytes("client-a", make_admin_write_frame())

        self.assertEqual(len(result.serial_chunks), 1)
        self.assertEqual(result.direct_chunks, [])
        self.assertEqual(self.broker.control_owner_id, "client-a")

    def test_second_control_writer_is_denied(self) -> None:
        self.broker.handle_client_bytes("client-a", make_admin_write_frame())

        result = self.broker.handle_client_bytes("client-b", make_admin_write_frame())

        self.assertEqual(result.serial_chunks, [])
        self.assertEqual(len(result.direct_chunks), 1)
        self.assertIn(b"127.0.0.1:5001", result.direct_chunks[0])
        self.assertEqual(self.broker.control_owner_id, "client-a")
        self.assertEqual(self.broker.denied_control_frames, 1)

    def test_read_only_admin_request_does_not_need_control_session(self) -> None:
        self.broker.handle_client_bytes("client-a", make_admin_write_frame())

        result = self.broker.handle_client_bytes("client-b", make_admin_read_frame())

        self.assertEqual(len(result.serial_chunks), 1)
        self.assertEqual(result.direct_chunks, [])

    def test_non_admin_traffic_is_not_blocked(self) -> None:
        self.broker.handle_client_bytes("client-a", make_admin_write_frame())

        result = self.broker.handle_client_bytes("client-b", make_text_frame())

        self.assertEqual(len(result.serial_chunks), 1)
        self.assertEqual(result.direct_chunks, [])

    def test_owner_release_on_unregister_allows_next_writer(self) -> None:
        self.broker.handle_client_bytes("client-a", make_admin_write_frame())
        self.broker.unregister_client("client-a")

        result = self.broker.handle_client_bytes("client-b", make_admin_write_frame())

        self.assertEqual(len(result.serial_chunks), 1)
        self.assertEqual(result.direct_chunks, [])
        self.assertEqual(self.broker.control_owner_id, "client-b")

    def test_snapshot_reports_owner_and_counters(self) -> None:
        self.broker.handle_client_bytes("client-a", make_admin_write_frame())
        self.broker.handle_client_bytes("client-b", make_admin_write_frame())

        snapshot = self.broker.snapshot()

        self.assertEqual(snapshot["client_count"], 2)
        self.assertEqual(snapshot["control_owner"], "127.0.0.1:5001")
        self.assertEqual(snapshot["control_session_confirmed"], False)
        self.assertEqual(snapshot["denied_control_frames"], 1)
        self.assertEqual(snapshot["forwarded_control_frames"], 1)
        self.assertGreater(snapshot["control_session_expires_in"], 0)

    def test_radio_admin_response_updates_protocol_aware_snapshot(self) -> None:
        self.broker.handle_client_bytes("client-a", make_admin_write_frame())

        self.broker.observe_radio_bytes(make_fromradio_admin_response_frame())
        snapshot = self.broker.snapshot()

        self.assertEqual(snapshot["observed_admin_responses"], 1)
        self.assertEqual(snapshot["last_session_passkey"], "01020304")
        self.assertEqual(snapshot["last_admin_response_owner"], "127.0.0.1:5001")
        self.assertEqual(snapshot["control_session_confirmed"], True)
        self.assertGreaterEqual(snapshot["control_session_expires_in"], 20.0)

    def test_unconfirmed_control_owner_expires_and_next_writer_can_claim(self) -> None:
        self.broker.handle_client_bytes("client-a", make_admin_write_frame())

        self.clock.advance(6.0)
        result = self.broker.handle_client_bytes("client-b", make_admin_write_frame())

        self.assertEqual(len(result.serial_chunks), 1)
        self.assertEqual(result.direct_chunks, [])
        self.assertEqual(self.broker.control_owner_id, "client-b")

    def test_confirmed_control_owner_uses_longer_admin_session_lease(self) -> None:
        self.broker.handle_client_bytes("client-a", make_admin_write_frame())
        self.broker.observe_radio_bytes(make_fromradio_admin_response_frame())

        self.clock.advance(10.0)
        denied = self.broker.handle_client_bytes("client-b", make_admin_write_frame())
        self.assertEqual(denied.serial_chunks, [])
        self.assertEqual(len(denied.direct_chunks), 1)

        self.clock.advance(11.0)
        allowed = self.broker.handle_client_bytes("client-b", make_admin_write_frame())
        self.assertEqual(len(allowed.serial_chunks), 1)
        self.assertEqual(allowed.direct_chunks, [])
        self.assertEqual(self.broker.control_owner_id, "client-b")


class MeshtasticProxyIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.status_file = str(pathlib.Path(self.temp_dir.name) / "proxy-status.json")
        self.fake_serial = FakeSerialHandle()
        self.proxy = LoopbackMeshtasticProxy(
            fake_serial=self.fake_serial,
            serial_port="fake-serial",
            baudrate=115200,
            listen_host="127.0.0.1",
            listen_port=0,
            reconnect_delay=0.01,
            status_file=self.status_file,
        )
        self.proxy.start_server()
        self.port = self.proxy.server_socket.getsockname()[1]
        self.serial_thread = threading.Thread(target=self.proxy.serial_reader_loop, daemon=True)
        self.accept_thread = threading.Thread(target=self.proxy.accept_loop, daemon=True)
        self.serial_thread.start()
        self.accept_thread.start()
        wait_until(lambda: self.proxy.serial_ready.is_set())

    def tearDown(self) -> None:
        self.proxy.stop()
        self.accept_thread.join(timeout=1.0)
        self.serial_thread.join(timeout=1.0)
        self.temp_dir.cleanup()

    def read_status(self) -> dict[str, object]:
        with open(self.status_file, encoding="utf-8") as handle:
            return json.load(handle)

    def test_proxy_arbitrates_control_writes_and_broadcasts_serial_data(self) -> None:
        owner_frame = make_admin_write_frame()
        denied_frame = make_admin_write_frame()
        radio_chunk = b"radio-broadcast\n"

        with socket.create_connection(("127.0.0.1", self.port), timeout=1.0) as client_a:
            with socket.create_connection(("127.0.0.1", self.port), timeout=1.0) as client_b:
                client_a.settimeout(1.0)
                client_b.settimeout(1.0)

                wait_until(lambda: self.read_status().get("client_count") == 2)

                client_a.sendall(owner_frame)
                wait_until(lambda: self.fake_serial.writes == [owner_frame])

                client_b.sendall(denied_frame)
                denied_message = client_b.recv(1024)

                self.assertIn(b"[broker] control session busy", denied_message)
                self.assertEqual(self.fake_serial.writes, [owner_frame])

                self.fake_serial.inject_read(radio_chunk)
                received_a = client_a.recv(len(radio_chunk))
                received_b = client_b.recv(len(radio_chunk))

                self.assertEqual(received_a, radio_chunk)
                self.assertEqual(received_b, radio_chunk)

                wait_until(lambda: self.read_status().get("denied_control_frames") == 1)
                status = self.read_status()

                self.assertEqual(status["client_count"], 2)
                self.assertEqual(status["denied_control_frames"], 1)
                self.assertEqual(status["forwarded_control_frames"], 1)
                self.assertEqual(status["serial_connected"], True)
                self.assertTrue(str(status["control_owner"]).startswith("127.0.0.1:"))


if __name__ == "__main__":
    unittest.main()