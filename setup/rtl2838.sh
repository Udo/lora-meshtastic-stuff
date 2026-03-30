#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RTL_ROOT="${ROOT_DIR}/rtl2838"
VENDOR_DIR="${RTL_ROOT}/vendor"
SRC_DIR="${VENDOR_DIR}/rtl-sdr"
BUILD_DIR="${RTL_ROOT}/build/rtl-sdr"
RTL433_SRC_DIR="${VENDOR_DIR}/rtl_433"
RTL433_BUILD_DIR="${RTL_ROOT}/build/rtl_433"
ACARSDEC_SRC_DIR="${VENDOR_DIR}/acarsdec"
ACARSDEC_BUILD_DIR="${RTL_ROOT}/build/acarsdec"
REDSEA_SRC_DIR="${VENDOR_DIR}/redsea"
REDSEA_BUILD_DIR="${RTL_ROOT}/build/redsea"
VDLM2DEC_SRC_DIR="${VENDOR_DIR}/vdlm2dec"
VDLM2DEC_BUILD_DIR="${RTL_ROOT}/build/vdlm2dec"
APTDEC_SRC_DIR="${VENDOR_DIR}/aptdec"
APTDEC_BUILD_DIR="${RTL_ROOT}/build/aptdec"
GR_LORA_SRC_DIR="${VENDOR_DIR}/gr-lora"
GR_LORA_BUILD_DIR="${RTL_ROOT}/build/gr-lora"
LOCAL_DIR="${RTL_ROOT}/local"
BIN_DIR="${LOCAL_DIR}/bin"
LIB_DIR="${LOCAL_DIR}/lib"
LIB64_DIR="${LOCAL_DIR}/lib64"
LOG_DIR="${RTL_ROOT}/logs"
IQ_DIR="${RTL_ROOT}/captures"
REPORT_DIR="${RTL_ROOT}/reports"
DEFAULT_DEVICE="${RTL2838_DEVICE:-/dev/swradio0}"
DEFAULT_VENDOR_REMOTE="${RTL2838_VENDOR_REMOTE:-https://github.com/osmocom/rtl-sdr.git}"
DEFAULT_VENDOR_REF="${RTL2838_VENDOR_REF:-v2.0.2}"
DEFAULT_RTL433_VENDOR_REMOTE="${RTL2838_RTL433_VENDOR_REMOTE:-https://github.com/merbanan/rtl_433.git}"
DEFAULT_RTL433_VENDOR_REF="${RTL2838_RTL433_VENDOR_REF:-25.02}"
DEFAULT_ACARSDEC_VENDOR_REMOTE="${RTL2838_ACARSDEC_VENDOR_REMOTE:-https://github.com/TLeconte/acarsdec.git}"
DEFAULT_ACARSDEC_VENDOR_REF="${RTL2838_ACARSDEC_VENDOR_REF:-acarsdec-3.7}"
DEFAULT_REDSEA_VENDOR_REMOTE="${RTL2838_REDSEA_VENDOR_REMOTE:-https://github.com/windytan/redsea.git}"
DEFAULT_REDSEA_VENDOR_REF="${RTL2838_REDSEA_VENDOR_REF:-master}"
DEFAULT_VDLM2DEC_VENDOR_REMOTE="${RTL2838_VDLM2DEC_VENDOR_REMOTE:-https://github.com/TLeconte/vdlm2dec.git}"
DEFAULT_VDLM2DEC_VENDOR_REF="${RTL2838_VDLM2DEC_VENDOR_REF:-master}"
DEFAULT_APTDEC_VENDOR_REMOTE="${RTL2838_APTDEC_VENDOR_REMOTE:-https://github.com/Xerbo/aptdec.git}"
DEFAULT_APTDEC_VENDOR_REF="${RTL2838_APTDEC_VENDOR_REF:-master}"
DEFAULT_GR_LORA_VENDOR_REMOTE="${RTL2838_GR_LORA_VENDOR_REMOTE:-https://github.com/rpp0/gr-lora.git}"
DEFAULT_GR_LORA_VENDOR_REF="${RTL2838_GR_LORA_VENDOR_REF:-master}"
DEFAULT_SAMPLE_RATE="${RTL2838_SAMPLE_RATE:-3200000}"
DEFAULT_BANDWIDTH="${RTL2838_BANDWIDTH:-0}"
DEFAULT_GAIN="${RTL2838_GAIN:-0}"
DEFAULT_FM_RATE="${RTL2838_FM_RATE:-170k}"
DEFAULT_FM_OUTPUT_RATE="${RTL2838_FM_OUTPUT_RATE:-48k}"
MONITOR_TOOL="${ROOT_DIR}/tools/rtl2838_monitor.py"
LIVE_WATERFALL_TOOL="${ROOT_DIR}/tools/rtl2838_live_waterfall.py"
ADSB_MONITOR_TOOL="${ROOT_DIR}/tools/rf_adsb_monitor.py"
RTL433_MONITOR_TOOL="${ROOT_DIR}/tools/rf_rtl433_monitor.py"
APRS_MONITOR_TOOL="${ROOT_DIR}/tools/rf_aprs_monitor.py"
ACARS_MONITOR_TOOL="${ROOT_DIR}/tools/rf_acars_monitor.py"
AIS_MONITOR_TOOL="${ROOT_DIR}/tools/rf_ais_monitor.py"
WEATHER_ALERT_MONITOR_TOOL="${ROOT_DIR}/tools/rf_weather_alert_monitor.py"
RDS_MONITOR_TOOL="${ROOT_DIR}/tools/rf_rds_monitor.py"
VDL2_MONITOR_TOOL="${ROOT_DIR}/tools/rf_vdl2_monitor.py"
PAGER_MONITOR_TOOL="${ROOT_DIR}/tools/rf_pager_monitor.py"
NOAA_APT_CAPTURE_TOOL="${ROOT_DIR}/tools/rf_noaa_apt_capture.py"
LORA_MONITOR_TOOL="${ROOT_DIR}/tools/rf_lora_monitor.py"
RF_COMMON_TOOL="${ROOT_DIR}/tools/_rf_monitor_common.py"

if [[ -t 1 ]]; then
  COLOR_RESET=$'\033[0m'
  COLOR_BOLD=$'\033[1m'
  COLOR_RED=$'\033[31m'
  COLOR_GREEN=$'\033[32m'
  COLOR_YELLOW=$'\033[33m'
  COLOR_BLUE=$'\033[34m'
  COLOR_CYAN=$'\033[36m'
else
  COLOR_RESET=''
  COLOR_BOLD=''
  COLOR_RED=''
  COLOR_GREEN=''
  COLOR_YELLOW=''
  COLOR_BLUE=''
  COLOR_CYAN=''
fi

