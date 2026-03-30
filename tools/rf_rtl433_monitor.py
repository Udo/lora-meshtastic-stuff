#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import curses
import json
from pathlib import Path
import shutil
import sys
from dataclasses import dataclass, field

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
class DeviceState:
    key: str
    model: str
    count: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    last_signal: float | None = None
    last_channel: str | None = None
    preview: str = ""
    fields: dict[str, str] = field(default_factory=dict)


RTL433_PRESETS = {
    "433": {"frequency": 433.92, "sample_rate": 250000, "description": "Common 433 MHz ISM sensors"},
    "868": {"frequency": 868.30, "sample_rate": 250000, "description": "Common EU 868 MHz ISM sensors"},
    "915": {"frequency": 915.00, "sample_rate": 250000, "description": "Common 915 MHz ISM sensors"},
}


def default_rtl433_binary() -> str:
    return repo_local_binary(__file__, "rtl_433", "rtl_433")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live rtl_433 monitor using JSON output")
    parser.add_argument(
        "--preset",
        choices=tuple(RTL433_PRESETS.keys()),
        default="868",
        help="Named rtl_433 tuning preset (default: 868)",
    )
    parser.add_argument("--rtl433", default=default_rtl433_binary())
    parser.add_argument("--frequency", type=float, default=None, help="Tuned frequency in MHz")
    parser.add_argument("--sample-rate", type=int, default=None, help="rtl_433 sample rate")
    parser.add_argument("--gain", default="0", help="Gain in dB-tenths or 0 for auto")
    parser.add_argument("--ppm", type=int, default=0)
    parser.add_argument("--device-index", default="0")
    parser.add_argument("--protocol", action="append", default=[], help="Optional rtl_433 -R protocol filters")
    parser.add_argument("--fps", type=float, default=4.0)
    parser.add_argument("--max-devices", type=int, default=18)
    parser.add_argument("--show-raw", action="store_true")
    parser.add_argument("--log-file", help="Optional JSONL log path for decoded rtl_433 events")
    args = parser.parse_args()
    preset = RTL433_PRESETS[args.preset]
    if args.frequency is None:
        args.frequency = preset["frequency"]
    if args.sample_rate is None:
        args.sample_rate = preset["sample_rate"]
    return args


def build_command(args: argparse.Namespace) -> list[str]:
    cmd = [
        args.rtl433,
        "-d",
        str(args.device_index),
        "-f",
        f"{args.frequency}M",
        "-s",
        str(args.sample_rate),
        "-g",
        str(args.gain),
        "-p",
        str(args.ppm),
        "-F",
        "json",
        "-M",
        "time:iso",
        "-M",
        "level",
    ]
    for proto in args.protocol:
        cmd.extend(["-R", proto])
    return cmd


def device_key(record: dict) -> tuple[str, str]:
    model = str(record.get("model") or record.get("protocol") or "unknown")
    for field in ("id", "device", "device_id", "radio_id", "meter_id", "mic"):
        if field in record:
            return model, f"{field}={record[field]}"
    for field in ("channel", "subtype", "type"):
        if field in record:
            return model, f"{field}={record[field]}"
    return model, "unkeyed"


def summarize_record(record: dict) -> tuple[str, dict[str, str], float | None, str | None]:
    preferred_fields = [
        "temperature_C",
        "temperature_F",
        "humidity",
        "wind_avg_km_h",
        "wind_avg_m_s",
        "wind_dir_deg",
        "rain_mm",
        "battery_ok",
        "pressure_hPa",
        "state",
        "event",
        "code",
    ]
    fields: dict[str, str] = {}
    for name in preferred_fields:
        if name in record:
            fields[name] = str(record[name])
    preview = ", ".join(f"{k}={v}" for k, v in list(fields.items())[:3])
    signal = None
    for name in ("rssi", "snr", "noise"):
        if name in record:
            try:
                signal = float(record[name])
                break
            except Exception:
                pass
    channel = str(record["channel"]) if "channel" in record else None
    return preview, fields, signal, channel


