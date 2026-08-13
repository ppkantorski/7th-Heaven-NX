#!/usr/bin/env python3
"""
ff7nx_palrange.py -- no tile may name a palette that does not exist.

FINDINGS-158.  Cosmos's section 9 was authored against the PC field, whose
palette table is larger than the Switch port's.  `build.py` splices the mod's
section 9 (SAFE_MOD_SECTIONS) and keeps Switch vanilla's section 3, so 13,481
tiles across 92 fields name a palette index at or past the end of the table.

On FFNx that byte is never read -- it replaces the page with the DDS and never
applies a palette.  The Switch port DOES apply it, and the lookup runs off the
end of the palette array into whatever memory follows.  That is the white
speckle in mds5_3 and the black blobs in mds5_5.

IT IS INVISIBLE OFFLINE.  `field_bg_dense` decodes with `pal % npg` and so
does `render_field`, so every renderer we own draws these cells through a
WRAPPED palette and looks correct.  Only the console reads past the end.  Do
not "verify" this with a render that clamps.

SECOND, LARGER EFFECT: `ff7nx_marginart` skips these cells outright --

    if pal >= npg:  st['no_dds'] += len(cs);  continue

-- so they never receive Cosmos's art either.  Fixing the palette BEFORE the
margin passes run therefore does two things at once: it stops the garbage read
and it lets the upscaled art reach 13,481 tiles that were being passed over.

REPLACEMENT RULE: PER CELL, BY RENDERING IT (build 68).  An earlier version
used palette 0 everywhere and that caused the build 67 WALL MARKET REGRESSION
-- flat tan squares.  A vanilla filler cell is entirely index 0, marginart's
keep-0 rule protects index 0 so the art never lands, and the rendered colour
is then decided purely by entry 0 of the palette we name.  Palette 0's entry 0
in mrkt2 is (224,168,104): the tan.

MEASURED, rendered colour of the out-of-range cells vs Cosmos's art:

    field     pal 0 always            best-by-render
    mrkt2     err 134.8  tan 151/151  err 11.9  tan 6/151
    mrkt1     err  76.8  tan   0/ 96  err 10.3  tan 0/ 96
    mrkt4     err  24.3  tan   0/ 64  err  2.6  tan 0/ 64
    mds5_3    err  41.8  tan   0/296  err 12.7  tan 0/296
    mds5_5    err  39.0  tan   0/245  err  1.3  tan 0/245

The old rule was chosen on a fidelity test that never included Wall Market,
and that test ran AFTER the repack -- where most of these cells had been
promoted to truecolor and were invisible to the comparison.  Measure the
thing that ships, on the fields that break.

SUPERSEDED: PALETTE 0.  That is FFNx's own fallback when the exact
palette's DDS is missing (saveload.cpp:138), and Cosmos ships `_00` for ~87%
of pages -- so palette 0 is the table its art was authored against.

MEASURED against Cosmos's art (`_fidelity.py`), four fields, three candidate
rules:

    field      neighbour-modal   wrap (pal %% npg)   PALETTE 0
    mds5_3            9.92            13.37            9.63
    mds5_5            8.01             7.29            7.29
    nivl_b22         42.59            28.96           28.96
    trnad_3          42.79            42.71           42.71

Palette 0 is best or tied-best in all four.  The neighbour rule -- which is
what this module did first -- is actively WORSE in nivl_b22 (29.07 -> 42.59),
so "keep it in the same colour world as its neighbours" sounded right and
measured wrong.

NOTE the baseline cannot be measured offline: `_fidelity` decodes with
`pal %% npg` like every other tool here, so an unfixed field scores as if it
were wrapped.  Only hardware reads past the end.  Compare rules to each
other, never to "no fix".
"""
from __future__ import annotations
from collections import Counter, defaultdict

import diag_common as DC
import ff7nx_marginblack as MB
import field_bg_native as FN

T_PAL = MB.T_PAL


def palette_rows(sec3):
    """How many palette rows section 3 actually provides."""
    try:
        cols, hdr, npg, cpp = MB.palette_colours(sec3)
        return len(cols) if cols is not None else npg
    except Exception:                                          # noqa: BLE001
        return 0


