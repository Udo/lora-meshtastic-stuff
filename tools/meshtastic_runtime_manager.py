#!/usr/bin/env python3
import argparse
import logging
import os
import signal
import subprocess
import sys
import time
import json
from pathlib import Path

from _meshtastic_common import DEFAULT_SERIAL_PORT, DEFAULT_TCP_HOST, DEFAULT_TCP_PORT, ensure_repo_python, normalize_tcp_client_host


ensure_repo_python("MESHTASTIC_RUNTIME_MANAGER_VENV_EXEC")

LOGGER = logging.getLogger("meshtastic_runtime_manager")
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROXY_TOOL = REPO_ROOT / "tools" / "meshtastic_proxy.py"
DEFAULT_PROTOCOL_TOOL = REPO_ROOT / "tools" / "meshtastic_protocol.py"
DEFAULT_STATUS_FILE = REPO_ROOT / ".runtime" / "meshtastic" / "runtime-manager-status.json"
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Meshtastic runtime manager")
    parser.add_argument("--serial-port", default=DEFAULT_SERIAL_PORT, help="Serial port owned by the proxy")
    parser.add_argument("--baud", type=int, default=115200, help="Serial baud rate")
    parser.add_argument("--listen-host", default=DEFAULT_TCP_HOST, help="TCP host the proxy binds to")
    parser.add_argument("--connect-host", default=DEFAULT_TCP_HOST, help="TCP host local tools should use to reach the proxy")
    parser.add_argument("--listen-port", type=int, default=DEFAULT_TCP_PORT, help="TCP port for proxy and protocol clients")
    parser.add_argument("--status-file", help="Proxy status JSON path")
    parser.add_argument("--manager-status-file", default=str(DEFAULT_STATUS_FILE), help="Runtime manager status JSON path")
    parser.add_argument("--config-file", help="Config file path loaded by the caller")
    parser.add_argument("--protocol-log-name", default="protocol", help="Protocol log name")
    parser.add_argument(
        "--protocol-sidecar-mode",
        choices=("auto", "on", "off"),
        default="auto",
        help="When to attach the protocol logger as a second TCP client: auto currently stays off unless explicitly enabled",
    )
    parser.add_argument("--protocol-connect-wait-seconds", type=float, default=20.0, help="Seconds the protocol logger waits for TCP readiness")
    parser.add_argument("--proxy-tool", default=str(DEFAULT_PROXY_TOOL), help=argparse.SUPPRESS)
    parser.add_argument("--protocol-tool", default=str(DEFAULT_PROTOCOL_TOOL), help=argparse.SUPPRESS)
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser


class RuntimeManager:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.stop_requested = False
        self.proxy_process: subprocess.Popen[str] | None = None
        self.protocol_process: subprocess.Popen[str] | None = None
        self.proxy_restart_count = 0
        self.protocol_restart_count = 0

    def write_status(self) -> None:
        status_path = Path(self.args.manager_status_file)
        status_path.parent.mkdir(parents=True, exist_ok=True)

        def process_state(process: subprocess.Popen[str] | None, restart_count: int) -> dict[str, object]:
            if process is None:
                return {"running": False, "pid": None, "returncode": None, "restart_count": restart_count}
            returncode = process.poll()
            return {
                "running": returncode is None,
                "pid": process.pid,
                "returncode": returncode,
                "restart_count": restart_count,
            }

        payload = {
            "manager_pid": os.getpid(),
            "listen_host": self.args.listen_host,
            "connect_host": normalize_tcp_client_host(self.args.connect_host),
            "listen_port": self.args.listen_port,
            "protocol_sidecar_mode": self.args.protocol_sidecar_mode,
            "protocol_sidecar_enabled": self.should_start_protocol_sidecar(),
            "proxy": process_state(self.proxy_process, self.proxy_restart_count),
            "protocol": process_state(self.protocol_process, self.protocol_restart_count),
        }
        temp_path = status_path.with_suffix(status_path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temp_path.replace(status_path)

    def request_stop(self, _signum=None, _frame=None) -> None:
        self.stop_requested = True
        self.write_status()

    def should_start_protocol_sidecar(self) -> bool:
        mode = self.args.protocol_sidecar_mode
        return mode == "on"

    def _spawn(self, name: str, command: list[str]) -> subprocess.Popen[str]:
        LOGGER.info("starting %s: %s", name, " ".join(command))
        process = subprocess.Popen(command)
        self.write_status()
        return process

    def start_proxy(self) -> subprocess.Popen[str]:
        command = [
            sys.executable,
            self.args.proxy_tool,
            "--serial-port",
            self.args.serial_port,
            "--baud",
            str(self.args.baud),
            "--listen-host",
            self.args.listen_host,
            "--listen-port",
            str(self.args.listen_port),
        ]
        if self.args.status_file:
            command.extend(["--status-file", self.args.status_file])
        if self.args.config_file:
            command.extend(["--config-file", self.args.config_file])
        if self.args.verbose:
            command.append("--verbose")
        self.proxy_process = self._spawn("proxy", command)
        return self.proxy_process

    def start_protocol(self) -> subprocess.Popen[str]:
        connect_host = normalize_tcp_client_host(self.args.connect_host)
        command = [
            sys.executable,
            self.args.protocol_tool,
            "--host",
            connect_host,
            "--tcp-port",
            str(self.args.listen_port),
            "--connect-wait-seconds",
            str(self.args.protocol_connect_wait_seconds),
            self.args.protocol_log_name,
            "--quiet",
        ]
        if self.args.verbose:
            command.append("--include-log-lines")
        self.protocol_process = self._spawn("protocol", command)
        return self.protocol_process

    def stop_process(self, name: str, process: subprocess.Popen[str] | None, timeout: float = 5.0) -> None:
        if process is None or process.poll() is not None:
            return
        LOGGER.info("stopping %s", name)
        process.terminate()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return
            time.sleep(0.1)
        LOGGER.warning("forcing %s to stop", name)
        process.kill()
        process.wait(timeout=2.0)
        self.write_status()

    def run(self) -> int:
        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGTERM, self.request_stop)

        self.start_proxy()
        if self.should_start_protocol_sidecar():
            self.start_protocol()
        else:
            LOGGER.info(
                "protocol sidecar disabled for listen host %s (mode=%s)",
                self.args.listen_host,
                self.args.protocol_sidecar_mode,
            )
        self.write_status()

        exit_code = 0
        try:
            while not self.stop_requested:
                proxy_process = self.proxy_process
                protocol_process = self.protocol_process
                if proxy_process is not None:
                    proxy_code = proxy_process.poll()
                    if proxy_code is not None:
                        LOGGER.error("proxy exited with status %s", proxy_code)
                        self.write_status()
                        exit_code = proxy_code or 1
                        break
                if protocol_process is not None:
                    protocol_code = protocol_process.poll()
                    if protocol_code is not None:
                        LOGGER.warning("protocol exited with status %s; restarting", protocol_code)
                        self.write_status()
                        if self.stop_requested:
                            break
                        time.sleep(1.0)
                        self.protocol_restart_count += 1
                        self.start_protocol()
                time.sleep(0.25)
        finally:
            self.stop_process("protocol", self.protocol_process)
            self.stop_process("proxy", self.proxy_process)
            self.write_status()
        return exit_code


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    return RuntimeManager(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
