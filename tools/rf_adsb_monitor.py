#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import curses
import json
import shutil
import sys
import tempfile
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
)


@dataclass
class AircraftState:
    icao: str
    count: int = 0
    last_seen: float = 0.0
    flight: str = "-"
    altitude: str = "-"
    speed: str = "-"
    track: str = "-"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live ADS-B monitor using readsb JSON output")
    parser.add_argument("--readsb", default="readsb")
    parser.add_argument("--device-index", default="0")
    parser.add_argument("--gain", default="-10", help="readsb gain in dB; use -10 for auto")
    parser.add_argument("--ppm", type=int, default=0)
    parser.add_argument("--fps", type=float, default=2.0)
    parser.add_argument("--write-json-every", type=float, default=1.0)
    parser.add_argument("--json-dir", help="Optional directory for readsb receiver.json / aircraft.json files")
    parser.add_argument("--max-aircraft", type=int, default=18)
    parser.add_argument("--show-raw", action="store_true")
    parser.add_argument("--log-file", help="Optional JSONL log path for fresh ADS-B aircraft snapshots")
    return parser.parse_args()


def validate_binaries(args: argparse.Namespace) -> None:
    if shutil.which(args.readsb) is None:
        raise RuntimeError("readsb is not installed. Run ./setup/rtl2838.sh bootstrap first")


def build_command(args: argparse.Namespace, json_dir: str) -> list[str]:
    return [
        args.readsb,
        "--device-type",
        "rtlsdr",
        "--device",
        str(args.device_index),
        "--gain",
        str(args.gain),
        "--ppm",
        str(args.ppm),
        "--freq",
        "1090000000",
        "--write-json",
        json_dir,
        "--write-json-every",
        str(args.write_json_every),
        "--json-location-accuracy",
        "0",
        "--quiet",
        "--no-interactive",
    ]


def read_aircraft_json(path: Path) -> list[dict[str, object]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    aircraft = payload.get("aircraft", [])
    return aircraft if isinstance(aircraft, list) else []


def aircraft_preview(record: dict[str, object]) -> tuple[str, str, str, str]:
    flight = str(record.get("flight") or "-").strip() or "-"
    altitude = record.get("alt_baro")
    if altitude in (None, "ground"):
        altitude_text = str(altitude or "-")
    else:
        altitude_text = str(int(altitude)) if isinstance(altitude, (int, float)) else str(altitude)
    speed = record.get("gs")
    speed_text = str(int(speed)) if isinstance(speed, (int, float)) else "-"
    track = record.get("track")
    track_text = str(int(track)) if isinstance(track, (int, float)) else "-"
    return flight, altitude_text, speed_text, track_text


def run_ui(stdscr: curses.window, args: argparse.Namespace) -> int:
    configure_curses(stdscr, args.fps)
    states: dict[str, AircraftState] = {}
    raw_lines: collections.deque[str] = collections.deque(maxlen=8)
    logger = EventLogger("adsb", args.log_file)
    started = current_time()
    last_draw = 0.0
    last_activity = 0.0
    last_status = "starting decoder"
    total_snapshots = 0
    last_json_mtime = 0.0
    last_logged: dict[str, float] = {}

    if args.json_dir:
        Path(args.json_dir).mkdir(parents=True, exist_ok=True)
        context = tempfile.TemporaryDirectory(prefix="readsb-", dir=None)
        temp_dir = context.__enter__()
        json_dir = args.json_dir
    else:
        context = tempfile.TemporaryDirectory(prefix="readsb-", dir=None)
        temp_dir = context.__enter__()
        json_dir = temp_dir
    try:
        aircraft_json = Path(json_dir) / "aircraft.json"
        command = build_command(args, json_dir)
        with NonBlockingProcess(command, text=True, bufsize=1) as proc:
            while True:
                    proc.assert_running("readsb exited")
                    _stdout_chunk, stderr_lines = proc.read_available()
                    for line in stderr_lines:
                        raw_lines.append(line)
                        last_status = line
                        last_activity = current_time()

                    if aircraft_json.exists():
                        try:
                            mtime = aircraft_json.stat().st_mtime
                        except FileNotFoundError:
                            mtime = 0.0
                        if mtime > last_json_mtime:
                            last_json_mtime = mtime
                            now = current_time()
                            records = read_aircraft_json(aircraft_json)
                            active = 0
                            for record in records:
                                icao = str(record.get("hex") or "").strip().upper()
                                if not icao:
                                    continue
                                flight, altitude, speed, track = aircraft_preview(record)
                                seen = record.get("seen", 0.0)
                                seen_value = float(seen) if isinstance(seen, (int, float)) else 0.0
                                state = states.setdefault(icao, AircraftState(icao=icao))
                                state.count += 1
                                state.last_seen = now - max(0.0, seen_value)
                                state.flight = flight
                                state.altitude = altitude
                                state.speed = speed
                                state.track = track
                                if seen_value <= 5.0:
                                    active += 1
                                    previous = last_logged.get(icao, 9999.0)
                                    if seen_value < previous or previous > 10.0:
                                        logger.log(timestamp=now, kind="aircraft", data=record)
                                        last_logged[icao] = seen_value
                            total_snapshots += active
                            last_status = f"loaded aircraft.json aircraft={active}"
                            last_activity = now

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
                    live_count = sum(1 for state in states.values() if now - state.last_seen < 60.0)
                    draw_text(
                        stdscr,
                        0,
                        0,
                        f"ADS-B Monitor  backend=readsb  freq=1090.000 MHz  fps={args.fps:.1f}  seen={total_snapshots}  rate={total_snapshots / elapsed:.1f}/s  aircraft={live_count}",
                        curses.A_BOLD,
                    )
                    draw_text(stdscr, 1, 0, f"readsb={args.readsb}  gain={args.gain}  ppm={args.ppm}  q=quit")
                    draw_text(
                        stdscr,
                        2,
                        0,
                        monitor_status_line(
                            now=now,
                            started=started,
                            last_activity=last_activity,
                            total_events=total_snapshots,
                            idle_label="decoder alive, waiting for aircraft.json refresh",
                            receiving_label="receiving",
                            detail=last_status,
                            width=width,
                        ),
                    )
                    draw_text(stdscr, 4, 0, "ICAO     Flight      Last(s)  Alt     GS   Trk")
                    row = 5
                    for state in sorted(states.values(), key=lambda item: (-item.last_seen, -item.count)):
                        age = now - state.last_seen
                        if age > 600.0:
                            continue
                        draw_text(
                            stdscr,
                            row,
                            0,
                            f"{state.icao[:8]:<8} {state.flight[:10]:<10} {age:>7.1f}  {state.altitude[:7]:>7} {state.speed[:4]:>5} {state.track[:4]:>5}",
                        )
                        row += 1
                        if row >= height - 4 or row - 5 >= args.max_aircraft:
                            break
                    footer_y = height - 3
                    draw_text(stdscr, footer_y, 0, f"aircraft.json={aircraft_json}  raw_msgs={len(raw_lines)}")
                    if args.show_raw and raw_lines:
                        draw_text(stdscr, footer_y + 1, 0, "Recent raw:")
                        draw_text(stdscr, footer_y + 2, 0, " | ".join(list(raw_lines)[-2:]))
                    stdscr.refresh()
                    key = stdscr.getch()
                    if key in (ord("q"), ord("Q"), 27):
                        return 0
    finally:
        context.__exit__(None, None, None)
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
        print(f"rf_adsb_monitor failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
