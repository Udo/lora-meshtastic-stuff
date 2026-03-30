#!/usr/bin/env python3
from __future__ import annotations

import curses
import os
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def repo_root(script_path: str) -> Path:
    return Path(script_path).resolve().parents[1]


def repo_local_binary(script_path: str, name: str, fallback: str) -> str:
    local_binary = repo_root(script_path) / "rtl2838" / "local" / "bin" / name
    if local_binary.exists():
        return str(local_binary)
    return fallback


def setup_script_path(script_path: str) -> Path:
    return repo_root(script_path) / "setup" / "rtl2838.sh"


def repo_local_runtime_env(script_path: str, *, binary_path: str | None = None) -> dict[str, str]:
    root = repo_root(script_path)
    local_root = root / "rtl2838" / "local"
    env = dict(os.environ)

    lib_paths = [str(local_root / "lib"), str(local_root / "lib64")]
    if binary_path and "/" in binary_path:
        binary_lib = str(Path(binary_path).resolve().parents[1] / "lib")
        lib_paths.insert(0, binary_lib)

    existing_ld = env.get("LD_LIBRARY_PATH", "")
    env["LD_LIBRARY_PATH"] = ":".join(path for path in [*lib_paths, existing_ld] if path)

    py_paths = [
        str(local_root / "lib" / "python3" / "dist-packages"),
        str(local_root / "lib" / "python3" / "site-packages"),
        str(local_root / "lib64" / "python3" / "dist-packages"),
        str(local_root / "lib64" / "python3" / "site-packages"),
    ]
    existing_py = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = ":".join(path for path in [*py_paths, existing_py] if path)
    return env


def rtl2838_usb_present() -> bool:
    return rtl2838_usb_sysfs_device() is not None


def rtl2838_usb_sysfs_device() -> str | None:
    usb_root = Path("/sys/bus/usb/devices")
    if not usb_root.exists():
        return None
    for dev in usb_root.glob("*"):
        try:
            vendor = (dev / "idVendor").read_text().strip()
            product = (dev / "idProduct").read_text().strip()
        except Exception:
            continue
        if f"{vendor}:{product}" == "0bda:2838":
            return str(dev)
    return None


def rtl2838_kernel_bound_interface() -> bool:
    dev = rtl2838_usb_sysfs_device()
    if not dev:
        return False
    if0 = Path(f"{dev}:1.0")
    if not if0.exists():
        return False
    try:
        driver = os.path.realpath(if0 / "driver")
    except Exception:
        return False
    return driver.endswith("/dvb_usb_rtl28xxu")


def kernel_device_available(device: str) -> bool:
    return os.path.exists(device)


def switch_dongle_mode(script_path: str, mode: str) -> None:
    setup_script = setup_script_path(script_path)
    if not setup_script.exists():
        raise RuntimeError(f"missing setup helper: {setup_script}")
    command = [str(setup_script), "use-kernel" if mode == "kernel" else "use-libusb"]
    print(
        f"Kernel/libusb mode switch required, running: {' '.join(command)}",
        file=sys.stderr,
    )
    result = subprocess.run(command)
    if result.returncode != 0:
        raise RuntimeError(f"failed to switch dongle to {mode} mode")


def ensure_dongle_mode(script_path: str, mode: str, kernel_device: str = "/dev/swradio0") -> str:
    if mode == "kernel":
        if not kernel_device_available(kernel_device):
            switch_dongle_mode(script_path, "kernel")
        return "kernel"

    if mode == "libusb":
        if kernel_device_available(kernel_device):
            switch_dongle_mode(script_path, "libusb")
        elif not rtl2838_usb_present():
            raise RuntimeError("RTL2838 USB dongle not detected")
        return "libusb"

    if mode == "auto":
        if kernel_device_available(kernel_device):
            return "kernel"
        if rtl2838_usb_present():
            return "libusb"
        raise RuntimeError("RTL2838 USB dongle not detected")

    raise ValueError(f"unsupported mode: {mode}")


def draw_text(stdscr: curses.window, y: int, x: int, text: str, attr: int = 0) -> None:
    height, width = stdscr.getmaxyx()
    if y < 0 or y >= height or x >= width:
        return
    clipped = text[: max(0, width - x - 1)]
    if not clipped:
        return
    try:
        stdscr.addstr(y, x, clipped, attr)
    except curses.error:
        pass


