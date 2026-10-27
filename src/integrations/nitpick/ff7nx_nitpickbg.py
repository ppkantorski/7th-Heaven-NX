"""
ff7nx_nitpickbg.py -- Nitpick's background fixes as PATCHES on Cosmos Limit
Break's art (BUILD 643).

Nitpick's seven fixes (the Seventh Heaven sign, its floor mat, the Sector 5
truck label, the Midgar clock, the Wall Market graffiti, the Sector 5 gate
number, the Corel Prison sign) ship WHOLE field pages, drawn for SYW's
backgrounds. Fed to the field-art repack as they are, each one replaced
Cosmos's whole page -- every other texel of those fields changed, so the
user's own hash-checked art patches (ff7nx_artpatch: the TV edge in
mds7pb_1) no longer matched and were dropped; and where Cosmos lays a page
out differently (its uncropped / extended fields: mrkt4 pages 2-4, mds5_5
page 1, md8_3 page 2) the vanilla-layout page drew the wrong picture --
"SYW graffiti is super fucked up".

So with Cosmos Limit Break on, a Nitpick page becomes Cosmos's own page with
ONLY the cells Nitpick actually changed copied in:

  * Cosmos's page is the one the repack would use for that (field, page,
    palette): its enabled folders, resolved the repack's way;
  * a page is compared on its 16x16 cell grid (one cell = one 16-unit tile);
    when the median cell differs by more than LAYOUT_MEDIAN, Cosmos laid the
    page out differently and the page is left to Cosmos entirely;
  * otherwise a cell is taken from Nitpick when it differs by more than
    max(CELL_MIN, 3 x median + 4) and is drawn (>= half opaque) in both --
    a fix of existing art, never art moved into a cell Cosmos left empty;
  * a page where more than MAX_CELLS cells qualify is Cosmos's own different
    art, not a fix, and is left to Cosmos.

The patched pages are written as uncompressed DDS under one folder that
takes Nitpick's place in the repack's sources; pages with nothing to patch
are not written, so Cosmos's own pages stay byte for byte. Without Cosmos
Limit Break, Nitpick's pages are used whole as before.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import hashlib
import json
import os
import struct

import numpy as np

CLB_ID = '7c6a0289-ae89-411d-836a-531e78938401'
VERSION = 'nitpickbg-10'
LAYOUT_MEDIAN = 12.0
CELL_MIN = 10.0
# BUILD 643b: the user -- "these fixes are meant to be precise to what they
# are describing". Cell differences elsewhere on these pages are Nitpick's
# SYW-based re-render of the same art, not fixes, so a fix is only looked
# for inside the area that holds the thing it names (page cells, rows r0..r1
# and columns c0..c1 inclusive, measured on the pages against Cosmos's):
#   mds7      page 2  the "Seventh Heaven" sign (Cosmos's reads "...STER")
#   mds7pb_1  page 3  the floor mat ("Welcome")
#   md8_2     page 1  the truck label (Cosmos's still reads "MIDGAL'S"); the
#                     letters are a few pixels of three cells, so only the
#                     pixels Nitpick changed are taken ('px'), not the cells
# The other four (clock, gate number, graffiti, prison sign) keep Cosmos's
# art: Cosmos lays those pages out differently (md8_3 2, mds5_5 1, mrkt4
# 2-4), and its prison sign already reads "Pyck".
FIX_AREAS = {
    # 644e: plus the two backdrop cells above "Heaven" (layer 1): the
    # board's top frame and upper edge, which Cosmos drew for the old sign;
    # 644f: and the board's left bracket (13,5), where a light speck of the
    # old "T" flourish peeks out beside the new "S"
    ('mds7', 2): ((13, 14, 1, 9), (2, 2, 5, 6, 'all'), (13, 13, 5, 5, 'all')),
    ('mds7pb_1', 3): (5, 7, 10, 13),
    ('md8_2', 1): (6, 6, 11, 13, 'px'),
}
# BUILD 644d: the bar sign's GLOW. The sign is lit by additive layer-2 tiles
# that animate onto page 15 (palettes 0, 11, 14 -- Limit Break AA's frames).
# That light was painted for the old sign, which stood taller: above the new
# "Seventh Heaven" board it still draws the old outline, the top of the R
# (seen on hardware). Measured against the vanilla tile layout, those tiles
# sit in one screen row above the board (page 15 cells (0,10)-(0,12)) and the
# row that ends at the board's top edge ((1,7)-(1,11)). Both are faded
# (see 644g below). Applied to every Cosmos palette of the page, only when
# the sign fix itself is applied.
# 644g: OFF. The leftover R was in the wall cells (644e/f), not the light;
# with those fixed, Cosmos's own glow sits naturally on the new board, and
# every trim (hard cut, feather) looked worse on hardware than none. Kept as
# a mechanism (trim_glow / FEATHER_EXAMPLE) should a sign ever need it.
GLOW_TRIMS = {}
FEATHER_EXAMPLE = {'requires': ('mds7', 2),
                   'feather': ((((0, 10), (0, 11), (0, 12)), 0.0, 0.45),
                               (((1, 7), (1, 8), (1, 9), (1, 10), (1, 11)),
                                0.45, 1.0))}
# 'px' areas: a pixel is Nitpick's when any channel differs by more than
# PX_DIFF; the mask is grown by PX_GROW pixels and feathered.
PX_DIFF = 40
PX_GROW = 7
# A fix is a sign, a label, a mat: a handful of cells. More than this on one
# page (mds5_5 page 0: 21 cells of crowd where Cosmos has sky; md8_3 page 1:
# 71 cells where Cosmos's page is dark) is Cosmos's own different art, not a
# fix, and the page is left to Cosmos.
MAX_CELLS = 12
GRID = 16


def write_dds(path, rgba):
    """Uncompressed 32-bit DDS (B8G8R8A8), the layout dds_decode reads."""
    h, w = rgba.shape[:2]
    hdr = bytearray(128)
    hdr[0:4] = b'DDS '
    struct.pack_into('<7I', hdr, 4, 124, 0x100F, h, w, w * 4, 0, 1)
    struct.pack_into('<2I4s5I', hdr, 76, 32, 0x41, b'\0\0\0\0', 32,
                     0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)
    struct.pack_into('<I', hdr, 108, 0x1000)
    bgra = rgba[:, :, [2, 1, 0, 3]].astype(np.uint8).tobytes()
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(bytes(hdr) + bgra)
    os.replace(tmp, path)


def patch_cells(nit, clb, area=None):
    """(merged RGBA or None, n cells, verdict) for one page; `area` limits
    the fix to page cells (r0, r1, c0, c1), inclusive."""
    from PIL import Image
    if nit.shape[:2] != clb.shape[:2]:
        nit = np.asarray(Image.fromarray(nit).resize(
            (clb.shape[1], clb.shape[0]), Image.LANCZOS))
    h, w = clb.shape[:2]
    if h % GRID or w % GRID:
        return None, 0, 'odd size'
    ch, cw = h // GRID, w // GRID
    if area is not None and isinstance(area[0], tuple):
        out, total, whys = clb, 0, []
        for sub in area:
            m, n, why = patch_cells(nit, out, sub)
            if m is not None:
                out, total = m, total + n
                whys.append(why)
        if not total:
            return None, 0, 'no fix on this page'
        return out, total, ' + '.join(whys)
    if area is not None and len(area) == 5 and area[4] == 'all':
        r0, r1, c0, c1 = area[:4]
        out = clb.copy()
        out[r0 * ch:(r1 + 1) * ch, c0 * cw:(c1 + 1) * cw] = \
            nit[r0 * ch:(r1 + 1) * ch, c0 * cw:(c1 + 1) * cw]
        n = (r1 - r0 + 1) * (c1 - c0 + 1)
        return out, n, '%d cell(s)' % n
    if area is not None and len(area) == 5:
        return _patch_pixels(nit, clb, area[:4], ch, cw)
    a = nit.astype(np.float32)
    b = clb.astype(np.float32)
    d = (np.abs(a[:, :, :3] * a[:, :, 3:] / 255 - b[:, :, :3] * b[:, :, 3:] / 255).mean(2)
         + np.abs(a[:, :, 3] - b[:, :, 3]) * 0.5)
    cells = d.reshape(GRID, ch, GRID, cw).mean((1, 3))
    med = float(np.median(cells))
    if med > LAYOUT_MEDIAN:
        return None, 0, 'Cosmos lays this page out differently'
    cov_a = (a[:, :, 3] >= 128).reshape(GRID, ch, GRID, cw).mean((1, 3))
    cov_b = (b[:, :, 3] >= 128).reshape(GRID, ch, GRID, cw).mean((1, 3))
    # Inside a named fix area the bar is lower (max(8, 2 x median + 2)): the
    # mat's "me" cell differs by 9 where its border's re-render differs by 5.
    thr = max(CELL_MIN, 3 * med + 4) if area is None else max(8.0, 2 * med + 2)
    sel = (cells > thr) & (cov_a >= 0.5) & (cov_b >= 0.5)
    if area is not None:
        r0, r1, c0, c1 = area
        inside = np.zeros_like(sel)
        inside[r0:r1 + 1, c0:c1 + 1] = True
        sel &= inside
    n = int(sel.sum())
    if not n:
        return None, 0, 'no fix on this page'
    if n > MAX_CELLS:
        return None, 0, '%d cells differ -- Cosmos\'s own art there, kept' % n
    out = clb.copy()
    mask = np.kron(sel, np.ones((ch, cw), bool))
    out[mask] = nit[mask]
    return out, n, '%d cell(s)' % n


def _patch_pixels(nit, clb, area, ch, cw):
    from PIL import Image, ImageFilter
    r0, r1, c0, c1 = area
    a = nit.astype(np.float32)
    b = clb.astype(np.float32)
    diff = np.abs(a[:, :, :3] - b[:, :, :3]).max(2)
    inside = np.zeros(diff.shape, bool)
    inside[r0 * ch:(r1 + 1) * ch, c0 * cw:(c1 + 1) * cw] = True
    hit = (diff > PX_DIFF) & inside
    n = int(hit.sum())
    if not n:
        return None, 0, 'no fix on this page'
    grown = Image.fromarray(hit.astype(np.uint8) * 255).filter(
        ImageFilter.MaxFilter(PX_GROW)).filter(ImageFilter.GaussianBlur(1.5))
    w = np.asarray(grown).astype(np.float32)[:, :, None] / 255
    w *= inside[:, :, None]
    out = (b * (1 - w) + a * w + 0.5).astype(np.uint8)
    return out, n, '%d pixel(s)' % n


def trim_glow(img, spec):
    """Page `img` (RGBA) with the glow rows of `spec` feathered: each cell's
    alpha is scaled by a vertical ramp (top factor -> bottom factor)."""
    out = img.copy()
    h, w = img.shape[:2]
    ch, cw = h // GRID, w // GRID
    for cells, top, bottom in spec['feather']:
        ramp = np.linspace(top, bottom, ch, dtype=np.float32)
        ramp = ramp * ramp * (3 - 2 * ramp)          # smoothstep
        for r, c in cells:
            blk = out[r * ch:(r + 1) * ch, c * cw:(c + 1) * cw, 3].astype(np.float32)
            out[r * ch:(r + 1) * ch, c * cw:(c + 1) * cw, 3] = (
                blk * ramp[:, None] + 0.5).astype(np.uint8)
    return out


def _key(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str)
                          .encode()).hexdigest()[:20]


def build_overlay(nit_path, nit_allowed, clb_path, clb_allowed, cache_root,
                  log=lambda *_: None):
    """A folder of patched pages to stand in for Nitpick's field art."""
    import iro
    import field_bg_repack as R
    import ff7nx_ddstex as DT
    sig = [(p, os.path.getsize(p), int(os.path.getmtime(p)))
           for p in (nit_path, clb_path)]
    key = _key(VERSION, sig, sorted(nit_allowed or []), sorted(clb_allowed or []),
               LAYOUT_MEDIAN, CELL_MIN, MAX_CELLS, sorted(FIX_AREAS.items()),
               sorted(GLOW_TRIMS.items()), PX_DIFF, PX_GROW)
    root = os.path.join(cache_root, key)
    done = os.path.join(root, '.done')
    report = os.path.join(root, 'report.json')
    if os.path.isfile(done):
        with open(report) as f:
            rep = json.load(f)
    else:
        nit_entries = [e if isinstance(e, str) else e[0] for e in iro.list_entries(nit_path)]
        clb_entries = [e if isinstance(e, str) else e[0] for e in iro.list_entries(clb_path)]
        nit = R.resolve(R.index_field_dds(nit_entries, nit_allowed))
        clb = R.resolve(R.index_field_dds(clb_entries, clb_allowed))
        rep = {}
        os.makedirs(root, exist_ok=True)
        for (field, page, pal), entry in sorted(nit.items()):
            name = '%s_%02d_%02d' % (field, page, pal)
            a = DT._decode(iro.read_one(nit_path, entry))[0]
            area = FIX_AREAS.get((field, page))
            if area is None:
                merged, n, why = None, 0, 'no fix on this page'
            elif (field, page, pal) not in clb:
                merged, n, why = None, 0, 'Cosmos has no art for it; left alone'
            else:
                b = DT._decode(iro.read_one(clb_path, clb[(field, page, pal)]))[0]
                merged, n, why = patch_cells(a, b, area)
            rep[name] = why
            if merged is None:
                continue
            d = os.path.join(root, 'field', field)
            os.makedirs(d, exist_ok=True)
            write_dds(os.path.join(d, name + '.dds'), merged)
        for (gf, gp), spec in sorted(GLOW_TRIMS.items()):
            need = '%s_%02d_%02d' % (spec['requires'][0], spec['requires'][1], 0)
            if not rep.get(need, '').endswith('cell(s)'):
                continue
            for (field, page, pal), entry in sorted(clb.items()):
                if (field, page) != (gf, gp):
                    continue
                img = DT._decode(iro.read_one(clb_path, entry))[0]
                d = os.path.join(root, 'field', field)
                os.makedirs(d, exist_ok=True)
                name = '%s_%02d_%02d' % (field, page, pal)
                write_dds(os.path.join(d, name + '.dds'), trim_glow(img, spec))
                rep[name] = 'glow feathered above the new sign'
        with open(report, 'w') as f:
            json.dump(rep, f, indent=1, sort_keys=True)
        open(done, 'w').close()
    fields = sorted({k.rsplit('_', 2)[0] for k in rep})
    for field in fields:
        bits = ['%s: %s' % (k[len(field) + 1:], v) for k, v in sorted(rep.items())
                if k.rsplit('_', 2)[0] == field]
        log('  Nitpick fix %s -- %s' % (field, '; '.join(bits)))
    return root
