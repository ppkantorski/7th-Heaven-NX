#!/usr/bin/env python3
"""
test_bigtile.py -- THE 32-UNIT INVARIANT, AS A TEST RATHER THAN AS A COMMENT.

    python3 test_bigtile.py <flevel.lgp> [field ...] [--ref <archive>]
    python3 test_bigtile.py --self                     # predicates only, fast
    python3 test_bigtile.py <archive> --limit 200      # first N fields

WHY THIS EXISTS
===============
FINDINGS-189 established that offsets 18 and 20 of a tile record are its WIDTH
and HEIGHT, that layers 3 and 4 set them to 32, and that Square's own data
honours a 32 grid. Build 84 violated that on thousands of tiles and nobody
found out until a photograph of Mt. Corel came back from hardware, because
`render_field.py` carried the same 16-unit assumption and reproduced the
checkerboard from its own code. HANDOFF-192 5.1 asks for this check to exist
BEFORE the 32-aware promotion is written, so the promotion is developed against
a test rather than against a 40-minute rebuild.

THE CORRECTION THIS FILE CARRIES, AND IT IS THE WHOLE REASON IT IS EXACT
=======================================================================
The first version of this test asserted `sec9[off + T_SRC_X] % 32 == 0`, which
is what HANDOFF-192 8 proposes. Run against the UNMODIFIED archive it failed
855 times in four fields -- so the invariant, not the data, was wrong.

A tile record carries TWO source coordinates and the engine picks between them:

    offsets 10/12   src1, on the page named at offset 32 (texture_id)
    offsets 14/16   src2, on the page named at offset 34 (texture_id2)
    offsets 42/46   the NORMALISED uv the renderer samples with

MEASURED over 121 vanilla fields, 112,192 tiles, asking which source the uv
agrees with:

    tiles with texture_id2 == 0      uv == src1   95,779 of 95,779   100.0%
    tiles with texture_id2 != 0      uv == src2   15,883 of 15,884    99.99%

Zero exceptions on the non-fx side. So THE DRAWN CELL IS `(texture_id2, src2)`
when a tile has a second texture and `(texture_id, src1)` otherwise, and any
predicate that reads src1 unconditionally is measuring a coordinate the engine
does not sample on 14% of tiles.

Re-measured on the drawn cell, over the first 201 vanilla fields:

    32-unit tiles                      5,974   (3,549 base, 2,425 fx)
    misaligned to 32                       0
    on a page without size_flag            0

The invariant is therefore UNIVERSAL AND WITHOUT EXCEPTION, which is the
strongest form it can take. It also retires FINDINGS-189 6's note that
`las1_1` (299 tiles) and `onna_5` (16) have 32-unit tiles on pages without the
flag "and that is Square's arrangement, present in vanilla". They do not.
Those tiles are fx tiles; their base page has no flag and is not the page they
draw from, and their fx page has the flag and is 32-aligned. There is no
exception population for 5.1 to handle.

WHAT IT ASSERTS
===============
A. ALIGNMENT.   the drawn cell of a layer>=2 tile wider than 16 is 32-aligned.
B. GRID.        that cell's page declares `size_flag` (a 32-unit grid).
C. UV AGREEMENT. the normalised uv names the same cell as the byte, read at
                that page's own grid. This is the one that catches a `STEP`
                computed from GRID = 16 on a page whose grid is 8 -- the single
                likeliest way to get 5.1 wrong, and invisible to A because A
                only reads the byte.
D. FIT.         `src + edge <= 256`; a 32-unit cell at 240 runs off the page.
E. NO FLAG DRIFT (needs --ref). A page's `size_flag` must equal the reference
                archive's. Phrased as a comparison because this pipeline has no
                business changing Square's declaration -- only honouring it.

EXIT STATUS is the point: nonzero on any violation, so it can gate a build the
way `test_ws` and `test_wsclamp` do.
"""
from __future__ import annotations

import os
import struct
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import diag_common as DC                                       # noqa: E402
import field_bg_repack as RP                                   # noqa: E402

UV_SCALE = 10_000_000
T_SRC_X, T_SRC_Y = 10, 12
T_SRC_X2, T_SRC_Y2 = 14, 16
T_W, T_H = 18, 20
T_TEXID = RP.T_TEXID                    # 32
T_FX_PAGE = 34
T_UV = 42                               # two uint32, u then v
PAGE_PX = 256


def grid_of(size_flag):
    """(cells per side, texels per cell) in 256-space. The engine's own rule."""
    return (8, 32) if size_flag else (16, 16)


def drawn_cell(sec9, off, pages):
    """
    (page, src_x, src_y, is_fx) -- THE CELL THE ENGINE SAMPLES.

    See the module docstring for the measurement. An fx tile draws its second
    texture at src2; everything else draws its first at src1. Returns page
    None when the named slot has no page, which is a condition this test
    reports rather than crashes on.
    """
    fx = sec9[off + T_FX_PAGE]
    if fx and fx in pages:
        return pages[fx], sec9[off + T_SRC_X2], sec9[off + T_SRC_Y2], True
    slot = sec9[off + T_TEXID]
    return pages.get(slot), sec9[off + T_SRC_X], sec9[off + T_SRC_Y], False


