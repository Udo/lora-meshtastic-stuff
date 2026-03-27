import importlib.util
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType


LOGGER = logging.getLogger("meshtastic_plugins")
PLUGIN_SUFFIX = ".handler.py"


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
        for plugin in self._plugins_for_port(portnum_name, portnum):
            handler = getattr(plugin.module, "handle_packet", None)
            if callable(handler):
                self._call(plugin.path, "handle_packet", handler, event, api)

    def dispatch_client_call(self, portnum_name: str | None, portnum: int | None, event: dict[str, object], api: dict[str, object]) -> dict[str, object]:
        for plugin in self._plugins_for_port(portnum_name, portnum):
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

    def _plugins_for_port(self, portnum_name: str | None, portnum: int | None) -> list[LoadedPlugin]:
        self._prune_deleted_plugins()
        candidates: list[Path] = []
        if portnum_name:
            candidates.append(self.plugins_dir / f"{portnum_name}{PLUGIN_SUFFIX}")
        if portnum is not None:
            candidates.append(self.plugins_dir / f"{portnum}{PLUGIN_SUFFIX}")

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
