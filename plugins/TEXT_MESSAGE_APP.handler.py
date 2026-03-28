import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


EVENT_LOG_DIR = "events"
MESSAGE_BLOB_DIR = "messages"


def _message_hash(record):
    stable_record = {
        "direct": record["direct"],
        "packet_from": record["packet_from"],
        "packet_id": record["packet_id"],
        "packet_to": record["packet_to"],
        "payload_hex": record["payload_hex"],
        "payload_text": record["payload_text"],
    }
    return hashlib.sha256(json.dumps(stable_record, sort_keys=True).encode("utf-8")).hexdigest()


def _message_dir(api):
    return Path(api["plugin_store_path"](api["plugin_name"], MESSAGE_BLOB_DIR))


def _event_log_path(api, ts):
    date_key = datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d")
    return Path(api["plugin_store_path"](api["plugin_name"], f"{EVENT_LOG_DIR}/{date_key}.jsonl"))


def _message_path(api, message_hash):
    return _message_dir(api) / f"{message_hash}.json"


def _atomic_write_json(path, payload):
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    temp_path.replace(path)


def _read_existing_record(path, api):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        api["logger"].warning("plugin %s skipped malformed blob %s: %s", api["plugin_name"], path, exc)
        return None


def handle_packet(event, api):
    if event.get("plugin_origin_likely"):
        return

    now_ts = api["time"]()
    record = {
        "direct": bool(event.get("packet_to")),
        "first_seen_ts": now_ts,
        "last_seen_ts": now_ts,
        "packet_from": event.get("packet_from"),
        "packet_id": event.get("packet_id"),
        "packet_to": event.get("packet_to"),
        "payload_hex": bytes(event["payload"]).hex(),
        "payload_text": bytes(event["payload"]).decode("utf-8", errors="replace"),
        "seen_count": 1,
    }
    message_hash = _message_hash(record)
    record["hash"] = message_hash

    path = _message_path(api, message_hash)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = _read_existing_record(path, api)
        if existing is not None:
            record["first_seen_ts"] = existing.get("first_seen_ts", now_ts)
            record["seen_count"] = int(existing.get("seen_count", 0)) + 1
    _atomic_write_json(path, record)

    api["plugin_store_append_jsonl"](
        api["plugin_name"],
        str(_event_log_path(api, now_ts).relative_to(Path(api["plugin_store_path"](api["plugin_name"])))),
        {
            "hash": message_hash,
            "packet_from": event.get("packet_from"),
            "packet_id": event.get("packet_id"),
            "packet_to": event.get("packet_to"),
            "ts": now_ts,
        },
    )
