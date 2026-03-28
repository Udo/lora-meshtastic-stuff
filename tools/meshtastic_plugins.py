#!/usr/bin/env python3
import argparse
import importlib.util
import json
import logging
import os
from pathlib import Path
import re
import sys
import threading
import time
from dataclasses import dataclass
from types import ModuleType


LOGGER = logging.getLogger("meshtastic_plugins")
PLUGIN_SUFFIX = ".handler.py"
PRIVATE_SUBTYPE_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass
class LoadedPlugin:
    path: Path
    module: ModuleType
    mtime_ns: int


class MeshtasticPluginManager:
    def __init__(self, plugins_dir: str | os.PathLike[str], logger: logging.Logger | None = None) -> None:
        self.plugins_dir = Path(plugins_dir)
        self.logger = logger or LOGGER
        self._lock = threading.Lock()
        self._loaded: dict[Path, LoadedPlugin] = {}

    def plugin_names(self) -> list[str]:
        with self._lock:
            return sorted(loaded.path.name for loaded in self._loaded.values() if loaded.path.exists())

    def dispatch_packet(self, portnum_name: str | None, portnum: int | None, event: dict[str, object], api: dict[str, object]) -> None:
        for plugin in self._plugins_for_port(portnum_name, portnum, event):
            handler = getattr(plugin.module, "handle_packet", None)
            if callable(handler):
                self._call(plugin.path, "handle_packet", handler, event, api)

    def dispatch_client_call(self, portnum_name: str | None, portnum: int | None, event: dict[str, object], api: dict[str, object]) -> dict[str, object]:
        for plugin in self._plugins_for_port(portnum_name, portnum, event):
            handler = getattr(plugin.module, "handle_client_call", None)
            if callable(handler):
                self._call(plugin.path, "handle_client_call", handler, event, api)
        return event

    def tick(self, api: dict[str, object]) -> None:
        for plugin in self._all_plugins():
            tick = getattr(plugin.module, "tick", None)
            if callable(tick):
                self._call(plugin.path, "tick", tick, {"event_type": "tick", "ts": time.time()}, api)

    def _all_plugins(self) -> list[LoadedPlugin]:
        self._prune_deleted_plugins()
        if not self.plugins_dir.exists():
            return []
        paths = sorted(self.plugins_dir.glob(f"*{PLUGIN_SUFFIX}"))
        return [plugin for path in paths if (plugin := self._load_plugin(path)) is not None]

    def _plugins_for_port(self, portnum_name: str | None, portnum: int | None, event: dict[str, object] | None = None) -> list[LoadedPlugin]:
        self._prune_deleted_plugins()
        candidates = self._candidate_paths(portnum_name, portnum, event)

        loaded: list[LoadedPlugin] = []
        seen: set[Path] = set()
        for path in candidates:
            if path in seen or not path.exists():
                continue
            seen.add(path)
            plugin = self._load_plugin(path)
            if plugin is not None:
                loaded.append(plugin)
        return loaded

    def _candidate_paths(self, portnum_name: str | None, portnum: int | None, event: dict[str, object] | None) -> list[Path]:
        candidates: list[Path] = []
        if portnum_name == "PRIVATE_APP":
            subtype = self._private_subtype(event)
            if subtype:
                typed_path = self.plugins_dir / f"PRIVATE_APP.{subtype}{PLUGIN_SUFFIX}"
                if typed_path.exists():
                    candidates.append(typed_path)
                else:
                    candidates.append(self.plugins_dir / f"PRIVATE_APP{PLUGIN_SUFFIX}")
            else:
                candidates.append(self.plugins_dir / f"PRIVATE_APP{PLUGIN_SUFFIX}")
            if portnum is not None:
                candidates.append(self.plugins_dir / f"{portnum}{PLUGIN_SUFFIX}")
            return candidates

        if portnum_name:
            candidates.append(self.plugins_dir / f"{portnum_name}{PLUGIN_SUFFIX}")
        if portnum is not None:
            candidates.append(self.plugins_dir / f"{portnum}{PLUGIN_SUFFIX}")
        return candidates

    def _private_subtype(self, event: dict[str, object] | None) -> str | None:
        if not event:
            return None
        payload = event.get("payload")
        if not isinstance(payload, (bytes, bytearray)):
            return None
        try:
            text = bytes(payload).decode("utf-8").strip()
        except UnicodeDecodeError:
            return None
        if not text:
            return None

        subtype = self._private_subtype_from_json(text)
        if subtype:
            return subtype
        return self._private_subtype_from_simple_text(text)

    def _private_subtype_from_json(self, text: str) -> str | None:
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(value, dict):
            return None
        subtype = value.get("type")
        if not isinstance(subtype, str):
            return None
        return subtype if PRIVATE_SUBTYPE_RE.fullmatch(subtype) else None

    def _private_subtype_from_simple_text(self, text: str) -> str | None:
        first_line = text.splitlines()[0].strip()
        if first_line.startswith("type="):
            subtype = first_line[5:].strip()
            return subtype if PRIVATE_SUBTYPE_RE.fullmatch(subtype) else None
        return None

    def _prune_deleted_plugins(self) -> None:
        with self._lock:
            deleted = [path for path in self._loaded if not path.exists()]
            for path in deleted:
                self._loaded.pop(path, None)

    def _load_plugin(self, path: Path) -> LoadedPlugin | None:
        try:
            stat = path.stat()
        except OSError as exc:
            self.logger.exception("plugin stat failed for %s: %s", path, exc)
            return None

        with self._lock:
            loaded = self._loaded.get(path)
            if loaded is not None and loaded.mtime_ns == stat.st_mtime_ns:
                return loaded

        module_name = f"meshtastic_plugin_{path.stem.replace('.', '_')}_{abs(hash(path.resolve()))}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ImportError(f"could not create loader for {path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception:
            self.logger.exception("plugin load failed for %s", path)
            return None

        loaded = LoadedPlugin(path=path, module=module, mtime_ns=stat.st_mtime_ns)
        with self._lock:
            self._loaded[path] = loaded
        self.logger.info("plugin loaded: %s", path.name)
        return loaded

    def _call(
        self,
        path: Path,
        function_name: str,
        handler,
        event: dict[str, object],
        api: dict[str, object],
    ) -> None:
        plugin_api = dict(api)
        plugin_api["plugin_name"] = path.name[: -len(PLUGIN_SUFFIX)]
        plugin_api["plugin_path"] = str(path)
        try:
            handler(event, plugin_api)
        except Exception:
            self.logger.exception("plugin handler failed: %s:%s", path.name, function_name)


def plugin_storage_path(runtime_dir: str | os.PathLike[str], plugin_name: str, relative_path: str = "") -> Path:
    runtime_path = Path(runtime_dir)
    plugin_dir = (runtime_path / "plugins" / plugin_name).resolve()
    plugin_dir.mkdir(parents=True, exist_ok=True)
    target = (plugin_dir / relative_path).resolve() if relative_path else plugin_dir
    if plugin_dir not in target.parents and target != plugin_dir:
        raise ValueError(f"plugin storage path escapes plugin directory: {relative_path}")
    return target


def plugin_store_append_jsonl(runtime_dir: str | os.PathLike[str], plugin_name: str, relative_path: str, record: dict[str, object]) -> str:
    path = plugin_storage_path(runtime_dir, plugin_name, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True))
        handle.write("\n")
    return str(path)


