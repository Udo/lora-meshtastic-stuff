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

- Owns the single Meshtastic serial device and exposes a Meshtastic-compatible TCP endpoint for local clients.
- Broadcasts radio output to all connected TCP clients.
- Delegates client-to-radio arbitration to `meshtastic_broker.py`.
- When started through the wrapper, it auto-starts the `meshtastic_protocol.py` sidecar so a historical archive is collected automatically.
- Writes a status snapshot JSON file used by wrappers and direct tools for auto-detection, health checks, and debugging.
- Exports broker lease state including whether the current control owner has a confirmed admin session and how long that lease has left.
- On Linux, can be installed as a systemd user service for automatic startup with logs sent to journald or syslog-compatible collectors.

## Troubleshooting

- If `proxy-start` says healthcheck failed, inspect `.runtime/meshtastic/proxy.log` first.
- If `proxy-status` is `unhealthy`, the process exists but the TCP endpoint is not reachable; check for bind conflicts or a crashed reader loop.
- If serial diagnostics or flashing are needed, stop the proxy first; the proxy intentionally owns `/dev/ttyUSB0` exclusively.
- If tools pick the wrong transport, inspect `.runtime/meshtastic/proxy-status.json` and run `./setup/meshtastic-python.sh target-debug`.
- If control ownership seems stuck after a failed client operation, inspect the lease fields in `proxy-status --json`; unconfirmed claims should expire quickly, while confirmed admin sessions hold the lease longer.
- If the Linux autostart service is installed, use `./setup/meshtastic-python.sh proxy-log` or `journalctl --user -u meshtastic-proxy.service -f` instead of tailing the file log.
- A systemd user service starts after login by default. For startup before login after reboot, enable lingering for the user.
- `proxy-autostart-status` prints the effective runtime root and proxy status snapshot path so it is obvious which repo checkout the service uses.

## Architecture

- Entry point: `tools/meshtastic_proxy.py`.
- Core components:
  - `MeshtasticProxy`: process lifecycle, TCP listener, serial reader, client threads.
  - `ClientConnection`: per-client socket wrapper with serialized sends.
  - `MeshtasticBroker`: frame-aware arbitration for client writes and radio-side protocol observation.
- Data flow:
  - serial bytes are read in one thread, observed by the broker, written into the status snapshot, then broadcast to all clients.
  - client bytes are parsed by the broker; allowed frames go to serial, denied control writes get a direct broker error response.
- State export:
  - status snapshot includes TCP bind data, serial-connected state, broker counters, control owner, lease timing, and latest observed admin session metadata.
  - wrapper JSON output also includes whether the proxy is managed manually or by the `meshtastic-proxy.service` systemd user unit.
