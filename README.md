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
- **Conversation mode (hands-free):** talk to `cursor-agent` like a phone call.
  The mic stays on, what you say goes straight into the same agent chat (no
  typing or pasting, no ydotool, no sudo), you watch its progress in a window,
  and its answer is read aloud. Talk over it to interrupt.

## Install (Ubuntu 24.04+ / Debian)

```sh
./build-deb.sh
sudo apt install ./dist-deb/cursor-speak_0.1.7_all.deb
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
cursor-speak converse            hands-free conversation, see below
cursor-speak shortcut install    keyboard shortcuts
```

Log: `~/.local/state/cursor-speak/cursor-speak.log`.

## Voice input (talk to Cursor)

1. Register the keyboard shortcuts (GNOME; picks keys no other shortcut uses,
   trying **Super+Alt+Space** for push-to-talk and **Super+Alt+C** for
   conversation mode first):

   ```sh
   cursor-speak shortcut install          # both; --which listen|converse, --key '<Super><Control>space'
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

## Conversation mode (hands-free, like a phone call)

```sh
cursor-speak converse                      # in the folder you want the agent to work in
cursor-speak converse --workspace ~/myapp  # or name the folder
```

or press **Super+Alt+C** (registered by `cursor-speak shortcut install`; the
shortcut starts in your home folder unless `converse_workspace` is set, and the
window lets you pick a folder). Pressing it again pauses or resumes listening;
`cursor-speak converse --stop` (or **End**, or saying "goodbye") ends it.

Then just talk. A pause of about a second ends what you said; it is
transcribed on your computer and sent to `cursor-agent -p --resume <chat>
--workspace <folder>`, so every turn continues the same chat (Cursor Speak
remembers the chat per folder, so the next `converse` there picks it up again;
`--new` starts fresh, `--resume <chat id>` picks one). The conversation window
shows what you said, the agent's messages and each tool it runs (`$ npm test`,
`read src/app.ts`), and has a box for typing too. When the agent finishes,
its final answer is read aloud, without code blocks, commands or tool output.

| say | does |
| --- | --- |
| *anything else* | sends it to the agent (queued if it's still working) |
| "stop" / "cancel" | stops speaking and cancels the agent's current turn |
| "pause listening" | ignores everything until you say "resume" |
| "resume" | listens again |
| "new conversation" | starts a new chat in the same folder |
| "goodbye" | ends the conversation |

Commands only count when they're the whole sentence, so "stop the dev server"
still goes to the agent.

States: a coloured dot in the window (listening, hearing you, thinking,
speaking, paused) plus short quiet tones (rising: listening, a blip: sent to the
agent, falling: paused). Notifications appear only for start, pause and end.
Turn the tones off with `"converse_sounds": false`.

**Wake word** (off by default; useful in a noisy room): `cursor-speak converse
--wake-word "hey cursor"`, or `"converse_wake_word_enabled": true`. Then only
sentences starting with the phrase are sent; for 15 seconds after a reply,
follow-ups don't need it. "Stop" works without it while the agent is talking.

**Interrupting (barge-in) and echo.** While a reply is being read, the mic
needs a voice about 3x louder than usual (`converse_barge_factor`) to count,
and anything it hears that's mostly the reply's own words is ignored. With a
headset this works well. On laptop speakers the reply can still leak into the
mic; then either:

- `cursor-speak converse --echo-cancel` (or `"converse_echo_cancel": true`):
  runs PipeWire's WebRTC echo canceller in a private `pipewire` process for the
  length of the conversation (no root, nothing in your audio config changes);
  replies play through it and the mic is recorded from it; or
- `--barge-in command` (replies always finish; only voice commands like "stop"
  act while it talks) or `--barge-in off`.

**Agent permissions.** Turns run with `--trust` (a headless run can't answer the
workspace-trust prompt; turn off with `"converse_trust": false`). Commands that
need approval are not auto-approved unless you pass `--force` or set
`"converse_force": true`. `--model` and `"converse_agent_args"` pass through.

**Reading replies once.** The app's chat reader skips the chat a conversation
owns (it reads it itself). An older Cursor Speak (0.1.4 or earlier) that's
running gets paused for the conversation and resumed afterwards.

```
cursor-speak converse [--workspace DIR] [--resume CHAT_ID | --new]
                      [--wake-word PHRASE | --no-wake-word]
                      [--barge-in on|command|off] [--echo-cancel]
                      [--force] [--model M] [--terminal]
cursor-speak converse --toggle     pause/resume (or start)
cursor-speak converse --stop       end
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
| `converse_workspace` | `""` | conversation folder; empty = where it was started |
| `converse_wake_word_enabled` | `false` | require the wake word |
| `converse_wake_word` | `hey cursor` | the wake phrase |
| `converse_followup_seconds` | `15` | after a reply, follow-ups need no wake word |
| `converse_barge_in` | `on` | talking over a reply: `on` interrupts, `command` only voice commands, `off` |
| `converse_barge_factor` | `3.0` | how much louder you must be to interrupt |
| `converse_silence_seconds` | `1.0` | pause that ends what you said |
| `converse_threshold` | `500` | mic level that counts as speech (adapts upward to background noise) |
| `converse_echo_cancel` | `false` | PipeWire echo cancellation (speakers) |
| `converse_sounds` | `true` | state tones |
| `converse_trust` / `converse_force` | `true` / `false` | `cursor-agent --trust` / `--force` |
| `converse_model` | `""` | `cursor-agent --model` |

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

Conversation mode: `converse.py` keeps `pw-record` running and segments speech
with an adaptive energy detector (`recorder.Segmenter`, with pre-roll and a
raised bar while speaking); a long-lived `stt_worker.py --serve` keeps the
model loaded; `agent.py` runs one `cursor-agent -p --output-format stream-json`
per turn and reads the final answer (the text after the last tool call);
`converse_ui.py` is the window; `cues.py` makes the tones; `echo_cancel.py`
is the optional PipeWire echo canceller.

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
git tag v0.1.7 && git push origin v0.1.7
```

The `release` workflow builds the `.deb` and the AppImage and attaches them (plus
`SHA256SUMS` and the `.zsync` file) to a GitHub release. To list the AppImage on
[AppImageHub](https://appimage.github.io), open a pull request there that adds
`data/Cursor_Speak` with the repository URL once a release exists.

## License

MIT, see `LICENSE`. Piper and the voice models are downloaded separately and have
their own licenses.
