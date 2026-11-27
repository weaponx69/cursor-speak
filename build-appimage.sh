#!/bin/sh
# Build dist-appimage/Cursor_Speak-<version>-x86_64.AppImage (+ .zsync). No root needed.
#
# The app is pure Python and runs on the host's python3 + PyGObject, so the AppImage
# is small and works on most desktop distros. Host requirements are listed in README.md.
#
# Tools: appimagetool is downloaded to build/tools if it isn't on PATH (override with
# APPIMAGETOOL=/path). zsyncmake comes from the `zsync` package; if it is missing the
# script tries `apt download zsync` and unpacks it locally (still no root).
set -eu
cd "$(dirname "$0")"

VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' src/cursor_speak/__init__.py)"
ID=io.github.weaponx69.CursorSpeak
UPDATE_INFO="gh-releases-zsync|weaponx69|cursor-speak|latest|Cursor_Speak-*x86_64.AppImage.zsync"
APPDIR=build/AppDir
OUT=dist-appimage
OUTFILE="Cursor_Speak-${VERSION}-x86_64.AppImage"
TOOLS="$(pwd)/build/tools"
mkdir -p "$TOOLS" "$OUT"

# --- tools ---------------------------------------------------------------------
if [ -n "${APPIMAGETOOL:-}" ]; then
    TOOL="$APPIMAGETOOL"
elif command -v appimagetool >/dev/null 2>&1; then
    TOOL="$(command -v appimagetool)"
else
    TOOL="$TOOLS/appimagetool-x86_64.AppImage"
    if [ ! -x "$TOOL" ]; then
        echo "Downloading appimagetool..."
        curl -fsSL -o "$TOOL" \
            https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
        chmod +x "$TOOL"
    fi
fi
if ! command -v zsyncmake >/dev/null 2>&1; then
    if [ ! -x "$TOOLS/zsync-root/usr/bin/zsyncmake" ]; then
        (cd "$TOOLS" && apt download zsync >/dev/null 2>&1 && dpkg -x zsync_*.deb zsync-root) \
            || echo "warning: zsyncmake not available; no .zsync file will be produced" >&2
    fi
    [ -d "$TOOLS/zsync-root/usr/bin" ] && PATH="$TOOLS/zsync-root/usr/bin:$PATH"
fi

# --- AppDir ----------------------------------------------------------------------
rm -rf "$APPDIR"
install -d "$APPDIR/usr/lib/cursor-speak" "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" \
    "$APPDIR/usr/share/metainfo" "$APPDIR/usr/share/icons/hicolor/scalable/apps" \
    "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp -r src/cursor_speak "$APPDIR/usr/lib/cursor-speak/"
find "$APPDIR/usr/lib/cursor-speak" -name __pycache__ -prune -exec rm -rf {} +
install -m 755 packaging/appimage/AppRun "$APPDIR/AppRun"
ln -s ../../AppRun "$APPDIR/usr/bin/cursor-speak"
install -m 644 data/$ID.desktop "$APPDIR/$ID.desktop"
install -m 644 data/$ID.desktop "$APPDIR/usr/share/applications/$ID.desktop"
install -m 644 data/$ID.appdata.xml "$APPDIR/usr/share/metainfo/$ID.appdata.xml"
install -m 644 data/icons/hicolor/scalable/apps/*.svg "$APPDIR/usr/share/icons/hicolor/scalable/apps/"
install -m 644 data/icons/hicolor/256x256/apps/cursor-speak.png "$APPDIR/usr/share/icons/hicolor/256x256/apps/"
install -m 644 data/icons/hicolor/256x256/apps/cursor-speak.png "$APPDIR/cursor-speak.png"
ln -s cursor-speak.png "$APPDIR/.DirIcon"
find "$APPDIR" -type d -exec chmod 755 {} +

# --- validate ----------------------------------------------------------------------
if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "$APPDIR/$ID.desktop"
    echo "desktop-file-validate: ok"
fi
if command -v appstreamcli >/dev/null 2>&1; then
    appstreamcli validate --no-net "$APPDIR/usr/share/metainfo/$ID.appdata.xml"
    # Also check that the homepage/bugtracker URLs resolve (needs the GitHub repo to be
    # public and to exist): VALIDATE_URLS=1 ./build-appimage.sh
    if [ "${VALIDATE_URLS:-0}" = "1" ]; then
        appstreamcli validate "$APPDIR/usr/share/metainfo/$ID.appdata.xml"
    fi
else
    echo "warning: appstreamcli not found; skipped AppStream validation" >&2
fi

# --- build ---------------------------------------------------------------------------
# appimagetool is itself an AppImage; extract-and-run avoids needing FUSE to build.
# AppStream is validated above, so appimagetool's own (network) check is skipped.
( cd "$OUT" && ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" --no-appstream \
    -u "$UPDATE_INFO" "../$APPDIR" "$OUTFILE" )

echo
ls -la "$OUT"
echo "Built $OUT/$OUTFILE"
