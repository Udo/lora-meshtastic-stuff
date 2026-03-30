#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import curses
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from _rf_event_log import EventLogger
from _rf_monitor_common import (
    NonBlockingProcess,
    configure_curses,
    current_time,
    draw_text,
    ensure_dongle_mode,
    monitor_status_line,
    repo_local_binary,
)


FRAME_RE = re.compile(r"^\*([0-9a-fA-F]+);$")
BADSAMPLE = 255
PREAMBLE_LEN = 16
LONG_FRAME = 112
SHORT_FRAME = 56
QUALITY = 10
ALLOWED_ERRORS = 5
CRC_GENERATOR = "1111111111111010000001001"
VALID_DF = {17, 18}


@dataclass
class AircraftState:
    icao: str
    frame_count: int = 0
    short_count: int = 0
    long_count: int = 0
    last_seen: float = 0.0
    last_df: int | None = None
    last_tc: int | None = None
    best_confidence: float = 0.0


@dataclass
class DecodedFrame:
    hex_frame: str
    df: int
    icao: str
    tc: int | None
    errors: int
    preamble_margin: float
    crc_ok: bool
    confidence: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live ADS-B monitor using kernel V4L2 IQ or repo-local rtl_adsb")
    parser.add_argument("--source", choices=("auto", "kernel", "libusb"), default="auto")
    parser.add_argument("--device", default="/dev/swradio0", help="Kernel SDR device path for V4L2 IQ mode")
    parser.add_argument("--rtl-adsb", default=repo_local_binary(__file__, "rtl_adsb", "rtl_adsb"))
    parser.add_argument("--gain", default="0", help="rtl_adsb gain in dB-tenths, or 0 for auto")
    parser.add_argument("--ppm", type=int, default=0)
    parser.add_argument("--device-index", default="0")
    parser.add_argument("--fps", type=float, default=4.0)
    parser.add_argument("--max-aircraft", type=int, default=18)
    parser.add_argument("--show-raw", action="store_true")
    parser.add_argument("--sample-bytes", type=int, default=131072, help="Kernel-mode bytes read per decode chunk")
    parser.add_argument("--max-errors", type=int, default=5, help="Maximum Manchester decode errors tolerated per candidate")
    parser.add_argument("--min-preamble-margin", type=float, default=0.0, help="Minimum preamble high/low margin for kernel candidates")
    parser.add_argument("--min-confidence", type=float, default=35.0, help="Minimum confidence score required for kernel-mode acceptance")
    parser.add_argument("--log-file", help="Optional JSONL log path for decoded ADS-B frames")
    return parser.parse_args()


def parse_frame(line: str) -> tuple[str, int, int, int | None] | None:
    match = FRAME_RE.match(line.strip())
    if not match:
        return None
    hex_frame = match.group(1).lower()
    return parse_frame_hex(hex_frame)


def parse_frame_hex(hex_frame: str) -> tuple[str, int, int, int | None] | None:
    if len(hex_frame) < 8:
        return None
    frame = bytes.fromhex(hex_frame)
    df = (frame[0] >> 3) & 0x1F
    icao = frame[1:4].hex()
    tc = None
    if len(frame) >= 5 and len(frame) > 7:
        tc = (frame[4] >> 3) & 0x1F
    return hex_frame, df, int(icao, 16), tc


def mode_s_crc_remainder(hex_frame: str) -> int:
    nbits = len(hex_frame) * 4
    data = list(bin(int(hex_frame, 16))[2:].zfill(nbits))
    generator = list(CRC_GENERATOR)
    for i in range(nbits - 24):
        if data[i] == "1":
            for j, bit in enumerate(generator):
                data[i + j] = "0" if data[i + j] == bit else "1"
    return int("".join(data[-24:]), 2)


def build_libusb_command(args: argparse.Namespace) -> list[str]:
    return [
        str(Path(args.rtl_adsb)),
        "-d",
        str(args.device_index),
        "-g",
        str(args.gain),
        "-p",
        str(args.ppm),
    ]


def build_kernel_command(args: argparse.Namespace) -> list[str]:
    return [
        "v4l2-ctl",
        "-d",
        args.device,
        "--set-freq=1090.000000",
        "--set-fmt-sdr=samplefmt=CU08",
        "--stream-mmap=4",
        "--stream-to=-",
    ]


def select_source(args: argparse.Namespace) -> str:
    if args.source != "auto":
        return args.source
    if os.path.exists(args.device):
        return "kernel"
    return "libusb"


