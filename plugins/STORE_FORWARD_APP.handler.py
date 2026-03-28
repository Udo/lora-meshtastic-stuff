import argparse
import json
from datetime import UTC, datetime
from pathlib import Path


DEFAULT_HISTORY_WINDOW_MINUTES = 60
DEFAULT_RETURN_MAX = 20
RETENTION_DAYS = 30
REQUEST_LOG = "requests.jsonl"
TEXT_PLUGIN_NAME = "TEXT_MESSAGE_APP"
TEXT_EVENT_DIR = "events"
TEXT_MESSAGE_DIR = "messages"
CLEANUP_STATE = "cleanup.json"
CONFIG_FILE = "config.json"
DEFAULT_REPLAY_DUPLICATES = False
HEARTBEAT_STATE = "heartbeat.json"
DEFAULT_HEARTBEAT_ENABLED = True
DEFAULT_HEARTBEAT_INTERVAL_SECS = 3600
DEFAULT_HEARTBEAT_SECONDARY = False


def _message_dir(api):
    return Path(api["plugin_store_path"](TEXT_PLUGIN_NAME, TEXT_MESSAGE_DIR))


def _event_dir(api):
    return Path(api["plugin_store_path"](TEXT_PLUGIN_NAME, TEXT_EVENT_DIR))


def _request_log_path(api):
    return Path(api["plugin_store_path"](api["plugin_name"], REQUEST_LOG))


def _cleanup_state_path(api):
    return Path(api["plugin_store_path"](api["plugin_name"], CLEANUP_STATE))


def _config_path(api):
    return Path(api["plugin_store_path"](api["plugin_name"], CONFIG_FILE))


def _heartbeat_state_path(api):
    return Path(api["plugin_store_path"](api["plugin_name"], HEARTBEAT_STATE))


def _atomic_write_json(path, payload):
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    temp_path.replace(path)


def _atomic_write_jsonl(path, records):
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with open(temp_path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True))
            handle.write("\n")
    temp_path.replace(path)


def _safe_read_json(path, api, *, context):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        api["logger"].warning("plugin %s skipped malformed %s %s: %s", api["plugin_name"], context, path, exc)
        return None


def _safe_read_jsonl(plugin_name, relative_path, api):
    records = []
    for raw in api["plugin_store_read_jsonl"](plugin_name, relative_path):
        if isinstance(raw, dict):
            records.append(raw)
        else:
            api["logger"].warning("plugin %s skipped non-object record in %s/%s", api["plugin_name"], plugin_name, relative_path)
    return records


def _iter_recent_event_paths(api):
    cutoff_dt = datetime.fromtimestamp(api["time"]() - (RETENTION_DAYS * 24 * 60 * 60), UTC).date()
    event_dir = _event_dir(api)
    if not event_dir.exists():
        return []
    paths = []
    for path in sorted(event_dir.glob("*.jsonl")):
        try:
            file_date = datetime.strptime(path.stem, "%Y-%m-%d").date()
        except ValueError:
            api["logger"].warning("plugin %s skipped malformed event log filename %s", api["plugin_name"], path.name)
            continue
        if file_date < cutoff_dt:
            continue
        paths.append(path)
    return paths


