# meshtastic_status.py

## Usage

```bash
./setup/meshtastic-python.sh status summary
./setup/meshtastic-python.sh status nodes
./setup/meshtastic-python.sh status config lora network
./setup/meshtastic-python.sh status raw-info
./setup/meshtastic-python.sh status traceroute !0438ca24
/home/udo/work/lora-meshtastic-stuff/.venv/bin/python tools/meshtastic_status.py summary
/home/udo/work/lora-meshtastic-stuff/.venv/bin/python tools/meshtastic_status.py --host 127.0.0.1 --tcp-port 4403 nodes
```

## Function

- Presents a concise human-readable view of node identity, firmware, radio config, power state, and known mesh nodes.
- Reports the LoRa modem preset explicitly, including default enum-backed values such as `LONG_FAST` that may be omitted from JSON-shaped config output.
- Reports the configured device role and fixed-position state explicitly in the summary view, with coordinate context when available.
- Supports both structured local rendering and Meshtastic CLI passthrough operations for `raw-info` and `traceroute`.
- Auto-selects the transport target in this order: explicit `--host`, `MESHTASTIC_HOST`, healthy local proxy or broker from `.runtime/meshtastic/proxy-status.json`, then serial fallback.

## Troubleshooting

- `Could not connect to host:port`: the proxy or remote Meshtastic TCP endpoint is unreachable; use `./setup/meshtastic-python.sh proxy-check` or `proxy-status --json`.
- `Could not open /dev/ttyUSB0`: another process owns the serial device; the intended fix is usually to use or start the local proxy instead of fighting over the port.
- `raw-info` and `traceroute` on non-default TCP ports are limited by the Meshtastic CLI wrapper; use the standard `4403` port or direct serial access.
- If target selection is unclear, run `./setup/meshtastic-python.sh target-debug`.

## Architecture

- Entry point: `tools/meshtastic_status.py`.
- Transport resolution: `tools/_meshtastic_common.py` via `resolve_meshtastic_target()`.
- Runtime connection path:
  - TCP mode uses `meshtastic.tcp_interface.TCPInterface`.
  - Serial mode uses `meshtastic.serial_interface.SerialInterface`.
- Rendering path:
  - summary/config/nodes are built directly from the protobuf-backed interface object.
  - `raw-info` and `traceroute` shell out to `python -m meshtastic` using the repo venv.
- When the proxy is used, status reads through the brokered TCP stream rather than touching `/dev/ttyUSB0` directly.
