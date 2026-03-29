# lora-meshtastic-stuff

This workspace now contains a verified and reproducible local workflow for a Meshnology N35 / Heltec V3 class ESP32-S3 LoRa board, with OS-specific serial-port defaults for Linux, macOS, and Windows.

## Current Device State

- Board family: Heltec V3 compatible (`HELTEC_V3`, ESP32-S3, 8 MB flash)
- Known-good Meshtastic firmware bundled in this repo: `2.7.13.597fa0b`
- Known-bad firmware on this board: `2.7.15.567b8ea` bootlooped after a clean flash and has been removed from the repo
- USB Meshtastic CLI access: working
- LoRa region: `EU_868`
- WiFi: not configured yet because LAN credentials were not available during setup

## Local Helper Script

Use [setup/meshtastic-python.sh](setup/meshtastic-python.sh) for repeatable setup and device operations.

The script is now the canonical setup path. It uses the extracted known-good firmware bundle under [docs/firmware/firmware-esp32s3-2.7.13.597fa0b](docs/firmware/firmware-esp32s3-2.7.13.597fa0b).

Common commands:

```bash
./setup/meshtastic-python.sh bootstrap
./setup/meshtastic-python.sh flash
./setup/meshtastic-python.sh provision
./setup/meshtastic-python.sh guided
./setup/meshtastic-python.sh probe
./setup/meshtastic-python.sh nodes
./setup/meshtastic-python.sh nodedb-reset
./setup/meshtastic-python.sh contacts list
./setup/meshtastic-python.sh contacts keys
./setup/meshtastic-python.sh contacts remove !0439d098
./setup/meshtastic-python.sh export-config
./setup/meshtastic-python.sh get range_test.sender
./setup/meshtastic-python.sh set range_test.sender 0
./setup/meshtastic-python.sh set-name "My Node" "MYND"
./setup/meshtastic-python.sh set-role CLIENT
./setup/meshtastic-python.sh set-region EU_868
./setup/meshtastic-python.sh set-modem-preset LONG_FAST
./setup/meshtastic-python.sh set-position 52.5200 13.4050 35
./setup/meshtastic-python.sh clear-position
./setup/meshtastic-python.sh channels list
./setup/meshtastic-python.sh channels add Friends
./setup/meshtastic-python.sh set-ham DO1ABC
./setup/meshtastic-python.sh set-wifi "YOUR_WIFI_SSID" "YOUR_WIFI_PASSWORD"
./setup/meshtastic-python.sh status summary
./setup/meshtastic-python.sh status channels
./setup/meshtastic-python.sh telemetry --type environment
./setup/meshtastic-python.sh telemetry cached --type environment
./setup/meshtastic-python.sh monitor
./setup/meshtastic-python.sh messages send WO67 "hello"
./setup/meshtastic-python.sh messages sync mesh-chat
./setup/meshtastic-python.sh protocol mesh-archive --quiet
./setup/meshtastic-python.sh plugins STORE_FORWARD_APP stats
./setup/meshtastic-python.sh proxy-start
./setup/meshtastic-python.sh proxy-status
./setup/meshtastic-python.sh proxy-check
./setup/meshtastic-python.sh console
```

## Reproducible Setup

From a fresh checkout, the minimal end-to-end path is:

```bash
./setup/meshtastic-python.sh bootstrap
./setup/meshtastic-python.sh provision
```

That will:

- install the Python tooling used by the repo (`meshtastic`, `esptool`, `pyserial`)
- flash the repo's known-good `heltec-v3` firmware bundle
- apply the default region `EU_868`
- apply the optional owner names from `MESHTASTIC_OWNER_LONG` and `MESHTASTIC_OWNER_SHORT`
- leave WiFi disabled unless credentials are provided via environment variables

To change the modem preset after provisioning:

```bash
./setup/meshtastic-python.sh set-modem-preset LONG_FAST
```

Additional node identity and fixed-position commands:

```bash
./setup/meshtastic-python.sh set-role CLIENT
./setup/meshtastic-python.sh set-position 52.5200 13.4050 35
./setup/meshtastic-python.sh clear-position
./setup/meshtastic-python.sh set-ham DO1ABC
```

