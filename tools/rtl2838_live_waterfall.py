#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import curses
import os
import re
import sys
import time

import numpy as np
from _rf_monitor_common import NonBlockingProcess, configure_curses, draw_text, ensure_dongle_mode
from _rf_profiles import MARKER_SETS, WATERFALL_PROFILES, Marker

CHARSET_PRESETS = {
    "blocks": " ▁▂▃▄▅▆▇█",
    "braille": " ⠂⠆⠖⠶⠷⠿⣿",
    "ascii": " .:-=+*#%@",
    "dense": " .`^,:;Il!i~+_-?][}{1)(|/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$",
}
DEFAULT_DEVICE = os.environ.get("RTL2838_DEVICE", "/dev/swradio0")
DEFAULT_PROFILE_NAME = os.environ.get("RTL2838_PROFILE", os.environ.get("RTL2838_PRESET", "eu868-wide"))
DEFAULT_CENTER_FREQ_HZ = int(os.environ.get("RTL2838_CENTER_FREQ_HZ", "868475000"))
DEFAULT_SAMPLE_RATE = int(os.environ.get("RTL2838_SAMPLE_RATE", "3200000"))
DEFAULT_CHARSET = os.environ.get("RTL2838_CHARSET", "blocks")
DEFAULT_FPS = float(os.environ.get("RTL2838_FPS", "12.0"))
DEFAULT_NORM_MODE = os.environ.get("RTL2838_NORM_MODE", "slow")
DEFAULT_NORM_ALPHA = float(os.environ.get("RTL2838_NORM_ALPHA", "0.08"))
DEFAULT_NORM_MIN_RANGE_DB = float(os.environ.get("RTL2838_NORM_MIN_RANGE_DB", "18.0"))
DEFAULT_NORM_START_HEADROOM_DB = float(os.environ.get("RTL2838_NORM_START_HEADROOM_DB", "8.0"))
DEFAULT_AVG_FRAMES = int(os.environ.get("RTL2838_AVG_FRAMES", "3"))

MARKER_SPEC_RE = re.compile(r"^\s*(?:(?P<label>[^=]+)=)?(?P<freq>.+?)\s*$")


def parse_freq_spec(spec: str) -> int:
    text = spec.strip().lower().replace("_", "")
    multiplier = 1
    if text.endswith("mhz"):
        text = text[:-3]
        multiplier = 1_000_000
    elif text.endswith("khz"):
        text = text[:-3]
        multiplier = 1_000
    elif text.endswith("hz"):
        text = text[:-2]
    elif "." in text:
        multiplier = 1_000_000
    return int(float(text) * multiplier)


def parse_marker_list(spec: str) -> list[Marker]:
    markers: list[Marker] = []
    if not spec.strip():
        return markers
    for item in spec.split(","):
        chunk = item.strip()
        if not chunk:
            continue
        match = MARKER_SPEC_RE.match(chunk)
        if not match:
            raise ValueError(f"invalid marker spec: {chunk}")
        freq_hz = parse_freq_spec(match.group("freq"))
        label = (match.group("label") or f"{freq_hz / 1e6:.3f}").strip()
        markers.append(Marker(label, freq_hz))
    return markers