usage() {
  cat <<'EOF'
Usage: setup/rtl2838.sh <command> [args]

Commands:
  info
      Print detected USB, V4L2 SDR, and local-tool status.

  bootstrap
      Vendor a pinned rtl-sdr source tree under rtl2838/vendor/rtl-sdr,
      vendor a pinned rtl_433 source tree under rtl2838/vendor/rtl_433,
      vendor a pinned acarsdec source tree under rtl2838/vendor/acarsdec,
      vendor pinned redsea, vdlm2dec, aptdec, and gr-lora source trees,
      install all host prerequisites needed by the shipped tools, build them
      locally, and install the tools under rtl2838/local/bin.

  probe-kernel
      Show the kernel SDR driver state via v4l2-ctl.

  use-kernel
      Switch the dongle into kernel/V4L2 SDR mode, prompting for sudo if needed.

  use-libusb
      Switch the dongle into libusb mode for rtl_test/rtl_sdr/rtl_fm/readsb
      and the other direct SDR decoder tools,
      prompting for sudo if needed.

  release-libusb
      Print the exact commands needed to free the dongle from the kernel
      DVB/V4L2 driver so rtl_test/rtl_sdr/rtl_fm can claim it via libusb.

  capture-v4l2 <freq_hz> [seconds] [output_file]
      Tune the kernel SDR interface and capture raw IQ (CU8 interleaved I/Q)
      to rtl2838/captures/.

  analyze <capture_file> <center_hz> [sample_rate] [output_prefix] [profile]
      Analyze a raw CU8 capture and render a PSD/waterfall PNG plus JSON summary.

  eu868-demo [seconds]
      Capture two practical EU868 monitoring windows and render a markdown report
      under rtl2838/reports/.

  live-waterfall [center_hz] [profile] [sample_rate] [charset]
      Start a realtime console waterfall directly from the kernel SDR stream.

  eu868-live [wide|low|high|<center_hz>] [sample_rate] [charset]
      Start a realtime console waterfall with practical EU868 defaults.

  preset-live [preset] [fps] [charset]
      Start a realtime console waterfall using a named preset such as
      max-span, eu868-wide, am-broadcast, shortwave-49m, weather, marine-vhf,
      cb-27mhz, ham-2m, ism433, pmr446, frs-gmrs, airband, adsb1090,
      adsb-monitor, aprs-monitor, ais-monitor, acars-monitor, rds-monitor,
      vdl2-monitor, pager-monitor, noaa-apt-monitor, lora-monitor,
      weather-alert-monitor, rtl433-433, rtl433-868, rtl433-915,
      fm-broadcast, or broadband-868.

  adsb-monitor [args...]
      Run a live ADS-B / Mode S monitor at 1090 MHz using readsb.
      Aliases: rf_adsb_monitor, rf-adsb-monitor

  rtl433-monitor [preset] [args...]
      Run a live rtl_433 JSON monitor for consumer RF sensors and simple ISM devices.
      Aliases: rf_rtl433_monitor, rf-rtl433-monitor

  aprs-monitor [args...]
      Run a live APRS monitor around 144.800 MHz using rtl_fm and multimon-ng.
      Aliases: rf_aprs_monitor, rf-aprs-monitor

  acars-monitor [args...]
      Run a live ACARS monitor using acarsdec JSON output.
      Aliases: rf_acars_monitor, rf-acars-monitor

  ais-monitor [args...]
      Run a live AIS monitor using rtl_ais.
      Aliases: rf_ais_monitor, rf-ais-monitor

  weather-alert-monitor [args...]
      Run a live SAME/EAS weather alert monitor using rtl_fm and multimon-ng.
      Aliases: rf_weather_alert_monitor, rf-weather-alert-monitor

  rds-monitor [args...]
      Run a live FM RDS monitor using rtl_fm and redsea.
      Aliases: rf_rds_monitor, rf-rds-monitor

  vdl2-monitor [args...]
      Run a live VDL2 aviation monitor using vdlm2dec JSON output.
      Aliases: rf_vdl2_monitor, rf-vdl2-monitor

  pager-monitor [args...]
      Run a live pager monitor using rtl_fm and multimon-ng POCSAG decoders.
      Aliases: rf_pager_monitor, rf-pager-monitor

  noaa-apt-capture [args...]
      Capture a NOAA APT weather satellite pass to WAV and decode it into PNG.
      Alias: rf_noaa_apt_capture

  lora-monitor [args...]
      Capture a LoRa channel and decode annotated raw hex dumps with gr-lora.
      Aliases: rf_lora_monitor, rf-lora-monitor

  rtl-test [args...]
      Run the locally built rtl_test binary.

  rtl-sdr <freq_hz> [seconds] [output_file]
      Run the locally built rtl_sdr binary and write raw IQ samples.

  fm <freq_hz> [seconds] [output_wav]
      Receive narrow/wide FM audio with rtl_fm and save a WAV file using sox.
      Requires local rtl-sdr tools and a system sox binary.

  doctor
      Print common diagnostics and hints for permissions, dependencies, and
      current device visibility.

Environment:
  RTL2838_DEVICE       SDR V4L2 device path (default: /dev/swradio0)
  RTL2838_PRESET       Named live-waterfall preset (default: eu868-wide)
  RTL2838_VENDOR_REMOTE Upstream rtl-sdr git remote
  RTL2838_VENDOR_REF   Upstream tag/branch/commit to build (default: v2.0.2)
  RTL2838_RTL433_VENDOR_REMOTE Upstream rtl_433 git remote
  RTL2838_RTL433_VENDOR_REF Upstream tag/branch/commit to build (default: 25.02)
  RTL2838_ACARSDEC_VENDOR_REMOTE Upstream acarsdec git remote
  RTL2838_ACARSDEC_VENDOR_REF Upstream tag/branch/commit to build (default: acarsdec-3.7)
  RTL2838_SAMPLE_RATE  Default IQ sample rate for captures/live view (default: 3200000)
  RTL2838_FPS          Target live-waterfall UI refresh rate (default: 12.0)
  RTL2838_CHARSET      Live-waterfall glyph preset or custom string (default: blocks)
  RTL2838_NORM_MODE    Live-waterfall normalization mode: slow or row (default: slow)
  RTL2838_NORM_ALPHA   Live-waterfall slow normalization update rate (default: 0.08)
  RTL2838_NORM_MIN_RANGE_DB Minimum live normalization range in dB (default: 18.0)
  RTL2838_NORM_START_HEADROOM_DB Extra startup headroom for slow normalization (default: 8.0)
  RTL2838_AVG_FRAMES   FFT frames averaged per rendered row (default: 3)
  RTL2838_BANDWIDTH    Optional SDR bandwidth control for kernel capture
  RTL2838_GAIN         Default rtl_sdr gain; 0 uses automatic gain
EOF
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

die() {
  print_error "$*"
  exit 1
}

require_command() {
  local name="${1}"
  command -v "${name}" >/dev/null 2>&1 || die "Required command not found: ${name}"
}

apt_available() {
  command -v apt-get >/dev/null 2>&1
}

ensure_apt_packages() {
  local missing=()
  local pkg

  for pkg in "$@"; do
    if ! dpkg -s "${pkg}" >/dev/null 2>&1; then
      missing+=("${pkg}")
    fi
  done

  if [[ "${#missing[@]}" -eq 0 ]]; then
    return 0
  fi

  apt_available || die "Missing required packages: ${missing[*]}. Install them manually on this host."
  print_info "Installing missing system packages: ${missing[*]}. sudo may prompt for your password."
  run_with_sudo apt-get update
  run_with_sudo apt-get install -y "${missing[@]}"
}

ensure_command_or_apt() {
  local command_name="${1}"
  shift

  if command -v "${command_name}" >/dev/null 2>&1; then
    return 0
  fi

  [[ "$#" -gt 0 ]] || die "Required command not found: ${command_name}"
  ensure_apt_packages "$@"
  command -v "${command_name}" >/dev/null 2>&1 || die "Required command not found after install: ${command_name}"
}

run_with_sudo() {
  if [[ "${EUID}" -eq 0 ]]; then
    "$@"
    return
  fi

  require_command sudo
  sudo "$@"
}

require_pkg_config_module() {
  local module="${1}"
  local apt_hint="${2:-}"

  if pkg-config --exists "${module}"; then
    return 0
  fi

  if [[ -n "${apt_hint}" ]]; then
    ensure_apt_packages "${apt_hint}"
    pkg-config --exists "${module}" && return 0
    die "Missing development package for ${module} even after installing ${apt_hint}"
  fi

  die "Missing development package for ${module}"
}

require_kernel_device() {
  [[ -e "${DEFAULT_DEVICE}" ]] || die "Missing SDR device: ${DEFAULT_DEVICE}"
}

ensure_layout() {
  mkdir -p "${RTL_ROOT}" "${VENDOR_DIR}" "${BUILD_DIR}" "${RTL433_BUILD_DIR}" "${ACARSDEC_BUILD_DIR}" "${LOCAL_DIR}" "${LOG_DIR}" "${IQ_DIR}" "${REPORT_DIR}"
}

default_capture_path() {
  local prefix="${1}"
  local ext="${2}"
  printf '%s/%s-%s.%s\n' "${IQ_DIR}" "${prefix}" "$(date +%Y%m%d-%H%M%S)" "${ext}"
}

hz_to_mhz() {
  local freq_hz="${1}"
  printf '%d.%06d\n' "$(( freq_hz / 1000000 ))" "$(( freq_hz % 1000000 ))"
}

have_local_tool() {
  [[ -x "${BIN_DIR}/${1}" ]]
}

rtl2838_usb_sysfs_device() {
  rf_common_query usb-sysfs-device
}

rtl2838_kernel_bound_interface() {
  rf_common_query kernel-bound-interface >/dev/null
}

local_library_path() {
  local parts=()

  [[ -d "${LIB_DIR}" ]] && parts+=("${LIB_DIR}")
  [[ -d "${LIB64_DIR}" ]] && parts+=("${LIB64_DIR}")

  if [[ -n "${LD_LIBRARY_PATH:-}" ]]; then
    parts+=("${LD_LIBRARY_PATH}")
  fi

  local IFS=':'
  printf '%s\n' "${parts[*]}"
}

run_python_tool() {
  local tool="${1}"
  shift
  require_command python3
  python3 "${tool}" "$@"
}

is_help_request() {
  case "${1:-}" in
    -h|--help)
      return 0
      ;;
  esac
  return 1
}

