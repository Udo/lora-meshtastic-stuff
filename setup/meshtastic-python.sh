#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
PORT="${MESHTASTIC_PORT:-/dev/ttyUSB0}"
BAUD="${MESHTASTIC_BAUD:-115200}"
LOG_SECONDS="${MESHTASTIC_LOG_SECONDS:-20}"

usage() {
  cat <<'EOF'
Usage: setup/meshtastic-python.sh <command> [args]

Commands:
  bootstrap       Create .venv and install/upgrade Meshtastic CLI
  probe           Probe the node with --info on the configured serial port
  nodes           Show the node table from the connected device
  export-config   Export the node config as YAML to stdout
  doctor          Print serial diagnostics and attempt a verbose Meshtastic probe
  rawlog          Capture raw UART output without the Meshtastic protocol handshake
  shell           Print the command needed to activate the venv
  help            Show this help

Environment:
  MESHTASTIC_PORT Override the serial device path (default: /dev/ttyUSB0)
  MESHTASTIC_BAUD Override the UART baud rate for rawlog (default: 115200)
  MESHTASTIC_LOG_SECONDS Override rawlog capture duration in seconds (default: 20)
EOF
}

ensure_python() {
  if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required" >&2
    exit 1
  fi
}

ensure_venv() {
  ensure_python
  if [[ ! -d "${VENV_DIR}" ]]; then
    python3 -m venv "${VENV_DIR}"
  fi
}

run_in_venv() {
  ensure_venv
  # shellcheck disable=SC1091
  source "${VENV_DIR}/bin/activate"
  "$@"
}

bootstrap() {
  ensure_venv
  run_in_venv python -m pip install --upgrade pip
  run_in_venv python -m pip install --upgrade meshtastic
}

check_port() {
  if [[ ! -e "${PORT}" ]]; then
    echo "Serial port not found: ${PORT}" >&2
    exit 1
  fi
}

resolve_port() {
  if [[ -L "${PORT}" ]]; then
    readlink -f "${PORT}"
  else
    printf '%s\n' "${PORT}"
  fi
}

probe() {
  check_port
  run_in_venv meshtastic --port "${PORT}" --info
}

nodes() {
  check_port
  run_in_venv meshtastic --port "${PORT}" --nodes
}

export_config() {
  check_port
  run_in_venv meshtastic --port "${PORT}" --export-config
}

doctor() {
  check_port
  echo "MESHTASTIC_PORT=${PORT}"
  echo "Resolved port: $(resolve_port)"
  ls -l "${PORT}"
  echo
  echo "Serial aliases:"
  ls -l /dev/serial/by-id 2>/dev/null || true
  echo
  echo "Processes using ${PORT}:"
  fuser "${PORT}" || true
  echo
  echo "Meshtastic verbose probe:"
  if ! run_in_venv meshtastic --port "${PORT}" --debug --timeout 20 --info; then
    cat <<'EOF'

Probe failed. Common causes:
  - the device is not running Meshtastic firmware
  - the board is in bootloader mode or hung and needs a reset
  - the wrong serial port was selected
EOF
    return 1
  fi
}

rawlog() {
  check_port
  run_in_venv python - "${PORT}" "${BAUD}" "${LOG_SECONDS}" <<'PY'
import sys
import time

import serial

port = sys.argv[1]
baud = int(sys.argv[2])
duration = float(sys.argv[3])

ser = serial.Serial(port=None, baudrate=baud, timeout=0.2, dsrdtr=False, rtscts=False)
ser.dtr = False
ser.rts = False
ser.port = port
ser.open()

start = time.time()
while time.time() - start < duration:
    data = ser.read(512)
    if data:
        sys.stdout.buffer.write(data)
        sys.stdout.flush()

ser.close()
PY
}

shell_cmd() {
  echo "source ${VENV_DIR}/bin/activate"
}

COMMAND="${1:-help}"

case "${COMMAND}" in
  bootstrap)
    bootstrap
    ;;
  probe)
    probe
    ;;
  nodes)
    nodes
    ;;
  export-config)
    export_config
    ;;
  doctor)
    doctor
    ;;
  rawlog)
    rawlog
    ;;
  shell)
    shell_cmd
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    echo "Unknown command: ${COMMAND}" >&2
    usage >&2
    exit 1
    ;;
esac