def merge_markers(base: list[Marker], extra: list[Marker]) -> list[Marker]:
    merged: list[Marker] = []
    seen: set[tuple[str, int]] = set()
    for marker in [*base, *extra]:
        key = (marker.label, marker.freq_hz)
        if key in seen:
            continue
        seen.add(key)
        merged.append(marker)
    return merged


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Realtime console waterfall for RTL2838 via kernel V4L2 SDR")
    parser.add_argument("--device", default=DEFAULT_DEVICE, help=f"Kernel SDR device path (default: {DEFAULT_DEVICE})")
    parser.add_argument(
        "--profile",
        "--preset",
        dest="profile_name",
        choices=tuple(WATERFALL_PROFILES.keys()),
        default=DEFAULT_PROFILE_NAME,
        help=f"Named band profile for center/span/default markers (default: {DEFAULT_PROFILE_NAME})",
    )
    parser.add_argument(
        "--center",
        "--center-freq-hz",
        dest="center_freq_hz",
        type=int,
        default=None,
        help="Center frequency in Hz; overrides the preset center if provided",
    )
    parser.add_argument(
        "--span",
        "--sample-rate",
        dest="sample_rate",
        type=int,
        default=None,
        help="Spectrum span in Hz, implemented via SDR sample rate; overrides the preset span if provided",
    )
    parser.add_argument(
        "--markers",
        default="",
        help="Additional markers to add as a comma-separated list like 144.800,145.825 or aprs=144.800,iss=145.825",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=DEFAULT_FPS,
        help=f"Target UI refresh rate in frames per second (default: {DEFAULT_FPS})",
    )
    parser.add_argument(
        "--charset",
        default=DEFAULT_CHARSET,
        help="Character preset name (blocks, braille, ascii, dense) or a custom string of glyphs ordered from low to high intensity",
    )
    parser.add_argument(
        "--norm-mode",
        choices=("slow", "row"),
        default=DEFAULT_NORM_MODE,
        help="Normalization mode: slow uses an adapting floor/ceiling across time, row rescales each row independently",
    )
    parser.add_argument(
        "--norm-alpha",
        type=float,
        default=DEFAULT_NORM_ALPHA,
        help="Update rate for slow normalization; lower values adapt more slowly",
    )
    parser.add_argument(
        "--norm-min-range-db",
        type=float,
        default=DEFAULT_NORM_MIN_RANGE_DB,
        help="Minimum normalization range in dB so quiet noise does not fully light up the display",
    )
    parser.add_argument(
        "--norm-start-headroom-db",
        type=float,
        default=DEFAULT_NORM_START_HEADROOM_DB,
        help="Extra headroom used when slow normalization initializes so the display starts conservative and becomes more sensitive over time",
    )
    parser.add_argument("--fft-size", type=int, default=2048)
    parser.add_argument("--avg-frames", type=int, default=DEFAULT_AVG_FRAMES)
    parser.add_argument("--gain-text", default="kernel-v4l2")
    parser.add_argument("--bandwidth", type=int, default=0)
    args = parser.parse_args()
    profile = WATERFALL_PROFILES[args.profile_name]
    if args.center_freq_hz is None:
        args.center_freq_hz = profile.center_hz
    if args.sample_rate is None:
        args.sample_rate = profile.span_hz
    args.base_markers = profile.marker_set
    args.extra_markers = parse_marker_list(args.markers)
    return args


def hz_to_mhz(freq_hz: int) -> str:
    return f"{freq_hz / 1e6:.6f}"


def choose_markers(profile: str) -> list[Marker]:
    return MARKER_SETS.get(profile, [])


def choose_charset(charset: str) -> str:
    palette = CHARSET_PRESETS.get(charset, charset)
    if len(palette) < 2:
        raise ValueError("charset must contain at least 2 characters")
    return palette


def build_stream_command(args: argparse.Namespace) -> list[str]:
    cmd = [
        "v4l2-ctl",
        "-d",
        args.device,
        f"--set-freq={hz_to_mhz(args.center_freq_hz)}",
        "--set-fmt-sdr=samplefmt=CU08",
        "--stream-mmap=4",
        "--stream-to=-",
    ]
    return cmd


def raw_to_complex(raw: bytes) -> np.ndarray:
    data = np.frombuffer(raw, dtype=np.uint8)
    if data.size < 2:
        return np.empty(0, dtype=np.complex64)
    if data.size % 2:
        data = data[:-1]
    iq = data.astype(np.float32).reshape(-1, 2)
    i = (iq[:, 0] - 127.5) / 128.0
    q = (iq[:, 1] - 127.5) / 128.0
    return (i + 1j * q).astype(np.complex64)


