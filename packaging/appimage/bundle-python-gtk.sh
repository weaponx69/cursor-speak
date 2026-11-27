#!/bin/sh
# Copy the build host's Python, PyGObject and GTK typelibs into an AppDir.
# Intended to run on Ubuntu 22.04 so the resulting AppImage's glibc requirement
# is low enough for the AppImageHub test image (and any later distro).
set -eu

APPDIR="${1:?usage: bundle-python-gtk.sh AppDir}"
APPDIR="$(readlink -f "$APPDIR")"

if ! python3 -c 'import gi; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk' ; then
    echo "bundle-python-gtk.sh: need python3-gi and gir1.2-gtk-3.0 on the build host" >&2
    exit 1
fi

PYVER="$(python3 -c 'import sys; print("%d.%d" % (sys.version_info[0], sys.version_info[1]))')"
PYBIN="$(readlink -f "$(command -v python3)")"
echo "Bundling Python $PYVER from $PYBIN"

install -d "$APPDIR/usr/bin" "$APPDIR/usr/lib" "$APPDIR/usr/lib/python3/dist-packages"
install -m 755 "$PYBIN" "$APPDIR/usr/bin/python3"

# Copy the full stdlib (minus tests / tk / idle) so PYTHONHOME=$APPDIR/usr works.
STDLIB="$(python3 -c 'import sysconfig; print(sysconfig.get_path("stdlib"))')"
if [ ! -d "$STDLIB" ]; then
    echo "could not find Python stdlib at $STDLIB" >&2
    exit 1
fi
# stdlib typically /usr/lib/python3.10; keep that layout under $APPDIR/usr/lib
rel="${STDLIB#/}"
mkdir -p "$APPDIR/$(dirname "$rel")"
cp -a "$STDLIB" "$APPDIR/$(dirname "$rel")/"
rm -rf \
    "$APPDIR/$rel/test" \
    "$APPDIR/$rel/tests" \
    "$APPDIR/$rel/idlelib" \
    "$APPDIR/$rel/tkinter" \
    "$APPDIR/$rel/turtledemo"
# The config-* dir is link-time only and can be large.
find "$APPDIR/$rel" -maxdepth 1 -type d -name "config-${PYVER}-*" -exec rm -rf {} +

# libpython shared library
python3 - "$APPDIR" << 'END_LIBPY'
import os, sys, sysconfig, shutil
appdir = sys.argv[1]
candidates = []
lib = sysconfig.get_config_var("LDLIBRARY") or ""
libdir = sysconfig.get_config_var("LIBDIR") or ""
if lib and libdir:
    candidates.append(os.path.join(libdir, lib))
    multi = os.path.join(libdir, sysconfig.get_config_var("MULTIARCH") or "", lib)
    candidates.append(multi)
print("libpython candidates:", candidates)
for c in candidates:
    if os.path.isfile(c):
        dest_dir = os.path.join(appdir, "usr/lib")
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, os.path.basename(os.path.realpath(c)))
        if not os.path.exists(dest):
            shutil.copy2(os.path.realpath(c), dest)
        link = os.path.join(dest_dir, os.path.basename(c))
        if link != dest and not os.path.exists(link):
            try:
                os.symlink(os.path.basename(dest), link)
            except FileExistsError:
                pass
        print("copied", c, "->", dest)
        break
else:
    print("warning: libpython not found in expected paths", file=sys.stderr)
END_LIBPY

# PyGObject + pycairo from the distro packages (ABI-matched to this Python)
copy_pkg() {
    src="$1"
    if [ -e "$src" ]; then
        dest="$APPDIR/usr/lib/python3/dist-packages/$(basename "$src")"
        rm -rf "$dest"
        cp -a "$src" "$dest"
        echo "copied $src"
    fi
}
DIST="$(python3 -c 'import gi, os; print(os.path.dirname(os.path.dirname(gi.__file__)))')"
copy_pkg "$DIST/gi"
copy_pkg "$DIST/pygtkcompat"
if [ -d "$DIST/cairo" ]; then
    copy_pkg "$DIST/cairo"
fi

# Typelibs used by the UI (plugin-gtk also copies a set; this is a baseline).
install -d "$APPDIR/usr/lib/girepository-1.0"
python3 - "$APPDIR" << 'END_TYPELIBS'
import os, shutil, sys
appdir = sys.argv[1]
dest = os.path.join(appdir, "usr/lib/girepository-1.0")
os.makedirs(dest, exist_ok=True)
names = [
    "Gtk-3.0", "Gdk-3.0", "GdkPixbuf-2.0", "GdkX11-3.0",
    "Atk-1.0", "Pango-1.0", "PangoCairo-1.0", "PangoFT2-1.0",
    "GLib-2.0", "GObject-2.0", "Gio-2.0", "GModule-2.0",
    "cairo-1.0", "HarfBuzz-0.0", "freetype2-2.0", "fontconfig-2.0",
    "xlib-2.0", "Atspi-2.0",
    "AyatanaAppIndicator3-0.1", "AppIndicator3-0.1",
    "Dbusmenu-0.4",
]
paths = [
    "/usr/lib/x86_64-linux-gnu/girepository-1.0",
    "/usr/lib/girepository-1.0",
]
for name in names:
    fn = name + ".typelib"
    for d in paths:
        src = os.path.join(d, fn)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(dest, fn))
            print("typelib", src)
            break
END_TYPELIBS

# Wrapper so desktop Exec=cursor-speak runs the bundled interpreter.
# Replace a thin-build symlink to AppRun if one is already there.
rm -f "$APPDIR/usr/bin/cursor-speak"
cat > "$APPDIR/usr/bin/cursor-speak" << 'END_WRAP'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
# When run via linuxdeploy's AppRun, PYTHONHOME/GI env is already set.
exec "$HERE/python3" -m cursor_speak "$@"
END_WRAP
chmod 755 "$APPDIR/usr/bin/cursor-speak"

# Marker so AppRun uses the bundled interpreter (not a host python3).
touch "$APPDIR/usr/lib/cursor-speak/.bundled-python"

echo "Bundled Python $PYVER + PyGObject into $APPDIR"
