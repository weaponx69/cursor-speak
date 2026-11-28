# Cursor Speak

A small Linux desktop app that reads new [Cursor CLI](https://cursor.com/cli)
(`cursor-agent`) replies out loud. It opens a small window (and a tray icon when
one is available), listens to your chats, and speaks each finished reply once,
leaving out code blocks, commands, file paths and tool output.

- Reads **only the final reply** of each turn, never in-progress text.
- Watches **all** chats under `~/.cursor/chats`, so a new session works with no setup.
- **Doesn't speak old replies** when it starts.
- Opens chat databases **read-only**. It never writes to them and never touches
  the `cursor-agent` process. It only types when you use voice input in
  "type" mode.
- Local neural voice ([piper](https://github.com/OHF-Voice/piper1-gpl)), with
  `espeak-ng` or `spd-say` as a fallback.
- **Voice input (push-to-talk):** press a shortcut, talk, and your words start a
  new `cursor-agent` session or are typed into the one you're in. Speech is
  recognised **on your computer** with
  [faster-whisper](https://github.com/SYSTRAN/faster-whisper); nothing is sent
  to a cloud speech service. Replies are still read out loud, so you get
  two-way voice.

## Install (Ubuntu 24.04+ / Debian)

```sh
./build-deb.sh
sudo apt install ./dist-deb/cursor-speak_0.1.5_all.deb
```

Then start **Cursor Speak** from the app menu (or run `cursor-speak`). A small
window appears with pause, replay and voice status; close it to hide to the tray
when a tray icon is available, or to quit if not. It also starts at login,
hidden (turn that off from the window or tray menu). On first run it downloads
piper and a voice into `~/.local/share/cursor-speak` (about 80 MB, once, no
sudo); until that finishes it uses the fallback voice. The package itself
contains only the app.

`apt` pulls in `python3-gi`, `gir1.2-ayatanaappindicator3-0.1`, `python3-venv`,
and a sound player, plus `speech-dispatcher` or `espeak-ng`.

## Install (AppImage, any distro)

Download `Cursor_Speak-<version>-x86_64.AppImage` from the
[releases page](https://github.com/weaponx69/cursor-speak/releases), then:

```sh
chmod +x Cursor_Speak-*-x86_64.AppImage
./Cursor_Speak-*-x86_64.AppImage
```

It updates in place with AppImageUpdate or any tool that reads the embedded zsync
info. Turn on **Start at login** from the window or tray menu to have it launch
this AppImage in the background at login (turn it off and on again if you move
the file).

Release AppImages bundle Python 3.10, PyGObject and GTK 3, built on Ubuntu 22.04
so they run there and on later distros, including without those packages on the
host. They still use a host `python3` (with the `venv` module) for the optional
first-run piper voice, and a host audio player / `espeak-ng` as a fallback.

Without FUSE: `./Cursor_Speak-*.AppImage --appimage-extract-and-run`.

### Tray icon on GNOME

Ubuntu ships the *AppIndicator and KStatusNotifierItem Support* extension, which
shows the tray icon. If you use stock GNOME, install and enable
`gnome-shell-extension-appindicator`. With no tray available, Cursor Speak shows
a small window with the same buttons instead.

## Use

Window and tray menu: **Pause / Resume**, **Stop speaking**, **Replay last**,
**Speak a test phrase**, **Start at login**, **Quit**. Closing the window hides
it to the tray if a tray icon is present; otherwise the app quits. Launch with
`--background` (or `--hidden`) to start without a window, as the login entry does.

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
cursor-speak listen [--toggle]   voice input, see below
cursor-speak shortcut install    push-to-talk keyboard shortcut
```

Log: `~/.local/state/cursor-speak/cursor-speak.log`.

## Voice input (talk to Cursor)

1. Register the keyboard shortcut (GNOME; picks a key no other shortcut uses,
   trying **Super+Alt+Space** first):

   ```sh
   cursor-speak shortcut install          # or: --key '<Super><Control>space'
   cursor-speak shortcut status | remove
   ```

2. Press the shortcut and talk. A notification (and a red microphone tray icon)
   shows it's listening. Press the shortcut again to send, or just stop talking
   for 1.5 seconds. The **Talk** button in the window does the same.
3. Your words go where the window's **Voice input** menu says:
   - **Open a new cursor-agent** (default): a new terminal (Ptyxis, GNOME
     Terminal or Console) running `cursor-agent "<what you said>"`.
   - **Type into the focused window**: click into a running `cursor-agent` (or
     anything else) first. Optionally press Enter afterwards. Uses `ydotool`;
     `wtype` on non-GNOME Wayland; otherwise the text is copied to the clipboard
     and a notification tells you to paste.
   - **Copy to the clipboard**.

The first time, it downloads faster-whisper and the `base.en` model (about
200 MB) into `~/.local/share/cursor-speak` in the background; press the
shortcut again when the "ready" notification appears. Transcribing a sentence
takes about a second on a modern CPU.

```
cursor-speak listen                 record until you pause, then send
cursor-speak listen --toggle        start, or stop and send (what the shortcut runs)
cursor-speak listen --mode type --enter
cursor-speak listen --file x.wav --mode print   transcribe a file, print the text
cursor-speak listen --check         what it will use, and what's missing
cursor-speak setup-stt [--model small.en]
```

### Typing with ydotool (one-time, needs sudo)

GNOME doesn't let apps type into other windows, so typing goes through
`ydotoold`, which needs `/dev/uinput`:

```sh
sudo apt install ydotool
sudo usermod -aG input $USER       # the package's udev rule gives the input group /dev/uinput
# log out and back in, then (no sudo):
systemctl --user enable --now ydotool
cursor-speak listen --check        # "typing backend: ydotool"
```

`ydotool` types US-layout key codes, so other keyboard layouts may get some
characters wrong; use clipboard mode there.

While you talk, replies aren't read out (they're held and the newest is read
when you finish); turn that off with **Pause speech while I talk**.

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
| `listen_mode` | `new` | voice input goes to: `new` cursor-agent terminal, `type` into the focused window, `clipboard` |
| `listen_press_enter` | `false` | press Enter after typing (`type` mode) |
| `pause_tts_while_listening` | `true` | hold replies while you talk |
| `stt_model` | `base.en` | faster-whisper model: `tiny.en`, `base.en`, `small.en` (more accurate, slower) |
| `stt_language` | `en` | spoken language (use a non-`.en` model for others) |
| `agent_cwd` | `~` | folder new `cursor-agent` sessions start in |
| `agent_command` | `cursor-agent` | command for new sessions |
| `listen_silence_seconds` | `1.5` | pause that ends a recording |
| `listen_max_seconds` | `60` | longest recording |
| `listen_threshold` | `500` | mic level that counts as speech (raise it in a noisy room) |

## How it works

`~/.cursor/chats/<workspace>/<chat id>/store.db` is a SQLite blob store. The
`meta` row names a root blob (protobuf) listing the conversation's message blobs
in order; messages are JSON. Every 2 seconds the app compares file sizes and
times (no database access). A chat that changed and then stayed quiet is opened
read-only, and if its last message is a prose-only assistant message (no tool
calls) that hasn't been seen, it is cleaned up (`textclean.py`) and spoken.
New chat folders (a new `cursor-agent` session, e.g. one opened by voice
input) are picked up on the next check.

Voice input: `listen.py` records 16 kHz mono with `pw-record` (or `parecord` /
`arecord`) and ends the take on a pause (`recorder.py`); `stt_worker.py` runs
faster-whisper (int8 on the CPU) in its own venv, `~/.local/share/cursor-speak/stt-venv`;
`deliver.py` opens the terminal or types; `shortcut.py` writes the GNOME custom
shortcut with `gsettings`.

## Remove

```sh
cursor-speak shortcut remove
sudo apt remove cursor-speak
rm -rf ~/.local/share/cursor-speak ~/.local/state/cursor-speak ~/.config/cursor-speak
```

## Develop

```sh
bin/cursor-speak-dev -v        # run from the source tree
python3 -m unittest discover -s tests
./build-deb.sh
```

Layout: `src/cursor_speak/` (app), `data/` (desktop file, AppStream metadata, icons),
`packaging/DEBIAN/` (control and maintainer scripts), `packaging/appimage/`
(AppRun, Python/GTK bundle helper, window smoke test), `build-deb.sh`,
`build-appimage.sh`, `.github/workflows/release.yml`.

### Build the AppImage

```sh
./build-appimage.sh      # -> dist-appimage/Cursor_Speak-<version>-x86_64.AppImage (+ .zsync)
```

Downloads `appimagetool` (and, when bundling, `linuxdeploy`) into `build/tools`
the first time, and validates the desktop file and the AppStream metadata
(`appstreamcli validate`) before building. Set `VALIDATE_URLS=1` to also check
that the URLs in the metadata resolve. Official releases set `BUNDLE_PYTHON_GTK`
implicitly by building on Ubuntu 22.04; on a newer distro the local script
stays thin unless you export `BUNDLE_PYTHON_GTK=1`.

## Release

Bump `__version__` in `src/cursor_speak/__init__.py` and add a `<release>` entry to
`data/io.github.weaponx69.CursorSpeak.appdata.xml`, then:

```sh
git tag v0.1.5 && git push origin v0.1.5
```

The `release` workflow builds the `.deb` and the AppImage and attaches them (plus
`SHA256SUMS` and the `.zsync` file) to a GitHub release. To list the AppImage on
[AppImageHub](https://appimage.github.io), open a pull request there that adds
`data/Cursor_Speak` with the repository URL once a release exists.

## License

MIT, see `LICENSE`. Piper and the voice models are downloaded separately and have
their own licenses.
