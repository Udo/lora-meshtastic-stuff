#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import curses
import os
import re
import sys
import time
from dataclasses import dataclass

import numpy as np
from _rf_monitor_common import NonBlockingProcess, configure_curses, draw_text, ensure_dongle_mode


@dataclass(frozen=True)
class Marker:
    label: str
    freq_hz: int


@dataclass(frozen=True)
class Preset:
    center_hz: int
    span_hz: int
    markers: str
    description: str


EU868_LOW_MARKERS = [
    Marker("867.1", 867_100_000),
    Marker("867.3", 867_300_000),
    Marker("867.5", 867_500_000),
    Marker("867.7", 867_700_000),
    Marker("867.9", 867_900_000),
    Marker("868.1", 868_100_000),
    Marker("868.3", 868_300_000),
    Marker("868.5", 868_500_000),
]

EU868_HIGH_MARKERS = [
    Marker("869.525", 869_525_000),
    Marker("869.850", 869_850_000),
]

EU868_WIDE_MARKERS = EU868_LOW_MARKERS + EU868_HIGH_MARKERS

ISM433_MARKERS = [
    Marker("433.050", 433_050_000),
    Marker("433.920", 433_920_000),
    Marker("434.790", 434_790_000),
]

PMR446_MARKERS = [
    Marker("446.006", 446_006_250),
    Marker("446.094", 446_093_750),
    Marker("446.194", 446_193_750),
]

AIRBAND_MARKERS = [
    Marker("131.525", 131_525_000),
    Marker("131.725", 131_725_000),
    Marker("131.825", 131_825_000),
    Marker("121.500", 121_500_000),
]

ADSB1090_MARKERS = [
    Marker("1090.000", 1_090_000_000),
]

APRS_MARKERS = [
    Marker("144.390", 144_390_000),
    Marker("144.800", 144_800_000),
    Marker("145.825", 145_825_000),
]

AIS_MARKERS = [
    Marker("156.800", 156_800_000),
    Marker("161.975", 161_975_000),
    Marker("162.025", 162_025_000),
]

ACARS_MARKERS = [
    Marker("131.525", 131_525_000),
    Marker("131.550", 131_550_000),
    Marker("131.725", 131_725_000),
    Marker("131.825", 131_825_000),
    Marker("131.850", 131_850_000),
]

RTL433_433_MARKERS = ISM433_MARKERS

RTL433_868_MARKERS = [
    Marker("868.100", 868_100_000),
    Marker("868.300", 868_300_000),
    Marker("868.500", 868_500_000),
    Marker("868.950", 868_950_000),
    Marker("869.525", 869_525_000),
]

RTL433_915_MARKERS = [
    Marker("914.900", 914_900_000),
    Marker("915.000", 915_000_000),
    Marker("915.200", 915_200_000),
]

AM_BROADCAST_MARKERS = [
    Marker("540", 540_000),
    Marker("720", 720_000),
    Marker("900", 900_000),
    Marker("1080", 1_080_000),
    Marker("1260", 1_260_000),
    Marker("1440", 1_440_000),
    Marker("1600", 1_600_000),
]

SHORTWAVE_MARKERS = [
    Marker("5.0", 5_000_000),
    Marker("7.1", 7_100_000),
    Marker("9.5", 9_500_000),
    Marker("11.7", 11_700_000),
    Marker("13.8", 13_800_000),
]

NOAA_WEATHER_MARKERS = [
    Marker("162.400", 162_400_000),
    Marker("162.425", 162_425_000),
    Marker("162.450", 162_450_000),
    Marker("162.475", 162_475_000),
    Marker("162.500", 162_500_000),
    Marker("162.525", 162_525_000),
    Marker("162.550", 162_550_000),
]

MARINE_VHF_MARKERS = [
    Marker("156.800", 156_800_000),
    Marker("156.300", 156_300_000),
    Marker("157.100", 157_100_000),
]

CB27_MARKERS = [
    Marker("26.965", 26_965_000),
    Marker("27.185", 27_185_000),
    Marker("27.405", 27_405_000),
]

HAM_10M_MARKERS = [
    Marker("28.400", 28_400_000),
    Marker("29.000", 29_000_000),
    Marker("29.600", 29_600_000),
]

HAM_6M_MARKERS = [
    Marker("50.125", 50_125_000),
    Marker("52.525", 52_525_000),
]

HAM_2M_MARKERS = [
    Marker("144.390", 144_390_000),
    Marker("144.800", 144_800_000),
    Marker("145.500", 145_500_000),
    Marker("146.520", 146_520_000),
]