Notes:

- `set-role` maps to Meshtastic `device.role`, which is the closest thing to a node type.
- Supported roles are: `CLIENT`, `CLIENT_MUTE`, `ROUTER`, `ROUTER_CLIENT`, `REPEATER`, `TRACKER`, `SENSOR`, `TAK`, `CLIENT_HIDDEN`, `LOST_AND_FOUND`, `TAK_TRACKER`, `ROUTER_LATE`, `CLIENT_BASE`.
- `set-position` configures a fixed latitude/longitude, with optional altitude in meters, for nodes that do not rely on live GPS.
- `clear-position` removes any configured fixed position.
- `channels` lists or manages configured Meshtastic channels on the local node.
- `set-ham` sets the licensed ham identifier and follows the upstream CLI behavior of disabling encryption.

Channel management examples:

```bash
./setup/meshtastic-python.sh channels list
./setup/meshtastic-python.sh channels url
./setup/meshtastic-python.sh channels add Friends
./setup/meshtastic-python.sh channels set 1 psk random
./setup/meshtastic-python.sh channels delete 1
./setup/meshtastic-python.sh channels qr
./setup/meshtastic-python.sh channels qr-all
```

Use `channels url` when you want a single-line share URL suitable for piping or copy/paste. The `add-url` and `set-url` actions now strip embedded whitespace so wrapped Meshtastic URLs pasted from terminals or YAML exports still import cleanly.

The status tool also exposes the configured channel list:

```bash
./setup/meshtastic-python.sh status channels
```

Meshtastic modem preset quick guide:

- `LONG_FAST`: general-purpose default balance of range and throughput.
- `LONG_SLOW`: more link budget than `LONG_FAST`, but slower packet airtime.
- `VERY_LONG_SLOW`: maximum range-oriented preset, with the slowest throughput and longest airtime.
- `MEDIUM_SLOW`: middle-ground preset that leans toward reliability over speed.
- `MEDIUM_FAST`: middle-ground preset that leans toward throughput over range.
- `SHORT_SLOW`: shorter-range preset with conservative signaling.
- `SHORT_FAST`: shorter-range preset optimized for faster local traffic.
- `LONG_MODERATE`: long-range preset between `LONG_FAST` and `LONG_SLOW`.
- `SHORT_TURBO`: highest-speed short-range preset, trading away significant range and robustness.
- `LONG_TURBO`: faster long-range preset than the slow long-range modes, but still a throughput-over-range tradeoff.

Rule of thumb:

- names starting with `LONG` favor distance and weaker links
- names starting with `SHORT` favor local throughput
- `SLOW` increases airtime and usually improves weak-link tolerance
- `FAST` and `TURBO` reduce airtime and favor speed over range

Use the same preset on nodes that need to talk to each other. Mixing presets on the same channel will generally prevent them from communicating.

Very rough distance expectations by environment:

- These are order-of-magnitude planning numbers, not guarantees.
- They assume legal regional power, decent antennas, and typical handheld, rooftop, or small fixed-node installs rather than optimized record-attempt setups.
- Terrain dominates everything: a hill between nodes can kill a link, while ridge-to-ridge or tower-to-tower line-of-sight can improve range by more than an order of magnitude.
- Meshtastic's published range-test records reach roughly `166 km`, `254 km`, and `331 km`, but those are elevated line-of-sight record conditions, not street-level expectations.

Preset range guide:

| Preset family | Dense urban / indoors | Typical urban / suburban | Rolling hills / ridge assist | Clear elevated line-of-sight |
| --- | --- | --- | --- | --- |
| `SHORT_TURBO`, `SHORT_FAST` | `0.1` to `1 km` | `0.5` to `3 km` | `1` to `5 km` | `2` to `10+ km` |
| `SHORT_SLOW`, `MEDIUM_FAST` | `0.3` to `2 km` | `1` to `5 km` | `2` to `10 km` | `5` to `20+ km` |
| `MEDIUM_SLOW`, `LONG_FAST` | `1` to `5 km` | `3` to `10 km` | `10` to `40 km` | `20` to `100+ km` |
| `LONG_MODERATE`, `LONG_SLOW` | `2` to `8 km` | `5` to `15 km` | `15` to `80 km` | `30` to `150+ km` |
| `VERY_LONG_SLOW` | `2` to `10 km` | `5` to `20 km` | `20` to `100+ km` | `50` to `200+ km` |

