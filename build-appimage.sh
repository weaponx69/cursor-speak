#!/bin/sh
# Build dist-appimage/Cursor_Speak-<version>-x86_64.AppImage (+ .zsync). No root needed.
#
# On Ubuntu 22.04 (or when BUNDLE_PYTHON_GTK=1) the AppImage bundles Python,
# PyGObject and GTK 3 so it opens a window on the AppImageHub test image.
# Official releases are built by the GitHub workflow on ubuntu-22.04. Local
# builds on a newer distro stay "thin" (host Python + GTK) unless you force
# bundling, because a newer glibc would not run on Ubuntu 22.04.
#
# Tools: appimagetool (and, when bundling, linuxdeploy + linuxdeploy-plugin-gtk)
# are downloaded to build/tools if they aren't on PATH.
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

# --- should we bundle Python + GTK? -------------------------------------------
should_bundle() {
    case "${BUNDLE_PYTHON_GTK:-auto}" in
        1|yes|true) return 0 ;;
        0|no|false) return 1 ;;
    esac
    if [ -f /etc/os-release ]; then
        # Read in a subshell so NAME/ID/VERSION from os-release cannot
        # clobber this script's AppStream id and app version.
        os_version_id="$(. /etc/os-release && printf '%s' "${VERSION_ID:-}")"
        [ "$os_version_id" = "22.04" ]
        return $?
    fi
    return 1
}

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

download_tool() {
    # download_tool url dest
    if [ ! -x "$2" ]; then
        echo "Downloading $(basename "$2")..."
        curl -fsSL -o "$2" "$1"
        chmod +x "$2"
    fi
}

# --- AppDir ------------------------------------------------------------------
rm -rf "$APPDIR"
install -d "$APPDIR/usr/lib/cursor-speak" "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" \
    "$APPDIR/usr/share/metainfo" "$APPDIR/usr/share/icons/hicolor/scalable/apps" \
    "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp -r src/cursor_speak "$APPDIR/usr/lib/cursor-speak/"
