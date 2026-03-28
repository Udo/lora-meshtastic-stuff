#!/usr/bin/env python3
import json
import os
import platform
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TCP_HOST = "127.0.0.1"
DEFAULT_TCP_PORT = 4403
PROXY_STATUS_FILE = REPO_ROOT / ".runtime" / "meshtastic" / "proxy-status.json"
SERVICE_CONFIG_FILE = REPO_ROOT / ".runtime" / "meshtastic" / "service.env"
WILDCARD_TCP_HOSTS = {"", "0.0.0.0", "::", "[::]", "*"}


def host_os() -> str:
    system = platform.system()
    if system == "Linux":
        return "linux"
    if system == "Darwin":
        return "macos"
    if system == "Windows":
        return "windows"
    return "unknown"


def venv_python_path() -> Path:
    if host_os() == "windows":
        return REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    return REPO_ROOT / ".venv" / "bin" / "python"


def default_serial_port() -> str:
    os_name = host_os()
    candidates: list[str]

    if os_name == "linux":
        candidates = ["/dev/ttyUSB0", "/dev/ttyACM0"]
    elif os_name == "macos":
        candidates = sorted(str(path) for path in Path("/dev").glob("tty.usbserial*"))
        candidates.extend(sorted(str(path) for path in Path("/dev").glob("tty.usbmodem*")))
    else:
        candidates = []

    for candidate in candidates:
        if Path(candidate).exists():
            return candidate

    if os_name == "macos":
        return "/dev/tty.usbmodem1"
    if os_name == "windows":
        return "COM3"
    return "/dev/ttyUSB0"


VENV_PYTHON = venv_python_path()


def __getattr__(name: str):
    if name == "DEFAULT_SERIAL_PORT":
        return default_serial_port()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def ensure_repo_python(env_guard: str) -> None:
    if os.environ.get(env_guard) == "1":
        return
    if not VENV_PYTHON.exists():
        return
    if Path(sys.executable).absolute() == VENV_PYTHON:
        return

    env = os.environ.copy()
    env[env_guard] = "1"
    os.execve(str(VENV_PYTHON), [str(VENV_PYTHON), sys.argv[0], *sys.argv[1:]], env)


def use_color() -> bool:
    return sys.stdout.isatty()


class Palette:
    def __init__(self) -> None:
        enabled = use_color()
        self.reset = "\033[0m" if enabled else ""
        self.bold = "\033[1m" if enabled else ""
        self.dim = "\033[2m" if enabled else ""
        self.red = "\033[31m" if enabled else ""
        self.green = "\033[32m" if enabled else ""
        self.yellow = "\033[33m" if enabled else ""
        self.blue = "\033[34m" if enabled else ""
        self.magenta = "\033[35m" if enabled else ""
        self.cyan = "\033[36m" if enabled else ""


def style(palette: Palette, color: str, text: str) -> str:
    return f"{color}{text}{palette.reset}"


def normalize_tcp_client_host(host: object) -> str:
    raw_host = str(host or "").strip()
    if raw_host in WILDCARD_TCP_HOSTS:
        return DEFAULT_TCP_HOST
    return raw_host


def strip_raw(obj):
    if isinstance(obj, dict):
        return {key: strip_raw(value) for key, value in obj.items() if key != "raw"}
    if isinstance(obj, list):
        return [strip_raw(item) for item in obj]
    if isinstance(obj, bytes):
        return f"<{len(obj)} bytes>"
    return obj


def iface_nodes(iface) -> dict[str, dict[str, object]]:
    nodes = getattr(iface, "nodes", None)
    return nodes if isinstance(nodes, dict) else {}


def iface_local_node_num(iface) -> int | None:
    my_info = getattr(iface, "myInfo", None)
    node_num = getattr(my_info, "my_node_num", None)
    return node_num if isinstance(node_num, int) else None


def interface_target(interface: object) -> str:
    dev_path = getattr(interface, "devPath", None)
    if dev_path:
        return str(dev_path)

    hostname = getattr(interface, "hostname", None)
    port_number = getattr(interface, "portNumber", None)
    if hostname and port_number:
        return f"{hostname}:{port_number}"
    if hostname:
        return str(hostname)
    return "-"