How to read that table:

- `Dense urban / indoors` means street canyons, buildings, foliage, cars, and low mounting height. In that environment, even the long presets are often still in the `sub-km to few-km` regime.
- `Typical urban / suburban` means neighborhood to neighborhood, some building blockage, some roofline clearance, and no major ridge obstruction.
- `Rolling hills / ridge assist` can be much better than flat urban terrain if one or both nodes are placed on local high ground with partial line-of-sight.
- `Clear elevated line-of-sight` means hilltop, tower, or mountain-to-mountain geometry. That is the regime where Meshtastic's published record distances live.

Choosing a preset in practice:

- Start with `LONG_FAST` unless you have a specific reason not to; it is the Meshtastic default because it is a good compromise.
- Move toward `SHORT_*` or `*_TURBO` if you have many nearby nodes and want shorter airtime and less channel congestion.
- Move toward `LONG_SLOW` or `VERY_LONG_SLOW` only when maximum reach matters more than latency and throughput.
- In EU regions, slower presets increase airtime, so duty-cycle limits become more visible during heavier traffic.

To provision WiFi at the same time:

```bash
MESHTASTIC_WIFI_SSID="YOUR_WIFI_SSID" \
MESHTASTIC_WIFI_PSK="YOUR_WIFI_PASSWORD" \
./setup/meshtastic-python.sh provision
```

To provision names at the same time:

```bash
MESHTASTIC_OWNER_LONG="Backpack Node" \
MESHTASTIC_OWNER_SHORT="PACK" \
./setup/meshtastic-python.sh provision
```

For an interactive colored flow with defaults and optional WiFi, run:

```bash
./setup/meshtastic-python.sh guided
```

If you leave the WiFi SSID or password blank in guided mode, WiFi setup is skipped.

## Status Tool

The repo-local pretty status tool lives at [tools/meshtastic_status.py](tools/meshtastic_status.py) and can be called directly or through the setup script wrapper.

For a primer on the main Meshtastic application protocols used throughout this repo, see [docs/meshtastic_protocols.md](docs/meshtastic_protocols.md).

After `./setup/meshtastic-python.sh bootstrap`, direct invocation works too:

```bash
tools/meshtastic_status.py
```

Connection selection for direct invocation:

- `--host` wins if provided explicitly
- otherwise `MESHTASTIC_HOST` is used if set
- otherwise a healthy local proxy or broker endpoint is auto-detected from `.runtime/meshtastic/proxy-status.json`
- otherwise the tool falls back to direct serial access on `--port` or `MESHTASTIC_PORT`

To inspect that decision path explicitly, run:

```bash
./setup/meshtastic-python.sh target-debug
./setup/meshtastic-python.sh target-debug --json
./setup/meshtastic-python.sh target-debug --brief
```

Examples:

```bash
./setup/meshtastic-python.sh status summary
./setup/meshtastic-python.sh status nodes
./setup/meshtastic-python.sh status neighbors
./setup/meshtastic-python.sh telemetry --type environment
./setup/meshtastic-python.sh telemetry cached --type environment
./setup/meshtastic-python.sh telemetry --type air-quality --limit 5 --include-multihop
./setup/meshtastic-python.sh status config lora network
./setup/meshtastic-python.sh status raw-info
./setup/meshtastic-python.sh status traceroute !0438ca24
```

NodeDB and contact maintenance:

```bash
./setup/meshtastic-python.sh nodedb-reset
./setup/meshtastic-python.sh contacts list
./setup/meshtastic-python.sh contacts keys
./setup/meshtastic-python.sh contacts remove !0439d098
./setup/meshtastic-python.sh contacts favorite !0439d098
./setup/meshtastic-python.sh contacts ignore !0439d098
```