find "$APPDIR/usr/lib/cursor-speak" -name __pycache__ -prune -exec rm -rf {} +
install -m 755 packaging/appimage/AppRun "$APPDIR/AppRun"
# Prefer a real binary name for the desktop Exec= line; the thin build can
# keep a symlink, the bundled build replaces this with a wrapper.
ln -sf ../../AppRun "$APPDIR/usr/bin/cursor-speak"
install -m 644 data/$ID.desktop "$APPDIR/$ID.desktop"
install -m 644 data/$ID.desktop "$APPDIR/usr/share/applications/$ID.desktop"
install -m 644 data/$ID.appdata.xml "$APPDIR/usr/share/metainfo/$ID.appdata.xml"
install -m 644 data/icons/hicolor/scalable/apps/*.svg "$APPDIR/usr/share/icons/hicolor/scalable/apps/"
install -m 644 data/icons/hicolor/256x256/apps/cursor-speak.png "$APPDIR/usr/share/icons/hicolor/256x256/apps/"
install -m 644 data/icons/hicolor/256x256/apps/cursor-speak.png "$APPDIR/cursor-speak.png"
ln -sf cursor-speak.png "$APPDIR/.DirIcon"
find "$APPDIR" -type d -exec chmod 755 {} +

BUNDLE=0
if should_bundle; then
    BUNDLE=1
    echo "Bundling Python + PyGObject + GTK 3 into the AppImage"
    ./packaging/appimage/bundle-python-gtk.sh "$APPDIR"
    install -d "$APPDIR/apprun-hooks"
    install -m 644 packaging/appimage/apprun-hook-python.sh "$APPDIR/apprun-hooks/cursor-speak-python.sh"

    LINUXDEPLOY="${LINUXDEPLOY:-$TOOLS/linuxdeploy-x86_64.AppImage}"
    GTK_PLUGIN="${GTK_PLUGIN:-$TOOLS/linuxdeploy-plugin-gtk.sh}"
    download_tool \
        "https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-x86_64.AppImage" \
        "$LINUXDEPLOY"
    download_tool \
        "https://raw.githubusercontent.com/linuxdeploy/linuxdeploy-plugin-gtk/master/linuxdeploy-plugin-gtk.sh" \
        "$GTK_PLUGIN"
    # linuxdeploy looks for plugins next to itself or on PATH. The default
    # download already lands the plugin next to linuxdeploy in $TOOLS; only
    # link when GTK_PLUGIN was overridden to another path.
    if [ "$(readlink -f "$GTK_PLUGIN")" != "$(readlink -f "$TOOLS/linuxdeploy-plugin-gtk.sh")" ]; then
        ln -sfn "$GTK_PLUGIN" "$TOOLS/linuxdeploy-plugin-gtk.sh"
    fi
    PATH="$TOOLS:$PATH"
    export PATH

    # Collect libraries from python, _gi*.so and libgtk so the GTK plugin
    # has something to work with (python itself is not linked to GTK).
    # Build the linuxdeploy argv in a file to handle spaces safely.
    LIBARGS="$TOOLS/linuxdeploy-libs.txt"
    : > "$LIBARGS"
    find "$APPDIR" -type f \( -name '_gi*.so' -o -name '_gi_cairo*.so' -o -name '_cairo*.so' \) \
        -print > "$LIBARGS" || true
    for so in \
        /usr/lib/x86_64-linux-gnu/libgtk-3.so.0 \
        /usr/lib/x86_64-linux-gnu/libgdk-3.so.0 \
        /usr/lib/x86_64-linux-gnu/libayatana-appindicator3.so.1 \
        /usr/lib/x86_64-linux-gnu/libappindicator3.so.1
    do
        [ -e "$so" ] && echo "$so" >> "$LIBARGS"
    done

    set -- --appdir "$APPDIR" --executable "$APPDIR/usr/bin/python3" \
        --desktop-file "$APPDIR/$ID.desktop" \
        --icon-file "$APPDIR/cursor-speak.png" \
        --plugin gtk
    while IFS= read -r so; do
        [ -n "$so" ] || continue
        set -- "$@" --library "$so"
    done < "$LIBARGS"

    export DEPLOY_GTK_VERSION=3
    export APPIMAGE_EXTRACT_AND_RUN=1
    # Do not emit an AppImage here; we run appimagetool ourselves for the name
    # and zsync update information.
    echo "Running linuxdeploy + gtk plugin..."
    "$LINUXDEPLOY" "$@"

    # linuxdeploy patches RPATH on libraries it copies into usr/lib, not on
    # the Python extensions that stay in dist-packages / lib-dynload.
    if command -v patchelf >/dev/null 2>&1; then
        LIBDIR="$APPDIR/usr/lib"
        find "$APPDIR" -type f \( -name '*.so' -o -name '*.so.*' \) | while read -r so; do
            case "$so" in
                */usr/lib/*/*.so|*/usr/lib/*/*.so.*|*/dist-packages/*|*/lib-dynload/*|*/python3/*|*/python3.*/)
                    rel="$(python3 -c "import os; print(os.path.relpath('${LIBDIR}', os.path.dirname('${so}')))")"
                    patchelf --set-rpath "\$ORIGIN/$rel" "$so" 2>/dev/null || true
                    ;;
            esac
        done
        echo "patchelf: set RPATH on bundled Python extensions"
    else
        echo "warning: patchelf not found; Python extensions may miss bundled libs" >&2
    fi

    # linuxdeploy may replace AppRun; put ours back so we exec -m cursor_speak
    # and still source apprun-hooks (GTK plugin + our PYTHONHOME hook).
    install -m 755 packaging/appimage/AppRun "$APPDIR/AppRun"
else
    echo "Thin AppImage (host Python + GTK). Official releases bundle a runtime on Ubuntu 22.04."
    echo "Set BUNDLE_PYTHON_GTK=1 to bundle from this machine (may not run on older glibc)."
fi

# --- validate ----------------------------------------------------------------
if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "$APPDIR/$ID.desktop"
    echo "desktop-file-validate: ok"
fi
if command -v appstreamcli >/dev/null 2>&1; then
    appstreamcli validate --no-net "$APPDIR/usr/share/metainfo/$ID.appdata.xml"
    # Also check that the homepage/bugtracker URLs resolve (needs the GitHub repo
    # to be public and to exist): VALIDATE_URLS=1 ./build-appimage.sh
    if [ "${VALIDATE_URLS:-0}" = "1" ]; then
        appstreamcli validate "$APPDIR/usr/share/metainfo/$ID.appdata.xml"
    fi
else
    echo "warning: appstreamcli not found; skipped AppStream validation" >&2
fi

# --- build -------------------------------------------------------------------
# appimagetool is itself an AppImage; extract-and-run avoids needing FUSE to build.
# AppStream is validated above, so appimagetool's own (network) check is skipped.
( cd "$OUT" && ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" --no-appstream \
    -u "$UPDATE_INFO" "../$APPDIR" "$OUTFILE" )

if command -v zsyncmake >/dev/null 2>&1; then
    ( cd "$OUT" && zsyncmake -u "$OUTFILE" "$OUTFILE" )
fi

echo
ls -la "$OUT"
echo "Built $OUT/$OUTFILE"
if [ "$BUNDLE" = 1 ]; then
    echo "Runtime: bundled Python + GTK 3"
else
    echo "Runtime: host Python + GTK 3"
fi
