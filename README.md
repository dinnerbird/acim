# ACIM: CoGS Image Manager, text edition

Share a folder of Apple II disk images, music and pictures to every CoGS
card on your network from a machine that cannot run the Mac or Windows
Image Manager. A PowerPC Mac on Tiger or Leopard, an Intel Mac on
whatever it was left on, a Raspberry Pi, a NAS with Python, a Windows box
with Python installed. One file, standard library only, no install.

    python cim.py ~/Apple2Disks

It speaks the same wire contract as the Mac and Windows apps and
a2cogs.com. The card cannot tell them apart, and the contract test in
`tools/` is the Mac app's own suite with the server start lines swapped.

```
+--------------------------------------------------------------------------------------------------+
| ] CoGS IMAGE MANAGER  text edition v0.2                                     Sun Nov 08  21:36:39 |
+--------------------------------------------------------------------------------------------------+
| Image Source URL: http://192.168.4.42:8271/  <- use for image source                             |
| Library: /Users/rob/Apple II Disks                                                               |
| 21 disk images (12 bootable)   4 tracks   12 pictures   1 files                                  |
| Sharing: on (Bonjour "RMB-Air")   Access: writable   Requests: 2   Served: 8 KB                   |
+------------------------------- FOLDERS  (][ = Apple II category) --------------------------------+
| [-] /                             I3       | I   B CoGS Comms Tools          65536 blk /BOOT.DISK|
|   [+] Games                    ][ I8    P6 | I R B RastanA                    1600 blk /RASTAN.A |
|   [+] Music                    ][ I4    A4 | I   B RastanB                    1600 blk /RASTAN.B |
|   [+] Pictures                 ][ I0    P6 | F     From The Mac.odt         FROMTHEMAC.ODT       |
|       System                   ][ I2       |                                                     |
|   [+] Utilities                ][ I4       |                                                     |
+---------------------------------------------- LOG -----------------------------------------------+
| 21:36:35  scan: 21 disk image(s), 4 track(s), 12 picture(s), 1 file(s)                           |
| 21:36:35  share up on port 8271                                                                  |
| 21:36:38  catalog page 1                                                                         |
| 21:36:38  GET RastanA.2mg [0-8191]                                                               |
+--------------------------------------------------------------------------------------------------+
  R Rescan  W Writable  B Stop sharing  C Category  P Prefix  L Log  ? Help  Q Quit
```

## Download