rf_common_query() {
  run_python_tool "${RF_COMMON_TOOL}" "$@"
}

resolve_binary_prefer_local() {
  local local_path="${1}"
  local fallback_name="${2}"

  if [[ -x "${local_path}" ]]; then
    printf '%s\n' "${local_path}"
    return 0
  fi

  if command -v "${fallback_name}" >/dev/null 2>&1; then
    printf '%s\n' "${fallback_name}"
    return 0
  fi

  return 1
}

run_local_tool() {
  local tool="${1}"
  shift
  have_local_tool "${tool}" || die "Missing ${tool}. Run ./setup/rtl2838.sh bootstrap first."
  case "${tool}" in
    rtl_test|rtl_sdr|rtl_fm|rtl_adsb)
      switch_to_libusb
      ;;
  esac
  LD_LIBRARY_PATH="$(local_library_path)" "${BIN_DIR}/${tool}" "$@"
}

detect_usb() {
  lsusb | grep -i '0bda:2838\|rtl2838\|rtl2832' || true
}

switch_to_libusb() {
  local dev if0 if_name

  if ! rtl2838_kernel_bound_interface; then
    return 0
  fi

  dev="$(rtl2838_usb_sysfs_device || true)"
  [[ -n "${dev}" ]] || die "RTL2838 USB device not found in /sys/bus/usb/devices"
  if0="${dev}:1.0"
  if_name="$(basename "${if0}")"

  print_info "Switching ${if_name} to libusb mode. sudo may prompt for your password."
  run_with_sudo modprobe -r rtl2832_sdr dvb_usb_rtl28xxu rtl2832 dvb_usb_v2 || true
  if [[ -e "/sys/bus/usb/drivers/dvb_usb_rtl28xxu/unbind" ]]; then
    run_with_sudo sh -c "echo '${if_name}' > /sys/bus/usb/drivers/dvb_usb_rtl28xxu/unbind" || true
  fi
  sleep 1

  if rtl2838_kernel_bound_interface; then
    die "Failed to switch ${if_name} to libusb mode"
  fi

  print_success "Dongle is now in libusb mode"
}

switch_to_kernel() {
  local dev if0 if_name dev_name

  dev="$(rtl2838_usb_sysfs_device || true)"
  [[ -n "${dev}" ]] || die "RTL2838 USB device not found in /sys/bus/usb/devices"
  if0="${dev}:1.0"
  if_name="$(basename "${if0}")"
  dev_name="$(basename "${dev}")"

  if [[ -e "${DEFAULT_DEVICE}" ]]; then
    print_success "Dongle is already in kernel/V4L2 mode"
    return 0
  fi

  print_info "Switching ${if_name} to kernel/V4L2 mode. sudo may prompt for your password."
  run_with_sudo modprobe dvb_usb_v2 rtl2832 dvb_usb_rtl28xxu rtl2832_sdr
  if [[ -e "${DEFAULT_DEVICE}" ]]; then
    print_success "Dongle is now in kernel/V4L2 mode"
    return 0
  fi
  if [[ -e "/sys/bus/usb/drivers_probe" ]]; then
    run_with_sudo sh -c "echo '${if_name}' > /sys/bus/usb/drivers_probe" || true
    run_with_sudo sh -c "echo '${dev_name}' > /sys/bus/usb/drivers_probe" || true
  fi
  if [[ -e "/sys/bus/usb/drivers/usb/unbind" && -e "/sys/bus/usb/drivers/usb/bind" ]]; then
    run_with_sudo sh -c "echo '${dev_name}' > /sys/bus/usb/drivers/usb/unbind" || true
    sleep 1
    run_with_sudo sh -c "echo '${dev_name}' > /sys/bus/usb/drivers/usb/bind" || true
  fi
  if ! rtl2838_kernel_bound_interface && [[ -e "/sys/bus/usb/drivers/dvb_usb_rtl28xxu/bind" ]]; then
    run_with_sudo sh -c "echo '${if_name}' > /sys/bus/usb/drivers/dvb_usb_rtl28xxu/bind" || true
  fi
  sleep 1

  if [[ -e "${DEFAULT_DEVICE}" ]]; then
    print_success "Dongle is now in kernel/V4L2 mode"
    return 0
  fi

  if ! rtl2838_kernel_bound_interface; then
    die "Failed to switch ${if_name} to kernel mode"
  fi

  print_success "Dongle is now in kernel/V4L2 mode"
}

detect_v4l2() {
  if ! command -v v4l2-ctl >/dev/null 2>&1; then
    print_warn "v4l2-ctl is not installed."
    return 0
  fi

  if [[ ! -e "${DEFAULT_DEVICE}" ]]; then
    print_warn "SDR device not found at ${DEFAULT_DEVICE}"
    return 0
  fi

  v4l2-ctl -d "${DEFAULT_DEVICE}" --all
}

vendor_rtl_sdr() {
  require_command git
  ensure_layout

  if [[ ! -d "${SRC_DIR}/.git" ]]; then
    print_info "Cloning rtl-sdr ${DEFAULT_VENDOR_REF} into ${SRC_DIR}"
    git clone --branch "${DEFAULT_VENDOR_REF}" --depth 1 "${DEFAULT_VENDOR_REMOTE}" "${SRC_DIR}"
    return 0
  fi

  print_info "Refreshing existing rtl-sdr checkout in ${SRC_DIR}"
  git -C "${SRC_DIR}" fetch --tags --depth 1 origin "${DEFAULT_VENDOR_REF}"
  git -C "${SRC_DIR}" checkout --force FETCH_HEAD
}

vendor_rtl433() {
  require_command git
  ensure_layout

  if [[ ! -d "${RTL433_SRC_DIR}/.git" ]]; then
    print_info "Cloning rtl_433 into ${RTL433_SRC_DIR}"
    git clone --depth 1 --branch "${DEFAULT_RTL433_VENDOR_REF}" "${DEFAULT_RTL433_VENDOR_REMOTE}" "${RTL433_SRC_DIR}"
    return
  fi

  print_info "Refreshing rtl_433 checkout in ${RTL433_SRC_DIR}"
  git -C "${RTL433_SRC_DIR}" fetch --depth 1 origin "${DEFAULT_RTL433_VENDOR_REF}"
  git -C "${RTL433_SRC_DIR}" checkout --force FETCH_HEAD
}

