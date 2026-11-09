#!/usr/bin/env python3
"""
img2shr.py - convert a modern still (JPEG/PNG/GIF/...) to a 32712-byte
Apple IIgs Super Hi-Res blob the CoGS CDA can blit.

Layout (same as viz/png2shr.py):
    [0      .. 31999]  pixels   (320x200, 4bpp, 2 px/byte, hi nibble = left)
    [32000  .. 32199]  SCBs     (one per scanline; low nibble = palette 0..15)
    [32200  .. 32711]  palettes (16 palettes x 16 colours x 2 bytes, $0RGB LE)

Also writes <out>_preview.png: the image decoded back through the 4-bit
palettes, i.e. what the GS will actually show.

Usage:
  python3 img2shr.py INPUT.jpg OUTPUT.shr
  python3 img2shr.py INPUT.png OUTPUT.shr --sat 1.45 --con 1.10 --dither
"""
import argparse
import sys

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

W, H = 320, 200
NPAL = 16
NCOL = 16
SHR_BYTES = 32000 + 200 + 512

# Punch defaults: 4-bit/16-colour is dull without a lift.
DEF_SAT = 1.45
DEF_CON = 1.10
DEF_SHARP = 0
DEF_SMOOTH = 0.4
DEF_GAMMA = 1.0


def median_cut_palette(pixels, ncol=NCOL):
    if len(pixels) == 0:
        return np.zeros((1, 3), np.uint8)
    im = Image.fromarray(pixels.reshape(-1, 1, 3).astype(np.uint8), "RGB")
    q = im.quantize(colors=ncol, method=Image.MEDIANCUT)
    pal = np.array(q.getpalette()[: ncol * 3], np.uint8).reshape(-1, 3)
    used = len(set(q.getdata()))
    return pal[:max(used, 1)]


def snap4(rgb):
    return np.clip((rgb.astype(np.int32) + 8) // 17, 0, 15).astype(np.uint8)


def expand4(rgb4):
    return (rgb4.astype(np.int32) * 17).astype(np.uint8)


def _pad16(pal):
    if len(pal) < NCOL:
        pal = np.vstack([pal, np.tile(pal[-1], (NCOL - len(pal), 1))])
    return pal[:NCOL]


def _row_feature(arr, y):
    pal = median_cut_palette(arr[y], NCOL).astype(np.float32)
    luma = pal @ np.array([0.299, 0.587, 0.114], np.float32)
    pal = pal[np.argsort(luma)]
    return _pad16(pal).reshape(-1)


def _kmeans(feats, k, iters=24, seed=0):
    rng = np.random.default_rng(seed)
    cent = feats[rng.choice(len(feats), k, replace=False)].copy()
    assign = np.zeros(len(feats), int)
    for _ in range(iters):
        d = ((feats[:, None, :] - cent[None, :, :]) ** 2).sum(2)
        new = d.argmin(1)
        if np.array_equal(new, assign):
            break
        assign = new
        for c in range(k):
            m = assign == c
            if m.any():
                cent[c] = feats[m].mean(0)
    return assign


def build_palettes(arr, n=NPAL):
    feats = np.stack([_row_feature(arr, y) for y in range(H)])
    assign = _kmeans(feats, n)
    palettes = []
    for c in range(n):
        rows = np.where(assign == c)[0]
        if len(rows) == 0:
            palettes.append(snap4(median_cut_palette(arr.reshape(-1, 3), NCOL)))
            palettes[-1] = _pad16(palettes[-1])
            continue
        px = arr[rows].reshape(-1, 3)
        palettes.append(_pad16(snap4(median_cut_palette(px, NCOL))))
    return palettes


def quantize_row(row8, pal4, dither):
    pal8 = expand4(pal4).astype(np.int32)
    work = row8.astype(np.float32).copy()
    idx = np.zeros(W, np.uint8)
    dec = np.zeros((W, 3), np.uint8)
    err = np.zeros(3, np.float32)
    for x in range(W):
        p = work[x] + (err if dither else 0)
        d = ((pal8 - p) ** 2).sum(1)
        k = int(d.argmin())
        idx[x] = k
        dec[x] = pal8[k]
        if dither:
            err = (p - pal8[k]) * 0.5
    return idx, dec


def best_palette_for_row(row8, palettes, skip=()):
    best_i, best_e = 0, None
    for i, pal4 in enumerate(palettes):
        if i in skip:
            continue
        pal8 = expand4(pal4).astype(np.int32)
        d = ((row8.astype(np.int32)[:, None, :] - pal8[None, :, :]) ** 2).sum(2)
        e = d.min(1).sum()
        if best_e is None or e < best_e:
            best_i, best_e = i, e
    return best_i


def load_rgb(src):
    """Open a still the way a human sees it: honor EXIF orientation, then
    RGB. Do not rotate portrait to fill the SHR canvas."""
    im = Image.open(src)
    im = ImageOps.exif_transpose(im)
    if im.mode == "RGBA" or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (0, 0, 0))
        bg.paste(im, mask=im.split()[-1])
        return bg
    return im.convert("RGB")