MURS_MARKERS = [
    Marker("151.820", 151_820_000),
    Marker("151.880", 151_880_000),
    Marker("151.940", 151_940_000),
    Marker("154.570", 154_570_000),
    Marker("154.600", 154_600_000),
]

HAM_70CM_MARKERS = [
    Marker("433.000", 433_000_000),
    Marker("433.920", 433_920_000),
    Marker("446.000", 446_000_000),
]

FRS_GMRS_MARKERS = [
    Marker("462.5625", 462_562_500),
    Marker("462.6750", 462_675_000),
    Marker("462.7250", 462_725_000),
    Marker("467.5625", 467_562_500),
    Marker("467.7125", 467_712_500),
]

ISM915_MARKERS = [
    Marker("902.300", 902_300_000),
    Marker("915.000", 915_000_000),
    Marker("927.500", 927_500_000),
]

CHARSET_PRESETS = {
    "blocks": " ▁▂▃▄▅▆▇█",
    "braille": " ⠂⠆⠖⠶⠷⠿⣿",
    "ascii": " .:-=+*#%@",
    "dense": " .`^,:;Il!i~+_-?][}{1)(|/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$",
}
DEFAULT_DEVICE = os.environ.get("RTL2838_DEVICE", "/dev/swradio0")
DEFAULT_PRESET = os.environ.get("RTL2838_PROFILE", os.environ.get("RTL2838_PRESET", "eu868-wide"))
DEFAULT_CENTER_FREQ_HZ = int(os.environ.get("RTL2838_CENTER_FREQ_HZ", "868475000"))
DEFAULT_SAMPLE_RATE = int(os.environ.get("RTL2838_SAMPLE_RATE", "3200000"))
DEFAULT_PROFILE = os.environ.get("RTL2838_PROFILE", "eu868-wide")
DEFAULT_CHARSET = os.environ.get("RTL2838_CHARSET", "blocks")
DEFAULT_FPS = float(os.environ.get("RTL2838_FPS", "12.0"))
DEFAULT_NORM_MODE = os.environ.get("RTL2838_NORM_MODE", "slow")
DEFAULT_NORM_ALPHA = float(os.environ.get("RTL2838_NORM_ALPHA", "0.08"))
DEFAULT_NORM_MIN_RANGE_DB = float(os.environ.get("RTL2838_NORM_MIN_RANGE_DB", "18.0"))
DEFAULT_NORM_START_HEADROOM_DB = float(os.environ.get("RTL2838_NORM_START_HEADROOM_DB", "8.0"))
DEFAULT_AVG_FRAMES = int(os.environ.get("RTL2838_AVG_FRAMES", "3"))

MARKER_SETS = {
    "none": [],
    "max-span": [],
    "eu868-low": EU868_LOW_MARKERS,
    "eu868-high": EU868_HIGH_MARKERS,
    "eu868-wide": EU868_WIDE_MARKERS,
    "am-broadcast": AM_BROADCAST_MARKERS,
    "shortwave": SHORTWAVE_MARKERS,
    "weather": NOAA_WEATHER_MARKERS,
    "marine-vhf": MARINE_VHF_MARKERS,
    "cb-27mhz": CB27_MARKERS,
    "ham-10m": HAM_10M_MARKERS,
    "ham-6m": HAM_6M_MARKERS,
    "ham-2m": HAM_2M_MARKERS,
    "murs": MURS_MARKERS,
    "ism433": ISM433_MARKERS,
    "pmr446": PMR446_MARKERS,
    "ham-70cm": HAM_70CM_MARKERS,
    "frs-gmrs": FRS_GMRS_MARKERS,
    "ism915": ISM915_MARKERS,
    "airband": AIRBAND_MARKERS,
    "adsb1090": ADSB1090_MARKERS,
    "adsb-monitor": ADSB1090_MARKERS,
    "aprs": APRS_MARKERS,
    "aprs-monitor": APRS_MARKERS,
    "ais": AIS_MARKERS,
    "ais-monitor": AIS_MARKERS,
    "acars": ACARS_MARKERS,
    "acars-monitor": ACARS_MARKERS,
    "weather-alert": NOAA_WEATHER_MARKERS,
    "weather-alert-monitor": NOAA_WEATHER_MARKERS,
    "rtl433-433": RTL433_433_MARKERS,
    "rtl433-868": RTL433_868_MARKERS,
    "rtl433-915": RTL433_915_MARKERS,
    "fm-broadcast": [],
    "broadband-868": [],
}

