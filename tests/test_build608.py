"""BUILD 608: field background fixes.

1. ff7nx_stackorder -- tiles stacked on one spot at one depth keep the mod's
   winner (zz2's chest).
2. ff7nx_fxpages / ff7nx_fxsplit -- a script-animated palette keeps its page
   paletted only where the mod animates it too (bugin1a's show goes HD,
   ghotel's fog stays animated).

The real-data tests run where the mod, the dump and a built SD tree are
present, and skip quietly elsewhere, like tests/test_fxsplit.py.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'tests'))
PROJ = os.path.expanduser('~/mnt/7th_heaven_nx')
if PROJ not in sys.path:
    sys.path.append(PROJ)

import lgp  # noqa: E402
import ff7nx_stackorder as SO  # noqa: E402
import ff7nx_fxpages as XP  # noqa: E402
import ff7nx_fxsplit as XS  # noqa: E402
import ff7nx_palanim as PA  # noqa: E402

BUILT = os.path.join(PROJ, 'sdout', 'atmosphere', 'contents',
                     '0100A5B00BDC6000', 'romfs', 'ff7', 'workingdir', 'data',
                     'field', 'flevel.lgp')
CHUNKS = os.path.join(PROJ, 'cache', 'CosmosLimitBreak', 'LIMIT BREAK',
                      'flevel.lgp')


# ------------------------------------------------------------ the draw rule
def test_winner_rule():
    # (param, state, page, z) in file order
    same_page = [(2, 2, 26, 100), (0, 0, 26, 100)]
    assert SO.winners(same_page) == {(0, 1): True}        # earlier wins
    split = [(2, 2, 26, 100), (0, 0, 27, 100)]
    assert SO.winners(split) == {(0, 1): False}           # higher page wins
    nearer = [(2, 2, 26, 98), (0, 0, 27, 100)]
    assert SO.winners(nearer) == {(0, 1): True}           # depth first
    states = [(5, 1, 26, 100), (5, 2, 27, 100)]
    assert SO.winners(states) == {}                       # never together


def test_zz2_chest_split_is_the_reported_rectangle():
    """The rule, checked against the hardware report: in the 606c archive
    the three flipped chest pairs are x=24 rows 120/136 (a tall rectangle
    in the lid) and x=56 row 152."""
    if not (os.path.exists(BUILT) and os.path.exists(CHUNKS)):
        return
    ref = SO.signature(open(os.path.join(CHUNKS, 'zz2.chunk.9'), 'rb').read())
    a = lgp.Archive(BUILT)
    s9 = lgp.split_sections(a.decompressed(a.index['zz2']))[8]
    tiles, *_ = SO._walk(s9)
    here = SO._by_spot(s9, tiles)
    bad = sorted(k[1:] for k, r in ref.items() if here.get(k)
                 and len(here[k]) == len(r)
                 and SO.flipped_pairs(SO._members(s9, here[k]), r))
    if not bad:
        return                         # an archive already built with 608
    assert bad == [(24, 120), (24, 136), (56, 152)]


def _pixels(s9):
    """Per tile: the exact texels it samples (base and fx) and its record
    minus the bytes the pass may change."""
    tiles, plist, _ts, _te, _px = SO._walk(s9)
    pages = {p.slot: p for p in plist if p is not None}
    out = []
    keep = [i for i in range(52)
            if i not in (8, 9, 10, 11, 32, 33, 38, 39, 40, 41,
                         42, 43, 44, 45, 46, 47, 48, 49)]
    for _layer, o in tiles:
        rec = []
        for sl in (s9[o + SO.T_TEXID], s9[o + SO.T_FX]):
            p = pages.get(sl)
            if p is None or (sl == s9[o + SO.T_FX] and not sl):
                rec.append(None)
                continue
            cx, cy = SO._cell(s9, o, p)
            grid = 8 if p.size_flag else 16
            side = p.px if p.depth == 2 else 256
            step = side // grid
            bpp = 2 if p.depth == 2 else 1
            rec.append((SO._kind(p), b''.join(
                p.data[((cy * step + r) * side + cx * step) * bpp:
                       ((cy * step + r) * side + cx * step + step) * bpp]
                for r in range(step))))
        out.append((rec, bytes(s9[o + i] for i in keep)))
    return out, sum(1 for p in plist if p is not None)


def test_fix_changes_order_not_pictures():
    """zz2 and a spread of other fields: after the fix no stack is flipped
    that the pass could fix, every tile samples byte-identical texels (base
    and fx), keeps its blend band, and no page is added."""
    if not (os.path.exists(BUILT) and os.path.exists(CHUNKS)):
        return
    a = lgp.Archive(BUILT)
    for name in ('zz2', 'delmin1', 'ghotel', 'hill', 'gidun_3'):
        s9 = lgp.split_sections(a.decompressed(a.index[name]))[8]
        c = os.path.join(CHUNKS, name + '.chunk.9')
        if not os.path.exists(c):
            continue
        ref = SO.signature(open(c, 'rb').read())
        new9, st = SO.fix_section9(s9, ref, name)
        left = sum(v for k, v in st.items() if k.startswith('left_'))
        assert SO.remaining(new9, ref)[0] <= left, name
        before, np0 = _pixels(s9)
        after, np1 = _pixels(new9)
        assert np0 == np1 and len(before) == len(after), name
        assert before == after, name
        # idempotent: nothing left to do on the output
        again, st2 = SO.fix_section9(new9, ref, name)
        assert not st2.get('fields'), name


def test_stackorder_switch():
    os.environ[SO.OFF_ENV] = '1'
    try:
        assert SO.disabled()
        assert SO.apply_to_flevel(None, {}, {'x': {}}, None) == (
            __import__('collections').Counter(), [])
    finally:
        os.environ.pop(SO.OFF_ENV, None)


# ---------------------------------------------------- palette animation rule
def _art():
    from test_fxsplit import _art as art
    return art()


def _have():
    from test_fxsplit import _have as have
    return have()


def _field(name):
    from test_fxsplit import _field as field
    return field(name)


def test_cosmos_animates():
    if not _have():
        return
    p = _art().provider
    # bugin1a: palettes 1/2 are MPPAL-dimmed by the script, the mod has one
    # static picture per page
    assert not XP.cosmos_animates(p, 'bugin1a', 15, 1)
    assert not XP.cosmos_animates(p, 'bugin1a', 16, 2)
    # ghotel's fog and sinbil_1's glow: state frames in AA REMOVED, a folder
    # the build does NOT enable -- still animated
    assert XP.cosmos_animates(p, 'ghotel', 15, 8)
    assert XP.cosmos_animates(p, 'ghotel', 15, 11)
    assert XP.cosmos_animates(p, 'sinbil_1', 15, 6)


def test_bugin1a_show_pages_go_truecolor_ghotel_fog_does_not():
    if not _have():
        return
    old = os.environ.pop(XP.ANIM_ALWAYS_ENV, None)
    try:
        parts = lgp.split_sections(_field('bugin1a'))
        anim = PA.animated_palettes(parts[0])
        assert set(anim) >= {1, 2}
        import field_bg_native as FN
        s9, _k = FN.resize_section9(parts[8], 768)
        _new, st = XP.upgrade_section9('bugin1a', s9, _art(), 768,
                                       animated=anim)
        assert st['page_names'] == ['bugin1a:15', 'bugin1a:16']
        assert st['tiles'] == 412

        parts = lgp.split_sections(_field('ghotel'))
        anim = PA.animated_palettes(parts[0])
        _new, st = XP.upgrade_section9('ghotel', parts[8], _art(), 768,
                                       animated=anim)
        assert st['anim_veto'] >= 1 and 'ghotel' in st['anim_names']
    finally:
        if old is not None:
            os.environ[XP.ANIM_ALWAYS_ENV] = old


def test_anim_always_restores_build581():
    if not _have():
        return
    os.environ[XP.ANIM_ALWAYS_ENV] = '1'
    try:
        parts = lgp.split_sections(_field('bugin1a'))
        anim = PA.animated_palettes(parts[0])
        import field_bg_native as FN
        s9, _k = FN.resize_section9(parts[8], 768)
        _new, st = XP.upgrade_section9('bugin1a', s9, _art(), 768,
                                       animated=anim)
        assert not st.get('pages') and st['anim_veto'] == 2
    finally:
        os.environ.pop(XP.ANIM_ALWAYS_ENV, None)


def test_fxsplit_never_worse_than_build581():
    """Fields where the split changes: the new rule converts at least as
    many tile references as the BUILD 581 rule did, page by page summed."""
    if not _have():
        return
    import field_bg_native as FN
    for name in ('icedun_2', 'junpb_2', 'lastmap', 'loslake1', 'ghotin_1'):
        raw = _field(name)
        if raw is None:
            continue
        parts = lgp.split_sections(raw)
        anim = PA.animated_palettes(parts[0])
        s9, _k = FN.resize_section9(parts[8], 768)
        got = {}
        for mode in ('1', ''):
            if mode:
                os.environ[XP.ANIM_ALWAYS_ENV] = mode
            else:
                os.environ.pop(XP.ANIM_ALWAYS_ENV, None)
            a, st = XP.upgrade_section9(name, s9, _art(), 768, animated=anim)
            _b, st2 = XS.split_section9(name, a, _art(), 768, animated=anim)
            got[mode] = st.get('tiles', 0) + st2.get('tiles', 0)
        os.environ.pop(XP.ANIM_ALWAYS_ENV, None)
        assert got[''] >= got['1'], (name, got)


# ------------------------------------------------------------ BUILD 608b
def test_band_keeps_the_additive_fx_band_apart():
    """608 moved opaque stacks onto a converted depth-2 FX page (fship_3's
    page 15) because `_band_of` calls depth-2 0x0F..0x17 opaque. The port
    draws those pages additively (FINDINGS-194 ladder)."""
    opaque = SO._band(26, 2)
    assert SO._band(15, 2) != opaque
    assert SO._band(0x17, 2) == SO._band(15, 2)
    assert SO._band(0x18, 2) not in (opaque, SO._band(15, 2))
    assert SO._band(0x19, 2) != SO._band(0x18, 2)
    assert SO._band(3, 2) == opaque            # low truecolor slots: opaque
    assert SO._band(15, 1) == SO._band(16, 1)  # paletted bands unchanged


def test_built_archive_has_no_base_tile_on_a_truecolor_fx_page():
    """Post-build gate. A tile's BASE page is never a depth-2 page in
    0x0F..0x19: ff7nx_fxpages/fxsplit convert FX-only pages and repoint only
    the fx byte, and stackorder may not move a cell into that band. The 608
    archive fails this (362 tiles in 14 fields: loslake2 216, games 50,
    tin_4 37, delmin2 13, ... fship_3 6); a 608b build must pass it."""
    if not os.path.exists(BUILT):
        return
    import diag_common as DC
    a = lgp.Archive(BUILT)
    bad = {}
    for name in a.names():
        e = a.index[name]
        if not a.is_field(e):
            continue
        try:
            s9 = lgp.split_sections(a.decompressed(e))[8]
            pages, ts, _te, _px = DC.parse_pages(s9)
        except Exception:                                      # noqa: BLE001
            continue
        d2 = {p.slot for p in pages
              if p is not None and p.depth == 2 and 0x0F <= p.slot < 0x1A}
        if not d2:
            continue
        for _layer, offs in DC.walk_layers(s9, s9.find(b'BACK'), ts):
            for o in offs:
                if s9[o + SO.T_TEXID] in d2:
                    bad[name] = bad.get(name, 0) + 1
    assert not bad, bad