def _covered(art_for, slot, sx, sy):
    """Does Cosmos actually PAINT this cell? (any texel with alpha > 0)

    BUILD 67 REGRESSION, and this is the whole reason this gate exists.
    Repointing a tile whose cell the mod does not paint hands it to
    ff7nx_marginart, whose keep-0 path then writes index 0 across the whole
    cell (`uncovered` texels keep the key).  Index 0 is DRAWN on a depth-1
    page, through palette 0, whose entry 0 ff7nx_palkey de-fringes to the
    filler colour -- (224,168,104) in Wall Market.  MEASURED: all 151 cells
    repointed in mrkt2 collapsed to ONE index and rendered flat tan.  Before
    the repoint they named an out-of-range palette, marginart skipped them,
    and they kept vanilla content.

    So: only repoint a cell the mod actually paints.  Where it paints nothing
    there is no art to rescue and the repoint can only make it worse.
    """
    if art_for is None:
        return True
    got = None
    for _p in (0,):
        try:
            got = art_for(slot, _p)
        except Exception:                                      # noqa: BLE001
            got = None
        if got is not None:
            break
    img = (got[0] if isinstance(got, tuple) else got) if got is not None else None
    if img is None:
        return False
    tm = getattr(img, 'tmask', None)
    if tm is None:
        return True
    try:
        s = img.px // 256
        blk = tm[sy * s:(sy + 16) * s, sx * s:(sx + 16) * s]
    except Exception:                                          # noqa: BLE001
        return True
    if blk.size == 0:
        return False
    return bool((~blk).any())


def _best_palette(arrays, pal565, art_for, rows, slot, sx, sy):
    """The valid palette that renders this cell closest to Cosmos's art."""
    import numpy as np
    idx = arrays.get(slot)
    if idx is None or art_for is None or not rows:
        return 0
    blk = idx[sy:sy + 16, sx:sx + 16]
    got = None
    try:
        got = art_for(slot, 0)
    except Exception:                                          # noqa: BLE001
        got = None
    img = (got[0] if isinstance(got, tuple) else got) if got is not None else None
    if img is None:
        return 0
    try:
        f = img.px // 256
        page = np.frombuffer(img.buf, '<u2').reshape(img.px, img.px)
        a = page[sy * f:(sy + 16) * f, sx * f:(sx + 16) * f].astype(np.int64)
        tru = np.stack([((a >> 11) & 31) << 3, ((a >> 5) & 63) << 2,
                        (a & 31) << 3], -1).astype(float)
        if f > 1:
            tru = tru.reshape(16, f, 16, f, 3).mean((1, 3))
    except Exception:                                          # noqa: BLE001
        return 0
    best, bestp = None, 0
    for p in range(min(rows, len(pal565))):
        v = pal565[p][blk].astype(np.int64)
        ren = np.stack([((v >> 11) & 31) << 3, ((v >> 5) & 63) << 2,
                        (v & 31) << 3], -1).astype(float)
        e = float(np.abs(ren - tru).mean())
        if best is None or e < best:
            best, bestp = e, p
    return bestp


def fix_field(sec3, sec9, name='', log=None, art_for=None):
    """Return (sec9, stats). Rewrites out-of-range palette bytes in place."""
    st = {'tiles': 0, 'cells': 0, 'pals': Counter(), 'rows': 0}
    rows = palette_rows(sec3)
    st['rows'] = rows
    if rows <= 0:
        return sec9, st
    try:
        surv = DC.survey(sec9)
        pages = {p.slot: p for p in surv['pages']}
        tiles = list(MB.read_tiles(sec9, surv, pages))
    except Exception:                                          # noqa: BLE001
        return sec9, st

    bad = [t for t in tiles
           if pages.get(t.slot) is not None
           and pages[t.slot].depth == 1 and t.pal >= rows]
    # NO COVERAGE GATE. An earlier version skipped cells the mod does not
    # paint, to dodge the tan square -- but that leaves the tile naming a
    # palette that still does not exist, i.e. the console still reads past
    # the array. _best_palette solves the colour properly, so every
    # out-of-range tile is fixed and none are left behind.
    st['skipped_unpainted'] = 0
    if not bad:
        return sec9, st

    try:
        import field_bg_dense as _FD
        pal565, _npg, _cpp = _FD._pal_rgb(sec3)
    except Exception:                                          # noqa: BLE001
        pal565 = None
    arrays = {}
    for sl, pg in pages.items():
        if pg.depth == 1:
            try:
                import numpy as _np
                arrays[sl] = _np.frombuffer(pg.data, _np.uint8).reshape(256, 256)
            except Exception:                                  # noqa: BLE001
                pass
    buf = bytearray(sec9)
    cells = set()
    # CHOOSE PER CELL, BY RENDERING IT. FINDINGS-158 part 3.
    #
    # Palette 0 as a blanket answer caused the build 67 Wall Market
    # regression. A vanilla FILLER cell is entirely index 0, and marginart's
    # keep-0 rule protects index 0, so the art never lands -- the rendered
    # colour is decided ENTIRELY by entry 0 of whichever palette we name.
    # Palette 0's entry 0 in mrkt2 is (224,168,104): the tan square.
    #
    # So score the candidates the way the screen will: render THIS cell's
    # actual indices through each valid palette and take the one closest to
    # Cosmos's art. For an all-index-0 cell that reduces to "the palette
    # whose entry 0 matches the art", which is exactly the question. For a
    # normal cell it is the ordinary best-palette choice.
    choice = {}
    for t in bad:
        key = (t.slot, t.sx, t.sy)
        if key not in choice:
            choice[key] = (0 if pal565 is None else
                           _best_palette(arrays, pal565, art_for, rows,
                                         t.slot, t.sx, t.sy))
    for t in bad:
        new = choice[(t.slot, t.sx, t.sy)]
        st['pals'][t.pal] += 1
        st['tiles'] += 1
        cells.add((t.slot, t.sx, t.sy))
        buf[t.off + T_PAL] = new
    st['cells'] = len(cells)
    if log and st['tiles']:
        log('    %s: %d tile(s) named a palette >= %d' % (name, st['tiles'], rows))
    return bytes(buf), st


