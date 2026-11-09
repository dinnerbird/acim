#!/bin/bash
# Contract test for CoGS Image Manager, text edition (cim.py).
# Same assertions as the Mac app's contract_test.sh; only the
# server start lines differ. PY=python2.7 tools/contract_test.sh runs it
# under another interpreter.
#
# Everything the card relies on, checked against a scratch folder in /tmp:
#   catalog shape + pagination + search, range GETs (206/suffix/416),
#   traversal and dotfile rejection, PUT block writes (in-place only,
#   never grow), keep-alive, HEAD.
#
# Usage: tools/contract_test.sh            (prints PASS/FAIL lines)
#        PY=/path/to/python tools/contract_test.sh

set -u
cd "$(dirname "$0")/.."

PORT=8298
PY="${PY:-python3}"
DIR=$(mktemp -d /tmp/cogs-share-test.XXXX)
PASS=0; FAIL=0

say()  { printf '%s\n' "$*"; }
ok()   { PASS=$((PASS+1)); say "PASS  $1"; }
bad()  { FAIL=$((FAIL+1)); say "FAIL  $1"; }
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
expect_code() { # expect_code <want> <desc> <curl args...>
    local want="$1" desc="$2"; shift 2
    local got; got=$(code "$@")
    [ "$got" = "$want" ] && ok "$desc ($got)" || bad "$desc (want $want got $got)"
}

# --- stage: one bootable-looking image, one plain data image (patterned) ---
python3 - "$DIR" <<'EOF'
import sys, os
d = sys.argv[1]
# plain data disk: patterned bytes, valid size, NOT bootable
data = bytearray(512*280)
for b in range(280):
    for i in range(512):
        data[b*512+i] = (b*7 + i*3 + 1) & 0xFF
open(os.path.join(d, "Pattern Data Disk.po"), "wb").write(bytes(data))
# minimal bootable image: $01 boot marker + volume dir with PRODOS entry
img = bytearray(512*280)
img[0] = 0x01
vd = 2*512
img[vd+2] = 0; img[vd+3] = 0          # no next dir block
e0 = vd + 4
img[e0] = 0xF0 | 4                     # header, name len 4
img[e0+1:e0+5] = b"BOOT"
e1 = vd + 4 + 0x27
img[e1] = 0x20 | 6                     # sapling, name len 6
img[e1+1:e1+7] = b"PRODOS"
img[e1+0x10] = 0xFF                    # SYS
open(os.path.join(d, "Bootable_Test.po"), "wb").write(bytes(img))
# your-hub staging: two top-level folders, one nested
os.makedirs(os.path.join(d, "Games", "Arcade"))
os.makedirs(os.path.join(d, "Utilities"))
open(os.path.join(d, "Games", "Arcade", "Deep Disk.po"), "wb").write(bytes(data))
open(os.path.join(d, "Games", "Top Disk.po"), "wb").write(bytes(data))
open(os.path.join(d, "Utilities", "Tool.po"), "wb").write(bytes(data))
EOF

# --- start ---
say "interpreter: $($PY --version 2>&1)"
$PY cim.py "$DIR" --port $PORT --no-bonjour --headless >/dev/null 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null; rm -rf "$DIR"' EXIT
for i in $(seq 1 20); do
    curl -s -o /dev/null "http://127.0.0.1:$PORT/cogs-volumes.json" && break
    sleep 0.25
done

B="http://127.0.0.1:$PORT"

# --- catalog shape ---
CAT=$(curl -s "$B/cogs-volumes.json")
echo "$CAT" | grep -q '"pages": 1, "total": 5' && ok "catalog head pages/total" || bad "catalog head: $CAT"
echo "$CAT" | python3 -c '
import json,sys
j = json.load(sys.stdin)
v = {e["name"]: e for e in j["volumes"]}
assert v["Bootable Test"]["boot"] is True, "boot flag"
assert v["Pattern Data Disk"]["boot"] is False, "data disk boot flag"
assert v["Pattern Data Disk"]["blocks"] == 280, "blocks"
assert v["Pattern Data Disk"]["url"].endswith("/Pattern%20Data%20Disk.po"), "pct-encoded url"
assert v["Pattern Data Disk"]["readonly"] is False, "writable share"
# entry field order contract: name must appear before url in the raw text
' && ok "catalog entries (boot flags, blocks, urls)" || bad "catalog entries"
echo "$CAT" | grep -q '"name".*"url"' && ok "field order name-before-url" || bad "field order"

