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