def setup_libusb_env(args: argparse.Namespace) -> dict[str, str]:
    env = os.environ.copy()
    rtl_adsb_path = Path(args.rtl_adsb)
    local_lib = rtl_adsb_path.resolve().parents[1] / "lib"
    env["LD_LIBRARY_PATH"] = str(local_lib) + (f":{env['LD_LIBRARY_PATH']}" if env.get("LD_LIBRARY_PATH") else "")
    return env


def abs8(v: np.ndarray) -> np.ndarray:
    return np.abs(v.astype(np.int16) - 127).astype(np.uint16)


def magnitude_from_iq(raw: bytes) -> np.ndarray:
    data = np.frombuffer(raw, dtype=np.uint8)
    if data.size < 2:
        return np.empty(0, dtype=np.uint16)
    if data.size % 2:
        data = data[:-1]
    i = abs8(data[0::2])
    q = abs8(data[1::2])
    return (i * i + q * q).astype(np.uint16)


def single_manchester(a: int, b: int, c: int, d: int) -> int:
    bit_p = int(a > b)
    bit = int(c > d)

    if QUALITY == 0:
        return bit

    if QUALITY == 5:
        if bit and bit_p and b > c:
            return BADSAMPLE
        if (not bit) and (not bit_p) and b < c:
            return BADSAMPLE
        return bit

    if QUALITY == 10:
        if bit and bit_p and c > b:
            return 1
        if bit and (not bit_p) and d < b:
            return 1
        if (not bit) and bit_p and d > b:
            return 0
        if (not bit) and (not bit_p) and c < b:
            return 0
        return BADSAMPLE

    if bit and bit_p and c > b and d < a:
        return 1
    if bit and (not bit_p) and c > a and d < b:
        return 1
    if (not bit) and bit_p and c < a and d > b:
        return 0
    if (not bit) and (not bit_p) and c < b and d > a:
        return 0
    return BADSAMPLE


def preamble_margin(buf: np.ndarray, i: int) -> float:
    highs = [int(buf[i + j]) for j in (0, 2, 7, 9)]
    lows = [int(buf[i + j]) for j in range(PREAMBLE_LEN) if j not in (0, 2, 7, 9)]
    return float(min(highs) - max(lows))


def has_preamble(buf: np.ndarray, i: int) -> bool:
    return preamble_margin(buf, i) > 0.0


def decode_candidate(buf: np.ndarray, start: int, max_errors: int) -> DecodedFrame | None:
    if start + PREAMBLE_LEN + (LONG_FRAME * 2) + 1 >= len(buf):
        return None

    a = int(buf[start])
    b = int(buf[start + 1])
    i = start + PREAMBLE_LEN
    data_i = 0
    frame_len = LONG_FRAME
    errors = 0
    frame = [0] * 14

    while i < len(buf) - 1 and data_i < frame_len:
        bit = single_manchester(a, b, int(buf[i]), int(buf[i + 1]))
        a = int(buf[i])
        b = int(buf[i + 1])
        if bit == BADSAMPLE:
            errors += 1
            if errors > max_errors:
                return None
            bit = 1 if a > b else 0
            a = 0
            b = 65535
        if bit:
            index = data_i // 8
            shift = 7 - (data_i % 8)
            frame[index] |= 1 << shift
        if data_i == 7:
            if frame[0] == 0:
                return None
            frame_len = LONG_FRAME if (frame[0] & 0x80) else SHORT_FRAME
        i += 2
        data_i += 1

    if data_i < (frame_len - 1):
        return None

    used_bytes = (frame_len + 7) // 8
    hex_frame = "".join(f"{b:02x}" for b in frame[:used_bytes])
    parsed = parse_frame_hex(hex_frame)
    if parsed is None:
        return None
    _, df, icao_int, tc = parsed
    if df not in VALID_DF:
        return None
    crc_ok = mode_s_crc_remainder(hex_frame) == 0
    margin = preamble_margin(buf, start)
    confidence = max(0.0, 100.0 - (errors * 12.0) + min(30.0, margin / 80.0) + (18.0 if crc_ok else -35.0))
    return DecodedFrame(
        hex_frame=hex_frame,
        df=df,
        icao=f"{icao_int:06x}",
        tc=tc,
        errors=errors,
        preamble_margin=margin,
        crc_ok=crc_ok,
        confidence=confidence,
    )


