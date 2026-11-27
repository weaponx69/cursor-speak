# Cursor Speak

A small Linux desktop app that reads new [Cursor CLI](https://cursor.com/cli)
(`cursor-agent`) replies out loud. It lives in the system tray, listens to your
chats, and speaks each finished reply once, leaving out code blocks, commands,
file paths and tool output.

- Reads **only the final reply** of each turn, never in-progress text.
- Watches **all** chats under `~/.cursor/chats`, so a new session works with no setup.
- **Doesn't speak old replies** when it starts.
- Opens chat databases **read-only**. It never writes to them, never touches the
  `cursor-agent` process, and never types into your terminal.
- Local neural voice ([piper](https://github.com/OHF-Voice/piper1-gpl)), with
  `espeak-ng` or `spd-say` as a fallback.

## Install (Ubuntu 24.04+ / Debian)

```sh
./build-deb.sh
sudo apt install ./dist-deb/cursor-speak_0.1.0_all.deb
```

Then start **Cursor Speak** from the app menu (or run `cursor-speak`). It also
starts at login (turn that off from the tray menu). On first run it downloads
piper and a voice into `~/.local/share/cursor-speak` (about 80 MB, once, no
sudo); until that finishes it uses the fallback voice. The package itself
contains only the app.

`apt` pulls in `python3-gi`, `gir1.2-ayatanaappindicator3-0.1`, `python3-venv`,
and a sound player, plus `speech-dispatcher` or `espeak-ng`.

### Tray icon on GNOME

Ubuntu ships the *AppIndicator and KStatusNotifierItem Support* extension, which
shows the tray icon. If you use stock GNOME, install and enable
`gnome-shell-extension-appindicator`. With no tray available, Cursor Speak shows
a small window with the same buttons instead.

## Use

Tray menu: **Pause / Resume**, **Stop speaking**, **Replay last**,
**Speak a test phrase**, **Start at login**, **Quit**.

While paused, new replies aren't spoken (they're still kept for **Replay last**).

Command line (talks to the running app):

```
cursor-speak              start the app
cursor-speak pause | resume | toggle
cursor-speak stop         stop the current speech
cursor-speak replay       read the last reply again
cursor-speak status       JSON status
cursor-speak say "text"   speak some text
cursor-speak test         speak a short test phrase
cursor-speak quit
cursor-speak latest       show what would be read for the newest reply (no speech)
cursor-speak setup        download piper and the voice now
cursor-speak autostart on|off|status
```

Log: `~/.local/state/cursor-speak/cursor-speak.log`.

## Configure

Optional `~/.config/cursor-speak/config.json`:

```json
{
  "voice": "en_US-ryan-high",
  "speed": 1.1,
  "volume": 0.8,
  "identifiers": "speak",
  "max_chars": 2500
}
```

| key | default | meaning |
| --- | --- | --- |
| `voice` | `en_US-lessac-medium` | any [piper voice](https://huggingface.co/rhasspy/piper-voices); downloaded on next start |
| `speed` | `1.0` | speaking rate |
| `volume` | `1.0` | 0.0 to 1.0 |
| `identifiers` | `speak` | `speak` reads names like `PlayerInput` as "Player Input"; `drop` skips them |
| `max_chars` | `2500` | longer replies are cut at a sentence |
| `quiet_seconds` | `3.0` | how long a chat must be quiet before its last message counts as final |
| `auto_setup` | `true` | download piper and the voice on first run |

## How it works

`~/.cursor/chats/<workspace>/<chat id>/store.db` is a SQLite blob store. The
`meta` row names a root blob (protobuf) listing the conversation's message blobs
in order; messages are JSON. Every 2 seconds the app compares file sizes and
times (no database access). A chat that changed and then stayed quiet is opened
read-only, and if its last message is a prose-only assistant message (no tool
calls) that hasn't been seen, it is cleaned up (`textclean.py`) and spoken.

## Remove

```sh
sudo apt remove cursor-speak
rm -rf ~/.local/share/cursor-speak ~/.local/state/cursor-speak ~/.config/cursor-speak
```

## Develop

```sh
bin/cursor-speak-dev -v        # run from the source tree
python3 -m unittest discover -s tests
./build-deb.sh
```

Layout: `src/cursor_speak/` (app), `data/` (desktop files, icons), `packaging/DEBIAN/`
(control and maintainer scripts), `build-deb.sh`.

## License

MIT, see `LICENSE`. Piper and the voice models are downloaded separately and have
their own licenses.