def summarise(total_tiles, total_cells, fields, pals):
    if not total_tiles:
        return ('  field background: palette range -- every tile names a palette '
                'that exists. (If this ever prints a non-zero count again, the '
                'console is reading past the end of section 3 -- FINDINGS-158.)')
    top = ', '.join('%d x%d' % (p, n) for p, n in sorted(pals.items())[:6])
    return ('  field background: PALETTE RANGE -- %s tile(s) across %s cell(s) in '
            '%s field(s) named a palette index at or past the end of section 3 '
            'and were repointed to the palette that renders each cell CLOSEST TO COSMOS\'S ART (chosen per cell, not a fixed index -- a fixed palette 0 caused the build 67 Wall Market tan squares). These come '
            'from COSMOS\'s own section 9, which was authored against the PC '
            'field and its larger palette table; FFNx never reads the byte '
            'because it replaces the page with the DDS, but this port applies '
            'it and the lookup runs off the end of the palette array -- the '
            'white speckle in mds5_3 and the black blobs in mds5_5. It is '
            'INVISIBLE to every offline renderer we own because they all decode '
            'with pal %% npg. Offending indices: %s. FINDINGS-158. '
            'SECOND EFFECT: ff7nx_marginart skipped these cells entirely '
            '("pal >= npg -> no_dds"), so they never received Cosmos\'s art '
            'either -- fixing the byte before the margin passes lets the '
            'upscaled art reach them.'
            % (f'{total_tiles:,}', f'{total_cells:,}', f'{fields:,}', top))


def apply_to_flevel(archive, payloads, encode=None, log=print, fields=None,
                    art=None):
    """
    Same contract as ff7nx_marginart.apply_to_flevel: a field already in
    `payloads` is taken from there, so this composes with the mod replacement
    passes instead of competing with them.

    MUST RUN BEFORE ff7nx_marginart. marginart skips any cell whose palette
    byte is >= npg ("no_dds"), so fixing the byte first is what lets Cosmos's
    art reach those cells at all.
    """
    import lgp

    st = {'read': 0, 'changed': 0, 'tiles': 0, 'cells': 0, 'fields': 0,
          'unpainted': 0, 'pals': Counter(), 'refused': []}
    encode = encode or (lambda raw: archive.encode_field(raw))
    for name in archive.names():
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        if fields and name not in fields:
            continue
        payload = payloads.get(name, entry.get('payload'))
        if not payload:
            continue
        try:
            raw = (lgp.lzs_decompress(payload[4:]) if name in payloads
                   else archive.decompressed(entry))
            secs = lgp.split_sections(raw)
            _af = art.open(name) if art is not None else None
            new9, s = fix_field(secs[3], secs[8], name, art_for=_af)
            st['read'] += 1
            st['unpainted'] += s.get('skipped_unpainted', 0)
            if not s['tiles']:
                continue
            secs[8] = new9
            payloads[name] = encode(lgp.join_sections(secs))
            st['changed'] += 1
            st['fields'] += 1
            st['tiles'] += s['tiles']
            st['cells'] += s['cells']
            st['pals'].update(s['pals'])
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, '%s: %s'
                                  % (type(exc).__name__, str(exc)[:60])))
    return st
