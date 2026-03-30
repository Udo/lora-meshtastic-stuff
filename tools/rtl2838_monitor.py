#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


@dataclass(frozen=True)
class Marker:
    label: str
    freq_hz: int


EU868_LOW_MARKERS = [
    Marker("EU868 867.1", 867_100_000),
    Marker("EU868 867.3", 867_300_000),
    Marker("EU868 867.5", 867_500_000),
    Marker("EU868 867.7", 867_700_000),
    Marker("EU868 867.9", 867_900_000),
    Marker("EU868 868.1", 868_100_000),
    Marker("EU868 868.3", 868_300_000),
    Marker("EU868 868.5", 868_500_000),
]

EU868_HIGH_MARKERS = [
    Marker("Meshtastic-ish 869.525", 869_525_000),
    Marker("EU868 869.850", 869_850_000),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze RTL2838 raw CU8 IQ captures and render a waterfall plus PSD."
    )
    parser.add_argument("--input", required=True, help="Path to raw CU8 IQ capture")
    parser.add_argument("--center-freq-hz", required=True, type=int)
    parser.add_argument("--sample-rate", required=True, type=int, help="Samples/sec")
    parser.add_argument("--output-prefix", required=True, help="Output path prefix without extension")
    parser.add_argument("--title", default="")
    parser.add_argument(
        "--profile",
        choices=("eu868-low", "eu868-high", "none"),
        default="none",
        help="Adds common marker frequencies for the selected monitoring profile.",
    )
    parser.add_argument(
        "--fft-size",
        type=int,
        default=4096,
        help="FFT size for waterfall/PSD generation",
    )
    parser.add_argument(
        "--hop-size",
        type=int,
        default=1024,
        help="Sample hop between FFT frames",
    )
    parser.add_argument(
        "--marker-bandwidth-hz",
        type=int,
        default=125_000,
        help="Bandwidth used when estimating marker band power",
    )
    return parser.parse_args()


def load_cu8(path: Path) -> np.ndarray:
    raw = np.fromfile(path, dtype=np.uint8)
    if raw.size < 2:
      raise ValueError(f"{path} is too small to contain IQ data")
    if raw.size % 2:
        raw = raw[:-1]
    iq = raw.astype(np.float32).reshape(-1, 2)
    i = (iq[:, 0] - 127.5) / 128.0
    q = (iq[:, 1] - 127.5) / 128.0
    return i + 1j * q


def choose_markers(profile: str) -> list[Marker]:
    if profile == "eu868-low":
        return EU868_LOW_MARKERS
    if profile == "eu868-high":
        return EU868_HIGH_MARKERS
    return []


