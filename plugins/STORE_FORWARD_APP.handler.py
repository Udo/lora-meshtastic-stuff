DEFAULT_HISTORY_WINDOW_MINUTES = 60
DEFAULT_RETURN_MAX = 20
MAX_HISTORY_MESSAGES = 256
MESSAGE_LOG = "messages.jsonl"


def _load_history(api):
    records = api["plugin_store_read_jsonl"]("TEXT_MESSAGE_APP", MESSAGE_LOG, limit=MAX_HISTORY_MESSAGES)
    return [record for record in records if record.get("payload_text") is not None]


def _build_stats(api, history, requests, requests_history):
    response = api["storeforward_pb2"].StoreAndForward(rr=api["storeforward_pb2"].StoreAndForward.ROUTER_STATS)
    response.stats.messages_total = len(history)
    response.stats.messages_saved = len(history)
    response.stats.messages_max = MAX_HISTORY_MESSAGES
    response.stats.up_time = 0
    response.stats.requests = requests
    response.stats.requests_history = requests_history
    response.stats.heartbeat = False
    response.stats.return_max = DEFAULT_RETURN_MAX
    response.stats.return_window = DEFAULT_HISTORY_WINDOW_MINUTES
    return response
def _filtered_history(history, now_ts, window_minutes, last_request):
    cutoff = now_ts - (window_minutes * 60)
    entries = []
    for index, entry in enumerate(history, start=1):
        if window_minutes > 0 and float(entry.get("ts", 0)) < cutoff:
            continue
        if last_request and index <= last_request:
            continue
        entries.append((index, entry))
    return entries


def _append_stat(api, key):
    api["plugin_store_append_jsonl"](
        api["plugin_name"],
        "requests.jsonl",
        {"key": key, "ts": api["time"]()},
    )


def _request_counts(api):
    requests = 0
    requests_history = 0
    for record in api["plugin_store_read_jsonl"](api["plugin_name"], "requests.jsonl"):
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
