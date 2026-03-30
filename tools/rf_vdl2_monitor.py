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
    repo_local_runtime_env,
)


@dataclass
class Vdl2State:
    key: str
    count: int = 0
    last_seen: float = 0.0
    flight: str = "-"
    label: str = "-"
    freq: str = "-"
    preview: str = ""


DEFAULT_FREQUENCIES = [136.650, 136.725, 136.775, 136.825, 136.875, 136.975]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live VDL2 monitor using vdlm2dec JSON output")
    parser.add_argument("--vdlm2dec", default=repo_local_binary(__file__, "vdlm2dec", "vdlm2dec"))
    parser.add_argument("--device-index", default="0")
    parser.add_argument("--gain", default="0")
    parser.add_argument("--ppm", type=int, default=0)
    parser.add_argument("--station-id", default="local-vdl2")
    parser.add_argument("--frequency", action="append", dest="frequencies", type=float, help="Frequency in MHz, repeatable")
    parser.add_argument("--fps", type=float, default=4.0)
    parser.add_argument("--max-aircraft", type=int, default=18)
    parser.add_argument("--show-raw", action="store_true")
    parser.add_argument("--log-file", help="Optional JSONL log path for decoded VDL2 messages")
    args = parser.parse_args()
    if not args.frequencies:
        args.frequencies = list(DEFAULT_FREQUENCIES)
    return args


def validate_binaries(args: argparse.Namespace) -> None:
    if shutil.which(args.vdlm2dec) is None:
        raise RuntimeError("vdlm2dec is not installed. Run ./setup/rtl2838.sh bootstrap first")


def build_command(args: argparse.Namespace) -> tuple[list[str], dict[str, str]]:
    cmd = [
        args.vdlm2dec,
        "-J",
        "-r",
        str(args.device_index),
        "-i",
        args.station_id,
    ]
    if str(args.gain) != "0":
        cmd.extend(["-g", str(args.gain)])
    if args.ppm:
        cmd.extend(["-p", str(args.ppm)])
    cmd.extend(str(freq) for freq in args.frequencies)
    return cmd, repo_local_runtime_env(__file__, binary_path=args.vdlm2dec)


def normalize_record(record: dict[str, object]) -> dict[str, str]:
    return {
        "icao": str(record.get("icao") or "?"),
        "tail": str(record.get("tail") or "?"),
        "flight": str(record.get("flight") or "-"),
        "label": str(record.get("label") or "-"),
        "freq": str(record.get("freq") or "-"),
        "text": str(record.get("text") or record.get("msg") or ""),
    }


def run_ui(stdscr: curses.window, args: argparse.Namespace) -> int:
    configure_curses(stdscr, args.fps)
    states: dict[str, Vdl2State] = {}
    raw_lines: collections.deque[str] = collections.deque(maxlen=8)
    started = current_time()
    last_draw = 0.0
    last_activity = 0.0
    last_status = "starting decoder"
    total_messages = 0
    bad_lines = 0
    stdout_buffer = ""
    logger = EventLogger("vdl2", args.log_file)
    command, env = build_command(args)

    try:
        with NonBlockingProcess(command, text=True, bufsize=1, env=env) as proc:
            while True:
                proc.assert_running("vdlm2dec exited")
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
                        state = states.setdefault(data["icao"], Vdl2State(key=data["icao"]))
                        state.count += 1
                        state.last_seen = now
                        state.flight = data["flight"]
                        state.label = data["label"]
                        state.freq = data["freq"]
                        state.preview = data["text"][:80]
                        total_messages += 1
                        last_status = f"decoded {data['icao']} {data['label']}"
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
                draw_text(
                    stdscr,
                    0,
                    0,
                    f"VDL2 Monitor  freqs={','.join(f'{freq:.3f}' for freq in args.frequencies)} MHz  fps={args.fps:.1f}  msgs={total_messages}  rate={total_messages / elapsed:.1f}/s  aircraft={len(states)}",
                    curses.A_BOLD,
                )
                draw_text(stdscr, 1, 0, f"vdlm2dec={args.vdlm2dec}  q=quit")
                draw_text(
                    stdscr,
                    2,
                    0,
                    monitor_status_line(
                        now=now,
                        started=started,
                        last_activity=last_activity,
                        total_events=total_messages,
                        idle_label="decoder alive, no accepted VDL2 messages yet",
                        receiving_label="receiving",
                        detail=last_status,
                        width=width,
                    ),
                )
                draw_text(stdscr, 4, 0, "ICAO      Flight      Count  Last(s)  Label  Freq      Preview")
                row = 5
                for state in sorted(states.values(), key=lambda item: (-item.last_seen, -item.count)):
                    age = now - state.last_seen
                    draw_text(
                        stdscr,
                        row,
                        0,
                        f"{state.key[:8]:<8}  {state.flight[:10]:<10} {state.count:>6}  {age:>7.1f}  {state.label[:5]:<5}  {state.freq[:8]:<8}  {state.preview[: max(0, width - 61)]}",
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
        print(f"rf_vdl2_monitor failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
