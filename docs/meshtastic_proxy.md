# meshtastic_proxy.py

## Usage

```bash
./setup/meshtastic-python.sh proxy-start
./setup/meshtastic-python.sh proxy-status
./setup/meshtastic-python.sh proxy-status --json
./setup/meshtastic-python.sh proxy-check
./setup/meshtastic-python.sh proxy-check --json
./setup/meshtastic-python.sh proxy-autostart-install
./setup/meshtastic-python.sh proxy-autostart-status
./setup/meshtastic-python.sh proxy-log
./setup/meshtastic-python.sh proxy-stop
./.venv/bin/python tools/meshtastic_proxy.py --serial-port "\$MESHTASTIC_PORT" --listen-host 127.0.0.1 --listen-port 4403 --status-file .runtime/meshtastic/proxy-status.json
```

## Function

- Owns the single Meshtastic serial device and exposes a Meshtastic-compatible TCP endpoint for local and LAN clients.
- Broadcasts radio output to all connected TCP clients.
- Keeps multiple TCP clients connected at once; the broker arbitrates conflicting host-session and control traffic instead of evicting clients on connect.
- Delegates client-to-radio arbitration to `meshtastic_broker.py`.
- Discovers packet handlers from the repo-local `plugins/` directory using `PORTNAME.handler.py` or `PORTNUM.handler.py` filenames.
- For `PRIVATE_APP`, also supports subtype routing via `PRIVATE_APP.<type>.handler.py` when the payload exposes a parseable `type`.
- For inbound direct text messages, also supports the DM handler chain under `plugins/DM/` and optionally `plugins/DM_<dm_mode>/`.
- For inbound text packets with a resolved channel name, also supports channel plugin chains under `plugins/CHAN_<channel name>/`.
- Actively refreshes local owner and channel metadata on startup and periodically through read-only `ADMIN_APP` queries so DM and channel routing do not depend solely on incidental traffic.
- Hot-reloads changed handler files without restarting the proxy.
- Calls optional plugin entry points `handle_packet(event, api)`, `handle_client_call(event, api)`, and `tick(event, api)`.
- Exposes a host-extension API so plugins can inspect mesh packets, persist state, and emit reply packets through the attached node without replacing firmware behavior.
- When started through the wrapper, the `meshtastic_protocol.py` sidecar stays off by default and must be enabled explicitly if you want a second TCP client attached automatically.
- Writes a status snapshot JSON file used by wrappers and direct tools for auto-detection, health checks, and debugging.
- Exports broker lease state including whether the current control owner has a confirmed admin session and how long that lease has left.
- On Linux, can be installed as a systemd user service for automatic startup with logs sent to journald or syslog-compatible collectors.
- Persistent service settings for the Linux autostart units live in `.runtime/meshtastic/service.env`.
- On first `proxy-autostart-install`, the wrapper creates `.runtime/meshtastic/service.env`, prints its location, and exits so the file can be reviewed before installing the units.
- After the units already exist, edits to `.runtime/meshtastic/service.env` only require restarting the service; rerun `proxy-autostart-install` only when the unit files themselves need refreshing.

For full plugin architecture details, see [meshtastic_plugins.md](./meshtastic_plugins.md).

## Troubleshooting

- If `proxy-start` says healthcheck failed, inspect `.runtime/meshtastic/proxy.log` first.
- If `proxy-status` is `unhealthy`, the process exists but the TCP endpoint is not reachable; check for bind conflicts or a crashed reader loop.
- If serial diagnostics or flashing are needed, stop the proxy first; the proxy intentionally owns `/dev/ttyUSB0` exclusively.
- If tools pick the wrong transport, inspect `.runtime/meshtastic/proxy-status.json` and run `./setup/meshtastic-python.sh target-debug`.
- If control ownership seems stuck after a failed client operation, inspect the lease fields in `proxy-status --json`; unconfirmed claims should expire quickly, while confirmed admin sessions hold the lease longer.
- If the Linux autostart service is installed, use `./setup/meshtastic-python.sh proxy-log` or `journalctl --user -u meshtastic-proxy.service -f` instead of tailing the file log.
- A systemd user service starts after login by default. For startup before login after reboot, enable lingering for the user.
- `proxy-autostart-status` prints the effective runtime root and proxy status snapshot path so it is obvious which repo checkout the service uses.
- Re-running `proxy-autostart-install` refreshes the unit files but preserves `.runtime/meshtastic/service.env`; use it for unit refreshes, not for ordinary config changes.
- Plugin exceptions are logged but do not terminate the proxy. Under systemd, they show up in the service journal and any attached syslog-compatible collector.
- Plugin state is stored under the proxy runtime directory in `plugins/<PLUGIN_NAME>/`, separate from the source `plugins/*.handler.py` files.
- Plugins can optionally expose a self-contained CLI entry point via `plugin_command(argv, api)`, callable through `tools/meshtastic_plugins.py`.

