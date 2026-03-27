#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="${ROOT_DIR}/.venv/bin/python"
PYTHON_BIN="${VENV_PYTHON}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  else
    echo "python3 is required" >&2
    exit 1
  fi
fi

resolve_contact_args() {
  "${PYTHON_BIN}" - "${ROOT_DIR}" <<'PY'
import sys

repo_root = sys.argv[1]
sys.path.insert(0, repo_root)

from tools._meshtastic_common import resolve_meshtastic_target

target = resolve_meshtastic_target()
if target.mode == "tcp":
    print("--host")
    print(target.label)
else:
    print("--port")
    print(target.serial_port)
PY
}

has_connection_arg=0
for arg in "$@"; do
  case "${arg}" in
    --port|--serial|-s|--host|--tcp|-t|--ble|-b)
      has_connection_arg=1
      break
      ;;
  esac
done

args=("$@")
if [[ ${has_connection_arg} -eq 0 ]]; then
  mapfile -t auto_args < <(resolve_contact_args)
  args=("${auto_args[@]}" "${args[@]}")
fi

export PYTHONPATH="${ROOT_DIR}/console:${ROOT_DIR}${PYTHONPATH:+:${PYTHONPATH}}"

exec "${PYTHON_BIN}" -m contact "${args[@]}"
