# meshtastic_plugins

## Purpose

The proxy plugin system is a host-side extension layer for Meshtastic app protocols.

It does not replace device firmware. The attached node still owns radio transmission, routing, airtime, encryption, and native firmware behavior. Plugins extend what happens around app-port traffic that the proxy can observe or emit through that node.

Typical uses:

- protocol extensions on existing Meshtastic app ports
- private application protocols on `PRIVATE_APP`
- archival, marshaling, and analytics
- request/response services implemented on the host
- periodic housekeeping through plugin ticks

## Discovery And Routing

The proxy scans the repo-local `plugins/` directory for files named:

- `PORTNAME.handler.py`
- `PORTNUM.handler.py`

Examples:

- `plugins/STORE_FORWARD_APP.handler.py`
- `plugins/TEXT_MESSAGE_APP.handler.py`
- `plugins/65.handler.py`

The symbolic port-name handler is tried first, then the numeric one.

For `PRIVATE_APP`, the proxy supports subtype dispatch:

- JSON payloads with a top-level string `type` field route to `plugins/PRIVATE_APP.<type>.handler.py`
- payloads beginning with `type=<value>` route to `plugins/PRIVATE_APP.<value>.handler.py`
- if no subtype-specific handler exists, the proxy falls back to `plugins/PRIVATE_APP.handler.py`
- numeric fallback still remains available through `plugins/256.handler.py`

Handlers are hot-reloaded when the file changes. Proxy restart is not required during development.

Inbound direct text messages also support a dedicated handler namespace.
The repo keeps `plugins/DM/` reserved for user-defined scripts, and the proxy optionally appends a mode-specific namespace from `dm_mode` in the proxy config file.

For radio packets on `TEXT_MESSAGE_APP` whose destination is non-zero, the proxy tries these DM handlers in order:

- `plugins/DM/handler_first.py`
- `plugins/DM/<first word of message text>.handler.py`
- `plugins/DM/<sender short name>.handler.py`
- `plugins/DM/handler.py`
- `plugins/DM_<dm_mode>/handler_first.py`
- `plugins/DM_<dm_mode>/<first word of message text>.handler.py`
- `plugins/DM_<dm_mode>/<sender short name>.handler.py`
- `plugins/DM_<dm_mode>/handler.py`

If `dm_mode` is unset, only the `plugins/DM/` chain is used.
Sender short names are learned opportunistically from observed `NODEINFO_APP` traffic.
Each DM handler runs at most once per packet in that order. By default, processing stops after the first callable handler runs.

To continue the chain, a handler can return a dict with `continue_chain: True`.
To rewrite the message for downstream DM handlers, it can also return `message: <mesh_pb2.FromRadio>`.
The rewritten message only affects later DM handlers in the chain; it does not rewrite the original radio broadcast to connected clients.

### DM Routing Details

DM routing is intentionally separate from the normal port plugin lookup.
It only applies to inbound radio packets where:

- `portnum_name == "TEXT_MESSAGE_APP"`
- `packet_to != 0`

That means:

- broadcast text messages do not use the DM chain
- client-originated outgoing text packets do not use the DM chain
- normal `TEXT_MESSAGE_APP.handler.py` plugins still run for all text packets, including direct messages

The DM chain is therefore an extra layer on top of the usual port plugin dispatch, not a replacement for it.

### `dm_mode` Configuration

The proxy reads `dm_mode` from the same env-style config file already passed as `--config-file`.
Accepted keys are:

- `dm_mode`
- `DM_MODE`
- `MESHTASTIC_DM_MODE`

Example:

```bash
dm_mode=work
```

With that set, the proxy appends the mode-specific namespace `plugins/DM_work/` after the base `plugins/DM/` chain.

### First-Word Matching

For `plugins/DM/<first word>.handler.py`, the router:

