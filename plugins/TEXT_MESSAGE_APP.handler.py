import json


MESSAGE_LOG = "messages.jsonl"
MAX_HISTORY_MESSAGES = 256


def handle_packet(event, api):
    if event.get("plugin_origin_likely"):
        return
    record = {
        "direct": bool(event.get("packet_to")),
        "packet_from": event.get("packet_from"),
        "packet_id": event.get("packet_id"),
        "packet_to": event.get("packet_to"),
        "payload_hex": bytes(event["payload"]).hex(),
        "payload_text": bytes(event["payload"]).decode("utf-8", errors="replace"),
        "ts": api["time"](),
    }
    api["plugin_store_append_jsonl"](api["plugin_name"], MESSAGE_LOG, record)
    records = api["plugin_store_read_jsonl"](api["plugin_name"], MESSAGE_LOG)
    if len(records) > MAX_HISTORY_MESSAGES:
        trimmed = records[-MAX_HISTORY_MESSAGES:]
        path = api["plugin_store_path"](api["plugin_name"], MESSAGE_LOG)
        with open(path, "w", encoding="utf-8") as handle:
            for item in trimmed:
                handle.write(json.dumps(item, sort_keys=True))
                handle.write("\n")
