#!/usr/bin/env python3
from __future__ import annotations

import argparse
import socket
import struct
import subprocess
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

from _rf_event_log import EventLogger
from _rf_monitor_common import ensure_dongle_mode, repo_local_binary, repo_root


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture a LoRa channel and decode raw annotated hex dumps with gr-lora")
    parser.add_argument("--rtl-sdr", default=repo_local_binary(__file__, "rtl_sdr", "rtl_sdr"))
    parser.add_argument("--seconds", type=int, default=10)
    parser.add_argument("--center", type=int, default=868_300_000, help="Capture center frequency in Hz")
    parser.add_argument("--frequency", type=int, default=868_300_000, help="Target LoRa channel frequency in Hz")
    parser.add_argument("--sample-rate", type=int, default=1_000_000)
    parser.add_argument("--gain", default="0")
    parser.add_argument("--ppm", type=int, default=0)
    parser.add_argument("--device-index", default="0")
    parser.add_argument("--sf", type=int, default=7)
    parser.add_argument("--cr", default="4/8", help="Coding rate like 4/5, 4/6, 4/7, or 4/8")
    parser.add_argument("--bandwidth", type=int, default=125_000)
    parser.add_argument("--implicit", action="store_true")
    parser.add_argument("--no-crc", dest="crc", action="store_false")
    parser.add_argument("--output-prefix", help="Optional capture prefix; .cu8 and .cfile are written")
    parser.add_argument("--log-file", help="Optional JSONL log path for decoded LoRa frames")
    parser.set_defaults(crc=True)
    return parser.parse_args()


def ensure_gr_lora_importable() -> None:
    root = repo_root(__file__)
    candidates = [
        root / "rtl2838" / "local" / "lib" / "python3" / "dist-packages",
        root / "rtl2838" / "local" / "lib" / "python3" / "site-packages",
        root / "rtl2838" / "local" / "lib64" / "python3" / "dist-packages",
        root / "rtl2838" / "local" / "lib64" / "python3" / "site-packages",
    ]
    for path in candidates:
        if path.exists():
            sys.path.insert(0, str(path))


def validate_runtime(args: argparse.Namespace) -> None:
    if not Path(args.rtl_sdr).exists() and not shutil.which(args.rtl_sdr):
        raise RuntimeError("rtl_sdr is not installed. Run ./setup/rtl2838.sh bootstrap first")
    ensure_gr_lora_importable()
    try:
        import lora  # noqa: F401
        import osmosdr  # noqa: F401
        from gnuradio import blocks, gr  # noqa: F401
        from lora.lora_receiver import lora_receiver  # noqa: F401
    except Exception as exc:
        raise RuntimeError(f"gr-lora runtime not available. Run ./setup/rtl2838.sh bootstrap first ({exc})") from exc


def capture_iq(args: argparse.Namespace, raw_path: Path) -> None:
    sample_count = args.sample_rate * args.seconds
    cmd = [
        args.rtl_sdr,
        "-d",
        str(args.device_index),
        "-f",
        str(args.center),
        "-s",
        str(args.sample_rate),
        "-g",
        str(args.gain),
        "-p",
        str(args.ppm),
        "-n",
        str(sample_count),
        str(raw_path),
    ]
    result = subprocess.run(cmd)
    if result.returncode != 0 or not raw_path.exists() or raw_path.stat().st_size == 0:
        raise RuntimeError(f"rtl_sdr capture failed for {raw_path}")


def convert_cu8_to_cfile(raw_path: Path, cfile_path: Path) -> int:
    data = np.fromfile(raw_path, dtype=np.uint8)
    if data.size < 2:
        raise RuntimeError(f"capture is empty: {raw_path}")
    if data.size % 2:
        data = data[:-1]
    iq = data.astype(np.float32).reshape(-1, 2)
    complex_iq = ((iq[:, 0] - 127.5) / 128.0 + 1j * ((iq[:, 1] - 127.5) / 128.0)).astype(np.complex64)
    complex_iq.tofile(cfile_path)
    return complex_iq.size


def parse_cr_number(cr_text: str) -> int:
    valid = {"4/5": 1, "4/6": 2, "4/7": 3, "4/8": 4}
    if cr_text not in valid:
        raise RuntimeError(f"unsupported coding rate: {cr_text}")
    return valid[cr_text]


def snr_db(raw_value: int) -> float:
    signed = raw_value if raw_value < 128 else raw_value - 256
    return signed / 4.0


