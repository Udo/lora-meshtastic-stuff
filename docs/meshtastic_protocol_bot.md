# meshtastic_protocol.py

## Usage

```bash
./setup/meshtastic-python.sh proxy-start
./setup/meshtastic-python.sh protocol
./setup/meshtastic-python.sh protocol mesh-archive --quiet
./setup/meshtastic-python.sh protocol mesh-archive --include-log-lines
./.venv/bin/python tools/meshtastic_protocol.py mesh-archive --quiet
```

## Function

- Maintains a live Meshtastic connection and persists protocol-level events into a grep-friendly transcript log.
- Auto-starts as a sidecar when `proxy-start` starts the long-running proxy/broker workflow.
- Logs received packets, including text messages, telemetry, routing packets, node info, position updates, and neighbor info.
- Logs housekeeping events such as connection establishment/loss and node updates.
- Uses the same transcript directory and single-line key/value storage convention as `meshtastic_messages.py`.
- Resolves transport the same way as the other repo tools: explicit `--host`, then `MESHTASTIC_HOST`, then a healthy local proxy or broker, then serial fallback.

## Log Format

- The default log file is `~/.local/log/meshtastic/protocol.log`.
- Records are single-line key/value text so they remain easy to inspect with `grep`, `rg`, `awk`, or the existing log helpers.
- Common fields include `ts`, `dir`, `scope`, `event`, `topic`, `from_id`, `to_id`, `portnum`, `packet_id`, and `summary`.
- Text messages also include `text`.
- Telemetry packets also include `telemetry_type`.
- `--include-log-lines` also stores `meshtastic.log.line` events from the local client library.

## Troubleshooting

- This tool is the persistent archive consumer; the proxy alone does not write a historical event log.
- When the proxy is started through the wrapper, this logger is started automatically against the local TCP endpoint.
- If you want the serial port to remain shareable, run the protocol logger through the local proxy instead of opening the UART directly.
- `--quiet` is appropriate for service/container use, because it suppresses stdout while still writing the log file.
- If the log path needs to move, use `MESHTASTIC_LOG_DIR` or `--log-dir`.

## Architecture

- Entry point: `tools/meshtastic_protocol.py`.
- Shared transport resolution: `tools/_meshtastic_common.py`.
- Shared log-file naming and line-formatting helpers: `tools/meshtastic_messages.py`.
- Runtime event source: Meshtastic pubsub topics consumed through a normal Meshtastic client connection.