def fit_canvas(im, w=W, h=H, fill=(0, 0, 0)):
    """Contain-fit into 320x200. Stretching a box scan (often ~1.1:1 or
    portrait) to 16:10 flattened the art and read as the wrong orientation."""
    canvas = Image.new("RGB", (w, h), fill)
    iw, ih = im.size
    if iw <= 0 or ih <= 0:
        return canvas
    scale = min(w / iw, h / ih)
    nw = max(1, int(round(iw * scale)))
    nh = max(1, int(round(ih * scale)))
    fitted = im.resize((nw, nh), Image.LANCZOS)
    canvas.paste(fitted, ((w - nw) // 2, (h - nh) // 2))
    return canvas


def convert(src, out, sat=DEF_SAT, con=DEF_CON, dither=False,
            smooth=DEF_SMOOTH, sharp=DEF_SHARP, gamma=DEF_GAMMA,
            preview2x=False, reserve_pal=None, unpacked32k=False):
    im = fit_canvas(load_rgb(src))
    if smooth > 0:
        im = im.filter(ImageFilter.GaussianBlur(smooth))
    if sharp > 0:
        im = im.filter(ImageFilter.UnsharpMask(radius=1.2, percent=int(sharp),
                                               threshold=2))
    im = ImageEnhance.Color(im).enhance(sat)
    im = ImageEnhance.Contrast(im).enhance(con)
    if gamma != 1.0:
        lut = [int(((i / 255.0) ** gamma) * 255 + 0.5) for i in range(256)] * 3
        im = im.point(lut)
    arr = np.array(im, np.uint8)

    skip = set() if reserve_pal is None else {reserve_pal}
    n_art = NPAL - len(skip)
    art = build_palettes(arr, n_art)
    palettes = []
    j = 0
    for i in range(NPAL):
        if i in skip:
            palettes.append(np.zeros((NCOL, 3), np.uint8))
        else:
            palettes.append(art[j])
            j += 1
    pix = np.zeros(H * 160, np.uint8)
    scb = np.zeros(H, np.uint8)
    preview = np.zeros((H, W, 3), np.uint8)
    for y in range(H):
        row = arr[y]
        pi = best_palette_for_row(row, palettes, skip=skip)
        scb[y] = pi
        idx, dec = quantize_row(row, palettes[pi], dither)
        preview[y] = dec
        hi = idx[0::2]
        lo = idx[1::2]
        pix[y * 160:(y + 1) * 160] = (hi << 4) | lo

    palbytes = np.zeros(NPAL * NCOL * 2, np.uint8)
    for i, pal4 in enumerate(palettes):
        for c in range(NCOL):
            r, g, b = int(pal4[c][0]), int(pal4[c][1]), int(pal4[c][2])
            off = (i * NCOL + c) * 2
            palbytes[off] = (g << 4) | b
            palbytes[off + 1] = r

    # CIM wire is 32712 (200 SCB bytes, palettes sit at $9DC8 and the
    # CDA slides them). Unpacked $C1/0000 screen is 32768: 56 zero SCB
    # pad so palettes land at $9E00 with a straight blit to $2000.
    scb_bytes = bytes(scb) + (b"\x00" * 56 if unpacked32k else b"")
    want = 32768 if unpacked32k else SHR_BYTES
    blob = bytes(pix) + scb_bytes + bytes(palbytes)
    if len(blob) != want:
        raise SystemExit("shr size %d, want %d" % (len(blob), want))
    with open(out, "wb") as f:
        f.write(blob)
    Image.fromarray(preview, "RGB").save(out + "_preview.png")
    if preview2x:
        Image.fromarray(preview, "RGB").resize((W * 2, H * 2), Image.NEAREST).save(
            out + "_preview2x.png")
    return blob


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--sat", type=float, default=DEF_SAT,
                    help="saturation boost (default %.2f)" % DEF_SAT)
    ap.add_argument("--con", type=float, default=DEF_CON,
                    help="contrast boost (default %.2f)" % DEF_CON)
    ap.add_argument("--dither", action="store_true",
                    help="row-carry error diffusion")
    ap.add_argument("--smooth", type=float, default=DEF_SMOOTH)
    ap.add_argument("--sharp", type=float, default=DEF_SHARP)
    ap.add_argument("--gamma", type=float, default=DEF_GAMMA)
    ap.add_argument("--preview2x", action="store_true")
    ap.add_argument("--reserve-pal", type=int, default=None, metavar="N",
                    help="leave palette N unused (SCB never selects it; "
                         "table stays zero). 1 = free for text overlay")
    ap.add_argument("--unpacked32k", action="store_true",
                    help="write 32768-byte $C1/0000 screen (56-byte SCB pad), "
                         "not the 32712 CIM wire")
    args = ap.parse_args()
    convert(args.input, args.output, sat=args.sat, con=args.con,
            dither=args.dither, smooth=args.smooth, sharp=args.sharp,
            gamma=args.gamma, preview2x=args.preview2x,
            reserve_pal=args.reserve_pal, unpacked32k=args.unpacked32k)
    print("wrote %s (%d bytes)" % (args.output, 32768 if args.unpacked32k else SHR_BYTES))
    print("preview: %s_preview.png" % args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