# --- search ---
S=$(curl -s "$B/search?q=pattern&p=1")
echo "$S" | grep -q '"count": 1' && ok "search count" || bad "search: $S"
S2=$(curl -s "$B/search?q=zzz&p=1")
echo "$S2" | grep -q '"count": 0' && ok "search no-hit" || bad "search no-hit: $S2"

# --- your hub (default: every top-level folder, A-Z; nested flattens) ---
H=$(curl -s "$B/cogs-your-hub.json")
echo "$H" | python3 -c '
import json,sys
j = json.load(sys.stdin)
assert j["hub"] == "your" and j["kind"] == "disks", "head"
assert j["total"] == 5, "total"
assert [c["cat"] for c in j["cats"]] == ["Games", "Utilities"], "default cats " + str(j["cats"])
assert j["cats"][0]["name"] == "Games" and j["cats"][0]["n"] == 2, "Games n (nested counted)"
assert j["cats"][1]["n"] == 1, "Utilities n"
' && ok "your hub defaults (top-level folders, nested counted)" || bad "your hub defaults: $H"
echo "$H" | grep -q '"name".*"cat".*"n"' && ok "hub field order name, cat, n" || bad "hub field order"
HA=$(curl -s "$B/cogs-your-hub.json?i=a")
echo "$HA" | grep -q '"kind": "audio", "total": 0, "cats": \[' && ok "your hub audio (empty cats)" || bad "hub audio: $HA"
C=$(curl -s "$B/search?cat=Games&p=1")
echo "$C" | python3 -c '
import json,sys
j = json.load(sys.stdin)
assert j["count"] == 2, "cat count"
names = [v["name"] for v in j["volumes"]]
assert names == ["Deep Disk", "Top Disk"], "flattened A-Z: " + str(names)
assert j["volumes"][0]["url"].endswith("/Games/Arcade/Deep%20Disk.po"), "nested url"
' && ok "search?cat= lists folder and below, catalog shape" || bad "search?cat=: $C"
CQ=$(curl -s "$B/search?cat=Games&q=deep")
echo "$CQ" | grep -q '"count": 1' && ok "cat= and q= combine" || bad "cat+q: $CQ"
CZ=$(curl -s "$B/search?cat=Nope")
echo "$CZ" | grep -q '"count": 0' && ok "unknown cat= is empty, not an error" || bad "unknown cat: $CZ"

# --- ranges ---
curl -s -o /tmp/ct_r1.bin -H "Range: bytes=1024-1535" "$B/Pattern%20Data%20Disk.po"
cmp -s <(dd if="$DIR/Pattern Data Disk.po" bs=1 skip=1024 count=512 2>/dev/null) /tmp/ct_r1.bin \
    && ok "range GET bytes match" || bad "range GET bytes"
CR=$(curl -s -D- -o /dev/null -H "Range: bytes=1024-1535" "$B/Pattern%20Data%20Disk.po" | tr -d '\r')
echo "$CR" | grep -q "^HTTP/1.1 206" && ok "range status 206" || bad "range status: $CR"
echo "$CR" | grep -q "Content-Range: bytes 1024-1535/143360" && ok "Content-Range header" || bad "Content-Range"
python3 - "$DIR/Pattern Data Disk.po" "$CR" <<'PY'
import sys, os, email.utils
path, hdr = sys.argv[1], sys.argv[2]
want = email.utils.formatdate(os.path.getmtime(path), usegmt=True)
got = ""
for line in hdr.splitlines():
    if line.lower().startswith("last-modified:"):
        got = line.split(":", 1)[1].strip()
sys.exit(0 if got == want else 1)
PY
[ $? = 0 ] && ok "Last-Modified matches file mtime" || bad "Last-Modified: $CR"
CS=$(curl -s -D- -o /dev/null -H "Range: bytes=-512" "$B/Bootable_Test.po" | tr -d '\r')
echo "$CS" | grep -q "bytes 142848-143359/143360" && ok "suffix range" || bad "suffix range: $CS"
expect_code 416 "range past EOF -> 416" -H "Range: bytes=999999999-" "$B/Bootable_Test.po"