vendor_acarsdec() {
  require_command git
  ensure_layout

  if [[ ! -d "${ACARSDEC_SRC_DIR}/.git" ]]; then
    print_info "Cloning acarsdec into ${ACARSDEC_SRC_DIR}"
    git clone --depth 1 --branch "${DEFAULT_ACARSDEC_VENDOR_REF}" "${DEFAULT_ACARSDEC_VENDOR_REMOTE}" "${ACARSDEC_SRC_DIR}"
    return
  fi

  print_info "Refreshing acarsdec checkout in ${ACARSDEC_SRC_DIR}"
  git -C "${ACARSDEC_SRC_DIR}" fetch --depth 1 origin "${DEFAULT_ACARSDEC_VENDOR_REF}"
  git -C "${ACARSDEC_SRC_DIR}" checkout --force FETCH_HEAD
}

vendor_redsea() {
  require_command git
  ensure_layout

  if [[ ! -d "${REDSEA_SRC_DIR}/.git" ]]; then
    print_info "Cloning redsea into ${REDSEA_SRC_DIR}"
    git clone --depth 1 --branch "${DEFAULT_REDSEA_VENDOR_REF}" "${DEFAULT_REDSEA_VENDOR_REMOTE}" "${REDSEA_SRC_DIR}"
    return
  fi

  print_info "Refreshing redsea checkout in ${REDSEA_SRC_DIR}"
  git -C "${REDSEA_SRC_DIR}" fetch --depth 1 origin "${DEFAULT_REDSEA_VENDOR_REF}"
  git -C "${REDSEA_SRC_DIR}" checkout --force FETCH_HEAD
}

vendor_vdlm2dec() {
  require_command git
  ensure_layout

  if [[ ! -d "${VDLM2DEC_SRC_DIR}/.git" ]]; then
    print_info "Cloning vdlm2dec into ${VDLM2DEC_SRC_DIR}"
    git clone --depth 1 --branch "${DEFAULT_VDLM2DEC_VENDOR_REF}" "${DEFAULT_VDLM2DEC_VENDOR_REMOTE}" "${VDLM2DEC_SRC_DIR}"
    return
  fi

  print_info "Refreshing vdlm2dec checkout in ${VDLM2DEC_SRC_DIR}"
  git -C "${VDLM2DEC_SRC_DIR}" fetch --depth 1 origin "${DEFAULT_VDLM2DEC_VENDOR_REF}"
  git -C "${VDLM2DEC_SRC_DIR}" checkout --force FETCH_HEAD
}

vendor_aptdec() {
  require_command git
  ensure_layout

  if [[ ! -d "${APTDEC_SRC_DIR}/.git" ]]; then
    print_info "Cloning aptdec into ${APTDEC_SRC_DIR}"
    git clone --depth 1 --branch "${DEFAULT_APTDEC_VENDOR_REF}" "${DEFAULT_APTDEC_VENDOR_REMOTE}" "${APTDEC_SRC_DIR}"
    return
  fi

  print_info "Refreshing aptdec checkout in ${APTDEC_SRC_DIR}"
  git -C "${APTDEC_SRC_DIR}" fetch --depth 1 origin "${DEFAULT_APTDEC_VENDOR_REF}"
  git -C "${APTDEC_SRC_DIR}" checkout --force FETCH_HEAD
}

vendor_gr_lora() {
  require_command git
  ensure_layout

  if [[ ! -d "${GR_LORA_SRC_DIR}/.git" ]]; then
    print_info "Cloning gr-lora into ${GR_LORA_SRC_DIR}"
    git clone --depth 1 --branch "${DEFAULT_GR_LORA_VENDOR_REF}" "${DEFAULT_GR_LORA_VENDOR_REMOTE}" "${GR_LORA_SRC_DIR}"
    return
  fi

  print_info "Refreshing gr-lora checkout in ${GR_LORA_SRC_DIR}"
  git -C "${GR_LORA_SRC_DIR}" fetch --depth 1 origin "${DEFAULT_GR_LORA_VENDOR_REF}"
  git -C "${GR_LORA_SRC_DIR}" checkout --force FETCH_HEAD
}

build_rtl_sdr() {
  require_command cmake
  require_command make
  require_command pkg-config
  require_pkg_config_module libusb-1.0 "libusb-1.0-0-dev"
  ensure_layout

  print_info "Configuring rtl-sdr local build"
  cmake -S "${SRC_DIR}" -B "${BUILD_DIR}" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="${LOCAL_DIR}" \
    -DINSTALL_UDEV_RULES=OFF \
    -DDETACH_KERNEL_DRIVER=ON

  print_info "Building rtl-sdr"
  cmake --build "${BUILD_DIR}" --parallel

  print_info "Installing rtl-sdr into ${LOCAL_DIR}"
  cmake --install "${BUILD_DIR}"
}

build_rtl433() {
  require_command cmake
  require_command pkg-config
  ensure_layout

  if ! have_local_tool rtl_test; then
    die "Missing repo-local rtl-sdr tools. Run ./setup/rtl2838.sh bootstrap or build rtl-sdr first."
  fi

  print_info "Configuring rtl_433 local build"
  PKG_CONFIG_PATH="${LOCAL_DIR}/lib/pkgconfig:${LOCAL_DIR}/lib64/pkgconfig:${PKG_CONFIG_PATH:-}" \
  cmake -S "${RTL433_SRC_DIR}" -B "${RTL433_BUILD_DIR}" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="${LOCAL_DIR}" \
    -DCMAKE_PREFIX_PATH="${LOCAL_DIR}" \
    -DCMAKE_INSTALL_RPATH="${LIB_DIR};${LIB64_DIR}" \
    -DENABLE_RTLSDR=ON \
    -DENABLE_SOAPYSDR=OFF \
    -DENABLE_OPENSSL=OFF \
    -DENABLE_IPV6=ON \
    -DBUILD_DOCUMENTATION=OFF \
    -DBUILD_TESTING_ANALYZER=OFF

  print_info "Building rtl_433"
  cmake --build "${RTL433_BUILD_DIR}" --parallel

  print_info "Installing rtl_433 into ${LOCAL_DIR}"
  cmake --install "${RTL433_BUILD_DIR}"
}

build_acarsdec() {
  require_command cmake
  ensure_layout

  if [[ ! -x "${BIN_DIR}/rtl_fm" ]]; then
    die "Missing repo-local rtl-sdr tools. Run ./setup/rtl2838.sh bootstrap or build rtl-sdr first."
  fi

  print_info "Configuring acarsdec local build"
  cmake -S "${ACARSDEC_SRC_DIR}" -B "${ACARSDEC_BUILD_DIR}" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="${LOCAL_DIR}" \
    -DCMAKE_PREFIX_PATH="${LOCAL_DIR}" \
    -DCMAKE_INCLUDE_PATH="${LOCAL_DIR}/include" \
    -DCMAKE_LIBRARY_PATH="${LIB_DIR};${LIB64_DIR}" \
    -DCMAKE_INSTALL_RPATH="${LIB_DIR};${LIB64_DIR}" \
    -DCMAKE_C_FLAGS="-I${LOCAL_DIR}/include" \
    -Drtl=ON \
    -Dairspy=OFF \
    -Dsdrplay=OFF

  print_info "Building acarsdec"
  cmake --build "${ACARSDEC_BUILD_DIR}" --parallel

  print_info "Installing acarsdec into ${LOCAL_DIR}"
  cmake --install "${ACARSDEC_BUILD_DIR}"
}

build_redsea() {
  require_command meson
  require_command ninja
  ensure_layout

  print_info "Configuring redsea local build"
  if [[ -f "${REDSEA_BUILD_DIR}/build.ninja" ]]; then
    meson setup "${REDSEA_BUILD_DIR}" "${REDSEA_SRC_DIR}" --prefix "${LOCAL_DIR}" --buildtype release --libdir lib --reconfigure
  else
    meson setup "${REDSEA_BUILD_DIR}" "${REDSEA_SRC_DIR}" --prefix "${LOCAL_DIR}" --buildtype release --libdir lib
  fi

  print_info "Building redsea"
  meson compile -C "${REDSEA_BUILD_DIR}"

  print_info "Installing redsea into ${LOCAL_DIR}"
  meson install -C "${REDSEA_BUILD_DIR}"
}

