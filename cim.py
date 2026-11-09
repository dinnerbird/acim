#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# cim.py - CoGS Image Manager, text edition.
#
# Share a folder of Apple II disk images (and music and pictures) to every
# CoGS card on your LAN from any machine with a Python interpreter: a
# PowerPC Mac on Tiger or Leopard, an Intel Mac on whatever it was left
# on, a Raspberry Pi, a NAS, a Windows box with Python installed. Same
# wire contract as the Mac and Windows Image Manager apps and a2cogs.com,
# so the card cannot tell them apart.
#
#     python cim.py ~/Apple2Disks
#
# One file, standard library only. Written to the Python 2.5 subset so the
# same file runs unchanged on 2.5, 2.6, 2.7 and 3.x. Do not add: b''
# literals, "except X as e", str.format, set literals, dict
# comprehensions, argparse, json, collections.namedtuple, os.path.relpath,
# bytearray, print() calls. See README.md.
#
# Screen: a curses text UI (ASCII box drawing, green on black) when the
# terminal has one; a line-mode prompt otherwise (--plain), or nothing at
# all (--headless) for launchd, cron, or a NAS.
#

import os
import re
import sys
import time
import socket
import signal
import hashlib
import threading
import subprocess
import optparse
from turtle import left, title

try:
    from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
    from SocketServer import ThreadingMixIn
except ImportError:
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from socketserver import ThreadingMixIn

try:
    from urllib import unquote as _unquote_bytes
except ImportError:
    _unquote_bytes = None
    from urllib.parse import unquote as _unquote_text

try:
    from email.utils import formatdate
except ImportError:
    from email.Utils import formatdate

try:
    import curses
except ImportError:
    curses = None

VERSION = "0.1"
DEFAULT_PORT = 8271
BLOCK = 512
PER_PAGE = 16            # disks: what Image Manager serves; the card holds 18
AUDIO_PER_PAGE = 17      # audio and pictures
HUB_MAX = 12             # one hub screen on the card
HUB_LABEL_MAX = 30       # 40 columns minus badges and count
FILES_SUB = u"files"
FILES_MAX_NAME = 64
FILES_MAX_BYTES = 4 * 1024 * 1024
AUDIO_RATE = 21973       # Tool225 / DOC true rate
SHR_BYTES = 32712
CFG_NAME = u".cim-text.cfg"

DISK_EXT = (u"po", u"hdv", u"2mg")
REJECTED_EXT = (u"dsk", u"do", u"woz", u"nib")
AUDIO_SRC_EXT = (u"mp3", u"m4a", u"wav", u"aif", u"aiff", u"flac", u"ogg")
PICTURE_SRC_EXT = (u"jpg", u"jpeg", u"png", u"gif", u"webp", u"bmp")
PACKAGE_DIR_EXT = (u".app", u".bundle", u".pkg", u".framework", u".photoslibrary")
SIG_EXT = DISK_EXT + REJECTED_EXT + AUDIO_SRC_EXT + PICTURE_SRC_EXT + (u"pcm", u"shr")

# ---------------------------------------------------------------- compat

PY2 = sys.version_info[0] == 2
if PY2:
    text_type = unicode          # noqa (only evaluated on 2.x)
    bytes_type = str
else:
    text_type = str
    bytes_type = bytes


def tob(s):
    """text -> UTF-8 bytes (str on 2.x, bytes on 3.x); bytes pass through."""
    if isinstance(s, text_type):
        return s.encode("utf-8")
    return s


def tos(b, errors="replace"):
    """bytes -> text. Text passes through."""
    if isinstance(b, text_type):
        return b
    return b.decode("utf-8", errors)


def byte_at(data, i):
    x = data[i]
    if isinstance(x, int):
        return x
    return ord(x)


def fs_text(s):
    """A path from sys.argv or os.environ as text."""
    if isinstance(s, text_type):
        return s
    return s.decode(sys.getfilesystemencoding() or "utf-8", "replace")


def unquote(s):
    """Percent-decode a URL path or query value to text (UTF-8)."""
    if _unquote_bytes is not None:
        return tos(_unquote_bytes(tob(s)))
    return _unquote_text(s)


_SAFE = set(u"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_~")


def pct(s):
    """Percent-encode one path segment (space -> %20, '/' encoded too)."""
    out = []
    for ch in s:
        if ch in _SAFE:
            out.append(ch)
        else:
            for b in bytearray_of(ch.encode("utf-8")):
                out.append(u"%%%02X" % b)
    return u"".join(out)


def bytearray_of(b):
    """Iterate a bytes object as ints on both Pythons (no bytearray on 2.5)."""
    return [byte_at(b, i) for i in range(len(b))]


def encode_path(rel):
    """Percent-encode a relative path segment by segment, keeping slashes."""
    return u"/".join([pct(seg) for seg in rel.split(u"/") if seg])


def json_str(s):
    """JSON string literal, with quotes and escapes. UTF-8 text in, text out."""
    out = [u'"']
    for ch in s:
        if ch == u'"':
            out.append(u'\\"') # escape double quote
        elif ch == u"\\":
            out.append(u"\\\\") # escape backslash
        elif ord(ch) < 0x20:
            out.append(u"\\u%04X" % ord(ch)) # magic control characters
        else:
            out.append(ch)
    out.append(u'"')
    return u"".join(out)


def lower_key(s):
    """Returns lower-case when asked."""
    return s.lower()


def last_component(path):
    """Last path component, or empty string if path is empty."""
    return path.split(u"/")[-1] if path else u""


def now_hms():
    """Current time as HH:MM:SS string."""
    return time.strftime("%H:%M:%S")


def makedirs(p):
    """Like os.makedirs but ignores existing directories."""
    if not os.path.isdir(p):
        try:
            os.makedirs(p)
        except OSError:
            pass


def file_size(p):
    """Return the size of a file in bytes, or None if it does not exist."""
    try:
        return os.path.getsize(p)
    except OSError:
        return None


def rename_over(src, dst):
    """os.rename that replaces on Windows too."""
    if os.path.exists(dst):
        try:
            os.remove(dst)
        except OSError:
            pass
    os.rename(src, dst)


def run_quiet(args):
    """Run a tool with no stdin and no output. Returns the exit code or -1."""
    devnull = open(os.devnull, "wb")
    try:
        try:
            return subprocess.call(args, stdin=devnull, stdout=devnull, stderr=devnull)
        except (OSError, ValueError):
            return -1
    finally:
        devnull.close()


def which(name, extra=()):
    """Return the absolute path of an executable, or None if not found."""
    paths = list(extra) + os.environ.get("PATH", "").split(os.pathsep) + [
        "/usr/local/bin", "/opt/homebrew/bin", "/opt/local/bin", "/sw/bin", "/usr/bin"]
    for d in paths:
        if not d:
            continue
        for cand in (name, name + ".exe"):
            p = os.path.join(d, cand)
            if os.path.isfile(p) and os.access(p, os.X_OK):
                return p
    return None


# ------------------------------------------------------------- disk image

def clean_name(file_name):
    """'Total.Replay.v6.0.1.hdv' -> 'Total Replay v6.0.1' (Image Manager's rule)."""
    base = os.path.splitext(file_name)[0].replace(u"_", u" ")
    out = []
    n = len(base)
    for i in range(n):
        c = base[i]
        if c == u".":
            prev_digit = i > 0 and base[i - 1].isdigit()
            next_digit = i + 1 < n and base[i + 1].isdigit()
            out.append(u"." if (prev_digit and next_digit) else u" ")
        else:
            out.append(c)
    collapsed = u" ".join(u"".join(out).split())
    return collapsed or file_name


def read_le32(d, off):
    """Read a little-endian 32-bit integer from a bytes object, or 0 if not enough bytes."""
    if len(d) < off + 4:
        return 0
    return (byte_at(d, off) | (byte_at(d, off + 1) << 8) |
            (byte_at(d, off + 2) << 16) | (byte_at(d, off + 3) << 24))


def boot_probe(head, data_offset):
    """(bootable, volume_name). Block 0 must carry the ProDOS $01 marker and
    the volume directory a PRODOS kernel or a *.SYSTEM loader. Same rule as
    the a2cogs catalog and the Mac app."""
    if len(head) < data_offset + BLOCK:
        return False, None
    img = head[data_offset:]
    if len(img) < BLOCK or byte_at(img, 0) != 0x01:
        return False, None
    vol_name = None
    has_prodos = False
    has_system = False
    blk = 2
    seen = set()
    while blk != 0 and blk not in seen and (blk + 1) * BLOCK <= len(img):
        seen.add(blk)
        b = img[blk * BLOCK:(blk + 1) * BLOCK]
        nxt = byte_at(b, 2) | (byte_at(b, 3) << 8)
        for e in range(13):
            off = 4 + e * 0x27
            if off + 0x27 > len(b):
                break
            storage = byte_at(b, off) >> 4
            name_len = byte_at(b, off) & 0x0F
            if name_len == 0 or name_len > 15:
                continue
            name = tos(b[off + 1:off + 1 + name_len], "ignore").upper()
            if storage == 0xF:
                vol_name = name
                continue
            if storage == 0:
                continue
            ftype = byte_at(b, off + 0x10)
            if name == u"PRODOS":
                has_prodos = True
            if ftype == 0xFF and name.endswith(u".SYSTEM"):
                has_system = True
        blk = nxt
    return (has_prodos or has_system), vol_name


class DiskImage(object):
    def __init__(self, path, folder, file_name, size, data_offset, bootable, vol_name):
        self.path = path
        self.folder = folder
        self.file_name = file_name
        self.display = clean_name(file_name)
        self.bytes = size
        self.blocks = (size - data_offset) // BLOCK
        self.bootable = bootable
        self.volume = vol_name

    def rel_path(self):
        return self.folder + u"/" + self.file_name if self.folder else self.file_name


def probe_disk(path, folder, file_name):
    """Return a DiskImage or None if not a valid disk image."""
    ext = os.path.splitext(file_name)[1][1:].lower()
    if ext not in DISK_EXT:
        return None
    size = file_size(path)
    if size is None or size < BLOCK:
        return None
    try:
        f = open(path, "rb")
        try:
            head = f.read(32 * BLOCK)
        finally:
            f.close()
    except IOError:
        return None
    data_offset = 0
    if len(head) >= 64 and head[:4] == tob(u"2IMG"):
        off = read_le32(head, 0x18)
        data_offset = off if (off >= 64 and off < size) else 64
    bootable, vol = boot_probe(head, data_offset)
    return DiskImage(path, folder, file_name, size, data_offset, bootable, vol)


# ------------------------------------------------------------- folder walk

class WalkedFile(object):
    def __init__(self, path, folder, name):
        self.path = path
        self.folder = folder
        self.name = name

    def rel_path(self):
        return self.folder + u"/" + self.name if self.folder else self.name

    def ext(self):
        return os.path.splitext(self.name)[1][1:].lower()