- decodes the payload as UTF-8
- trims surrounding whitespace
- takes the first whitespace-delimited token
- rejects tokens containing `/` or `\`

Examples:

- payload `b"ping hello"` tries `plugins/DM/ping.handler.py`
- payload `b"  help   now"` tries `plugins/DM/help.handler.py`
- payload `b""` has no first-word match
- non-UTF-8 payload has no first-word match

### Sender Short Name Matching

The sender short-name step uses the most recent short name the proxy has observed for that sender node through `NODEINFO_APP`.

Important implications:

- `plugins/DM/<shortname>.handler.py` only matches after the proxy has learned that node's short name
- the short name is opportunistic cache data, not a guaranteed directory lookup source
- if no short name is known yet, routing simply skips that step

### Continue And Rewrite Semantics

If a DM handler returns nothing, the chain stops after that handler.

If it returns:

```python
{"continue_chain": True}
```

the router continues to later DM handlers.

If it returns:

```python
{"continue_chain": True, "message": rewritten_message}
```

then later DM handlers see the rewritten `mesh_pb2.FromRadio` message and derived fields such as:

- `payload`
- `dm_command`
- `portnum`
- `packet_from`
- `packet_to`

This is what makes sanitizer or normalizer plugins useful. A base handler can clean the message, then hand the cleaned version to `DM_<dm_mode>/...` handlers.

### Hot Reload, Create, And Delete Behavior

DM handlers use the same loader and mtime-based hot reload path as the normal protocol plugins.

Operationally, that means:

- editing an existing DM handler reloads it automatically on the next matching dispatch
- creating a new DM handler file makes it eligible on the next matching dispatch
- deleting a DM handler file removes it from consideration on the next matching dispatch

Proxy restart is not required for any of those cases.

## DM Examples

### Example 1: Simple Command Handler

```python
# plugins/DM/ping.handler.py
def handle_packet(event, api):
    api["logger"].info(
        "dm ping from=%s short=%s payload=%r",
        event.get("packet_from"),
        event.get("sender_short_name"),
        event["payload"],
    )
```

If a direct message payload starts with `ping`, this handler runs before sender-name or generic DM handlers.

### Example 2: Generic DM Logger

```python
# plugins/DM/handler.py
def handle_packet(event, api):
    api["logger"].info(
        "generic dm from=%s to=%s command=%r text=%r",
        event.get("packet_from"),
        event.get("packet_to"),
        event.get("dm_command"),
        event["payload"].decode("utf-8", errors="replace"),
    )
```

This is a useful catch-all when no earlier DM handler matched, or when earlier handlers are absent.

### Example 3: Sender-Specific Handler

```python
# plugins/DM/ALICE.handler.py
def handle_packet(event, api):
    api["logger"].info("special dm path for ALICE: %r", event["payload"])
```

This only runs if the proxy has already learned that the sender's short name is `ALICE`.

### Example 4: Sanitizer That Continues

```python
# plugins/DM/handler.py
def handle_packet(event, api):
    text = event["payload"].decode("utf-8", errors="replace").strip()
    text = " ".join(text.split())

    message = api["mesh_pb2"].FromRadio()
    message.CopyFrom(event["message"])
    message.packet.decoded.payload = text.encode("utf-8")

    return {
        "continue_chain": True,
        "message": message,
    }
```

This normalizes whitespace and lets the chain continue.

### Example 5: Base Sanitizer Plus Mode-Specific Commands

Config file:

```bash
dm_mode=work
```

Base sanitizer:

```python
# plugins/DM/handler.py
def handle_packet(event, api):
    text = event["payload"].decode("utf-8", errors="replace").strip().lower()

    message = api["mesh_pb2"].FromRadio()
    message.CopyFrom(event["message"])
    message.packet.decoded.payload = text.encode("utf-8")

    return {
        "continue_chain": True,
        "message": message,
    }
```

Mode-specific command:

```python
# plugins/DM_work/todo.handler.py
def handle_packet(event, api):
    api["logger"].info("work todo command: %r", event["payload"])
```

If the incoming direct message is `b"  TODO   review docs  "`, the base handler rewrites it to `b"todo review docs"`, then the mode-specific command step tries `plugins/DM_work/todo.handler.py`.

### Example 6: Global Pre-Filter With `handler_first.py`

```python
# plugins/DM/handler_first.py
def handle_packet(event, api):
    text = event["payload"].decode("utf-8", errors="replace")
    if text.startswith("ignore-me"):
        return
    return {"continue_chain": True}
