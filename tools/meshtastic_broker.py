import logging
import time
from dataclasses import dataclass, field

from meshtastic.protobuf import admin_pb2, mesh_pb2, portnums_pb2
from meshtastic.stream_interface import HEADER_LEN, MAX_TO_FROM_RADIO_SIZE, START1, START2


LOGGER = logging.getLogger("meshtastic_broker")
PROVISIONAL_CONTROL_LEASE_SECONDS = 10.0
ADMIN_SESSION_LEASE_SECONDS = 300.0


@dataclass
class ParseResult:
    raw_chunks: list[bytes] = field(default_factory=list)
    frames: list[bytes] = field(default_factory=list)


class FrameParser:
    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> ParseResult:
        result = ParseResult()
        raw_buffer = bytearray()

        def flush_raw() -> None:
            if raw_buffer:
                result.raw_chunks.append(bytes(raw_buffer))
                raw_buffer.clear()

        self._buffer.extend(data)

        while self._buffer:
            if self._buffer[0] != START1:
                raw_buffer.append(self._buffer.pop(0))
                continue

            if len(self._buffer) < 2:
                break

            if self._buffer[1] != START2:
                raw_buffer.append(self._buffer.pop(0))
                continue

            if len(self._buffer) < HEADER_LEN:
                break

            packet_len = (self._buffer[2] << 8) + self._buffer[3]
            if packet_len > MAX_TO_FROM_RADIO_SIZE:
                LOGGER.debug("dropping oversize frame header with payload length %s", packet_len)
                del self._buffer[0]
                continue

            total_len = HEADER_LEN + packet_len
            if len(self._buffer) < total_len:
                break

            flush_raw()
            result.frames.append(bytes(self._buffer[:total_len]))
            del self._buffer[:total_len]

        flush_raw()
        return result


def encode_frame(payload: bytes) -> bytes:
    payload_len = len(payload)
    return bytes([START1, START2, (payload_len >> 8) & 0xFF, payload_len & 0xFF]) + payload


def decode_toradio_frame(frame: bytes) -> mesh_pb2.ToRadio:
    message = mesh_pb2.ToRadio()
    message.ParseFromString(frame[HEADER_LEN:])
    return message


def decode_fromradio_frame(frame: bytes) -> mesh_pb2.FromRadio:
    message = mesh_pb2.FromRadio()
    message.ParseFromString(frame[HEADER_LEN:])
    return message


def is_control_request(message: mesh_pb2.ToRadio) -> bool:
    variant = message.WhichOneof("payload_variant")
    if variant in {"disconnect", "xmodemPacket"}:
        return True
    if variant != "packet":
        return False
    return is_control_mesh_packet(message.packet)


def is_control_mesh_packet(packet: mesh_pb2.MeshPacket) -> bool:
    if not packet.HasField("decoded"):
        return False
    if packet.decoded.portnum != portnums_pb2.ADMIN_APP:
        return False

    admin_message = admin_pb2.AdminMessage()
    try:
        admin_message.ParseFromString(packet.decoded.payload)
    except Exception:
        LOGGER.debug("ignoring undecodable ADMIN_APP client payload", exc_info=True)
        return False
    operation = admin_message.WhichOneof("payload_variant")
    if operation is None:
        return False
    return not operation.startswith("get_")


def control_denied_message(owner_label: str) -> bytes:
    return f"[broker] control session busy: currently held by {owner_label}\n".encode("utf-8")


@dataclass
class BrokerDecision:
    serial_chunks: list[bytes] = field(default_factory=list)
    direct_chunks: list[bytes] = field(default_factory=list)
    forwarded_frames: list[bytes] = field(default_factory=list)


@dataclass
class BrokerClientState:
    label: str
    parser: FrameParser = field(default_factory=FrameParser)


@dataclass
class ObservedRadioFrame:
    frame: bytes
    message: mesh_pb2.FromRadio