Notes:

- `nodedb-reset` clears the connected node's known-node database so peer metadata is relearned from the mesh.
- `contacts list` shows the current NodeDB entries, including whether a public key is known for each peer.
- `contacts keys` focuses on the current peer public keys known to the node.
- `contacts remove`, `favorite`, `unfavorite`, `ignore`, and `unignore` forward to the upstream Meshtastic admin actions.
- There is no real Meshtastic CLI operation to manually inject an arbitrary new contact plus public key; contacts are learned from on-air node info.

Low-level config passthrough:

```bash
./setup/meshtastic-python.sh get range_test.sender
./setup/meshtastic-python.sh set range_test.sender 0
./setup/meshtastic-python.sh get telemetry.environment_update_interval
./setup/meshtastic-python.sh set telemetry.environment_update_interval 900
```

These map directly to the upstream Meshtastic `--get` and `--set` field access pattern, but keep the repo's normal target-selection behavior.

For the full field-path reference supported by this wrapper surface, see [docs/meshtastic_get_set_fields.md](docs/meshtastic_get_set_fields.md).

The summary view now reports the modem preset explicitly, even when the device is using the protobuf default enum value such as `LONG_FAST`.

It also reports the configured Meshtastic device role and whether fixed-position mode is enabled, including the current coordinates when the node is advertising them.

The `neighbors` view provides a live RF snapshot of peers with SNR data, including direct-neighbor counts and averages, and it skips incomplete NodeDB records instead of crashing on malformed entries.

The wrapper also exposes `telemetry`, which delegates to `status telemetry`. The default mode is an active request/response poll of nearby nodes. `telemetry cached ...` only prints telemetry the local node has already learned in the background. Both modes target the closest direct neighbors first using hop count and SNR; `--include-multihop` lets them continue to farther nodes if you want a wider sweep.

## Proxy Plugins

The proxy now supports hot-reloaded protocol plugins from the repo-local `plugins/` directory.

For the full plugin architecture, runtime API, storage model, and a worked `STORE_FORWARD_APP` example, see [docs/meshtastic_plugins.md](docs/meshtastic_plugins.md).

Plugin filename convention:

- `plugins/STORE_FORWARD_APP.handler.py`
- `plugins/65.handler.py`

The proxy checks for both the symbolic Meshtastic port name and the numeric port number. Matching plugins are reloaded automatically when the file changes, so handler development does not require restarting the proxy.

For `PRIVATE_APP`, the router now supports subtype dispatch:

- if the payload is UTF-8 JSON with a top-level string `type`, it tries `plugins/PRIVATE_APP.<type>.handler.py`
- if the payload starts with `type=<value>`, it tries `plugins/PRIVATE_APP.<value>.handler.py`
- if no typed handler exists, it falls back to `plugins/PRIVATE_APP.handler.py`

Inbound direct text messages also support a DM handler chain. The reserved base namespace is `plugins/DM/`, and if the proxy config file sets `dm_mode`, it appends a second namespace under `plugins/DM_<dm_mode>/`.

The DM router only applies to incoming `TEXT_MESSAGE_APP` packets whose destination is non-zero. Resolution order is:

- `plugins/DM/handler_first.py`
- `plugins/DM/<first word>.handler.py`
- `plugins/DM/<sender short name>.handler.py`
- `plugins/DM/handler.py`
- `plugins/DM_<dm_mode>/handler_first.py`
- `plugins/DM_<dm_mode>/<first word>.handler.py`
- `plugins/DM_<dm_mode>/<sender short name>.handler.py`
- `plugins/DM_<dm_mode>/handler.py`

DM handlers stop the chain by default. To continue, return a dict containing `continue_chain: True`. A handler can also return `message: <FromRadio>` so downstream DM handlers see a rewritten message.

Keep automated DM replies short. In practical testing, an auto-reply banner around 426 UTF-8 bytes was too large to deliver reliably, while a short reply on the same path worked immediately. Treat about 200 UTF-8 bytes as a conservative upper bound for DM plugin replies unless you have tested your exact firmware and client combination.