def _load_config(api):
    path = _config_path(api)
    if not path.exists():
        return {
            "heartbeat_enabled": DEFAULT_HEARTBEAT_ENABLED,
            "heartbeat_interval_secs": DEFAULT_HEARTBEAT_INTERVAL_SECS,
            "heartbeat_secondary": DEFAULT_HEARTBEAT_SECONDARY,
            "replay_duplicates": DEFAULT_REPLAY_DUPLICATES,
        }
    config = _safe_read_json(path, api, context="config")
    if not isinstance(config, dict):
        return {
            "heartbeat_enabled": DEFAULT_HEARTBEAT_ENABLED,
            "heartbeat_interval_secs": DEFAULT_HEARTBEAT_INTERVAL_SECS,
            "heartbeat_secondary": DEFAULT_HEARTBEAT_SECONDARY,
            "replay_duplicates": DEFAULT_REPLAY_DUPLICATES,
        }
    heartbeat_enabled = config.get("heartbeat_enabled", DEFAULT_HEARTBEAT_ENABLED)
    if not isinstance(heartbeat_enabled, bool):
        api["logger"].warning(
            "plugin %s skipped invalid heartbeat_enabled flag in %s: %r",
            api["plugin_name"],
            path,
            heartbeat_enabled,
        )
        heartbeat_enabled = DEFAULT_HEARTBEAT_ENABLED
    heartbeat_secondary = config.get("heartbeat_secondary", DEFAULT_HEARTBEAT_SECONDARY)
    if not isinstance(heartbeat_secondary, bool):
        api["logger"].warning(
            "plugin %s skipped invalid heartbeat_secondary flag in %s: %r",
            api["plugin_name"],
            path,
            heartbeat_secondary,
        )
        heartbeat_secondary = DEFAULT_HEARTBEAT_SECONDARY
    heartbeat_interval_secs = config.get("heartbeat_interval_secs", DEFAULT_HEARTBEAT_INTERVAL_SECS)
    try:
        heartbeat_interval_secs = int(heartbeat_interval_secs)
    except (TypeError, ValueError):
        api["logger"].warning(
            "plugin %s skipped invalid heartbeat_interval_secs in %s: %r",
            api["plugin_name"],
            path,
            heartbeat_interval_secs,
        )
        heartbeat_interval_secs = DEFAULT_HEARTBEAT_INTERVAL_SECS
    if heartbeat_interval_secs <= 0:
        api["logger"].warning(
            "plugin %s skipped non-positive heartbeat_interval_secs in %s: %r",
            api["plugin_name"],
            path,
            heartbeat_interval_secs,
        )
        heartbeat_interval_secs = DEFAULT_HEARTBEAT_INTERVAL_SECS
    replay_duplicates = config.get("replay_duplicates", DEFAULT_REPLAY_DUPLICATES)
    if not isinstance(replay_duplicates, bool):
        api["logger"].warning(
            "plugin %s skipped invalid replay_duplicates flag in %s: %r",
            api["plugin_name"],
            path,
            replay_duplicates,
        )
        replay_duplicates = DEFAULT_REPLAY_DUPLICATES
    return {
        "heartbeat_enabled": heartbeat_enabled,
        "heartbeat_interval_secs": heartbeat_interval_secs,
        "heartbeat_secondary": heartbeat_secondary,
        "replay_duplicates": replay_duplicates,
    }


def _load_history(api):
    history = []
    for event_path in _iter_recent_event_paths(api):
        try:
            lines = event_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            api["logger"].warning("plugin %s could not read event log %s: %s", api["plugin_name"], event_path, exc)
            continue
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                event_record = json.loads(line)
            except json.JSONDecodeError as exc:
                api["logger"].warning(
                    "plugin %s skipped malformed event log line %s:%s: %s",
                    api["plugin_name"],
                    event_path,
                    line_number,
                    exc,
                )
                continue
            if not isinstance(event_record, dict):
                api["logger"].warning(
                    "plugin %s skipped non-object event record %s:%s",
                    api["plugin_name"],
                    event_path,
                    line_number,
                )
                continue
            blob_hash = event_record.get("hash")
            if not isinstance(blob_hash, str) or not blob_hash:
                api["logger"].warning(
                    "plugin %s skipped event without hash %s:%s",
                    api["plugin_name"],
                    event_path,
                    line_number,
                )
                continue
            blob = _safe_read_json(_message_dir(api) / f"{blob_hash}.json", api, context="message blob")
            if not isinstance(blob, dict):
                continue
            if blob.get("payload_text") is None:
                continue
            history.append(
                {
                    "direct": bool(blob.get("direct")),
                    "hash": blob_hash,
                    "last_seen_ts": float(event_record.get("ts", 0)),
                    "payload_text": blob.get("payload_text"),
                }
            )
    history.sort(key=lambda item: (float(item.get("last_seen_ts", 0)), item.get("hash", "")))
    if _load_config(api).get("replay_duplicates"):
        return history
    unique_history = []
    seen_hashes = set()
    for item in history:
        item_hash = item.get("hash")
        if item_hash in seen_hashes:
            continue
        seen_hashes.add(item_hash)
        unique_history.append(item)
    return unique_history


