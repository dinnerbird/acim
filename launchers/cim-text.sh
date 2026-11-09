#!/bin/sh
# CoGS Image Manager, text edition: Linux / BSD / NAS launcher.
#   ./cim-text.sh /path/to/disk/images [cim.py options]
# With no path it uses a "Disk Images" folder next to this file, then the
# folder used last time, then asks.
DIR=`dirname "$0"`
DIR=`cd "$DIR" && pwd`

PY=""
for c in python3 python python2.7 python2; do
    if command -v "$c" >/dev/null 2>&1; then
        PY="$c"
        break
    fi
done
if [ -z "$PY" ]; then
    echo "No Python found. Install python3 from your package manager." >&2
    exit 1
fi

LIB=""
if [ -n "$1" ] && [ -d "$1" ]; then
    LIB="$1"
    shift
fi
if [ -z "$LIB" ] && [ -d "$DIR/Disk Images" ]; then
    LIB="$DIR/Disk Images"
fi
if [ -z "$LIB" ] && [ -f "$HOME/.cim-text-library" ]; then
    LIB=`cat "$HOME/.cim-text-library"`
    [ -d "$LIB" ] || LIB=""
fi
while [ -z "$LIB" ] || [ ! -d "$LIB" ]; do
    printf "Folder of disk images to share: "
    read LIB
    [ -d "$LIB" ] || echo "Not a folder: $LIB"
done
printf '%s' "$LIB" > "$HOME/.cim-text-library"

exec "$PY" "$DIR/cim.py" "$LIB" "$@"