The release zip (`cim-text-<version>.zip`: `cim.py`, `img2shr.py`, a signed
`CIM Text` app for Macs on 10.13+, `CIM Text.command` for older Macs,
`cim-text.sh`, `cim-text.bat`) is on [a2cogs.com/download](https://a2cogs.com/download/).
The card itself: [a2cogs.com](https://a2cogs.com). Patches: see `CONTRIBUTING.md`.

## What runs it

Any Python from 2.5 up, 2.x or 3.x, same file. That is what ships on:

| Machine | OS | Python on board |
|---|---|---|
| G4, G5 (PowerPC) | Leopard 10.5 | 2.5 |
| G4, G5 (PowerPC) | Tiger 10.4 | 2.3 (too old; python.org's 2.7.18 "i386/PPC" installer runs on 10.3+) |
| Intel Mac 2006 to 2012 | Snow Leopard to Catalina | 2.6 or 2.7 |
| Any Mac on 12.3 or later | Monterey+ | none; `xcode-select --install` or python.org |
| Raspberry Pi, Linux | anything current | 3.x |
| Windows | 10, 11 | install from python.org; runs in `--plain` mode (no curses) |

Tested so far on Python 2.7.18 and 3.13 on a Mac, 3.14 on Windows 11,
and the share end on a real IIGS and in the emulator. The 2.5 subset is a
coding rule (see the file header) and not yet a run on real Leopard; a
G4 or G5 report is welcome.

## Screen

Full-screen text UI when the terminal has one, ASCII box drawing, green
on black if the terminal does colour.

- Header: the Image Source URL to type on the card, the library path,
  counts, sharing state (with the Bonjour name), writable or read-only,
  requests served.
- Left: the folder tree. `][` marks a folder the card shows as a category
  under Browse > Your source. `I`/`A`/`P` are disk images, audio tracks and
  pictures in that folder and below.
- Right: what sits directly in the selected folder. `B` = self-boots,
  blocks, ProDOS volume name. `R` or `W` beside a row for two seconds
  after the card reads or writes it, like the LED on a drive.
- Bottom: the request log. `L` for the full-screen version.

Keys: arrows or j/k move, Right/Enter opens a folder, Left closes, Tab
switches to the item list, `C` toggles the selected folder as a category,
`P` toggles the subfolder prefix inside categories, `W` writable/read-only,
`B` start/stop sharing (stop drops the Bonjour announce and answers the
card 503 until started again), `R` rescan, `?` help, `Q` quit.

`--plain` gives a line-mode prompt instead (`s` status, `t` tree, `l
Games/Arcade` list, `c <folder>`, `w`, `b`, `r`, `g` log, `q`). Windows
without curses falls back to it on its own. `--headless` runs with no UI
and logs to stdout, for launchd, cron, systemd or a NAS.

Settings (categories, prefix, writable) persist in `.cim-text.cfg` inside
the library folder, so each library keeps its own.

## What it serves

Everything the Mac app serves, from the same folder layout:

- `.po`, `.hdv`, `.2mg` disk images with byte-range GET and in-place PUT
  (writable by default, `--read-only` or `W` to refuse). The boot probe is
  the same rule as a2cogs.com: block 0 `$01` marker and a `PRODOS` kernel
  or `*.SYSTEM` loader in the volume directory, so `B` means it boots.
  `.dsk`/`.do`/`.woz`/`.nib` are counted and skipped (the card mounts
  ProDOS-order images only).
- `cogs-volumes.json` pages, `/search?q=&cat=&p=`, `cogs-your-hub.json`
  (top-level folders as categories by default, `--category FOLDER=Label`
  or `C` to curate, `--prefix-subfolder`), `X-CoGS-Writable` and
  `X-CoGS-Poll` headers, Last-Modified, keep-alive, LAN-only peers.
- Audio: `.pcm` files as-is. `.mp3`, `.m4a`, `.wav`, `.aif(f)` are
  transcoded once to the card's u8/21973/mono PCM by ffmpeg if installed,
  otherwise by `afconvert`, which every OS X since 10.4 ships (so a Leopard
  G4 converts music with nothing installed). `.flac` and `.ogg` need
  ffmpeg. `$00` samples are floored to `$01` (DOC oscillator halt). The
  cache is `~/Library/Caches/CoGSImageManager/pcm` with the Mac app's
  key, so a PCM either program made is reused by the other.
- Pictures: `.shr` files as-is. JPEG/PNG/GIF convert through `img2shr.py`
  when python3 + numpy + Pillow are present (put `img2shr.py` beside
  `cim.py`; the release zip includes it). A pure-Python converter for
  machines that cannot have numpy is on the list.
- The `files` folder (CIM 1.8): documents an Apple II program sends and
  fetches one at a time, with ProDOS aliases and `.part` staging.
- Bonjour `_cogs-share._tcp` through `dns-sd` (any Mac since 10.4,
  Bonjour for Windows) or `avahi-publish` on Linux. Without either the
  URL is typed on the card, as always.

## Shipping

`tools/make_zip.sh` builds `dist/cim-text-<VERSION>.zip` (and a
`.sha256`) from `cim.py`, `img2shr.py`, the launchers in
`launchers/`, `README.txt` with the version stamped in, and `CIM Text.app`.
Flat folder, Unix modes kept, the `.bat` in CRLF. `VERSION` in `cim.py` is
the release number. The zip is what goes on a2cogs.com.

`CIM Text.app` is for Macs with Gatekeeper (10.13+): Ventura and later
refuse a downloaded bare `.command`, and scripts cannot be notarized. The
app is a 118 KB universal Mach-O (`launchers/mac-app/launcher.c`) that
writes a one-line script to `$TMPDIR` (files we create carry no quarantine
flag) and hands it to Terminal through `/usr/bin/open`; no Apple Events,
so no automation prompt. `cim.py`, `img2shr.py` and the `.command` ride in
`Contents/Resources`. `tools/make_mac_app.sh` builds it, signs with the
Developer ID and hardened runtime, notarizes through the `cogs-notary`
keychain profile and staples; `make_zip.sh` rebuilds it only when its
inputs changed (`APP_BUILD=0` skips). Verified: unzipped from a
quarantined zip, `spctl --assess` says `accepted, source=Notarized
Developer ID`.

Every launcher finds an interpreter (`python3`, `python`, `python2.7`),
takes a path argument, else a `Disk Images` folder next to the app or
script, else the last-used folder from `~/.cim-text-library`, else asks
(drag the folder into the Terminal window on a Mac). Older Macs (Tiger
through Sierra) double-click `CIM Text.command`; Linux runs `cim-text.sh`;
Windows `cim-text.bat`.

## Test

    tools/contract_test.sh                                   # python3
    PY=/path/to/python2.7 tools/contract_test.sh             # another interpreter

47 checks: catalog shape and field order, pagination, search, hub
defaults and overrides, ranges (206, suffix, 416), traversal and dotfile
rejection, PUT in place and never growing, files folder pieces, HEAD,
keep-alive, read-only refusal.

To compare against the Swift app byte for byte, run both on a copy of the
same folder and `curl -H "Host: X"` the same endpoints from each; they
match on `cogs-volumes.json`, `search`, `cogs-audio.json`,
`cogs-pictures.json`, the hub and the files listings.

## Options

```
--port N                 TCP port (default 8271)
--read-only              refuse the card's block writes
--no-bonjour             do not announce
--name NAME              Bonjour share name (default: this machine's name)
--category F[=Label]     show folder F as an Apple II category (repeatable)
--prefix-subfolder       names one level down carry the subfolder inside a category
--audio-tool T           auto | ffmpeg | afconvert | none
--plain                  line-mode prompt
--headless               no UI, log to stdout
```
