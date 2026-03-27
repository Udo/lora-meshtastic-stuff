# meshtastic_messages.py

## Usage

```bash
./setup/meshtastic-python.sh messages send WO67 "hello from the wrapper"
./setup/meshtastic-python.sh messages send !0439d098 "private test" --log-name worms-dm
./setup/meshtastic-python.sh messages sync mesh-chat
./setup/meshtastic-python.sh messages sync mesh-chat --scope private
./setup/meshtastic-python.sh messages tail mesh-chat --lines 20 --follow
./setup/meshtastic-python.sh messages grep mesh-chat 'scope="private"' --count
./setup/meshtastic-python.sh messages prune --days 14 --dry-run
/home/udo/work/lora-meshtastic-stuff/.venv/bin/python tools/meshtastic_messages.py sync mesh-chat --timeout 30
```

## Function

- Sends private text messages to a known peer by node ID, short name, long name, or unique prefix.
- Resolves transport the same way as the other repo tools: explicit `--host`, then `MESHTASTIC_HOST`, then a healthy local proxy or broker, then serial fallback.
- Appends grep-friendly single-line transcripts to `~/.local/log/meshtastic/<logname>.log`.
- Records live public `TEXT_MESSAGE_APP` traffic and private `PRIVATE_APP` traffic while `sync` is running.
- Provides `tail`, `grep`, and `prune` subcommands for transcript inspection and cleanup without touching the radio.

## Log Format

- Each line is key-value text rather than multiline JSON, so `grep`, `rg`, and `awk` work well.
- Common fields include `ts`, `dir`, `scope`, `from_id`, `from_short`, `to_id`, `status`, and `text`.
- `send` writes `dir="tx"` lines for private messages sent through the wrapper.
- `sync` writes `dir="rx"` lines for public and private packets received while connected.
- `MESHTASTIC_LOG_DIR` changes the default transcript directory globally, and `--log-dir` overrides it for one command.
- `tail --follow` continues streaming appended lines until interrupted, or until `--follow-seconds` expires.
- `grep --count` prints only the number of matching lines.
- `prune --days N` deletes `.log` files older than `N` days from the selected transcript directory.

## Troubleshooting

- `sync` can only record packets seen while it is connected; Meshtastic radios do not offer a general message-history download for old traffic.
- If selector resolution fails, run `./setup/meshtastic-python.sh contacts list` first and reuse the exact node ID or short name shown there.
- If serial mode fails because the port is busy, start the local proxy and let the wrapper auto-route through it.
- Log names must be simple file names like `messages`, `mesh-chat`, or `worms-dm`; path separators are rejected on purpose.
- `tail` and `grep` require the transcript file to exist already; run `send` or `sync` first if the log has not been created yet.
- `prune` only touches `.log` files in the selected transcript directory. Use `--dry-run` before destructive cleanup if you want to inspect the targets first.

## Architecture

- Entry point: `tools/meshtastic_messages.py`.
- Transport resolution: `tools/_meshtastic_common.py` via `resolve_meshtastic_target()`.
- Send path: resolves the peer from the current NodeDB snapshot, sends with Meshtastic `PRIVATE_APP`, then appends the transmitted line to the chosen log.
- Sync path: subscribes to Meshtastic pubsub receive topics and writes only public text and private payload packets.
- Transcript path selection: `--log-dir`, then `MESHTASTIC_LOG_DIR`, then `~/.local/log/meshtastic`.
- Transcript maintenance: `tail` and `grep` work on existing transcript files, and `prune` deletes old `*.log` files by modification time.