def decode_messages_from_magnitude(
    magnitude: np.ndarray, max_errors: int, min_preamble_margin: float, min_confidence: float
) -> tuple[list[DecodedFrame], dict[str, int]]:
    stats = {"candidates": 0, "crc_fail": 0, "weak_preamble": 0, "low_confidence": 0}
    if magnitude.size == 0:
        return [], stats

    frames: list[DecodedFrame] = []
    seen: set[str] = set()
    i = 0
    limit = len(magnitude) - (PREAMBLE_LEN + (SHORT_FRAME * 2) + 1)
    while i < limit:
        if not has_preamble(magnitude, i):
            i += 1
            continue
        margin = preamble_margin(magnitude, i)
        if margin < min_preamble_margin:
            stats["weak_preamble"] += 1
            i += 1
            continue
        stats["candidates"] += 1
        decoded = decode_candidate(magnitude, i, max_errors)
        if decoded is None:
            i += 1
            continue
        if not decoded.crc_ok:
            stats["crc_fail"] += 1
            i += PREAMBLE_LEN
            continue
        if decoded.confidence < min_confidence:
            stats["low_confidence"] += 1
            i += PREAMBLE_LEN
            continue
        if decoded.hex_frame not in seen:
            seen.add(decoded.hex_frame)
            frames.append(decoded)
        i += PREAMBLE_LEN
    return frames, stats


class LibusbFrameSource:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.proc: NonBlockingProcess | None = None
        self.stdout_buffer = ""

    def __enter__(self) -> "LibusbFrameSource":
        self.proc = NonBlockingProcess(
            build_libusb_command(self.args),
            text=True,
            bufsize=1,
            env=setup_libusb_env(self.args),
        )
        self.proc.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.proc is not None:
            self.proc.__exit__(exc_type, exc, tb)

    def read_frames(self) -> tuple[list[DecodedFrame], str]:
        assert self.proc is not None
        self.proc.assert_running("rtl_adsb exited")

        frames: list[DecodedFrame] = []
        raw_preview = ""
        stdout_chunk, stderr_lines = self.proc.read_available()
        if stderr_lines:
            raw_preview = stderr_lines[-1]
        if stdout_chunk:
            self.stdout_buffer += str(stdout_chunk)
            while "\n" in self.stdout_buffer:
                line, self.stdout_buffer = self.stdout_buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                raw_preview = line
                if parsed := parse_frame(line):
                    hex_frame, df, icao_int, tc = parsed
                    crc_ok = mode_s_crc_remainder(hex_frame) == 0 if len(hex_frame) == 28 else False
                    frames.append(
                        DecodedFrame(
                            hex_frame=hex_frame,
                            df=df,
                            icao=f"{icao_int:06x}",
                            tc=tc,
                            errors=0,
                            preamble_margin=0.0,
                            crc_ok=crc_ok,
                            confidence=100.0 if crc_ok else 70.0,
                        )
                    )
        return frames, raw_preview


class KernelFrameSource:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.proc: NonBlockingProcess | None = None

    def __enter__(self) -> "KernelFrameSource":
        self.proc = NonBlockingProcess(
            build_kernel_command(self.args),
            bufsize=0,
            text=False,
        )
        self.proc.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.proc is not None:
            self.proc.__exit__(exc_type, exc, tb)

    def read_frames(self) -> tuple[list[DecodedFrame], str]:
        stats = {"candidates": 0, "crc_fail": 0, "weak_preamble": 0, "low_confidence": 0}
        assert self.proc is not None
        self.proc.assert_running("v4l2-ctl exited")

        raw = b""
        preview = ""
        stdout_chunk, stderr_lines = self.proc.read_available(stdout_bytes=self.args.sample_bytes)
        if stderr_lines:
            preview = stderr_lines[-1]
        if stdout_chunk:
            raw += bytes(stdout_chunk)
        if not raw:
            return [], preview
        magnitude = magnitude_from_iq(raw)
        decoded, stats = decode_messages_from_magnitude(
            magnitude,
            max_errors=self.args.max_errors,
            min_preamble_margin=self.args.min_preamble_margin,
            min_confidence=self.args.min_confidence,
        )
        preview = (
            f"chunk={len(raw)}B ok={len(decoded)} cand={stats['candidates']} "
            f"crc_fail={stats['crc_fail']} weak={stats['weak_preamble']} lowq={stats['low_confidence']}"
        )
        return decoded, preview


def make_source(args: argparse.Namespace):
    source_name = select_source(args)
    if source_name == "kernel":
        return source_name, KernelFrameSource(args)
    return source_name, LibusbFrameSource(args)


