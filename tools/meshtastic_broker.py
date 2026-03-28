import logging
import re
import socket
import time
from dataclasses import dataclass, field

from meshtastic.protobuf import admin_pb2, mesh_pb2, portnums_pb2
from meshtastic.stream_interface import HEADER_LEN, MAX_TO_FROM_RADIO_SIZE, START1, START2


LOGGER = logging.getLogger("meshtastic_broker")
PROVISIONAL_CONTROL_LEASE_SECONDS = 10.0
ADMIN_SESSION_LEASE_SECONDS = 300.0
HOST_SESSION_LEASE_SECONDS = 120.0
MALFORMED_RADIO_LOG_INTERVAL_SECONDS = 5.0
UART_DEBUG_LOG_INTERVAL_SECONDS = 5.0
ANSI_ESCAPE_RE = re.compile(rb"\x1b\[[0-9;]*[A-Za-z]")
ANSI_CONTROL_FRAGMENT_RE = re.compile(rb"^(?:\x1b(?:\[[0-9;?]*[ -/]*[@-~]?)?)+$")
UART_DEBUG_BRACKETED_SUBSYSTEM_RE = re.compile(r"\[([^\]\s]+)\]")
UART_DEBUG_PIPE_SUBSYSTEM_RE = re.compile(r"^\S+\s+\|\s+([A-Za-z][\w.-]*)\b")


def strip_ansi_escape_sequences(data: bytes) -> bytes:
    return ANSI_ESCAPE_RE.sub(b"", data)


def looks_like_text_console_noise(data: bytes) -> bool:
    if not data:
        return False
    stripped = strip_ansi_escape_sequences(data)
    if not stripped:
        return False
    printable = sum(1 for byte in stripped if byte in b"\r\n\t" or 32 <= byte <= 126)
    alpha = sum(1 for byte in stripped if 65 <= byte <= 90 or 97 <= byte <= 122)
    return printable >= max(8, int(len(stripped) * 0.85)) and alpha >= 3


def raw_chunk_sample_text(data: bytes) -> str:
    stripped = strip_ansi_escape_sequences(data).strip()
    if not stripped:
        return ""
    sample = stripped[:80].decode("utf-8", errors="replace")
    return " ".join(sample.split())


def is_whitespace_only_chunk(data: bytes) -> bool:
    stripped = strip_ansi_escape_sequences(data)
    return bool(stripped) and all(byte in b"\r\n\t " for byte in stripped)


def is_ansi_control_fragment(data: bytes) -> bool:
    if not data or not data.startswith(b"\x1b"):
        return False
    if all(byte == 0x1B or byte in b"\r\n\t" or 32 <= byte <= 126 for byte in data):
        return True
    if not strip_ansi_escape_sequences(data):
        return True
    return bool(ANSI_CONTROL_FRAGMENT_RE.fullmatch(data))


def uart_debug_subsystem(sample_text: str) -> str:
    match = UART_DEBUG_BRACKETED_SUBSYSTEM_RE.search(sample_text)
    if match:
        return match.group(1)
    match = UART_DEBUG_PIPE_SUBSYSTEM_RE.search(sample_text)
    if match:
        return match.group(1)
    return "general"


@dataclass
class ParseResult:
    text_chunks: list[bytes] = field(default_factory=list)
    raw_chunks: list[bytes] = field(default_factory=list)
    frames: list[bytes] = field(default_factory=list)


