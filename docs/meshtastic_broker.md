# meshtastic_broker.py

## Usage

```bash
./.venv/bin/python -m unittest tests.test_meshtastic_broker
```

This module is not intended as a standalone CLI. It is consumed by `tools/meshtastic_proxy.py`.

## Function

- Parses Meshtastic stream frames instead of treating the TCP proxy as a blind byte relay.
- Classifies control traffic, especially admin writes and top-level control messages such as `want_config_id`, `disconnect`, and `xmodemPacket`.
- Enforces a single control owner for mutating traffic while allowing read-only admin requests and normal mesh traffic from other clients.
- Uses a short provisional lease for new control claims so abandoned sessions age out quickly.
- Upgrades the lease to a longer admin-session window when a radio-side admin response includes a session passkey.
- Observes radio-side admin responses and records the last seen session passkey and the owner label active when that response arrived.
- Returns frame-boundary metadata to the proxy so packet plugins can be dispatched without reparsing the raw stream.

## Troubleshooting

- If clients receive `[broker] control session busy`, another TCP client currently owns mutating admin traffic.
- If control ownership appears stale, check `proxy-status` for current owner, allowed/denied counts, confirmation state, remaining lease time, and last admin response metadata.
- If framing issues are suspected, the first things to inspect are oversize header drops, client disconnect churn, and whether the upstream client is speaking the standard Meshtastic framed stream.

## Architecture

- Core parser: `FrameParser`.
  - recognizes `START1`, `START2`, validates payload length, resynchronizes after malformed headers.
- Client-side policy:
  - `handle_client_bytes()` parses inbound TCP streams into raw chunks and Meshtastic frames.
  - `_should_forward_frame()` applies single-owner control arbitration.
  - provisional claims expire automatically if they are not reinforced by observed admin-session traffic.
- Radio-side observation:
  - `observe_radio_bytes()` parses `FromRadio` frames from the serial stream.
  - `_observe_fromradio()` watches `ADMIN_APP` payloads for session-bearing responses and extends confirmed leases.
- Exported state:
  - `snapshot()` returns broker counters, lease timing, and protocol-aware session metadata for the proxy status JSON.
- Coverage:
  - unit tests and loopback integration tests live in `tests/test_meshtastic_broker.py`.