The detailed DM documentation, including examples for `handler_first.py`, sender-specific handlers, sanitizers, `dm_mode`, and continue/rewrite chaining, lives in [docs/meshtastic_plugins.md](docs/meshtastic_plugins.md).

Inbound text traffic can also route through channel-specific plugin namespaces under `plugins/CHAN_<channel name>/`.
The order is:

- `plugins/CHAN_<channel name>/handler_alltraffic.py`
- `plugins/CHAN_<channel name>/handler_first.py`
- `plugins/CHAN_<channel name>/<first word after local-name prefix>.handler.py`
- `plugins/CHAN_<channel name>/handler.py`

Except for `handler_alltraffic.py`, channel handlers only run when the incoming text starts with the local node short name, optionally prefixed with `@`. The proxy learns the local short name and channel-name mapping opportunistically from observed admin traffic. Full details and examples live in [docs/meshtastic_plugins.md](docs/meshtastic_plugins.md).

The proxy now also actively refreshes owner and channel metadata on startup and periodically through read-only admin queries, so channel and DM routing do not depend entirely on incidental mesh traffic.
As a safety measure, channel plugins are blocked by default on a `PRIMARY` channel using `none` or `default` PSK. You can opt in explicitly through the proxy config file if you really want automation there.

Supported plugin entry points:

- `handle_packet(event, api)` for radio-to-client traffic seen from the attached node
- `handle_client_call(event, api)` for client-to-radio packet writes forwarded through the proxy
- `tick(event, api)` for periodic housekeeping, polling, or maintenance work

The `event` argument includes decoded protobuf messages, raw framed bytes, payload bytes, packet IDs, source and destination node IDs, response flags, client metadata where relevant, and a `plugin_origin_likely` hint for loop avoidance.

The `api` dictionary exposes host-extension helpers including:

- `send_app(destination=..., portnum=..., payload=..., want_response=False)` to emit a mesh app packet through the attached node
- `reply_app(event, payload=..., portnum=...)` to reply either to a proxy client or to a mesh-originated packet
- `send_mesh_packet`, `send_toradio`, `send_fromradio`, `send_client`, `broadcast_bytes`
- `plugin_store_append_jsonl`, `plugin_store_read_jsonl`, and `plugin_store_path` for durable plugin state under the proxy runtime directory
- `status_snapshot`, `list_clients`, `mesh_pb2`, `portnums_pb2`, and `storeforward_pb2`

Handler failures are isolated from the proxy data path. Exceptions are logged and the proxy continues forwarding traffic.

The repo now includes built-in protocol plugin examples:

- `plugins/STORE_FORWARD_APP.handler.py` answers store-and-forward ping, stats, and history requests for both local proxy clients and mesh-originated packets, and exposes a self-contained `stats` tool command
- `plugins/TEXT_MESSAGE_APP.handler.py` persists observed text packets into a content-addressed store used by that store-and-forward handler
- `plugins/IP_TUNNEL_APP.handler.py` exposes a host-side Unix datagram bridge for `IP_TUNNEL_APP`, emits periodic same-port gateway heartbeats, and persists seen gateway announcements for service discovery

Current store-forward storage behavior:

- text messages are stored under `.runtime/meshtastic/plugins/TEXT_MESSAGE_APP/messages/<sha256>.json`
- replay events are stored separately under `.runtime/meshtastic/plugins/TEXT_MESSAGE_APP/events/YYYY-MM-DD.jsonl`
- each blob is content-addressed by a hash over stable message fields, while the dated event logs preserve arrival order and can preserve duplicates
- the logical retention window is the last 30 days
- optional `ROUTER_HEARTBEAT` emission is handled from `STORE_FORWARD_APP.tick()` using plugin-local config
- heartbeat is enabled by default and this repo uses a default interval of `3600` seconds
- duplicate replay is disabled by default and can be enabled with the plugin-local `replay_duplicates` flag
- `STORE_FORWARD_APP.tick()` performs physical cleanup at most once per day
- malformed plugin storage is skipped with warning logs instead of aborting stats, replay, or cleanup
- plugin stats can be queried directly with:

