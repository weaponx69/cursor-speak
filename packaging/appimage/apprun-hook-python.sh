# Sourced by AppRun / linuxdeploy AppRun. Sets up the bundled CPython + typelibs.
# APPDIR is already set.
if [ -e "$APPDIR/usr/lib/cursor-speak/.bundled-python" ]; then
    export PYTHONHOME="$APPDIR/usr"
    _typelibs=""
    for _d in \
        "$APPDIR/usr/lib/girepository-1.0" \
        "$APPDIR/usr/lib/x86_64-linux-gnu/girepository-1.0"
    do
        [ -d "$_d" ] && _typelibs="${_typelibs:+$_typelibs:}$_d"
    done
    if [ -n "$_typelibs" ]; then
        export GI_TYPELIB_PATH="${_typelibs}${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}"
    fi
    export PYTHONPATH="$APPDIR/usr/lib/cursor-speak:$APPDIR/usr/lib/python3/dist-packages${PYTHONPATH:+:$PYTHONPATH}"
    unset _d _typelibs
fi
