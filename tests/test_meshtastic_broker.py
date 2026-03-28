import json
import pathlib
import queue
import shutil
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

from meshtastic.protobuf import admin_pb2, channel_pb2, mesh_pb2, portnums_pb2, storeforward_pb2

from tools.meshtastic_broker import FrameParser, MeshtasticBroker, decode_fromradio_frame, decode_toradio_frame, encode_frame
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


def make_want_config_frame() -> bytes:
    to_radio = mesh_pb2.ToRadio()
    to_radio.want_config_id = 123456
    return make_frame(to_radio)


def make_text_frame() -> bytes:
    to_radio = mesh_pb2.ToRadio()
    to_radio.packet.decoded.portnum = portnums_pb2.TEXT_MESSAGE_APP
    to_radio.packet.decoded.payload = b"hello"
    return make_frame(to_radio)


def make_private_frame(payload: bytes) -> bytes:
    to_radio = mesh_pb2.ToRadio()
    to_radio.packet.decoded.portnum = portnums_pb2.PRIVATE_APP
    to_radio.packet.decoded.payload = payload
    return make_frame(to_radio)


def make_storeforward_client_frame(
    rr: storeforward_pb2.StoreAndForward.RequestResponse.ValueType,
    *,
    history_messages: int = 0,
    window: int = 0,
    last_request: int = 0,
) -> bytes:
    request = storeforward_pb2.StoreAndForward(rr=rr)
    if rr == storeforward_pb2.StoreAndForward.CLIENT_HISTORY:
        request.history.history_messages = history_messages
        request.history.window = window
        request.history.last_request = last_request

    to_radio = mesh_pb2.ToRadio()
    to_radio.packet.decoded.portnum = portnums_pb2.STORE_FORWARD_APP
    to_radio.packet.decoded.payload = request.SerializeToString()
    return make_frame(to_radio)


def make_fromradio_admin_response_frame(session_passkey: bytes = b"\x01\x02\x03\x04") -> bytes:
    admin_message = admin_pb2.AdminMessage()
    admin_message.session_passkey = session_passkey
    admin_message.get_owner_response.long_name = "Broker Test"

    from_radio = mesh_pb2.FromRadio()
    from_radio.packet.decoded.portnum = portnums_pb2.ADMIN_APP
    from_radio.packet.decoded.payload = admin_message.SerializeToString()
    return encode_frame(from_radio.SerializeToString())


def make_invalid_admin_write_frame() -> bytes:
    to_radio = mesh_pb2.ToRadio()
    to_radio.packet.decoded.portnum = portnums_pb2.ADMIN_APP
    to_radio.packet.decoded.payload = b"\xff\xfe\xfd"
    return make_frame(to_radio)


def make_fromradio_invalid_admin_frame() -> bytes:
    from_radio = mesh_pb2.FromRadio()
    from_radio.packet.decoded.portnum = portnums_pb2.ADMIN_APP
    from_radio.packet.decoded.payload = b"\xff\xfe\xfd"
    return encode_frame(from_radio.SerializeToString())


def make_fromradio_text_frame(payload: bytes = b"hello-radio") -> bytes:
    from_radio = mesh_pb2.FromRadio()
    setattr(from_radio.packet, "from", 42)
    from_radio.packet.to = 0
    from_radio.packet.channel = 0
    from_radio.packet.decoded.portnum = portnums_pb2.TEXT_MESSAGE_APP
    from_radio.packet.decoded.payload = payload
    return encode_frame(from_radio.SerializeToString())


def make_fromradio_direct_text_frame(payload: bytes, *, from_node: int = 42, to_node: int = 123) -> bytes:
    from_radio = mesh_pb2.FromRadio()
    setattr(from_radio.packet, "from", from_node)
    from_radio.packet.to = to_node
    from_radio.packet.channel = 0
    from_radio.packet.decoded.portnum = portnums_pb2.TEXT_MESSAGE_APP
    from_radio.packet.decoded.payload = payload
    return encode_frame(from_radio.SerializeToString())


def make_fromradio_channel_text_frame(payload: bytes, *, from_node: int = 42, to_node: int = 0, channel_num: int = 2) -> bytes:
    from_radio = mesh_pb2.FromRadio()
    setattr(from_radio.packet, "from", from_node)
    from_radio.packet.to = to_node
    from_radio.packet.channel = channel_num
    from_radio.packet.decoded.portnum = portnums_pb2.TEXT_MESSAGE_APP
    from_radio.packet.decoded.payload = payload
    return encode_frame(from_radio.SerializeToString())


def make_fromradio_nodeinfo_frame(short_name: str, *, from_node: int = 42) -> bytes:
    user = mesh_pb2.User()
    user.short_name = short_name

    from_radio = mesh_pb2.FromRadio()
    setattr(from_radio.packet, "from", from_node)
    from_radio.packet.to = 0
    from_radio.packet.channel = 0
    from_radio.packet.decoded.portnum = portnums_pb2.NODEINFO_APP
    from_radio.packet.decoded.payload = user.SerializeToString()
    return encode_frame(from_radio.SerializeToString())


def make_fromradio_admin_owner_response_frame(short_name: str, *, from_node: int = 1) -> bytes:
    admin_message = admin_pb2.AdminMessage()
    admin_message.get_owner_response.short_name = short_name

    from_radio = mesh_pb2.FromRadio()
    setattr(from_radio.packet, "from", from_node)
    from_radio.packet.to = 0
    from_radio.packet.channel = 0
    from_radio.packet.decoded.portnum = portnums_pb2.ADMIN_APP
    from_radio.packet.decoded.payload = admin_message.SerializeToString()
    return encode_frame(from_radio.SerializeToString())


def make_fromradio_admin_channel_response_frame(
    channel_name: str,
    *,
    channel_num: int = 2,
    index: int = 0,
    from_node: int = 1,
    psk: bytes = b"\x02",
) -> bytes:
    admin_message = admin_pb2.AdminMessage()
    admin_message.get_channel_response.index = index
    admin_message.get_channel_response.role = channel_pb2.Channel.Role.PRIMARY if index == 0 else channel_pb2.Channel.Role.SECONDARY
    admin_message.get_channel_response.settings.name = channel_name
    admin_message.get_channel_response.settings.channel_num = channel_num
    admin_message.get_channel_response.settings.psk = psk

    from_radio = mesh_pb2.FromRadio()
    setattr(from_radio.packet, "from", from_node)
    from_radio.packet.to = 0
    from_radio.packet.channel = 0
    from_radio.packet.decoded.portnum = portnums_pb2.ADMIN_APP
    from_radio.packet.decoded.payload = admin_message.SerializeToString()
    return encode_frame(from_radio.SerializeToString())


def make_fromradio_private_frame(payload: bytes, *, from_node: int = 42) -> bytes:
    from_radio = mesh_pb2.FromRadio()
    setattr(from_radio.packet, "from", from_node)
    from_radio.packet.to = 0
    from_radio.packet.decoded.portnum = portnums_pb2.PRIVATE_APP
    from_radio.packet.decoded.payload = payload
    return encode_frame(from_radio.SerializeToString())