def compute_row(samples: np.ndarray, fft_size: int, avg_frames: int) -> np.ndarray:
    if samples.size < fft_size:
        return np.zeros(fft_size, dtype=np.float32)

    usable = min(samples.size, fft_size * avg_frames)
    frame_count = max(1, usable // fft_size)
    trimmed = samples[: frame_count * fft_size]
    frames = trimmed.reshape(frame_count, fft_size)
    window = np.hanning(fft_size).astype(np.float32)
    spectra = np.fft.fftshift(np.fft.fft(frames * window, axis=1), axes=1)
    power = 20.0 * np.log10(np.abs(spectra) + 1e-12)
    return power.mean(axis=0).astype(np.float32)


def resample_row(row: np.ndarray, width: int) -> np.ndarray:
    if width <= 0:
        return np.empty(0, dtype=np.float32)
    if row.size == width:
        return row
    src_x = np.linspace(0.0, 1.0, row.size)
    dst_x = np.linspace(0.0, 1.0, width)
    return np.interp(dst_x, src_x, row).astype(np.float32)


def marker_columns(markers: list[Marker], center_hz: int, sample_rate: int, width: int) -> dict[int, str]:
    half_span = sample_rate / 2.0
    start = center_hz - half_span
    end = center_hz + half_span
    out: dict[int, str] = {}
    for marker in markers:
        if marker.freq_hz < start or marker.freq_hz > end or width <= 1:
          continue
        position = (marker.freq_hz - start) / (end - start)
        column = int(round(position * (width - 1)))
        out[column] = marker.label
    return out


def init_colors() -> list[int]:
    color_ids: list[int] = []
    if not curses.has_colors():
        return color_ids
    curses.start_color()
    try:
        curses.use_default_colors()
    except curses.error:
        pass
    palette = [17, 18, 19, 20, 27, 33, 39, 45, 81, 118, 190, 226, 214, 208, 202, 196]
    for idx, color in enumerate(palette, start=1):
        try:
            curses.init_pair(idx, color, -1)
            color_ids.append(idx)
        except curses.error:
            break
    return color_ids


def row_percentiles(values: np.ndarray) -> tuple[float, float]:
    lo = float(np.percentile(values, 20))
    hi = float(np.percentile(values, 99))
    if hi <= lo:
        hi = lo + 1.0
    return lo, hi


def normalize_row(values: np.ndarray, lo: float, hi: float) -> np.ndarray:
    if hi <= lo:
        hi = lo + 1.0
    return np.clip((values - lo) / (hi - lo), 0.0, 1.0)


def draw_row(
    stdscr,
    y: int,
    norm_values: np.ndarray,
    color_ids: list[int],
    markers: dict[int, str],
    charset: str,
) -> None:
    height, width = stdscr.getmaxyx()
    if y < 0 or y >= height:
        return
    if norm_values.size == 0:
        return

    for x, value in enumerate(norm_values[: max(0, width - 1)]):
        idx = int(value * (len(charset) - 1))
        ch = charset[idx]
        attr = 0
        if color_ids:
            color_index = min(len(color_ids) - 1, int(value * (len(color_ids) - 1)))
            attr |= curses.color_pair(color_ids[color_index])
        if x in markers:
            ch = "|"
            attr |= curses.A_BOLD
        try:
            stdscr.addstr(y, x, ch, attr)
        except curses.error:
            pass


def run_ui(stdscr: curses.window, args: argparse.Namespace) -> int:
    configure_curses(stdscr, args.fps)
    color_ids = init_colors()
    markers = merge_markers(choose_markers(args.base_markers), args.extra_markers)
    charset = choose_charset(args.charset)
    cmd = build_stream_command(args)

    chunk_bytes = args.fft_size * args.avg_frames * 2
    rows: collections.deque[np.ndarray] = collections.deque()
    last_row = np.zeros(args.fft_size, dtype=np.float32)
    norm_lo = None
    norm_hi = None
    actual_rate = 0.0
    frame_count = 0
    started_at = time.monotonic()
    next_draw_at = started_at

    with NonBlockingProcess(cmd, text=False, bufsize=0) as proc:
        while True:
            proc.assert_running("v4l2-ctl stream ended unexpectedly")
            raw_chunk, _stderr_lines = proc.read_available(stdout_bytes=chunk_bytes)
            raw = bytes(raw_chunk) if raw_chunk else b""
            if raw:
                samples = raw_to_complex(raw)
                if samples.size:
                    last_row = compute_row(samples, args.fft_size, args.avg_frames)
                    rows.append(last_row)
                    row_lo, row_hi = row_percentiles(last_row)
                    if args.norm_mode == "slow":
                        alpha = min(1.0, max(0.001, args.norm_alpha))
                        fast_alpha = min(1.0, alpha * 2.0)
                        if norm_lo is None or norm_hi is None:
                            norm_hi = row_hi + args.norm_start_headroom_db
                            norm_lo = norm_hi - max(args.norm_min_range_db, row_hi - row_lo)
                        else:
                            if row_lo < norm_lo:
                                norm_lo = ((1.0 - alpha) * norm_lo) + (alpha * row_lo)
                            else:
                                norm_lo = ((1.0 - fast_alpha) * norm_lo) + (fast_alpha * row_lo)
                            if row_hi > norm_hi:
                                norm_hi = ((1.0 - fast_alpha) * norm_hi) + (fast_alpha * row_hi)
                            else:
                                norm_hi = ((1.0 - alpha) * norm_hi) + (alpha * row_hi)
                        if norm_hi - norm_lo < args.norm_min_range_db:
                            norm_lo = norm_hi - args.norm_min_range_db
                    frame_count += 1
                    elapsed = max(0.001, time.monotonic() - started_at)
                    actual_rate = frame_count / elapsed

            height, width = stdscr.getmaxyx()
            now = time.monotonic()
            if now < next_draw_at:
                key = stdscr.getch()
                if key in (ord("q"), ord("Q"), 27):
                    return 0
                continue
            next_draw_at = now + (1.0 / max(1.0, args.fps))

            plot_top = 2
            plot_bottom = max(plot_top + 1, height - 3)
            plot_height = plot_bottom - plot_top
            plot_width = max(16, width - 1)
            while len(rows) > plot_height:
                rows.popleft()

            stdscr.erase()
            draw_text(
                stdscr,
                0,
                0,
                f"RTL2838 Live Waterfall  profile={args.profile_name}  center={args.center_freq_hz / 1e6:.3f} MHz  span={args.sample_rate / 1e6:.3f} MHz  fps={args.fps:.1f}  markers={args.base_markers}{'+' if args.extra_markers else ''}",
                curses.A_BOLD,
            )
            draw_text(
                stdscr,
                1,
                0,
                f"device={args.device}  charset={args.charset}  norm={args.norm_mode}  actual={actual_rate:.1f}  q=quit",
            )

            marker_map = marker_columns(markers, args.center_freq_hz, args.sample_rate, plot_width)
            rendered = []
            for row in rows:
                if args.norm_mode == "row":
                    row_lo, row_hi = row_percentiles(row)
                else:
                    row_lo = norm_lo if norm_lo is not None else row_percentiles(row)[0]
                    row_hi = norm_hi if norm_hi is not None else row_percentiles(row)[1]
                rendered.append(resample_row(normalize_row(row, row_lo, row_hi), plot_width))
            blank_rows = plot_height - len(rendered)
            for idx in range(blank_rows):
                draw_text(stdscr, plot_top + idx, 0, " " * max(0, plot_width - 1))
            for idx, row in enumerate(rendered):
                draw_row(stdscr, plot_top + blank_rows + idx, row, color_ids, marker_map, charset)

            freq_left = args.center_freq_hz - (args.sample_rate / 2.0)
            freq_right = args.center_freq_hz + (args.sample_rate / 2.0)
            axis_y = height - 2
            draw_text(stdscr, axis_y, 0, f"{freq_left / 1e6:9.3f} MHz")
            center_label = f"{args.center_freq_hz / 1e6:9.3f} MHz"
            draw_text(stdscr, axis_y, max(0, (width // 2) - (len(center_label) // 2)), center_label)
            right_label = f"{freq_right / 1e6:9.3f} MHz"
            draw_text(stdscr, axis_y, max(0, width - len(right_label) - 1), right_label)

            current = resample_row(last_row, plot_width)
            if current.size:
                peak_bin = int(np.argmax(current))
                peak_freq = freq_left + ((peak_bin / max(1, plot_width - 1)) * (freq_right - freq_left))
                peak_db = float(np.max(current))
                if args.norm_mode == "row":
                    display_lo, display_hi = row_percentiles(last_row)
                else:
                    display_lo = norm_lo if norm_lo is not None else row_percentiles(last_row)[0]
                    display_hi = norm_hi if norm_hi is not None else row_percentiles(last_row)[1]
                draw_text(
                    stdscr,
                    height - 1,
                    0,
                    f"peak={peak_freq / 1e6:.4f} MHz  level={peak_db:.1f} dB  floor={display_lo:.1f}  ceil={display_hi:.1f}",
                )

            stdscr.refresh()
            key = stdscr.getch()
            if key in (ord("q"), ord("Q"), 27):
                return 0


def main() -> int:
    args = parse_args()
    try:
        ensure_dongle_mode(__file__, "kernel", kernel_device=args.device)
        return curses.wrapper(run_ui, args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"rtl2838 live waterfall failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