build_vdlm2dec() {
  require_command cmake
  ensure_layout

  if [[ ! -x "${BIN_DIR}/rtl_fm" ]]; then
    die "Missing repo-local rtl-sdr tools. Run ./setup/rtl2838.sh bootstrap or build rtl-sdr first."
  fi

  print_info "Configuring vdlm2dec local build"
  cmake -S "${VDLM2DEC_SRC_DIR}" -B "${VDLM2DEC_BUILD_DIR}" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="${LOCAL_DIR}" \
    -DCMAKE_PREFIX_PATH="${LOCAL_DIR}" \
    -DCMAKE_INCLUDE_PATH="${LOCAL_DIR}/include" \
    -DCMAKE_LIBRARY_PATH="${LIB_DIR};${LIB64_DIR}" \
    -DCMAKE_INSTALL_RPATH="${LIB_DIR};${LIB64_DIR}" \
    -DCMAKE_C_FLAGS="-I${LOCAL_DIR}/include" \
    -Drtl=ON \
    -Dairspy=OFF

  print_info "Building vdlm2dec"
  cmake --build "${VDLM2DEC_BUILD_DIR}" --parallel

  print_info "Installing vdlm2dec into ${LOCAL_DIR}"
  cmake --install "${VDLM2DEC_BUILD_DIR}"
}

build_aptdec() {
  require_command cmake
  ensure_layout

  print_info "Configuring aptdec local build"
  cmake -S "${APTDEC_SRC_DIR}" -B "${APTDEC_BUILD_DIR}" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="${LOCAL_DIR}" \
    -DCMAKE_PREFIX_PATH="${LOCAL_DIR}" \
    -DCMAKE_INSTALL_RPATH="${LIB_DIR};${LIB64_DIR}"

  print_info "Building aptdec"
  cmake --build "${APTDEC_BUILD_DIR}" --parallel

  print_info "Installing aptdec into ${LOCAL_DIR}"
  cmake --install "${APTDEC_BUILD_DIR}"
}

build_gr_lora() {
  require_command cmake
  ensure_layout

  print_info "Configuring gr-lora local build"
  cmake -S "${GR_LORA_SRC_DIR}" -B "${GR_LORA_BUILD_DIR}" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="${LOCAL_DIR}" \
    -DCMAKE_PREFIX_PATH="${LOCAL_DIR}" \
    -DCMAKE_INSTALL_RPATH="${LIB_DIR};${LIB64_DIR}" \
    -DPYTHON_EXECUTABLE="$(command -v python3)" \
    -DENABLE_DOXYGEN=OFF

  print_info "Building gr-lora"
  cmake --build "${GR_LORA_BUILD_DIR}" --parallel

  print_info "Installing gr-lora into ${LOCAL_DIR}"
  cmake --install "${GR_LORA_BUILD_DIR}"
}

bootstrap() {
  ensure_apt_packages \
    git cmake make gcc g++ pkg-config \
    libusb-1.0-0-dev v4l-utils usbutils sox \
    multimon-ng rtl-ais readsb direwolf \
    meson ninja-build libsndfile1-dev libliquid-dev nlohmann-json3-dev libpng-dev \
    gnuradio gnuradio-dev gr-osmosdr swig libcppunit-dev python3-numpy python3-scipy
  require_command lsusb
  require_command v4l2-ctl
  vendor_rtl_sdr
  build_rtl_sdr
  vendor_rtl433
  build_rtl433
  vendor_acarsdec
  build_acarsdec
  vendor_redsea
  build_redsea
  vendor_vdlm2dec
  build_vdlm2dec
  vendor_aptdec
  build_aptdec
  vendor_gr_lora
  build_gr_lora
  print_success "Local rtl-sdr, rtl_433, acarsdec, redsea, vdlm2dec, aptdec, and gr-lora support installed under ${LOCAL_DIR}"
  print_success "Next steps:"
  printf '  %s\n' "./setup/rtl2838.sh info"
  printf '  %s\n' "./setup/rtl2838.sh adsb-monitor"
  printf '  %s\n' "./setup/rtl2838.sh rds-monitor"
  printf '  %s\n' "./setup/rtl2838.sh vdl2-monitor"
  printf '  %s\n' "./setup/rtl2838.sh pager-monitor"
  printf '  %s\n' "./setup/rtl2838.sh rtl433-monitor"
  printf '  %s\n' "./setup/rtl2838.sh noaa-apt-capture --seconds 120"
  printf '  %s\n' "./setup/rtl2838.sh lora-monitor --seconds 5"
}

info() {
  ensure_layout
  printf '%sRTL2838 local workflow%s\n' "${COLOR_BOLD}${COLOR_CYAN}" "${COLOR_RESET}"
  printf 'Root: %s\n' "${RTL_ROOT}"
  printf 'Device: %s\n' "${DEFAULT_DEVICE}"
  printf 'Pinned rtl-sdr ref: %s\n' "${DEFAULT_VENDOR_REF}"
  printf 'Pinned rtl_433 ref: %s\n' "${DEFAULT_RTL433_VENDOR_REF}"
  printf 'Pinned acarsdec ref: %s\n' "${DEFAULT_ACARSDEC_VENDOR_REF}"
  printf 'Pinned redsea ref: %s\n' "${DEFAULT_REDSEA_VENDOR_REF}"
  printf 'Pinned vdlm2dec ref: %s\n' "${DEFAULT_VDLM2DEC_VENDOR_REF}"
  printf 'Pinned aptdec ref: %s\n' "${DEFAULT_APTDEC_VENDOR_REF}"
  printf 'Pinned gr-lora ref: %s\n' "${DEFAULT_GR_LORA_VENDOR_REF}"
  printf '\nUSB:\n'
  detect_usb || true
  printf '\nKernel SDR interface:\n'
  detect_v4l2 || true
  printf '\nLocal tools:\n'
  for tool in rtl_test rtl_sdr rtl_fm rtl_adsb rtl_433 acarsdec redsea vdlm2dec aptdec; do
    if have_local_tool "${tool}"; then
      printf '  %s%s%s -> %s\n' "${COLOR_GREEN}" "${tool}" "${COLOR_RESET}" "${BIN_DIR}/${tool}"
    else
      printf '  %s%s%s -> missing\n' "${COLOR_YELLOW}" "${tool}" "${COLOR_RESET}"
    fi
  done
  if compgen -G "${LOCAL_DIR}/lib/python*/dist-packages/lora*" >/dev/null || \
     compgen -G "${LOCAL_DIR}/lib/python*/site-packages/lora*" >/dev/null || \
     compgen -G "${LOCAL_DIR}/lib64/python*/dist-packages/lora*" >/dev/null || \
     compgen -G "${LOCAL_DIR}/lib64/python*/site-packages/lora*" >/dev/null; then
    printf '  %s%s%s -> %s\n' "${COLOR_GREEN}" "gr-lora" "${COLOR_RESET}" "python module present"
  else
    printf '  %s%s%s -> missing\n' "${COLOR_YELLOW}" "gr-lora" "${COLOR_RESET}"
  fi
  printf '\nSystem decoder tools:\n'
  for tool in readsb direwolf multimon-ng rtl_ais sox; do
    if command -v "${tool}" >/dev/null 2>&1; then
      printf '  %s%s%s -> %s\n' "${COLOR_GREEN}" "${tool}" "${COLOR_RESET}" "$(command -v "${tool}")"
    else
      printf '  %s%s%s -> missing\n' "${COLOR_YELLOW}" "${tool}" "${COLOR_RESET}"
    fi
  done
  if [[ -d "${LIB_DIR}" || -d "${LIB64_DIR}" ]]; then
    printf '\nLocal runtime library path:\n'
    printf '  %s\n' "$(local_library_path)"
  fi
}