```bash
tools/meshtastic_plugins.py STORE_FORWARD_APP stats
./setup/meshtastic-python.sh plugins STORE_FORWARD_APP stats
tools/meshtastic_plugins.py STORE_FORWARD_APP config --heartbeat yes --heartbeat-interval-secs 3600
tools/meshtastic_plugins.py STORE_FORWARD_APP config --replay-duplicates yes
```

The IP tunnel helper for local Python consumers lives in `tools/meshtastic_ip_tunnel.py` and exposes:

- `setup_ip_tunnel_client(...)` for raw `IP_TUNNEL_APP` payload access over the plugin socket
- `setup_linux_ip_tunnel(...)` for a Linux TUN-device bridge built on top of that helper

`setup_linux_ip_tunnel(...)` is stdlib-only, uses `/dev/net/tun`, assigns the interface an address derived from `local_node_num` on the synthetic `10.115.x.x` subnet by default, filters a few known chatty protocols/ports, and forwards packets by mapping the destination IP's low 16 bits back to a Meshtastic node number.

The gateway advertisement layer is plugin-local and enabled by default for this repo's standard gateway plugin. Upstream Meshtastic standardizes `IP_TUNNEL_APP` as raw IP payload on port `33`, but does not appear to define an official gateway heartbeat for that port. This repo therefore layers a framework-standard same-port control envelope on top of the raw IP transport. The envelope currently uses `schema = "meshtastic.gateway.control"`, `version = 1`, `kind = "announce"`, and a `payload` object that carries the `gateway_announce` body. Raw IPv4/IPv6 payloads still pass through unchanged; non-IP payloads on `IP_TUNNEL_APP` are interpreted as service-control frames for this gateway service. Inspect or change it with:

```bash
tools/meshtastic_plugins.py IP_TUNNEL_APP status
tools/meshtastic_plugins.py IP_TUNNEL_APP config --announce yes --announce-interval-secs 300
tools/meshtastic_plugins.py IP_TUNNEL_APP recent --limit 20
```

## Protocol Logger

The long-running proxy/broker does relay realtime traffic, but it does not persist decoded events by itself. For continuous archival of messages, telemetry, node updates, connection transitions, and other protocol-level events, run the protocol logger as a separate client:

```bash
./setup/meshtastic-python.sh proxy-start
```

Direct invocation works too:

```bash
tools/meshtastic_protocol.py mesh-archive --quiet
```

Notes:

- `proxy-start` now auto-starts the protocol logger sidecar, using the default log name `protocol` unless `MESHTASTIC_PROTOCOL_LOG_NAME` is set.
- Protocol logs use the same transcript directory and single-line key/value storage convention as `messages sync`.
- The default log file is `~/.local/log/meshtastic/protocol.log`.
- Use `MESHTASTIC_LOG_DIR` or `--log-dir` the same way you would for `messages`.
- `protocol` is intended for always-on collection; `messages sync` remains the narrower text-message transcript tool.
- Persistent proxy/protocol service settings now live in `.runtime/meshtastic/service.env`. Reinstalling the systemd unit refreshes the unit file, but preserves that config file instead of silently rewriting the serial port or TCP settings.
- On first `proxy-autostart-install`, the wrapper creates `.runtime/meshtastic/service.env`, prints its path, and stops so you can review/edit it before installing the service units.
- After the units are already installed, editing `.runtime/meshtastic/service.env` only requires a service restart; you do not need to rerun `proxy-autostart-install` just to apply config changes.

## Messaging Tool

The repo-local messaging tool lives at [tools/meshtastic_messages.py](tools/meshtastic_messages.py) and is exposed through the wrapper as `messages`.

Examples:

```bash
./setup/meshtastic-python.sh messages send WO67 "hello from UDO1"
./setup/meshtastic-python.sh messages send !0439d098 "private check" --log-name worms-dm
./setup/meshtastic-python.sh messages sync mesh-chat
./setup/meshtastic-python.sh messages sync mesh-chat --scope private
./setup/meshtastic-python.sh messages tail mesh-chat --lines 20 --follow
./setup/meshtastic-python.sh messages grep mesh-chat 'scope="private"' --count
./setup/meshtastic-python.sh messages stats
./setup/meshtastic-python.sh messages stats mesh-chat
./setup/meshtastic-python.sh messages prune --days 14 --dry-run
tools/meshtastic_messages.py sync mesh-chat --timeout 30
```

Notes:

- `messages send` resolves peers by node ID, short name, long name, or a unique prefix from the current NodeDB snapshot and sends on Meshtastic `PRIVATE_APP`.
- `messages sync` records live public and private text traffic that arrives while it is connected; it does not backfill old traffic from before the process started.
- `messages tail`, `messages grep`, `messages stats`, and `messages prune` are file-only helpers for inspecting and cleaning transcript logs without opening a radio connection.
- Logs are appended to `~/.local/log/meshtastic/<logname>.log` using single-line key-value records so they stay easy to grep.
- Set `MESHTASTIC_LOG_DIR` if you want the transcript files somewhere else, or use `tools/meshtastic_messages.py --log-dir /path/...` for a one-off override.
- `messages tail --follow` behaves like `tail -f`, and `messages grep --count` prints only the number of matching lines.
- `messages stats` prints a small summary over one transcript log or, if no log name is provided, all transcript logs in the selected directory.
- `messages stats` skips malformed transcript lines instead of aborting the whole summary and reports how many were ignored.
- `messages prune --days N` removes `.log` files older than `N` days; start with `--dry-run` if you want to inspect the candidates first.
- The tool follows the same transport selection order as `status` and `monitor`, so it will automatically use the local proxy or broker when one is healthy.

## Monitor Tool

The repo-local event monitor lives at [tools/meshtastic_monitor.py](tools/meshtastic_monitor.py). It stays connected and prints Meshtastic events as they happen.

Like the status tool, direct monitor runs now auto-prefer an explicit `--host`, then `MESHTASTIC_HOST`, then a healthy local proxy or broker, and only fall back to serial when no TCP target is available.

Examples:

```bash
./setup/meshtastic-python.sh monitor
./setup/meshtastic-python.sh monitor --include-log-lines
./setup/meshtastic-python.sh monitor --json
./setup/meshtastic-python.sh monitor --only connection,node
./setup/meshtastic-python.sh monitor --exclude log,routing
./setup/meshtastic-python.sh monitor --log-file logs/meshtastic-monitor.log
tools/meshtastic_monitor.py --topic-prefix meshtastic.receive
```

Filter notes:

- `--only` accepts comma-separated topic or event filters such as `connection`, `node`, `receive`, `receive.text`, `position`, `telemetry`
- `--exclude` suppresses matching events after inclusion filtering
- `--log-file` appends the emitted stream to a file while still printing to the terminal

When the local proxy is running, the setup wrapper automatically routes `status`, `monitor`, and `console` through TCP on `127.0.0.1:4403` unless `MESHTASTIC_HOST` is set explicitly.

The setup and console entrypoints now resolve their virtualenv interpreter and default serial-port name per OS. Linux keeps the existing `/dev/ttyUSB0` behavior, macOS defaults to a `/dev/tty.usbmodem*` style port, and Windows uses `COM` ports.

## Proxy

The repo now includes a local serial-to-TCP proxy at [tools/meshtastic_proxy.py](tools/meshtastic_proxy.py). It owns the configured serial port once and exposes a Meshtastic-compatible TCP endpoint for local clients such as the status tool, monitor, or Contact console.

In practice the proxy now behaves as a local broker as well: direct repo tools auto-detect it and prefer it, and the broker layer arbitrates control writes so multiple local clients can safely share the same Meshtastic link.

Examples:

```bash
./setup/meshtastic-python.sh proxy-start
./setup/meshtastic-python.sh proxy-status
./setup/meshtastic-python.sh proxy-status --json
./setup/meshtastic-python.sh proxy-check
./setup/meshtastic-python.sh proxy-check --json
./setup/meshtastic-python.sh proxy-autostart-install
./setup/meshtastic-python.sh proxy-autostart-status
./setup/meshtastic-python.sh status summary
./setup/meshtastic-python.sh monitor --only connection,node
./setup/meshtastic-python.sh console
./setup/meshtastic-python.sh proxy-stop
```