def packet_rssi_dbm(raw_value: int, snr_value: int) -> float:
    if snr_db(snr_value) >= 0:
        return -139.0 + raw_value
    return -139.0 + (raw_value * 0.25)


def printable_ascii(payload: bytes) -> str:
    text = "".join(chr(b) if 32 <= b < 127 else "." for b in payload)
    return text.strip(".")


def decode_file(args: argparse.Namespace, cfile_path: Path, logger: EventLogger) -> int:
    ensure_gr_lora_importable()
    from gnuradio import blocks, gr
    import lora
    from lora.lora_receiver import lora_receiver

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(0.5)
    port = sock.getsockname()[1]

    class TopBlock(gr.top_block):
        def __init__(self) -> None:
            super().__init__("LoRa File Decode")
            self.source = blocks.file_source(gr.sizeof_gr_complex, str(cfile_path), False)
            self.throttle = blocks.throttle(gr.sizeof_gr_complex, args.sample_rate, True)
            self.receiver = lora_receiver(
                args.sample_rate,
                args.center,
                [args.frequency],
                args.bandwidth,
                args.sf,
                args.implicit,
                parse_cr_number(args.cr),
                args.crc,
                reduced_rate=False,
                decimation=1,
            )
            self.sink = lora.message_socket_sink("127.0.0.1", port, 0)
            self.connect((self.source, 0), (self.throttle, 0))
            self.connect((self.throttle, 0), (self.receiver, 0))
            self.msg_connect((self.receiver, "frames"), (self.sink, "in"))

    tb = TopBlock()
    worker = threading.Thread(target=tb.run, daemon=True)
    worker.start()
    decoded = 0
    idle_loops = 0
    try:
        while worker.is_alive() or idle_loops < 3:
            try:
                packet, _addr = sock.recvfrom(4096)
            except socket.timeout:
                idle_loops += 1
                continue
            idle_loops = 0
            if len(packet) < 14:
                continue
            version, _pad, _length, freq, bandwidth_step, sf, packet_rssi, _max_rssi, _current_rssi, snr_raw, sync = struct.unpack(
                ">BBHIBBBBBBB", packet[:14]
            )
            payload = packet[14:]
            record = {
                "version": version,
                "frequency_hz": freq,
                "bandwidth_khz": bandwidth_step * 125,
                "sf": sf,
                "sync_word": f"0x{sync:02x}",
                "packet_rssi_dbm": round(packet_rssi_dbm(packet_rssi, snr_raw), 2),
                "snr_db": round(snr_db(snr_raw), 2),
                "hex": payload.hex(),
                "ascii": printable_ascii(payload),
            }
            logger.log(timestamp=time.time(), kind="frame", data=record)
            print(
                f"freq={record['frequency_hz'] / 1e6:.3f}MHz sf={record['sf']} bw={record['bandwidth_khz']}kHz "
                f"sync={record['sync_word']} rssi={record['packet_rssi_dbm']:.1f}dBm snr={record['snr_db']:.1f}dB "
                f"hex={record['hex']} ascii={record['ascii']}"
            )
            decoded += 1
        worker.join(timeout=1.0)
        return decoded
    finally:
        sock.close()


def main() -> int:
    args = parse_args()
    logger = EventLogger("lora", args.log_file)
    try:
        if shutil.which(args.rtl_sdr) is None and not Path(args.rtl_sdr).exists():
            raise RuntimeError("rtl_sdr is not installed. Run ./setup/rtl2838.sh bootstrap first")
        validate_runtime(args)
        ensure_dongle_mode(__file__, "libusb")
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        base = Path(args.output_prefix) if args.output_prefix else Path("rtl2838/captures") / f"lora-{timestamp}"
        base.parent.mkdir(parents=True, exist_ok=True)
        raw_path = base.with_suffix(".cu8")
        cfile_path = base.with_suffix(".cfile")
        print(f"Capturing {args.seconds}s around {args.center / 1e6:.3f} MHz to {raw_path}")
        capture_iq(args, raw_path)
        sample_count = convert_cu8_to_cfile(raw_path, cfile_path)
        print(f"Converted {sample_count} complex samples to {cfile_path}")
        decoded = decode_file(args, cfile_path, logger)
        print(f"Decoded {decoded} LoRa frame(s)")
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"rf_lora_monitor failed: {exc}", file=sys.stderr)
        return 1
    finally:
        logger.close()


if __name__ == "__main__":
    raise SystemExit(main())
