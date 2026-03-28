import json
from pathlib import Path
import time


def _repo_root(api) -> Path:
    return Path(api["plugin_path"]).resolve().parents[2]


def _bbs_dir(api) -> Path:
    return _repo_root(api) / "data" / "bbs"


def _users_dir(api) -> Path:
    path = _bbs_dir(api) / "users"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _sender_key(event) -> str | None:
    packet_from = int(event.get("packet_from") or 0)
    if packet_from <= 0:
        return None
    return str(packet_from)


def _sender_label(event) -> str:
    short_name = str(event.get("sender_short_name") or "").strip()
    if short_name:
        return short_name
    packet_from = int(event.get("packet_from") or 0)
    return f"!{packet_from:08x}" if packet_from > 0 else "unknown"


def _upsert_user(event, api) -> None:
    sender_key = _sender_key(event)
    if sender_key is None:
        return

    path = _users_dir(api) / f"{sender_key}.json"
    now = int(time.time())
    record = {}
    if path.exists():
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            record = {}
    record.update(
        {
            "first_seen_at": int(record.get("first_seen_at") or now),
            "last_seen_at": now,
            "node_num": int(event.get("packet_from") or 0),
            "sender_short_name": str(event.get("sender_short_name") or "").strip() or None,
        }
    )
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _banner_text(event, api) -> str:
    bbs_name = str(event.get("local_short_name") or "").strip() or "BBS"
    bbs_dir = _bbs_dir(api)
    user_count = len(list((bbs_dir / "users").glob("*.json"))) if (bbs_dir / "users").exists() else 0
    return "\n".join(
        [
            f"{bbs_name} BBS",
            f"Status: online | users: {user_count}",
            f"Caller: {_sender_label(event)}",
            "",
            "Commands:",
            "hello  show this page",
            "",
            "More BBS features will be added next.",
        ]
    )


def handle_packet(event, api):
    if event.get("plugin_origin_likely"):
        return

    _upsert_user(event, api)

    packet = api["mesh_pb2"].MeshPacket()
    packet.to = int(event.get("packet_from") or 0)
    packet.channel = 0
    packet.decoded.portnum = api["portnums_pb2"].TEXT_MESSAGE_APP
    packet.decoded.payload = _banner_text(event, api).encode("utf-8")
    packet.decoded.want_response = False
    api["send_mesh_packet"](packet)