probe_kernel() {
  require_command v4l2-ctl
  require_kernel_device
  v4l2-ctl -d "${DEFAULT_DEVICE}" --all
  printf '\n'
  v4l2-ctl -d "${DEFAULT_DEVICE}" --list-freq-bands || true
}

release_libusb() {
  local dev if0 if_name

  dev="$(rtl2838_usb_sysfs_device || true)"
  [[ -n "${dev}" ]] || die "RTL2838 USB device not found in /sys/bus/usb/devices"
  if0="${dev}:1.0"
  if_name="$(basename "${if0}")"

  printf 'Kernel-release commands for libusb tools:\n\n'
  printf 'sudo modprobe -r rtl2832_sdr dvb_usb_rtl28xxu rtl2832 dvb_usb_v2\n'
  printf "echo '%s' | sudo tee /sys/bus/usb/drivers/dvb_usb_rtl28xxu/unbind\n" "${if_name}"
  printf '\nAfter that:\n\n'
  printf './setup/rtl2838.sh rtl-test\n'
}

capture_v4l2() {
  local freq_hz="${1:-}"
  local seconds="${2:-10}"
  local output_file="${3:-}"
  local size_bytes buffers bytes_per_buffer freq_mhz

  require_command v4l2-ctl
  [[ -n "${freq_hz}" ]] || die "Usage: ./setup/rtl2838.sh capture-v4l2 <freq_hz> [seconds] [output_file]"
  require_kernel_device

  ensure_layout
  if [[ -z "${output_file}" ]]; then
    output_file="$(default_capture_path "v4l2-${freq_hz}" "cu8")"
  fi

  bytes_per_buffer=65536
  size_bytes=$(( DEFAULT_SAMPLE_RATE * seconds * 2 ))
  buffers=$(( (size_bytes + bytes_per_buffer - 1) / bytes_per_buffer ))
  freq_mhz="$(hz_to_mhz "${freq_hz}")"

  print_info "Tuning ${DEFAULT_DEVICE} to ${freq_hz} Hz (${freq_mhz} MHz)"
  v4l2-ctl -d "${DEFAULT_DEVICE}" --set-freq="${freq_mhz}" >/dev/null
  if [[ "${DEFAULT_BANDWIDTH}" != "0" ]]; then
    v4l2-ctl -d "${DEFAULT_DEVICE}" --set-ctrl="bandwidth_auto=0,bandwidth=${DEFAULT_BANDWIDTH}" >/dev/null || true
  fi

  print_info "Capturing ${seconds}s of CU8 IQ to ${output_file}"
  v4l2-ctl -d "${DEFAULT_DEVICE}" \
    --set-fmt-sdr=samplefmt=CU08 \
    --stream-mmap=4 \
    --stream-count="${buffers}" \
    --stream-to="${output_file}"

  print_success "Wrote ${output_file}"
}

analyze_capture() {
  local capture_file="${1:-}"
  local center_hz="${2:-}"
  local sample_rate="${3:-${DEFAULT_SAMPLE_RATE}}"
  local output_prefix="${4:-}"
  local profile="${5:-none}"

  [[ -n "${capture_file}" && -n "${center_hz}" ]] || die "Usage: ./setup/rtl2838.sh analyze <capture_file> <center_hz> [sample_rate] [output_prefix] [profile]"
  [[ -f "${capture_file}" ]] || die "Capture file not found: ${capture_file}"
  ensure_layout

  if [[ -z "${output_prefix}" ]]; then
    output_prefix="${REPORT_DIR}/$(basename "${capture_file%.*}")"
  fi

  run_python_tool "${MONITOR_TOOL}" \
    --input "${capture_file}" \
    --center-freq-hz "${center_hz}" \
    --sample-rate "${sample_rate}" \
    --output-prefix "${output_prefix}" \
    --profile "${profile}" \
    --title "RTL2838 analysis: $(basename "${capture_file}")"
}

eu868_demo() {
  local seconds="${1:-10}"
  local timestamp low_capture high_capture low_prefix high_prefix report_path
  local low_center="867900000"
  local high_center="869525000"

  ensure_layout
  timestamp="$(date +%Y%m%d-%H%M%S)"
  low_capture="${IQ_DIR}/eu868-low-${timestamp}.cu8"
  high_capture="${IQ_DIR}/eu868-high-${timestamp}.cu8"
  low_prefix="${REPORT_DIR}/eu868-low-${timestamp}"
  high_prefix="${REPORT_DIR}/eu868-high-${timestamp}"
  report_path="${REPORT_DIR}/eu868-demo-${timestamp}.md"

  print_info "EU868 demo capture 1/2: low-band window around 867.9 MHz"
  capture_v4l2 "${low_center}" "${seconds}" "${low_capture}"
  analyze_capture "${low_capture}" "${low_center}" "${DEFAULT_SAMPLE_RATE}" "${low_prefix}" "eu868-low" >/dev/null

  print_info "EU868 demo capture 2/2: high-band window around 869.525 MHz"
  capture_v4l2 "${high_center}" "${seconds}" "${high_capture}"
  analyze_capture "${high_capture}" "${high_center}" "${DEFAULT_SAMPLE_RATE}" "${high_prefix}" "eu868-high" >/dev/null

  cat > "${report_path}" <<EOF
# EU868 Monitoring Demo

- Generated: ${timestamp}
- Capture duration per window: ${seconds} s
- Assumed sample rate: ${DEFAULT_SAMPLE_RATE} S/s
- Kernel SDR device: ${DEFAULT_DEVICE}

## Low-band window

- Center frequency: ${low_center} Hz
- Capture: ${low_capture}
- Plot: ${low_prefix}.png
- Summary: ${low_prefix}.json

## High-band window

- Center frequency: ${high_center} Hz
- Capture: ${high_capture}
- Plot: ${high_prefix}.png
- Summary: ${high_prefix}.json

Notes:

- This is a spectrum-monitoring demo, not a LoRa demodulator.
- The low-band window is intended to catch common EU868 LoRa activity around 867.1 to 868.5 MHz.
- The high-band window highlights the 869.525 MHz area often worth checking for Meshtastic/EU868 activity.
EOF

  print_success "Wrote ${report_path}"
  print_success "Low-band plot: ${low_prefix}.png"
  print_success "High-band plot: ${high_prefix}.png"
}

live_waterfall() {
  local center_hz="${1:-868475000}"
  local profile="${2:-eu868-wide}"
  local sample_rate="${3:-${DEFAULT_SAMPLE_RATE}}"
  local charset="${4:-${RTL2838_CHARSET:-blocks}}"

  if is_help_request "${1:-}"; then
    run_python_tool "${LIVE_WATERFALL_TOOL}" --help
    return 0
  fi

  require_command v4l2-ctl
  require_kernel_device

  run_python_tool "${LIVE_WATERFALL_TOOL}" \
    --device "${DEFAULT_DEVICE}" \
    --profile "${profile}" \
    --center "${center_hz}" \
    --span "${sample_rate}" \
    --charset "${charset}" \
    --bandwidth "${DEFAULT_BANDWIDTH}"
}

run_profile_waterfall() {
  local profile="${1}"
  local fps="${2:-${RTL2838_FPS:-12}}"
  local charset="${3:-${RTL2838_CHARSET:-blocks}}"

  if is_help_request "${profile:-}"; then
    run_python_tool "${LIVE_WATERFALL_TOOL}" --help
    return 0
  fi

  require_command v4l2-ctl
  require_kernel_device

  run_python_tool "${LIVE_WATERFALL_TOOL}" \
    --device "${DEFAULT_DEVICE}" \
    --profile "${profile}" \
    --fps "${fps}" \
    --charset "${charset}" \
    --bandwidth "${DEFAULT_BANDWIDTH}"
}