## Architecture

- Entry point: `tools/meshtastic_proxy.py`.
- Core components:
  - `MeshtasticProxy`: process lifecycle, TCP listener, serial reader, client threads.
  - `ClientConnection`: per-client socket wrapper with serialized sends.
  - `MeshtasticBroker`: frame-aware arbitration for client writes and radio-side protocol observation.
  - `MeshtasticPluginManager`: filename-based plugin discovery, hot reload, and periodic ticks.
- Data flow:
  - serial bytes are read in one thread, observed by the broker, written into the status snapshot, then broadcast to all clients.
  - decoded radio packets also dispatch to matching `handle_packet()` plugins before broadcast.
  - client bytes are parsed by the broker; allowed frames go to serial, denied control writes get a direct broker error response.
  - forwarded client packets also dispatch to matching `handle_client_call()` plugins.
  - a periodic tick loop scans `plugins/*.handler.py` and calls `tick()` on any plugin that defines it.
- Extension API:
  - `send_app()` sends an app-port packet through the real node, keeping the firmware in charge of radio transmission.
  - `reply_app()` replies either to a local proxy client or to a mesh-originated packet, depending on the triggering event.
  - `plugin_store_append_jsonl()` and `plugin_store_read_jsonl()` provide simple durable state for protocol plugins.
  - event dictionaries include source and destination node IDs, packet IDs, want-response flags, and a `plugin_origin_likely` hint to avoid trivial self-triggered loops.
- State export:
  - status snapshot includes TCP bind data, serial-connected state, plugin directory, loaded plugin names, broker counters, control owner, lease timing, and latest observed admin session metadata.
  - wrapper JSON output also includes whether the proxy is managed manually or by the `meshtastic-proxy.service` systemd user unit.
- Service configuration:
  - systemd units use `EnvironmentFile=.runtime/meshtastic/service.env` for the serial port, bind host, connect host, TCP port, baud rate, and protocol log name.

## Plugin Example

```python
def handle_packet(event, api):
    if event["portnum_name"] != "TEXT_MESSAGE_APP":
        return
    api["logger"].info("text packet: %r", event["payload"])


def tick(event, api):
    for client in api["list_clients"]():
        api["logger"].debug("client connected: %s", client["label"])
```

Current repo plugins:

- `plugins/STORE_FORWARD_APP.handler.py` provides a host-side store-and-forward extension that can answer both local proxy requests and mesh-originated requests, print stats, and run daily retention cleanup from `tick()`.
- `plugins/TEXT_MESSAGE_APP.handler.py` persists text packets into content-addressed JSON blobs plus dated event logs and supplies the replay history used by `STORE_FORWARD_APP`.

Example plugin tool usage:

```bash
tools/meshtastic_plugins.py STORE_FORWARD_APP stats
tools/meshtastic_plugins.py STORE_FORWARD_APP config --replay-duplicates yes
```

`PRIVATE_APP` subtype routing rules:

- JSON payloads with a top-level string `type` field route to `plugins/PRIVATE_APP.<type>.handler.py`
- payloads beginning with `type=<value>` route to `plugins/PRIVATE_APP.<value>.handler.py`
- if no subtype-specific handler exists, the proxy falls back to `plugins/PRIVATE_APP.handler.py`

Direct-message routing rules:

- only inbound `TEXT_MESSAGE_APP` packets with `packet_to != 0` enter the DM chain
- the base namespace is `plugins/DM/`
- if the proxy config file defines `dm_mode`, a second namespace `plugins/DM_<dm_mode>/` is appended after the base namespace
- per namespace, the order is `handler_first.py`, first-word handler, sender-shortname handler, then `handler.py`
- a DM handler can return `{"continue_chain": True}` to continue or also include `message: <mesh_pb2.FromRadio>` to rewrite the packet seen by downstream DM handlers

For the full DM behavior, examples, and caveats around sender short names, see [meshtastic_plugins.md](./meshtastic_plugins.md).

Channel-message routing rules:

- only inbound `TEXT_MESSAGE_APP` packets with a resolvable numeric packet channel enter the channel chain
- the proxy queries and refreshes channel names through `ADMIN_APP.get_channel_request/get_channel_response`
- the proxy queries and refreshes the local short name through `ADMIN_APP.get_owner_request/get_owner_response`
- per channel namespace, the order is `handler_alltraffic.py`, `handler_first.py`, first-word command handler, then `handler.py`
- except for `handler_alltraffic.py`, handlers only run if the text starts with the local short name, optionally prefixed with `@`
- by default, channel plugins are blocked on a `PRIMARY` channel using `none` or `default` PSK; opt in explicitly if you really want automation there

For the full channel-plugin behavior and examples, see [meshtastic_plugins.md](./meshtastic_plugins.md).