def run_ui(stdscr: curses.window, args: argparse.Namespace) -> int:
    configure_curses(stdscr, args.fps)
    stdout_buffer = ""

    devices: dict[str, DeviceState] = {}
    raw_lines: collections.deque[str] = collections.deque(maxlen=8)
    started = current_time()
    total_records = 0
    bad_lines = 0
    last_draw = 0.0
    last_activity = 0.0
    last_status = "starting decoder"
    logger = EventLogger("rtl_433", args.log_file)

    try:
        with NonBlockingProcess(build_command(args), text=True, bufsize=1) as proc:
            while True:
                proc.assert_running("rtl_433 exited")
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
                        else:
                            model, key_suffix = device_key(record)
                            key_name = f"{model}|{key_suffix}"
                            state = devices.setdefault(key_name, DeviceState(key=key_name, model=model))
                            state.count += 1
                            now = current_time()
                            if state.first_seen == 0.0:
                                state.first_seen = now
                            state.last_seen = now
                            preview, fields, signal_level, channel = summarize_record(record)
                            state.preview = preview
                            state.fields = fields
                            state.last_signal = signal_level
                            state.last_channel = channel
                            total_records += 1
                            last_status = f"decoded {state.model[:20]} {preview or key_suffix}"
                            logger.log(timestamp=now, kind="record", data=record)

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
                rate = total_records / elapsed
                live_count = sum(1 for state in devices.values() if now - state.last_seen < 300.0)
                draw_text(
                    stdscr,
                    0,
                    0,
                    f"rtl_433 Monitor  preset={args.preset}  freq={args.frequency:.3f} MHz  fps={args.fps:.1f}  records={total_records}  rate={rate:.1f}/s  devices={live_count}",
                    curses.A_BOLD,
                )
                draw_text(
                    stdscr,
                    1,
                    0,
                    f"rtl_433={args.rtl433}  gain={args.gain}  ppm={args.ppm}  q=quit",
                )
                draw_text(
                    stdscr,
                    2,
                    0,
                    monitor_status_line(
                        now=now,
                        started=started,
                        last_activity=last_activity,
                        total_events=total_records,
                        idle_label="decoder alive, no accepted records yet",
                        receiving_label="receiving",
                        detail=last_status,
                        width=width,
                    ),
                )
                draw_text(stdscr, 4, 0, "Model                  Count  Last(s)  Sig     Channel  Preview")

                ranked = sorted(
                    devices.values(),
                    key=lambda item: (-item.last_seen, -item.count),
                )
                row = 5
                shown = 0
                for state in ranked:
                    age = now - state.last_seen
                    if age > 1800.0:
                        continue
                    signal_text = f"{state.last_signal:.1f}" if state.last_signal is not None else "-"
                    channel_text = state.last_channel or "-"
                    draw_text(
                        stdscr,
                        row,
                        0,
                        f"{state.model[:22]:<22} {state.count:>6}  {age:>7.1f}  {signal_text:>6}  {channel_text[:7]:<7}  {state.preview[: max(0, width - 55)]}",
                    )
                    row += 1
                    shown += 1
                    if shown >= args.max_devices or row >= height - 4:
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
    if shutil.which(args.rtl433) is None:
        repo_local = Path(__file__).resolve().parents[1] / "rtl2838" / "local" / "bin" / "rtl_433"
        print(
            "rf_rtl433_monitor failed: missing rtl_433. Run ./setup/rtl2838.sh bootstrap to build the repo-local copy"
            + (f" at {repo_local}" if not repo_local.exists() else f" ({repo_local})"),
            file=sys.stderr,
        )
        return 1
    try:
        ensure_dongle_mode(__file__, "libusb")
        return curses.wrapper(run_ui, args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"rf_rtl433_monitor failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
