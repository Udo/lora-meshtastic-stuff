#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import curses
import os
import shlex
import shutil
import sys
from pathlib import Path
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
class AlertState:
    key: str
    count: int = 0
    last_seen: float = 0.0
    preview: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live weather alert monitor using rtl_fm piped into multimon-ng EAS")
    parser.add_argument("--rtl-fm", default=repo_local_binary(__file__, "rtl_fm", "rtl_fm"))
    parser.add_argument("--multimon", default="multimon-ng")
    parser.add_argument("--frequency", type=int, default=162_550_000, help="Weather alert frequency in Hz")
    parser.add_argument("--sample-rate", type=int, default=22_050)
    parser.add_argument("--gain", default="0")
    parser.add_argument("--ppm", type=int, default=0)
    parser.add_argument("--device-index", default="0")
    parser.add_argument("--fps", type=float, default=4.0)
    parser.add_argument("--max-alerts", type=int, default=12)
    parser.add_argument("--show-raw", action="store_true")
    parser.add_argument("--log-file", help="Optional JSONL log path for decoded SAME/EAS lines")
    return parser.parse_args()


def validate_binaries(args: argparse.Namespace) -> None:
    if shutil.which(args.rtl_fm) is None:
        raise RuntimeError("rtl_fm is not installed. Run ./setup/rtl2838.sh bootstrap first")
    if shutil.which(args.multimon) is None:
        raise RuntimeError("multimon-ng is not installed. On Ubuntu/Debian install it with: sudo apt-get install multimon-ng")


def build_command(args: argparse.Namespace) -> tuple[list[str], dict[str, str] | None]:
    pipeline = (
        f"{shlex.quote(args.rtl_fm)} -M fm -f {args.frequency} -s {args.sample_rate} -r {args.sample_rate} "
        f"-g {shlex.quote(str(args.gain))} -p {args.ppm} -d {shlex.quote(str(args.device_index))} -A fast -F 9 - "
        f"| {shlex.quote(args.multimon)} -t raw -a EAS -A -"
    )
    env = None
    if "/" in args.rtl_fm:
        local_lib = str((Path(args.rtl_fm).resolve().parents[1] / "lib"))
        env = dict(os.environ)
        env["LD_LIBRARY_PATH"] = local_lib + (f":{env['LD_LIBRARY_PATH']}" if env.get("LD_LIBRARY_PATH") else "")
    return ["bash", "-lc", pipeline], env


def parse_alert_line(line: str) -> dict[str, str] | None:
    if "EAS:" not in line:
        return None
    payload = line.split("EAS:", 1)[1].strip()
    key = payload.split("-", 2)[0] if payload else "EAS"
    return {"key": key, "line": payload}


def run_ui(stdscr: curses.window, args: argparse.Namespace) -> int:
    configure_curses(stdscr, args.fps)
    alerts: dict[str, AlertState] = {}
    raw_lines: collections.deque[str] = collections.deque(maxlen=8)
    started = current_time()
    last_draw = 0.0
    last_activity = 0.0
    last_status = "starting decoder"
    total_alerts = 0
    bad_lines = 0
    stdout_buffer = ""
    logger = EventLogger("weather-alert", args.log_file)
    command, env = build_command(args)

    try:
        with NonBlockingProcess(command, text=True, bufsize=1, env=env) as proc:
            while True:
                proc.assert_running("weather alert decoder exited")
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
                        parsed = parse_alert_line(line)
                        if parsed is None:
                            bad_lines += 1
                            continue
                        now = current_time()
                        state = alerts.setdefault(parsed["key"], AlertState(key=parsed["key"]))
                        state.count += 1
                        state.last_seen = now
                        state.preview = parsed["line"][:100]
                        total_alerts += 1
                        last_status = f"decoded {parsed['key']}"
                        logger.log(timestamp=now, kind="alert", data=parsed)

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
                rate = total_alerts / elapsed
                live_count = sum(1 for item in alerts.values() if now - item.last_seen < 3600.0)
                draw_text(
                    stdscr,
                    0,
                    0,
                    f"Weather Alert Monitor  freq={args.frequency / 1e6:.3f} MHz  fps={args.fps:.1f}  alerts={total_alerts}  rate={rate:.1f}/s  active={live_count}",
                    curses.A_BOLD,
                )
                draw_text(stdscr, 1, 0, f"rtl_fm={args.rtl_fm}  multimon={args.multimon}  q=quit")
                draw_text(
                    stdscr,
                    2,
                    0,
                    monitor_status_line(
                        now=now,
                        started=started,
                        last_activity=last_activity,
                        total_events=total_alerts,
                        idle_label="decoder alive, no accepted alerts yet",
                        receiving_label="receiving",
                        detail=last_status,
                        width=width,
                    ),
                )
                draw_text(stdscr, 4, 0, "Code            Count  Last(s)  Preview")
                row = 5
                for state in sorted(alerts.values(), key=lambda item: (-item.last_seen, -item.count)):
                    age = now - state.last_seen
                    draw_text(
                        stdscr,
                        row,
                        0,
                        f"{state.key[:14]:<14} {state.count:>6}  {age:>7.1f}  {state.preview[: max(0, width - 32)]}",
                    )
                    row += 1
                    if row >= height - 4 or row - 5 >= args.max_alerts:
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
        print(f"rf_weather_alert_monitor failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
