import pathlib
import re
import subprocess
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "setup" / "meshtastic-python.sh"


def run_wrapper_snippet(snippet: str) -> subprocess.CompletedProcess[str]:
    script = f"""set -euo pipefail
source {SCRIPT_PATH}
{snippet}
"""
    return subprocess.run(
        ["bash", "-lc", script],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


class MeshtasticSetupWrapperTests(unittest.TestCase):
    def test_embedded_python_blocks_compile(self) -> None:
        script_text = SCRIPT_PATH.read_text(encoding="utf-8")
        blocks = re.findall(r"<<'PY'\n(.*?)\nPY", script_text, re.DOTALL)

        self.assertGreater(len(blocks), 0)
        for index, block in enumerate(blocks, start=1):
            try:
                compile(block, f"{SCRIPT_PATH.name}:heredoc:{index}", "exec")
            except SyntaxError as exc:
                self.fail(f"Embedded Python heredoc {index} does not compile: {exc}")

    def test_wrapper_can_be_sourced_without_running_dispatch(self) -> None:
        result = run_wrapper_snippet("printf 'ready\\n'")

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "ready\n")

    def test_windows_os_layer_uses_scripts_python_and_com_port(self) -> None:
        result = run_wrapper_snippet(
            """
uname() { printf 'MINGW64_NT-10.0\\n'; }
source "$ROOT_DIR/setup/lib/meshtastic-os.sh"
printf '%s\\n' "$(meshtastic_venv_python_path "$ROOT_DIR/.venv")"
printf '%s\\n' "$(meshtastic_default_serial_port)"
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], f"{REPO_ROOT}/.venv/Scripts/python.exe")
        self.assertEqual(lines[1], "COM3")

    def test_macos_os_layer_uses_bin_python_and_usbmodem_default(self) -> None:
        result = run_wrapper_snippet(
            """
uname() { printf 'Darwin\\n'; }
source "$ROOT_DIR/setup/lib/meshtastic-os.sh"
printf '%s\\n' "$(meshtastic_venv_python_path "$ROOT_DIR/.venv")"
printf '%s\\n' "$(meshtastic_default_serial_port)"
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], f"{REPO_ROOT}/.venv/bin/python")
        self.assertEqual(lines[1], "/dev/tty.usbmodem1")

    def test_ensure_venv_recreates_broken_environment(self) -> None:
        result = run_wrapper_snippet(
            """
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
VENV_DIR="$tmpdir/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
mkdir -p "$VENV_DIR/bin"
cat > "$VENV_DIR/bin/python" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
chmod +x "$VENV_DIR/bin/python"
python3() {
  if [[ "$1" == "-m" && "$2" == "venv" ]]; then
    mkdir -p "$3/bin"
    cat > "$3/bin/python" <<'EOF'
#!/usr/bin/env bash
if [[ "$1" == "-m" && "$2" == "pip" && "$3" == "--version" ]]; then
  echo pip-ok
  exit 0
fi
exit 1
EOF
    chmod +x "$3/bin/python"
    return 0
  fi
  return 1
}
ensure_venv
"$VENV_DIR/bin/python" -m pip --version
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("pip-ok\n", result.stdout)

    def test_create_venv_auto_installs_system_support_when_available(self) -> None:
        result = run_wrapper_snippet(
            """
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
VENV_DIR="$tmpdir/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
marker="$tmpdir/venv-installed"
python3() {
  if [[ "$1" == "-m" && "$2" == "venv" ]]; then
    if [[ ! -f "$marker" ]]; then
      printf 'The virtual environment was not created successfully because ensurepip is not available.\n' >&2
      return 1
    fi
    mkdir -p "$3/bin"
    cat > "$3/bin/python" <<'EOF'
#!/usr/bin/env bash
if [[ "$1" == "-m" && "$2" == "pip" && "$3" == "--version" ]]; then
  echo pip-ok
  exit 0
fi
exit 1
EOF
    chmod +x "$3/bin/python"
    return 0
  fi
  command python3 "$@"
}
can_auto_install_python_venv_support() { return 0; }
install_python_venv_support() { touch "$marker"; }
create_venv
"$VENV_DIR/bin/python" -m pip --version
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("pip-ok\n", result.stdout)

    def test_ensure_venv_reports_missing_system_venv_support(self) -> None:
        result = run_wrapper_snippet(
            """
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
VENV_DIR="$tmpdir/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
python3() {
  if [[ "$1" == "-m" && "$2" == "venv" ]]; then
    printf 'The virtual environment was not created successfully because ensurepip is not available.\n' >&2
    return 1
  fi
  command python3 "$@"
}
can_auto_install_python_venv_support() { return 1; }
ensure_venv
"""
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("python3 venv support is missing on this system.", result.stderr)
        self.assertIn("Install python3.", result.stderr)
        self.assertIn("-venv or python3-venv, then rerun this command.", result.stderr)

    def test_guided_runs_preflight_before_prompting(self) -> None:
        result = run_wrapper_snippet(
            """
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
order_file="$tmpdir/order.txt"
print_banner() { :; }
print_warn() { :; }
preflight_guided_setup() { echo preflight >> "$order_file"; }
prompt_with_default() { echo "prompt:$1" >> "$order_file"; printf '%s\n' "$2"; }
prompt_secret_optional() { echo secret >> "$order_file"; printf '\n'; }
prompt_yes_no() { echo confirm >> "$order_file"; return 1; }
guided >/dev/null 2>&1 || true
cat "$order_file"
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = [line for line in result.stdout.splitlines() if line]
        self.assertGreaterEqual(len(lines), 2)
        self.assertEqual(lines[0], "preflight")
        self.assertTrue(lines[1].startswith("prompt:"))

    def test_main_forwards_autostart_scope_arguments(self) -> None:
        result = run_wrapper_snippet(
            """
proxy_autostart_install() { printf 'install:%s\\n' "$1"; }
proxy_autostart_remove() { printf 'remove:%s\\n' "$1"; }
proxy_autostart_status() { printf 'status:%s\\n' "$1"; }
main proxy-autostart-install --system
main proxy-autostart-remove --system
main proxy-autostart-status --system
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(
            result.stdout,
            "install:--system\nremove:--system\nstatus:--system\n",
        )

    def test_main_dispatches_nodedb_reset(self) -> None:
        result = run_wrapper_snippet(
            """
nodedb_reset() { printf 'nodedb-reset\n'; }
main nodedb-reset
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "nodedb-reset\n")

    def test_main_dispatches_messages(self) -> None:
        result = run_wrapper_snippet(
            """
messages() { printf 'messages:%s\n' "$*"; }
main messages send WO67 hello mesh
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "messages:send WO67 hello mesh\n")

    def test_main_dispatches_channels(self) -> None:
        result = run_wrapper_snippet(
            """
channels() { printf 'channels:%s\n' "$*"; }
main channels add Friends
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "channels:add Friends\n")

    def test_main_dispatches_protocol(self) -> None:
        result = run_wrapper_snippet(
            """
protocol() { printf 'protocol:%s\n' "$*"; }
main protocol capture --quiet
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "protocol:capture --quiet\n")

    def test_main_dispatches_plugins(self) -> None:
        result = run_wrapper_snippet(
            """
plugins() { printf 'plugins:%s\n' "$*"; }
main plugins STORE_FORWARD_APP stats
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "plugins:STORE_FORWARD_APP stats\n")

    def test_main_dispatches_get(self) -> None:
        result = run_wrapper_snippet(
            """
get_pref() { printf 'get:%s\n' "$*"; }
main get range_test.sender
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "get:range_test.sender\n")

    def test_main_dispatches_set(self) -> None:
        result = run_wrapper_snippet(
            """
set_pref() { printf 'set:%s\n' "$*"; }
main set range_test.sender 0
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "set:range_test.sender 0\n")

    def test_get_pref_forwards_to_meshtastic_cli(self) -> None:
        result = run_wrapper_snippet(
            """
run_meshtastic_cli() { printf 'cli:%s\n' "$*"; }
get_pref range_test.sender
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "cli:--get range_test.sender\n")

    def test_set_pref_forwards_to_meshtastic_cli(self) -> None:
        result = run_wrapper_snippet(
            """
run_meshtastic_cli() { printf 'cli:%s\n' "$*"; }
set_pref range_test.sender 0
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "cli:--set range_test.sender 0\n")

    def test_channels_list_delegates_to_status_tool(self) -> None:
        result = run_wrapper_snippet(
            """
status() { printf 'status:%s\n' "$*"; }
channels list
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "status:channels\n")

    def test_channels_add_forwards_to_meshtastic_cli(self) -> None:
        result = run_wrapper_snippet(
            """
run_meshtastic_cli() { printf 'cli:%s\n' "$*"; }
channels add Friends
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "cli:--ch-add Friends\n")

    def test_channels_url_defaults_to_all_scope(self) -> None:
        result = run_wrapper_snippet(
            """
channel_url() { printf 'url:%s\n' "$1"; }
channels url
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "url:all\n")

    def test_channels_url_accepts_primary_scope(self) -> None:
        result = run_wrapper_snippet(
            """
channel_url() { printf 'url:%s\n' "$1"; }
channels url primary
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "url:primary\n")

    def test_channels_set_forwards_index_and_field(self) -> None:
        result = run_wrapper_snippet(
            """
run_meshtastic_cli() { printf 'cli:%s\n' "$*"; }
channels set 1 psk random
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "cli:--ch-index 1 --ch-set psk random\n")

    def test_channels_add_url_strips_embedded_whitespace(self) -> None:
        result = run_wrapper_snippet(
            """
run_meshtastic_cli() { printf 'cli:%s\n' "$*"; }
channels add-url $'https://meshtastic.org/e/#ABC\n DEF\tGHI'
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "cli:--ch-add-url https://meshtastic.org/e/#ABCDEFGHI\n")

    def test_channels_set_url_strips_embedded_whitespace(self) -> None:
        result = run_wrapper_snippet(
            """
run_meshtastic_cli() { printf 'cli:%s\n' "$*"; }
channels set-url $'https://meshtastic.org/e/#ABC\r\nDEF GHI'
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "cli:--ch-set-url https://meshtastic.org/e/#ABCDEFGHI\n")

    def test_proxy_start_manual_uses_runtime_manager(self) -> None:
        result = run_wrapper_snippet(
            """
check_proxy_tool() { :; }
ensure_python_packages() { :; }
ensure_runtime_dir() { mkdir -p "$RUNTIME_DIR"; }
proxy_service_installed() { return 1; }
proxy_is_running() { return 1; }
check_port() { :; }
seq() { printf '1\n'; }
nohup() { printf '%s\n' "$*"; return 0; }
sleep() { :; }
proxy_is_ready() { return 0; }
proxy_pid() { printf '123\n'; }
proxy_start
cat "$PROXY_LOG_FILE"
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn(f"nohup:{REPO_ROOT}/.venv/bin/python {REPO_ROOT}/tools/meshtastic_runtime_manager.py --serial-port", result.stdout)

    def test_proxy_unit_uses_runtime_manager_without_protocol_unit_dependency(self) -> None:
        result = run_wrapper_snippet(
            """
proxy_unit_content user
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertNotIn("Wants=meshtastic-protocol.service", result.stdout)
        self.assertIn(f"{REPO_ROOT}/tools/meshtastic_runtime_manager.py", result.stdout)

    def test_main_dispatches_telemetry(self) -> None:
        result = run_wrapper_snippet(
            """
telemetry() { printf 'telemetry:%s\n' "$*"; }
main telemetry --type environment --limit 2
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "telemetry:--type environment --limit 2\n")

    def test_contacts_remove_forwards_to_meshtastic_cli(self) -> None:
        result = run_wrapper_snippet(
            """
run_meshtastic_cli() { printf 'cli:%s\n' "$*"; }
contacts remove '!0439d098'
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "cli:--remove-node !0439d098\n")

    def test_contacts_favorite_forwards_to_meshtastic_cli(self) -> None:
        result = run_wrapper_snippet(
            """
run_meshtastic_cli() { printf 'cli:%s\n' "$*"; }
contacts favorite '!0439d098'
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "cli:--set-favorite-node !0439d098\n")

    def test_contacts_ignore_forwards_to_meshtastic_cli(self) -> None:
        result = run_wrapper_snippet(
            """
run_meshtastic_cli() { printf 'cli:%s\n' "$*"; }
contacts ignore '!0439d098'
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "cli:--set-ignored-node !0439d098\n")

    def test_contacts_add_reports_meshtastic_limitation(self) -> None:
        result = run_wrapper_snippet("contacts add '!0439d098'")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("contacts add is not supported", result.stderr)

    def test_doctor_reports_pkc_readiness(self) -> None:
        result = run_wrapper_snippet(
            """
require_direct_serial() { :; }
check_port() { :; }
run_in_venv() {
  if [[ "$1" == "meshtastic" ]]; then
    printf 'probe-ok\n'
    return 0
  fi
  if [[ "$1" == "python" ]]; then
    printf '  Node PKC capable: yes\n'
    printf '  Local public key: test-key\n'
    printf '  Known peers with public keys: 1/2\n'
    return 0
  fi
  return 1
}
doctor
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("PKC readiness:\n", result.stdout)
        self.assertIn("Node PKC capable: yes\n", result.stdout)

    def test_proxy_autostart_install_system_restarts_existing_service(self) -> None:
        result = run_wrapper_snippet(
            """
require_systemd_system() { :; }
check_proxy_tool() { :; }
check_protocol_tool() { :; }
ensure_python_packages() { :; }
ensure_runtime_dir() { :; }
ensure_service_config() { return 1; }
warn_if_service_config_differs() { :; }
proxy_user_service_installed() { return 1; }
proxy_user_service_enabled() { return 1; }
proxy_user_service_active() { return 1; }
proxy_system_service_installed() { return 0; }
proxy_system_service_enabled() { return 1; }
proxy_system_service_active() { return 0; }
proxy_manual_is_running() { return 1; }
proxy_write_system_systemd_unit() { :; }
remove_legacy_protocol_units_system() { printf 'legacy-system-removed\n'; }
proxy_system_service_active() { return 0; }
run_with_sudo() { printf 'sudo:%s\\n' "$*"; }
tcp_endpoint_ready() { return 0; }
proxy_autostart_install_system
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("legacy-system-removed\n", result.stdout)
        self.assertIn("sudo:systemctl daemon-reload\n", result.stdout)
        self.assertIn("sudo:systemctl enable meshtastic-proxy.service\n", result.stdout)
        self.assertIn("sudo:systemctl restart meshtastic-proxy.service\n", result.stdout)

    def test_proxy_autostart_install_user_restarts_existing_service(self) -> None:
        result = run_wrapper_snippet(
            """
require_systemd_user() { :; }
check_proxy_tool() { :; }
check_protocol_tool() { :; }
ensure_python_packages() { :; }
ensure_runtime_dir() { :; }
ensure_service_config() { return 1; }
warn_if_service_config_differs() { :; }
proxy_system_service_installed() { return 1; }
proxy_system_service_enabled() { return 1; }
proxy_system_service_active() { return 1; }
proxy_user_service_installed() { return 0; }
proxy_user_service_enabled() { return 1; }
proxy_user_service_active() { return 0; }
proxy_manual_is_running() { return 1; }
proxy_write_user_systemd_unit() { :; }
remove_legacy_protocol_units_user() { printf 'legacy-user-removed\n'; }
systemctl() { printf 'user:%s\\n' "$*"; }
tcp_endpoint_ready() { return 0; }
proxy_linger_status() { printf 'yes\\n'; }
proxy_autostart_install_user
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("legacy-user-removed\n", result.stdout)
        self.assertIn("user:--user daemon-reload\n", result.stdout)
        self.assertIn("user:--user enable meshtastic-proxy.service\n", result.stdout)
        self.assertIn("user:--user restart meshtastic-proxy.service\n", result.stdout)

    def test_proxy_unit_content_places_requires_mounts_for_in_unit_section(self) -> None:
        result = run_wrapper_snippet("proxy_unit_content system")

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("[Unit]\n", result.stdout)
        self.assertIn(f"RequiresMountsFor={REPO_ROOT}\n\n[Service]\n", result.stdout)
        self.assertNotIn("[Service]\nType=simple\nWorkingDirectory", result.stdout.split("RequiresMountsFor=")[-1].split("\n", 1)[0])

    def test_proxy_unit_content_uses_environment_file_and_variable_expansion(self) -> None:
        result = run_wrapper_snippet("proxy_unit_content user")

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn(f"EnvironmentFile={REPO_ROOT}/.runtime/meshtastic/service.env\n", result.stdout)
        self.assertIn(r"ExecStart=" + f"{REPO_ROOT}/.venv/bin/python {REPO_ROOT}/tools/meshtastic_runtime_manager.py --serial-port ${{MESHTASTIC_PORT}}", result.stdout)
        self.assertIn(r"--listen-host ${MESHTASTIC_PROXY_BIND_HOST}", result.stdout)
        self.assertIn(r"--connect-host ${MESHTASTIC_PROXY_HOST}", result.stdout)

    def test_ensure_service_config_rewrites_wildcard_proxy_host_for_local_clients(self) -> None:
        result = run_wrapper_snippet(
            """
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
RUNTIME_DIR="$tmpdir/runtime"
SERVICE_CONFIG_FILE="$RUNTIME_DIR/service.env"
mkdir -p "$RUNTIME_DIR"
cat > "$SERVICE_CONFIG_FILE" <<'EOF'
MESHTASTIC_PROXY_BIND_HOST=0.0.0.0
MESHTASTIC_PROXY_HOST=0.0.0.0
EOF
PROXY_CONNECT_HOST=127.0.0.1
ensure_service_config || true
cat "$SERVICE_CONFIG_FILE"
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("MESHTASTIC_PROXY_BIND_HOST=0.0.0.0\n", result.stdout)
        self.assertIn("MESHTASTIC_PROXY_HOST=127.0.0.1\n", result.stdout)

    def test_proxy_service_start_uses_system_manager_when_installed(self) -> None:
        result = run_wrapper_snippet(
            """
proxy_installed_manager_label() { printf 'systemd-system\\n'; }
require_systemd_system() { :; }
run_with_sudo() { printf 'sudo:%s\\n' "$*"; }
proxy_service_start
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "sudo:systemctl start meshtastic-proxy.service\n")

    def test_ensure_service_config_preserves_existing_values(self) -> None:
        result = run_wrapper_snippet(
            """
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
RUNTIME_DIR="$tmpdir/runtime"
SERVICE_CONFIG_FILE="$RUNTIME_DIR/service.env"
mkdir -p "$RUNTIME_DIR"
cat > "$SERVICE_CONFIG_FILE" <<'EOF'
MESHTASTIC_PORT=/dev/custom0
EOF
MESHTASTIC_PORT=/dev/override1
PORT=/dev/override1
ensure_service_config || true
cat "$SERVICE_CONFIG_FILE"
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "MESHTASTIC_PORT=/dev/custom0\n")

    def test_ensure_service_config_creates_defaults_when_missing(self) -> None:
        result = run_wrapper_snippet(
            """
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
RUNTIME_DIR="$tmpdir/runtime"
SERVICE_CONFIG_FILE="$RUNTIME_DIR/service.env"
PORT=/dev/default0
BAUD=115200
TCP_PORT=4403
PROXY_BIND_HOST=127.0.0.1
PROXY_CONNECT_HOST=127.0.0.1
PROTOCOL_LOG_NAME=protocol
if ensure_service_config; then
  printf 'created\n'
fi
cat "$SERVICE_CONFIG_FILE"
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("created\n", result.stdout)
        self.assertIn("MESHTASTIC_PORT=/dev/default0\n", result.stdout)

    def test_proxy_autostart_install_system_stops_after_creating_service_config(self) -> None:
        result = run_wrapper_snippet(
            """
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
RUNTIME_DIR="$tmpdir/runtime"
SERVICE_CONFIG_FILE="$RUNTIME_DIR/service.env"
require_systemd_system() { :; }
check_proxy_tool() { :; }
check_protocol_tool() { :; }
ensure_python_packages() { :; }
ensure_runtime_dir() { mkdir -p "$RUNTIME_DIR"; }
run_with_sudo() { printf 'sudo:%s\\n' "$*"; }
proxy_autostart_install_system
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Created persistent service config:", result.stdout + result.stderr)
        self.assertNotIn("sudo:systemctl", result.stdout)

    def test_proxy_service_start_uses_user_manager_when_installed(self) -> None:
        result = run_wrapper_snippet(
            """
proxy_installed_manager_label() { printf 'systemd-user\\n'; }
require_systemd_user() { :; }
systemctl() { printf 'user:%s\\n' "$*"; }
proxy_service_start
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "user:--user start meshtastic-proxy.service\n")

    def test_proxy_status_reports_installed_manager_when_stopped(self) -> None:
        result = run_wrapper_snippet(
            """
proxy_is_ready() { return 1; }
proxy_is_running() { return 1; }
proxy_service_installed() { return 0; }
proxy_installed_manager_label() { printf 'systemd-user\\n'; }
proxy_status
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Proxy:   stopped\n", result.stdout)
        self.assertIn("  Manager: systemd-user\n", result.stdout)

        def test_proxy_status_reports_radio_fault_counters(self) -> None:
                result = run_wrapper_snippet(
                        """
proxy_is_ready() { return 0; }
proxy_manager_label() { printf 'systemd-system\n'; }
proxy_pid() { printf '123\n'; }
read_proxy_status_field() {
    case "$1" in
        client_count) printf '2\n' ;;
        serial_connected) printf 'true\n' ;;
        denied_control_frames) printf '1\n' ;;
        forwarded_control_frames) printf '5\n' ;;
        observed_admin_responses) printf '4\n' ;;
        control_session_confirmed) printf 'false\n' ;;
        control_session_expires_in) printf '9.5\n' ;;
        dropped_radio_bytes) printf '17\n' ;;
        ignored_serial_debug_bytes) printf '64\n' ;;
        invalid_radio_frames) printf '3\n' ;;
        manager_pid) printf '444\n' ;;
        proxy.running) printf 'true\n' ;;
        protocol.running) printf 'true\n' ;;
        *) return 1 ;;
    esac
}
proxy_status
"""
                )

                self.assertEqual(result.returncode, 0, msg=result.stderr)
                self.assertIn("  Dropped radio bytes: 17\n", result.stdout)
                self.assertIn("  Ignored serial debug bytes: 64\n", result.stdout)
                self.assertIn("  Invalid radio frames: 3\n", result.stdout)
                self.assertIn("  Runtime manager pid: 444\n", result.stdout)
                self.assertIn("  Runtime proxy child: true\n", result.stdout)
                self.assertIn("  Runtime protocol child: true\n", result.stdout)

    def test_have_systemctl_user_requires_user_bus_environment(self) -> None:
        result = run_wrapper_snippet(
            """
command() {
  if [[ "$1" == "-v" && "$2" == "systemctl" ]]; then
    return 0
  fi
  builtin command "$@"
}
systemctl() { printf 'should-not-run\n'; }
unset XDG_RUNTIME_DIR
unset DBUS_SESSION_BUS_ADDRESS
if have_systemctl_user; then
  printf 'yes\n'
else
  printf 'no\n'
fi
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "no\n")

    def test_proxy_user_service_installed_returns_false_without_bus(self) -> None:
        result = run_wrapper_snippet(
            """
unset XDG_RUNTIME_DIR
unset DBUS_SESSION_BUS_ADDRESS
command() {
  if [[ "$1" == "-v" && "$2" == "systemctl" ]]; then
    return 0
  fi
  builtin command "$@"
}
systemctl() { printf 'unexpected-user-systemctl:%s\n' "$*"; return 1; }
if proxy_user_service_installed; then
  printf 'yes\n'
else
  printf 'no\n'
fi
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "no\n")


if __name__ == "__main__":
    unittest.main()