eu868_live() {
  local mode="${1:-wide}"
  local sample_rate="${2:-${DEFAULT_SAMPLE_RATE}}"
  local charset="${3:-${RTL2838_CHARSET:-blocks}}"
  local center_hz
  local profile

  if is_help_request "${mode:-}"; then
    run_python_tool "${LIVE_WATERFALL_TOOL}" --help
    return 0
  fi

  case "${mode}" in
    wide)
      center_hz="868475000"
      profile="eu868-wide"
      ;;
    low)
      center_hz="867900000"
      profile="eu868-low"
      ;;
    high)
      center_hz="869525000"
      profile="eu868-high"
      ;;
    *)
      live_waterfall "${mode}" "eu868-wide" "${DEFAULT_SAMPLE_RATE}" "${charset}"
      return 0
      ;;
  esac

  live_waterfall "${center_hz}" "${profile}" "${sample_rate}" "${charset}"
}

preset_live() {
  local preset="${1:-${RTL2838_PRESET:-eu868-wide}}"
  local fps="${2:-${RTL2838_FPS:-12}}"
  local charset="${3:-${RTL2838_CHARSET:-blocks}}"

  run_profile_waterfall "${preset}" "${fps}" "${charset}"
}

apply_monitor_mode() {
  local mode="${1}"
  case "${mode}" in
    libusb)
      switch_to_libusb
      ;;
    kernel)
      switch_to_kernel
      ;;
    auto-adsb)
      if [[ -e "${DEFAULT_DEVICE}" ]] && rtl2838_kernel_bound_interface; then
        :
      else
        switch_to_libusb
      fi
      ;;
    none)
      ;;
    *)
      die "Unsupported monitor mode: ${mode}"
      ;;
  esac
}

run_monitor_tool() {
  local tool="${1}"
  local mode="${2}"
  shift 2

  local injected=()
  while [[ $# -gt 0 && "${1}" != "--" ]]; do
    injected+=("${1}")
    shift
  done
  [[ "${1:-}" == "--" ]] && shift
  local user_args=("$@")

  if is_help_request "${user_args[0]:-}"; then
    run_python_tool "${tool}" "${injected[@]}" "${user_args[@]}"
    return 0
  fi

  apply_monitor_mode "${mode}"
  run_python_tool "${tool}" "${injected[@]}" "${user_args[@]}"
}

adsb_monitor() {
  if is_help_request "${1:-}"; then
    run_python_tool "${ADSB_MONITOR_TOOL}" "$@"
    return 0
  fi
  ensure_command_or_apt readsb readsb
  run_monitor_tool "${ADSB_MONITOR_TOOL}" "libusb" -- "$@"
}

rtl433_monitor() {
  local preset='868'
  local rtl433_bin
  if is_help_request "${1:-}"; then
    run_python_tool "${RTL433_MONITOR_TOOL}" "$@"
    return 0
  fi
  case "${1:-}" in
    433|868|915)
      preset="${1}"
      shift
      ;;
  esac

  rtl433_bin="$(resolve_binary_prefer_local "${BIN_DIR}/rtl_433" "rtl_433" || true)"
  [[ -n "${rtl433_bin}" ]] || die "Missing rtl_433. Run ./setup/rtl2838.sh bootstrap to build the repo-local copy."

  run_monitor_tool "${RTL433_MONITOR_TOOL}" "libusb" -- --rtl433 "${rtl433_bin}" --preset "${preset}" "$@"
}

aprs_monitor() {
  if is_help_request "${1:-}"; then
    run_python_tool "${APRS_MONITOR_TOOL}" "$@"
    return 0
  fi
  have_local_tool rtl_fm || die "Missing rtl_fm. Run ./setup/rtl2838.sh bootstrap first."
  ensure_command_or_apt multimon-ng multimon-ng
  run_monitor_tool "${APRS_MONITOR_TOOL}" "libusb" -- --rtl-fm "${BIN_DIR}/rtl_fm" "$@"
}

acars_monitor() {
  local acarsdec_bin
  if is_help_request "${1:-}"; then
    run_python_tool "${ACARS_MONITOR_TOOL}" "$@"
    return 0
  fi
  acarsdec_bin="$(resolve_binary_prefer_local "${BIN_DIR}/acarsdec" "acarsdec" || true)"
  [[ -n "${acarsdec_bin}" ]] || die "Missing acarsdec. Run ./setup/rtl2838.sh bootstrap first."
  run_monitor_tool "${ACARS_MONITOR_TOOL}" "libusb" -- --acarsdec "${acarsdec_bin}" "$@"
}

ais_monitor() {
  if is_help_request "${1:-}"; then
    run_python_tool "${AIS_MONITOR_TOOL}" "$@"
    return 0
  fi
  ensure_command_or_apt rtl_ais rtl-ais
  run_monitor_tool "${AIS_MONITOR_TOOL}" "libusb" -- "$@"
}

weather_alert_monitor() {
  if is_help_request "${1:-}"; then
    run_python_tool "${WEATHER_ALERT_MONITOR_TOOL}" "$@"
    return 0
  fi
  have_local_tool rtl_fm || die "Missing rtl_fm. Run ./setup/rtl2838.sh bootstrap first."
  ensure_command_or_apt multimon-ng multimon-ng
  run_monitor_tool "${WEATHER_ALERT_MONITOR_TOOL}" "libusb" -- --rtl-fm "${BIN_DIR}/rtl_fm" "$@"
}

rds_monitor() {
  if is_help_request "${1:-}"; then
    run_python_tool "${RDS_MONITOR_TOOL}" "$@"
    return 0
  fi
  have_local_tool rtl_fm || die "Missing rtl_fm. Run ./setup/rtl2838.sh bootstrap first."
  have_local_tool redsea || die "Missing redsea. Run ./setup/rtl2838.sh bootstrap first."
  run_monitor_tool "${RDS_MONITOR_TOOL}" "libusb" -- --rtl-fm "${BIN_DIR}/rtl_fm" --redsea "${BIN_DIR}/redsea" "$@"
}

vdl2_monitor() {
  local vdlm2dec_bin
  if is_help_request "${1:-}"; then
    run_python_tool "${VDL2_MONITOR_TOOL}" "$@"
    return 0
  fi
  vdlm2dec_bin="$(resolve_binary_prefer_local "${BIN_DIR}/vdlm2dec" "vdlm2dec" || true)"
  [[ -n "${vdlm2dec_bin}" ]] || die "Missing vdlm2dec. Run ./setup/rtl2838.sh bootstrap first."
  run_monitor_tool "${VDL2_MONITOR_TOOL}" "libusb" -- --vdlm2dec "${vdlm2dec_bin}" "$@"
}

pager_monitor() {
  if is_help_request "${1:-}"; then
    run_python_tool "${PAGER_MONITOR_TOOL}" "$@"
    return 0
  fi
  have_local_tool rtl_fm || die "Missing rtl_fm. Run ./setup/rtl2838.sh bootstrap first."
  ensure_command_or_apt multimon-ng multimon-ng
  run_monitor_tool "${PAGER_MONITOR_TOOL}" "libusb" -- --rtl-fm "${BIN_DIR}/rtl_fm" "$@"
}

noaa_apt_capture_cmd() {
  if is_help_request "${1:-}"; then
    run_python_tool "${NOAA_APT_CAPTURE_TOOL}" "$@"
    return 0
  fi
  have_local_tool rtl_fm || die "Missing rtl_fm. Run ./setup/rtl2838.sh bootstrap first."
  have_local_tool aptdec || die "Missing aptdec. Run ./setup/rtl2838.sh bootstrap first."
  ensure_command_or_apt sox sox
  apply_monitor_mode "libusb"
  run_python_tool "${NOAA_APT_CAPTURE_TOOL}" --rtl-fm "${BIN_DIR}/rtl_fm" --aptdec "${BIN_DIR}/aptdec" "$@"
}

lora_monitor() {
  if is_help_request "${1:-}"; then
    run_python_tool "${LORA_MONITOR_TOOL}" "$@"
    return 0
  fi
  have_local_tool rtl_sdr || die "Missing rtl_sdr. Run ./setup/rtl2838.sh bootstrap first."
  apply_monitor_mode "libusb"
  run_python_tool "${LORA_MONITOR_TOOL}" --rtl-sdr "${BIN_DIR}/rtl_sdr" "$@"
}

rtl_test_cmd() {
  run_local_tool rtl_test "$@"
}

rtl_sdr_cmd() {
  local freq_hz="${1:-}"
  local seconds="${2:-10}"
  local output_file="${3:-}"
  local sample_count

  [[ -n "${freq_hz}" ]] || die "Usage: ./setup/rtl2838.sh rtl-sdr <freq_hz> [seconds] [output_file]"
  ensure_layout
  if [[ -z "${output_file}" ]]; then
    output_file="$(default_capture_path "rtl-sdr-${freq_hz}" "cu8")"
  fi

  sample_count=$(( DEFAULT_SAMPLE_RATE * seconds ))
  print_info "Capturing ${seconds}s via rtl_sdr to ${output_file}"
  run_local_tool rtl_sdr \
    -f "${freq_hz}" \
    -s "${DEFAULT_SAMPLE_RATE}" \
    -g "${DEFAULT_GAIN}" \
    -n "${sample_count}" \
    "${output_file}"
  print_success "Wrote ${output_file}"
}

fm_cmd() {
  local freq_hz="${1:-}"
  local seconds="${2:-20}"
  local output_file="${3:-}"

  [[ -n "${freq_hz}" ]] || die "Usage: ./setup/rtl2838.sh fm <freq_hz> [seconds] [output_wav]"
  ensure_command_or_apt sox sox
  ensure_layout
  if [[ -z "${output_file}" ]]; then
    output_file="$(default_capture_path "fm-${freq_hz}" "wav")"
  fi

  print_info "Receiving FM audio from ${freq_hz} Hz for ${seconds}s"
  timeout "${seconds}" \
    bash -lc "\"${BIN_DIR}/rtl_fm\" -M fm -f \"${0}\" -s \"${1}\" -r \"${2}\" - | sox -t raw -r \"${2}\" -e signed -b 16 -c 1 - -t wav \"${3}\"" \
    "${freq_hz}" "${DEFAULT_FM_RATE}" "${DEFAULT_FM_OUTPUT_RATE}" "${output_file}" || true

  if [[ -s "${output_file}" ]]; then
    print_success "Wrote ${output_file}"
  else
    die "No WAV data written. Confirm rtl_fm built correctly and the tuned frequency carries FM audio."
  fi
}

doctor() {
  printf '%sRTL2838 doctor%s\n' "${COLOR_BOLD}${COLOR_CYAN}" "${COLOR_RESET}"
  printf '\nUSB dongle:\n'
  detect_usb || true
  printf '\nDevice node:\n'
  ls -l "${DEFAULT_DEVICE}" 2>/dev/null || print_warn "Missing ${DEFAULT_DEVICE}"
  if command -v getfacl >/dev/null 2>&1 && [[ -e "${DEFAULT_DEVICE}" ]]; then
    printf '\nACL:\n'
    getfacl "${DEFAULT_DEVICE}" || true
  fi
  printf '\nKernel SDR state:\n'
  detect_v4l2 || true
  printf '\nBuild prerequisites:\n'
  for cmd in git cmake make gcc pkg-config v4l2-ctl lsusb sox; do
    if command -v "${cmd}" >/dev/null 2>&1; then
      printf '  %s%s%s\n' "${COLOR_GREEN}" "${cmd}" "${COLOR_RESET}"
    else
      printf '  %s%s%s\n' "${COLOR_YELLOW}" "${cmd}" "${COLOR_RESET}"
    fi
  done
  printf '\nHints:\n'
  printf '  - Run ./setup/rtl2838.sh bootstrap to build repo-local rtl-sdr and rtl_433 tools.\n'
  printf '  - Run ./setup/rtl2838.sh probe-kernel to confirm the V4L2 SDR path.\n'
  printf '  - Run ./setup/rtl2838.sh eu868-demo 10 for a practical spectrum-monitoring pass.\n'
  printf '  - If rtl_test cannot claim the dongle, first stop any other SDR app using it.\n'
  printf '  - If the kernel still owns interface 1.0, run ./setup/rtl2838.sh release-libusb.\n'
}

normalize_command_alias() {
  case "${1:-help}" in
    rf_adsb_monitor|rf-adsb-monitor)
      printf 'adsb-monitor\n'
      ;;
    rf_rtl433_monitor|rf-rtl433-monitor)
      printf 'rtl433-monitor\n'
      ;;
    rf_aprs_monitor|rf-aprs-monitor)
      printf 'aprs-monitor\n'
      ;;
    rf_acars_monitor|rf-acars-monitor)
      printf 'acars-monitor\n'
      ;;
    rf_ais_monitor|rf-ais-monitor)
      printf 'ais-monitor\n'
      ;;
    rf_weather_alert_monitor|rf-weather-alert-monitor)
      printf 'weather-alert-monitor\n'
      ;;
    rf_rds_monitor|rf-rds-monitor)
      printf 'rds-monitor\n'
      ;;
    rf_vdl2_monitor|rf-vdl2-monitor)
      printf 'vdl2-monitor\n'
      ;;
    rf_pager_monitor|rf-pager-monitor)
      printf 'pager-monitor\n'
      ;;
    rf_noaa_apt_capture)
      printf 'noaa-apt-capture\n'
      ;;
    rf_lora_monitor|rf-lora-monitor)
      printf 'lora-monitor\n'
      ;;
    *)
      printf '%s\n' "${1:-help}"
      ;;
  esac
}