class MeshtasticBroker:
    def __init__(
        self,
        logger: logging.Logger | None = None,
        clock=None,
        provisional_control_timeout: float = PROVISIONAL_CONTROL_LEASE_SECONDS,
        admin_session_timeout: float = ADMIN_SESSION_LEASE_SECONDS,
    ) -> None:
        self.logger = logger or LOGGER
        self.clock = clock or time.monotonic
        self.provisional_control_timeout = provisional_control_timeout
        self.admin_session_timeout = admin_session_timeout
        self.clients: dict[str, BrokerClientState] = {}
        self.control_owner_id: str | None = None
        self.control_owner_confirmed = False
        self.control_owner_expires_at: float | None = None
        self.radio_parser = FrameParser()
        self.denied_control_frames = 0
        self.forwarded_control_frames = 0
        self.observed_admin_responses = 0
        self.last_session_passkey = ""
        self.last_session_passkey_seen_at: float | None = None
        self.last_admin_response_owner: str | None = None

    def register_client(self, client_id: str, label: str) -> None:
        self.clients[client_id] = BrokerClientState(label=label)

    def unregister_client(self, client_id: str) -> None:
        self.clients.pop(client_id, None)
        if self.control_owner_id == client_id:
            self.logger.info("released control session from %s", client_id)
            self._clear_control_owner()

    def handle_client_bytes(self, client_id: str, data: bytes) -> BrokerDecision:
        self._expire_control_owner_if_needed()
        state = self.clients.get(client_id)
        if state is None:
            self.register_client(client_id, client_id)
            state = self.clients[client_id]

        parsed = state.parser.feed(data)
        decision = BrokerDecision(serial_chunks=list(parsed.raw_chunks))

        for frame in parsed.frames:
            if self._should_forward_frame(client_id, frame):
                decision.serial_chunks.append(frame)
                decision.forwarded_frames.append(frame)
            else:
                owner_label = self._owner_label()
                decision.direct_chunks.append(control_denied_message(owner_label))

        return decision

    def observe_radio_bytes(self, data: bytes) -> list[ObservedRadioFrame]:
        self._expire_control_owner_if_needed()
        parsed = self.radio_parser.feed(data)
        observed: list[ObservedRadioFrame] = []
        for frame in parsed.frames:
            try:
                message = decode_fromradio_frame(frame)
            except Exception as exc:
                self.logger.debug("ignoring undecodable radio frame: %s", exc)
                continue
            self._observe_fromradio(message)
            observed.append(ObservedRadioFrame(frame=frame, message=message))
        return observed

    def _observe_fromradio(self, message: mesh_pb2.FromRadio) -> None:
        if not message.HasField("packet"):
            return
        packet = message.packet
        if not packet.HasField("decoded"):
            return
        if packet.decoded.portnum != portnums_pb2.ADMIN_APP:
            return

        admin_message = admin_pb2.AdminMessage()
        try:
            admin_message.ParseFromString(packet.decoded.payload)
        except Exception:
            self.logger.debug("ignoring undecodable ADMIN_APP radio payload", exc_info=True)
            return
        self.observed_admin_responses += 1
        self.last_admin_response_owner = self._owner_label() if self.control_owner_id is not None else None
        if admin_message.session_passkey:
            self.last_session_passkey = admin_message.session_passkey.hex()
            self.last_session_passkey_seen_at = self.clock()
            if self.control_owner_id is not None:
                self.control_owner_confirmed = True
                self.control_owner_expires_at = self.last_session_passkey_seen_at + self.admin_session_timeout
        elif self.control_owner_id is not None and self.control_owner_confirmed:
            self.control_owner_expires_at = self.clock() + self.admin_session_timeout

    def _should_forward_frame(self, client_id: str, frame: bytes) -> bool:
        self._expire_control_owner_if_needed()
        try:
            message = decode_toradio_frame(frame)
        except Exception as exc:
            self.logger.debug("allowing undecodable frame from %s: %s", client_id, exc)
            return True

        if not is_control_request(message):
            return True

        if self.control_owner_id is None:
            self._claim_control_owner(client_id)
            self.forwarded_control_frames += 1
            self.logger.info("control session claimed by %s", self._client_label(client_id))
            return True

        if self.control_owner_id != client_id:
            self.denied_control_frames += 1
            self.logger.info(
                "denied control request from %s while owned by %s",
                self._client_label(client_id),
                self._owner_label(),
            )
            return False

        self.forwarded_control_frames += 1
        if message.WhichOneof("payload_variant") == "disconnect":
            self.logger.info("control session released by %s", self._client_label(client_id))
            self._clear_control_owner()
            return True

        self._refresh_control_owner_lease()

        return True

    def snapshot(self) -> dict[str, object]:
        self._expire_control_owner_if_needed()
        expires_in = None
        if self.control_owner_expires_at is not None:
            expires_in = max(0.0, round(self.control_owner_expires_at - self.clock(), 3))
        return {
            "client_count": len(self.clients),
            "control_owner": self._owner_label() if self.control_owner_id is not None else None,
            "control_session_confirmed": self.control_owner_confirmed,
            "control_session_expires_in": expires_in,
            "denied_control_frames": self.denied_control_frames,
            "forwarded_control_frames": self.forwarded_control_frames,
            "observed_admin_responses": self.observed_admin_responses,
            "last_session_passkey": self.last_session_passkey or None,
            "last_admin_response_owner": self.last_admin_response_owner,
        }

    def _claim_control_owner(self, client_id: str) -> None:
        self.control_owner_id = client_id
        self.control_owner_confirmed = False
        self.control_owner_expires_at = self.clock() + self.provisional_control_timeout

    def _refresh_control_owner_lease(self) -> None:
        if self.control_owner_id is None:
            return
        timeout = self.admin_session_timeout if self.control_owner_confirmed else self.provisional_control_timeout
        self.control_owner_expires_at = self.clock() + timeout

    def _clear_control_owner(self) -> None:
        self.control_owner_id = None
        self.control_owner_confirmed = False
        self.control_owner_expires_at = None

    def _expire_control_owner_if_needed(self) -> None:
        if self.control_owner_id is None or self.control_owner_expires_at is None:
            return
        if self.clock() < self.control_owner_expires_at:
            return
        self.logger.info("control session expired for %s", self._owner_label())
        self._clear_control_owner()

    def _client_label(self, client_id: str) -> str:
        state = self.clients.get(client_id)
        return state.label if state is not None else client_id

    def _owner_label(self) -> str:
        if self.control_owner_id is None:
            return "unknown client"
        return self._client_label(self.control_owner_id)