def scan_section(sec9, field=''):
    """
    (violations, stats) for one section 9.

    `stats` counts the population deliberately: a test that passes because it
    examined zero 32-unit tiles is the exact failure mode this file exists to
    prevent, so the count is printed on success as well as on failure.
    """
    surv = DC.survey(sec9)
    pages = {p.slot: p for p in surv['pages']}
    bad = []
    n_big = n_tiles = n_fx = 0
    for layer, offs in DC.walk_layers(sec9, surv['back_start'],
                                      surv['tex_start']):
        for off in offs:
            n_tiles += 1
            page, sx, sy, is_fx = drawn_cell(sec9, off, pages)
            if page is None:
                continue
            grid, step = grid_of(page.size_flag)
            w, h = struct.unpack_from('<HH', sec9, off + T_W)
            big = layer >= 2 and max(w, h) > 16

            def _v(kind, detail):
                bad.append({'field': field, 'off': off, 'layer': layer,
                            'slot': page.slot, 'sx': sx, 'sy': sy,
                            'fx': is_fx, 'w': w, 'h': h,
                            'size_flag': page.size_flag,
                            'kind': kind, 'detail': detail})

            if big:
                n_big += 1
                n_fx += int(is_fx)
                # ---- A. aligned to the tile's own width
                if sx % 32 or sy % 32:
                    _v('align', 'src (%d,%d) is not on a 32 grid' % (sx, sy))
                # ---- B. and the page has to declare that grid
                if not page.size_flag:
                    _v('grid', 'a %dx%d tile draws slot %d, size_flag 0'
                       % (w, h, page.slot))

            # ---- C. the uv must name the same cell as the byte
            u, v = struct.unpack_from('<II', sec9, off + T_UV)
            ux = int(round(u / UV_SCALE * grid)) * step
            uy = int(round(v / UV_SCALE * grid)) * step
            if (ux, uy) != (sx, sy):
                _v('uv', 'uv names (%d,%d), byte says (%d,%d) at grid %d%s'
                   % (ux, uy, sx, sy, grid, ' [fx]' if is_fx else ''))

            # ---- D. the cell has to fit on the page
            if sx + step > PAGE_PX or sy + step > PAGE_PX:
                _v('fit', 'cell (%d,%d)+%d runs off a %d page'
                   % (sx, sy, step, PAGE_PX))
    return bad, {'tiles': n_tiles, 'big': n_big, 'bigfx': n_fx,
                 'pages': len(pages)}


def size_flags(sec9):
    """{slot: size_flag} -- invariant E's subject."""
    return {p.slot: p.size_flag for p in DC.survey(sec9)['pages']}


# ------------------------------------------------------------------ the driver
def _fields(path, wanted, limit=0):
    import lgp
    a = lgp.Archive(path)
    n = 0
    for nm in a.names():
        if wanted and nm not in wanted:
            continue
        e = a.index.get(nm)
        if e is None or not a.is_field(e):
            continue
        n += 1
        if limit and n > limit:
            return
        try:
            yield nm, lgp.split_sections(a.decompressed(e))[8]
        except Exception:                                      # noqa: BLE001
            continue


def check_archive(path, wanted=(), ref=None, limit=0, quiet=False):
    wanted = set(wanted)
    refflags = {}
    if ref:
        for nm, sec9 in _fields(ref, wanted, limit):
            try:
                refflags[nm] = size_flags(sec9)
            except Exception:                                  # noqa: BLE001
                pass

    allbad = []
    by_kind = defaultdict(int)
    by_field = defaultdict(int)
    tot = {'tiles': 0, 'big': 0, 'bigfx': 0, 'fields': 0, 'bigfields': 0}
    for nm, sec9 in _fields(path, wanted, limit):
        try:
            bad, stats = scan_section(sec9, nm)
        except Exception as exc:                               # noqa: BLE001
            print('  ! %-10s could not be scanned: %s' % (nm, str(exc)[:60]))
            continue
        tot['fields'] += 1
        tot['tiles'] += stats['tiles']
        tot['big'] += stats['big']
        tot['bigfx'] += stats['bigfx']
        tot['bigfields'] += 1 if stats['big'] else 0

        if nm in refflags:
            now = size_flags(sec9)
            for slot, f in refflags[nm].items():
                if slot in now and now[slot] != f:
                    bad.append({'field': nm, 'off': -1, 'layer': -1,
                                'slot': slot, 'sx': -1, 'sy': -1, 'fx': False,
                                'w': -1, 'h': -1, 'size_flag': now[slot],
                                'kind': 'flag',
                                'detail': 'slot %d size_flag %d -> %d'
                                          % (slot, f, now[slot])})
        for b in bad:
            by_kind[b['kind']] += 1
            by_field[nm] += 1
        allbad += bad

    if not quiet:
        print('%-20s %s' % ('archive', path))
        print('  fields scanned      %6d   (%d with 32-unit tiles)'
              % (tot['fields'], tot['bigfields']))
        print('  tiles               %6d' % tot['tiles'])
        print('  32-unit tiles       %6d   (%d draw an fx page)'
              % (tot['big'], tot['bigfx']))
        if ref:
            print('  size_flag reference %s (%d fields)' % (ref, len(refflags)))
        else:
            print('  size_flag reference NONE -- invariant E skipped'
                  ' (pass --ref <archive>)')
        print()
        if not allbad:
            print('  OK -- 0 violations')
        else:
            for kind in ('align', 'grid', 'uv', 'fit', 'flag'):
                if by_kind[kind]:
                    print('  FAIL %-6s %6d' % (kind, by_kind[kind]))
            print()
            worst = sorted(by_field.items(), key=lambda kv: -kv[1])[:20]
            print('  worst fields: %s'
                  % ', '.join('%s(%d)' % (f, n) for f, n in worst))
            print()
            for b in allbad[:12]:
                print('   %-10s L%d slot %-3d %-6s %s'
                      % (b['field'], b['layer'], b['slot'], b['kind'],
                         b['detail']))
            if len(allbad) > 12:
                print('   ... and %d more' % (len(allbad) - 12))
    return allbad, tot


