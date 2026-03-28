#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT_DIR}/setup/lib/meshtastic-os.sh"
RUNTIME_DIR="${ROOT_DIR}/.runtime/meshtastic"
SERVICE_CONFIG_FILE="${RUNTIME_DIR}/service.env"
VENV_DIR="${ROOT_DIR}/.venv"
VENV_PYTHON="$(meshtastic_venv_python_path "${VENV_DIR}")"
VENV_ACTIVATE="$(meshtastic_venv_activate_path "${VENV_DIR}")"
DEFAULT_SERIAL_PORT="$(meshtastic_default_serial_port)"
if [[ -f "${SERVICE_CONFIG_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${SERVICE_CONFIG_FILE}"
fi
PORT="${MESHTASTIC_PORT:-${DEFAULT_SERIAL_PORT}}"
BAUD="${MESHTASTIC_BAUD:-115200}"
HOST="${MESHTASTIC_HOST:-}"
TCP_PORT="${MESHTASTIC_TCP_PORT:-4403}"
PROXY_BIND_HOST="${MESHTASTIC_PROXY_BIND_HOST:-127.0.0.1}"
PROXY_CONNECT_HOST="${MESHTASTIC_PROXY_HOST:-127.0.0.1}"
LOG_SECONDS="${MESHTASTIC_LOG_SECONDS:-20}"
DEFAULT_REGION="${MESHTASTIC_REGION:-EU_868}"
WIFI_SSID="${MESHTASTIC_WIFI_SSID:-}"
WIFI_PSK="${MESHTASTIC_WIFI_PSK:-}"
OWNER_LONG="${MESHTASTIC_OWNER_LONG:-}"
OWNER_SHORT="${MESHTASTIC_OWNER_SHORT:-}"
FIRMWARE_DIR="${ROOT_DIR}/docs/firmware/firmware-esp32s3-2.7.13.597fa0b"
FIRMWARE_BIN="firmware-heltec-v3-2.7.13.597fa0b.bin"
FIRMWARE_INSTALLER="${FIRMWARE_DIR}/device-install.sh"
STATUS_TOOL="${ROOT_DIR}/tools/meshtastic_status.py"
MONITOR_TOOL="${ROOT_DIR}/tools/meshtastic_monitor.py"
MESSAGES_TOOL="${ROOT_DIR}/tools/meshtastic_messages.py"
PROTOCOL_TOOL="${ROOT_DIR}/tools/meshtastic_protocol.py"
PLUGINS_TOOL="${ROOT_DIR}/tools/meshtastic_plugins.py"
PROXY_TOOL="${ROOT_DIR}/tools/meshtastic_proxy.py"
CONSOLE_TOOL="${ROOT_DIR}/console/contact.sh"
PORT_WAIT_SECONDS="${MESHTASTIC_PORT_WAIT_SECONDS:-30}"
PROTOCOL_LOG_NAME="${MESHTASTIC_PROTOCOL_LOG_NAME:-protocol}"
PROXY_PID_FILE="${RUNTIME_DIR}/proxy.pid"
PROXY_LOG_FILE="${RUNTIME_DIR}/proxy.log"
PROXY_STATUS_FILE="${RUNTIME_DIR}/proxy-status.json"
PROTOCOL_PID_FILE="${RUNTIME_DIR}/protocol.pid"
PROTOCOL_RUNNER_LOG_FILE="${RUNTIME_DIR}/protocol-runner.log"
PROXY_SYSTEMD_UNIT="meshtastic-proxy.service"
PROTOCOL_SYSTEMD_UNIT="meshtastic-protocol.service"
PROXY_SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
PROXY_SYSTEMD_USER_UNIT_FILE="${PROXY_SYSTEMD_USER_DIR}/${PROXY_SYSTEMD_UNIT}"
PROTOCOL_SYSTEMD_USER_UNIT_FILE="${PROXY_SYSTEMD_USER_DIR}/${PROTOCOL_SYSTEMD_UNIT}"
PROXY_SYSTEMD_SYSTEM_DIR="/etc/systemd/system"
PROXY_SYSTEMD_SYSTEM_UNIT_FILE="${PROXY_SYSTEMD_SYSTEM_DIR}/${PROXY_SYSTEMD_UNIT}"
PROTOCOL_SYSTEMD_SYSTEM_UNIT_FILE="${PROXY_SYSTEMD_SYSTEM_DIR}/${PROTOCOL_SYSTEMD_UNIT}"

if [[ -t 1 ]]; then
  COLOR_RESET=$'\033[0m'
  COLOR_BOLD=$'\033[1m'
  COLOR_DIM=$'\033[2m'
  COLOR_RED=$'\033[31m'
  COLOR_GREEN=$'\033[32m'
  COLOR_YELLOW=$'\033[33m'
  COLOR_BLUE=$'\033[34m'
  COLOR_MAGENTA=$'\033[35m'
  COLOR_CYAN=$'\033[36m'
else
  COLOR_RESET=''
  COLOR_BOLD=''
  COLOR_DIM=''
  COLOR_RED=''
  COLOR_GREEN=''
  COLOR_YELLOW=''
  COLOR_BLUE=''
  COLOR_MAGENTA=''
  COLOR_CYAN=''
fi

usage() {
  cat <<'EOF'
Usage: setup/meshtastic-python.sh <command> [args]

Commands:
  bootstrap       Create .venv and install/upgrade Meshtastic CLI
  flash           Flash the repo's known-good Meshtastic firmware to the device
  provision       Flash known-good firmware, apply region, and optionally WiFi
  guided          Interactive ANSI-colored setup flow with sensible defaults
  probe           Probe the node with --info on the configured serial port
  nodes           Show the node table from the connected device
  nodedb-reset    Clear the connected node's known-node database
  contacts        List contacts or manage NodeDB entries (list|keys|remove|favorite|unfavorite|ignore|unignore)
  export-config   Export the node config as YAML to stdout
  set-name        Set the node long and short names
  set-role        Set the Meshtastic device role (node type)
  set-region      Set the LoRa region on the connected node
  set-modem-preset Set the LoRa modem preset on the connected node
  set-position    Set a fixed node position as latitude/longitude[/altitude]
  clear-position  Remove the configured fixed node position
  set-ham         Set licensed ham ID and disable encryption
  set-wifi        Enable WiFi client mode and store SSID/PSK on the node
  status          Run the pretty Meshtastic status tool from tools/
  telemetry       Request telemetry from nearby nodes via the status tool
  monitor         Run the continuous Meshtastic event monitor from tools/
  messages        Send private messages, sync transcripts, or inspect logs under ~/.local/log/meshtastic/*.log
  protocol        Persist protocol-level mesh traffic and housekeeping events under ~/.local/log/meshtastic/*.log
  plugins         Run a plugin-defined utility, for example: plugins STORE_FORWARD_APP stats
  proxy-start     Start the local Meshtastic serial-to-TCP proxy in the background
  proxy-stop      Stop the local Meshtastic proxy
  proxy-status    Show local Meshtastic proxy status
  proxy-check     Check whether the local Meshtastic proxy TCP endpoint is healthy
  proxy-autostart-install Install and enable Linux systemd autostart for the proxy [--user|--system]
  proxy-autostart-remove Remove and disable Linux systemd autostart for the proxy [--user|--system]
  proxy-autostart-status Show Linux systemd autostart status for the proxy [--user|--system]
  proxy-log       Tail the local Meshtastic proxy log
  target-debug    Explain which Meshtastic target the repo tools would choose (--json or --brief)
  console         Run the vendored Contact Meshtastic console TUI
  doctor          Print serial diagnostics and attempt a verbose Meshtastic probe
  rawlog          Capture raw UART output without the Meshtastic protocol handshake
  shell           Print the command needed to activate the venv
  help            Show this help

Environment:
  MESHTASTIC_PORT Override the serial device path (default: OS-specific auto-detected port)
  MESHTASTIC_HOST Override Meshtastic access to use a TCP host instead of serial
  MESHTASTIC_TCP_PORT Override the TCP port used with MESHTASTIC_HOST or the local proxy (default: 4403)
  MESHTASTIC_PROXY_BIND_HOST Override the local proxy bind host (default: 127.0.0.1)
  MESHTASTIC_PROXY_HOST Override the host wrappers should use when the local proxy is running (default: 127.0.0.1)
  MESHTASTIC_PROTOCOL_LOG_NAME Override the protocol sidecar log basename (default: protocol)
  MESHTASTIC_BAUD Override the UART baud rate for rawlog (default: 115200)
  MESHTASTIC_LOG_SECONDS Override rawlog capture duration in seconds (default: 20)
  MESHTASTIC_REGION Override the region used by provision (default: EU_868)
  MESHTASTIC_OWNER_LONG Optional long node name applied by provision
  MESHTASTIC_OWNER_SHORT Optional short node name applied by provision
  MESHTASTIC_WIFI_SSID Optional WiFi SSID applied by provision
  MESHTASTIC_WIFI_PSK Optional WiFi PSK applied by provision
  MESHTASTIC_LOG_DIR Override the default Meshtastic transcript directory (default: ~/.local/log/meshtastic)
  MESHTASTIC_PORT_WAIT_SECONDS Seconds to wait for the serial port after flashing (default: 30)
EOF
}

print_banner() {
  printf '%s%sMeshtastic Setup%s\n' "${COLOR_BOLD}" "${COLOR_CYAN}" "${COLOR_RESET}"
  printf '%sKnown-good firmware:%s %s\n' "${COLOR_DIM}" "${COLOR_RESET}" "${FIRMWARE_BIN}"
  printf '%sPress Enter to accept defaults shown in brackets.%s\n\n' "${COLOR_DIM}" "${COLOR_RESET}"
}

print_info() {
  printf '%s%s%s\n' "${COLOR_BLUE}" "$*" "${COLOR_RESET}"
}

print_success() {
  printf '%s%s%s\n' "${COLOR_GREEN}" "$*" "${COLOR_RESET}"
}

print_warn() {
  printf '%s%s%s\n' "${COLOR_YELLOW}" "$*" "${COLOR_RESET}"
}

print_error() {
  printf '%s%s%s\n' "${COLOR_RED}" "$*" "${COLOR_RESET}" >&2
}

SYSTEM_PYTHON=''

find_system_python() {
  if command -v python3 >/dev/null 2>&1; then
    printf 'python3\n'
    return 0
  fi

  if command -v python >/dev/null 2>&1; then
    printf 'python\n'
    return 0
  fi

  return 1
}

ensure_python() {
  if [[ -n "${SYSTEM_PYTHON}" ]]; then
    return 0
  fi

  if ! SYSTEM_PYTHON="$(find_system_python)"; then
    echo "python3 or python is required" >&2
    exit 1
  fi
}

run_system_python() {
  ensure_python
  "${SYSTEM_PYTHON}" "$@"
}

python_venv_package_hint() {
  run_system_python - <<'PY'
import sys

major = sys.version_info.major
minor = sys.version_info.minor
print(f"python{major}.{minor}-venv")
PY
}

python_venv_fallback_package() {
  printf 'python3-venv\n'
}

python_venv_support_missing() {
  local error_log="${1}"
  grep -Eqi 'ensurepip is not available|No module named ensurepip' "${error_log}"
}

can_auto_install_python_venv_support() {
  command -v apt-get >/dev/null 2>&1 || return 1
  if [[ "${EUID}" -eq 0 ]]; then
    return 0
  fi
  command -v sudo >/dev/null 2>&1
}

install_python_venv_support() {
  local primary_package fallback_package

  primary_package="$(python_venv_package_hint)"
  fallback_package="$(python_venv_fallback_package)"

  print_info "Installing Python venv support (${primary_package} or ${fallback_package})..."
  run_with_sudo env DEBIAN_FRONTEND=noninteractive apt-get update
  if run_with_sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y "${primary_package}"; then
    return 0
  fi

  run_with_sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y "${fallback_package}"
}

create_venv() {
  local error_log package_hint

  error_log="$(mktemp)"
  if run_system_python -m venv "${VENV_DIR}" 2>"${error_log}"; then
    rm -f "${error_log}"
    return 0
  fi

  rm -rf "${VENV_DIR}"
  package_hint="$(python_venv_package_hint)"

  if python_venv_support_missing "${error_log}"; then
    if can_auto_install_python_venv_support; then
      print_warn "python3 venv support is missing on this system. Attempting to install it automatically."
      if install_python_venv_support; then
        rm -f "${error_log}"
        run_system_python -m venv "${VENV_DIR}"
        return 0
      fi
      print_error "Automatic installation of python3 venv support failed."
    else
      print_error "python3 venv support is missing on this system."
      print_error "Install ${package_hint} or python3-venv, then rerun this command."
    fi
  fi

  cat "${error_log}" >&2
  rm -f "${error_log}"
  exit 1
}

venv_is_healthy() {
  [[ -x "${VENV_PYTHON}" ]] || return 1
  "${VENV_PYTHON}" -m pip --version >/dev/null 2>&1
}

ensure_venv() {
  ensure_python
  if ! venv_is_healthy; then
    rm -rf "${VENV_DIR}"
    create_venv
  fi
}

ensure_python_packages() {
  ensure_venv
  if ! "${VENV_PYTHON}" -c 'import esptool, meshtastic, serial' >/dev/null 2>&1; then
    "${VENV_PYTHON}" -m pip install --upgrade pip
    "${VENV_PYTHON}" -m pip install --upgrade meshtastic esptool pyserial
  fi
}

derive_short_name() {
  local source_name="${1:-}"
  local derived

  derived="$(printf '%s' "${source_name}" | tr -cd '[:alnum:]' | cut -c1-4)"
  if [[ -z "${derived}" ]]; then
    derived="node"
  fi
  printf '%s\n' "${derived}"
}

prompt_with_default() {
  local label="${1}"
  local default_value="${2:-}"
  local response

  if [[ -n "${default_value}" ]]; then
    printf '%s?%s %s [%s%s%s]: ' "${COLOR_MAGENTA}" "${COLOR_RESET}" "${label}" "${COLOR_BOLD}" "${default_value}" "${COLOR_RESET}" >&2
  else
    printf '%s?%s %s: ' "${COLOR_MAGENTA}" "${COLOR_RESET}" "${label}" >&2
  fi

  read -r response
  if [[ -z "${response}" ]]; then
    response="${default_value}"
  fi
  printf '%s\n' "${response}"
}

prompt_secret_optional() {
  local label="${1}"
  local response

  printf '%s?%s %s: ' "${COLOR_MAGENTA}" "${COLOR_RESET}" "${label}" >&2
  read -r -s response
  printf '\n' >&2
  printf '%s\n' "${response}"
}

prompt_yes_no() {
  local label="${1}"
  local default_answer="${2:-Y}"
  local response

  printf '%s?%s %s [%s]: ' "${COLOR_MAGENTA}" "${COLOR_RESET}" "${label}" "${default_answer}" >&2
  read -r response
  if [[ -z "${response}" ]]; then
    response="${default_answer}"
  fi

  case "${response}" in
    y|Y|yes|YES)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

run_in_venv() {
  ensure_python_packages
  # shellcheck disable=SC1091
  source "${VENV_ACTIVATE}"
  "$@"
}

bootstrap() {
  ensure_python_packages
}

check_port() {
  if ! meshtastic_port_reference_is_valid "${PORT}"; then
    echo "Serial port not found: ${PORT}" >&2
    exit 1
  fi
}

resolve_port() {
  meshtastic_resolve_port "${PORT}"
}

check_firmware() {
  local required_files=(
    "${FIRMWARE_INSTALLER}"
    "${FIRMWARE_DIR}/${FIRMWARE_BIN}"
    "${FIRMWARE_DIR}/bleota-s3.bin"
    "${FIRMWARE_DIR}/littlefs-${FIRMWARE_BIN#firmware-}"
  )
  local file

  for file in "${required_files[@]}"; do
    if [[ ! -f "${file}" ]]; then
      echo "Required firmware file not found: ${file}" >&2
      exit 1
    fi
  done
}

check_status_tool() {
  if [[ ! -f "${STATUS_TOOL}" ]]; then
    print_error "Status tool not found: ${STATUS_TOOL}"
    exit 1
  fi
}

check_monitor_tool() {
  if [[ ! -f "${MONITOR_TOOL}" ]]; then
    print_error "Monitor tool not found: ${MONITOR_TOOL}"
    exit 1
  fi
}

check_messages_tool() {
  if [[ ! -f "${MESSAGES_TOOL}" ]]; then
    print_error "Messages tool not found: ${MESSAGES_TOOL}"
    exit 1
  fi
}

check_protocol_tool() {
  if [[ ! -f "${PROTOCOL_TOOL}" ]]; then
    print_error "Protocol tool not found: ${PROTOCOL_TOOL}"
    exit 1
  fi
}

check_plugins_tool() {
  if [[ ! -f "${PLUGINS_TOOL}" ]]; then
    print_error "Plugins tool not found: ${PLUGINS_TOOL}"
    exit 1
  fi
}

check_proxy_tool() {
  if [[ ! -f "${PROXY_TOOL}" ]]; then
    print_error "Proxy tool not found: ${PROXY_TOOL}"
    exit 1
  fi
}

check_console_tool() {
  if [[ ! -f "${CONSOLE_TOOL}" ]]; then
    print_error "Console tool not found: ${CONSOLE_TOOL}"
    exit 1
  fi
}

wait_for_port() {
  local elapsed=0

  while (( elapsed < PORT_WAIT_SECONDS )); do
    if meshtastic_port_reference_is_valid "${PORT}"; then
      return 0
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done

  echo "Timed out waiting for serial port: ${PORT}" >&2
  exit 1
}

ensure_runtime_dir() {
  mkdir -p "${RUNTIME_DIR}"
}

service_config_value() {
  case "${1}" in
    MESHTASTIC_PORT)
      printf '%s\n' "${PORT}"
      ;;
    MESHTASTIC_BAUD)
      printf '%s\n' "${BAUD}"
      ;;
    MESHTASTIC_TCP_PORT)
      printf '%s\n' "${TCP_PORT}"
      ;;
    MESHTASTIC_PROXY_BIND_HOST)
      printf '%s\n' "${PROXY_BIND_HOST}"
      ;;
    MESHTASTIC_PROXY_HOST)
      printf '%s\n' "${PROXY_CONNECT_HOST}"
      ;;
    MESHTASTIC_PROTOCOL_LOG_NAME)
      printf '%s\n' "${PROTOCOL_LOG_NAME}"
      ;;
    *)
      return 1
      ;;
  esac
}

write_service_config() {
  ensure_runtime_dir
  cat > "${SERVICE_CONFIG_FILE}" <<EOF
MESHTASTIC_PORT=$(printf '%q' "${PORT}")
MESHTASTIC_BAUD=$(printf '%q' "${BAUD}")
MESHTASTIC_TCP_PORT=$(printf '%q' "${TCP_PORT}")
MESHTASTIC_PROXY_BIND_HOST=$(printf '%q' "${PROXY_BIND_HOST}")
MESHTASTIC_PROXY_HOST=$(printf '%q' "${PROXY_CONNECT_HOST}")
MESHTASTIC_PROTOCOL_LOG_NAME=$(printf '%q' "${PROTOCOL_LOG_NAME}")
EOF
}

ensure_service_config() {
  ensure_runtime_dir
  if [[ -f "${SERVICE_CONFIG_FILE}" ]]; then
    return 1
  fi
  write_service_config
  return 0
}

warn_if_service_config_differs() {
  [[ -f "${SERVICE_CONFIG_FILE}" ]] || return 0

  local key configured current drift='no'
  for key in \
    MESHTASTIC_PORT \
    MESHTASTIC_BAUD \
    MESHTASTIC_TCP_PORT \
    MESHTASTIC_PROXY_BIND_HOST \
    MESHTASTIC_PROXY_HOST \
    MESHTASTIC_PROTOCOL_LOG_NAME; do
    configured="$(sed -n "s/^${key}=//p" "${SERVICE_CONFIG_FILE}" | head -n1)"
    current="$(printf '%q' "$(service_config_value "${key}")")"
    if [[ -n "${configured}" && "${configured}" != "${current}" ]]; then
      if [[ "${drift}" == 'no' ]]; then
        print_warn "Existing service config preserved at ${SERVICE_CONFIG_FILE}."
        print_warn "Current shell settings differ from the persisted service settings:"
        drift='yes'
      fi
      printf '  %s: config=%s shell=%s\n' "${key}" "${configured}" "${current}" >&2
    fi
  done
}

is_linux() {
  meshtastic_is_linux
}

have_systemctl_user() {
  is_linux || return 1
  command -v systemctl >/dev/null 2>&1 || return 1
  [[ -n "${XDG_RUNTIME_DIR:-}" || -n "${DBUS_SESSION_BUS_ADDRESS:-}" ]] || return 1
  systemctl --user --version >/dev/null 2>&1
}

have_systemctl_system() {
  is_linux || return 1
  command -v systemctl >/dev/null 2>&1 || return 1
  systemctl --version >/dev/null 2>&1
}

require_systemd_user() {
  if ! is_linux; then
    print_error "Proxy autostart is only supported on Linux."
    exit 1
  fi

  if ! command -v systemctl >/dev/null 2>&1; then
    print_error "systemctl is required for proxy autostart on Linux."
    exit 1
  fi

  if ! systemctl --user --version >/dev/null 2>&1; then
    print_error "systemd user services are not available in this session."
    exit 1
  fi
}

require_systemd_system() {
  if ! is_linux; then
    print_error "Proxy autostart is only supported on Linux."
    exit 1
  fi

  if ! command -v systemctl >/dev/null 2>&1; then
    print_error "systemctl is required for proxy autostart on Linux."
    exit 1
  fi
}

run_with_sudo() {
  if [[ "${EUID}" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

proxy_service_scope() {
  case "${1:-}" in
    ""|--user)
      printf 'user\n'
      ;;
    --system)
      printf 'system\n'
      ;;
    *)
      print_error "Unknown autostart scope: ${1}. Use --user or --system."
      exit 1
      ;;
  esac
}

proxy_user_service_installed() {
  [[ -f "${PROXY_SYSTEMD_USER_UNIT_FILE}" ]]
}

proxy_system_service_installed() {
  [[ -f "${PROXY_SYSTEMD_SYSTEM_UNIT_FILE}" ]]
}

protocol_user_service_installed() {
  [[ -f "${PROTOCOL_SYSTEMD_USER_UNIT_FILE}" ]]
}

protocol_system_service_installed() {
  [[ -f "${PROTOCOL_SYSTEMD_SYSTEM_UNIT_FILE}" ]]
}

proxy_user_service_active() {
  if ! have_systemctl_user; then
    return 1
  fi
  systemctl --user is-active --quiet "${PROXY_SYSTEMD_UNIT}"
}

proxy_system_service_active() {
  if ! have_systemctl_system; then
    return 1
  fi
  systemctl is-active --quiet "${PROXY_SYSTEMD_UNIT}"
}

protocol_user_service_active() {
  if ! have_systemctl_user; then
    return 1
  fi
  systemctl --user is-active --quiet "${PROTOCOL_SYSTEMD_UNIT}"
}

protocol_system_service_active() {
  if ! have_systemctl_system; then
    return 1
  fi
  systemctl is-active --quiet "${PROTOCOL_SYSTEMD_UNIT}"
}

proxy_user_service_enabled() {
  if ! have_systemctl_user; then
    return 1
  fi
  systemctl --user is-enabled --quiet "${PROXY_SYSTEMD_UNIT}"
}

proxy_system_service_enabled() {
  if ! have_systemctl_system; then
    return 1
  fi
  systemctl is-enabled --quiet "${PROXY_SYSTEMD_UNIT}"
}

proxy_user_service_pid() {
  if ! proxy_user_service_active; then
    return 1
  fi

  systemctl --user show "${PROXY_SYSTEMD_UNIT}" --property MainPID --value 2>/dev/null
}

proxy_system_service_pid() {
  if ! proxy_system_service_active; then
    return 1
  fi

  systemctl show "${PROXY_SYSTEMD_UNIT}" --property MainPID --value 2>/dev/null
}

proxy_manual_pid() {
  if [[ -f "${PROXY_PID_FILE}" ]]; then
    cat "${PROXY_PID_FILE}"
  fi
}

protocol_manual_pid() {
  if [[ -f "${PROTOCOL_PID_FILE}" ]]; then
    cat "${PROTOCOL_PID_FILE}"
  fi
}

proxy_pid() {
  local pid

  pid="$(proxy_system_service_pid || true)"
  if [[ -n "${pid}" && "${pid}" != "0" ]]; then
    printf '%s\n' "${pid}"
    return 0
  fi

  pid="$(proxy_user_service_pid || true)"
  if [[ -n "${pid}" && "${pid}" != "0" ]]; then
    printf '%s\n' "${pid}"
    return 0
  fi

  proxy_manual_pid || true
}

proxy_manual_is_running() {
  local pid
  pid="$(proxy_manual_pid || true)"

  if [[ -z "${pid}" ]]; then
    return 1
  fi

  if kill -0 "${pid}" >/dev/null 2>&1; then
    return 0
  fi

  rm -f "${PROXY_PID_FILE}"
  return 1
}

protocol_manual_is_running() {
  local pid
  pid="$(protocol_manual_pid || true)"

  if [[ -z "${pid}" ]]; then
    return 1
  fi

  if kill -0 "${pid}" >/dev/null 2>&1; then
    return 0
  fi

  rm -f "${PROTOCOL_PID_FILE}"
  return 1
}

protocol_start_manual() {
  if protocol_manual_is_running; then
    return 0
  fi

  nohup "${VENV_PYTHON}" "${PROTOCOL_TOOL}" \
    --host "${PROXY_CONNECT_HOST}" \
    --tcp-port "${TCP_PORT}" \
    "${PROTOCOL_LOG_NAME}" \
    --quiet \
    >>"${PROTOCOL_RUNNER_LOG_FILE}" 2>&1 &
  echo "$!" > "${PROTOCOL_PID_FILE}"
}

protocol_stop_manual() {
  local pid

  if ! protocol_manual_is_running; then
    rm -f "${PROTOCOL_PID_FILE}"
    return 0
  fi

  pid="$(protocol_manual_pid)"
  kill "${pid}" >/dev/null 2>&1 || true

  for _ in $(seq 1 20); do
    if ! kill -0 "${pid}" >/dev/null 2>&1; then
      rm -f "${PROTOCOL_PID_FILE}"
      return 0
    fi
    sleep 0.25
  done

  kill -9 "${pid}" >/dev/null 2>&1 || true
  rm -f "${PROTOCOL_PID_FILE}"
}

proxy_is_running() {
  if proxy_system_service_active; then
    return 0
  fi

  if proxy_user_service_active; then
    return 0
  fi

  proxy_manual_is_running
}

proxy_manager_label() {
  if proxy_system_service_active; then
    printf 'systemd-system\n'
    return 0
  fi

  if proxy_user_service_active; then
    printf 'systemd-user\n'
    return 0
  fi

  if proxy_manual_is_running; then
    printf 'manual\n'
    return 0
  fi

  printf 'stopped\n'
}

proxy_installed_manager_label() {
  if proxy_system_service_installed; then
    printf 'systemd-system\n'
    return 0
  fi

  if proxy_user_service_installed; then
    printf 'systemd-user\n'
    return 0
  fi

  printf 'stopped\n'
}

proxy_service_installed() {
  proxy_system_service_installed || proxy_user_service_installed
}

proxy_service_active() {
  proxy_system_service_active || proxy_user_service_active
}

proxy_service_start() {
  local manager
  manager="$(proxy_installed_manager_label)"

  case "${manager}" in
    systemd-system)
      require_systemd_system
      run_with_sudo systemctl start "${PROXY_SYSTEMD_UNIT}"
      ;;
    systemd-user)
      require_systemd_user
      systemctl --user start "${PROXY_SYSTEMD_UNIT}"
      ;;
    *)
      return 1
      ;;
  esac
}

proxy_service_stop() {
  local manager
  manager="$(proxy_manager_label)"

  case "${manager}" in
    systemd-system)
      require_systemd_system
      run_with_sudo systemctl stop "${PROXY_SYSTEMD_UNIT}"
      ;;
    systemd-user)
      require_systemd_user
      systemctl --user stop "${PROXY_SYSTEMD_UNIT}"
      ;;
    *)
      return 1
      ;;
  esac
}

protocol_service_start() {
  local manager
  manager="$(proxy_installed_manager_label)"

  case "${manager}" in
    systemd-system)
      require_systemd_system
      run_with_sudo systemctl start "${PROTOCOL_SYSTEMD_UNIT}"
      ;;
    systemd-user)
      require_systemd_user
      systemctl --user start "${PROTOCOL_SYSTEMD_UNIT}"
      ;;
    *)
      return 1
      ;;
  esac
}

protocol_service_stop() {
  local manager
  manager="$(proxy_manager_label)"

  case "${manager}" in
    systemd-system)
      require_systemd_system
      run_with_sudo systemctl stop "${PROTOCOL_SYSTEMD_UNIT}"
      ;;
    systemd-user)
      require_systemd_user
      systemctl --user stop "${PROTOCOL_SYSTEMD_UNIT}"
      ;;
    *)
      return 1
      ;;
  esac
}

proxy_service_log() {
  local manager
  manager="$(proxy_installed_manager_label)"

  case "${manager}" in
    systemd-system)
      require_systemd_system
      journalctl -u "${PROXY_SYSTEMD_UNIT}" -n 50 -f
      ;;
    systemd-user)
      require_systemd_user
      journalctl --user -u "${PROXY_SYSTEMD_UNIT}" -n 50 -f
      ;;
    *)
      return 1
      ;;
  esac
}

proxy_unit_content() {
  local scope="${1}"
  local extra_unit_lines=""
  local extra_service_lines=""

  if [[ "${scope}" == "system" ]]; then
    local service_user service_group
    service_user="$(stat -c '%U' "${ROOT_DIR}")"
    service_group="$(stat -c '%G' "${ROOT_DIR}")"
    extra_unit_lines="RequiresMountsFor=${ROOT_DIR}"
    extra_service_lines="User=${service_user}
Group=${service_group}"
  fi

  cat <<EOF
[Unit]
Description=Meshtastic serial-to-TCP proxy and broker
After=default.target local-fs.target
Wants=${PROTOCOL_SYSTEMD_UNIT}
${extra_unit_lines}

[Service]
Type=simple
WorkingDirectory=${ROOT_DIR}
${extra_service_lines}
EnvironmentFile=${SERVICE_CONFIG_FILE}
ExecStart=${VENV_PYTHON} ${PROXY_TOOL} --serial-port \${MESHTASTIC_PORT} --baud \${MESHTASTIC_BAUD} --listen-host \${MESHTASTIC_PROXY_BIND_HOST} --listen-port \${MESHTASTIC_TCP_PORT} --status-file ${PROXY_STATUS_FILE}
Restart=always
RestartSec=2
Environment=PYTHONUNBUFFERED=1
StandardOutput=journal
StandardError=journal
SyslogIdentifier=meshtastic-proxy

[Install]
WantedBy=default.target
EOF
}

protocol_unit_content() {
  local scope="${1}"
  local extra_unit_lines=""
  local extra_service_lines=""

  if [[ "${scope}" == "system" ]]; then
    local service_user service_group
    service_user="$(stat -c '%U' "${ROOT_DIR}")"
    service_group="$(stat -c '%G' "${ROOT_DIR}")"
    extra_unit_lines="RequiresMountsFor=${ROOT_DIR}"
    extra_service_lines="User=${service_user}
Group=${service_group}"
  fi

  cat <<EOF
[Unit]
Description=Meshtastic protocol logger
After=${PROXY_SYSTEMD_UNIT}
Requires=${PROXY_SYSTEMD_UNIT}
PartOf=${PROXY_SYSTEMD_UNIT}
${extra_unit_lines}

[Service]
Type=simple
WorkingDirectory=${ROOT_DIR}
${extra_service_lines}
EnvironmentFile=${SERVICE_CONFIG_FILE}
ExecStart=${VENV_PYTHON} ${PROTOCOL_TOOL} --host \${MESHTASTIC_PROXY_HOST} --tcp-port \${MESHTASTIC_TCP_PORT} \${MESHTASTIC_PROTOCOL_LOG_NAME} --quiet
Restart=always
RestartSec=2
Environment=PYTHONUNBUFFERED=1
StandardOutput=journal
StandardError=journal
SyslogIdentifier=meshtastic-protocol

[Install]
WantedBy=default.target
EOF
}

proxy_write_user_systemd_unit() {
  mkdir -p "${PROXY_SYSTEMD_USER_DIR}"
  proxy_unit_content user > "${PROXY_SYSTEMD_USER_UNIT_FILE}"
}

proxy_write_system_systemd_unit() {
  run_with_sudo mkdir -p "${PROXY_SYSTEMD_SYSTEM_DIR}"
  proxy_unit_content system | run_with_sudo tee "${PROXY_SYSTEMD_SYSTEM_UNIT_FILE}" >/dev/null
}

protocol_write_user_systemd_unit() {
  mkdir -p "${PROXY_SYSTEMD_USER_DIR}"
  protocol_unit_content user > "${PROTOCOL_SYSTEMD_USER_UNIT_FILE}"
}

protocol_write_system_systemd_unit() {
  run_with_sudo mkdir -p "${PROXY_SYSTEMD_SYSTEM_DIR}"
  protocol_unit_content system | run_with_sudo tee "${PROTOCOL_SYSTEMD_SYSTEM_UNIT_FILE}" >/dev/null
}

proxy_linger_status() {
  if ! command -v loginctl >/dev/null 2>&1; then
    return 1
  fi

  loginctl show-user "${USER}" --property Linger --value 2>/dev/null || true
}

proxy_autostart_install_user() {
  require_systemd_user
  check_proxy_tool
  check_protocol_tool
  ensure_python_packages
  ensure_runtime_dir
  if ensure_service_config; then
    print_warn "Created persistent service config: ${SERVICE_CONFIG_FILE}"
    print_warn "Review and edit it, then run proxy-autostart-install again to install the service units."
    return 0
  fi
  warn_if_service_config_differs
  local had_user_service='no'

  if proxy_system_service_installed || proxy_system_service_enabled || proxy_system_service_active; then
    print_info "Removing conflicting system-wide autostart before installing the user service."
    proxy_autostart_remove_system
  fi

  if proxy_user_service_installed || proxy_user_service_enabled || proxy_user_service_active; then
    had_user_service='yes'
  fi

  if proxy_manual_is_running; then
    print_info "Stopping manually started proxy before enabling systemd autostart."
    proxy_stop
  fi

  proxy_write_user_systemd_unit
  protocol_write_user_systemd_unit
  systemctl --user daemon-reload
  if [[ "${had_user_service}" == 'yes' ]]; then
    systemctl --user enable "${PROXY_SYSTEMD_UNIT}"
    systemctl --user restart "${PROXY_SYSTEMD_UNIT}"
  else
    systemctl --user enable --now "${PROXY_SYSTEMD_UNIT}"
  fi

  for _ in $(seq 1 20); do
    if proxy_user_service_active && tcp_endpoint_ready "${PROXY_CONNECT_HOST}" "${TCP_PORT}"; then
      print_success "Proxy autostart installed via systemd user service ${PROXY_SYSTEMD_UNIT}."
      print_info "Logs: journalctl --user -u ${PROXY_SYSTEMD_UNIT} -f"
      print_info "Config: ${SERVICE_CONFIG_FILE}"
      if [[ "$(proxy_linger_status || true)" != "yes" ]]; then
        print_warn "This user service starts automatically after login. To keep it running across reboots before login, enable linger with: sudo loginctl enable-linger ${USER}"
      fi
      return 0
    fi
    sleep 0.25
  done

  print_error "Systemd service was enabled, but the proxy did not become healthy. Check: journalctl --user -u ${PROXY_SYSTEMD_UNIT} -n 100"
  return 1
}

proxy_autostart_install_system() {
  require_systemd_system
  check_proxy_tool
  check_protocol_tool
  ensure_python_packages
  ensure_runtime_dir
  if ensure_service_config; then
    print_warn "Created persistent service config: ${SERVICE_CONFIG_FILE}"
    print_warn "Review and edit it, then run proxy-autostart-install again to install the service units."
    return 0
  fi
  warn_if_service_config_differs
  local had_system_service='no'

  if proxy_user_service_installed || proxy_user_service_enabled || proxy_user_service_active; then
    print_info "Removing conflicting user-scoped autostart before installing the system service."
    proxy_autostart_remove_user
  fi

  if proxy_system_service_installed || proxy_system_service_enabled || proxy_system_service_active; then
    had_system_service='yes'
  fi

  if proxy_manual_is_running; then
    print_info "Stopping manually started proxy before enabling systemd autostart."
    proxy_stop
  fi

  proxy_write_system_systemd_unit
  protocol_write_system_systemd_unit
  run_with_sudo systemctl daemon-reload
  if [[ "${had_system_service}" == 'yes' ]]; then
    run_with_sudo systemctl enable "${PROXY_SYSTEMD_UNIT}"
    run_with_sudo systemctl restart "${PROXY_SYSTEMD_UNIT}"
  else
    run_with_sudo systemctl enable --now "${PROXY_SYSTEMD_UNIT}"
  fi

  for _ in $(seq 1 20); do
    if proxy_system_service_active && tcp_endpoint_ready "${PROXY_CONNECT_HOST}" "${TCP_PORT}"; then
      print_success "Proxy autostart installed via system-wide service ${PROXY_SYSTEMD_SYSTEM_UNIT_FILE}."
      print_info "Logs: journalctl -u ${PROXY_SYSTEMD_UNIT} -f"
      print_info "Config: ${SERVICE_CONFIG_FILE}"
      return 0
    fi
    sleep 0.25
  done

  print_error "System-wide service was enabled, but the proxy did not become healthy. Check: sudo journalctl -u ${PROXY_SYSTEMD_UNIT} -n 100"
  return 1
}

proxy_autostart_remove_user() {
  require_systemd_user

  if proxy_user_service_installed || proxy_user_service_enabled || proxy_user_service_active; then
    systemctl --user disable --now "${PROXY_SYSTEMD_UNIT}" >/dev/null 2>&1 || true
    systemctl --user reset-failed "${PROXY_SYSTEMD_UNIT}" >/dev/null 2>&1 || true
    systemctl --user disable --now "${PROTOCOL_SYSTEMD_UNIT}" >/dev/null 2>&1 || true
    systemctl --user reset-failed "${PROTOCOL_SYSTEMD_UNIT}" >/dev/null 2>&1 || true
  fi

  rm -f "${PROXY_SYSTEMD_USER_UNIT_FILE}"
  rm -f "${PROTOCOL_SYSTEMD_USER_UNIT_FILE}"
  systemctl --user daemon-reload
  rm -f "${PROXY_PID_FILE}"
  rm -f "${PROTOCOL_PID_FILE}"
  print_success "Proxy autostart removed for systemd user service ${PROXY_SYSTEMD_UNIT}."
}

proxy_autostart_remove_system() {
  require_systemd_system

  if proxy_system_service_installed || proxy_system_service_enabled || proxy_system_service_active; then
    run_with_sudo systemctl disable --now "${PROXY_SYSTEMD_UNIT}" >/dev/null 2>&1 || true
    run_with_sudo systemctl reset-failed "${PROXY_SYSTEMD_UNIT}" >/dev/null 2>&1 || true
    run_with_sudo systemctl disable --now "${PROTOCOL_SYSTEMD_UNIT}" >/dev/null 2>&1 || true
    run_with_sudo systemctl reset-failed "${PROTOCOL_SYSTEMD_UNIT}" >/dev/null 2>&1 || true
  fi

  if proxy_system_service_installed; then
    run_with_sudo rm -f "${PROXY_SYSTEMD_SYSTEM_UNIT_FILE}"
    run_with_sudo rm -f "${PROTOCOL_SYSTEMD_SYSTEM_UNIT_FILE}"
  fi
  run_with_sudo systemctl daemon-reload
  rm -f "${PROXY_PID_FILE}"
  rm -f "${PROTOCOL_PID_FILE}"
  print_success "Proxy autostart removed for system-wide service ${PROXY_SYSTEMD_SYSTEM_UNIT_FILE}."
}

proxy_autostart_install() {
  local scope
  scope="$(proxy_service_scope "${1:-}")"

  if [[ "${scope}" == "system" ]]; then
    proxy_autostart_install_system
  else
    proxy_autostart_install_user
  fi
}

proxy_autostart_remove() {
  local scope
  scope="$(proxy_service_scope "${1:-}")"

  if [[ "${scope}" == "system" ]]; then
    proxy_autostart_remove_system
  else
    proxy_autostart_remove_user
  fi
}

proxy_autostart_status_user() {
  require_systemd_user
  local active enabled linger
  active='no'
  enabled='no'
  linger="$(proxy_linger_status || true)"

  if proxy_user_service_active; then
    active='yes'
  fi
  if proxy_user_service_enabled; then
    enabled='yes'
  fi

  printf 'Proxy autostart scope: user\n'
  printf '  Installed: %s\n' "$([[ -f "${PROXY_SYSTEMD_USER_UNIT_FILE}" ]] && printf 'yes' || printf 'no')"
  printf '  Unit:      %s\n' "${PROXY_SYSTEMD_USER_UNIT_FILE}"
  printf '  Runtime:   %s\n' "${ROOT_DIR}"
  printf '  Status:    %s\n' "${PROXY_STATUS_FILE}"
  printf '  Enabled: %s\n' "${enabled}"
  printf '  Active:  %s\n' "${active}"
  if [[ -n "${linger}" ]]; then
    printf '  Linger:  %s\n' "${linger}"
  fi
}

proxy_autostart_status_system() {
  require_systemd_system
  local active enabled
  active='no'
  enabled='no'

  if proxy_system_service_active; then
    active='yes'
  fi
  if proxy_system_service_enabled; then
    enabled='yes'
  fi

  printf 'Proxy autostart scope: system\n'
  printf '  Installed: %s\n' "$([[ -f "${PROXY_SYSTEMD_SYSTEM_UNIT_FILE}" ]] && printf 'yes' || printf 'no')"
  printf '  Unit:      %s\n' "${PROXY_SYSTEMD_SYSTEM_UNIT_FILE}"
  printf '  Runtime:   %s\n' "${ROOT_DIR}"
  printf '  Status:    %s\n' "${PROXY_STATUS_FILE}"
  printf '  Enabled:   %s\n' "${enabled}"
  printf '  Active:    %s\n' "${active}"
}

proxy_autostart_status() {
  local scope
  scope="$(proxy_service_scope "${1:-}")"

  if [[ "${scope}" == "system" ]]; then
    proxy_autostart_status_system
  else
    proxy_autostart_status_user
  fi
}

read_proxy_status_field() {
  local field_path="${1}"

  if [[ ! -f "${PROXY_STATUS_FILE}" ]]; then
    return 1
  fi

  run_system_python - "${PROXY_STATUS_FILE}" "${field_path}" <<'PY'
import json
import sys

status_path = sys.argv[1]
field_path = sys.argv[2].split('.')

with open(status_path, encoding='utf-8') as handle:
    value = json.load(handle)

for part in field_path:
    value = value.get(part)
    if value is None:
        raise SystemExit(1)

if isinstance(value, bool):
    print('true' if value else 'false')
else:
    print(value)
PY
}

proxy_print_json() {
  local health="${1}"
  local pid
  local manager
  pid="$(proxy_pid || true)"
  manager="$(proxy_manager_label)"

  run_system_python - "${health}" "${PROXY_STATUS_FILE}" "${PROXY_CONNECT_HOST}" "${TCP_PORT}" "${PORT}" "${PROXY_LOG_FILE}" "${pid}" "${manager}" "${PROXY_SYSTEMD_UNIT}" <<'PY'
import json
import pathlib
import sys

health = sys.argv[1]
status_path = pathlib.Path(sys.argv[2])
tcp_host = sys.argv[3]
tcp_port = int(sys.argv[4])
serial_port = sys.argv[5]
log_file = sys.argv[6]
pid = sys.argv[7] or None
manager = sys.argv[8]
journal_unit = sys.argv[9]

payload = {}
if status_path.exists():
  try:
    payload = json.loads(status_path.read_text(encoding="utf-8"))
  except json.JSONDecodeError:
    payload = {}

payload.update(
  {
    "health": health,
    "running": health in {"healthy", "unhealthy"},
    "reachable": health == "healthy",
    "tcp_host": tcp_host,
    "tcp_port": tcp_port,
    "serial_port": serial_port,
    "log_file": log_file,
    "pid": pid or (payload.get("pid") if health in {"healthy", "unhealthy"} else None),
    "manager": manager,
    "journal_unit": journal_unit if manager == "systemd-user" else None,
  }
)
print(json.dumps(payload, indent=2, sort_keys=True))
PY
}

tcp_endpoint_ready() {
  local host="${1}"
  local port="${2}"

  run_system_python - "${host}" "${port}" <<'PY' >/dev/null 2>&1
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])

with socket.create_connection((host, port), timeout=1.0):
    pass
PY
}

proxy_is_ready() {
  if ! proxy_is_running; then
    return 1
  fi

  tcp_endpoint_ready "${PROXY_CONNECT_HOST}" "${TCP_PORT}"
}

effective_host() {
  if [[ -n "${HOST}" ]]; then
    printf '%s\n' "${HOST}"
    return 0
  fi

  if proxy_is_ready; then
    printf '%s\n' "${PROXY_CONNECT_HOST}"
    return 0
  fi

  return 1
}

require_direct_serial() {
  if proxy_is_running; then
    print_error "The local proxy is running and owns ${PORT}. Stop it first with ./setup/meshtastic-python.sh proxy-stop."
    exit 1
  fi
}

run_meshtastic_cli() {
  local host
  host="$(effective_host || true)"

  ensure_python_packages
  # shellcheck disable=SC1091
  source "${VENV_ACTIVATE}"

  if [[ -n "${host}" ]]; then
    meshtastic --host "${host}" "$@"
  else
    check_port
    meshtastic --port "${PORT}" "$@"
  fi
}

flash() {
  require_direct_serial
  check_port
  check_firmware
  ensure_python_packages
  # shellcheck disable=SC1091
  source "${VENV_ACTIVATE}"
  (
    cd "${FIRMWARE_DIR}"
    PYTHON="${VENV_PYTHON}" bash "${FIRMWARE_INSTALLER}" -p "${PORT}" -f "${FIRMWARE_BIN}"
  )
  wait_for_port
}

probe() {
  run_meshtastic_cli --info
}

nodes() {
  run_meshtastic_cli --nodes
}

nodedb_reset() {
  run_meshtastic_cli --reset-nodedb
}

contacts() {
  local action="${1:-list}"

  case "${action}" in
    list)
      run_in_venv python - "${ROOT_DIR}" "${PORT}" "${HOST}" "${TCP_PORT}" <<'PY'
import sys

repo_root, port, host, tcp_port = sys.argv[1:5]
sys.path.insert(0, repo_root)
sys.path.insert(0, f"{repo_root}/tools")

from _meshtastic_common import Palette, resolve_meshtastic_target, style

from meshtastic.serial_interface import SerialInterface
from meshtastic.tcp_interface import TCPInterface


def connect_interface(target):
    if target.mode == "tcp":
        return TCPInterface(target.host, portNumber=target.tcp_port)
    return SerialInterface(target.serial_port)


PALETTE = Palette()

target = resolve_meshtastic_target(port=port, host=host, tcp_port=int(tcp_port))
iface = connect_interface(target)
try:
    print(style(PALETTE, PALETTE.bold + PALETTE.cyan, "Contacts"))
    header = f"{'ID':<12} {'Long Name':<24} {'Short':<8} {'Model':<14} {'PK':<3} {'Fav':<3} {'Ign':<3}"
    print(style(PALETTE, PALETTE.bold, header))
    for node in sorted(iface.nodes.values(), key=lambda item: item.get("num", 0)):
        user = node.get("user", {})
        print(
            f"{user.get('id', '-'):12} "
            f"{user.get('longName', '-'):24.24} "
            f"{user.get('shortName', '-'):8.8} "
            f"{user.get('hwModel', '-'):14.14} "
            f"{'yes' if user.get('publicKey') else 'no':<3} "
            f"{'yes' if node.get('isFavorite') else 'no':<3} "
            f"{'yes' if user.get('isIgnored') or node.get('isIgnored') else 'no':<3}"
        )
finally:
    iface.close()
PY
      ;;
    keys)
      run_in_venv python - "${ROOT_DIR}" "${PORT}" "${HOST}" "${TCP_PORT}" <<'PY'
import sys

repo_root, port, host, tcp_port = sys.argv[1:5]
sys.path.insert(0, repo_root)
sys.path.insert(0, f"{repo_root}/tools")

from _meshtastic_common import Palette, resolve_meshtastic_target, style

from meshtastic.serial_interface import SerialInterface
from meshtastic.tcp_interface import TCPInterface


def connect_interface(target):
    if target.mode == "tcp":
        return TCPInterface(target.host, portNumber=target.tcp_port)
    return SerialInterface(target.serial_port)


PALETTE = Palette()

target = resolve_meshtastic_target(port=port, host=host, tcp_port=int(tcp_port))
iface = connect_interface(target)
try:
    print(style(PALETTE, PALETTE.bold + PALETTE.cyan, "Contact Keys"))
    header = f"{'ID':<12} {'Long Name':<24} {'Short':<8} {'Public Key'}"
    print(style(PALETTE, PALETTE.bold, header))
    for node in sorted(iface.nodes.values(), key=lambda item: item.get("num", 0)):
        user = node.get("user", {})
        print(
            f"{user.get('id', '-'):12} "
            f"{user.get('longName', '-'):24.24} "
            f"{user.get('shortName', '-'):8.8} "
            f"{user.get('publicKey') or '-'}"
        )
finally:
    iface.close()
PY
      ;;
    add)
      echo "Usage: setup/meshtastic-python.sh contacts add is not supported by Meshtastic radios." >&2
      echo "Contacts are learned from on-air node info; use contacts list/keys to inspect them and nodedb-reset to force relearning." >&2
      exit 1
      ;;
    remove)
      local node_id="${2:-}"
      if [[ -z "${node_id}" ]]; then
        echo "Usage: setup/meshtastic-python.sh contacts remove <NODE_ID>" >&2
        exit 1
      fi
      run_meshtastic_cli --remove-node "${node_id}"
      ;;
    favorite)
      local node_id="${2:-}"
      if [[ -z "${node_id}" ]]; then
        echo "Usage: setup/meshtastic-python.sh contacts favorite <NODE_ID>" >&2
        exit 1
      fi
      run_meshtastic_cli --set-favorite-node "${node_id}"
      ;;
    unfavorite)
      local node_id="${2:-}"
      if [[ -z "${node_id}" ]]; then
        echo "Usage: setup/meshtastic-python.sh contacts unfavorite <NODE_ID>" >&2
        exit 1
      fi
      run_meshtastic_cli --remove-favorite-node "${node_id}"
      ;;
    ignore)
      local node_id="${2:-}"
      if [[ -z "${node_id}" ]]; then
        echo "Usage: setup/meshtastic-python.sh contacts ignore <NODE_ID>" >&2
        exit 1
      fi
      run_meshtastic_cli --set-ignored-node "${node_id}"
      ;;
    unignore)
      local node_id="${2:-}"
      if [[ -z "${node_id}" ]]; then
        echo "Usage: setup/meshtastic-python.sh contacts unignore <NODE_ID>" >&2
        exit 1
      fi
      run_meshtastic_cli --remove-ignored-node "${node_id}"
      ;;
    *)
      echo "Usage: setup/meshtastic-python.sh contacts <list|keys|add|remove|favorite|unfavorite|ignore|unignore> [NODE_ID]" >&2
      exit 1
      ;;
  esac
}

export_config() {
  run_meshtastic_cli --export-config
}

set_name() {
  local long_name="${1:-}"
  local short_name="${2:-}"
  local args=()

  if [[ -z "${long_name}" && -z "${short_name}" ]]; then
    echo "Usage: setup/meshtastic-python.sh set-name <LONG_NAME> [SHORT_NAME]" >&2
    exit 1
  fi

  if [[ -z "${short_name}" && -n "${long_name}" ]]; then
    short_name="$(derive_short_name "${long_name}")"
  fi

  if [[ -n "${long_name}" ]]; then
    args+=(--set-owner "${long_name}")
  fi
  if [[ -n "${short_name}" ]]; then
    args+=(--set-owner-short "${short_name}")
  fi

  run_meshtastic_cli "${args[@]}"
}

set_role() {
  local role="${1:-}"

  if [[ -z "${role}" ]]; then
    echo "Usage: setup/meshtastic-python.sh set-role <ROLE>" >&2
    echo "Supported roles: CLIENT, CLIENT_MUTE, ROUTER, ROUTER_CLIENT, REPEATER, TRACKER, SENSOR, TAK, CLIENT_HIDDEN, LOST_AND_FOUND, TAK_TRACKER, ROUTER_LATE, CLIENT_BASE" >&2
    exit 1
  fi

  run_meshtastic_cli --begin-edit --set device.role "${role}" --commit-edit
}

set_region() {
  local region="${1:-}"

  if [[ -z "${region}" ]]; then
    echo "Usage: setup/meshtastic-python.sh set-region <REGION>" >&2
    exit 1
  fi

  run_meshtastic_cli --begin-edit --set lora.region "${region}" --commit-edit
}

set_modem_preset() {
  local preset="${1:-}"

  if [[ -z "${preset}" ]]; then
    echo "Usage: setup/meshtastic-python.sh set-modem-preset <PRESET>" >&2
    echo "Supported presets: LONG_FAST, LONG_SLOW, VERY_LONG_SLOW, MEDIUM_SLOW, MEDIUM_FAST, SHORT_SLOW, SHORT_FAST, LONG_MODERATE, SHORT_TURBO, LONG_TURBO" >&2
    exit 1
  fi

  run_meshtastic_cli --begin-edit --set lora.modem_preset "${preset}" --commit-edit
}

set_position() {
  local latitude="${1:-}"
  local longitude="${2:-}"
  local altitude="${3:-}"
  local args=()

  if [[ -z "${latitude}" || -z "${longitude}" ]]; then
    echo "Usage: setup/meshtastic-python.sh set-position <LAT> <LON> [ALT_METERS]" >&2
    exit 1
  fi

  args+=(--setlat "${latitude}" --setlon "${longitude}")
  if [[ -n "${altitude}" ]]; then
    args+=(--setalt "${altitude}")
  fi

  run_meshtastic_cli "${args[@]}"
}

clear_position() {
  run_meshtastic_cli --remove-position
}

set_ham() {
  local callsign="${1:-}"

  if [[ -z "${callsign}" ]]; then
    echo "Usage: setup/meshtastic-python.sh set-ham <CALLSIGN>" >&2
    exit 1
  fi

  run_meshtastic_cli --set-ham "${callsign}"
}

set_wifi() {
  local ssid="${1:-}"
  local psk="${2:-}"

  if [[ -z "${ssid}" || -z "${psk}" ]]; then
    echo "Usage: setup/meshtastic-python.sh set-wifi <SSID> <PSK>" >&2
    exit 1
  fi

  run_meshtastic_cli \
    --begin-edit \
    --set network.wifi_enabled true \
    --set network.wifi_ssid "${ssid}" \
    --set network.wifi_psk "${psk}" \
    --commit-edit
}

provision() {
  flash
  set_region "${DEFAULT_REGION}"

  if [[ -n "${OWNER_LONG}" || -n "${OWNER_SHORT}" ]]; then
    set_name "${OWNER_LONG}" "${OWNER_SHORT}"
  fi

  if [[ -n "${WIFI_SSID}" || -n "${WIFI_PSK}" ]]; then
    if [[ -z "${WIFI_SSID}" || -z "${WIFI_PSK}" ]]; then
      echo "MESHTASTIC_WIFI_SSID and MESHTASTIC_WIFI_PSK must both be set to enable WiFi during provision" >&2
      exit 1
    fi
    set_wifi "${WIFI_SSID}" "${WIFI_PSK}"
  fi

  probe
}

preflight_guided_setup() {
  print_info 'Checking local prerequisites for guided setup...'
  check_firmware
  ensure_python_packages
}

guided_validate_selected_port() {
  require_direct_serial
  check_port
}

guided() {
  local default_host default_long default_short chosen_port chosen_region chosen_long chosen_short chosen_ssid chosen_psk

  preflight_guided_setup

  default_host="$(hostname -s 2>/dev/null || printf 'node')"
  default_long="${OWNER_LONG:-Meshtastic ${default_host}}"
  default_short="${OWNER_SHORT:-$(derive_short_name "${default_long}")}"

  print_banner
  chosen_port="$(prompt_with_default 'Serial port' "${PORT}")"
  chosen_region="$(prompt_with_default 'LoRa region' "${DEFAULT_REGION}")"
  chosen_long="$(prompt_with_default 'Long node name' "${default_long}")"
  chosen_short="$(prompt_with_default 'Short node name' "${default_short}")"
  chosen_ssid="$(prompt_with_default 'WiFi SSID (leave blank to skip)' "${WIFI_SSID}")"

  chosen_psk=''
  if [[ -n "${chosen_ssid}" ]]; then
    chosen_psk="$(prompt_secret_optional 'WiFi password')"
    if [[ -z "${chosen_psk}" ]]; then
      print_warn 'WiFi password left blank, WiFi setup will be skipped.'
      chosen_ssid=''
    fi
  fi

  PORT="${chosen_port}"
  guided_validate_selected_port

  printf '\n%sSelected configuration%s\n' "${COLOR_BOLD}" "${COLOR_RESET}"
  printf '  Port:   %s\n' "${PORT}"
  printf '  Region: %s\n' "${chosen_region}"
  printf '  Name:   %s (%s)\n' "${chosen_long}" "${chosen_short}"
  if [[ -n "${chosen_ssid}" ]]; then
    printf '  WiFi:   %s%s%s\n' "${COLOR_GREEN}" "${chosen_ssid}" "${COLOR_RESET}"
  else
    printf '  WiFi:   %sskipped%s\n' "${COLOR_DIM}" "${COLOR_RESET}"
  fi
  printf '\n'

  if ! prompt_yes_no 'Flash known-good firmware and apply this configuration?' 'Y'; then
    print_warn 'Aborted guided setup.'
    return 0
  fi

  flash
  set_region "${chosen_region}"
  set_name "${chosen_long}" "${chosen_short}"

  if [[ -n "${chosen_ssid}" ]]; then
    set_wifi "${chosen_ssid}" "${chosen_psk}"
  fi

  print_success 'Guided setup complete. Current node status:'
  status summary
}

status() {
  check_status_tool
  local host
  host="$(effective_host || true)"

  if [[ -n "${host}" ]]; then
    run_in_venv python "${STATUS_TOOL}" --host "${host}" --tcp-port "${TCP_PORT}" "$@"
  else
    check_port
    run_in_venv python "${STATUS_TOOL}" --port "${PORT}" "$@"
  fi
}

telemetry() {
  status telemetry "$@"
}

monitor() {
  check_monitor_tool
  local host
  host="$(effective_host || true)"

  if [[ -n "${host}" ]]; then
    run_in_venv python "${MONITOR_TOOL}" --host "${host}" --tcp-port "${TCP_PORT}" "$@"
  else
    check_port
    run_in_venv python "${MONITOR_TOOL}" --port "${PORT}" "$@"
  fi
}

messages() {
  check_messages_tool
  local host
  host="$(effective_host || true)"

  if [[ -n "${host}" ]]; then
    run_in_venv python "${MESSAGES_TOOL}" --host "${host}" --tcp-port "${TCP_PORT}" "$@"
  else
    check_port
    run_in_venv python "${MESSAGES_TOOL}" --port "${PORT}" "$@"
  fi
}

protocol() {
  check_protocol_tool
  local host
  host="$(effective_host || true)"

  if [[ -n "${host}" ]]; then
    run_in_venv python "${PROTOCOL_TOOL}" --host "${host}" --tcp-port "${TCP_PORT}" "$@"
  else
    check_port
    run_in_venv python "${PROTOCOL_TOOL}" --port "${PORT}" "$@"
  fi
}

plugins() {
  check_plugins_tool
  run_in_venv python "${PLUGINS_TOOL}" --plugins-dir "${ROOT_DIR}/plugins" --runtime-dir "${RUNTIME_DIR}" "$@"
}

proxy_start() {
  check_proxy_tool
  ensure_python_packages
  ensure_runtime_dir
  local service_manager

  if proxy_service_installed; then
    service_manager="$(proxy_installed_manager_label)"
    proxy_service_start
    protocol_service_start || true

    for _ in $(seq 1 20); do
      if proxy_is_ready; then
        print_success "Proxy started via ${service_manager} service ${PROXY_SYSTEMD_UNIT} on ${PROXY_BIND_HOST}:${TCP_PORT}."
        return 0
      fi
      sleep 0.25
    done

    print_error "Systemd started the proxy service, but TCP healthcheck failed. Check proxy-log or journalctl for ${PROXY_SYSTEMD_UNIT}."
    return 1
  fi

  if proxy_is_running; then
    print_warn "Proxy already running (pid $(proxy_pid))."
    if ! protocol_manual_is_running; then
      protocol_start_manual
    fi
    return 0
  fi

  check_port
  nohup "${VENV_PYTHON}" "${PROXY_TOOL}" \
    --serial-port "${PORT}" \
    --baud "${BAUD}" \
    --listen-host "${PROXY_BIND_HOST}" \
    --listen-port "${TCP_PORT}" \
    --status-file "${PROXY_STATUS_FILE}" \
    >>"${PROXY_LOG_FILE}" 2>&1 &
  echo "$!" > "${PROXY_PID_FILE}"

  for _ in $(seq 1 20); do
    if proxy_is_ready; then
      protocol_start_manual
      print_success "Proxy started on ${PROXY_BIND_HOST}:${TCP_PORT} for ${PORT} (pid $(proxy_pid))."
      return 0
    fi
    sleep 0.25
  done

  if proxy_is_running; then
    print_error "Proxy process started but TCP healthcheck failed. See ${PROXY_LOG_FILE}."
    return 1
  fi

  print_error "Proxy failed to start. See ${PROXY_LOG_FILE}."
  return 1
}

proxy_check() {
  local json_output="${1:-}"

  if proxy_is_ready; then
    if [[ "${json_output}" == "--json" ]]; then
      proxy_print_json healthy
      return 0
    fi
    printf 'Proxy health: healthy\n'
    printf '  TCP:   %s:%s\n' "${PROXY_CONNECT_HOST}" "${TCP_PORT}"
    return 0
  fi

  if proxy_is_running; then
    if [[ "${json_output}" == "--json" ]]; then
      proxy_print_json unhealthy
      return 1
    fi
    printf 'Proxy health: unhealthy\n'
    printf '  PID:   %s\n' "$(proxy_pid)"
    printf '  TCP:   %s:%s\n' "${PROXY_CONNECT_HOST}" "${TCP_PORT}"
    return 1
  fi

  if [[ "${json_output}" == "--json" ]]; then
    proxy_print_json stopped
    return 1
  fi

  printf 'Proxy health: stopped\n'
  printf '  TCP:   %s:%s\n' "${PROXY_CONNECT_HOST}" "${TCP_PORT}"
  return 1
}

proxy_stop() {
  local pid
  local service_manager

  if proxy_service_active; then
    service_manager="$(proxy_manager_label)"
    protocol_service_stop || true
    proxy_service_stop
    print_success "Proxy stopped via ${service_manager} service ${PROXY_SYSTEMD_UNIT}."
    return 0
  fi

  if ! proxy_is_running; then
    print_warn "Proxy is not running."
    return 0
  fi

  pid="$(proxy_pid)"
  protocol_stop_manual
  kill "${pid}" >/dev/null 2>&1 || true

  for _ in $(seq 1 20); do
    if ! kill -0 "${pid}" >/dev/null 2>&1; then
      rm -f "${PROXY_PID_FILE}"
      print_success "Proxy stopped."
      return 0
    fi
    sleep 0.25
  done

  print_warn "Proxy did not stop cleanly; sending SIGKILL to pid ${pid}."
  kill -9 "${pid}" >/dev/null 2>&1 || true
  rm -f "${PROXY_PID_FILE}"
}

proxy_status() {
  local owner denied forwarded clients serial_connected json_output admin_responses admin_owner session_key session_confirmed session_expires_in manager
  json_output="${1:-}"
  manager="$(proxy_manager_label)"

  if proxy_is_ready; then
    if [[ "${json_output}" == "--json" ]]; then
      proxy_print_json healthy
      return 0
    fi
    print_success "Proxy started on ${PROXY_BIND_HOST}:${TCP_PORT} for ${PORT} (pid $(proxy_pid))."
    owner="$(read_proxy_status_field control_owner || true)"
    denied="$(read_proxy_status_field denied_control_frames || true)"
    forwarded="$(read_proxy_status_field forwarded_control_frames || true)"
    clients="$(read_proxy_status_field client_count || true)"
    serial_connected="$(read_proxy_status_field serial_connected || true)"
    admin_responses="$(read_proxy_status_field observed_admin_responses || true)"
    admin_owner="$(read_proxy_status_field last_admin_response_owner || true)"
    session_key="$(read_proxy_status_field last_session_passkey || true)"
    session_confirmed="$(read_proxy_status_field control_session_confirmed || true)"
    session_expires_in="$(read_proxy_status_field control_session_expires_in || true)"
    if [[ -n "${clients}" ]]; then
      printf '  Clients: %s\n' "${clients}"
    fi
    printf '  Manager: %s\n' "${manager}"
    if [[ -n "${serial_connected}" ]]; then
      printf '  Serial:  %s\n' "${serial_connected}"
    fi
    if [[ -n "${owner}" ]]; then
      printf '  Control: %s\n' "${owner}"
    else
      printf '  Control: idle\n'
    fi
    if [[ -n "${session_confirmed}" ]]; then
      printf '  Session confirmed: %s\n' "${session_confirmed}"
    fi
    if [[ -n "${session_expires_in}" ]]; then
      printf '  Lease remaining: %ss\n' "${session_expires_in}"
    fi
    if [[ -n "${forwarded}" ]]; then
      printf '  Allowed: %s\n' "${forwarded}"
    fi
    if [[ -n "${denied}" ]]; then
      printf '  Denied:  %s\n' "${denied}"
    fi
    if [[ -n "${admin_responses}" ]]; then
      printf '  Admin responses: %s\n' "${admin_responses}"
    fi
    if [[ -n "${admin_owner}" ]]; then
      printf '  Last admin owner: %s\n' "${admin_owner}"
    fi
    if [[ -n "${session_key}" ]]; then
      printf '  Last session key: %s\n' "${session_key}"
    fi
    return 0
  fi

  if proxy_is_running; then
    if [[ "${json_output}" == "--json" ]]; then
      proxy_print_json unhealthy
      return 0
    fi
    printf 'Proxy:   unhealthy\n'
    printf '  PID:   %s\n' "$(proxy_pid)"
    printf '  Manager: %s\n' "${manager}"
    printf '  TCP:   %s:%s\n' "${PROXY_CONNECT_HOST}" "${TCP_PORT}"
    printf '  Serial:%s\n' " ${PORT}"
    printf '  Log:   %s\n' "${PROXY_LOG_FILE}"
    return 0
  fi

  if [[ "${json_output}" == "--json" ]]; then
    proxy_print_json stopped
    return 0
  fi

  printf 'Proxy:   stopped\n'
  if proxy_service_installed; then
    printf '  Manager: %s\n' "$(proxy_installed_manager_label)"
  fi
  printf '  TCP:   %s:%s\n' "${PROXY_CONNECT_HOST}" "${TCP_PORT}"
  printf '  Serial:%s\n' " ${PORT}"
  printf '  Log:   %s\n' "${PROXY_LOG_FILE}"
}

proxy_log() {
  if proxy_service_installed; then
    proxy_service_log
    return 0
  fi

  ensure_runtime_dir
  touch "${PROXY_LOG_FILE}"
  tail -n 50 -f "${PROXY_LOG_FILE}"
}

target_debug() {
  local output_mode="${1:-}"

  run_in_venv python - "${ROOT_DIR}" "${PORT}" "${HOST}" "${TCP_PORT}" "${output_mode}" <<'PY'
import json
import sys

repo_root, port, host, tcp_port, output_mode = sys.argv[1:6]
sys.path.insert(0, repo_root)

from tools._meshtastic_common import explain_meshtastic_target

details = explain_meshtastic_target(port=port, host=host, tcp_port=int(tcp_port))
if output_mode == "--json":
  print(json.dumps(details, indent=2, sort_keys=True))
  raise SystemExit(0)

selected = details["selected"]
if output_mode == "--brief":
  print(f"label={selected['label']} mode={selected['mode']} source={selected['source']}")
  raise SystemExit(0)

print(f"Selected target: {selected['label']}")
print(f"  Mode:   {selected['mode']}")
print(f"  Source: {selected['source']}")
for check in details.get("checks", []):
  print(f"  Why:    {check}")

proxy_snapshot = details.get("proxy_snapshot")
if proxy_snapshot:
  print(f"  Proxy snapshot: present ({details['proxy_status_file']})")
else:
  print(f"  Proxy snapshot: absent ({details['proxy_status_file']})")

if "proxy_candidate" in details:
  candidate = details["proxy_candidate"]
  print(f"  Proxy candidate: {candidate['host']}:{candidate['tcp_port']}")
  print(f"  Proxy reachable: {details.get('proxy_reachable', False)}")

print(f"  Serial fallback: {details['serial_fallback']}")
PY
}

console_tui() {
  check_console_tool
  ensure_python_packages
  local host
  host="$(effective_host || true)"

  if [[ -n "${host}" ]]; then
    "${CONSOLE_TOOL}" --host "${host}" "$@"
  else
    MESHTASTIC_PORT="${PORT}" "${CONSOLE_TOOL}" "$@"
  fi
}

doctor() {
  require_direct_serial
  check_port
  echo "MESHTASTIC_PORT=${PORT}"
  echo "Resolved port: $(resolve_port)"
  if ! meshtastic_is_windows; then
    ls -l "${PORT}"
  fi
  echo
  echo "Serial aliases:"
  if meshtastic_is_linux; then
    ls -l /dev/serial/by-id 2>/dev/null || true
  elif meshtastic_is_macos; then
    ls -l /dev/tty.usb* 2>/dev/null || true
  else
    echo "  Alias listing is not available in this shell on Windows."
  fi
  echo
  echo "Processes using ${PORT}:"
  if meshtastic_is_linux && command -v fuser >/dev/null 2>&1; then
    fuser "${PORT}" || true
  else
    echo "  Process ownership check is only available on Linux with fuser."
  fi
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

  echo
  echo "PKC readiness:"
  run_in_venv python - "${ROOT_DIR}" "${PORT}" <<'PY'
import sys

repo_root, port = sys.argv[1:3]
sys.path.insert(0, repo_root)
sys.path.insert(0, f"{repo_root}/tools")

from google.protobuf.json_format import MessageToDict
from meshtastic.serial_interface import SerialInterface
from meshtastic.tcp_interface import TCPInterface

from _meshtastic_common import resolve_meshtastic_target


def connect_interface(target):
    if target.mode == "tcp":
        return TCPInterface(target.host, portNumber=target.tcp_port)
    return SerialInterface(target.serial_port)

target = resolve_meshtastic_target(port=port)
iface = connect_interface(target)
try:
    metadata = MessageToDict(iface.metadata)
    local_node = next((node for node in iface.nodes.values() if node.get("num") == iface.myInfo.my_node_num), {})
    local_user = local_node.get("user", {})
    peer_count = 0
    keyed_peers = 0
    for node in iface.nodes.values():
        user = node.get("user", {})
        if user.get("id") == local_user.get("id"):
            continue
        peer_count += 1
        if user.get("publicKey"):
            keyed_peers += 1
    print(f"  Node PKC capable: {'yes' if metadata.get('hasPKC') else 'no'}")
    print(f"  Local public key: {local_user.get('publicKey') or '-'}")
    print(f"  Known peers with public keys: {keyed_peers}/{peer_count}")
finally:
    iface.close()
PY
}

rawlog() {
  require_direct_serial
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
  echo "source ${VENV_ACTIVATE}"
}

main() {
  local command="${1:-help}"

  case "${command}" in
    bootstrap)
      bootstrap
      ;;
    flash)
      flash
      ;;
    provision)
      provision
      ;;
    guided)
      guided
      ;;
    probe)
      probe
      ;;
    nodes)
      nodes
      ;;
    nodedb-reset)
      nodedb_reset
      ;;
    contacts)
      shift
      contacts "$@"
      ;;
    export-config)
      export_config
      ;;
    set-name)
      shift
      set_name "$@"
      ;;
    set-role)
      shift
      set_role "$@"
      ;;
    set-region)
      shift
      set_region "$@"
      ;;
    set-modem-preset)
      shift
      set_modem_preset "$@"
      ;;
    set-position)
      shift
      set_position "$@"
      ;;
    clear-position)
      clear_position
      ;;
    set-ham)
      shift
      set_ham "$@"
      ;;
    set-wifi)
      shift
      set_wifi "$@"
      ;;
    status)
      shift
      status "$@"
      ;;
    telemetry)
      shift
      telemetry "$@"
      ;;
    monitor)
      shift
      monitor "$@"
      ;;
    messages)
      shift
      messages "$@"
      ;;
    protocol)
      shift
      protocol "$@"
      ;;
    plugins)
      shift
      plugins "$@"
      ;;
    proxy-start)
      proxy_start
      ;;
    proxy-stop)
      proxy_stop
      ;;
    proxy-autostart-install)
      shift
      proxy_autostart_install "$@"
      ;;
    proxy-autostart-remove)
      shift
      proxy_autostart_remove "$@"
      ;;
    proxy-autostart-status)
      shift
      proxy_autostart_status "$@"
      ;;
    proxy-status)
      shift
      proxy_status "$@"
      ;;
    proxy-log)
      proxy_log
      ;;
    target-debug)
      shift
      target_debug "$@"
      ;;
    proxy-check)
      shift
      proxy_check "$@"
      ;;
    console)
      shift
      console_tui "$@"
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
      echo "Unknown command: ${command}" >&2
      usage >&2
      exit 1
      ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
