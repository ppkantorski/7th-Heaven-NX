#!/usr/bin/env python3
"""Render every field's layer-1/2 background to a small thumbnail, for
screenshot identification. Uses this tree's own section-9 reader, so it
handles the modded page sizes and depth-2 pages."""
import os, struct, sys, time
import numpy as np
import lgp, diag_common as DC

T_DSTX, T_DSTY, T_SRCX, T_SRCY, T_PAL, T_TEX, T_DEPTH = 2, 4, 10, 12, 22, 32, 36
TILE = 16
W, H = 640, 480          # canvas; centre at (320, 240)
CX, CY = 320, 240


def palettes(sec):
    for hdr in (8, 12, 16):
        if len(sec) < hdr:
            continue
        if hdr == 8:
            _x, _y, cpp, npg = struct.unpack_from('<HHHH', sec, 0)
        elif hdr == 12:
            _l, _x, _y, cpp, npg = struct.unpack_from('<IHHHH', sec, 0)
        else:
            _a, _l, _x, _y, cpp, npg = struct.unpack_from('<IIHHHH', sec, 0)
        if 1 <= cpp <= 1024 and 1 <= npg <= 256 and hdr + 2 * cpp * npg == len(sec):
            v = np.frombuffer(sec, '<u2', count=cpp * npg,
                              offset=hdr).reshape(npg, cpp)
            r = ((v & 0x1F) << 3) | ((v & 0x1F) >> 2)
            g = (((v >> 5) & 0x1F) << 3) | (((v >> 5) & 0x1F) >> 2)
            b = (((v >> 10) & 0x1F) << 3) | (((v >> 10) & 0x1F) >> 2)
            return np.stack([r, g, b], -1).astype(np.uint8), cpp, npg
    raise ValueError('palette does not close')


def page_rgb(p):
    """A page as HxWx3 uint8, plus a bool 'opaque' mask."""
    if p.depth == 1:
        idx = np.frombuffer(p.data, np.uint8).reshape(256, 256)
        return idx, None
    v = np.frombuffer(p.data, '<u2').reshape(p.px, p.px)
    r = ((v >> 11) & 0x1F).astype(np.uint16); r = (r << 3) | (r >> 2)
    g = ((v >> 5) & 0x3F).astype(np.uint16);  g = (g << 2) | (g >> 4)
    b = (v & 0x1F).astype(np.uint16);         b = (b << 3) | (b >> 2)
    return np.stack([r, g, b], -1).astype(np.uint8), (v != 0)


def render(raw):
    parts = lgp.split_sections(raw)
    pal, cpp, npg = palettes(parts[3])
    sec9 = parts[8]
    surv = DC.survey(sec9)
    pages = {p.slot: p for p in surv['pages']}
    cache = {}
    img = np.zeros((H, W, 3), np.uint8)
    drawn = np.zeros((H, W), bool)
    for layer, offs in DC.walk_layers(sec9, surv['back_start'],
                                      surv['tex_start']):
        if layer != 1:
            continue
        for o in offs:
            dx = struct.unpack_from('<h', sec9, o + T_DSTX)[0] + CX
            dy = struct.unpack_from('<h', sec9, o + T_DSTY)[0] + CY
            if not (0 <= dx <= W - TILE and 0 <= dy <= H - TILE):
                continue
            slot = sec9[o + T_TEX]
            p = pages.get(slot)
            if p is None:
                continue
            if slot not in cache:
                cache[slot] = page_rgb(p)
            data, opaque = cache[slot]
            sx, sy = sec9[o + T_SRCX], sec9[o + T_SRCY]
            if p.depth == 1:
                idx = data[sy:sy + TILE, sx:sx + TILE]
                if idx.shape != (TILE, TILE):
                    continue
                pid = sec9[o + T_PAL]
                if pid >= npg:
                    continue
                rgb = pal[pid][idx]
                m = idx != 0
            else:
                k = p.px // 256
                rgb = data[sy * k:sy * k + TILE, sx * k:sx * k + TILE]
                if rgb.shape[:2] != (TILE, TILE):
                    continue
                m = opaque[sy * k:sy * k + TILE, sx * k:sx * k + TILE]
            sub = img[dy:dy + TILE, dx:dx + TILE]
            sub[m] = rgb[m]
            drawn[dy:dy + TILE, dx:dx + TILE] |= m
    return img, drawn


def thumb(img, tw=24, th=16):
    """The 4:3 picture (dst -160..160, -112..112) as a small RGB array."""
    crop = img[CY - 112:CY + 112, CX - 160:CX + 160].astype(np.float32)
    ph, pw = 224 // th, 320 // tw
    crop = crop[:th * ph, :tw * pw]
    return crop.reshape(th, ph, tw, pw, 3).mean(axis=(1, 3))


def main():
    a, b = int(sys.argv[1]), int(sys.argv[2])
    arc = lgp.Archive(os.environ.get('FLEVEL', '/sessions/determined-adoring-ride/mnt/uploads/flevel.lgp'))
    names = sorted(n for n in arc.names() if arc.is_field(arc.index[n]))[a:b]
    out, bad = {}, {}
    t = time.time()
    for n in names:
        try:
            img, drawn = render(arc.decompressed(arc.index[n]))
            cols = drawn.any(axis=0)
            xs = np.nonzero(cols)[0]
            out[n] = (thumb(img), int(xs.min()) - CX, int(xs.max()) - CX)
        except Exception as exc:                                  # noqa: BLE001
            bad[n] = '%s: %s' % (type(exc).__name__, exc)
    np.savez_compressed(os.environ.get('THUMBDIR','/tmp')+'/thumbs_%d.npz' % a,
                        names=np.array(list(out)),
                        th=np.array([v[0] for v in out.values()]),
                        ext=np.array([[v[1], v[2]] for v in out.values()]))
    print('%d ok, %d bad, %.1fs' % (len(out), len(bad), time.time() - t))
    for k, v in list(bad.items())[:6]:
        print('   ', k, v)


if __name__ == '__main__':
    main()