```

This runs before command-word, sender-shortname, or generic handlers in the same namespace.
It is the right place for global admission checks, rate-limit markers, or mandatory normalization.

## Entry Points

A plugin can define any of these functions:

- `handle_packet(event, api)`
  Called for radio-to-client traffic observed from the attached node.
- `handle_client_call(event, api)`
  Called for client-to-radio traffic that the broker allows through.
- `tick(event, api)`
  Called periodically by the proxy for housekeeping or background work.
- `plugin_command(argv, api)`
  Optional self-contained CLI surface, callable through `tools/meshtastic_plugins.py`.

If `handle_client_call()` wants to answer locally and suppress forwarding, it can set `event["consume"] = True`.

Plugin failures are isolated. Exceptions are logged and the proxy keeps running.

## Event Shape

The exact event dictionary varies by path, but the common fields include:

- `event_type`
- `portnum`
- `portnum_name`
- `payload`
- `packet_from`
- `packet_to`
- `packet_id`
- `want_response`
- `plugin_origin_likely`

DM handlers also receive:

- `direct_message`
- `dm_command`
- `sender_short_name`

Client-originated events also include client metadata such as `client_id`.

`plugin_origin_likely` is a loop-avoidance hint. It marks packets that likely originated from a plugin send and helps handlers avoid trivial self-triggering behavior.

## API Surface

The proxy passes an `api` dictionary to plugins so they do not need to import proxy internals.

Useful helpers:

- `send_app(destination=..., portnum=..., payload=..., want_response=False)`
- `reply_app(event, payload=..., portnum=...)`
- `send_mesh_packet(...)`
- `send_toradio(...)`
- `send_fromradio(...)`
- `send_client(...)`
- `broadcast_bytes(...)`
- `plugin_store_append_jsonl(...)`
- `plugin_store_read_jsonl(...)`
- `plugin_store_path(...)`
- `status_snapshot()`
- `list_clients()`

Useful protobuf modules exposed through the API:

- `mesh_pb2`
- `portnums_pb2`
- `storeforward_pb2`

The CLI path exposed by `tools/meshtastic_plugins.py` provides the same plugin-local storage helpers and protobuf modules, so a plugin can own its own operator tooling.

## Storage Model

Plugin runtime state lives under:

```text
.runtime/meshtastic/plugins/<PLUGIN_NAME>/
```

That is separate from the source file under `plugins/*.handler.py`.

The proxy provides JSONL append/read helpers, but a plugin can also manage its own files under its assigned runtime directory. Plugins should treat persisted state as potentially malformed due to partial writes, manual edits, or older buggy code paths. Runtime code should skip bad records and log warnings rather than crashing the proxy.

## STORE_FORWARD_APP Example

The built-in store-and-forward example spans two plugins:

- `plugins/TEXT_MESSAGE_APP.handler.py`
- `plugins/STORE_FORWARD_APP.handler.py`

`TEXT_MESSAGE_APP.handler.py` watches incoming text packets and stores them in two layers:

- content-addressed blobs under `.runtime/meshtastic/plugins/TEXT_MESSAGE_APP/messages/<sha256>.json`
- dated append-only event logs under `.runtime/meshtastic/plugins/TEXT_MESSAGE_APP/events/YYYY-MM-DD.jsonl`

That split keeps payload storage deduplicated while still preserving event order.

`STORE_FORWARD_APP.handler.py` then:

- answers `CLIENT_PING`, `CLIENT_STATS`, and `CLIENT_HISTORY`
- serves both local proxy clients and mesh-originated requests
- reads the text-plugin event history and message blobs
- performs daily retention cleanup from `tick()`
- exposes a self-contained tool surface through `plugin_command()`

### Duplicate Replay Flag

Duplicate replay is controlled by the plugin-local config file:

```text
.runtime/meshtastic/plugins/STORE_FORWARD_APP/config.json
```

Current flag:

- `heartbeat_enabled`
- `heartbeat_interval_secs`
- `heartbeat_secondary`
- `replay_duplicates`

Default:

- `heartbeat_enabled = true`
- `heartbeat_interval_secs = 3600`
- `heartbeat_secondary = false`
- `replay_duplicates = false`

With the default setting, repeated identical messages are stored in the event log but collapsed at replay time, so history returns one entry per unique message hash. If `replay_duplicates` is enabled, replay follows the event log and repeated identical messages are returned as separate history entries.

If heartbeat is enabled, `STORE_FORWARD_APP.tick()` emits `ROUTER_HEARTBEAT` packets through the real node on the store-and-forward port. The heartbeat packet includes the configured period and whether this service should identify itself as secondary.

The upstream protobufs define the heartbeat packet and a boolean store-forward heartbeat setting, but they do not document a default interval in the protobuf comments. The `3600`-second value is therefore this repo's default, not a Meshtastic protocol default.

Inspect or change it with:

```bash
tools/meshtastic_plugins.py STORE_FORWARD_APP config
tools/meshtastic_plugins.py STORE_FORWARD_APP config --heartbeat yes --heartbeat-interval-secs 3600
tools/meshtastic_plugins.py STORE_FORWARD_APP config --replay-duplicates yes
tools/meshtastic_plugins.py STORE_FORWARD_APP stats
./setup/meshtastic-python.sh plugins STORE_FORWARD_APP config --replay-duplicates no
```

### Why This Example Matters

`STORE_FORWARD_APP` is a good illustration because it uses the main pieces of the architecture together:

- packet observation
- mesh replies through the real node
- durable plugin-owned state
- periodic cleanup through `tick()`
- a self-contained operator CLI

That same pattern is enough for many host-side protocol extensions without needing firmware changes.