def _build_stats(api, history, requests, requests_history):
    config = _load_config(api)
    response = api["storeforward_pb2"].StoreAndForward(rr=api["storeforward_pb2"].StoreAndForward.ROUTER_STATS)
    response.stats.messages_total = len(history)
    response.stats.messages_saved = len(history)
    response.stats.messages_max = 0
    response.stats.up_time = 0
    response.stats.requests = requests
    response.stats.requests_history = requests_history
    response.stats.heartbeat = bool(config.get("heartbeat_enabled"))
    response.stats.return_max = DEFAULT_RETURN_MAX
    response.stats.return_window = DEFAULT_HISTORY_WINDOW_MINUTES
    return response


def _filtered_history(history, now_ts, window_minutes, last_request):
    cutoff = now_ts - (window_minutes * 60)
    entries = []
    for index, entry in enumerate(history, start=1):
        if window_minutes > 0 and float(entry.get("last_seen_ts", 0)) < cutoff:
            continue
        if last_request and index <= last_request:
            continue
        entries.append((index, entry))
    return entries


def _append_stat(api, key):
    api["plugin_store_append_jsonl"](
        api["plugin_name"],
        REQUEST_LOG,
        {"key": key, "ts": api["time"]()},
    )


def _request_counts(api):
    cutoff = api["time"]() - (RETENTION_DAYS * 24 * 60 * 60)
    requests = 0
    requests_history = 0
    for record in _safe_read_jsonl(api["plugin_name"], REQUEST_LOG, api):
        ts = record.get("ts", 0)
        try:
            if float(ts) < cutoff:
                continue
        except (TypeError, ValueError):
            api["logger"].warning("plugin %s skipped request record with invalid ts: %r", api["plugin_name"], record)
            continue
        if record.get("key") == "request":
            requests += 1
        elif record.get("key") == "history":
            requests_history += 1
    return requests, requests_history


def _handle_request(request, event, api):
    history = _load_history(api)

    if request.rr == api["storeforward_pb2"].StoreAndForward.CLIENT_PING:
        _append_stat(api, "request")
        response = api["storeforward_pb2"].StoreAndForward(rr=api["storeforward_pb2"].StoreAndForward.ROUTER_PONG)
        api["reply_app"](event, payload=response.SerializeToString(), portnum=api["portnums_pb2"].STORE_FORWARD_APP)
        return True

    if request.rr == api["storeforward_pb2"].StoreAndForward.CLIENT_STATS:
        _append_stat(api, "request")
        requests, requests_history = _request_counts(api)
        response = _build_stats(api, history, requests, requests_history)
        api["reply_app"](event, payload=response.SerializeToString(), portnum=api["portnums_pb2"].STORE_FORWARD_APP)
        return True

    if request.rr != api["storeforward_pb2"].StoreAndForward.CLIENT_HISTORY:
        return False

    _append_stat(api, "request")
    _append_stat(api, "history")

    window = request.history.window or DEFAULT_HISTORY_WINDOW_MINUTES
    history_messages = request.history.history_messages or DEFAULT_RETURN_MAX
    last_request = request.history.last_request
    entries = _filtered_history(history, api["time"](), window, last_request)[:history_messages]

    summary = api["storeforward_pb2"].StoreAndForward(rr=api["storeforward_pb2"].StoreAndForward.ROUTER_HISTORY)
    summary.history.history_messages = len(entries)
    summary.history.window = window
    summary.history.last_request = entries[-1][0] if entries else last_request
    api["reply_app"](event, payload=summary.SerializeToString(), portnum=api["portnums_pb2"].STORE_FORWARD_APP)

    for _, entry in entries:
        rr = (
            api["storeforward_pb2"].StoreAndForward.ROUTER_TEXT_DIRECT
            if entry.get("direct")
            else api["storeforward_pb2"].StoreAndForward.ROUTER_TEXT_BROADCAST
        )
        response = api["storeforward_pb2"].StoreAndForward(rr=rr, text=entry["payload_text"].encode("utf-8"))
        api["reply_app"](event, payload=response.SerializeToString(), portnum=api["portnums_pb2"].STORE_FORWARD_APP)
    return True