def make_fromradio_storeforward_request_frame(
    rr: storeforward_pb2.StoreAndForward.RequestResponse.ValueType,
    *,
    from_node: int = 77,
    history_messages: int = 0,
    window: int = 0,
    last_request: int = 0,
) -> bytes:
    request = storeforward_pb2.StoreAndForward(rr=rr)
    if rr == storeforward_pb2.StoreAndForward.CLIENT_HISTORY:
        request.history.history_messages = history_messages
        request.history.window = window
        request.history.last_request = last_request

    from_radio = mesh_pb2.FromRadio()
    setattr(from_radio.packet, "from", from_node)
    from_radio.packet.to = 0
    from_radio.packet.decoded.portnum = portnums_pb2.STORE_FORWARD_APP
    from_radio.packet.decoded.payload = request.SerializeToString()
    return encode_frame(from_radio.SerializeToString())


def recv_fromradio_frames(sock: socket.socket, expected_count: int, timeout: float = 1.0) -> list[mesh_pb2.FromRadio]:
    parser = FrameParser()
    frames: list[mesh_pb2.FromRadio] = []
    deadline = time.time() + timeout
    while time.time() < deadline and len(frames) < expected_count:
        chunk = sock.recv(4096)
        if not chunk:
            break
        parsed = parser.feed(chunk)
        for frame in parsed.frames:
            frames.append(decode_fromradio_frame(frame))
            if len(frames) >= expected_count:
                break
    if len(frames) < expected_count:
        raise AssertionError(f"expected {expected_count} frames, got {len(frames)}")
    return frames


def wait_until(predicate, timeout: float = 2.0, interval: float = 0.01) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError("condition was not met before timeout")


def is_proxy_admin_probe(frame: bytes) -> bool:
    try:
        message = decode_toradio_frame(frame)
    except Exception:
        return False
    if not message.HasField("packet") or not message.packet.HasField("decoded"):
        return False
    if message.packet.decoded.portnum != portnums_pb2.ADMIN_APP:
        return False
    admin_message = admin_pb2.AdminMessage()
    try:
        admin_message.ParseFromString(message.packet.decoded.payload)
    except Exception:
        return False
    variant = admin_message.WhichOneof("payload_variant")
    return variant in {"get_owner_request", "get_channel_request"}


def non_probe_writes(writes: list[bytes]) -> list[bytes]:
    return [frame for frame in writes if not is_proxy_admin_probe(frame)]


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


class TimeoutSendSocket:
    def __init__(self) -> None:
        self.closed = False
        self.shutdown_calls = 0
        self.close_calls = 0

    def sendall(self, _data: bytes) -> None:
        raise socket.timeout("simulated blocked client send")

    def shutdown(self, _how: int) -> None:
        self.shutdown_calls += 1

    def close(self) -> None:
        self.closed = True
        self.close_calls += 1


class CrashySerialHandle(FakeSerialHandle):
    def __init__(self) -> None:
        super().__init__()
        self.crashed = False

    def read(self, _size: int) -> bytes:
        if not self.crashed:
            self.crashed = True
            raise TypeError("simulated pyserial disconnect race")
        return super().read(_size)


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


