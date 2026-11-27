#!/bin/sh
# Launch the AppImage under Xvfb (or the current DISPLAY) and wait for a
# "Cursor Speak" window. Uses isolated XDG dirs so it cannot touch a running
# install or ~/.cursor chats, and swallows speech backends so nothing is audible.
set -eu

APP="${1:?usage: smoke-window.sh /path/to/Cursor_Speak-*.AppImage}"
TIMEOUT="${SMOKE_TIMEOUT:-20}"
WORKDIR="${SMOKE_WORKDIR:-$(mktemp -d /tmp/cursor-speak-smoke.XXXXXX)}"
cleanup() {
    if [ -n "${APP_PID:-}" ]; then
        kill "$APP_PID" 2>/dev/null || true
        wait "$APP_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

mkdir -p "$WORKDIR/data" "$WORKDIR/config" "$WORKDIR/state" "$WORKDIR/runtime" "$WORKDIR/chats"
export XDG_DATA_HOME="$WORKDIR/data"
export XDG_CONFIG_HOME="$WORKDIR/config"
export XDG_STATE_HOME="$WORKDIR/state"
export XDG_RUNTIME_DIR="$WORKDIR/runtime"
export CURSOR_SPEAK_CHATS="$WORKDIR/chats"
# Disable first-run piper so the smoke test stays offline and silent.
mkdir -p "$XDG_CONFIG_HOME/cursor-speak"
printf '%s\n' '{"auto_setup": false}' > "$XDG_CONFIG_HOME/cursor-speak/config.json"
# Silent "speech" backends: swallow any fallback TTS just in case.
mkdir -p "$WORKDIR/bin"
for cmd in espeak-ng espeak spd-say pw-play aplay ffplay paplay; do
    printf '#!/bin/sh\nexit 0\n' > "$WORKDIR/bin/$cmd"
    chmod +x "$WORKDIR/bin/$cmd"
done
export PATH="$WORKDIR/bin:$PATH"

export APPIMAGE_EXTRACT_AND_RUN="${APPIMAGE_EXTRACT_AND_RUN:-1}"
chmod +x "$APP" 2>/dev/null || true
"$APP" --verbose > "$WORKDIR/app.log" 2>&1 &
APP_PID=$!

i=0
while [ "$i" -lt "$TIMEOUT" ]; do
    if ! kill -0 "$APP_PID" 2>/dev/null; then
        echo "app exited before showing a window; log:" >&2
        cat "$WORKDIR/app.log" >&2 || true
        exit 1
    fi
    if command -v xwininfo >/dev/null 2>&1; then
        if xwininfo -root -tree 2>/dev/null | grep -F 'Cursor Speak' >/dev/null; then
            echo "window appeared after ${i}s"
            xwininfo -root -tree | grep -F 'Cursor Speak' || true
            exit 0
        fi
    fi
    i=$((i + 1))
    sleep 1
done

echo "no window after ${TIMEOUT}s; log:" >&2
cat "$WORKDIR/app.log" >&2 || true
if command -v xwininfo >/dev/null 2>&1; then
    echo "xwininfo -root -tree:" >&2
    xwininfo -root -tree >&2 || true
fi
exit 1
