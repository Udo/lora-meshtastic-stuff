#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import curses
import re
import shutil
import sys
from dataclasses import dataclass

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


NMEA_RE = re.compile(r"^!AIVD[MO],")


@dataclass
class AisState:
    mmsi: str
    count: int = 0
    last_seen: float = 0.0
    msg_type: int | None = None
    preview: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live AIS monitor using rtl_ais")
    parser.add_argument("--rtl-ais", default=repo_local_binary(__file__, "rtl_ais", "rtl_ais"))
    parser.add_argument("--left-frequency", default="161.975M")
    parser.add_argument("--right-frequency", default="162.025M")
    parser.add_argument("--sample-rate", default="24k")
    parser.add_argument("--output-rate", default="48k")
    parser.add_argument("--device-index", default="0")
    parser.add_argument("--gain", default="0")
    parser.add_argument("--ppm", type=int, default=0)
    parser.add_argument("--fps", type=float, default=4.0)
    parser.add_argument("--max-vessels", type=int, default=18)
    parser.add_argument("--show-raw", action="store_true")
    parser.add_argument("--log-file", help="Optional JSONL log path for decoded AIS NMEA lines")
    return parser.parse_args()


def validate_binaries(args: argparse.Namespace) -> None:
    if shutil.which(args.rtl_ais) is None:
        raise RuntimeError("rtl_ais is not installed. On Ubuntu/Debian install it with: sudo apt-get install rtl-ais")


def build_command(args: argparse.Namespace) -> list[str]:
    cmd = [
        args.rtl_ais,
        "-n",
        "-d",
        str(args.device_index),
        "-l",
        str(args.left_frequency),
        "-r",
        str(args.right_frequency),
        "-s",
        str(args.sample_rate),
        "-o",
        str(args.output_rate),
    ]
    if str(args.gain) != "0":
        cmd.extend(["-g", str(args.gain)])
    if args.ppm:
        cmd.extend(["-p", str(args.ppm)])
    return cmd


def ais_payload_bits(payload: str, fill_bits: int) -> str:
    bits = ""
    for ch in payload:
        val = ord(ch) - 48
        if val > 40:
            val -= 8
        bits += f"{val:06b}"
    return bits[: len(bits) - fill_bits] if fill_bits else bits


def parse_ais_line(line: str) -> dict[str, object] | None:
    if not NMEA_RE.match(line):
        return None
    parts = line.split(",")
    if len(parts) < 7:
        return None
    payload = parts[5]
    try:
        fill_bits = int(parts[6].split("*", 1)[0])
    except Exception:
        fill_bits = 0
    bits = ais_payload_bits(payload, fill_bits)
    if len(bits) < 38:
        return {"mmsi": "fragment", "msg_type": None, "line": line}
    msg_type = int(bits[0:6], 2)
    mmsi = str(int(bits[8:38], 2))
    return {"mmsi": mmsi, "msg_type": msg_type, "line": line}


def run_ui(stdscr: curses.window, args: argparse.Namespace) -> int:
    configure_curses(stdscr, args.fps)
    vessels: dict[str, AisState] = {}
    raw_lines: collections.deque[str] = collections.deque(maxlen=8)
    started = current_time()
    last_draw = 0.0
    last_activity = 0.0
    last_status = "starting decoder"
    total_sentences = 0
    bad_lines = 0
    stdout_buffer = ""
    logger = EventLogger("ais", args.log_file)

    try:
        with NonBlockingProcess(build_command(args), text=True, bufsize=1) as proc:
            while True:
                proc.assert_running("rtl_ais exited")
                stdout_chunk, stderr_lines = proc.read_available()
                for line in stderr_lines:
                    raw_lines.append(line)
                    last_status = line
                    last_activity = current_time()
                if stdout_chunk:
                    stdout_buffer += str(stdout_chunk)
                    while "\n" in stdout_buffer:
                        line, stdout_buffer = stdout_buffer.split("\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        raw_lines.append(line)
                        last_status = line
                        last_activity = current_time()
                        parsed = parse_ais_line(line)
                        if parsed is None:
                            bad_lines += 1
                            continue
                        now = current_time()
                        state = vessels.setdefault(str(parsed["mmsi"]), AisState(mmsi=str(parsed["mmsi"])))
                        state.count += 1
                        state.last_seen = now
                        state.msg_type = parsed["msg_type"] if isinstance(parsed["msg_type"], int) else state.msg_type
                        state.preview = str(parsed["line"])[:90]
                        total_sentences += 1
                        last_status = f"decoded MMSI {state.mmsi}"
                        logger.log(timestamp=now, kind="sentence", data=parsed)

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
                rate = total_sentences / elapsed
                live_count = sum(1 for item in vessels.values() if now - item.last_seen < 900.0)
                draw_text(
                    stdscr,
                    0,
                    0,
                    f"AIS Monitor  left={args.left_frequency} right={args.right_frequency}  fps={args.fps:.1f}  sentences={total_sentences}  rate={rate:.1f}/s  vessels={live_count}",
                    curses.A_BOLD,
                )
                draw_text(stdscr, 1, 0, f"rtl_ais={args.rtl_ais}  q=quit")
                draw_text(
                    stdscr,
                    2,
                    0,
                    monitor_status_line(
                        now=now,
                        started=started,
                        last_activity=last_activity,
                        total_events=total_sentences,
                        idle_label="decoder alive, no accepted AIS sentences yet",
                        receiving_label="receiving",
                        detail=last_status,
                        width=width,
                    ),
                )
                draw_text(stdscr, 4, 0, "MMSI          Count  Last(s)  Type  Preview")
                row = 5
                for state in sorted(vessels.values(), key=lambda item: (-item.last_seen, -item.count)):
                    age = now - state.last_seen
                    type_text = str(state.msg_type) if state.msg_type is not None else "-"
                    draw_text(
                        stdscr,
                        row,
                        0,
                        f"{state.mmsi[:12]:<12} {state.count:>6}  {age:>7.1f}  {type_text:>4}  {state.preview[: max(0, width - 35)]}",
                    )
                    row += 1
                    if row >= height - 4 or row - 5 >= args.max_vessels:
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
        validate_binaries(args)
        ensure_dongle_mode(__file__, "libusb")
        return curses.wrapper(run_ui, args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"rf_ais_monitor failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
