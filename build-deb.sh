#!/bin/sh
# Build dist-deb/cursor-speak_<version>_all.deb (no root needed, nothing is installed).
set -eu
cd "$(dirname "$0")"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' src/cursor_speak/__init__.py)"
PKG="cursor-speak_${VERSION}_all"
STAGE="build/$PKG"

rm -rf "$STAGE"
install -d "$STAGE/DEBIAN" "$STAGE/usr/lib/cursor-speak" "$STAGE/usr/bin" \
    "$STAGE/usr/share/applications" "$STAGE/etc/xdg/autostart" \
    "$STAGE/usr/share/icons/hicolor/scalable/apps" "$STAGE/usr/share/doc/cursor-speak"

cp -r src/cursor_speak "$STAGE/usr/lib/cursor-speak/"
find "$STAGE/usr/lib/cursor-speak" -name __pycache__ -prune -exec rm -rf {} +
chmod 755 "$STAGE/usr/lib/cursor-speak/cursor_speak/piper_worker.py" "$STAGE/usr/lib/cursor-speak/cursor_speak/stt_worker.py"

cat > "$STAGE/usr/bin/cursor-speak" <<'LAUNCH'
#!/bin/sh
PYTHONPATH="/usr/lib/cursor-speak${PYTHONPATH:+:$PYTHONPATH}" exec /usr/bin/python3 -m cursor_speak "$@"
LAUNCH
chmod 755 "$STAGE/usr/bin/cursor-speak"

install -m 644 data/io.github.weaponx69.CursorSpeak.desktop "$STAGE/usr/share/applications/io.github.weaponx69.CursorSpeak.desktop"
install -D -m 644 data/io.github.weaponx69.CursorSpeak.appdata.xml "$STAGE/usr/share/metainfo/io.github.weaponx69.CursorSpeak.appdata.xml"
install -D -m 644 data/icons/hicolor/256x256/apps/cursor-speak.png "$STAGE/usr/share/icons/hicolor/256x256/apps/cursor-speak.png"
install -m 644 data/cursor-speak-autostart.desktop "$STAGE/etc/xdg/autostart/cursor-speak.desktop"
install -m 644 data/icons/hicolor/scalable/apps/*.svg "$STAGE/usr/share/icons/hicolor/scalable/apps/"
install -m 644 README.md "$STAGE/usr/share/doc/cursor-speak/README.md"
cat > "$STAGE/usr/share/doc/cursor-speak/copyright" <<COPY
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: cursor-speak
Files: *
Copyright: 2026 Brian Perry <perry.brian@gmail.com>
License: MIT
$(sed -n '3,$p' LICENSE | sed 's/^$/./; s/^/ /')
COPY

SIZE="$(du -sk "$STAGE" | cut -f1)"
sed -e "s/@VERSION@/$VERSION/" -e "s/@SIZE@/$SIZE/" packaging/DEBIAN/control > "$STAGE/DEBIAN/control"
install -m 755 packaging/DEBIAN/postinst packaging/DEBIAN/postrm "$STAGE/DEBIAN/"

find "$STAGE" -type d -exec chmod 755 {} +
find "$STAGE" -type f -not -path "*/DEBIAN/*" -not -name cursor-speak -not -name piper_worker.py -not -name stt_worker.py -exec chmod 644 {} +
mkdir -p dist-deb
dpkg-deb --root-owner-group --build "$STAGE" "dist-deb/$PKG.deb"
echo
dpkg-deb --info "dist-deb/$PKG.deb"
echo
dpkg-deb --contents "dist-deb/$PKG.deb"
echo
echo "Built dist-deb/$PKG.deb  (install with: sudo apt install ./dist-deb/$PKG.deb)"