def walk_library(root):
    """Regular files under root, sorted by relative path. Hidden names,
    package folders and the files/ folder are skipped; symlinks that
    resolve outside the root are not part of the library."""
    out = []
    root_real = os.path.realpath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        rel = dirpath[len(root):].replace(os.sep, u"/").strip(u"/")
        keep = []
        for d in dirnames:
            if d.startswith(u"."):
                continue
            if d.lower().endswith(PACKAGE_DIR_EXT):
                continue
            if not rel and d == FILES_SUB:
                continue
            full = os.path.join(dirpath, d)
            if os.path.islink(full):
                r = os.path.realpath(full)
                if not (r == root_real or r.startswith(root_real + os.sep)):
                    continue
            keep.append(d)
        dirnames[:] = keep
        for fn in filenames:
            if fn.startswith(u"."):
                continue
            full = os.path.join(dirpath, fn)
            if os.path.islink(full):
                r = os.path.realpath(full)
                if not r.startswith(root_real + os.sep):
                    continue
            if not os.path.isfile(full):
                continue
            out.append(WalkedFile(full, rel, fn))
    out.sort(key=lambda f: f.rel_path().lower())
    return out


def folders_of(files):
    """Every folder with a file in or below it, plus ancestors. Sorted."""
    s = set()
    for f in files:
        parts = [p for p in f.folder.split(u"/") if p]
        while parts:
            s.add(u"/".join(parts))
            parts.pop()
    return sorted(s, key=lower_key)


def resolve_request_path(root, req_path):
    """'/Games/Arkanoid.2mg' (decoded) -> absolute file inside root, or None
    on traversal, dotfiles, directories, or anything not there."""
    segs = [s for s in req_path.split(u"/") if s]
    if not segs:
        return None
    for s in segs:
        if s == u"." or s == u".." or s.startswith(u".") or u"\\" in s:
            return None
    p = root
    for s in segs:
        p = os.path.join(p, s)
    if not os.path.isfile(p):
        return None
    root_real = os.path.realpath(root)
    if not os.path.realpath(p).startswith(root_real + os.sep):
        return None
    return p


# ---------------------------------------------------------------- hub

def hub_under(folder, cat):
    return folder == cat or folder.startswith(cat + u"/")


def hub_prefixed(name, folder, cat):
    if folder == cat or not folder.startswith(cat + u"/"):
        return name
    return last_component(folder) + u": " + name


def hub_clip(s):
    return s if len(s) <= HUB_LABEL_MAX else s[:HUB_LABEL_MAX]


class HubConfig(object):
    def __init__(self):
        self.cats = []            # list of [path, label-or-None]
        self.prefix_subfolder = False
        self.explicit = False     # user set; otherwise defaults from top folders

    def defaults_from(self, folders):
        top = sorted([f for f in folders if u"/" not in f], key=lower_key)
        self.cats = [[t, None] for t in top]

    def label(self, path):
        for p, l in self.cats:
            if p == path:
                return hub_clip((l or u"").strip() or last_component(p))
        return None

    def contains(self, path):
        for p, l in self.cats:
            if p == path:
                return True
        return False

    def toggle(self, path):
        if self.contains(path):
            self.cats = [c for c in self.cats if c[0] != path]
        else:
            self.cats.append([path, None])
        self.explicit = True

    def json(self, kind, total, count_fn):
        rows = []
        for p, l in self.cats:
            n = count_fn(p)
            if n > 0:
                rows.append((p, self.label(p), n))
            if len(rows) >= HUB_MAX:
                break
        s = [u'{ "hub": "your", "kind": "%s", "total": %d, "cats": [\n' % (kind, total)]
        for i in range(len(rows)):
            p, lab, n = rows[i]
            s.append(u'  { "name": %s, "cat": %s, "n": %d }' % (json_str(lab), json_str(p), n))
            s.append(u",\n" if i < len(rows) - 1 else u"\n")
        s.append(u"] }\n")
        return u"".join(s)


# ------------------------------------------------------------- catalogs