def configure_curses(stdscr: curses.window, fps: float) -> None:
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    stdscr.nodelay(True)
    stdscr.timeout(max(1, int(1000 / max(1.0, fps))))


def monitor_status_line(
    *,
    now: float,
    started: float,
    last_activity: float,
    total_events: int,
    idle_label: str,
    receiving_label: str,
    detail: str,
    width: int,
) -> str:
    if total_events > 0:
        status_text = f"status={receiving_label}  last_data={now - last_activity:.1f}s ago"
    elif last_activity > 0.0:
        status_text = f"status={idle_label}  last_msg={now - last_activity:.1f}s ago"
    else:
        status_text = f"status=waiting for first decoder output  startup_age={now - started:.1f}s"
    detail_width = max(0, width - len(status_text) - 10)
    return f"{status_text}  detail={detail[:detail_width]}"


class NonBlockingProcess:
    def __init__(
        self,
        command: list[str],
        *,
        text: bool,
        env: dict[str, str] | None = None,
        bufsize: int = 0,
    ):
        self.command = command
        self.text = text
        self.env = env
        self.bufsize = bufsize
        self.proc: subprocess.Popen[Any] | None = None
        self.selector: selectors.BaseSelector | None = None

    def __enter__(self) -> "NonBlockingProcess":
        self.proc = subprocess.Popen(
            self.command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=self.text,
            bufsize=self.bufsize,
            env=self.env,
            preexec_fn=os.setsid,
        )
        self.selector = selectors.DefaultSelector()
        if self.proc.stdout is not None:
            os.set_blocking(self.proc.stdout.fileno(), False)
            self.selector.register(self.proc.stdout, selectors.EVENT_READ, "stdout")
        if self.proc.stderr is not None:
            os.set_blocking(self.proc.stderr.fileno(), False)
            self.selector.register(self.proc.stderr, selectors.EVENT_READ, "stderr")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        if self.selector is not None:
            self.selector.close()
            self.selector = None
        if self.proc is None:
            return
        try:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
        except Exception:
            pass
        try:
            self.proc.wait(timeout=1.0)
        except Exception:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            except Exception:
                pass

    def assert_running(self, default_message: str) -> None:
        assert self.proc is not None
        if self.proc.poll() is None:
            return
        stderr_tail = self.read_stderr_tail()
        raise RuntimeError(stderr_tail or default_message)

    def read_stderr_tail(self) -> str:
        assert self.proc is not None
        if self.proc.stderr is None:
            return ""
        try:
            data = self.proc.stderr.read()
        except Exception:
            return ""
        if not data:
            return ""
        if isinstance(data, bytes):
            return data.decode("utf-8", "replace").strip()
        return data.strip()

    def read_available(self, stdout_bytes: int | None = None) -> tuple[str | bytes, list[str]]:
        assert self.selector is not None
        stdout_data: str | bytes = "" if self.text else b""
        stderr_lines: list[str] = []
        for key, _ in self.selector.select(timeout=0):
            stream = key.fileobj
            kind = key.data
            try:
                if kind == "stdout" and stdout_bytes is not None:
                    chunk = stream.read(stdout_bytes)
                else:
                    chunk = stream.read()
            except BlockingIOError:
                chunk = "" if self.text or kind == "stderr" else b""
            if not chunk:
                continue
            if kind == "stderr":
                text = chunk.decode("utf-8", "replace") if isinstance(chunk, bytes) else chunk
                stderr_lines.extend(line for line in text.strip().splitlines() if line)
                continue
            stdout_data += chunk
        return stdout_data, stderr_lines


def current_time() -> float:
    return time.monotonic()


def _main() -> int:
    if len(sys.argv) < 2:
      print("usage: _rf_monitor_common.py <usb-sysfs-device|usb-present|kernel-bound-interface>")
      return 2

    command = sys.argv[1]
    if command == "usb-sysfs-device":
        dev = rtl2838_usb_sysfs_device()
        if dev:
            print(dev)
            return 0
        return 1
    if command == "usb-present":
        return 0 if rtl2838_usb_present() else 1
    if command == "kernel-bound-interface":
        return 0 if rtl2838_kernel_bound_interface() else 1

    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main())
