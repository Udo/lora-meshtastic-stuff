#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import curses
import json
import os
import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

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
class StationState:
    key: str
    count: int = 0
    last_seen: float = 0.0
    ps: str = "-"
    pi: str = "-"
    prog_type: str = "-"
    radiotext: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live RDS monitor using rtl_fm piped into redsea")
    parser.add_argument("--rtl-fm", default=repo_local_binary(__file__, "rtl_fm", "rtl_fm"))
    parser.add_argument("--redsea", default=repo_local_binary(__file__, "redsea", "redsea"))
    parser.add_argument("--frequency", type=float, default=100.5, help="FM frequency in MHz")
    parser.add_argument("--sample-rate", type=str, default="171k", help="MPX sample rate for rtl_fm/redsea")
    parser.add_argument("--gain", default="0")
    parser.add_argument("--ppm", type=int, default=0)
    parser.add_argument("--device-index", default="0")
    parser.add_argument("--fps", type=float, default=4.0)
    parser.add_argument("--max-stations", type=int, default=12)
    parser.add_argument("--show-raw", action="store_true")
    parser.add_argument("--log-file", help="Optional JSONL log path for decoded RDS groups")
    return parser.parse_args()


def validate_binaries(args: argparse.Namespace) -> None:
    if shutil.which(args.rtl_fm) is None:
        raise RuntimeError("rtl_fm is not installed. Run ./setup/rtl2838.sh bootstrap first")
    if shutil.which(args.redsea) is None:
        raise RuntimeError("redsea is not installed. Run ./setup/rtl2838.sh bootstrap first")


def build_command(args: argparse.Namespace) -> tuple[list[str], dict[str, str]]:
    pipeline = (
        f"{shlex.quote(args.rtl_fm)} -M fm -f {args.frequency}M -s {args.sample_rate} -r {args.sample_rate} "
        f"-g {shlex.quote(str(args.gain))} -p {args.ppm} -d {shlex.quote(str(args.device_index))} -F 9 - "
        f"| {shlex.quote(args.redsea)} -r {args.sample_rate}"
    )
    env = repo_local_runtime_env(__file__, binary_path=args.redsea)
    return ["bash", "-lc", pipeline], env


def station_key(record: dict[str, object]) -> str:
    return str(record.get("pi") or record.get("ps") or "unknown")


def run_ui(stdscr: curses.window, args: argparse.Namespace) -> int:
    configure_curses(stdscr, args.fps)
    stations: dict[str, StationState] = {}
    raw_lines: collections.deque[str] = collections.deque(maxlen=8)
    started = current_time()
    last_draw = 0.0
    last_activity = 0.0
    last_status = "starting decoder"
    total_groups = 0
    bad_lines = 0
    stdout_buffer = ""
    logger = EventLogger("rds", args.log_file)
    command, env = build_command(args)

    try:
        with NonBlockingProcess(command, text=True, bufsize=1, env=env) as proc:
            while True:
                proc.assert_running("RDS decoder exited")
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
                        now = current_time()
                        key = station_key(record)
                        state = stations.setdefault(key, StationState(key=key))
                        state.count += 1
                        state.last_seen = now
                        state.pi = str(record.get("pi") or state.pi)
                        state.ps = str(record.get("ps") or state.ps)
                        state.prog_type = str(record.get("prog_type") or state.prog_type)
                        state.radiotext = str(record.get("radiotext") or state.radiotext)
                        total_groups += 1
                        last_status = f"decoded {state.ps} {record.get('group', '-')}"
                        logger.log(timestamp=now, kind="group", data=record)

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
                    f"RDS Monitor  freq={args.frequency:.1f} MHz  fps={args.fps:.1f}  groups={total_groups}  rate={total_groups / elapsed:.1f}/s  stations={len(stations)}",
                    curses.A_BOLD,
                )
                draw_text(stdscr, 1, 0, f"rtl_fm={args.rtl_fm}  redsea={args.redsea}  q=quit")
                draw_text(
                    stdscr,
                    2,
                    0,
                    monitor_status_line(
                        now=now,
                        started=started,
                        last_activity=last_activity,
                        total_events=total_groups,
                        idle_label="decoder alive, no accepted RDS groups yet",
                        receiving_label="receiving",
                        detail=last_status,
                        width=width,
                    ),
                )
                draw_text(stdscr, 4, 0, "PI        PS         Count  Last(s)  Type              Radiotext")
                row = 5
                for state in sorted(stations.values(), key=lambda item: (-item.last_seen, -item.count)):
                    age = now - state.last_seen
                    draw_text(
                        stdscr,
                        row,
                        0,
                        f"{state.pi[:8]:<8}  {state.ps[:10]:<10} {state.count:>6}  {age:>7.1f}  {state.prog_type[:16]:<16}  {state.radiotext[: max(0, width - 58)]}",
                    )
                    row += 1
                    if row >= height - 4 or row - 5 >= args.max_stations:
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
        print(f"rf_rds_monitor failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