Notes:

- the proxy defaults to `127.0.0.1:4403`, which matches Meshtastic's standard TCP default
- `proxy-status` now reports `running` only when the TCP endpoint is actually reachable; `proxy-check` is a machine-friendly health probe
- `proxy-status` also shows broker state such as connected client count, current control-session owner, and allowed/denied control-write counters
- both `proxy-status --json` and `proxy-check --json` emit machine-readable state derived from the same proxy status snapshot file
- on Linux, `proxy-autostart-install` installs a systemd user service that enables the proxy or broker automatically and sends logs to journald with the identifier `meshtastic-proxy`
- the generated proxy and protocol systemd units now read persistent settings from `.runtime/meshtastic/service.env` instead of baking the serial port and TCP values directly into the unit file
- `proxy-autostart-status` now prints the effective runtime root and proxy status-file path, so it is explicit which checkout the service is pinned to
- `proxy-log` automatically follows `journalctl --user -u meshtastic-proxy.service -f` when the systemd user service is installed
- the systemd user service starts automatically after login; to keep it running across reboot before login, enable user lingering with `sudo loginctl enable-linger $USER`
- direct invocations of [tools/meshtastic_status.py](tools/meshtastic_status.py), [tools/meshtastic_monitor.py](tools/meshtastic_monitor.py), and [console/contact.sh](console/contact.sh) auto-detect and prefer the local proxy or broker when it is healthy
- `./setup/meshtastic-python.sh target-debug` explains which target the repo tools would use and why, with optional `--json` and single-line `--brief` modes for automation
- `proxy-start` writes runtime state under `.runtime/meshtastic/`
- the broker now ages out stale control owners, upgrades observed admin sessions into longer-lived leases, and records the last seen session passkey in its status snapshot
- flash, doctor, and raw serial log capture require direct serial access, so stop the proxy first for those commands
- `MESHTASTIC_HOST` and `MESHTASTIC_TCP_PORT` can point the wrappers at a remote Meshtastic TCP endpoint instead of the local proxy

## Console TUI

The upstream Contact console UI for Meshtastic is vendored under [console/README.md](console/README.md), so there is no separate `pip install contact` step for this repo.

Examples:

```bash
./setup/meshtastic-python.sh console
./setup/meshtastic-python.sh console --settings
./setup/meshtastic-python.sh console --host 192.168.1.10
./console/contact.sh --port /dev/ttyUSB0
```

If the local proxy is healthy, [console/contact.sh](console/contact.sh) now prefers it automatically even when launched directly.

It now uses the same shared target resolver as the repo-local Python tools, so Contact follows the same precedence order as `meshtastic_status.py` and `meshtastic_monitor.py`.

## Notes

- WiFi on ESP32 Meshtastic nodes is client mode only.
- Enabling WiFi disables Bluetooth on the node.
- For browser access over USB, use Meshtastic Web with Web Serial.
- For phone access over WiFi, connect the node to the same LAN as the phone and then use the Meshtastic app or web client over the node IP / `meshtastic.local`.
- Local backup artifacts under `docs/backup/` are ignored and are not part of the reproducible repo state.

Supporting notes and downloaded firmware are under [docs/notes](docs/notes) and [docs/firmware](docs/firmware).

## Script Docs

Per-script reference documents are available under `docs/`:

- [docs/meshtastic_tools_overview.md](docs/meshtastic_tools_overview.md)
- [docs/meshtastic_status.md](docs/meshtastic_status.md)
- [docs/meshtastic_monitor.md](docs/meshtastic_monitor.md)
- [docs/meshtastic_messages.md](docs/meshtastic_messages.md)
- [docs/meshtastic_proxy.md](docs/meshtastic_proxy.md)
- [docs/meshtastic_broker.md](docs/meshtastic_broker.md)
