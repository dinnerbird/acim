#!/usr/bin/env bash
# Build, sign, notarize and staple "CIM Text.app" for modern Macs (10.13+,
# Intel and Apple Silicon). The app is a 20 KB Mach-O launcher that opens the
# text edition in Terminal; cim.py, img2shr.py and the .command live in
# Contents/Resources. Gatekeeper refuses a downloaded bare .command on
# Ventura and later, a notarized app it opens with the usual one-time
# "downloaded from the internet" dialog.
#
#   tools/make_mac_app.sh            build + sign + notarize + staple
#   NOTARIZE=0 tools/make_mac_app.sh build + sign only (local testing)
#
# Output: dist/CIM Text.app. Called by make_zip.sh.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"      # repo root
IMG2SHR="$HERE/img2shr.py"
ICON="$HERE/launchers/mac-app/AppIcon.icns"
# Signing is Rob's step; anyone else can build the unsigned app with
# SIGN=0 (Gatekeeper will then want a right-click Open the first time).
IDENTITY="${SIGN_IDENTITY:-Developer ID Application: ROBERT DANIEL PERISSI (3CFCRVWD2M)}"
PROFILE="${NOTARY_PROFILE:-cogs-notary}"
NOTARIZE="${NOTARIZE:-1}"
MIN_OS="${MIN_OS:-10.13}"

VER="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$HERE/cim.py")"
[ -n "$VER" ] || { echo "no VERSION in cim.py" >&2; exit 1; }
BUILD=$(git -C "$HERE" rev-list --count HEAD 2>/dev/null || date +%m%d%H%M)

APP="$HERE/dist/CIM Text.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

echo "==> launcher (arm64 + x86_64, macOS $MIN_OS+)"
clang -Os -Wall -arch arm64 -arch x86_64 -mmacosx-version-min="$MIN_OS" \
	-o "$APP/Contents/MacOS/CIM Text" "$HERE/launchers/mac-app/launcher.c"

cp "$HERE/cim.py" "$IMG2SHR" "$HERE/launchers/CIM Text.command" "$APP/Contents/Resources/"
chmod 755 "$APP/Contents/Resources/CIM Text.command" "$APP/Contents/Resources/cim.py"
if [ -f "$ICON" ]; then
	cp "$ICON" "$APP/Contents/Resources/AppIcon.icns"
fi

cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>                 <string>CIM Text</string>
    <key>CFBundleDisplayName</key>          <string>CIM Text</string>
    <key>CFBundleExecutable</key>           <string>CIM Text</string>
    <key>CFBundleIdentifier</key>           <string>com.a2cogs.cim-text</string>
    <key>CFBundleVersion</key>              <string>$BUILD</string>
    <key>CFBundleShortVersionString</key>   <string>$VER</string>
    <key>CFBundlePackageType</key>          <string>APPL</string>
    <key>CFBundleIconFile</key>             <string>AppIcon</string>
    <key>LSMinimumSystemVersion</key>       <string>$MIN_OS</string>
    <key>LSUIElement</key>                  <true/>
    <key>LSApplicationCategoryType</key>    <string>public.app-category.utilities</string>
    <key>NSHumanReadableCopyright</key>
    <string>Rob Perissi 2026 - a2cogs.com</string>
</dict>
</plist>
EOF

if [ "${SIGN:-1}" = "1" ]; then
	echo "==> codesign (Developer ID, hardened runtime)"
	codesign --force --options runtime --timestamp --sign "$IDENTITY" "$APP"
	codesign --verify --strict --verbose=1 "$APP"
else
	echo "==> ad-hoc signature only (SIGN=0)"
	codesign --force --sign - "$APP"
	NOTARIZE=0
fi

if [ "$NOTARIZE" = "1" ]; then
	echo "==> notarize ($PROFILE)"
	NZ="$(mktemp -d /tmp/cim-text-notarize.XXXXXX)"
	ditto -c -k --keepParent "$APP" "$NZ/CIM-Text.zip"
	xcrun notarytool submit "$NZ/CIM-Text.zip" --keychain-profile "$PROFILE" --wait 2>&1 | tail -6
	xcrun stapler staple "$APP"
	rm -rf "$NZ"
	spctl --assess --type execute --verbose=1 "$APP" 2>&1 | tail -2
fi

echo "built: $APP  (v$VER build $BUILD)"
lipo -info "$APP/Contents/MacOS/CIM Text"