class MultiHandleMeshtasticProxy(MeshtasticProxy):
    def __init__(self, handles: list[FakeSerialHandle | None], *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.handles = handles
        self.open_count = 0

    def open_serial(self):
        if self.open_count < len(self.handles):
            handle = self.handles[self.open_count]
            self.open_count += 1
            return handle
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

    def test_want_config_handshake_does_not_claim_control_session(self) -> None:
        result = self.broker.handle_client_bytes("client-a", make_want_config_frame())

        self.assertEqual(len(result.serial_chunks), 1)
        self.assertEqual(result.direct_chunks, [])
        self.assertIsNone(self.broker.control_owner_id)

    def test_second_client_can_also_send_want_config_handshake(self) -> None:
        self.broker.handle_client_bytes("client-a", make_want_config_frame())

        result = self.broker.handle_client_bytes("client-b", make_want_config_frame())

        self.assertEqual(len(result.serial_chunks), 1)
        self.assertEqual(result.direct_chunks, [])
        self.assertIsNone(self.broker.control_owner_id)
        self.assertEqual(self.broker.denied_control_frames, 0)

    def test_non_admin_traffic_is_not_blocked(self) -> None:
        self.broker.handle_client_bytes("client-a", make_admin_write_frame())

        result = self.broker.handle_client_bytes("client-b", make_text_frame())

        self.assertEqual(len(result.serial_chunks), 1)
        self.assertEqual(result.direct_chunks, [])

    def test_invalid_admin_payload_is_treated_as_non_control(self) -> None:
        result = self.broker.handle_client_bytes("client-a", make_invalid_admin_write_frame())

        self.assertEqual(len(result.serial_chunks), 1)
        self.assertEqual(result.direct_chunks, [])
        self.assertIsNone(self.broker.control_owner_id)

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

    def test_invalid_radio_admin_payload_is_ignored_without_resetting_state(self) -> None:
        self.broker.handle_client_bytes("client-a", make_admin_write_frame())

        observed = self.broker.observe_radio_bytes(make_fromradio_invalid_admin_frame())
        snapshot = self.broker.snapshot()

        self.assertEqual(len(observed), 1)
        self.assertEqual(snapshot["observed_admin_responses"], 0)
        self.assertEqual(snapshot["control_owner"], "127.0.0.1:5001")

    def test_malformed_radio_prefix_bytes_are_counted_and_dropped(self) -> None:
        observed = self.broker.observe_radio_bytes(b"garbage" + make_fromradio_text_frame(b"ok"))
        snapshot = self.broker.snapshot()

        self.assertEqual(len(observed), 1)
        self.assertEqual(snapshot["dropped_radio_bytes"], len(b"garbage"))
        self.assertEqual(snapshot["invalid_radio_frames"], 0)

    def test_undecodable_radio_frame_is_counted_and_dropped(self) -> None:
        bad_frame = bytes([0x94, 0xC3, 0x00, 0x03]) + b"\xff\xfe\xfd"

        observed = self.broker.observe_radio_bytes(bad_frame)
        snapshot = self.broker.snapshot()

        self.assertEqual(observed, [])
        self.assertEqual(snapshot["invalid_radio_frames"], 1)

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
        self.config_file = str(pathlib.Path(self.temp_dir.name) / "service.env")
        self.plugins_dir = pathlib.Path(self.temp_dir.name) / "plugins"
        self.plugins_dir.mkdir()
        self.fake_serial = FakeSerialHandle()
        self.proxy = LoopbackMeshtasticProxy(
            fake_serial=self.fake_serial,
            serial_port="fake-serial",
            baudrate=115200,
            listen_host="127.0.0.1",
            listen_port=0,
            reconnect_delay=0.01,
            status_file=self.status_file,
            config_file=self.config_file,
            plugins_dir=str(self.plugins_dir),
            tick_interval=0.05,
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

    def test_proxy_drops_client_when_server_send_times_out(self) -> None:
        stalled_socket = TimeoutSendSocket()

        client = self.proxy.register_client(stalled_socket, ("127.0.0.1", 6100))

        self.proxy.broadcast(make_fromradio_text_frame(b"timeout-check"))

        self.assertNotIn(client, self.proxy.clients)
        self.assertEqual(stalled_socket.shutdown_calls, 1)
        self.assertEqual(stalled_socket.close_calls, 1)

    def test_proxy_queries_owner_and_channels_on_startup(self) -> None:
        probe_frames = [frame for frame in self.fake_serial.writes if is_proxy_admin_probe(frame)]
        self.assertGreaterEqual(len(probe_frames), 1 + 8)

        admin_variants = []
        for frame in probe_frames:
            message = decode_toradio_frame(frame)
            admin_message = admin_pb2.AdminMessage()
            admin_message.ParseFromString(message.packet.decoded.payload)
            admin_variants.append(admin_message.WhichOneof("payload_variant"))

        self.assertIn("get_owner_request", admin_variants)
        self.assertGreaterEqual(admin_variants.count("get_channel_request"), 8)

    def test_proxy_arbitrates_control_writes_and_broadcasts_serial_data(self) -> None:
        owner_frame = make_admin_write_frame()
        denied_frame = make_admin_write_frame()
        radio_chunk = make_fromradio_text_frame(b"radio-broadcast")

        with socket.create_connection(("127.0.0.1", self.port), timeout=1.0) as client_a:
            with socket.create_connection(("127.0.0.1", self.port), timeout=1.0) as client_b:
                client_a.settimeout(1.0)
                client_b.settimeout(1.0)

                wait_until(lambda: self.read_status().get("client_count") == 2)

                client_a.sendall(owner_frame)
                wait_until(lambda: non_probe_writes(self.fake_serial.writes) == [owner_frame])

                client_b.sendall(denied_frame)
                denied_message = client_b.recv(1024)

                self.assertIn(b"[broker] control session busy", denied_message)
                self.assertEqual(non_probe_writes(self.fake_serial.writes), [owner_frame])

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
                self.assertEqual(status["config_file"], self.config_file)
                self.assertTrue(str(status["control_owner"]).startswith("127.0.0.1:"))

    def test_proxy_recovers_from_unexpected_serial_exception(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        try:
            status_file = str(pathlib.Path(temp_dir.name) / "proxy-status.json")
            crashy = CrashySerialHandle()
            replacement = FakeSerialHandle()
            proxy = MultiHandleMeshtasticProxy(
                handles=[crashy, replacement],
                serial_port="fake-serial",
                baudrate=115200,
                listen_host="127.0.0.1",
                listen_port=0,
                reconnect_delay=0.01,
                status_file=status_file,
            )

            serial_thread = threading.Thread(target=proxy.serial_reader_loop, daemon=True)
            serial_thread.start()

            wait_until(lambda: proxy.open_count >= 2)
            wait_until(lambda: proxy.serial_ready.is_set())

            proxy.stop()
            serial_thread.join(timeout=1.0)

            self.assertFalse(serial_thread.is_alive())
            self.assertTrue(crashy.closed)
            self.assertTrue(replacement.closed)
        finally:
            temp_dir.cleanup()

    def test_proxy_runs_radio_packet_plugin_and_hot_reloads_on_change(self) -> None:
        output_file = pathlib.Path(self.temp_dir.name) / "plugin-output.txt"
        plugin_path = self.plugins_dir / "TEXT_MESSAGE_APP.handler.py"
        plugin_path.write_text(
            "def handle_packet(event, api):\n"
            f"    with open({str(output_file)!r}, 'a', encoding='utf-8') as handle:\n"
            "        handle.write('v1:' + event['payload'].decode('utf-8') + '\\n')\n",
            encoding="utf-8",
        )

        self.fake_serial.inject_read(make_fromradio_text_frame(b"first"))
        wait_until(lambda: output_file.exists() and "v1:first" in output_file.read_text(encoding="utf-8"))

        time.sleep(0.02)
        plugin_path.write_text(
            "def handle_packet(event, api):\n"
            f"    with open({str(output_file)!r}, 'a', encoding='utf-8') as handle:\n"
            "        handle.write('v2:' + event['payload'].decode('utf-8').upper() + '\\n')\n",
            encoding="utf-8",
        )

        self.fake_serial.inject_read(make_fromradio_text_frame(b"second"))
        wait_until(lambda: "v2:SECOND" in output_file.read_text(encoding="utf-8"))

        lines = output_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines, ["v1:first", "v2:SECOND"])

    def test_proxy_runs_client_call_plugin_for_forwarded_packet(self) -> None:
        output_file = pathlib.Path(self.temp_dir.name) / "client-plugin.txt"
        plugin_path = self.plugins_dir / "TEXT_MESSAGE_APP.handler.py"
        plugin_path.write_text(
            "def handle_client_call(event, api):\n"
            f"    with open({str(output_file)!r}, 'a', encoding='utf-8') as handle:\n"
            "        handle.write(event['client_id'] + ':' + event['payload'].decode('utf-8') + '\\n')\n",
            encoding="utf-8",
        )

        with socket.create_connection(("127.0.0.1", self.port), timeout=1.0) as client:
            client.sendall(make_text_frame())

        wait_until(lambda: output_file.exists())
        logged = output_file.read_text(encoding="utf-8").strip()
        self.assertRegex(logged, r"^client-\d+:hello$")

    def test_plugin_handler_errors_are_logged_and_do_not_break_proxy(self) -> None:
        plugin_path = self.plugins_dir / "TEXT_MESSAGE_APP.handler.py"
        plugin_path.write_text(
            "def handle_packet(event, api):\n"
            "    raise RuntimeError('boom from plugin')\n",
            encoding="utf-8",
        )

        with socket.create_connection(("127.0.0.1", self.port), timeout=1.0) as client:
            client.settimeout(1.0)
            with self.assertLogs("meshtastic_proxy", level="ERROR") as logs:
                self.fake_serial.inject_read(make_fromradio_text_frame(b"still-broadcast"))
                received = client.recv(1024)

        self.assertEqual(received, make_fromradio_text_frame(b"still-broadcast"))
        self.assertIn("plugin handler failed", "\n".join(logs.output))

    def test_proxy_drops_malformed_radio_bytes_without_poisoning_clients(self) -> None:
        valid_frame = make_fromradio_text_frame(b"healthy")

        with socket.create_connection(("127.0.0.1", self.port), timeout=1.0) as client:
            client.settimeout(1.0)
            with self.assertLogs("meshtastic_proxy", level="WARNING") as logs:
                self.fake_serial.inject_read(b"garbage" + valid_frame)
                received = client.recv(1024)

        self.assertEqual(received, valid_frame)
        self.assertIn("dropping 7 malformed radio byte(s)", "\n".join(logs.output))
        wait_until(lambda: self.read_status().get("dropped_radio_bytes") == 7)
        status = self.read_status()
        self.assertEqual(status["dropped_radio_bytes"], 7)

    def test_plugin_tick_errors_are_logged_and_do_not_stop_tick_loop(self) -> None:
        output_file = pathlib.Path(self.temp_dir.name) / "tick-after-error.txt"
        plugin_path = self.plugins_dir / "256.handler.py"
        plugin_path.write_text(
            "state = {'count': 0}\n"
            "def tick(event, api):\n"
            "    state['count'] += 1\n"
            "    if state['count'] == 1:\n"
            "        raise RuntimeError('tick boom')\n"
            f"    with open({str(output_file)!r}, 'a', encoding='utf-8') as handle:\n"
            "        handle.write('tick\\n')\n",
            encoding="utf-8",
        )

        tick_thread = threading.Thread(target=self.proxy.plugin_tick_loop, daemon=True)
        with self.assertLogs("meshtastic_proxy", level="ERROR") as logs:
            tick_thread.start()
            try:
                wait_until(lambda: output_file.exists() and output_file.read_text(encoding='utf-8').strip() == 'tick')
            finally:
                self.proxy.stop_event.set()
                tick_thread.join(timeout=1.0)

        self.assertFalse(tick_thread.is_alive())
        self.assertIn("plugin handler failed", "\n".join(logs.output))

    def test_plugin_tick_runs_periodically(self) -> None:
        output_file = pathlib.Path(self.temp_dir.name) / "tick-output.txt"
        plugin_path = self.plugins_dir / "256.handler.py"
        plugin_path.write_text(
            "def tick(event, api):\n"
            f"    with open({str(output_file)!r}, 'a', encoding='utf-8') as handle:\n"
            "        handle.write('tick\\n')\n",
            encoding="utf-8",
        )

        tick_thread = threading.Thread(target=self.proxy.plugin_tick_loop, daemon=True)
        tick_thread.start()
        try:
            wait_until(lambda: output_file.exists() and len(output_file.read_text(encoding='utf-8').splitlines()) >= 2)
        finally:
            self.proxy.stop_event.set()
            tick_thread.join(timeout=1.0)

        self.assertGreaterEqual(len(output_file.read_text(encoding="utf-8").splitlines()), 2)

    def test_client_reader_unblocks_when_proxy_stops_during_serial_outage(self) -> None:
        client_sock, peer_sock = socket.socketpair()
        try:
            client = self.proxy.register_client(client_sock, ("127.0.0.1", 6200))
            self.proxy.serial_ready.clear()

            thread = threading.Thread(target=self.proxy.client_reader_loop, args=(client,), daemon=True)
            thread.start()

            peer_sock.sendall(make_text_frame())
            wait_until(lambda: thread.is_alive())

            self.proxy.stop()
            thread.join(timeout=1.0)

            self.assertFalse(thread.is_alive())
        finally:
            peer_sock.close()

    def test_private_app_json_type_routes_to_specific_handler(self) -> None:
        output_file = pathlib.Path(self.temp_dir.name) / "private-app-output.txt"
        (self.plugins_dir / "PRIVATE_APP.chat.handler.py").write_text(
            "def handle_packet(event, api):\n"
            f"    with open({str(output_file)!r}, 'a', encoding='utf-8') as handle:\n"
            "        handle.write('typed:' + event['payload'].decode('utf-8') + '\\n')\n",
            encoding="utf-8",
        )
        (self.plugins_dir / "PRIVATE_APP.handler.py").write_text(
            "def handle_packet(event, api):\n"
            f"    with open({str(output_file)!r}, 'a', encoding='utf-8') as handle:\n"
            "        handle.write('generic\\n')\n",
            encoding="utf-8",
        )

        self.fake_serial.inject_read(make_fromradio_private_frame(b'{\"type\":\"chat\",\"text\":\"hi\"}'))
        wait_until(lambda: output_file.exists())

        self.assertEqual(output_file.read_text(encoding="utf-8").splitlines(), ['typed:{"type":"chat","text":"hi"}'])

    def test_private_app_falls_back_to_generic_handler_when_typed_handler_missing(self) -> None:
        output_file = pathlib.Path(self.temp_dir.name) / "private-app-generic.txt"
        (self.plugins_dir / "PRIVATE_APP.handler.py").write_text(
            "def handle_client_call(event, api):\n"
            f"    with open({str(output_file)!r}, 'a', encoding='utf-8') as handle:\n"
            "        handle.write('generic:' + event['payload'].decode('utf-8') + '\\n')\n",
            encoding="utf-8",
        )

        with socket.create_connection(("127.0.0.1", self.port), timeout=1.0) as client:
            client.sendall(make_private_frame(b'type=wormhole\nhello'))

        wait_until(lambda: output_file.exists())
        self.assertEqual(output_file.read_text(encoding="utf-8").strip(), "generic:type=wormhole\nhello")

    def test_direct_message_routes_to_first_word_handler_before_sender_or_generic(self) -> None:
        output_file = pathlib.Path(self.temp_dir.name) / "dm-routing.txt"
        dm_dir = self.plugins_dir / "DM"
        dm_dir.mkdir()
        (dm_dir / "hello.handler.py").write_text(
            "def handle_packet(event, api):\n"
            f"    with open({str(output_file)!r}, 'a', encoding='utf-8') as handle:\n"
            "        handle.write('word:' + str(event.get('dm_command')) + '\\n')\n",
            encoding="utf-8",
        )
        (dm_dir / "PEER.handler.py").write_text(
            "def handle_packet(event, api):\n"
            f"    with open({str(output_file)!r}, 'a', encoding='utf-8') as handle:\n"
            "        handle.write('sender\\n')\n",
            encoding="utf-8",
        )
        (dm_dir / "handler.py").write_text(
            "def handle_packet(event, api):\n"
            f"    with open({str(output_file)!r}, 'a', encoding='utf-8') as handle:\n"
            "        handle.write('generic\\n')\n",
            encoding="utf-8",
        )

        self.fake_serial.inject_read(make_fromradio_nodeinfo_frame("PEER", from_node=42))
        self.fake_serial.inject_read(make_fromradio_direct_text_frame(b"hello there", from_node=42, to_node=777))
        wait_until(lambda: output_file.exists())

        self.assertEqual(output_file.read_text(encoding="utf-8").splitlines(), ["word:hello"])

    def test_direct_message_handler_first_runs_before_specific_handler(self) -> None:
        output_file = pathlib.Path(self.temp_dir.name) / "dm-handler-first.txt"
        dm_dir = self.plugins_dir / "DM"
        dm_dir.mkdir()
        (dm_dir / "handler_first.py").write_text(
            "def handle_packet(event, api):\n"
            f"    with open({str(output_file)!r}, 'a', encoding='utf-8') as handle:\n"
            "        handle.write('first\\n')\n",
            encoding="utf-8",
        )
        (dm_dir / "hello.handler.py").write_text(
            "def handle_packet(event, api):\n"
            f"    with open({str(output_file)!r}, 'a', encoding='utf-8') as handle:\n"
            "        handle.write('word\\n')\n",
            encoding="utf-8",
        )

        self.fake_serial.inject_read(make_fromradio_direct_text_frame(b"hello there", from_node=42, to_node=777))
        wait_until(lambda: output_file.exists())

        self.assertEqual(output_file.read_text(encoding="utf-8").splitlines(), ["first"])

    def test_direct_message_falls_back_to_sender_short_name_then_generic(self) -> None:
        output_file = pathlib.Path(self.temp_dir.name) / "dm-sender.txt"
        dm_dir = self.plugins_dir / "DM"
        dm_dir.mkdir()
        (dm_dir / "PEER.handler.py").write_text(
            "def handle_packet(event, api):\n"
            f"    with open({str(output_file)!r}, 'a', encoding='utf-8') as handle:\n"
            "        handle.write(str(event.get('sender_short_name')) + '\\n')\n",
            encoding="utf-8",
        )

        self.fake_serial.inject_read(make_fromradio_nodeinfo_frame("PEER", from_node=51))
        self.fake_serial.inject_read(make_fromradio_direct_text_frame(b"unknown-command payload", from_node=51, to_node=777))
        wait_until(lambda: output_file.exists())

        self.assertEqual(output_file.read_text(encoding="utf-8").splitlines(), ["PEER"])

    def test_direct_message_chain_can_continue_into_dm_mode_with_rewritten_message(self) -> None:
        pathlib.Path(self.config_file).write_text("dm_mode=work\n", encoding="utf-8")
        output_file = pathlib.Path(self.temp_dir.name) / "dm-mode-chain.txt"
        dm_dir = self.plugins_dir / "DM"
        dm_dir.mkdir()
        dm_mode_dir = self.plugins_dir / "DM_work"
        dm_mode_dir.mkdir()
        (dm_dir / "handler.py").write_text(
            "def handle_packet(event, api):\n"
            "    message = api['mesh_pb2'].FromRadio()\n"
            "    message.CopyFrom(event['message'])\n"
            "    message.packet.decoded.payload = b'clean request'\n"
            "    return {'continue_chain': True, 'message': message}\n",
            encoding="utf-8",
        )
        (dm_mode_dir / "clean.handler.py").write_text(
            "def handle_packet(event, api):\n"
            f"    with open({str(output_file)!r}, 'a', encoding='utf-8') as handle:\n"
            "        handle.write(event['payload'].decode('utf-8') + ':' + str(event.get('dm_command')) + '\\n')\n",
            encoding="utf-8",
        )

        self.fake_serial.inject_read(make_fromradio_direct_text_frame(b"dirty request", from_node=42, to_node=777))
        wait_until(lambda: output_file.exists())

        self.assertEqual(output_file.read_text(encoding="utf-8").splitlines(), ["clean request:clean"])

    def test_direct_message_plugin_hot_reloads_under_dm_directory(self) -> None:
        output_file = pathlib.Path(self.temp_dir.name) / "dm-reload.txt"
        dm_dir = self.plugins_dir / "DM"
        dm_dir.mkdir()
        plugin_path = dm_dir / "handler.py"
        plugin_path.write_text(
            "def handle_packet(event, api):\n"
            f"    with open({str(output_file)!r}, 'a', encoding='utf-8') as handle:\n"
            "        handle.write('v1:' + event['payload'].decode('utf-8') + '\\n')\n",
            encoding="utf-8",
        )

        self.fake_serial.inject_read(make_fromradio_direct_text_frame(b"first", from_node=70, to_node=777))
        wait_until(lambda: output_file.exists() and "v1:first" in output_file.read_text(encoding="utf-8"))

        time.sleep(0.02)
        plugin_path.write_text(
            "def handle_packet(event, api):\n"
            f"    with open({str(output_file)!r}, 'a', encoding='utf-8') as handle:\n"
            "        handle.write('v2:' + event['payload'].decode('utf-8').upper() + '\\n')\n",
            encoding="utf-8",
        )

        self.fake_serial.inject_read(make_fromradio_direct_text_frame(b"second", from_node=70, to_node=777))
        wait_until(lambda: "v2:SECOND" in output_file.read_text(encoding="utf-8"))

        self.assertEqual(output_file.read_text(encoding="utf-8").splitlines(), ["v1:first", "v2:SECOND"])

    def test_direct_message_picks_up_new_handler_created_after_previous_call(self) -> None:
        output_file = pathlib.Path(self.temp_dir.name) / "dm-created.txt"
        dm_dir = self.plugins_dir / "DM"
        dm_dir.mkdir()
        (dm_dir / "handler.py").write_text(
            "def handle_packet(event, api):\n"
            f"    with open({str(output_file)!r}, 'a', encoding='utf-8') as handle:\n"
            "        handle.write('generic:' + event['payload'].decode('utf-8') + '\\n')\n",
            encoding="utf-8",
        )

        self.fake_serial.inject_read(make_fromradio_direct_text_frame(b"hello one", from_node=70, to_node=777))
        wait_until(lambda: output_file.exists() and "generic:hello one" in output_file.read_text(encoding="utf-8"))

        time.sleep(0.02)
        (dm_dir / "hello.handler.py").write_text(
            "def handle_packet(event, api):\n"
            f"    with open({str(output_file)!r}, 'a', encoding='utf-8') as handle:\n"
            "        handle.write('word:' + event['payload'].decode('utf-8') + '\\n')\n",
            encoding="utf-8",
        )

        self.fake_serial.inject_read(make_fromradio_direct_text_frame(b"hello two", from_node=70, to_node=777))
        wait_until(lambda: "word:hello two" in output_file.read_text(encoding="utf-8"))

        self.assertEqual(
            output_file.read_text(encoding="utf-8").splitlines(),
            ["generic:hello one", "word:hello two"],
        )

    def test_direct_message_falls_back_when_specific_handler_is_deleted(self) -> None:
        output_file = pathlib.Path(self.temp_dir.name) / "dm-deleted.txt"
        dm_dir = self.plugins_dir / "DM"
        dm_dir.mkdir()
        generic_path = dm_dir / "handler.py"
        specific_path = dm_dir / "hello.handler.py"
        generic_path.write_text(
            "def handle_packet(event, api):\n"
            f"    with open({str(output_file)!r}, 'a', encoding='utf-8') as handle:\n"
            "        handle.write('generic:' + event['payload'].decode('utf-8') + '\\n')\n",
            encoding="utf-8",
        )
        specific_path.write_text(
            "def handle_packet(event, api):\n"
            f"    with open({str(output_file)!r}, 'a', encoding='utf-8') as handle:\n"
            "        handle.write('word:' + event['payload'].decode('utf-8') + '\\n')\n",
            encoding="utf-8",
        )

        self.fake_serial.inject_read(make_fromradio_direct_text_frame(b"hello one", from_node=70, to_node=777))
        wait_until(lambda: output_file.exists() and "word:hello one" in output_file.read_text(encoding="utf-8"))

        time.sleep(0.02)
        specific_path.unlink()

        self.fake_serial.inject_read(make_fromradio_direct_text_frame(b"hello two", from_node=70, to_node=777))
        wait_until(lambda: "generic:hello two" in output_file.read_text(encoding="utf-8"))

        self.assertEqual(
            output_file.read_text(encoding="utf-8").splitlines(),
            ["word:hello one", "generic:hello two"],
        )

    def test_channel_plugin_routes_by_channel_name_and_local_mention(self) -> None:
        output_file = pathlib.Path(self.temp_dir.name) / "chan-routing.txt"
        chan_dir = self.plugins_dir / "CHAN_Friends"
        chan_dir.mkdir()
        (chan_dir / "ping.handler.py").write_text(
            "def handle_packet(event, api):\n"
            f"    with open({str(output_file)!r}, 'a', encoding='utf-8') as handle:\n"
            "        handle.write(str(event.get('channel_name')) + ':' + str(event.get('channel_command')) + '\\n')\n",
            encoding="utf-8",
        )

        self.fake_serial.inject_read(make_fromradio_admin_owner_response_frame("UDO1"))
        self.fake_serial.inject_read(make_fromradio_admin_channel_response_frame("Friends", channel_num=2, index=1))
        self.fake_serial.inject_read(make_fromradio_channel_text_frame(b"@UDO1 ping now", from_node=55, channel_num=2))
        wait_until(lambda: output_file.exists())

        self.assertEqual(output_file.read_text(encoding="utf-8").splitlines(), ["Friends:ping"])

    def test_channel_plugin_does_not_fire_without_local_name_prefix_except_alltraffic(self) -> None:
        output_file = pathlib.Path(self.temp_dir.name) / "chan-alltraffic.txt"
        chan_dir = self.plugins_dir / "CHAN_Friends"
        chan_dir.mkdir()
        (chan_dir / "handler_alltraffic.py").write_text(
            "def handle_packet(event, api):\n"
            f"    with open({str(output_file)!r}, 'a', encoding='utf-8') as handle:\n"
            "        handle.write('all:' + event['payload'].decode('utf-8') + '\\n')\n"
            "    return {'continue_chain': True}\n",
            encoding="utf-8",
        )
        (chan_dir / "ping.handler.py").write_text(
            "def handle_packet(event, api):\n"
            f"    with open({str(output_file)!r}, 'a', encoding='utf-8') as handle:\n"
            "        handle.write('cmd\\n')\n",
            encoding="utf-8",
        )

        self.fake_serial.inject_read(make_fromradio_admin_owner_response_frame("UDO1"))
        self.fake_serial.inject_read(make_fromradio_admin_channel_response_frame("Friends", channel_num=2, index=1))
        self.fake_serial.inject_read(make_fromradio_channel_text_frame(b"ping now", from_node=55, channel_num=2))
        wait_until(lambda: output_file.exists())

        self.assertEqual(output_file.read_text(encoding="utf-8").splitlines(), ["all:ping now"])

    def test_channel_handler_first_and_generic_use_command_after_local_prefix(self) -> None:
        output_file = pathlib.Path(self.temp_dir.name) / "chan-order.txt"
        chan_dir = self.plugins_dir / "CHAN_Friends"
        chan_dir.mkdir()
        (chan_dir / "handler_first.py").write_text(
            "def handle_packet(event, api):\n"
            "    return {'continue_chain': True}\n",
            encoding="utf-8",
        )
        (chan_dir / "handler.py").write_text(
            "def handle_packet(event, api):\n"
            f"    with open({str(output_file)!r}, 'a', encoding='utf-8') as handle:\n"
            "        handle.write(str(event.get('channel_command')) + '\\n')\n",
            encoding="utf-8",
        )

        self.fake_serial.inject_read(make_fromradio_admin_owner_response_frame("UDO1"))
        self.fake_serial.inject_read(make_fromradio_admin_channel_response_frame("Friends", channel_num=2, index=1))
        self.fake_serial.inject_read(make_fromradio_channel_text_frame(b"UDO1: hello there", from_node=55, channel_num=2))
        wait_until(lambda: output_file.exists())

        self.assertEqual(output_file.read_text(encoding="utf-8").splitlines(), ["hello"])

    def test_channel_plugins_are_blocked_on_public_primary_by_default(self) -> None:
        output_file = pathlib.Path(self.temp_dir.name) / "chan-public-primary.txt"
        chan_dir = self.plugins_dir / "CHAN_Public"
        chan_dir.mkdir()
        (chan_dir / "handler_alltraffic.py").write_text(
            "def handle_packet(event, api):\n"
            f"    with open({str(output_file)!r}, 'a', encoding='utf-8') as handle:\n"
            "        handle.write('should-not-run\\n')\n",
            encoding="utf-8",
        )

        self.fake_serial.inject_read(make_fromradio_admin_owner_response_frame("UDO1"))
        self.fake_serial.inject_read(
            make_fromradio_admin_channel_response_frame("Public", channel_num=2, index=0, psk=b"\x01")
        )
        self.fake_serial.inject_read(make_fromradio_channel_text_frame(b"hello mesh", from_node=55, channel_num=2))
        time.sleep(0.1)

        self.assertFalse(output_file.exists())

    def test_channel_plugins_can_be_enabled_on_public_primary_via_config(self) -> None:
        pathlib.Path(self.config_file).write_text("channel_plugins_allow_public_primary=yes\n", encoding="utf-8")
        output_file = pathlib.Path(self.temp_dir.name) / "chan-public-primary-enabled.txt"
        chan_dir = self.plugins_dir / "CHAN_Public"
        chan_dir.mkdir()
        (chan_dir / "handler_alltraffic.py").write_text(
            "def handle_packet(event, api):\n"
            f"    with open({str(output_file)!r}, 'a', encoding='utf-8') as handle:\n"
            "        handle.write(str(event.get('channel_name')) + '\\n')\n",
            encoding="utf-8",
        )

        self.fake_serial.inject_read(make_fromradio_admin_owner_response_frame("UDO1"))
        self.fake_serial.inject_read(
            make_fromradio_admin_channel_response_frame("Public", channel_num=2, index=0, psk=b"\x01")
        )
        self.fake_serial.inject_read(make_fromradio_channel_text_frame(b"hello mesh", from_node=55, channel_num=2))
        wait_until(lambda: output_file.exists())

        self.assertEqual(output_file.read_text(encoding="utf-8").splitlines(), ["Public"])


class StoreForwardPluginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.status_file = str(pathlib.Path(self.temp_dir.name) / "proxy-status.json")
        self.plugins_dir = pathlib.Path(self.temp_dir.name) / "plugins"
        self.plugins_dir.mkdir()
        for plugin_name in ("STORE_FORWARD_APP.handler.py", "TEXT_MESSAGE_APP.handler.py"):
            shutil.copy2(REPO_ROOT / "plugins" / plugin_name, self.plugins_dir / plugin_name)

        self.fake_serial = FakeSerialHandle()
        self.proxy = LoopbackMeshtasticProxy(
            fake_serial=self.fake_serial,
            serial_port="fake-serial",
            baudrate=115200,
            listen_host="127.0.0.1",
            listen_port=0,
            reconnect_delay=0.01,
            status_file=self.status_file,
            plugins_dir=str(self.plugins_dir),
            tick_interval=0.05,
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

    def restart_proxy(self) -> None:
        self.proxy.stop()
        self.accept_thread.join(timeout=1.0)
        self.serial_thread.join(timeout=1.0)
        self.fake_serial = FakeSerialHandle()
        self.proxy = LoopbackMeshtasticProxy(
            fake_serial=self.fake_serial,
            serial_port="fake-serial",
            baudrate=115200,
            listen_host="127.0.0.1",
            listen_port=0,
            reconnect_delay=0.01,
            status_file=self.status_file,
            plugins_dir=str(self.plugins_dir),
            tick_interval=0.05,
        )
        self.proxy.start_server()
        self.port = self.proxy.server_socket.getsockname()[1]
        self.serial_thread = threading.Thread(target=self.proxy.serial_reader_loop, daemon=True)
        self.accept_thread = threading.Thread(target=self.proxy.accept_loop, daemon=True)
        self.serial_thread.start()
        self.accept_thread.start()
        wait_until(lambda: self.proxy.serial_ready.is_set())

    def test_store_forward_handler_replies_to_ping_without_forwarding_to_serial(self) -> None:
        with socket.create_connection(("127.0.0.1", self.port), timeout=1.0) as client:
            client.settimeout(1.0)
            client.sendall(make_storeforward_client_frame(storeforward_pb2.StoreAndForward.CLIENT_PING))
            frames = recv_fromradio_frames(client, expected_count=1)

        self.assertEqual(non_probe_writes(self.fake_serial.writes), [])
        response = storeforward_pb2.StoreAndForward()
        response.ParseFromString(frames[0].packet.decoded.payload)
        self.assertEqual(frames[0].packet.decoded.portnum, portnums_pb2.STORE_FORWARD_APP)
        self.assertEqual(response.rr, storeforward_pb2.StoreAndForward.ROUTER_PONG)

    def test_store_forward_handler_replies_to_mesh_request_through_serial(self) -> None:
        self.fake_serial.inject_read(make_fromradio_storeforward_request_frame(storeforward_pb2.StoreAndForward.CLIENT_PING, from_node=91))
        wait_until(lambda: len(non_probe_writes(self.fake_serial.writes)) == 1)

        reply = decode_toradio_frame(non_probe_writes(self.fake_serial.writes)[0])
        response = storeforward_pb2.StoreAndForward()
        response.ParseFromString(reply.packet.decoded.payload)

        self.assertEqual(reply.packet.to, 91)
        self.assertEqual(reply.packet.decoded.portnum, portnums_pb2.STORE_FORWARD_APP)
        self.assertEqual(response.rr, storeforward_pb2.StoreAndForward.ROUTER_PONG)

    def test_store_forward_handler_reports_stats_and_replays_archived_messages(self) -> None:
        message_dir = self.proxy.plugin_state_dir / "TEXT_MESSAGE_APP" / "messages"
        event_dir = self.proxy.plugin_state_dir / "TEXT_MESSAGE_APP" / "events"
        self.fake_serial.inject_read(make_fromradio_text_frame(b"alpha"))
        self.fake_serial.inject_read(make_fromradio_text_frame(b"beta"))
        wait_until(
            lambda: message_dir.exists()
            and len(list(message_dir.glob("*.json"))) == 2
            and event_dir.exists()
            and len(list(event_dir.glob("*.jsonl"))) == 1
        )

        with socket.create_connection(("127.0.0.1", self.port), timeout=1.0) as client:
            client.settimeout(1.0)

            client.sendall(make_storeforward_client_frame(storeforward_pb2.StoreAndForward.CLIENT_STATS))
            stats_frame = recv_fromradio_frames(client, expected_count=1)[0]
            stats_response = storeforward_pb2.StoreAndForward()
            stats_response.ParseFromString(stats_frame.packet.decoded.payload)

            self.assertEqual(stats_response.rr, storeforward_pb2.StoreAndForward.ROUTER_STATS)
            self.assertEqual(stats_response.stats.messages_total, 2)
            self.assertEqual(stats_response.stats.messages_saved, 2)
            self.assertGreaterEqual(stats_response.stats.requests, 1)

            client.sendall(
                make_storeforward_client_frame(
                    storeforward_pb2.StoreAndForward.CLIENT_HISTORY,
                    history_messages=10,
                    window=60,
                )
            )
            history_frames = recv_fromradio_frames(client, expected_count=3)

        self.assertEqual(non_probe_writes(self.fake_serial.writes), [])

        summary = storeforward_pb2.StoreAndForward()
        summary.ParseFromString(history_frames[0].packet.decoded.payload)
        self.assertEqual(summary.rr, storeforward_pb2.StoreAndForward.ROUTER_HISTORY)
        self.assertEqual(summary.history.history_messages, 2)

        text_payloads: list[bytes] = []
        text_rrs: list[int] = []
        for frame in history_frames[1:]:
            response = storeforward_pb2.StoreAndForward()
            response.ParseFromString(frame.packet.decoded.payload)
            text_rrs.append(int(response.rr))
            text_payloads.append(bytes(response.text))

        self.assertEqual(text_payloads, [b"alpha", b"beta"])
        self.assertEqual(
            text_rrs,
            [
                int(storeforward_pb2.StoreAndForward.ROUTER_TEXT_BROADCAST),
                int(storeforward_pb2.StoreAndForward.ROUTER_TEXT_BROADCAST),
            ],
        )

    def test_store_forward_history_survives_proxy_restart_via_plugin_storage(self) -> None:
        self.fake_serial.inject_read(make_fromradio_text_frame(b"persisted"))
        message_dir = self.proxy.plugin_state_dir / "TEXT_MESSAGE_APP" / "messages"
        event_dir = self.proxy.plugin_state_dir / "TEXT_MESSAGE_APP" / "events"
        wait_until(
            lambda: message_dir.exists()
            and len(list(message_dir.glob('*.json'))) == 1
            and event_dir.exists()
            and len(list(event_dir.glob("*.jsonl"))) == 1
        )

        self.restart_proxy()

        with socket.create_connection(("127.0.0.1", self.port), timeout=1.0) as client:
            client.settimeout(1.0)
            client.sendall(
                make_storeforward_client_frame(
                    storeforward_pb2.StoreAndForward.CLIENT_HISTORY,
                    history_messages=5,
                    window=60,
                )
            )
            frames = recv_fromradio_frames(client, expected_count=2)

        summary = storeforward_pb2.StoreAndForward()
        summary.ParseFromString(frames[0].packet.decoded.payload)
        replay = storeforward_pb2.StoreAndForward()
        replay.ParseFromString(frames[1].packet.decoded.payload)

        self.assertEqual(summary.rr, storeforward_pb2.StoreAndForward.ROUTER_HISTORY)
        self.assertEqual(summary.history.history_messages, 1)
        self.assertEqual(replay.text, b"persisted")

    def test_text_message_store_is_content_addressed_by_hash(self) -> None:
        message_dir = self.proxy.plugin_state_dir / "TEXT_MESSAGE_APP" / "messages"
        event_dir = self.proxy.plugin_state_dir / "TEXT_MESSAGE_APP" / "events"
        self.fake_serial.inject_read(make_fromradio_text_frame(b"same"))
        self.fake_serial.inject_read(make_fromradio_text_frame(b"same"))
        wait_until(
            lambda: message_dir.exists()
            and len(list(message_dir.glob("*.json"))) == 1
            and event_dir.exists()
            and sum(len(path.read_text(encoding="utf-8").splitlines()) for path in event_dir.glob("*.jsonl")) == 2
        )

        files = list(message_dir.glob("*.json"))
        self.assertEqual(len(files), 1)
        record = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(record["payload_text"], "same")
        self.assertEqual(record["seen_count"], 2)

    def test_store_forward_replays_duplicate_messages_from_event_index(self) -> None:
        message_dir = self.proxy.plugin_state_dir / "TEXT_MESSAGE_APP" / "messages"
        event_dir = self.proxy.plugin_state_dir / "TEXT_MESSAGE_APP" / "events"
        self.fake_serial.inject_read(make_fromradio_text_frame(b"dup"))
        self.fake_serial.inject_read(make_fromradio_text_frame(b"dup"))
        wait_until(
            lambda: message_dir.exists()
            and len(list(message_dir.glob("*.json"))) == 1
            and event_dir.exists()
            and sum(len(path.read_text(encoding="utf-8").splitlines()) for path in event_dir.glob("*.jsonl")) == 2
        )
        with socket.create_connection(("127.0.0.1", self.port), timeout=1.0) as client:
            client.settimeout(1.0)
            client.sendall(
                make_storeforward_client_frame(
                    storeforward_pb2.StoreAndForward.CLIENT_HISTORY,
                    history_messages=10,
                    window=60,
                )
            )
            frames = recv_fromradio_frames(client, expected_count=2)

        history_frames = [frame for frame in frames if frame.packet.decoded.portnum == portnums_pb2.STORE_FORWARD_APP]
        self.assertEqual(len(history_frames), 2)
        payloads = []
        for frame in history_frames[1:]:
            response = storeforward_pb2.StoreAndForward()
            response.ParseFromString(frame.packet.decoded.payload)
            payloads.append(bytes(response.text))
        self.assertEqual(payloads, [b"dup"])

    def test_store_forward_can_replay_duplicate_messages_when_enabled(self) -> None:
        config_dir = self.proxy.plugin_state_dir / "STORE_FORWARD_APP"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.json").write_text(json.dumps({"replay_duplicates": True}) + "\n", encoding="utf-8")

        message_dir = self.proxy.plugin_state_dir / "TEXT_MESSAGE_APP" / "messages"
        event_dir = self.proxy.plugin_state_dir / "TEXT_MESSAGE_APP" / "events"
        self.fake_serial.inject_read(make_fromradio_text_frame(b"dup"))
        self.fake_serial.inject_read(make_fromradio_text_frame(b"dup"))
        wait_until(
            lambda: message_dir.exists()
            and len(list(message_dir.glob("*.json"))) == 1
            and event_dir.exists()
            and sum(len(path.read_text(encoding="utf-8").splitlines()) for path in event_dir.glob("*.jsonl")) == 2
        )

        with socket.create_connection(("127.0.0.1", self.port), timeout=1.0) as client:
            client.settimeout(1.0)
            client.sendall(
                make_storeforward_client_frame(
                    storeforward_pb2.StoreAndForward.CLIENT_HISTORY,
                    history_messages=10,
                    window=60,
                )
            )
            frames = recv_fromradio_frames(client, expected_count=3)

        history_frames = [frame for frame in frames if frame.packet.decoded.portnum == portnums_pb2.STORE_FORWARD_APP]
        self.assertEqual(len(history_frames), 3)
        payloads = []
        for frame in history_frames[1:]:
            response = storeforward_pb2.StoreAndForward()
            response.ParseFromString(frame.packet.decoded.payload)
            payloads.append(bytes(response.text))
        self.assertEqual(payloads, [b"dup", b"dup"])

    def test_store_forward_tick_cleans_up_messages_older_than_30_days(self) -> None:
        message_dir = self.proxy.plugin_state_dir / "TEXT_MESSAGE_APP" / "messages"
        event_dir = self.proxy.plugin_state_dir / "TEXT_MESSAGE_APP" / "events"
        message_dir.mkdir(parents=True, exist_ok=True)
        event_dir.mkdir(parents=True, exist_ok=True)
        old_record = {
            "direct": False,
            "first_seen_ts": time.time() - (31 * 24 * 60 * 60),
            "hash": "old",
            "last_seen_ts": time.time() - (31 * 24 * 60 * 60),
            "packet_from": 1,
            "packet_id": 1,
            "packet_to": 0,
            "payload_hex": "6f6c64",
            "payload_text": "old",
            "seen_count": 1,
        }
        fresh_record = {
            "direct": False,
            "first_seen_ts": time.time(),
            "hash": "fresh",
            "last_seen_ts": time.time(),
            "packet_from": 2,
            "packet_id": 2,
            "packet_to": 0,
            "payload_hex": "6672657368",
            "payload_text": "fresh",
            "seen_count": 1,
        }
        (message_dir / "old.json").write_text(json.dumps(old_record) + "\n", encoding="utf-8")
        (message_dir / "fresh.json").write_text(json.dumps(fresh_record) + "\n", encoding="utf-8")
        (event_dir / "2000-01-01.jsonl").write_text(json.dumps({"hash": "old", "ts": old_record["last_seen_ts"]}) + "\n", encoding="utf-8")
        today = time.strftime("%Y-%m-%d", time.gmtime())
        (event_dir / f"{today}.jsonl").write_text(json.dumps({"hash": "fresh", "ts": fresh_record["last_seen_ts"]}) + "\n", encoding="utf-8")

        self.proxy.plugins.tick(self.proxy.build_plugin_api())

        wait_until(lambda: not (message_dir / "old.json").exists())
        self.assertTrue((message_dir / "fresh.json").exists())
        self.assertTrue((self.proxy.plugin_state_dir / "STORE_FORWARD_APP" / "cleanup.json").exists())

    def test_store_forward_tick_emits_heartbeat_when_enabled(self) -> None:
        config_dir = self.proxy.plugin_state_dir / "STORE_FORWARD_APP"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.json").write_text(
            json.dumps({"heartbeat_enabled": True, "heartbeat_interval_secs": 60, "heartbeat_secondary": True}) + "\n",
            encoding="utf-8",
        )

        self.proxy.plugins.tick(self.proxy.build_plugin_api())
        self.assertEqual(len(non_probe_writes(self.fake_serial.writes)), 1)

        heartbeat_write = decode_toradio_frame(non_probe_writes(self.fake_serial.writes)[0])
        self.assertEqual(heartbeat_write.packet.to, 0)
        self.assertEqual(heartbeat_write.packet.decoded.portnum, portnums_pb2.STORE_FORWARD_APP)
        heartbeat = storeforward_pb2.StoreAndForward()
        heartbeat.ParseFromString(heartbeat_write.packet.decoded.payload)
        self.assertEqual(heartbeat.rr, storeforward_pb2.StoreAndForward.ROUTER_HEARTBEAT)
        self.assertEqual(heartbeat.heartbeat.period, 60)
        self.assertEqual(heartbeat.heartbeat.secondary, 1)
        self.assertTrue((config_dir / "heartbeat.json").exists())

        self.proxy.plugins.tick(self.proxy.build_plugin_api())
        self.assertEqual(len(non_probe_writes(self.fake_serial.writes)), 1)

    def test_store_forward_skips_malformed_storage_and_logs_warning(self) -> None:
        message_dir = self.proxy.plugin_state_dir / "TEXT_MESSAGE_APP" / "messages"
        event_dir = self.proxy.plugin_state_dir / "TEXT_MESSAGE_APP" / "events"
        message_dir.mkdir(parents=True, exist_ok=True)
        event_dir.mkdir(parents=True, exist_ok=True)
        (message_dir / "broken.json").write_text("{not-json\n", encoding="utf-8")
        today = time.strftime("%Y-%m-%d", time.gmtime())
        (event_dir / f"{today}.jsonl").write_text('{"hash":"broken","ts":1}\nnot-json\n', encoding="utf-8")

        with self.assertLogs("meshtastic_proxy", level="WARNING") as logs:
            with socket.create_connection(("127.0.0.1", self.port), timeout=1.0) as client:
                client.settimeout(1.0)
                client.sendall(make_storeforward_client_frame(storeforward_pb2.StoreAndForward.CLIENT_STATS))
                frame = recv_fromradio_frames(client, expected_count=1)[0]

        response = storeforward_pb2.StoreAndForward()
        response.ParseFromString(frame.packet.decoded.payload)
        self.assertEqual(response.stats.messages_total, 0)
        self.assertIn("skipped malformed", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