PRESETS = {
    "max-span": Preset(868_475_000, 3_200_000, "none", "Maximum instantaneous span supported by this workflow"),
    "eu868-wide": Preset(868_475_000, 3_200_000, "eu868-wide", "Wide EU868 overview"),
    "eu868-low": Preset(867_900_000, 2_048_000, "eu868-low", "Lower EU868 LoRa channels"),
    "eu868-high": Preset(869_525_000, 2_048_000, "eu868-high", "Upper EU868 / Meshtastic-ish area"),
    "am-broadcast": Preset(1_050_000, 1_600_000, "am-broadcast", "AM broadcast band"),
    "shortwave-49m": Preset(6_100_000, 2_400_000, "shortwave", "Shortwave 49m-ish listening window"),
    "shortwave-31m": Preset(9_650_000, 2_400_000, "shortwave", "Shortwave 31m-ish listening window"),
    "weather": Preset(162_475_000, 400_000, "weather", "NOAA weather radio"),
    "marine-vhf": Preset(156_800_000, 2_400_000, "marine-vhf", "Marine VHF"),
    "cb-27mhz": Preset(27_185_000, 2_400_000, "cb-27mhz", "11m CB radio"),
    "ham-10m": Preset(28_850_000, 3_200_000, "ham-10m", "10 meter amateur band"),
    "ham-6m": Preset(51_000_000, 2_400_000, "ham-6m", "6 meter amateur band"),
    "ham-2m": Preset(146_000_000, 4_000_000, "ham-2m", "2 meter amateur band"),
    "murs": Preset(152_500_000, 4_000_000, "murs", "US MURS channels"),
    "ism433": Preset(433_920_000, 2_400_000, "ism433", "433 MHz ISM devices"),
    "pmr446": Preset(446_100_000, 2_400_000, "pmr446", "PMR446 handheld channels"),
    "ham-70cm": Preset(434_500_000, 4_000_000, "ham-70cm", "70cm amateur / LPD-ish region"),
    "frs-gmrs": Preset(465_137_500, 6_000_000, "frs-gmrs", "FRS / GMRS handheld channels"),
    "ism915": Preset(915_000_000, 12_000_000, "ism915", "902-928 MHz ISM band slice"),
    "airband": Preset(121_500_000, 2_400_000, "airband", "Airband around 121.5 MHz"),
    "adsb1090": Preset(1_090_000_000, 2_400_000, "adsb1090", "ADS-B / Mode S at 1090 MHz"),
    "adsb-monitor": Preset(1_090_000_000, 2_400_000, "adsb-monitor", "ADS-B / Mode S monitor band"),
    "aprs": Preset(144_800_000, 2_400_000, "aprs", "APRS around 144.800 MHz"),
    "aprs-monitor": Preset(144_800_000, 2_400_000, "aprs-monitor", "APRS monitor band"),
    "ais": Preset(162_000_000, 1_200_000, "ais", "AIS marine channels"),
    "ais-monitor": Preset(162_000_000, 1_200_000, "ais-monitor", "AIS marine monitor band"),
    "acars": Preset(131_700_000, 2_400_000, "acars", "ACARS VHF channels"),
    "acars-monitor": Preset(131_700_000, 2_400_000, "acars-monitor", "ACARS monitor band"),
    "weather-alert": Preset(162_475_000, 400_000, "weather-alert", "Weather alert channels"),
    "weather-alert-monitor": Preset(162_475_000, 400_000, "weather-alert-monitor", "Weather alert monitor band"),
    "rtl433-433": Preset(433_920_000, 2_400_000, "rtl433-433", "rtl_433 433 MHz sensor band"),
    "rtl433-868": Preset(868_300_000, 2_048_000, "rtl433-868", "rtl_433 868 MHz sensor band"),
    "rtl433-915": Preset(915_000_000, 2_400_000, "rtl433-915", "rtl_433 915 MHz sensor band"),
    "fm-broadcast": Preset(100_500_000, 3_200_000, "none", "Wide FM broadcast slice"),
    "broadband-868": Preset(868_475_000, 3_200_000, "none", "Max-span EU868 broadband view"),
}


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
        choices=tuple(PRESETS.keys()),
        default=DEFAULT_PRESET,
        help=f"Named band profile for center/span/default markers (default: {DEFAULT_PRESET})",
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
    preset = PRESETS[args.profile_name]
    if args.center_freq_hz is None:
        args.center_freq_hz = preset.center_hz
    if args.sample_rate is None:
        args.sample_rate = preset.span_hz
    args.base_markers = preset.markers
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
