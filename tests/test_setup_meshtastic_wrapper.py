import pathlib
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
    def test_wrapper_can_be_sourced_without_running_dispatch(self) -> None:
        result = run_wrapper_snippet("printf 'ready\\n'")

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "ready\n")

    def test_ensure_venv_recreates_broken_environment(self) -> None:
        result = run_wrapper_snippet(
            """
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
VENV_DIR="$tmpdir/.venv"
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

    def test_proxy_autostart_install_system_restarts_existing_service(self) -> None:
        result = run_wrapper_snippet(
            """
require_systemd_system() { :; }
check_proxy_tool() { :; }
ensure_python_packages() { :; }
ensure_runtime_dir() { :; }
proxy_user_service_installed() { return 1; }
proxy_user_service_enabled() { return 1; }
proxy_user_service_active() { return 1; }
proxy_system_service_installed() { return 0; }
proxy_system_service_enabled() { return 1; }
proxy_system_service_active() { return 0; }
proxy_manual_is_running() { return 1; }
proxy_write_system_systemd_unit() { :; }
run_with_sudo() { printf 'sudo:%s\\n' "$*"; }
tcp_endpoint_ready() { return 0; }
proxy_autostart_install_system
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("sudo:systemctl daemon-reload\n", result.stdout)
        self.assertIn("sudo:systemctl enable meshtastic-proxy.service\n", result.stdout)
        self.assertIn("sudo:systemctl restart meshtastic-proxy.service\n", result.stdout)

    def test_proxy_autostart_install_user_restarts_existing_service(self) -> None:
        result = run_wrapper_snippet(
            """
require_systemd_user() { :; }
check_proxy_tool() { :; }
ensure_python_packages() { :; }
ensure_runtime_dir() { :; }
proxy_system_service_installed() { return 1; }
proxy_system_service_enabled() { return 1; }
proxy_system_service_active() { return 1; }
proxy_user_service_installed() { return 0; }
proxy_user_service_enabled() { return 1; }
proxy_user_service_active() { return 0; }
proxy_manual_is_running() { return 1; }
proxy_write_user_systemd_unit() { :; }
systemctl() { printf 'user:%s\\n' "$*"; }
tcp_endpoint_ready() { return 0; }
proxy_linger_status() { printf 'yes\\n'; }
proxy_autostart_install_user
"""
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("user:--user daemon-reload\n", result.stdout)
        self.assertIn("user:--user enable meshtastic-proxy.service\n", result.stdout)
        self.assertIn("user:--user restart meshtastic-proxy.service\n", result.stdout)

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


if __name__ == "__main__":
    unittest.main()