# --- whole-file GET ---
curl -s "$B/Bootable_Test.po" -o /tmp/ct_whole.po
cmp -s "$DIR/Bootable_Test.po" /tmp/ct_whole.po && ok "whole-file GET match" || bad "whole-file GET"

# --- security: traversal, dotfiles, unknown ---
expect_code 404 "path traversal rejected" --path-as-is "$B/../../../etc/hosts"
expect_code 404 "dotfile rejected" "$B/.DS_Store"
expect_code 404 "missing image 404" "$B/nope.po"
expect_code 405 "unknown method 405" -X DELETE "$B/Bootable_Test.po"

# --- PUT (writable share) ---
GOT=$(printf 'COGSWRITE' | curl -s -o /dev/null -w '%{http_code}' -X PUT \
      -H "Content-Range: bytes 2048-2056/143360" --data-binary @- "$B/Pattern%20Data%20Disk.po")
[ "$GOT" = 200 ] && ok "PUT in-place 200" || bad "PUT in-place ($GOT)"
DD=$(dd if="$DIR/Pattern Data Disk.po" bs=1 skip=2048 count=9 2>/dev/null)
[ "$DD" = "COGSWRITE" ] && ok "PUT bytes landed" || bad "PUT bytes: '$DD'"
GOT=$(printf 'X' | curl -s -o /dev/null -w '%{http_code}' -X PUT \
      -H "Content-Range: bytes 143360-143360/143361" --data-binary @- "$B/Bootable_Test.po")
[ "$GOT" = 416 ] && ok "PUT never grows (416)" || bad "PUT grow ($GOT)"
GOT=$(printf 'X' | curl -s -o /dev/null -w '%{http_code}' -X PUT --data-binary @- "$B/newfile.po")
[ "$GOT" = 404 ] && ok "PUT new file rejected (404)" || bad "PUT new file ($GOT)"

# --- files folder (CIM 1.8): documents one at a time, RetroWrite's CoGS Share ---
mkdir -p "$DIR/files"
head -c 3000 /dev/urandom > "$DIR/files/From The Mac.odt"
cp "$DIR/files/From The Mac.odt" "$DIR/files/notes.txt"
TXT=$(curl -s "$B/files/?fmt=txt")
[ "$TXT" = "$(printf 'FROMTHEMAC.ODT\tFrom The Mac.odt\t3000\nNOTES.TXT\tnotes.txt\t3000\n')" ] \
    && ok "files txt listing with ProDOS aliases" || bad "files txt: $TXT"
curl -s "$B/files/" | python3 -c '
import json,sys
j = json.load(sys.stdin)
assert j["writable"] is True
assert [f["a2"] for f in j["files"]] == ["FROMTHEMAC.ODT", "NOTES.TXT"]
assert j["files"][0]["url"].endswith("/files/From%20The%20Mac.odt")
' && ok "files JSON listing" || bad "files JSON"
curl -s -o "$DIR/alias.bin" "$B/files/FROMTHEMAC.ODT"
cmp -s "$DIR/alias.bin" "$DIR/files/From The Mac.odt" && ok "GET file by alias" || bad "GET by alias"
expect_code 206 "files Range 206" -r 0-99 "$B/files/From%20The%20Mac.odt"
expect_code 404 "files missing 404" "$B/files/NOPE.ODT"
CAT=$(curl -s "$B/search?q=notes")
echo "$CAT" | grep -q '"count": 0' && ok "files folder left out of the disk catalog" || bad "files in catalog: $CAT"
head -c 2500 /dev/urandom > "$DIR/doc.bin"
P1=$(head -c 1024 "$DIR/doc.bin" | curl -s -o /dev/null -w '%{http_code}' -X PUT -H "Content-Range: bytes 0-1023/2500" --data-binary @- "$B/files/LETTER.ODT")
P2=$(tail -c +1025 "$DIR/doc.bin" | head -c 1024 | curl -s -o /dev/null -w '%{http_code}' -X PUT -H "Content-Range: bytes 1024-2047/2500" --data-binary @- "$B/files/LETTER.ODT")
[ -f "$DIR/files/LETTER.ODT.part" ] && [ ! -f "$DIR/files/LETTER.ODT" ] && ok "pieces go to .part until complete" || bad ".part staging"
P3=$(tail -c +2049 "$DIR/doc.bin" | curl -s -o /dev/null -w '%{http_code}' -X PUT -H "Content-Range: bytes 2048-2499/2500" --data-binary @- "$B/files/LETTER.ODT")
[ "$P1$P2$P3" = "201201201" ] && ok "PUT pieces 201" || bad "PUT pieces ($P1 $P2 $P3)"
cmp -s "$DIR/files/LETTER.ODT" "$DIR/doc.bin" && ok "pieces joined byte for byte" || bad "pieces joined"
GOT=$(printf 'abc' | curl -s -o /dev/null -w '%{http_code}' -X PUT -H "Content-Range: bytes 1024-1026/2500" --data-binary @- "$B/files/OTHER.ODT")
[ "$GOT" = 409 ] && ok "piece out of order 409" || bad "out of order ($GOT)"
GOT=$(printf 'abc' | curl -s -o /dev/null -w '%{http_code}' -X PUT -H "Content-Range: bytes 2499-2501/2500" --data-binary @- "$B/files/OTHER.ODT")
[ "$GOT" = 416 ] && ok "piece past total 416" || bad "past total ($GOT)"
GOT=$(printf 'new' | curl -s -o /dev/null -w '%{http_code}' -X PUT --data-binary @- "$B/files/FROMTHEMAC.ODT")
[ "$GOT" = 200 ] && [ "$(cat "$DIR/files/From The Mac.odt")" = "new" ] && ok "PUT by alias replaces the real file (200)" || bad "replace by alias ($GOT)"
expect_code 400 "files dotfile name rejected" -X PUT --data-binary 'x' "$B/files/.hidden"