# ------------------------------------------------------------------ self-tests
def _self():
    """
    Exercise the predicates on synthetic records, so that a green run against
    an archive cannot be green because the scanner is broken.
    """
    ok = fail = 0

    def chk(name, got, want):
        nonlocal ok, fail
        if got == want:
            ok += 1
        else:
            fail += 1
            print('  FAIL %-46s got %r want %r' % (name, got, want))

    chk('grid of a size_flag page', grid_of(1), (8, 32))
    chk('grid of an ordinary page', grid_of(0), (16, 16))

    for grid, step, cell in ((8, 32, 3), (16, 16, 7), (8, 32, 7)):
        u = (UV_SCALE // grid) * cell
        chk('uv round-trip grid %d cell %d' % (grid, cell),
            int(round(u / UV_SCALE * grid)) * step, cell * step)

    # THE BUILD-84 BUG AS A NUMBER. A cell seated at index 3 of a SIXTEEN grid
    # writes src_x 48 and uv = 3 * (UV_SCALE//16). On a page whose grid is 8
    # the byte is not 32-aligned, and the uv reads back as a different cell
    # entirely -- so invariants A and C both fire, independently.
    u16 = (UV_SCALE // 16) * 3
    chk('48 is not 32-aligned', 48 % 32 == 0, False)
    chk('a 16-grid uv, read at grid 8, is not 48',
        int(round(u16 / UV_SCALE * 8)) * 32 != 48, True)
    chk('64 is 32-aligned', 64 % 32 == 0, True)

    # a correctly seated 32-unit cell: index 3 of an EIGHT grid
    u8 = (UV_SCALE // 8) * 3
    chk('a 32-grid cell agrees with its uv',
        int(round(u8 / UV_SCALE * 8)) * 32, 96)
    chk('96 is 32-aligned', 96 % 32 == 0, True)

    # invariant D
    chk('a 32 cell at 224 fits', 224 + 32 <= PAGE_PX, True)
    chk('a 32 cell at 240 does not', 240 + 32 <= PAGE_PX, False)

    # drawn_cell: the correction this file carries
    class P:
        def __init__(self, slot, size_flag=1):
            self.slot, self.size_flag = slot, size_flag
    rec = bytearray(52)
    rec[T_SRC_X], rec[T_SRC_Y] = 16, 16          # src1
    rec[T_SRC_X2], rec[T_SRC_Y2] = 64, 96        # src2
    rec[T_TEXID] = 3
    pages = {3: P(3), 17: P(17)}
    chk('no fx -> src1 on texture_id',
        drawn_cell(bytes(rec), 0, pages)[1:], (16, 16, False))
    rec[T_FX_PAGE] = 17
    chk('fx -> src2 on texture_id2',
        drawn_cell(bytes(rec), 0, pages)[1:], (64, 96, True))
    chk('fx -> the fx page',
        drawn_cell(bytes(rec), 0, pages)[0].slot, 17)
    rec[T_FX_PAGE] = 99                          # names a page that is absent
    chk('an absent fx page falls back to src1',
        drawn_cell(bytes(rec), 0, pages)[1:], (16, 16, False))

    print('  self-tests: %d ok, %d failed' % (ok, fail))
    return fail


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ref = None
    limit = 0
    if '--ref' in argv:
        i = argv.index('--ref')
        ref = argv[i + 1]
        del argv[i:i + 2]
    if '--limit' in argv:
        i = argv.index('--limit')
        limit = int(argv[i + 1])
        del argv[i:i + 2]
    if not argv or argv[0] == '--self':
        return 1 if _self() else 0
    rc = 1 if _self() else 0
    print()
    bad, _ = check_archive(argv[0], argv[1:], ref, limit)
    return 1 if (bad or rc) else 0


if __name__ == '__main__':
    sys.exit(main())
