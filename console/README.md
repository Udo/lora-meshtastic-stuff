# Contact Console TUI

This directory vendors [`pdxlocations/contact`](https://github.com/pdxlocations/contact) version `1.5.6` under GPL-3.0-only so the Meshtastic console TUI can run from this repo without a separate `pip install contact`.

Use it directly:

```bash
./console/contact.sh --port /dev/ttyUSB0
```

Or through the existing setup wrapper:

```bash
./setup/meshtastic-python.sh console
./setup/meshtastic-python.sh console --settings
```

Notes:

- `./setup/meshtastic-python.sh bootstrap` is still the expected dependency setup step because Contact uses the repo's Meshtastic Python environment.
- Runtime state is written under `console/contact/` as `client.db`, `client.log`, `config.json`, and `node-configs/`.
- The vendored upstream metadata is kept in `console/contact-1.5.6.dist-info/`.
- The upstream license text is copied to `console/LICENSE.contact`.
