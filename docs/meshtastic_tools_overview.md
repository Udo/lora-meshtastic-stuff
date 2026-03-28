# Meshtastic Tools Overview

## Usage

```bash
./setup/meshtastic-python.sh status summary
./setup/meshtastic-python.sh status channels
./setup/meshtastic-python.sh get range_test.sender
./setup/meshtastic-python.sh set range_test.sender 0
./setup/meshtastic-python.sh channels list
./setup/meshtastic-python.sh channels url
./setup/meshtastic-python.sh telemetry --type environment
./setup/meshtastic-python.sh telemetry cached --type environment
./setup/meshtastic-python.sh monitor --only connection,node
./setup/meshtastic-python.sh messages sync mesh-chat
./setup/meshtastic-python.sh protocol mesh-archive --quiet
./setup/meshtastic-python.sh proxy-start
./setup/meshtastic-python.sh console
./setup/meshtastic-python.sh target-debug
./setup/meshtastic-python.sh target-debug --brief
```

## Function

- For a primer on the main Meshtastic application protocols and app ports, see [meshtastic_protocols.md](./meshtastic_protocols.md).
- `meshtastic_status.py` is the read-oriented inspection tool for summary, config, channels, nodes, and a few Meshtastic CLI passthrough operations.
- The wrapper-level `get` and `set` commands expose raw Meshtastic preference access for fields like `range_test.sender` or `telemetry.environment_update_interval`.
- The wrapper-level `channels` command group exposes Meshtastic channel inspection and common channel-management actions such as URL export, add, delete, set, enable, disable, and QR export.
- For the full `get` / `set` field list, see [meshtastic_get_set_fields.md](./meshtastic_get_set_fields.md).
- The wrapper-level `telemetry` command uses `meshtastic_status.py telemetry` for either active polling or cached telemetry display, with direct neighbors preferred over multihop nodes.
- `meshtastic_monitor.py` is the continuous event stream consumer for connection, node, receive, and optional log topics.
- `meshtastic_messages.py` is the lightweight send-and-transcript tool for private sends plus public/private message logging into `~/.local/log/meshtastic/*.log`, with local `tail` and `grep` helpers for those transcript files.
- `meshtastic_protocol.py` is the broad protocol/event archivist for long-running message, telemetry, and housekeeping capture into the same transcript directory.
- `meshtastic_proxy.py` is the long-running serial-owning TCP endpoint that lets multiple local clients share one radio connection.
- `meshtastic_broker.py` is the frame-aware policy layer inside the proxy that arbitrates mutating control traffic.
- `setup/meshtastic-python.sh` is the operational wrapper that bootstraps the environment, manages the proxy lifecycle, and routes user-facing commands through the correct target.
- On Linux, the wrapper can install the proxy as a systemd user service so it auto-starts and logs to journald.

## Troubleshooting

- If anything involving transport selection is unclear, run `./setup/meshtastic-python.sh target-debug` first.
- If multiple tools need the radio at once, prefer `proxy-start` and let the direct tools auto-detect the healthy local proxy.
- `proxy-start` auto-starts the protocol logger sidecar, so the long-running shared-UART workflow now archives protocol events by default.
- If peer lookup is ambiguous, use `./setup/meshtastic-python.sh contacts list` and send to the exact node ID instead of a short prefix.
- If a tool unexpectedly falls back to serial, inspect `.runtime/meshtastic/proxy-status.json` and confirm the local TCP endpoint is reachable with `proxy-check`.
- If control writes are denied, inspect `proxy-status --json` for the current owner, whether the lease is confirmed, and how much lease time remains.
- If the proxy is installed as a systemd user service, inspect logs with `proxy-log` or `journalctl --user -u meshtastic-proxy.service`.

## Architecture

- Shared target resolution lives in `tools/_meshtastic_common.py`.
- Shared transport connection setup and connection error formatting also live in `tools/_meshtastic_common.py`.
- The wrapper and direct tools all follow the same transport precedence: explicit host, `MESHTASTIC_HOST`, healthy local proxy, then serial fallback.
- The proxy owns `/dev/ttyUSB0` once and exports a Meshtastic-compatible TCP stream on `127.0.0.1:4403` by default.
- On Linux, that proxy can be promoted from an ad hoc background process to a systemd user service managed by the wrapper.
- The broker parses framed Meshtastic traffic so control ownership is enforced on protocol boundaries instead of raw bytes.
- Status JSON under `.runtime/meshtastic/proxy-status.json` is the contract between the long-running proxy process and the short-lived wrapper or tool invocations.
