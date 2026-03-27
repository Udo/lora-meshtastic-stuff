# meshtastic_status.py

## Usage

```bash
./setup/meshtastic-python.sh status summary
./setup/meshtastic-python.sh status nodes
./setup/meshtastic-python.sh telemetry --type environment
./setup/meshtastic-python.sh telemetry cached --type environment
./setup/meshtastic-python.sh telemetry --type power --limit 2
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
- Can request telemetry from nearby nodes, with proximity defined by direct-neighbor preference, hop count, and observed SNR.
- Supports both structured local rendering and Meshtastic CLI passthrough operations for `raw-info` and `traceroute`.
- Auto-selects the transport target in this order: explicit `--host`, `MESHTASTIC_HOST`, healthy local proxy or broker from `.runtime/meshtastic/proxy-status.json`, then serial fallback.

## Telemetry Requests

- `status telemetry` defaults to active request/response polling.
- `status telemetry cached` prints only telemetry already cached on the local node from prior mesh traffic.
- Supported types are `environment`, `air-quality`, `power`, `device`, and `local-stats`.
- The default selection is the best direct neighbors first. If there are no direct neighbors with routing metadata, it falls back to the best known nodes overall.
- `--include-multihop` widens the scan after direct neighbors.
- `--limit N` caps how many nodes are queried, and `--json` emits machine-readable results.

## Troubleshooting

- `Could not connect to host:port`: the proxy or remote Meshtastic TCP endpoint is unreachable; use `./setup/meshtastic-python.sh proxy-check` or `proxy-status --json`.
- `Could not open /dev/ttyUSB0`: another process owns the serial device; the intended fix is usually to use or start the local proxy instead of fighting over the port.
- `raw-info` and `traceroute` on non-default TCP ports are limited by the Meshtastic CLI wrapper; use the standard `4403` port or direct serial access.
- Telemetry requests can still time out even for visible nodes if the remote firmware does not expose that telemetry type or telemetry replies are disabled on the destination node.
- Cached telemetry mode only shows values the local node has already received; if nothing has been heard yet, it will correctly return no cached results.
- If target selection is unclear, run `./setup/meshtastic-python.sh target-debug`.

## Architecture

- Entry point: `tools/meshtastic_status.py`.
- Transport resolution and shared connection/error helpers live in `tools/_meshtastic_common.py`.
- Runtime connection path:
  - TCP mode uses `meshtastic.tcp_interface.TCPInterface`.
  - Serial mode uses `meshtastic.serial_interface.SerialInterface`.
- Rendering path:
  - summary/config/nodes are built directly from the protobuf-backed interface object.
  - `raw-info` and `traceroute` shell out to `python -m meshtastic` using the repo venv.
- When the proxy is used, status reads through the brokered TCP stream rather than touching `/dev/ttyUSB0` directly.