def _cleanup_old_history(api):
    cutoff_dt = datetime.fromtimestamp(api["time"]() - (RETENTION_DAYS * 24 * 60 * 60), UTC).date()
    event_dir = _event_dir(api)
    message_dir = _message_dir(api)
    removed_events = 0
    referenced_hashes = set()

    if event_dir.exists():
        for path in sorted(event_dir.glob("*.jsonl")):
            try:
                file_date = datetime.strptime(path.stem, "%Y-%m-%d").date()
            except ValueError:
                api["logger"].warning("plugin %s skipped malformed event log filename %s", api["plugin_name"], path.name)
                continue
            if file_date < cutoff_dt:
                path.unlink(missing_ok=True)
                removed_events += 1
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError as exc:
                api["logger"].warning("plugin %s could not read event log %s during cleanup: %s", api["plugin_name"], path, exc)
                continue
            for line in lines:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict) and isinstance(record.get("hash"), str):
                    referenced_hashes.add(record["hash"])

    removed_blobs = 0
    if message_dir.exists():
        for path in message_dir.glob("*.json"):
            if path.stem in referenced_hashes:
                continue
            path.unlink(missing_ok=True)
            removed_blobs += 1
    return removed_events, removed_blobs


def _cleanup_old_requests(api):
    cutoff = api["time"]() - (RETENTION_DAYS * 24 * 60 * 60)
    path = _request_log_path(api)
    if not path.exists():
        return 0
    records = []
    for record in _safe_read_jsonl(api["plugin_name"], REQUEST_LOG, api):
        try:
            if float(record.get("ts", 0)) < cutoff:
                continue
        except (TypeError, ValueError):
            continue
        records.append(record)
    _atomic_write_jsonl(path, records)
    return len(records)


def _run_cleanup(api):
    removed_event_logs, removed_blobs = _cleanup_old_history(api)
    kept_requests = _cleanup_old_requests(api)
    state_path = _cleanup_state_path(api)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(
        state_path,
        {
            "last_cleanup_ts": api["time"](),
            "removed_blobs": removed_blobs,
            "removed_event_logs": removed_event_logs,
            "retained_days": RETENTION_DAYS,
            "retained_requests": kept_requests,
        },
    )


def _heartbeat_due(api):
    config = _load_config(api)
    if not config.get("heartbeat_enabled"):
        return False, config, None
    state_path = _heartbeat_state_path(api)
    last_sent_ts = 0.0
    if state_path.exists():
        state = _safe_read_json(state_path, api, context="heartbeat state")
        if isinstance(state, dict):
            try:
                last_sent_ts = float(state.get("last_sent_ts", 0))
            except (TypeError, ValueError):
                api["logger"].warning(
                    "plugin %s skipped invalid heartbeat state timestamp in %s: %r",
                    api["plugin_name"],
                    state_path,
                    state.get("last_sent_ts"),
                )
                last_sent_ts = 0.0
    due = (api["time"]() - last_sent_ts) >= int(config["heartbeat_interval_secs"])
    return due, config, last_sent_ts


def _emit_heartbeat(api, config):
    heartbeat = api["storeforward_pb2"].StoreAndForward(rr=api["storeforward_pb2"].StoreAndForward.ROUTER_HEARTBEAT)
    heartbeat.heartbeat.period = int(config["heartbeat_interval_secs"])
    heartbeat.heartbeat.secondary = 1 if config.get("heartbeat_secondary") else 0
    api["send_app"](
        destination=0,
        portnum=api["portnums_pb2"].STORE_FORWARD_APP,
        payload=heartbeat.SerializeToString(),
        want_response=False,
    )
    state_path = _heartbeat_state_path(api)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(
        state_path,
        {
            "last_sent_ts": api["time"](),
            "period": int(config["heartbeat_interval_secs"]),
            "secondary": bool(config.get("heartbeat_secondary")),
        },
    )


def tick(event, api):
    break_cleanup = False
    state_path = _cleanup_state_path(api)
    if state_path.exists():
        state = _safe_read_json(state_path, api, context="cleanup state")
        if isinstance(state, dict):
            try:
                last_cleanup_ts = float(state.get("last_cleanup_ts", 0))
            except (TypeError, ValueError):
                api["logger"].warning(
                    "plugin %s skipped invalid cleanup state timestamp in %s: %r",
                    api["plugin_name"],
                    state_path,
                    state.get("last_cleanup_ts"),
                )
            else:
                if api["time"]() - last_cleanup_ts < 24 * 60 * 60:
                    break_cleanup = True
                else:
                    break_cleanup = False
        else:
            break_cleanup = False
    else:
        break_cleanup = False
    if not break_cleanup:
        _run_cleanup(api)
    heartbeat_due, config, _ = _heartbeat_due(api)
    if heartbeat_due:
        _emit_heartbeat(api, config)