def run_ui(stdscr: curses.window, args: argparse.Namespace) -> int:
    configure_curses(stdscr, args.fps)

    aircraft: dict[str, AircraftState] = {}
    raw_lines: collections.deque[str] = collections.deque(maxlen=8)
    started = current_time()
    total_frames = 0
    bad_lines = 0
    last_draw = 0.0
    last_activity = 0.0
    last_status = "starting decoder"
    logger = EventLogger("adsb", args.log_file)

    source_name, source = make_source(args)
    try:
        with source:
            while True:
                frames, preview = source.read_frames()
                if preview:
                    raw_lines.append(preview)
                    last_status = preview
                    last_activity = current_time()

                for frame in frames:
                    icao = frame.icao
                    state = aircraft.setdefault(icao, AircraftState(icao=icao))
                    state.frame_count += 1
                    if len(frame.hex_frame) <= 14:
                        state.short_count += 1
                    else:
                        state.long_count += 1
                    state.last_seen = current_time()
                    state.last_df = frame.df
                    state.last_tc = frame.tc
                    state.best_confidence = max(state.best_confidence, frame.confidence)
                    total_frames += 1
                    last_activity = state.last_seen
                    if source_name == "kernel":
                        last_status = f"decoded {frame.icao.upper()} df={frame.df} tc={frame.tc if frame.tc is not None else '-'} conf={frame.confidence:.1f}"
                    else:
                        last_status = f"decoded {frame.icao.upper()} df={frame.df} crc={'ok' if frame.crc_ok else 'fail'}"
                    logger.log(
                        timestamp=state.last_seen,
                        kind="frame",
                        data={
                            "source": source_name,
                            "hex_frame": frame.hex_frame,
                            "df": frame.df,
                            "icao": frame.icao,
                            "tc": frame.tc,
                            "errors": frame.errors,
                            "preamble_margin": frame.preamble_margin,
                            "crc_ok": frame.crc_ok,
                            "confidence": frame.confidence,
                        },
                    )

                now = current_time()
                if now - last_draw < (1.0 / max(1.0, args.fps)):
                    key = stdscr.getch()
                    if key in (ord("q"), ord("Q"), 27):
                        return 0
                    continue
                last_draw = now

                height, width = stdscr.getmaxyx()
                stdscr.erase()
                elapsed = max(0.001, now - started)
                rate = total_frames / elapsed
                live_count = sum(1 for state in aircraft.values() if now - state.last_seen < 60.0)
                draw_text(
                    stdscr,
                    0,
                    0,
                    f"ADS-B Monitor  source={source_name}  freq=1090.000 MHz  fps={args.fps:.1f}  frames={total_frames}  rate={rate:.1f}/s  aircraft={live_count}",
                    curses.A_BOLD,
                )
                draw_text(
                    stdscr,
                    1,
                    0,
                    f"gain={args.gain}  ppm={args.ppm}  device={args.device if source_name == 'kernel' else args.device_index}  q=quit",
                )
                draw_text(
                    stdscr,
                    2,
                    0,
                    monitor_status_line(
                        now=now,
                        started=started,
                        last_activity=last_activity,
                        total_events=total_frames,
                        idle_label="decoder alive, no accepted frames yet",
                        receiving_label="receiving",
                        detail=last_status,
                        width=width,
                    ),
                )
                draw_text(stdscr, 4, 0, "ICAO     Frames  Last(s)  DF   TC  Conf")

                ranked = sorted(
                    aircraft.values(),
                    key=lambda item: (-item.last_seen, -item.frame_count),
                )
                shown = 0
                row = 5
                for state in ranked:
                    age = now - state.last_seen
                    if age > 180.0:
                        continue
                    draw_text(
                        stdscr,
                        row,
                        0,
                        f"{state.icao.upper():<8} {state.frame_count:>6}  {age:>7.1f}  {str(state.last_df or '-'):>2}   {str(state.last_tc or '-'):>2}  {state.best_confidence:>5.1f}",
                    )
                    row += 1
                    shown += 1
                    if shown >= args.max_aircraft or row >= height - 4:
                        break

                footer_y = height - 3
                draw_text(stdscr, footer_y, 0, f"Malformed/other lines: {bad_lines}  raw_msgs={len(raw_lines)}")
                if args.show_raw and raw_lines:
                    draw_text(stdscr, footer_y + 1, 0, "Recent raw:")
                    draw_text(stdscr, footer_y + 2, 0, " | ".join(list(raw_lines)[-2:]))
                stdscr.refresh()

                key = stdscr.getch()
                if key in (ord("q"), ord("Q"), 27):
                    return 0
    finally:
        logger.close()


def main() -> int:
    args = parse_args()
    try:
        if args.source == "auto":
            args.source = ensure_dongle_mode(__file__, "auto", kernel_device=args.device)
        elif args.source in {"kernel", "libusb"}:
            ensure_dongle_mode(__file__, args.source, kernel_device=args.device)
        return curses.wrapper(run_ui, args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"rf_adsb_monitor failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
