#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

from _rf_event_log import EventLogger
from _rf_monitor_common import ensure_dongle_mode, repo_local_binary, repo_local_runtime_env


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture a NOAA APT pass with rtl_fm and decode it into PNG with aptdec")
    parser.add_argument("--rtl-fm", default=repo_local_binary(__file__, "rtl_fm", "rtl_fm"))
    parser.add_argument("--aptdec", default=repo_local_binary(__file__, "aptdec", "aptdec"))
    parser.add_argument("--frequency", type=float, default=137.1, help="NOAA APT downlink frequency in MHz")
    parser.add_argument("--seconds", type=int, default=900, help="Capture duration in seconds")
    parser.add_argument("--sample-rate", type=int, default=60_000)
    parser.add_argument("--gain", default="0")
    parser.add_argument("--ppm", type=int, default=0)
    parser.add_argument("--device-index", default="0")
    parser.add_argument("--satellite", type=int, default=19, help="NOAA satellite number for aptdec, usually 15/18/19")
    parser.add_argument("--output-prefix", help="Output path prefix; .wav and .png are added automatically")
    parser.add_argument("--log-file", help="Optional JSONL log path for capture/decode metadata")
    return parser.parse_args()


def validate_binaries(args: argparse.Namespace) -> None:
    if shutil.which(args.rtl_fm) is None:
        raise RuntimeError("rtl_fm is not installed. Run ./setup/rtl2838.sh bootstrap first")
    if shutil.which(args.aptdec) is None:
        raise RuntimeError("aptdec is not installed. Run ./setup/rtl2838.sh bootstrap first")
    if shutil.which("sox") is None:
        raise RuntimeError("sox is not installed. Run ./setup/rtl2838.sh bootstrap first")


def capture_command(args: argparse.Namespace, wav_path: Path) -> list[str]:
    pipeline = (
        f"\"{args.rtl_fm}\" -M fm -f \"{args.frequency}M\" -s \"{args.sample_rate}\" -r \"{args.sample_rate}\" "
        f"-g \"{args.gain}\" -p \"{args.ppm}\" -d \"{args.device_index}\" -E deemp -F 9 - "
        f"| sox -t raw -r \"{args.sample_rate}\" -e signed -b 16 -c 1 - -t wav \"{wav_path}\""
    )
    return ["timeout", str(args.seconds), "bash", "-lc", pipeline]


def decode_command(args: argparse.Namespace, wav_path: Path, output_dir: Path) -> list[str]:
    return [
        args.aptdec,
        "-s",
        str(args.satellite),
        "-d",
        str(output_dir),
        str(wav_path),
    ]


def main() -> int:
    args = parse_args()
    logger = EventLogger("noaa-apt", args.log_file)
    try:
        validate_binaries(args)
        ensure_dongle_mode(__file__, "libusb")
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        base = Path(args.output_prefix) if args.output_prefix else Path("rtl2838/captures") / f"noaa-apt-{timestamp}"
        base.parent.mkdir(parents=True, exist_ok=True)
        wav_path = base.with_suffix(".wav")
        output_dir = base.parent
        env = repo_local_runtime_env(__file__, binary_path=args.aptdec)

        print(f"Capturing NOAA APT audio to {wav_path} for {args.seconds}s at {args.frequency:.4f} MHz")
        capture = subprocess.run(capture_command(args, wav_path), env=env)
        if capture.returncode not in (0, 124):
            raise RuntimeError(f"capture pipeline exited with status {capture.returncode}")
        if not wav_path.exists() or wav_path.stat().st_size == 0:
            raise RuntimeError(f"no WAV data written to {wav_path}")

        print(f"Decoding {wav_path} with aptdec")
        subprocess.run(decode_command(args, wav_path, output_dir), env=env, check=True)
        png_candidates = sorted(output_dir.glob(f"{wav_path.stem}*.png"))
        logger.log(
            timestamp=time.time(),
            kind="capture",
            data={
                "frequency_mhz": args.frequency,
                "seconds": args.seconds,
                "satellite": args.satellite,
                "wav": str(wav_path),
                "png": [str(path) for path in png_candidates],
            },
        )
        print(f"Wrote WAV: {wav_path}")
        for png in png_candidates:
            print(f"Wrote PNG: {png}")
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"rf_noaa_apt_capture failed: {exc}", file=sys.stderr)
        return 1
    finally:
        logger.close()


if __name__ == "__main__":
    raise SystemExit(main())