def connect_interface_for_target(
    target,
    *,
    serial_factory=None,
    tcp_factory=None,
    serial_connect_now: bool = True,
    tcp_connect_now: bool = True,
):
    def prepare_interface(interface):
        if getattr(interface, "nodes", None) is None:
            interface.nodes = {}
        if getattr(interface, "nodesByNum", None) is None:
            interface.nodesByNum = {}
        return interface

    if target.mode == "tcp":
        if tcp_factory is None:
            from meshtastic.tcp_interface import TCPInterface

            tcp_factory = TCPInterface
        tcp_host = normalize_tcp_client_host(target.host)
        interface = prepare_interface(tcp_factory(tcp_host, portNumber=target.tcp_port, connectNow=False))
        if getattr(interface, "socket", None) is None and hasattr(interface, "myConnect"):
            interface.myConnect()
        if tcp_connect_now:
            interface.connect()
        return interface

    if serial_factory is None:
        from meshtastic.serial_interface import SerialInterface

        serial_factory = SerialInterface
    interface = prepare_interface(serial_factory(target.serial_port, connectNow=False))
    if serial_connect_now:
        interface.connect()
    return interface


def connection_error_message(target, exc: Exception) -> str:
    if target.mode == "tcp":
        return f"Could not connect to {target.host}:{target.tcp_port}: {exc}."
    return (
        f"Could not open {target.serial_port}: {exc}. "
        "Another process is probably using the Meshtastic serial port."
    )


@dataclass(frozen=True)
class MeshtasticTarget:
    mode: str
    source: str
    serial_port: str = field(default_factory=default_serial_port)
    host: str = ""
    tcp_port: int = DEFAULT_TCP_PORT

    @property
    def label(self) -> str:
        if self.mode == "tcp":
            return f"{self.host}:{self.tcp_port}"
        return self.serial_port


def env_serial_port() -> str:
    return os.environ.get("MESHTASTIC_PORT", default_serial_port())


def env_tcp_port() -> int:
    raw_value = os.environ.get("MESHTASTIC_TCP_PORT", str(DEFAULT_TCP_PORT))
    try:
        return int(raw_value)
    except ValueError:
        return DEFAULT_TCP_PORT


def env_host_override() -> str:
    return os.environ.get("MESHTASTIC_HOST", "")


def env_proxy_host() -> str:
    return normalize_tcp_client_host(os.environ.get("MESHTASTIC_PROXY_HOST", DEFAULT_TCP_HOST))