dispatch_command() {
  local command="${1}"
  shift || true

  case "${command}" in
    help|-h|--help)
      usage
      ;;
    info)
      info
      ;;
    bootstrap)
      bootstrap
      ;;
    probe-kernel)
      probe_kernel
      ;;
    use-kernel)
      switch_to_kernel
      ;;
    use-libusb)
      switch_to_libusb
      ;;
    release-libusb)
      release_libusb
      ;;
    capture-v4l2)
      capture_v4l2 "$@"
      ;;
    analyze)
      analyze_capture "$@"
      ;;
    eu868-demo)
      eu868_demo "$@"
      ;;
    live-waterfall)
      live_waterfall "$@"
      ;;
    eu868-live)
      eu868_live "$@"
      ;;
    preset-live)
      preset_live "$@"
      ;;
    adsb-monitor)
      adsb_monitor "$@"
      ;;
    rtl433-monitor)
      rtl433_monitor "$@"
      ;;
    aprs-monitor)
      aprs_monitor "$@"
      ;;
    acars-monitor)
      acars_monitor "$@"
      ;;
    ais-monitor)
      ais_monitor "$@"
      ;;
    weather-alert-monitor)
      weather_alert_monitor "$@"
      ;;
    rds-monitor)
      rds_monitor "$@"
      ;;
    vdl2-monitor)
      vdl2_monitor "$@"
      ;;
    pager-monitor)
      pager_monitor "$@"
      ;;
    noaa-apt-capture)
      noaa_apt_capture_cmd "$@"
      ;;
    lora-monitor)
      lora_monitor "$@"
      ;;
    rtl-test)
      rtl_test_cmd "$@"
      ;;
    rtl-sdr)
      rtl_sdr_cmd "$@"
      ;;
    fm)
      fm_cmd "$@"
      ;;
    doctor)
      doctor
      ;;
    *)
      usage >&2
      exit 1
      ;;
  esac
}

main() {
  local command
  command="$(normalize_command_alias "${1:-help}")"
  shift || true
  dispatch_command "${command}" "$@"
}

main "$@"
