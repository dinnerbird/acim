#!/bin/sh
# CoGS Image Manager, text edition: double-click launcher for the Mac.
# Finder opens .command files in Terminal. Plain Bourne shell on purpose
# (Tiger's bash 2.05 runs this too).
#
# Library folder, first match wins:
#   1. a path given on the command line
#   2. a "Disk Images" folder next to this file
#   3. the folder used last time (~/.cim-text-library)
#   4. ask (drag a folder from the Finder into the Terminal window)

DIR=`dirname "$0"`
DIR=`cd "$DIR" && pwd`
cd "$DIR"
# Inside "CIM Text.app" (modern Macs) this file sits in Contents/Resources;
# a "Disk Images" folder is then looked for next to the .app itself.
TOP="$DIR"
case "$DIR" in
    *.app/Contents/Resources) TOP=`cd "$DIR/../../.." && pwd` ;;
esac

PY=""
for c in python3 python python2.7 python2; do
    if command -v "$c" >/dev/null 2>&1; then
        PY="$c"
        break
    fi
done
if [ -z "$PY" ]; then
    echo "No Python found."
    echo "  Leopard (10.5) and later Macs through 12.2 ship one."
    echo "  Tiger (10.4): install Python 2.7.18 from python.org (the i386/PPC installer)."
    echo "  Monterey 12.3 and later: type python3 once and accept the install, or use python.org."
    echo
    echo "Press Return to close."
    read x
    exit 1
fi

LIB=""
if [ -n "$1" ] && [ -d "$1" ]; then
    LIB="$1"
    shift
fi
if [ -z "$LIB" ] && [ -d "$TOP/Disk Images" ]; then
    LIB="$TOP/Disk Images"
fi
if [ -z "$LIB" ] && [ -f "$HOME/.cim-text-library" ]; then
    LIB=`cat "$HOME/.cim-text-library"`
    [ -d "$LIB" ] || LIB=""
fi
while [ -z "$LIB" ] || [ ! -d "$LIB" ]; do
    echo "Folder of disk images to share (drag it from the Finder into this window, then Return):"
    printf "> "
    read LIB
    # Finder drops a trailing space and escapes spaces as "\ "
    LIB=`printf '%s' "$LIB" | sed -e 's/\\\\ / /g' -e 's/[ ]*$//' -e "s/^'//" -e "s/'$//"`
    [ -d "$LIB" ] || echo "Not a folder: $LIB"
done
printf '%s' "$LIB" > "$HOME/.cim-text-library"

clear
exec "$PY" "$DIR/cim.py" "$LIB" "$@"