class FrameParser:
    def __init__(self, *, strip_text_prefix: bool = False) -> None:
        self._buffer = bytearray()
        self.strip_text_prefix = strip_text_prefix

    def _extract_text_prefix(self, buf: bytearray, pos: int) -> tuple[bytes | None, int, bool]:
        if not self.strip_text_prefix or buf[pos] == START1:
            return None, pos, False

        next_start = buf.find(bytes([START1]), pos)
        line_end_candidates = [index for index in (buf.find(b"\n", pos), buf.find(b"\r", pos)) if index != -1]
        next_line_end = min(line_end_candidates) if line_end_candidates else -1

        if next_line_end != -1 and (next_start == -1 or next_line_end < next_start):
            end = next_line_end + 1
            candidate = bytes(buf[pos:end])
            if is_whitespace_only_chunk(candidate) or is_ansi_control_fragment(candidate) or looks_like_text_console_noise(candidate):
                return candidate, end, False
            return None, pos, False

        if next_start != -1 and next_start > pos:
            candidate = bytes(buf[pos:next_start])
            if is_whitespace_only_chunk(candidate) or is_ansi_control_fragment(candidate) or looks_like_text_console_noise(candidate):
                return candidate, next_start, False
            return None, pos, False

        candidate = bytes(buf[pos:])
        if is_whitespace_only_chunk(candidate) or is_ansi_control_fragment(candidate) or looks_like_text_console_noise(candidate):
            return None, pos, True
        return None, pos, False

    def feed(self, data: bytes) -> ParseResult:
        result = ParseResult()
        raw_buffer = bytearray()

        def flush_raw() -> None:
            if raw_buffer:
                result.raw_chunks.append(bytes(raw_buffer))
                raw_buffer.clear()

        self._buffer.extend(data)
        buf = self._buffer
        pos = 0

        while pos < len(buf):
            text_chunk, new_pos, wait_for_more = self._extract_text_prefix(buf, pos)
            if wait_for_more:
                break
            if text_chunk is not None:
                flush_raw()
                result.text_chunks.append(text_chunk)
                pos = new_pos
                continue

            if buf[pos] != START1:
                raw_buffer.append(buf[pos])
                pos += 1
                continue

            if pos + 1 >= len(buf):
                break

            if buf[pos + 1] != START2:
                raw_buffer.append(buf[pos])
                pos += 1
                continue

            if pos + HEADER_LEN > len(buf):
                break

            packet_len = (buf[pos + 2] << 8) + buf[pos + 3]
            if packet_len > MAX_TO_FROM_RADIO_SIZE:
                LOGGER.debug("dropping oversize frame header with payload length %s", packet_len)
                pos += 1
                continue

            total_len = HEADER_LEN + packet_len
            if pos + total_len > len(buf):
                break

            flush_raw()
            result.frames.append(bytes(buf[pos : pos + total_len]))
            pos += total_len

        del self._buffer[:pos]
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


@dataclass(frozen=True)
class ForwardDecision:
    action: str
    reason: str | None = None


@dataclass
class BrokerClientState:
    label: str
    parser: FrameParser = field(default_factory=FrameParser)


@dataclass
class ObservedRadioFrame:
    frame: bytes
    message: mesh_pb2.FromRadio


