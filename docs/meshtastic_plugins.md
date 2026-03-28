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

Inbound direct text messages also support a dedicated handler namespace under `plugins/DM/`.
For radio packets on `TEXT_MESSAGE_APP` whose destination is non-zero, the proxy tries these DM handlers in order and stops on the first callable match:

- `plugins/DM/<first word of message text>.handler.py`
- `plugins/DM/<sender short name>.handler.py`
- `plugins/DM/handler.py`

Sender short names are learned opportunistically from observed `NODEINFO_APP` traffic.

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
