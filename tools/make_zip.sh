#!/usr/bin/env bash
# Build the release zip for the text edition:
#   dist/cim-text-<VERSION>.zip  (+ .sha256)
# VERSION comes from cim.py. The zip holds cim.py, img2shr.py (from the Mac
# app's tools), the three launchers and README.txt, flat, so "unzip and
# double-click" works on a Tiger Mac as well as Windows Explorer. Unix
# modes are kept (Info-ZIP), the .bat gets CRLF, everything else LF.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"      # repo root
IMG2SHR="$HERE/img2shr.py"
VER="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$HERE/cim.py")"
[ -n "$VER" ] || { echo "no VERSION in cim.py" >&2; exit 1; }
[ -f "$IMG2SHR" ] || { echo "missing $IMG2SHR" >&2; exit 1; }

NAME="cim-text-$VER"
STAGE="$(mktemp -d /tmp/cim-text-zip.XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/$NAME" "$HERE/dist"

cp "$HERE/cim.py" "$IMG2SHR" "$STAGE/$NAME/"
cp "$HERE/launchers/CIM Text.command" "$HERE/launchers/cim-text.sh" "$STAGE/$NAME/"

# "CIM Text.app": the notarized launcher for modern Macs. Built here unless
# dist/ already holds a stapled one newer than cim.py (a notarization round
# trip per zip is wasteful while iterating on the README). APP=0 skips it.
APP="$HERE/dist/CIM Text.app"
if [ "${APP_BUILD:-1}" = "1" ]; then
	if [ ! -d "$APP" ] || [ "$HERE/cim.py" -nt "$APP/Contents/Resources/cim.py" ] \
	   || [ "$HERE/launchers/CIM Text.command" -nt "$APP/Contents/Resources/CIM Text.command" ] \
	   || [ "$HERE/launchers/mac-app/launcher.c" -nt "$APP/Contents/MacOS/CIM Text" ]; then
		"$HERE/tools/make_mac_app.sh"
	fi
	if [ "${SIGN:-1}" = "1" ]; then xcrun stapler validate "$APP" >/dev/null || { echo "CIM Text.app is not stapled; run tools/make_mac_app.sh" >&2; exit 1; }; fi
	cp -R "$APP" "$STAGE/$NAME/"
fi
sed "s/@@VER@@/$VER/" "$HERE/launchers/README.txt" > "$STAGE/$NAME/README.txt"
sed 's/$/\r/' "$HERE/launchers/cim-text.bat" > "$STAGE/$NAME/cim-text.bat"
chmod 755 "$STAGE/$NAME/cim.py" "$STAGE/$NAME/CIM Text.command" "$STAGE/$NAME/cim-text.sh"
chmod 644 "$STAGE/$NAME/img2shr.py" "$STAGE/$NAME/README.txt" "$STAGE/$NAME/cim-text.bat"

# py_compile on 2 and 3 when both are around: the 2.5-subset rule is only
# enforced by running, so at least catch syntax.
python3 -m py_compile "$STAGE/$NAME/cim.py"
rm -rf "$STAGE/$NAME/__pycache__"
if command -v python2.7 >/dev/null 2>&1; then
	python2.7 -m py_compile "$STAGE/$NAME/cim.py" && rm -f "$STAGE/$NAME/cim.pyc"
fi

OUT="$HERE/dist/$NAME.zip"
rm -f "$OUT"
(cd "$STAGE" && zip -X -r -q "$OUT" "$NAME")
(cd "$HERE/dist" && shasum -a 256 "$NAME.zip" > "$NAME.zip.sha256")
echo "built $OUT"
unzip -l "$OUT"
cat "$HERE/dist/$NAME.zip.sha256"