def handle_client_call(event, api):
    request = api["storeforward_pb2"].StoreAndForward()
    request.ParseFromString(event["payload"])
    if _handle_request(request, event, api):
        event["consume"] = True


def handle_packet(event, api):
    if event.get("plugin_origin_likely"):
        return
    request = api["storeforward_pb2"].StoreAndForward()
    request.ParseFromString(event["payload"])
    _handle_request(request, event, api)


def plugin_command(argv, api):
    parser = argparse.ArgumentParser(prog=f"{api['plugin_name']} tool", description="STORE_FORWARD_APP plugin utilities")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("stats", help="Print store-and-forward storage stats")
    config_parser = subparsers.add_parser("config", help="Show or update store-and-forward plugin config")
    config_parser.add_argument("--heartbeat", choices=("yes", "no"), help="Whether the plugin should emit ROUTER_HEARTBEAT packets from tick()")
    config_parser.add_argument("--heartbeat-interval-secs", type=int, help="Heartbeat interval in seconds when heartbeat is enabled")
    config_parser.add_argument("--heartbeat-secondary", choices=("yes", "no"), help="Whether heartbeat should mark this server as secondary")
    config_parser.add_argument("--replay-duplicates", choices=("yes", "no"), help="Whether replay history should include duplicate message events")
    args = parser.parse_args(argv)

    if args.command == "config":
        config = _load_config(api)
        if args.heartbeat is not None:
            config["heartbeat_enabled"] = args.heartbeat == "yes"
        if args.heartbeat_interval_secs is not None:
            config["heartbeat_interval_secs"] = max(1, int(args.heartbeat_interval_secs))
        if args.heartbeat_secondary is not None:
            config["heartbeat_secondary"] = args.heartbeat_secondary == "yes"
        if args.replay_duplicates is not None:
            config["replay_duplicates"] = args.replay_duplicates == "yes"
        path = _config_path(api)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(path, config)
        print(f"plugin: {api['plugin_name']}")
        print(f"config_path: {_config_path(api)}")
        print(f"heartbeat_enabled: {config.get('heartbeat_enabled')}")
        print(f"heartbeat_interval_secs: {config.get('heartbeat_interval_secs')}")
        print(f"heartbeat_secondary: {config.get('heartbeat_secondary')}")
        print(f"replay_duplicates: {config.get('replay_duplicates')}")
        return

    if args.command != "stats":
        raise SystemExit(2)

    history = _load_history(api)
    requests, requests_history = _request_counts(api)
    cleanup_state_path = _cleanup_state_path(api)
    cleanup_state = {}
    if cleanup_state_path.exists():
        cleanup_state = _safe_read_json(cleanup_state_path, api, context="cleanup state") or {}
    config = _load_config(api)
    heartbeat_state_path = _heartbeat_state_path(api)
    heartbeat_state = {}
    if heartbeat_state_path.exists():
        heartbeat_state = _safe_read_json(heartbeat_state_path, api, context="heartbeat state") or {}

    oldest = history[0]["last_seen_ts"] if history else None
    newest = history[-1]["last_seen_ts"] if history else None
    print(f"plugin: {api['plugin_name']}")
    print(f"retention_days: {RETENTION_DAYS}")
    print(f"config_path: {_config_path(api)}")
    print(f"heartbeat_enabled: {config.get('heartbeat_enabled')}")
    print(f"heartbeat_interval_secs: {config.get('heartbeat_interval_secs')}")
    print(f"heartbeat_secondary: {config.get('heartbeat_secondary')}")
    print(f"heartbeat_last_sent_ts: {heartbeat_state.get('last_sent_ts')}")
    print(f"replay_duplicates: {config.get('replay_duplicates')}")
    print(f"messages_dir: {_message_dir(api)}")
    print(f"event_logs_dir: {_event_dir(api)}")
    print(f"history_events: {len(history)}")
    print(f"requests: {requests}")
    print(f"history_requests: {requests_history}")
    print(f"oldest_message_ts: {oldest}")
    print(f"newest_message_ts: {newest}")
    print(f"last_cleanup_ts: {cleanup_state.get('last_cleanup_ts')}")
