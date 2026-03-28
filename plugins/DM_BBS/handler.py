from pathlib import Path
import importlib.util
import sys


def _load_shared():
    plugin_path = Path(__file__).resolve().parent / "_shared.py"
    module_name = "_dm_bbs_shared"
    module = sys.modules.get(module_name)
    if module is not None and getattr(module, "__file__", None) == str(plugin_path):
        return module
    spec = importlib.util.spec_from_file_location(module_name, plugin_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load {plugin_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


shared = _load_shared()


def handle_packet(event, api):
    if event.get("plugin_origin_likely"):
        return
    shared.send_text_reply(event, api, shared.handle_command(event, api))