def plugin_store_read_jsonl(runtime_dir: str | os.PathLike[str], plugin_name: str, relative_path: str, limit: int | None = None) -> list[dict[str, object]]:
    path = plugin_storage_path(runtime_dir, plugin_name, relative_path)
    if not path.exists():
        return []
    records: list[dict[str, object]] = []
    with open(path, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                LOGGER.warning(
                    "plugin storage skipped malformed jsonl line %s:%s: %s",
                    path,
                    line_number,
                    exc,
                )
                continue
            if not isinstance(record, dict):
                LOGGER.warning(
                    "plugin storage skipped non-object jsonl record %s:%s",
                    path,
                    line_number,
                )
                continue
            records.append(record)
    if limit is not None and limit >= 0:
        return records[-limit:]
    return records


def load_plugin_module(plugins_dir: str | os.PathLike[str], plugin_name: str) -> tuple[Path, ModuleType]:
    path = Path(plugins_dir) / f"{plugin_name}{PLUGIN_SUFFIX}"
    if not path.exists():
        raise FileNotFoundError(f"plugin not found: {path}")

    spec = importlib.util.spec_from_file_location(f"meshtastic_plugin_cli_{plugin_name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not create loader for {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return path, module


def build_cli_api(plugin_name: str, plugin_path: Path, runtime_dir: str | os.PathLike[str]) -> dict[str, object]:
    from meshtastic.protobuf import mesh_pb2, portnums_pb2, storeforward_pb2

    return {
        "logger": LOGGER,
        "mesh_pb2": mesh_pb2,
        "plugin_name": plugin_name,
        "plugin_path": str(plugin_path),
        "plugin_store_append_jsonl": lambda target_plugin, relative_path, record: plugin_store_append_jsonl(runtime_dir, target_plugin, relative_path, record),
        "plugin_store_path": lambda target_plugin, relative_path="": str(plugin_storage_path(runtime_dir, target_plugin, relative_path)),
        "plugin_store_read_jsonl": lambda target_plugin, relative_path, limit=None: plugin_store_read_jsonl(runtime_dir, target_plugin, relative_path, limit),
        "portnums_pb2": portnums_pb2,
        "storeforward_pb2": storeforward_pb2,
        "time": time.time,
    }


def build_cli_parser() -> argparse.ArgumentParser:
    root_dir = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Run self-contained Meshtastic proxy plugin utilities")
    parser.add_argument("plugin_name", help="Plugin name without .handler.py, for example STORE_FORWARD_APP")
    parser.add_argument("plugin_args", nargs=argparse.REMAINDER, help="Arguments forwarded to the plugin tool")
    parser.add_argument("--plugins-dir", default=str(root_dir / "plugins"), help="Directory containing *.handler.py plugin files")
    parser.add_argument("--runtime-dir", default=str(root_dir / ".runtime" / "meshtastic"), help="Runtime directory containing plugin state")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_cli_parser()
    args = parser.parse_args(argv)

    try:
        plugin_path, module = load_plugin_module(args.plugins_dir, args.plugin_name)
    except (FileNotFoundError, ImportError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    plugin_command = getattr(module, "plugin_command", None)
    if not callable(plugin_command):
        print(f"plugin {args.plugin_name} does not expose plugin_command(argv, api)", file=sys.stderr)
        return 2

    api = build_cli_api(args.plugin_name, plugin_path, args.runtime_dir)
    plugin_command(args.plugin_args, api)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
