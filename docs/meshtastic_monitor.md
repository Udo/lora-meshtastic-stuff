# meshtastic_monitor.py

## Usage

```bash
./setup/meshtastic-python.sh monitor
./setup/meshtastic-python.sh monitor --json
./setup/meshtastic-python.sh monitor --only connection,node
./setup/meshtastic-python.sh monitor --exclude log,routing
./setup/meshtastic-python.sh monitor --log-file logs/meshtastic-monitor.log
/home/udo/work/lora-meshtastic-stuff/.venv/bin/python tools/meshtastic_monitor.py --only connection
/home/udo/work/lora-meshtastic-stuff/.venv/bin/python tools/meshtastic_monitor.py --host 127.0.0.1 --tcp-port 4403 --json
```

## Function

- Maintains a live Meshtastic connection and streams connection, node, receive, and optional log events.
- Supports ANSI text mode and newline-delimited JSON mode.
- Supports inclusion and exclusion filters on Meshtastic topics and decoded packet categories.
- Uses the same target-selection order as the status tool: explicit `--host`, `MESHTASTIC_HOST`, healthy local proxy or broker, then serial fallback.

## Troubleshooting

- If the monitor fails immediately in serial mode, the device is probably already owned; use the local proxy instead of direct serial access.
- If no events appear, broaden filters first by removing `--only` and `--exclude`.
- If the connection target is ambiguous, confirm it with `./setup/meshtastic-python.sh target-debug`.
- If writing to a log file, ensure the destination directory is writable; the tool will create parent directories but cannot recover from permission failures.

## Architecture

- Entry point: `tools/meshtastic_monitor.py`.
- Transport resolution and shared connection/error helpers live in `tools/_meshtastic_common.py`.
- Event source:
  - subscribes to Meshtastic pubsub topics before connecting.
  - TCP mode uses an already-connected `TCPInterface` so connection events are captured correctly.
  - Serial mode uses deferred `SerialInterface.connect()` after subscription.
- Event shaping:
  - `topic_tags()` and `filter_matches()` implement topic and decoded-payload filters.
  - `event_summary()` reduces raw packet objects into readable summaries.
  - `strip_raw()` removes byte-heavy payload fields from console and JSON output.
- When routed through the proxy, the monitor consumes the shared brokered TCP endpoint rather than direct UART access.