def stft_db(samples: np.ndarray, fft_size: int, hop_size: int) -> tuple[np.ndarray, np.ndarray]:
    if samples.size < fft_size:
        padding = np.zeros(fft_size - samples.size, dtype=samples.dtype)
        samples = np.concatenate([samples, padding])

    frame_count = 1 + max(0, (samples.size - fft_size) // hop_size)
    window = np.hanning(fft_size).astype(np.float32)
    spec = np.empty((frame_count, fft_size), dtype=np.float32)

    for idx in range(frame_count):
        start = idx * hop_size
        frame = samples[start : start + fft_size]
        if frame.size < fft_size:
            padded = np.zeros(fft_size, dtype=samples.dtype)
            padded[: frame.size] = frame
            frame = padded
        fft = np.fft.fftshift(np.fft.fft(frame * window))
        spec[idx] = 20.0 * np.log10(np.abs(fft) + 1e-12)

    freq_offsets = np.fft.fftshift(np.fft.fftfreq(fft_size, d=1.0))
    return spec, freq_offsets


def top_peaks(freqs_hz: np.ndarray, psd_db: np.ndarray, count: int = 8) -> list[dict[str, float]]:
    order = np.argsort(psd_db)[::-1]
    peaks: list[dict[str, float]] = []
    used: list[int] = []

    for idx in order:
        if len(peaks) >= count:
            break
        if any(abs(idx - prev) < 8 for prev in used):
            continue
        used.append(int(idx))
        peaks.append(
            {
                "freq_hz": float(freqs_hz[idx]),
                "freq_mhz": float(freqs_hz[idx] / 1e6),
                "power_db": float(psd_db[idx]),
            }
        )
    return peaks


def marker_band_summary(
    freqs_hz: np.ndarray, psd_db: np.ndarray, markers: list[Marker], bandwidth_hz: int
) -> list[dict[str, float | str]]:
    half_bw = bandwidth_hz / 2.0
    summary = []
    for marker in markers:
        mask = (freqs_hz >= marker.freq_hz - half_bw) & (freqs_hz <= marker.freq_hz + half_bw)
        if not np.any(mask):
            continue
        summary.append(
            {
                "label": marker.label,
                "freq_hz": marker.freq_hz,
                "freq_mhz": marker.freq_hz / 1e6,
                "peak_db": float(np.max(psd_db[mask])),
                "mean_db": float(np.mean(psd_db[mask])),
            }
        )
    return summary


def render(
    spec_db: np.ndarray,
    freqs_hz: np.ndarray,
    times_s: np.ndarray,
    psd_db: np.ndarray,
    output_png: Path,
    title: str,
    markers: list[Marker],
) -> None:
    fig, (ax_psd, ax_wf) = plt.subplots(
        2, 1, figsize=(15, 10), constrained_layout=True, height_ratios=(1, 2)
    )

    x_mhz = freqs_hz / 1e6
    ax_psd.plot(x_mhz, psd_db, color="#0f4c5c", linewidth=1.2)
    ax_psd.set_ylabel("PSD [dB]")
    ax_psd.set_xlabel("Frequency [MHz]")
    ax_psd.grid(True, alpha=0.25)
    ax_psd.set_title(title or "RTL2838 Capture PSD")

    vmin = float(np.percentile(spec_db, 5))
    vmax = float(np.percentile(spec_db, 99))
    im = ax_wf.imshow(
        spec_db,
        aspect="auto",
        origin="lower",
        extent=[x_mhz[0], x_mhz[-1], times_s[0], times_s[-1] if times_s.size else 0.0],
        cmap="magma",
        vmin=vmin,
        vmax=vmax,
    )
    ax_wf.set_xlabel("Frequency [MHz]")
    ax_wf.set_ylabel("Time [s]")
    ax_wf.set_title("Waterfall")

    for marker in markers:
        marker_mhz = marker.freq_hz / 1e6
        for ax in (ax_psd, ax_wf):
            ax.axvline(marker_mhz, color="#4ecdc4", linestyle="--", linewidth=0.8, alpha=0.65)
        ax_psd.text(
            marker_mhz,
            ax_psd.get_ylim()[1],
            marker.label,
            rotation=90,
            va="top",
            ha="right",
            fontsize=8,
            color="#1b4332",
        )

    cbar = fig.colorbar(im, ax=ax_wf, pad=0.01)
    cbar.set_label("Power [dB]")
    fig.savefig(output_png, dpi=150)
    plt.close(fig)


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    output_prefix = Path(args.output_prefix)
    output_prefix.parent.mkdir(parents=True, exist_ok=True)

    samples = load_cu8(input_path)
    spec_db, freq_offsets = stft_db(samples, args.fft_size, args.hop_size)
    freq_axis_hz = args.center_freq_hz + (freq_offsets * args.sample_rate)
    psd_db = np.median(spec_db, axis=0)
    frame_duration = args.hop_size / args.sample_rate
    times_s = np.arange(spec_db.shape[0], dtype=np.float32) * frame_duration
    markers = choose_markers(args.profile)

    png_path = output_prefix.with_suffix(".png")
    json_path = output_prefix.with_suffix(".json")
    render(spec_db, freq_axis_hz, times_s, psd_db, png_path, args.title, markers)

    summary = {
        "input": str(input_path),
        "output_png": str(png_path),
        "center_freq_hz": args.center_freq_hz,
        "center_freq_mhz": args.center_freq_hz / 1e6,
        "sample_rate": args.sample_rate,
        "fft_size": args.fft_size,
        "hop_size": args.hop_size,
        "duration_seconds": len(samples) / float(args.sample_rate),
        "markers": marker_band_summary(freq_axis_hz, psd_db, markers, args.marker_bandwidth_hz),
        "top_peaks": top_peaks(freq_axis_hz, psd_db),
    }
    json_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