def paged(items, page, per):
    pages = max(1, (len(items) + per - 1) // per)
    lo = (page - 1) * per
    hi = min(lo + per, len(items))
    return pages, (items[lo:hi] if lo < hi else [])


class DiskCatalog(object):
    def __init__(self, images, writable):
        counts = {}
        for i in images:
            k = i.display.lower()
            counts[k] = counts.get(k, 0) + 1
        for i in images:
            if counts[i.display.lower()] > 1 and i.folder:
                i.display = i.display + u" (" + last_component(i.folder) + u")"
        images.sort(key=lambda i: i.display.lower())
        self.images = images
        self.writable = writable

    def pages(self):
        return max(1, (len(self.images) + PER_PAGE - 1) // PER_PAGE)

    def in_category(self, cat, prefix_sub):
        hits = [i for i in self.images if hub_under(i.folder, cat)]
        if not prefix_sub:
            return [(i, i.display) for i in hits]
        rows = [(i, hub_prefixed(i.display, i.folder, cat)) for i in hits]
        rows.sort(key=lambda r: r[1].lower())
        return rows

    def page_json(self, page, base, covers):
        pages, sl = paged(self.images, page, PER_PAGE)
        return self.body([(i, i.display) for i in sl], base,
                         u'"pages": %d, "total": %d' % (self.pages(), len(self.images)), covers)

    def search_json(self, q, page, base, covers, cat=None, prefix_sub=False):
        q = q.lower()
        pool = self.in_category(cat, prefix_sub) if cat else [(i, i.display) for i in self.images]
        if q:
            pool = [r for r in pool if q in r[1].lower() or q in r[0].file_name.lower()
                    or q in r[0].folder.lower()]
        pages, sl = paged(pool, page, PER_PAGE)
        return self.body(sl, base, u'"pages": %d, "count": %d' % (pages, len(pool)), covers)

    def body(self, rows, base, head, covers):
        s = [u"{ %s, \"volumes\": [\n" % head]
        for i in range(len(rows)):
            img, name = rows[i]
            url = base + encode_path(img.rel_path())
            e = [u'  { "name": %s,' % json_str(name),
                 u' "bytes": %d,' % img.bytes,
                 u' "blocks": %d,' % img.blocks,
                 u' "boot": %s,' % (img.bootable and u"true" or u"false"),
                 u' "readonly": %s,' % (self.writable and u"false" or u"true"),
                 u' "source": "lan",']
            cover = covers.get(pic_stem(name))
            if cover:
                e.append(u' "cover": %s,' % json_str(cover))
            e.append(u' "url": %s }' % json_str(url))
            s.append(u"".join(e))
            s.append(u",\n" if i < len(rows) - 1 else u"\n")
        s.append(u"] }\n")
        return u"".join(s)


def pic_stem(s):
    t = s.lower()
    i = t.find(u" dk")
    if i >= 0:
        t = t[:i]
    i = t.find(u" (")
    if i >= 0:
        t = t[:i]
    return t.strip()


class Track(object):
    """One audio track (served under /audio/) or one picture (/pictures/)."""
    def __init__(self, served, folder, source, blob, nbytes):
        self.served = served          # relpath with .pcm / .shr
        self.folder = folder
        self.display = clean_name(os.path.basename(source))
        self.source = source
        self.blob = blob              # the file actually served
        self.bytes = nbytes

    def secs(self):
        return (self.bytes + AUDIO_RATE // 2) // AUDIO_RATE


class MediaCatalog(object):
    """Audio and pictures share one shape: the card's parser walks both."""
    def __init__(self, kind, tracks, pending, skipped):
        self.kind = kind              # "audio" or "pictures"
        tracks.sort(key=lambda t: t.display.lower())
        self.tracks = tracks
        self.pending = pending        # display names still converting
        self.skipped = skipped        # sources with no converter available
        self.prefix = u"audio/" if kind == u"audio" else u"pictures/"
        self.file = u"cogs-audio" if kind == u"audio" else u"cogs-pictures"

    def pages(self):
        return max(1, (len(self.tracks) + AUDIO_PER_PAGE - 1) // AUDIO_PER_PAGE)

    def in_category(self, cat, prefix_sub):
        hits = [t for t in self.tracks if hub_under(t.folder, cat)]
        if not prefix_sub:
            return [(t, t.display) for t in hits]
        rows = [(t, hub_prefixed(t.display, t.folder, cat)) for t in hits]
        rows.sort(key=lambda r: r[1].lower())
        return rows

    def page_json(self, page, base):
        pages, sl = paged(self.tracks, page, AUDIO_PER_PAGE)
        nxt = (page < self.pages()) and (u"%s-%d.json" % (self.file, page + 1)) or None
        return self.body([(t, t.display) for t in sl], self.pages(), len(self.tracks), nxt, base)

    def search_json(self, q, page, base, cat=None, prefix_sub=False):
        q = q.lower()
        pool = self.in_category(cat, prefix_sub) if cat else [(t, t.display) for t in self.tracks]
        if q:
            pool = [r for r in pool if q in r[1].lower()]
        pages = max(1, (len(pool) + AUDIO_PER_PAGE - 1) // AUDIO_PER_PAGE)
        p = min(max(1, page), pages)
        _, sl = paged(pool, p, AUDIO_PER_PAGE)
        return self.body(sl, pages, len(pool), None, base)

    def body(self, rows, pages, total, nxt, base):
        s = [u'{ "pages": %d, "total": %d,' % (pages, total)]
        if nxt:
            s.append(u' "next": %s,' % json_str(nxt))
        s.append(u' "catalog": "%s", "tracks": [\n' % self.kind)
        for i in range(len(rows)):
            t, name = rows[i]
            url = base + self.prefix + encode_path(t.served)
            e = [u'  { "name": %s,' % json_str(name), u' "bytes": %d,' % t.bytes]
            if self.kind == u"audio":
                e.append(u' "secs": %d, "rate": %d, "ch": 1,' % (t.secs(), AUDIO_RATE))
            e.append(u' "source": "lan", "url": %s }' % json_str(url))
            s.append(u"".join(e))
            s.append(u",\n" if i < len(rows) - 1 else u"\n")
        s.append(u"] }\n")
        return u"".join(s)

    def find(self, served):
        for t in self.tracks:
            if t.served == served:
                return t
        return None


STATIONS_EMPTY = u'{ "pages": 1, "total": 0, "catalog": "stations", "tracks": [] }\n'


# ------------------------------------------------------------ converters

def cache_root():
    home = fs_text(os.path.expanduser("~"))
    if sys.platform == "darwin":
        return os.path.join(home, u"Library", u"Caches", u"CoGSImageManager")
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return os.path.join(fs_text(xdg), u"cogs-image-manager")
    if sys.platform.startswith("win"):
        la = os.environ.get("LOCALAPPDATA")
        if la:
            return os.path.join(fs_text(la), u"CoGSImageManager", u"cache")
    return os.path.join(home, u".cache", u"cogs-image-manager")


def cache_key(path):
    """sha256('realpath|mtime')[:32], the Mac app's scheme, so a PCM the app
    already made is reused here and the other way round."""
    real = os.path.realpath(path)
    try:
        mtime = int(os.path.getmtime(path))
    except OSError:
        mtime = 0
    key = u"%s|%d" % (real, mtime)
    return hashlib.sha256(tob(key)).hexdigest()[:32]


class Converters(object):
    """Finds what this machine can convert with. ffmpeg first, afconvert
    (every OS X since 10.4) for audio when ffmpeg is missing; img2shr.py
    with python3 + numpy + Pillow for pictures."""
    def __init__(self, script_dir, audio_tool=u"auto"):
        self.ffmpeg = which("ffmpeg")
        self.afconvert = None
        if sys.platform == "darwin" and os.path.isfile("/usr/bin/afconvert"):
            self.afconvert = "/usr/bin/afconvert"
        if audio_tool == u"afconvert":
            self.ffmpeg = None
        elif audio_tool == u"ffmpeg":
            self.afconvert = None
        elif audio_tool == u"none":
            self.ffmpeg = self.afconvert = None
        self.python3 = None
        self.img2shr = None
        for cand in (os.path.join(script_dir, u"img2shr.py"),
                     os.path.join(script_dir, u"..", u"imagemanager-mac", u"tools", u"img2shr.py")):
            if os.path.isfile(cand):
                self.img2shr = os.path.realpath(cand)
                break
        if self.img2shr:
            p3 = which("python3")
            if p3 and run_quiet([p3, "-c", "import numpy, PIL"]) == 0:
                self.python3 = p3
        self.failed = set()

    def audio_ok(self):
        return bool(self.ffmpeg or self.afconvert)

    def picture_ok(self):
        return bool(self.python3 and self.img2shr)

    def audio_how(self):
        if self.ffmpeg:
            return u"ffmpeg"
        if self.afconvert:
            return u"afconvert"
        return u"none (install ffmpeg; .pcm files still share)"

    def picture_how(self):
        if self.picture_ok():
            return u"img2shr.py"
        return u"none (.shr files still share)"

    def pcm_path(self, src):
        return os.path.join(cache_root(), u"pcm", cache_key(src) + u".pcm")

    def shr_path(self, src):
        return os.path.join(cache_root(), u"shr-text", cache_key(src) + u".shr")

    def transcode_audio(self, src):
        out = self.pcm_path(src)
        if os.path.exists(out):
            return True
        makedirs(os.path.dirname(out))
        tmp = out + u".part"
        ok = False
        if self.ffmpeg:
            rc = run_quiet([self.ffmpeg, "-nostdin", "-v", "error", "-y", "-i", src,
                            "-ac", "1", "-ar", str(AUDIO_RATE), "-f", "u8", tmp])
            ok = (rc == 0 and (file_size(tmp) or 0) > 0)
        elif self.afconvert:
            wav = out + u".wav"
            rc = run_quiet([self.afconvert, "-f", "WAVE", "-d", "UI8@%d" % AUDIO_RATE,
                            "-c", "1", src, wav])
            if rc == 0 and os.path.exists(wav):
                ok = wav_data_to_file(wav, tmp)
            try:
                os.remove(wav)
            except OSError:
                pass
        if ok:
            # The Ensoniq DOC treats a $00 sample as an oscillator halt, so
            # $00 is forbidden on the wire: floor them to $01.
            sanitize_zero_bytes(tmp)
            try:
                rename_over(tmp, out)
                return True
            except OSError:
                pass
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False

    def convert_picture(self, src):
        out = self.shr_path(src)
        if os.path.exists(out):
            return True
        makedirs(os.path.dirname(out))
        tmp = out + u".part"
        rc = run_quiet([self.python3, self.img2shr, src, tmp])
        if rc == 0 and file_size(tmp) == SHR_BYTES:
            try:
                rename_over(tmp, out)
                return True
            except OSError:
                pass
        for junk in (tmp, tmp + u"_preview.png"):
            try:
                os.remove(junk)
            except OSError:
                pass
        return False


def wav_data_to_file(wav, out):
    """Copy the 'data' chunk of a RIFF WAVE file to out. afconvert writes a
    4 KB header, so walk the chunks instead of assuming 44 bytes."""
    try:
        f = open(wav, "rb")
    except IOError:
        return False
    try:
        head = f.read(12)
        if len(head) < 12 or head[:4] != tob(u"RIFF") or head[8:12] != tob(u"WAVE"):
            return False
        while True:
            ch = f.read(8)
            if len(ch) < 8:
                return False
            cid = ch[:4]
            clen = read_le32(ch, 4)
            if cid == tob(u"data"):
                o = open(out, "wb")
                try:
                    left = clen
                    while left > 0:
                        chunk = f.read(min(left, 1 << 20))
                        if not chunk:
                            break
                        o.write(chunk)
                        left -= len(chunk)
                finally:
                    o.close()
                return (file_size(out) or 0) > 0
            f.seek(clen + (clen & 1), 1)
    finally:
        f.close()


def sanitize_zero_bytes(path):
    try:
        f = open(path, "rb")
        try:
            data = f.read()
        finally:
            f.close()
        if tob(u"\x00") in data:
            data = data.replace(tob(u"\x00"), tob(u"\x01"))
            f = open(path, "wb")
            try:
                f.write(data)
            finally:
                f.close()
    except IOError:
        pass


# ------------------------------------------------------------ files folder

def prodos_alias(name, taken):
    """'Letter to Mom.odt' -> LETTERTOMOM.ODT (15 chars, 3-char extension)."""
    stem, ext = os.path.splitext(name)
    ext = u"".join([c for c in ext[1:].upper() if c.isalnum() and ord(c) < 128])[:3]
    stem = u"".join([c for c in stem.upper() if (c.isalnum() or c == u".") and ord(c) < 128])
    stem = stem.strip(u".")
    if not stem or not stem[0].isalpha():
        stem = u"F" + stem
    room = 15 - (ext and (len(ext) + 1) or 0)
    stem = stem[:room]
    suffix = ext and (u"." + ext) or u""
    alias = stem + suffix
    n = 1
    while alias in taken:
        tail = text_type(n)
        alias = stem[:room - len(tail)] + tail + suffix
        n += 1
    return alias


class SharedFile(object):
    def __init__(self, alias, name, path, nbytes, mtime):
        self.alias = alias
        self.name = name
        self.path = path
        self.bytes = nbytes
        self.mtime = mtime


def files_dir(root):
    return os.path.join(root, FILES_SUB)


def files_list(root):
    d = files_dir(root)
    try:
        names = os.listdir(d)
    except OSError:
        return []
    out = []
    taken = set()
    for fn in sorted(names, key=lower_key):
        if fn.startswith(u".") or fn.endswith(u".part"):
            continue
        p = os.path.join(d, fn)
        if not os.path.isfile(p):
            continue
        try:
            st = os.stat(p)
        except OSError:
            continue
        alias = prodos_alias(fn, taken)
        taken.add(alias)
        out.append(SharedFile(alias, fn, p, st.st_size, int(st.st_mtime)))
    return out


def files_find(root, name):
    allf = files_list(root)
    for f in allf:
        if f.name == name:
            return f
    up = name.upper()
    for f in allf:
        if f.alias == up or f.name.upper() == up:
            return f
    return None


def files_safe_name(raw):
    last = raw.replace(u"\\", u"/").split(u"/")
    last = [s for s in last if s]
    fn = (last and last[-1] or u"").strip()
    if not fn or fn.startswith(u".") or len(fn) > FILES_MAX_NAME:
        return None
    for ch in fn:
        if ord(ch) < 0x20 or ch == u":":
            return None
    return fn


def files_listing_txt(root):
    return u"".join([u"%s\t%s\t%d\n" % (f.alias, f.name, f.bytes) for f in files_list(root)])


def files_listing_json(root, base, writable):
    fl = files_list(root)
    s = [u'{\n  "writable": %s,\n  "files": [\n' % (writable and u"true" or u"false")]
    for i in range(len(fl)):
        f = fl[i]
        s.append(u'    {"name": %s, "a2": %s, "bytes": %d, "mtime": %d, "url": %s}' % (
            json_str(f.name), json_str(f.alias), f.bytes, f.mtime,
            json_str(base + u"files/" + pct(f.name))))
        s.append(u",\n" if i + 1 < len(fl) else u"\n")
    s.append(u"  ]\n}\n")
    return u"".join(s)


def files_put(root, raw_name, body, content_range):
    """Returns (code, name). 201 created, 200 replaced, 200/201 piece, or an
    error code. Pieces with Content-Range arrive in order into a .part that
    is renamed into place when complete."""
    safe = files_safe_name(raw_name)
    if safe is None:
        return 400, raw_name
    existing = files_find(root, safe)
    fn = existing and existing.name or safe
    d = files_dir(root)
    makedirs(d)
    final = os.path.join(d, fn)
    part = final + u".part"
    start = 0
    total = len(body)
    if content_range and content_range.startswith(u"bytes "):
        spec = content_range[6:].split(u"/", 1)
        a = spec[0].split(u"-", 1)[0]
        if not a.isdigit():
            return 400, fn
        start = int(a)
        if len(spec) == 2 and spec[1] != u"*":
            if not spec[1].strip().isdigit():
                return 400, fn
            total = int(spec[1])
        else:
            total = -1
    if total >= 0 and start + len(body) > total:
        return 416, fn
    if total > FILES_MAX_BYTES or len(body) > FILES_MAX_BYTES:
        return 413, fn
    have_part = os.path.exists(part)
    if start > 0 and not have_part:
        return 409, fn
    try:
        if start == 0:
            f = open(part, "wb")
            try:
                f.write(body)
            finally:
                f.close()
        else:
            f = open(part, "r+b")
            try:
                f.seek(start)
                f.write(body)
            finally:
                f.close()
    except IOError:
        return 500, fn
    done = total < 0 or start + len(body) >= total
    if done:
        try:
            rename_over(part, final)
        except OSError:
            return 500, fn
        return (existing and 200 or 201), fn
    return (existing and 200 or 201), fn


# ---------------------------------------------------------------- library

class Library(object):
    """The shared folder, its three catalogs, the hub, and the status the
    screen shows. One lock; the HTTP threads read under it, rescans swap
    whole catalogs under it."""

    def __init__(self, root, writable, port, share_name, advertise, converters):
        self.root = root
        self.writable = writable
        self.port = port
        self.share_name = share_name
        self.advertise = advertise
        self.conv = converters
        self.lock = threading.RLock()
        self.disks = DiskCatalog([], writable)
        self.audio = MediaCatalog(u"audio", [], [], 0)
        self.pictures = MediaCatalog(u"pictures", [], [], 0)
        self.hub = HubConfig()
        self.folders = []
        self.files = []
        self.rejected = 0
        self.requests = 0
        self.bytes_served = 0
        self.log_lines = []
        self.last_read = {}
        self.last_write = {}
        self.pending_audio = []
        self.pending_pictures = []
        self.converting = None
        self.signature = None
        self.bonjour = None
        self.bonjour_state = u"off"
        self.sharing = True       # B key: serve + announce (off = 503 to the card, no Bonjour)
        self.stop_flag = False
        self.load_cfg()

    # -- config (per library, a dotfile the walker skips)
    def cfg_path(self):
        return os.path.join(self.root, CFG_NAME)

    def load_cfg(self):
        try:
            f = open(self.cfg_path(), "rb")
        except IOError:
            return
        try:
            cats = []
            for raw in f.read().splitlines():
                line = tos(raw).strip()
                if line.startswith(u"category="):
                    pl = line[9:].split(u"|", 1)
                    cats.append([pl[0], len(pl) == 2 and pl[1] or None])
                elif line == u"prefix_subfolder=1":
                    self.hub.prefix_subfolder = True
                elif line == u"writable=0":
                    self.writable = False
                elif line == u"writable=1":
                    self.writable = True
            if cats:
                self.hub.cats = cats
                self.hub.explicit = True
        finally:
            f.close()

    def save_cfg(self):
        lines = [u"# CoGS Image Manager (text) settings for this library\n"]
        if self.hub.explicit:
            for p, l in self.hub.cats:
                lines.append(u"category=%s%s\n" % (p, l and (u"|" + l) or u""))
        if self.hub.prefix_subfolder:
            lines.append(u"prefix_subfolder=1\n")
        lines.append(u"writable=%d\n" % (self.writable and 1 or 0))
        try:
            f = open(self.cfg_path(), "wb")
            try:
                f.write(tob(u"".join(lines)))
            finally:
                f.close()
        except IOError:
            pass

    # -- logging / status
    def log(self, line):
        self.lock.acquire()
        try:
            self.log_lines.append(u"%s  %s" % (now_hms(), line))
            if len(self.log_lines) > 300:
                del self.log_lines[:len(self.log_lines) - 300]
        finally:
            self.lock.release()
        if HEADLESS:
            write(self.log_lines[-1])

    def base_url_guess(self):
        return u"http://%s:%d/" % (lan_ip(), self.port)

    # -- scanning
    def file_signature(self):
        """Names, sizes and mtimes of everything the share could serve. Other
        files (a log written into the library, a stray .txt) are left out so
        they cannot trigger rescans."""
        h = hashlib.sha1()
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if not d.startswith(u".")]
            in_files = (dirpath == os.path.join(self.root, FILES_SUB))
            for fn in filenames:
                if fn.startswith(u"."):
                    continue
                ext = os.path.splitext(fn)[1][1:].lower()
                if not in_files and ext not in SIG_EXT:
                    continue
                p = os.path.join(dirpath, fn)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                h.update(tob(u"%s|%d|%d\n" % (p, st.st_size, int(st.st_mtime))))
        return h.hexdigest()

    def rescan(self, reason=u"rescan"):
        files = walk_library(self.root)
        images = []
        rejected = 0
        audio_ready = []
        audio_pending = []
        audio_pending_src = []
        audio_skipped = 0
        pic_ready = []
        pic_pending = []
        pic_pending_src = []
        pic_skipped = 0
        seen_a = set()
        seen_p = set()
        for f in files:
            ext = f.ext()
            if ext in DISK_EXT:
                img = probe_disk(f.path, f.folder, f.name)
                if img:
                    images.append(img)
            elif ext in REJECTED_EXT:
                rejected += 1
            elif ext == u"pcm":
                sz = file_size(f.path)
                if sz and f.rel_path() not in seen_a:
                    seen_a.add(f.rel_path())
                    audio_ready.append(Track(f.rel_path(), f.folder, f.path, f.path, sz))
            elif ext == u"shr":
                sz = file_size(f.path)
                if sz and f.rel_path() not in seen_p:
                    seen_p.add(f.rel_path())
                    pic_ready.append(Track(f.rel_path(), f.folder, f.path, f.path, sz))
        for f in files:
            ext = f.ext()
            if ext in AUDIO_SRC_EXT:
                served = os.path.splitext(f.rel_path())[0] + u".pcm"
                if served in seen_a:
                    continue
                seen_a.add(served)
                cached = self.conv.pcm_path(f.path)
                sz = file_size(cached)
                if sz:
                    audio_ready.append(Track(served, f.folder, f.path, cached, sz))
                elif self.conv.audio_ok() and f.path not in self.conv.failed:
                    audio_pending.append(clean_name(f.name))
                    audio_pending_src.append(f.path)
                else:
                    audio_skipped += 1
            elif ext in PICTURE_SRC_EXT:
                served = os.path.splitext(f.rel_path())[0] + u".shr"
                if served in seen_p:
                    continue
                seen_p.add(served)
                cached = self.conv.shr_path(f.path)
                sz = file_size(cached)
                if sz == SHR_BYTES:
                    pic_ready.append(Track(served, f.folder, f.path, cached, sz))
                elif self.conv.picture_ok() and f.path not in self.conv.failed:
                    pic_pending.append(clean_name(f.name))
                    pic_pending_src.append(f.path)
                else:
                    pic_skipped += 1
        folders = folders_of(files)
        self.lock.acquire()
        try:
            self.disks = DiskCatalog(images, self.writable)
            self.audio = MediaCatalog(u"audio", audio_ready, sorted(audio_pending), audio_skipped)
            self.pictures = MediaCatalog(u"pictures", pic_ready, sorted(pic_pending), pic_skipped)
            self.folders = folders
            self.files = files_list(self.root)
            self.rejected = rejected
            self.all_files = files
            if not self.hub.explicit:
                self.hub.defaults_from(folders)
            self.pending_audio = audio_pending_src
            self.pending_pictures = pic_pending_src
            self.signature = self.file_signature()
        finally:
            self.lock.release()
        self.log(u"%s: %d disk image(s), %d track(s), %d picture(s), %d file(s)" % (
            reason, len(images), len(audio_ready), len(pic_ready), len(self.files)))

    def set_writable(self, w):
        self.lock.acquire()
        try:
            self.writable = w
            self.disks.writable = w
        finally:
            self.lock.release()
        self.save_cfg()
        self.log(u"share is now %s" % (w and u"writable" or u"read-only"))

    def counts_under(self, folder):
        """(disks, tracks, pictures) in folder and below."""
        d = len([i for i in self.disks.images if hub_under(i.folder, folder)])
        a = len([t for t in self.audio.tracks if hub_under(t.folder, folder)])
        p = len([t for t in self.pictures.tracks if hub_under(t.folder, folder)])
        return d, a, p

    def hub_json(self, kind):
        self.lock.acquire()
        try:
            if kind == u"audio":
                return self.hub.json(kind, len(self.audio.tracks),
                                     lambda c: len([t for t in self.audio.tracks if hub_under(t.folder, c)]))
            if kind == u"pictures":
                return self.hub.json(kind, len(self.pictures.tracks),
                                     lambda c: len([t for t in self.pictures.tracks if hub_under(t.folder, c)]))
            return self.hub.json(u"disks", len(self.disks.images),
                                 lambda c: len([i for i in self.disks.images if hub_under(i.folder, c)]))
        finally:
            self.lock.release()

    def cover_map(self, base):
        m = {}
        for p in self.pictures.tracks:
            # pct(), not encode_path(): the Mac app encodes the slash too
            # (%2F) and the two servers should emit identical bytes.
            m[pic_stem(p.display)] = base + u"pictures/" + pct(p.served)
        return m

    # -- background work
    def watcher(self):
        """Rescan when the folder changes (poll every 3 s; no FSEvents on a
        G4), and run conversions one at a time."""
        while not self.stop_flag:
            try:
                sig = self.file_signature()
                if sig != self.signature:
                    self.rescan(u"folder changed")
            except Exception:
                pass
            did = False
            src = None
            self.lock.acquire()
            try:
                if self.pending_audio:
                    src = self.pending_audio.pop(0)
                    kind = u"audio"
                elif self.pending_pictures:
                    src = self.pending_pictures.pop(0)
                    kind = u"picture"
            finally:
                self.lock.release()
            if src:
                self.converting = clean_name(os.path.basename(src))
                if kind == u"audio":
                    ok = self.conv.transcode_audio(src)
                else:
                    ok = self.conv.convert_picture(src)
                self.converting = None
                if ok:
                    self.log(u"%s ready: %s" % (kind, clean_name(os.path.basename(src))))
                else:
                    self.conv.failed.add(src)
                    self.log(u"%s convert failed: %s" % (kind, os.path.basename(src)))
                self.rescan(u"converted")
                did = True
            if not did:
                for _ in range(30):
                    if self.stop_flag:
                        break
                    time.sleep(0.1)

    # -- bonjour
    def start_bonjour(self):
        if self.bonjour or not self.advertise:
            return
        cmd = None
        name = self.share_name
        if which("dns-sd"):
            cmd = [which("dns-sd"), "-R", name, "_cogs-share._tcp", "local", str(self.port), "path=/"]
        elif which("avahi-publish"):
            cmd = [which("avahi-publish"), "-s", name, "_cogs-share._tcp", str(self.port), "path=/"]
        elif which("avahi-publish-service"):
            cmd = [which("avahi-publish-service"), name, "_cogs-share._tcp", str(self.port), "path=/"]
        if cmd is None:
            self.bonjour_state = u"no dns-sd/avahi (type the URL on the card)"
            return
        devnull = open(os.devnull, "wb")
        try:
            try:
                self.bonjour = subprocess.Popen(cmd, stdin=devnull, stdout=devnull, stderr=devnull)
                self.bonjour_state = u"on"
                self.log(u"Bonjour: announcing \"%s\" as _cogs-share._tcp" % name)
            except OSError:
                self.bonjour_state = u"failed to start"
        finally:
            devnull.close()

    def stop_bonjour(self):
        if self.bonjour:
            try:
                self.bonjour.terminate()
            except (OSError, AttributeError):
                try:
                    os.kill(self.bonjour.pid, 15)
                except OSError:
                    pass
            self.bonjour = None
            self.bonjour_state = u"off"
            self.log(u"Bonjour: off")

    def reap_stale_bonjour(self):
        """A dns-sd/avahi child outlives a cim.py that was killed (not quit),
        and keeps announcing a share nothing listens on: the card finds it
        and the fetch fails. Kill any leftover announcer of our service type
        on our port before starting ours. ps(1) instead of pgrep: Tiger and
        Leopard have no pgrep."""
        try:
            out = subprocess.Popen(["ps", "-axo", "pid=,command="], stdout=subprocess.PIPE).communicate()[0]
        except OSError:
            return
        me = os.getpid()
        for line in tos(out).splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) != 2:
                continue
            pid, cmd = parts
            if u"_cogs-share._tcp" not in cmd or (u"dns-sd" not in cmd and u"avahi-publish" not in cmd):
                continue
            if (u" %d " % self.port) not in cmd + u" ":
                continue
            try:
                if int(pid) == me:
                    continue
                os.kill(int(pid), 15)
                self.log(u"stopped a leftover announcer (pid %s)" % pid)
            except (OSError, ValueError):
                pass

    # -- sharing (B key): the card sees the share only while this is on
    def sharing_state(self):
        if not self.sharing:
            return u"off"
        if self.bonjour:
            return u"on (Bonjour \"%s\")" % self.share_name
        if not self.advertise:
            return u"on (not announced)"
        return u"on (%s)" % self.bonjour_state

    def start_sharing(self):
        self.sharing = True
        self.log(u"sharing on")
        self.start_bonjour()

    def stop_sharing(self):
        self.sharing = False
        self.stop_bonjour()
        self.log(u"sharing off (card requests get 503 until B again)")

    def toggle_sharing(self):
        if self.sharing:
            self.stop_sharing()
        else:
            self.start_sharing()


HEADLESS = False
LIB = None


def lan_ip():
    """The address the card will reach us at. A UDP connect picks the
    outbound interface without sending anything."""
    s = None
    try:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("10.255.255.255", 1))
            return tos(s.getsockname()[0])
        except socket.error:
            pass
    finally:
        if s:
            s.close()
    try:
        return tos(socket.gethostbyname(socket.gethostname()))
    except socket.error:
        return u"127.0.0.1"


def is_private_ipv4(ip):
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        p = [int(x) for x in parts]
    except ValueError:
        return False
    if p[0] == 10 or p[0] == 127:
        return True
    if p[0] == 172 and 16 <= p[1] <= 31:
        return True
    if p[0] == 192 and p[1] == 168:
        return True
    if p[0] == 169 and p[1] == 254:
        return True
    return False


# ------------------------------------------------------------------- HTTP

STATUS_TEXT = {200: "OK", 201: "Created", 206: "Partial Content", 400: "Bad Request",
               403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed",
               409: "Conflict", 413: "Payload Too Large", 416: "Range Not Satisfiable",
               500: "Internal Server Error"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "CoGS-IM-text/" + VERSION
    sys_version = ""

    def log_message(self, fmt, *args):
        pass

    def __getattr__(self, name):
        # Any method we do not implement answers 405, not 501.
        if name.startswith("do_"):
            return self._method_not_allowed
        raise AttributeError(name)

    def _method_not_allowed(self):
        self.reply(405, "text/plain", u"no\n")

    # -- helpers
    def hdr(self, name):
        v = self.headers.get(name)
        return v is not None and tos(v).strip() or None

    def wants_close(self):
        return (self.hdr("Connection") or u"").lower() == u"close"

    def split_path(self):
        raw = tos(self.path)
        if u"?" in raw:
            p, q = raw.split(u"?", 1)
        else:
            p, q = raw, u""
        query = {}
        for pair in q.split(u"&"):
            if not pair:
                continue
            if u"=" in pair:
                k, v = pair.split(u"=", 1)
                query[k] = unquote(v)
            else:
                query[pair] = u""
        return unquote(p), query

    def base_url(self):
        host = self.hdr("Host") or (u"%s:%d" % (lan_ip(), LIB.port))
        return u"http://%s/" % host

    def reply(self, code, mime, body, extra=None):
        data = tob(body)
        self.send_response(code, STATUS_TEXT.get(code, "?"))
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        if code == 200 and mime == "application/json":
            self.send_header("Accept-Ranges", "bytes")
        for k, v in (extra or []):
            self.send_header(k, v)
        self.send_header("Connection", self.wants_close() and "close" or "keep-alive")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def lan_guard(self):
        ip = self.client_address[0]
        ip4 = ip.replace("::ffff:", "")
        if is_private_ipv4(ip4) or ip.startswith("fe80") or ip == "::1":
            return True
        LIB.log(u"rejected non-LAN peer %s" % tos(ip))
        self.close_connection = 1
        return False

    # -- methods
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self.lan_guard():
            return
        LIB.requests += 1
        path, q = self.split_path()
        base = self.base_url()
        lib = LIB
        if path in (u"/", u"/index.html"):
            return self.reply(200, "text/html; charset=utf-8", index_html(lib, base))
        if not lib.sharing:
            return self.reply(503, "text/plain", u"sharing is off", [("Retry-After", "5")])
        if path == u"/cogs-volumes.json" or (path.startswith(u"/cogs-volumes-") and path.endswith(u".json")):
            n = 1
            if path != u"/cogs-volumes.json":
                try:
                    n = max(1, int(path[len(u"/cogs-volumes-"):-5]))
                except ValueError:
                    n = 1
            lib.log(u"catalog page %d" % n)
            lib.lock.acquire()
            try:
                body = lib.disks.page_json(n, base, lib.cover_map(base))
            finally:
                lib.lock.release()
            return self.reply(200, "application/json", body)
        if path == u"/cogs-your-hub.json":
            kind = {u"a": u"audio", u"p": u"pictures"}.get(q.get(u"i"), u"disks")
            lib.log(u"your hub (%s)" % kind)
            return self.reply(200, "application/json", lib.hub_json(kind))
        if path == u"/search":
            qs = q.get(u"q", u"")
            try:
                p = max(1, int(q.get(u"p", u"1") or u"1"))
            except ValueError:
                p = 1
            cat = q.get(u"cat") or None
            what = cat and (u' cat "%s"' % cat) or u""
            i = q.get(u"i")
            lib.lock.acquire()
            try:
                pre = lib.hub.prefix_subfolder
                if i == u"a":
                    lib.log(u'audio search "%s"%s p%d' % (qs, what, p))
                    body = lib.audio.search_json(qs, p, base, cat, pre)
                elif i == u"s":
                    lib.log(u'stations search "%s" (none on a LAN share)' % qs)
                    body = STATIONS_EMPTY
                elif i == u"p":
                    lib.log(u'pictures search "%s"%s p%d' % (qs, what, p))
                    body = lib.pictures.search_json(qs, p, base, cat, pre)
                else:
                    lib.log(u'search "%s"%s p%d' % (qs, what, p))
                    body = lib.disks.search_json(qs, p, base, lib.cover_map(base), cat, pre)
            finally:
                lib.lock.release()
            return self.reply(200, "application/json", body)
        for cat_obj, stem in ((lib.audio, u"/cogs-audio"), (lib.pictures, u"/cogs-pictures")):
            if path == stem + u".json" or (path.startswith(stem + u"-") and path.endswith(u".json")):
                n = 1
                if path != stem + u".json":
                    try:
                        n = max(1, int(path[len(stem) + 1:-5]))
                    except ValueError:
                        n = 1
                lib.log(u"%s page %d" % (cat_obj.kind, n))
                lib.lock.acquire()
                try:
                    body = cat_obj.page_json(n, base)
                finally:
                    lib.lock.release()
                return self.reply(200, "application/json", body)
        if path.startswith(u"/audio/"):
            t = lib.audio.find(served_path(path, u"/audio/"))
            return self.serve_file(t and t.blob, t and (u"audio/" + t.served))
        if path.startswith(u"/pictures/"):
            t = lib.pictures.find(served_path(path, u"/pictures/"))
            return self.serve_file(t and t.blob, t and (u"pictures/" + t.served))
        if path == u"/files" or path.startswith(u"/files/"):
            return self.serve_files(path, q, base)
        p = resolve_request_path(lib.root, path)
        rel = p and path.strip(u"/") or None
        return self.serve_file(p, rel)

    def serve_files(self, path, q, base):
        lib = LIB
        rest = u"/".join([s for s in path[len(u"/files"):].split(u"/") if s])
        if not rest:
            if q.get(u"fmt") == u"txt":
                lib.log(u"files listing (txt)")
                return self.reply(200, "text/plain; charset=utf-8", files_listing_txt(lib.root))
            lib.log(u"files listing")
            return self.reply(200, "application/json", files_listing_json(lib.root, base, lib.writable))
        name = files_safe_name(rest)
        f = name and files_find(lib.root, name) or None
        if not f:
            return self.reply(404, "text/plain", u"no such file\n")
        lib.log(u"GET files/%s" % f.name)
        return self.serve_file(f.path, u"files/" + f.name)

    def serve_file(self, path, key):
        lib = LIB
        if not path:
            return self.reply(404, "text/plain", u"no such file\n")
        try:
            st = os.stat(path)
        except OSError:
            return self.reply(404, "text/plain", u"no such file\n")
        size = st.st_size
        start = 0
        end = size > 0 and size - 1 or 0
        partial = False
        rng = self.hdr("Range")
        if rng and rng.startswith(u"bytes="):
            partial = True
            spec = rng[6:].split(u",")[0].strip()
            ab = spec.split(u"-", 1)
            try:
                if ab[0] == u"":
                    n = int(ab[1] or u"0")
                    start = size > n and size - n or 0
                else:
                    start = int(ab[0])
                    if len(ab) > 1 and ab[1] != u"":
                        end = int(ab[1])
            except ValueError:
                start, end = 0, size - 1
            end = min(end, size > 0 and size - 1 or 0)
            if start > end or start >= size:
                self.send_response(416, STATUS_TEXT[416])
                self.send_header("Content-Range", "bytes */%d" % size)
                self.send_header("Content-Length", "0")
                self.send_header("Connection", self.wants_close() and "close" or "keep-alive")
                self.end_headers()
                return
        length = end - start + 1
        self.send_response(partial and 206 or 200, STATUS_TEXT[partial and 206 or 200])
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        # fw 1.26 reads these on every reply: the write-protect flag and the
        # idle re-check interval.
        self.send_header("X-CoGS-Writable", lib.writable and "1" or "0")
        self.send_header("X-CoGS-Poll", "5")
        if partial:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.send_header("Last-Modified", formatdate(st.st_mtime, usegmt=True))
        self.send_header("Content-Length", str(length))
        self.send_header("Connection", self.wants_close() and "close" or "keep-alive")
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            f = open(path, "rb")
        except IOError:
            self.close_connection = 1
            return
        try:
            f.seek(start)
            lib.bytes_served += length
            if key:
                lib.last_read[key] = time.time()
            if length > 4096:
                lib.log(u"GET %s [%d-%d]" % (os.path.basename(path), start, end))
            left = length
            while left > 0:
                chunk = f.read(min(left, 256 * 1024))
                if not chunk:
                    self.close_connection = 1
                    break
                try:
                    self.wfile.write(chunk)
                except socket.error:
                    self.close_connection = 1
                    break
                left -= len(chunk)
        finally:
            f.close()

    def read_body(self):
        try:
            n = int(self.hdr("Content-Length") or u"0")
        except ValueError:
            n = 0
        if n <= 0:
            return tob(u"")
        return self.rfile.read(n)

    def do_PUT(self):
        if not self.lan_guard():
            return
        LIB.requests += 1
        lib = LIB
        path, q = self.split_path()
        body = self.read_body()
        if not lib.sharing:
            return self.reply(503, "text/plain", u"sharing is off", [("Retry-After", "5")])
        if not lib.writable:
            return self.reply(403, "text/plain", u"")
        if path.startswith(u"/files/"):
            code, name = files_put(lib.root, path[len(u"/files/"):], body, self.hdr("Content-Range"))
            if code in (200, 201):
                lib.last_write[u"files/" + name] = time.time()
                cr = self.hdr("Content-Range") or u""
                if cr.startswith(u"bytes 0-"):
                    lib.log(u"files: receiving %s (%s bytes)" % (name, cr.split(u"/")[-1]))
                elif not cr:
                    lib.log(u"files: %s saved" % name)
                lib.lock.acquire()
                try:
                    lib.files = files_list(lib.root)
                finally:
                    lib.lock.release()
            else:
                lib.log(u"files: PUT %s -> %d" % (name, code))
            return self.reply(code, "text/plain", u"")
        p = resolve_request_path(lib.root, path)
        if not p:
            return self.reply(404, "text/plain", u"")
        size = file_size(p)
        start = 0
        cr = self.hdr("Content-Range")
        if cr and cr.startswith(u"bytes "):
            a = cr[6:].split(u"/")[0].split(u"-")[0]
            if not a.isdigit():
                return self.reply(400, "text/plain", u"")
            start = int(a)
        # Never grow an image: writes land inside the existing file only.
        if start + len(body) > size:
            return self.reply(416, "text/plain", u"")
        try:
            f = open(p, "r+b")
            try:
                f.seek(start)
                f.write(body)
            finally:
                f.close()
        except IOError:
            return self.reply(500, "text/plain", u"")
        lib.last_write[path.strip(u"/")] = time.time()
        lib.log(u"PUT %s @%d +%d" % (os.path.basename(p), start, len(body)))
        return self.reply(200, "text/plain", u"")


def served_path(path, prefix):
    rel = path[len(prefix):]
    segs = [s for s in rel.split(u"/") if s]
    for s in segs:
        if s == u".." or s.startswith(u"."):
            return None
    return u"/".join(segs) or None


class Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 16


def html_esc(s):
    return s.replace(u"&", u"&amp;").replace(u"<", u"&lt;").replace(u">", u"&gt;")


def index_html(lib, base):
    lib.lock.acquire()
    try:
        rows = []
        for img in lib.disks.images:
            rows.append(u'<li><a href="/%s">%s</a> (%.1f MB, %d blocks%s)</li>' % (
                encode_path(img.rel_path()), html_esc(img.display), img.bytes / 1048576.0,
                img.blocks, img.bootable and u", bootable" or u""))
        if not rows:
            rows.append(u"<li><i>no disk images found</i></li>")
        parts = [u"<!doctype html><meta charset=utf-8><title>CoGS Image Manager (text)</title>",
                 u'<body style="font-family: ui-monospace, monospace; background:#111; color:#3fda66; padding:2em">',
                 u"<h1>] CoGS IMAGE MANAGER (text edition)</h1>",
                 u'<p>Image Source URL for the card: <code style="color:#fff">%s</code></p>' % html_esc(base)]
        if lib.hub.cats:
            cats = []
            for p, l in lib.hub.cats[:HUB_MAX]:
                d, a, pc = lib.counts_under(p)
                cats.append(u'<li><a href="/search?cat=%s">%s</a> (%d)</li>' % (
                    encode_path(p), html_esc(lib.hub.label(p)), d + a + pc))
            parts.append(u'<p>Apple II categories: <a style="color:#3fda66" href="/cogs-your-hub.json">'
                         u'/cogs-your-hub.json</a> (%d)</p><ul>%s</ul>' % (len(lib.hub.cats), u"".join(cats)))
        parts.append(u'<p>Catalog: <a style="color:#3fda66" href="/cogs-volumes.json">/cogs-volumes.json</a> '
                     u'(%d disk images, %d page%s, %s)</p><ul>%s</ul>' % (
                         len(lib.disks.images), lib.disks.pages(), lib.disks.pages() == 1 and u"" or u"s",
                         lib.writable and u"writable" or u"read-only", u"".join(rows)))
        for cat in (lib.audio, lib.pictures):
            if cat.tracks or cat.pending:
                li = [u'<li><a href="/%s%s">%s</a></li>' % (cat.prefix, encode_path(t.served), html_esc(t.display))
                      for t in cat.tracks]
                li += [u"<li><i>%s (converting...)</i></li>" % html_esc(n) for n in cat.pending]
                parts.append(u'<p>%s: <a style="color:#3fda66" href="/%s.json">/%s.json</a> (%d)</p><ul>%s</ul>' % (
                    cat.kind.capitalize(), cat.file, cat.file, len(cat.tracks), u"".join(li)))
        if lib.files or lib.writable:
            li = [u'<li><a href="/files/%s">%s</a> (%s, %d bytes)</li>' % (
                pct(f.name), html_esc(f.name), f.alias, f.bytes) for f in lib.files]
            if not li:
                li = [u"<li><i>empty; an Apple II program can PUT documents here</i></li>"]
            parts.append(u'<p>Files: <a style="color:#3fda66" href="/files/">/files/</a> (%d, %s)</p><ul>%s</ul>' % (
                len(lib.files), lib.writable and u"writable" or u"read-only", u"".join(li)))
        parts.append(u"</body>")
        return u"\n".join(parts)
    finally:
        lib.lock.release()


# ------------------------------------------------------------------ TUI

HELP_LINES = [
    u"KEYS",
    u"",
    u"  Up/Down, j/k   move in the folder tree",
    u"  Right/Enter    open a folder (show its subfolders)",
    u"  Left           close a folder",
    u"  Tab            switch between the tree and the item list",
    u"  C              show/hide this folder as an Apple II category",
    u"  P              prefix subfolder names inside categories",
    u"  W              writable / read-only (the card's lock icon)",
    u"  B              start / stop sharing (Bonjour announce + answering the card)",
    u"  R              rescan the library now",
    u"  L              full-screen request log (L again to return)",
    u"  ?              this help",
    u"  Q              quit",
    u"",
    u"ON THE APPLE II",
    u"",
    u"  CoGS config (C at boot, or the CDA) > Image/Audio Source,",
    u"  type the Image Source URL shown at the top of this screen,",
    u"  then Browse Disks. Top-level folders appear as categories.",
]


def fmt_mb(n):
    if n >= 1048576 * 10:
        return u"%d MB" % (n // 1048576)
    if n >= 1048576:
        return u"%.1f MB" % (n / 1048576.0)
    return u"%d KB" % (n // 1024)


class Tui(object):
    def __init__(self, scr, lib):
        """Initialize the text-based user interface with the given curses screen and library."""
        self.scr = scr
        self.lib = lib
        self.expanded = set([u""])
        self.sel = 0
        self.focus = u"tree"
        self.item_sel = 0
        self.mode = u"main"
        self.color = False
        try:
            curses.curs_set(0)
        except curses.error:
            pass                       # vt100 and friends have no cursor control
        if curses.has_colors():
            curses.start_color()
            try:
                # Trying to set up curses colors is a pain in the ass: some terminals don't support default colors, some do but don't support init_pair() with -1, and some do but don't support bold. 
                # So we try the best we can, and fall back to a safe set of colors if anything fails.
                curses.use_default_colors()
                curses.init_pair(1, curses.COLOR_WHITE, -1)
                curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_WHITE)
                curses.init_pair(3, curses.COLOR_YELLOW, -1)
                curses.init_pair(4, curses.COLOR_WHITE, -1)
            except curses.error:
                # Terminal compatibility is a 55-gallon drum of worms and this is the can opener
                curses.init_pair(1, curses.COLOR_GREEN, curses.COLOR_BLACK)
                curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_GREEN)
                curses.init_pair(3, curses.COLOR_YELLOW, curses.COLOR_BLACK)
                curses.init_pair(4, curses.COLOR_WHITE, curses.COLOR_BLACK)
            self.color = True
        scr.timeout(500)
        scr.keypad(1)

    def attr(self, n, bold=False):
        """Return the curses attribute for the given color pair number, with optional bold."""
        a = self.color and curses.color_pair(n) or 0
        if bold:
            a |= curses.A_BOLD
        return a

    def put(self, y, x, s, attr=0, width=None):
        """Put a string at the given coordinates, with optional attributes and width limit."""
        h, w = self.scr.getmaxyx()
        if y < 0 or y >= h or x >= w:
            return
        if width is None:
            width = w - x
        s = s[:max(0, min(width, w - x - (1 if y == h - 1 else 0)))]
        if not s:
            return
        try:
            self.scr.addstr(y, x, PY2 and s.encode("utf-8") or s, attr)
        except curses.error:
            pass

    # Decided to spiff it up with fancy box-drawing characters, why not

    # The text edition is meant to run on a Tiger PowerBook, a NAS over SSH, PuTTY and a Windows command prompt.
    # Literal UTF-8 turns to garbage on a few of those while the ACS set draws the same lines through the terminal's own line-drawing mode and falls back to + | by itself.
    # I <?> Unicode, it really makes you feel like a pile of [] sometimes though
    # So let's use the ACS set and let the terminal do the work...

    # Horizontal line
    def hline(self, y, title=None, corners=False):
        """Draw a horizontal line at the given y coordinate, with optional title and corners."""
        _, w = self.scr.getmaxyx()
        if w < 2:
            return

        attr = self.attr(1)
        # Use ULCORNER if corners, otherwise use LTEE/RTEE for a tee.
        left = curses.ACS_ULCORNER if corners else curses.ACS_LTEE
        right = curses.ACS_URCORNER if corners else curses.ACS_RTEE

        self.scr.addch(y, 0, left | attr)
        self.scr.hline(y, 1, curses.ACS_HLINE | attr, w - 2)
        self.scr.addch(y, w - 1, right | attr)

        if title:
            text = (u" %s " % title)[:w - 2]
            if text:
                x = max(1, (w - len(text)) // 2) # divides by 2, to center the text
                self.scr.addstr(y, x, text, attr)

    def vline(self, y, x):
        """Draw a vertical line at the given x coordinate, from y to the bottom of the screen."""
        h, w = self.scr.getmaxyx()
        if y < 0 or y >= h or x < 0 or x >= w:
            return
        try:
            self.scr.addch(y, x, curses.ACS_VLINE | self.attr(1))
        except curses.error:
            pass

    # Box sides
    def box_sides(self, y0, y1):
        """Draw vertical lines at the left and right edges of the screen, from y0 to y1."""
        h, w = self.scr.getmaxyx()
        for y in range(y0, y1):
            if y < 0 or y >= h:
                continue
            for x in (0, w - 1):
                self.vline(y, x)

    def bottomLine(self, y, corners=False):
        """Draw a horizontal line at the bottom of the screen, with corners upon request."""
        _, w = self.scr.getmaxyx()
        if w < 2:
            return

        attr = self.attr(1)
        # |_ and _| 
        left = curses.ACS_LLCORNER if corners else curses.ACS_LTEE
        right = curses.ACS_LRCORNER if corners else curses.ACS_RTEE

        self.scr.addch(y, 0, left | attr)
        self.scr.hline(y, 1, curses.ACS_HLINE | attr, w - 2)
        self.scr.addch(y, w - 1, right | attr)

    # -- tree rows: (path, depth, has_children)
    def tree_rows(self):
        """Return a list of all paths in the library, with their depth and whether they have children."""
        lib = self.lib
        folders = lib.folders
        children = {}
        for f in folders:
            parent = u"/".join(f.split(u"/")[:-1])
            children.setdefault(parent, []).append(f)
        rows = []

        def add(path, depth):
            """Add a path and its children to the rows list."""
            kids = children.get(path, [])
            rows.append((path, depth, bool(kids)))
            if path in self.expanded:
                for k in kids:
                    # Using "kids" instead of "children" to avoid munging up the outer variable
                    add(k, depth + 1)
        add(u"", 0)
        return rows

    def draw(self):
        """Draw the whole screen. Called on a timer and after any keypress."""
        scr = self.scr
        scr.erase()
        h, w = scr.getmaxyx()
        lib = self.lib
        if self.mode == u"help":
            self.draw_help()
            return
        if self.mode == u"log":
            self.hline(0, u"REQUEST LOG")
            self.box_sides(1, h - 2)
            lines = lib.log_lines[-(h - 3):]
            for i in range(len(lines)):
                self.put(1 + i, 2, lines[i], self.attr(1), w - 4)
            self.hline(h - 2)
            self.put(h - 1, 1, u" L back   Q quit ", self.attr(2))
            return
        # Draw the fancy header
        title = u" CoGS Image Manager: Text Edition v%s" % VERSION
        self.hline(0, title, corners=True)
        self.vline(1, 0)

        # A clock is useful to ensure it's not hanged
        clock = time.strftime("%a %b %d  %H:%M:%S ")
        self.put(1, w - 1 - len(clock), clock, self.attr(1))
        self.vline(1, w - 1)
        self.hline(2)

        # Draw the library info
        # I'd really like to minimize the vline calls but the curses ACS set is a bit of a pain in the butt to work with
        url = lib.base_url_guess()
        self.vline(3, 0)
        self.put(3, 2, u"Image Source URL: ", self.attr(1))
        self.put(3, 20, url, self.attr(4, True))
        self.put(3, 22 + len(url), u"<- use for image source", self.attr(1))
        self.vline(3, w - 1)
        self.vline(4, 0)
        self.put(4, 2, u"Library: %s" % lib.root, self.attr(1), w - 4)
        self.vline(4, w - 1)
        self.vline(5, 0)
        nb = len([i for i in lib.disks.images if i.bootable])
        line = u"%d disk images (%d bootable)   %d tracks   %d pictures   %d files" % (
            len(lib.disks.images), nb, len(lib.audio.tracks), len(lib.pictures.tracks), len(lib.files))
        self.put(5, 2, line, self.attr(1), w - 4)
        self.vline(5, w - 1)
        self.vline(6, 0)
        state = u"Sharing: %s   Access: %s   Requests: %d   Served: %s" % (
            lib.sharing_state(), lib.writable and u"writable" or u"read-only", lib.requests,
            fmt_mb(lib.bytes_served))
        self.put(6, 2, state, self.attr(1), w - 4)
        self.vline(6, w - 1)
        extra = []
        if lib.converting:
            extra.append(u"converting: %s" % lib.converting)
        pend = len(lib.pending_audio) + len(lib.pending_pictures)
        if pend:
            extra.append(u"%d waiting" % pend)
        if lib.audio.skipped:
            extra.append(u"%d audio need a converter (%s)" % (lib.audio.skipped, lib.conv.audio_how()))
        if lib.pictures.skipped:
            extra.append(u"%d pictures need a converter" % lib.pictures.skipped)
        if lib.rejected:
            extra.append(u"%d .dsk/.do/.woz/.nib skipped (ProDOS order only)" % lib.rejected)
        row = 7
        if extra:
            self.vline(row, 0)
            self.put(row, 2, u"; ".join(extra), self.attr(3), w - 4)
            self.vline(row, w - 1)
            row += 1
        # panes
        log_h = max(4, min(8, (h - row - 6) // 3))
        body_top = row + 1
        body_bot = h - log_h - 3
        self.hline(row, u"FOLDERS" + (u"  (][ = Apple II category)"))
        self.box_sides(body_top, body_bot)
        tree_w = min(44, max(24, w // 2 - 2))
        for y in range(body_top, body_bot):
            self.vline(y, tree_w + 1)
        rows = self.tree_rows()
        if self.sel >= len(rows):
            self.sel = max(0, len(rows) - 1)
        avail = body_bot - body_top
        first = max(0, min(self.sel - avail // 2, len(rows) - avail))
        for i in range(avail):
            idx = first + i
            if idx >= len(rows):
                break
            path, depth, kids = rows[idx]
            d, a, p = lib.counts_under(path)
            name = path and last_component(path) or u"/"
            mark = kids and (path in self.expanded and u"[-]" or u"[+]") or u"   "
            cat = (path and lib.hub.contains(path)) and u"][" or u"  "
            counts = u"I%-4d" % d
            if a:
                counts += u" A%-3d" % a
            if p:
                counts += u" P%-3d" % p
            text = u"%s%s %s" % (u"  " * depth, mark, name)
            text = text[:tree_w - 14].ljust(tree_w - 14) + u" " + cat + u" " + counts
            sel_attr = (idx == self.sel and self.focus == u"tree") and self.attr(2) or self.attr(1)
            if idx == self.sel and self.focus != u"tree":
                sel_attr = self.attr(1, True)
            self.put(body_top + i, 2, text, sel_attr, tree_w - 1)
        # items for the selected folder
        sel_path = rows and rows[self.sel][0] or u""
        x0 = tree_w + 3
        iw = w - x0 - 1
        items = self.items_in(sel_path, max(12, iw - 24))
        if self.item_sel >= len(items):
            self.item_sel = max(0, len(items) - 1)
        first_i = max(0, min(self.item_sel - avail // 2, len(items) - avail))
        if not items:
            self.put(body_top, x0, u"(no items directly in this folder)", self.attr(1), iw)
        for i in range(avail):
            idx = first_i + i
            if idx >= len(items):
                break
            kind, text, key = items[idx]
            act = u"  "
            t = time.time()
            if lib.last_write.get(key, 0) > t - 2:
                act = u"W "
            elif lib.last_read.get(key, 0) > t - 2:
                act = u"R "
            a = self.attr(1)
            if idx == self.item_sel and self.focus == u"items":
                a = self.attr(2)
            elif act != u"  ":
                a = self.attr(3, True)
            self.put(body_top + i, x0, u"%s %s%s" % (kind, act, text), a, iw)
        # Log pane
        self.hline(body_bot, u"LOG") 
        # What rolls down stairs, alone or in pairs, and over your neighbor's dog?
        lines = lib.log_lines[-(log_h):]
        for i in range(len(lines)):
            self.vline(body_bot + 1 + i, 0)
            self.put(body_bot + 1 + i, 2, lines[i], self.attr(1), w - 4)
            self.vline(body_bot + 1 + i, w - 1)
        self.box_sides(body_bot + 1 + len(lines), h - 2)
        self.bottomLine(h - 2, corners=True)

        # Key legend.
        # Maybe we could spiff it up? Trying to minimize terminal jank though
        keys = u" R Rescan  W Writable  B %s  C Category  P Prefix  L Log  ? Help  Q Quit " % (
            lib.sharing and u"Stop sharing" or u"Start sharing")
        self.put(h - 1, 1, keys, self.attr(2), w - 2)

    def items_in(self, folder, nw=30):
        """Return a list of items in the given folder, each as (kind, text, key)."""
        lib = self.lib
        out = []
        for i in lib.disks.images:
            if i.folder == folder:
                tag = i.bootable and u"B" or u" "
                vol = i.volume and (u"/" + i.volume) or u""
                out.append((u"I", u"%s %-*s %5d blk %s" % (tag, nw, i.display[:nw], i.blocks, vol), i.rel_path()))
        for t in lib.audio.tracks:
            if t.folder == folder:
                s = t.secs()
                out.append((u"A", u"  %-*s %3d:%02d" % (nw, t.display[:nw], s // 60, s % 60), u"audio/" + t.served))
        for t in lib.pictures.tracks:
            if t.folder == folder:
                out.append((u"P", u"  %-*s SHR" % (nw, t.display[:nw]), u"pictures/" + t.served))
        if folder == u"":
            for f in lib.files:
                out.append((u"F", u"  %-*s %s" % (nw, f.name[:nw], f.alias), u"files/" + f.name))
        return out

    def draw_help(self):
        """Draw the help screen."""
        h, w = self.scr.getmaxyx()
        self.hline(0, u"HELP")
        self.box_sides(1, h - 2)
        for i in range(len(HELP_LINES)):
            if 1 + i >= h - 2:
                break
            self.put(1 + i, 3, HELP_LINES[i], self.attr(1, HELP_LINES[i].isupper()), w - 5)
        self.hline(h - 2)
        self.put(h - 1, 1, u" any key to return ", self.attr(2))

    def run(self):
        lib = self.lib
        while True:
            self.draw()
            self.scr.refresh()
            try:
                ch = self.scr.getch()
            except KeyboardInterrupt:
                return
            if ch == -1:
                continue
            if self.mode == u"help":
                self.mode = u"main"
                continue
            if ch in (ord("q"), ord("Q")):
                return
            if ch in (ord("?"), ord("h"), ord("H")):
                self.mode = u"help"
                continue
            if ch in (ord("l"), ord("L")):
                self.mode = (self.mode == u"log") and u"main" or u"log"
                continue
            if self.mode == u"log":
                continue
            if ch in (ord("r"), ord("R")):
                daemon_thread(lib.rescan, (u"rescan (R)",)).start()
            elif ch in (ord("w"), ord("W")):
                lib.set_writable(not lib.writable)
            elif ch in (ord("b"), ord("B")):
                lib.toggle_sharing()
            elif ch in (ord("p"), ord("P")):
                lib.hub.prefix_subfolder = not lib.hub.prefix_subfolder
                lib.save_cfg()
                lib.log(u"category names %s the subfolder" % (
                    lib.hub.prefix_subfolder and u"carry" or u"do not carry"))
            elif ch in (ord("c"), ord("C")):
                rows = self.tree_rows()
                if rows and rows[self.sel][0]:
                    if not lib.hub.explicit:
                        lib.hub.explicit = True
                    lib.hub.toggle(rows[self.sel][0])
                    lib.save_cfg()
                    lib.log(u"category %s: %s" % (
                        lib.hub.contains(rows[self.sel][0]) and u"on" or u"off", rows[self.sel][0]))
            elif ch == 9:
                self.focus = (self.focus == u"tree") and u"items" or u"tree"
            elif ch in (curses.KEY_UP, ord("k")):
                if self.focus == u"tree":
                    self.sel = max(0, self.sel - 1)
                    self.item_sel = 0
                else:
                    self.item_sel = max(0, self.item_sel - 1)
            elif ch in (curses.KEY_DOWN, ord("j")):
                if self.focus == u"tree":
                    self.sel = self.sel + 1
                    self.item_sel = 0
                else:
                    self.item_sel += 1
            elif ch in (curses.KEY_RIGHT, 10, 13, curses.KEY_ENTER):
                rows = self.tree_rows()
                if rows:
                    self.expanded.add(rows[self.sel][0])
            elif ch == curses.KEY_LEFT:
                rows = self.tree_rows()
                if rows:
                    path = rows[self.sel][0]
                    if path in self.expanded and path:
                        self.expanded.discard(path)
                    else:
                        parent = u"/".join(path.split(u"/")[:-1])
                        for i in range(len(rows)):
                            if rows[i][0] == parent:
                                self.sel = i
                                break
            elif ch == 12 or ch == curses.KEY_RESIZE:
                self.scr.clearok(1)       # Ctrl-L or a resize: repaint everything


def run_tui(lib):
    try:
        import locale
        locale.setlocale(locale.LC_ALL, "")
    except Exception:
        pass

    def body(scr):
        Tui(scr, lib).run()
    curses.wrapper(body)


# ---------------------------------------------------------------- plain

def plain_status(lib):
    out = []
    out.append(u"] CoGS IMAGE MANAGER  text edition v%s" % VERSION)
    out.append(u"")
    out.append(u"  Image Source URL:  %s   <- use for image source" % lib.base_url_guess())
    out.append(u"  Library:           %s" % lib.root)
    nb = len([i for i in lib.disks.images if i.bootable])
    out.append(u"  %d disk images (%d bootable), %d tracks, %d pictures, %d files" % (
        len(lib.disks.images), nb, len(lib.audio.tracks), len(lib.pictures.tracks), len(lib.files)))
    out.append(u"  Sharing: %s   Access: %s   Requests: %d   Served: %s" % (
        lib.sharing_state(), lib.writable and u"writable" or u"read-only", lib.requests, fmt_mb(lib.bytes_served)))
    out.append(u"  Converters: audio %s, pictures %s" % (lib.conv.audio_how(), lib.conv.picture_how()))
    if lib.rejected:
        out.append(u"  %d .dsk/.do/.woz/.nib skipped (the card mounts ProDOS-order images only)" % lib.rejected)
    return u"\n".join(out)


def plain_tree(lib):
    out = [u"  /  (I %d)" % lib.counts_under(u"")[0]]
    for f in lib.folders:
        depth = f.count(u"/")
        d, a, p = lib.counts_under(f)
        cat = lib.hub.contains(f) and u" ][" or u""
        extra = (a and (u" A %d" % a) or u"") + (p and (u" P %d" % p) or u"")
        out.append(u"  %s%s  (I %d%s)%s" % (u"  " * (depth + 1), last_component(f), d, extra, cat))
    return u"\n".join(out)


def plain_list(lib, folder):
    out = []
    for i in lib.disks.images:
        if i.folder == folder:
            out.append(u"  I %s %-32s %6d blk  %s" % (i.bootable and u"B" or u" ", i.display[:32], i.blocks,
                                                       i.volume and (u"/" + i.volume) or u""))
    for t in lib.audio.tracks:
        if t.folder == folder:
            out.append(u"  A   %-32s %3d:%02d" % (t.display[:32], t.secs() // 60, t.secs() % 60))
    for t in lib.pictures.tracks:
        if t.folder == folder:
            out.append(u"  P   %-32s SHR" % t.display[:32])
    if folder == u"":
        for f in lib.files:
            out.append(u"  F   %-32s %s" % (f.name[:32], f.alias))
    return u"\n".join(out) or u"  (nothing directly in this folder)"


PLAIN_HELP = u"""  s            status (URL, counts)
  t            folder tree
  l [folder]   list what is in a folder (l Games/Arcade)
  c <folder>   show/hide a folder as an Apple II category
  p            toggle subfolder prefix inside categories
  w            toggle writable / read-only
  b            start / stop sharing
  r            rescan now
  g            last 20 log lines
  q            quit"""


def write(s):
    """Print one text line; UTF-8 bytes on 2.x so a pipe or an ASCII
    terminal never raises."""
    if PY2:
        sys.stdout.write(tob(s))
    else:
        sys.stdout.write(s)
    sys.stdout.write("\n")
    sys.stdout.flush()


def run_plain(lib):
    """Run a simple line-mode prompt for headless operation (launchd, cron, NAS)."""
    write(plain_status(lib))
    write(u"")
    write(u"Type ? for commands.")
    while True:
        try:
            sys.stdout.write("cim> ")
            sys.stdout.flush()
            line = sys.stdin.readline()
        except KeyboardInterrupt:
            return # no ^C traceback, just exit
        if not line:
            return
        line = fs_text(line).strip()
        if not line:
            continue
        cmd, _, arg = line.partition(u" ")
        cmd = cmd.lower()
        arg = arg.strip().strip(u"/")

        # Headless commands
        if cmd in (u"q", u"quit", u"exit"): # quit, exit (take notes, Vim...)
            return
        elif cmd in (u"?", u"help", u"h"): # help
            write(PLAIN_HELP)
        elif cmd == u"s": # status
            write(plain_status(lib))
        elif cmd == u"t": # tree
            write(plain_tree(lib))
        elif cmd == u"l": # list
            write(plain_list(lib, arg))
        elif cmd == u"c" and arg: # ???
            lib.hub.toggle(arg)
            lib.save_cfg()
            write(u"  category %s: %s" % (lib.hub.contains(arg) and u"on" or u"off", arg))
        elif cmd == u"p":
            lib.hub.prefix_subfolder = not lib.hub.prefix_subfolder
            lib.save_cfg()
            write(u"  prefix subfolder: %s" % (lib.hub.prefix_subfolder and u"on" or u"off"))
        elif cmd == u"w": # toggle writable / read-only
            lib.set_writable(not lib.writable)
            write(u"  share: %s" % (lib.writable and u"writable" or u"read-only"))
        elif cmd == u"b": # start / stop sharing
            lib.toggle_sharing()
            write(u"  sharing: %s" % lib.sharing_state())
        elif cmd == u"r": # Rescan the library
            lib.rescan(u"rescan")
            write(plain_status(lib))
        elif cmd == u"g": # show last 20 log lines
            write(u"\n".join(lib.log_lines[-20:]) or u"  (nothing yet)")
        else:
            write(u"  ? for commands")


# ---------------------------------------------------------------- main

def daemon_thread(target, args=()):
    """Start a thread that will not block the process from exiting.
    The thread is not joined, so it will be killed when the process exits."""
    t = threading.Thread(target=target, args=args)
    if hasattr(t, "daemon"):
        t.daemon = True            # 2.6+ / 3.x
    else:
        t.setDaemon(True)          # 2.5
        # setDaemon is deprecated in 3.10, but we still need it for 2.5
    return t


def main(argv):
    """This is where the magic happens. Parse command-line options, start the server, and run the UI."""
    global LIB, HEADLESS
    op = optparse.OptionParser(
        usage="%prog [options] FOLDER",
        version="CoGS Image Manager text edition " + VERSION,
        description="Share a folder of Apple II disk images (plus music and pictures) "
                    "to CoGS cards on the LAN. Same wire contract as the Mac and Windows apps.")
    op.add_option("--port", type="int", default=DEFAULT_PORT, help="TCP port (default %d)" % DEFAULT_PORT)
    op.add_option("--read-only", action="store_true", default=False,
                  help="refuse the card's block writes (default: writable)")
    op.add_option("--no-bonjour", action="store_true", default=False, help="do not announce over Bonjour")
    op.add_option("--name", default=None, help="Bonjour share name (default: this machine's name)")
    op.add_option("--category", action="append", default=[], metavar="FOLDER[=LABEL]",
                  help="show FOLDER as an Apple II category (repeatable; default: every top-level folder)")
    op.add_option("--prefix-subfolder", action="store_true", default=False,
                  help="inside a category, names one level down carry the subfolder")
    op.add_option("--audio-tool", default="auto", metavar="auto|ffmpeg|afconvert|none",
                  help="what turns MP3/M4A/WAV/AIFF into the card's PCM (default: ffmpeg if installed, "
                       "else afconvert on a Mac)")
    op.add_option("--plain", action="store_true", default=False, help="line-mode prompt instead of the full-screen UI")
    op.add_option("--headless", action="store_true", default=False, help="no UI; log to stdout (launchd, cron, NAS)")
    opts, args = op.parse_args(argv[1:])
    if len(args) != 1:
        op.print_help()
        return 2
    root = os.path.abspath(fs_text(args[0]))
    if not os.path.isdir(root):
        sys.stderr.write("not a folder: %s\n" % tos(tob(root)))
        return 2
    HEADLESS = bool(opts.headless)
    script_dir = os.path.dirname(os.path.abspath(fs_text(__file__)))
    conv = Converters(script_dir, fs_text(opts.audio_tool))
    name = opts.name and fs_text(opts.name) or (tos(socket.gethostname()).split(u".")[0] or u"CoGS Share")
    lib = Library(root, not opts.read_only, opts.port, name, not opts.no_bonjour, conv)
    if opts.read_only:
        lib.writable = False          # the flag beats a saved writable=1
    if opts.category:
        lib.hub.cats = []
        for c in opts.category:
            c = fs_text(c)
            if u"=" in c:
                p, l = c.split(u"=", 1)
                lib.hub.cats.append([p.strip(u"/"), l])
            else:
                lib.hub.cats.append([c.strip(u"/"), None])
        lib.hub.explicit = True
    if opts.prefix_subfolder:
        lib.hub.prefix_subfolder = True
    LIB = lib
    lib.rescan(u"scan")
    try:
        srv = Server(("", opts.port), Handler)
    except socket.error:
        e = sys.exc_info()[1]
        sys.stderr.write("cannot listen on port %d: %s\n" % (opts.port, e))
        return 1
    t = daemon_thread(srv.serve_forever)
    t.start()
    lib.log(u"share up on port %d" % opts.port)
    lib.reap_stale_bonjour()
    lib.start_bonjour()
    wt = daemon_thread(lib.watcher)
    wt.start()

    # A closed Terminal window (SIGHUP) or a kill (SIGTERM) must take the
    # dns-sd child down with us, or the card keeps finding a dead share.
    def on_signal(signum, frame):
        raise KeyboardInterrupt()
    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, on_signal)
        except (ValueError, OSError, AttributeError):
            pass
    try:
        if HEADLESS:
            write(plain_status(lib))
            while True:
                time.sleep(3600)
        elif opts.plain or curses is None or not sys.stdout.isatty():
            run_plain(lib)
        else:
            run_tui(lib)
    except KeyboardInterrupt:
        pass
    lib.stop_flag = True
    lib.stop_bonjour()
    try:
        srv.shutdown()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))