# --- HEAD + keep-alive ---
expect_code 200 "HEAD image" -I "$B/Bootable_Test.po"
KA=$(curl -s -w '%{http_code} ' -o /dev/null "$B/cogs-volumes.json" -o /dev/null "$B/Bootable_Test.po" -o /dev/null "$B/cogs-volumes-2.json")
[ "$KA" = "200 200 200 " ] && ok "keep-alive x3 one connection" || bad "keep-alive: $KA"

# --- read-only share refuses PUT; explicit categories + subfolder prefix ---
kill $SRV 2>/dev/null; wait $SRV 2>/dev/null
$PY cim.py "$DIR" --port $PORT --no-bonjour --headless --read-only \
    --category "Utilities=My Tools" --category Games --prefix-subfolder >/dev/null 2>&1 &
SRV=$!
for i in $(seq 1 20); do
    curl -s -o /dev/null "$B/cogs-volumes.json" && break
    sleep 0.25
done
H2=$(curl -s "$B/cogs-your-hub.json")
echo "$H2" | python3 -c '
import json,sys
j = json.load(sys.stdin)
assert [c["cat"] for c in j["cats"]] == ["Utilities", "Games"], "user order kept"
assert j["cats"][0]["name"] == "My Tools", "label override on the wire"
' && ok "your hub honours --category order and label" || bad "explicit hub: $H2"
P=$(curl -s "$B/search?cat=Games")
echo "$P" | grep -q '"name": "Arcade: Deep Disk"' && ok "prefix-subfolder names nested disks" || bad "prefix: $P"
echo "$P" | grep -q '"name": "Top Disk"' && ok "prefix leaves direct children plain" || bad "prefix direct: $P"
GOT=$(printf 'X' | curl -s -o /dev/null -w '%{http_code}' -X PUT \
      -H "Content-Range: bytes 0-0/143360" --data-binary @- "$B/Pattern%20Data%20Disk.po")
[ "$GOT" = 403 ] && ok "read-only share refuses PUT (403)" || bad "read-only PUT ($GOT)"
GOT=$(printf 'X' | curl -s -o /dev/null -w '%{http_code}' -X PUT --data-binary @- "$B/files/RO.ODT")
[ "$GOT" = 403 ] && ok "read-only share refuses files PUT (403)" || bad "read-only files PUT ($GOT)"
CAT=$(curl -s "$B/cogs-volumes.json")
echo "$CAT" | grep -q '"readonly": true' && ok "read-only flagged in catalog" || bad "readonly flag"

say ""
say "== $PASS passed, $FAIL failed =="
exit $((FAIL > 0))
