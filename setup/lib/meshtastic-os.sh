#!/usr/bin/env bash

meshtastic_os_name() {
  case "$(uname -s)" in
    Linux)
      printf 'linux\n'
      ;;
    Darwin)
      printf 'macos\n'
      ;;
    MINGW*|MSYS*|CYGWIN*)
      printf 'windows\n'
      ;;
    *)
      printf 'unknown\n'
      ;;
  esac
}

meshtastic_is_linux() {
  [[ "$(meshtastic_os_name)" == "linux" ]]
}

meshtastic_is_macos() {
  [[ "$(meshtastic_os_name)" == "macos" ]]
}

meshtastic_is_windows() {
  [[ "$(meshtastic_os_name)" == "windows" ]]
}

meshtastic_default_serial_port() {
  local candidate

  if meshtastic_is_linux; then
    for candidate in /dev/ttyUSB0 /dev/ttyACM0; do
      if [[ -e "${candidate}" ]]; then
        printf '%s\n' "${candidate}"
        return 0
      fi
    done
    printf '/dev/ttyUSB0\n'
    return 0
  fi

  if meshtastic_is_macos; then
    for candidate in /dev/tty.usbserial* /dev/tty.usbmodem*; do
      if [[ -e "${candidate}" ]]; then
        printf '%s\n' "${candidate}"
        return 0
      fi
    done
    printf '/dev/tty.usbmodem1\n'
    return 0
  fi

  if meshtastic_is_windows; then
    printf 'COM3\n'
    return 0
  fi

  printf '/dev/ttyUSB0\n'
}

meshtastic_venv_python_path() {
  local venv_dir="${1}"

  if meshtastic_is_windows; then
    printf '%s/Scripts/python.exe\n' "${venv_dir}"
  else
    printf '%s/bin/python\n' "${venv_dir}"
  fi
}

meshtastic_venv_activate_path() {
  local venv_dir="${1}"

  if meshtastic_is_windows; then
    printf '%s/Scripts/activate\n' "${venv_dir}"
  else
    printf '%s/bin/activate\n' "${venv_dir}"
  fi
}

meshtastic_port_reference_is_valid() {
  local port="${1}"

  if meshtastic_is_windows; then
    [[ "${port}" =~ ^COM[0-9]+$ ]]
    return
  fi

  [[ -e "${port}" ]]
}

meshtastic_resolve_port() {
  local port="${1}"

  if meshtastic_is_windows; then
    printf '%s\n' "${port}"
    return 0
  fi

  if [[ -L "${port}" ]]; then
    readlink -f "${port}"
  else
    printf '%s\n' "${port}"
  fi
}
