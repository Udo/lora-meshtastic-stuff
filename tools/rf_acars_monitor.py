#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import curses
import json
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


@dataclass
class AcarsState:
    key: str
    count: int = 0
    last_seen: float = 0.0
    flight: str = "-"
    label: str = "-"
    preview: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live ACARS monitor using acarsdec JSON output")
    parser.add_argument("--acarsdec", default=repo_local_binary(__file__, "acarsdec", "acarsdec"))
    parser.add_argument("--device-index", default="0")
    parser.add_argument("--gain", default="0")
    parser.add_argument("--ppm", type=int, default=0)
    parser.add_argument("--frequency", action="append", dest="frequencies", type=float, help="Frequency in MHz, repeatable")
    parser.add_argument("--fps", type=float, default=4.0)
    parser.add_argument("--max-aircraft", type=int, default=18)
    parser.add_argument("--show-raw", action="store_true")
    parser.add_argument("--log-file", help="Optional JSONL log path for decoded ACARS messages")
    args = parser.parse_args()
    if not args.frequencies:
        args.frequencies = [131.525, 131.725, 131.825]
    return args


def validate_binaries(args: argparse.Namespace) -> None:
    if shutil.which(args.acarsdec) is None:
        raise RuntimeError("acarsdec is not installed. Build it from https://github.com/TLeconte/acarsdec or provide --acarsdec")


def build_command(args: argparse.Namespace) -> list[str]:
    cmd = [args.acarsdec, "-o", "4", "-r", str(args.device_index)]
    if str(args.gain) != "0":
        cmd.extend(["-g", str(args.gain)])
    if args.ppm:
        cmd.extend(["-p", str(args.ppm)])
    cmd.extend(str(freq) for freq in args.frequencies)
    return cmd


def normalize_record(record: dict) -> dict[str, object]:
    return {
        "tail": str(record.get("tail") or "?"),
        "flight": str(record.get("flight") or "-"),
        "label": str(record.get("label") or "-"),
        "freq": record.get("freq"),
        "text": str(record.get("text") or ""),
        "channel": record.get("channel"),
    }


def run_ui(stdscr: curses.window, args: argparse.Namespace) -> int:
    configure_curses(stdscr, args.fps)
    states: dict[str, AcarsState] = {}
    raw_lines: collections.deque[str] = collections.deque(maxlen=8)
    started = current_time()
    last_draw = 0.0
    last_activity = 0.0
    last_status = "starting decoder"
    total_messages = 0
    bad_lines = 0
    stdout_buffer = ""
    logger = EventLogger("acars", args.log_file)

    try:
        with NonBlockingProcess(build_command(args), text=True, bufsize=1) as proc:
            while True:
                proc.assert_running("acarsdec exited")
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
                        try:
                            record = json.loads(line)
                        except Exception:
                            bad_lines += 1
                            continue
                        data = normalize_record(record)
                        now = current_time()
                        state = states.setdefault(data["tail"], AcarsState(key=str(data["tail"])))
                        state.count += 1
                        state.last_seen = now
                        state.flight = str(data["flight"])
                        state.label = str(data["label"])
                        state.preview = str(data["text"])[:80]
                        total_messages += 1
                        last_status = f"decoded {state.key} {state.flight} {state.label}"
                        logger.log(timestamp=now, kind="message", data=record)

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
                rate = total_messages / elapsed
                live_count = sum(1 for item in states.values() if now - item.last_seen < 900.0)
                draw_text(
                    stdscr,
                    0,
                    0,
                    f"ACARS Monitor  freqs={','.join(f'{freq:.3f}' for freq in args.frequencies)} MHz  fps={args.fps:.1f}  msgs={total_messages}  rate={rate:.1f}/s  aircraft={live_count}",
                    curses.A_BOLD,
                )
                draw_text(stdscr, 1, 0, f"acarsdec={args.acarsdec}  q=quit")
                draw_text(
                    stdscr,
                    2,
                    0,
                    monitor_status_line(
                        now=now,
                        started=started,
                        last_activity=last_activity,
                        total_events=total_messages,
                        idle_label="decoder alive, no accepted ACARS messages yet",
                        receiving_label="receiving",
                        detail=last_status,
                        width=width,
                    ),
                )
                draw_text(stdscr, 4, 0, "Tail        Flight      Count  Last(s)  Label  Preview")
                row = 5
                for state in sorted(states.values(), key=lambda item: (-item.last_seen, -item.count)):
                    age = now - state.last_seen
                    draw_text(
                        stdscr,
                        row,
                        0,
                        f"{state.key[:10]:<10} {state.flight[:10]:<10} {state.count:>6}  {age:>7.1f}  {state.label[:5]:<5}  {state.preview[: max(0, width - 50)]}",
                    )
                    row += 1
                    if row >= height - 4 or row - 5 >= args.max_aircraft:
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
        print(f"rf_acars_monitor failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
