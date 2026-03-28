import pathlib
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import meshtastic_runtime_manager as runtime_manager


class RuntimeManagerTests(unittest.TestCase):
    def test_auto_sidecar_enabled_for_loopback_bind(self) -> None:
        args = runtime_manager.build_parser().parse_args([
            "--listen-host",
            "127.0.0.1",
        ])

        manager = runtime_manager.RuntimeManager(args)

        self.assertTrue(manager.should_start_protocol_sidecar())

    def test_auto_sidecar_disabled_for_wildcard_bind(self) -> None:
        args = runtime_manager.build_parser().parse_args([
            "--listen-host",
            "0.0.0.0",
        ])

        manager = runtime_manager.RuntimeManager(args)

        self.assertFalse(manager.should_start_protocol_sidecar())

    def test_explicit_on_overrides_non_loopback_bind(self) -> None:
        args = runtime_manager.build_parser().parse_args([
            "--listen-host",
            "0.0.0.0",
            "--protocol-sidecar-mode",
            "on",
        ])

        manager = runtime_manager.RuntimeManager(args)

        self.assertTrue(manager.should_start_protocol_sidecar())


if __name__ == "__main__":
    unittest.main()