@dataclass
class SerialDebugThrottleState:
    last_log_at: float = float("-inf")
    suppressed_chunks: int = 0
    suppressed_bytes: int = 0
    latest_sample: str = ""


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
        self.host_session_owner_id: str | None = None
        self.host_session_expires_at: float | None = None
        self.radio_parser = FrameParser(strip_text_prefix=True)
        self.denied_control_frames = 0
        self.forwarded_control_frames = 0
        self.observed_admin_responses = 0
        self.dropped_radio_bytes = 0
        self.ignored_serial_debug_bytes = 0
        self.invalid_radio_frames = 0
        self.last_session_passkey = ""
        self.last_session_passkey_seen_at: float | None = None
        self.last_admin_response_owner: str | None = None
        self._last_malformed_radio_log_at = float("-inf")
        self._suppressed_malformed_radio_chunks = 0
        self._suppressed_malformed_radio_bytes = 0
        self._last_serial_debug_log_at = float("-inf")
        self._suppressed_serial_debug_chunks = 0
        self._suppressed_serial_debug_bytes = 0
        self._serial_debug_throttle: dict[str, SerialDebugThrottleState] = {}

    def _log_uart_debug_chunk(self, chunk: bytes, now: float) -> None:
        sample_text = raw_chunk_sample_text(chunk)
        if not sample_text:
            return

        subsystem = uart_debug_subsystem(sample_text)
        state = self._serial_debug_throttle.setdefault(subsystem, SerialDebugThrottleState())
        chunk_len = len(chunk)
        if now - state.last_log_at < UART_DEBUG_LOG_INTERVAL_SECONDS:
            state.suppressed_chunks += 1
            state.suppressed_bytes += chunk_len
            state.latest_sample = sample_text
            return

        message = "uart debug[%s]: %s"
        args: tuple[object, ...] = (subsystem, sample_text)
        if state.suppressed_chunks:
            message += "; suppressed %s line(s) totaling %s byte(s); latest=%r"
            args += (state.suppressed_chunks, state.suppressed_bytes, state.latest_sample)
            state.suppressed_chunks = 0
            state.suppressed_bytes = 0
            state.latest_sample = ""
        self.logger.info(message, *args)
        state.last_log_at = now

    def _log_serial_debug_chunks(self, raw_chunks: list[bytes]) -> None:
        ignored_len = sum(len(chunk) for chunk in raw_chunks)
        if ignored_len <= 0:
            return

        now = self.clock()
        for chunk in raw_chunks:
            self._log_uart_debug_chunk(chunk, now)

        if now - self._last_serial_debug_log_at < MALFORMED_RADIO_LOG_INTERVAL_SECONDS:
            self._suppressed_serial_debug_chunks += len(raw_chunks)
            self._suppressed_serial_debug_bytes += ignored_len
            return

        suppressed_chunks = self._suppressed_serial_debug_chunks
        suppressed_bytes = self._suppressed_serial_debug_bytes
        self._suppressed_serial_debug_chunks = 0
        self._suppressed_serial_debug_bytes = 0
        self._last_serial_debug_log_at = now

        sample_text = raw_chunk_sample_text(raw_chunks[0]) or raw_chunks[0][:24].hex()
        message = "ignored %s serial debug byte(s) before frame sync; sample_text=%r"
        args: tuple[object, ...] = (ignored_len, sample_text)
        if suppressed_bytes:
            message += "; suppressed %s similar chunk(s) totaling %s byte(s)"
            args += (suppressed_chunks, suppressed_bytes)
        self.logger.info(message, *args)

    def _log_malformed_radio_chunks(self, raw_chunks: list[bytes]) -> None:
        dropped_len = sum(len(chunk) for chunk in raw_chunks)
        if dropped_len <= 0:
            return

        now = self.clock()
        if now - self._last_malformed_radio_log_at < MALFORMED_RADIO_LOG_INTERVAL_SECONDS:
            self._suppressed_malformed_radio_chunks += len(raw_chunks)
            self._suppressed_malformed_radio_bytes += dropped_len
            return

        suppressed_chunks = self._suppressed_malformed_radio_chunks
        suppressed_bytes = self._suppressed_malformed_radio_bytes
        self._suppressed_malformed_radio_chunks = 0
        self._suppressed_malformed_radio_bytes = 0
        self._last_malformed_radio_log_at = now

        message = "dropping %s malformed radio byte(s) before frame sync; sample=%s"
        args = (dropped_len, raw_chunks[0][:24].hex())
        if suppressed_bytes:
            message += "; suppressed %s similar chunk(s) totaling %s byte(s)"
            args += (suppressed_chunks, suppressed_bytes)
        self.logger.warning(message, *args)

    def register_client(self, client_id: str, label: str) -> None:
        self.clients[client_id] = BrokerClientState(label=label)

    def unregister_client(self, client_id: str) -> None:
        self.clients.pop(client_id, None)
        if self.control_owner_id == client_id:
            self.logger.info("released control session from %s", client_id)
            self._clear_control_owner()
        if self.host_session_owner_id == client_id:
            self.logger.info("released host session from %s", client_id)
            self._clear_host_session_owner()

    def handle_client_bytes(self, client_id: str, data: bytes) -> BrokerDecision:
        self._expire_control_owner_if_needed()
        self._expire_host_session_owner_if_needed()
        state = self.clients.get(client_id)
        if state is None:
            self.register_client(client_id, client_id)
            state = self.clients[client_id]

        parsed = state.parser.feed(data)
        decision = BrokerDecision(serial_chunks=list(parsed.raw_chunks))

        for frame in parsed.frames:
            forward = self._should_forward_frame(client_id, frame)
            if forward.action == "forward":
                decision.serial_chunks.append(frame)
                decision.forwarded_frames.append(frame)
            elif forward.action == "deny":
                owner_label = self._owner_label()
                decision.direct_chunks.append(control_denied_message(owner_label))

        return decision

    def observe_radio_bytes(self, data: bytes) -> list[ObservedRadioFrame]:
        self._expire_control_owner_if_needed()
        self._expire_host_session_owner_if_needed()
        parsed = self.radio_parser.feed(data)
        observed: list[ObservedRadioFrame] = []
        if parsed.text_chunks:
            self.ignored_serial_debug_bytes += sum(len(chunk) for chunk in parsed.text_chunks)
            self._log_serial_debug_chunks(parsed.text_chunks)
        if parsed.raw_chunks:
            self.dropped_radio_bytes += sum(len(chunk) for chunk in parsed.raw_chunks)
            self._log_malformed_radio_chunks(parsed.raw_chunks)
        for frame in parsed.frames:
            try:
                message = decode_fromradio_frame(frame)
            except Exception as exc:
                self.invalid_radio_frames += 1
                self.logger.warning("dropping undecodable radio frame: %s", exc)
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

    def _should_forward_frame(self, client_id: str, frame: bytes) -> ForwardDecision:
        self._expire_control_owner_if_needed()
        self._expire_host_session_owner_if_needed()
        try:
            message = decode_toradio_frame(frame)
        except Exception as exc:
            self.logger.debug("allowing undecodable frame from %s: %s", client_id, exc)
            return ForwardDecision("forward")

        variant = message.WhichOneof("payload_variant")
        if variant == "want_config_id":
            if self._should_claim_host_session_owner(client_id):
                self._claim_host_session_owner(client_id)
                self.logger.info("host session claimed by %s via want_config_id", self._client_label(client_id))
                return ForwardDecision("forward")
            self.logger.debug(
                "suppressing host-session want_config_id from %s while owned by %s",
                self._client_label(client_id),
                self._host_session_owner_label(),
            )
            return ForwardDecision("consume")

        if variant in {"heartbeat", "disconnect"}:
            if self.host_session_owner_id is None or (
                variant == "heartbeat" and self._should_preempt_host_session_owner(client_id)
            ):
                self._claim_host_session_owner(client_id)
                self.logger.info("host session claimed by %s via %s", self._client_label(client_id), variant)
                return ForwardDecision("forward")
            if self.host_session_owner_id != client_id:
                self.logger.debug(
                    "suppressing host-session %s from %s while owned by %s",
                    variant,
                    self._client_label(client_id),
                    self._host_session_owner_label(),
                )
                return ForwardDecision("consume")
            if variant == "disconnect":
                self.logger.info("host session released by %s", self._client_label(client_id))
                self._clear_host_session_owner()
                return ForwardDecision("forward")
            self._refresh_host_session_owner_lease()
            return ForwardDecision("forward")

        if not is_control_request(message):
            return ForwardDecision("forward")

        if self.control_owner_id is None:
            self._claim_control_owner(client_id)
            self.forwarded_control_frames += 1
            self.logger.info("control session claimed by %s", self._client_label(client_id))
            return ForwardDecision("forward")

        if self.control_owner_id != client_id:
            self.denied_control_frames += 1
            self.logger.info(
                "denied control request from %s while owned by %s",
                self._client_label(client_id),
                self._owner_label(),
            )
            return ForwardDecision("deny")

        self.forwarded_control_frames += 1
        if message.WhichOneof("payload_variant") == "disconnect":
            self.logger.info("control session released by %s", self._client_label(client_id))
            self._clear_control_owner()
            return ForwardDecision("forward")

        self._refresh_control_owner_lease()

        return ForwardDecision("forward")

    def snapshot(self) -> dict[str, object]:
        self._expire_control_owner_if_needed()
        self._expire_host_session_owner_if_needed()
        expires_in = None
        if self.control_owner_expires_at is not None:
            expires_in = max(0.0, round(self.control_owner_expires_at - self.clock(), 3))
        host_session_expires_in = None
        if self.host_session_expires_at is not None:
            host_session_expires_in = max(0.0, round(self.host_session_expires_at - self.clock(), 3))
        return {
            "client_count": len(self.clients),
            "control_owner": self._owner_label() if self.control_owner_id is not None else None,
            "control_session_confirmed": self.control_owner_confirmed,
            "control_session_expires_in": expires_in,
            "denied_control_frames": self.denied_control_frames,
            "dropped_radio_bytes": self.dropped_radio_bytes,
            "forwarded_control_frames": self.forwarded_control_frames,
            "host_session_owner": self._host_session_owner_label() if self.host_session_owner_id is not None else None,
            "host_session_expires_in": host_session_expires_in,
            "ignored_serial_debug_bytes": self.ignored_serial_debug_bytes,
            "invalid_radio_frames": self.invalid_radio_frames,
            "observed_admin_responses": self.observed_admin_responses,
            "last_session_passkey": self.last_session_passkey or None,
            "last_admin_response_owner": self.last_admin_response_owner,
        }

    def _claim_host_session_owner(self, client_id: str) -> None:
        self.host_session_owner_id = client_id
        self.host_session_expires_at = self.clock() + HOST_SESSION_LEASE_SECONDS

    def _should_claim_host_session_owner(self, client_id: str) -> bool:
        if self.host_session_owner_id is None or self.host_session_owner_id == client_id:
            return True
        current_is_loopback = self._is_loopback_client(self.host_session_owner_id)
        candidate_is_loopback = self._is_loopback_client(client_id)
        if current_is_loopback and not candidate_is_loopback:
            return True
        if not current_is_loopback and candidate_is_loopback:
            return False
        return True

    def _should_preempt_host_session_owner(self, client_id: str) -> bool:
        if self.host_session_owner_id is None or self.host_session_owner_id == client_id:
            return True
        return self._is_loopback_client(self.host_session_owner_id) and not self._is_loopback_client(client_id)

    def _refresh_host_session_owner_lease(self) -> None:
        if self.host_session_owner_id is None:
            return
        self.host_session_expires_at = self.clock() + HOST_SESSION_LEASE_SECONDS

    def _clear_host_session_owner(self) -> None:
        self.host_session_owner_id = None
        self.host_session_expires_at = None

    def _expire_host_session_owner_if_needed(self) -> None:
        if self.host_session_owner_id is None or self.host_session_expires_at is None:
            return
        if self.clock() < self.host_session_expires_at:
            return
        self.logger.info("host session expired for %s", self._host_session_owner_label())
        self._clear_host_session_owner()

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

    def _is_loopback_client(self, client_id: str) -> bool:
        label = self._client_label(client_id)
        host = label.rsplit(":", 1)[0] if ":" in label else label
        host = host.strip("[]")
        if host in {"127.0.0.1", "::1", "localhost"}:
            return True
        try:
            return socket.gethostbyname(host) == "127.0.0.1"
        except OSError:
            return False

    def _owner_label(self) -> str:
        if self.control_owner_id is None:
            return "unknown client"
        return self._client_label(self.control_owner_id)

    def _host_session_owner_label(self) -> str:
        if self.host_session_owner_id is None:
            return "unknown client"
        return self._client_label(self.host_session_owner_id)