def load_proxy_status(status_file: Path = PROXY_STATUS_FILE) -> dict[str, object]:
    if not status_file.exists():
        return {}
    try:
        return json.loads(status_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def pid_is_running(pid_value: object) -> bool:
    try:
        pid = int(pid_value)
    except (TypeError, ValueError):
        return False

    if pid <= 0:
        return False

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def tcp_endpoint_ready(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def summarize_proxy_runtime(
    status_file: Path | None = None,
    service_config_file: Path | None = None,
) -> dict[str, object]:
    status_file = status_file or PROXY_STATUS_FILE
    service_config_file = service_config_file or SERVICE_CONFIG_FILE
    snapshot = load_proxy_status(status_file)

    host = normalize_tcp_client_host(env_proxy_host() or snapshot.get("listen_host"))
    listen_port = snapshot.get("listen_port")
    tcp_port = listen_port if isinstance(listen_port, int) else env_tcp_port()
    reachable = tcp_endpoint_ready(host, tcp_port)
    running = pid_is_running(snapshot.get("pid")) or reachable

    serial_connected_value = snapshot.get("serial_connected")
    serial_connected = serial_connected_value if isinstance(serial_connected_value, bool) else None
    if not running:
        connection_status = "stopped"
    elif serial_connected is True:
        connection_status = "connected"
    elif serial_connected is False:
        connection_status = "disconnected"
    else:
        connection_status = "unknown"

    config_file_value = snapshot.get("config_file")
    config_file = str(config_file_value) if isinstance(config_file_value, str) and config_file_value.strip() else None
    persistent_config_file = str(service_config_file) if service_config_file.exists() else None

    return {
        "running": running,
        "reachable": reachable,
        "connection_status": connection_status,
        "host": host,
        "tcp_port": tcp_port,
        "pid": snapshot.get("pid"),
        "snapshot": snapshot,
        "snapshot_exists": status_file.exists(),
        "snapshot_file": str(status_file),
        "config_file": config_file,
        "config_file_loaded": config_file is not None,
        "persistent_config_file": persistent_config_file,
    }


def detect_proxy_target(status_file: Path | None = None) -> MeshtasticTarget | None:
    status_file = status_file or PROXY_STATUS_FILE
    status = load_proxy_status(status_file)
    host = normalize_tcp_client_host(env_proxy_host() or status.get("listen_host"))
    port = status.get("listen_port")
    if isinstance(port, int):
        tcp_port = port
    else:
        tcp_port = env_tcp_port()

    if tcp_endpoint_ready(host, tcp_port):
        return MeshtasticTarget(mode="tcp", source="local-proxy", host=host, tcp_port=tcp_port)
    return None


def _resolve_meshtastic_target_with_details(
    port: str = "",
    host: str = "",
    tcp_port: int | None = None,
    status_file: Path | None = None,
) -> tuple[MeshtasticTarget, dict[str, object]]:
    status_file = status_file or PROXY_STATUS_FILE
    serial_port = port or env_serial_port()
    resolved_tcp_port = tcp_port if tcp_port is not None else env_tcp_port()
    explicit_host = normalize_tcp_client_host(host or env_host_override())
    details: dict[str, object] = {
        "requested": {
            "port": port,
            "host": host,
            "tcp_port": tcp_port,
        },
        "env": {
            "MESHTASTIC_PORT": os.environ.get("MESHTASTIC_PORT", ""),
            "MESHTASTIC_HOST": env_host_override(),
            "MESHTASTIC_TCP_PORT": os.environ.get("MESHTASTIC_TCP_PORT", ""),
            "MESHTASTIC_PROXY_HOST": env_proxy_host(),
        },
        "proxy_status_file": str(status_file),
        "checks": [],
        "serial_fallback": serial_port,
    }

    if explicit_host:
        target = MeshtasticTarget(
            mode="tcp",
            source="explicit-host" if host else "env-host",
            host=explicit_host,
            tcp_port=resolved_tcp_port,
        )
        details["checks"].append(
            "Using explicit --host override." if host else "Using MESHTASTIC_HOST environment override."
        )
        return target, details

    status = load_proxy_status(status_file)
    details["proxy_snapshot_exists"] = status_file.exists()
    if status:
        details["proxy_snapshot"] = status

    proxy_host = normalize_tcp_client_host(env_proxy_host() or status.get("listen_host"))
    listen_port = status.get("listen_port")
    proxy_port = listen_port if isinstance(listen_port, int) else resolved_tcp_port
    proxy_reachable = tcp_endpoint_ready(proxy_host, proxy_port)
    details["proxy_candidate"] = {"host": proxy_host, "tcp_port": proxy_port}
    details["proxy_reachable"] = proxy_reachable

    if proxy_reachable:
        target = MeshtasticTarget(mode="tcp", source="local-proxy", host=proxy_host, tcp_port=proxy_port)
        details["checks"].append(f"Using healthy local proxy or broker at {proxy_host}:{proxy_port}.")
        return target, details

    target = MeshtasticTarget(mode="serial", source="serial-fallback", serial_port=serial_port, tcp_port=resolved_tcp_port)
    details["checks"].append(f"Falling back to direct serial access on {serial_port}.")
    return target, details


def resolve_meshtastic_target(
    port: str = "",
    host: str = "",
    tcp_port: int | None = None,
    status_file: Path | None = None,
) -> MeshtasticTarget:
    target, _ = _resolve_meshtastic_target_with_details(port=port, host=host, tcp_port=tcp_port, status_file=status_file)
    return target


def explain_meshtastic_target(
    port: str = "",
    host: str = "",
    tcp_port: int | None = None,
    status_file: Path | None = None,
) -> dict[str, object]:
    target, details = _resolve_meshtastic_target_with_details(port=port, host=host, tcp_port=tcp_port, status_file=status_file)
    details["selected"] = {
        "mode": target.mode,
        "source": target.source,
        "serial_port": target.serial_port,
        "host": target.host,
        "tcp_port": target.tcp_port,
        "label": target.label,
    }
    return details
