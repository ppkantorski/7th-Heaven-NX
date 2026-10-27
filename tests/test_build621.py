"""BUILD 621: Enhanced Stock UI's main menu (ff7nx_esui) and the calendar's
ESUI profile.

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
import os
import struct
import sys

import pytest

HERE = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, HERE)

import ff7nx_esui as E          # noqa: E402
import ff7nx_calendar as C      # noqa: E402

DUMP_MAIN = os.path.join(HERE, 'dump', 'exefs', 'main')
DUMP_EXE = os.path.join(HERE, 'dump', 'romfs', 'ff7', 'resources', 'ff7_1.02',
                        'ff7_en')
WINDOW = os.path.join(HERE, 'dump', 'romfs', 'ff7', 'workingdir', 'data',
                      'lang-en', 'kernel', 'window.bin')
need_dump = pytest.mark.skipif(not os.path.isfile(DUMP_MAIN),
                               reason='needs the game dump')


@pytest.fixture
def esui_on(monkeypatch):
    monkeypatch.setenv(E.ENV, 'main')


def test_switch(monkeypatch):
    monkeypatch.setattr(E, 'CURRENT', None)
    monkeypatch.delenv(E.ENV, raising=False)
    assert E.screens() == () and not E.enabled()
    assert E.module_words() == {}
    monkeypatch.setenv(E.ENV, 'main')
    assert E.screens() == ('main',)
    monkeypatch.setenv(E.ENV, 'main,field')
    assert E.screens() == ('main',) and E.unknown_screens() == ('field',)
    monkeypatch.setenv(E.ENV, '0')
    assert not E.enabled()


def test_code_table_shape(esui_on):
    addrs = [r[0] for r in E.CODE_MAIN]
    assert len(addrs) == len(set(addrs)) == 109
    assert {r[0] for r in E.CODE_FULLMENU} >= {0xCA6684, 0xCA6644, 0xCA5BC4}
    assert all(a % 4 == 0 for a in addrs)
    assert all(old != new for _a, old, new, _f, _n in E.CODE_MAIN)
    # every function is one of the ten 00-Main touches; nothing from 01-*
    fns = {r[3] for r in E.CODE_MAIN}
    assert fns == {0x6C62A2, 0x6C98A6, 0x6CA1EF, 0x6CA23F, 0x6CA2A9,
                   0x6CA346, 0x6F62F7, 0x6F9739, 0x70212A, 0x703176}
    assert 0x947D64 not in addrs        # 01-Base_1's opening cue, left out


@need_dump
def test_expect_words_are_the_dump(esui_on):
    import nxmap
    m = nxmap.Main(DUMP_MAIN)
    for va, old, _new, fn, note in E.CODE_MAIN:
        assert struct.unpack_from('<I', m.img, va)[0] == old, (hex(va), note)
    for va, old, _new, note in E.RAW_MAIN:
        assert struct.unpack_from('<I', m.img, va)[0] == old, note


@need_dump
def test_new_words_decode():
    capstone = pytest.importorskip('capstone')
    cs = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    for va, old, new, _f, note in E.CODE_MAIN:
        i = next(cs.disasm(struct.pack('<I', new), va), None)
        assert i is not None and i.mnemonic != 'udf', (hex(va), note)
        o = next(cs.disasm(struct.pack('<I', old), va))
        # an immediate edit keeps the destination register
        if note in ('OK', 'GROUP') and o.mnemonic in ('add', 'sub', 'mov', 'movz'):
            assert i.op_str.split(',')[0] == o.op_str.split(',')[0], hex(va)


def test_language_table_is_english_only(esui_on):
    (va, old, new, _n), = E.RAW_MAIN
    assert (va, old, new) == (0x11B0FB0, 0x1A4, 0x1B0)


@need_dump
def test_module_applies_and_reruns(esui_on, tmp_path):
    out = tmp_path / 'main'
    assert E.apply_to_nso(DUMP_MAIN, str(out))
    import nxmap
    m = nxmap.Main(str(out))
    for va, (old, new, _l) in E.module_words(m.text).items():
        assert struct.unpack_from('<I', m.img, va)[0] == new, hex(va)
    again = tmp_path / 'main2'
    assert E.apply_to_nso(str(out), str(again))


@need_dump
def test_exe_data(esui_on):
    import exe_patch
    d = open(DUMP_EXE, 'rb').read()
    pe = exe_patch.parse_pe(d)
    off = lambda va: exe_patch.va_to_offset(pe, va)     # noqa: E731
    new, n = E.patch_exe(d, off)
    assert n == 10
    diff = [i for i, (a, b) in enumerate(zip(d, new)) if a != b]
    assert len(diff) == 15
    assert new[off(0x7B7CF8):off(0x7B7CF8) + 4] == struct.pack('<f', 2.0)
    for va in (0x919DA5, 0x919DC5, 0x919DE6, 0x919E06, 0x919E26, 0x919E46,
               0x919E63, 0x919E84):
        o = off(va)
        assert new[o - 1:o + 4] == b'_l.ti'
    # the calendar's two parking spots stay free
    for g in (C.LABEL_AT, C.MONTH_AT):
        assert new[off(g)] == 0
    again, n2 = E.patch_exe(new, off)
    assert again == new and n2 == 0


@need_dump
def test_window_bin_roundtrip():
    d = open(WINDOW, 'rb').read()
    new, changed = E.patch_window_bin(d)
    assert changed
    secs, _t = E._sections(new)
    assert secs[2][1][:256] == E.WIDTHS
    assert E._sections(d)[0][2][1][256:] == secs[2][1][256:]
    assert [s[1] for s in E._sections(d)[0][:2]] == [s[1] for s in secs[:2]]
    assert E.patch_window_bin(new) == (new, False)


def test_calendar_profile(monkeypatch):
    monkeypatch.delenv(E.ENV, raising=False)
    assert C.constants() == C.CONSTANTS
    assert C.layout_from_env() == (C.MONTH_X, C.SEPARATOR_X, C.DAY_X)
    monkeypatch.setenv(E.ENV, 'main')
    monkeypatch.delenv(C.LAYOUT_ENV, raising=False)
    got = {site: new for site, _w, new, _what in C.constants()}
    assert set(got) == {s for s, _w, _n, _what in C.CONSTANTS}
    assert got[0xD02830] == C._movz_imm(0x52802B88, 0x155)     # box Y
    assert got[0xD02ED4] == C._movz_imm(0x52800908, 0x5A)      # height
    assert got[0xD02908] == C._add_imm(0x11010916, 0x4B)       # hours X (623f: 2 left, 630: 1 more)
    assert C.layout_from_env() == (0x4F, 0x6A, 0x75)


@need_dump
def test_calendar_then_esui(esui_on, tmp_path):
    """The real order: the calendar (ESUI profile), then ESUI, which must
    leave every calendar word alone and still apply the rest."""
    pytest.importorskip('ff7nx_tables')
    cal = tmp_path / 'main.cal'
    assert C.apply_to_nso(DUMP_MAIN, str(cal), stock=DUMP_MAIN)
    both = tmp_path / 'main.both'
    assert E.apply_to_nso(str(cal), str(both))
    import nxmap
    a = nxmap.Main(str(cal))
    b = nxmap.Main(str(both))
    owned = C.owned_sites()
    for va in owned:
        assert struct.unpack_from('<I', a.img, va)[0] == \
            struct.unpack_from('<I', b.img, va)[0]
    words = E.module_words(a.text)
    s = E._sets()
    allw = {r[0] for r in s['code']} | {r[0] for r in s['raw']}
    assert len(words) == len(allw - owned)
    for va, (_old, new, _l) in words.items():
        assert struct.unpack_from('<I', b.img, va)[0] == new
    # the ESUI time box: Y 0x155, height 0x5A
    assert struct.unpack_from('<I', b.img, 0xD02830)[0] == \
        C._movz_imm(0x52802B88, 0x155)


def test_build_hook_order():
    gui = open(os.path.join(HERE, '7th_heaven_nx.py')).read()
    assert gui.index('build.apply_esui(') > gui.index('build.apply_calendar(')


MOD = os.path.join(HERE, 'mods', 'Enhanced Stock UI-rel2.iro')
need_mod = pytest.mark.skipif(not os.path.isfile(MOD), reason='needs the ESUI .iro')

SETTINGS = {"Main": 0, "enhanced": 1, "start": 1, "items_materia": 3, "icons": 2,
            "cursor": 2, "bars": 2, "fonts": 1, "avatars": 1, "opacity": 0,
            "nt": 0, "menuwidth": 1, "forcenoaddon": 0, "dialogueavatars": 0,
            "fds": 1, "fdb": 2, "finger": 0, "pos": 1, "battleview": 3,
            "ba": 0, "tbb": 1, "nbb": 0, "hbb": 1, "esuiscale": 0,
            "force43": 1, "controller": 1, "controllerorder": 1,
            "controllerxinput": 0, "ECHOS": 1}


@pytest.fixture
def esui_mod(monkeypatch):
    import build
    mod = build.Mod(MOD, os.path.join(HERE, 'cache'))
    mod.ensure_extracted()
    monkeypatch.delenv(E.ENV, raising=False)
    monkeypatch.setattr(E, 'CURRENT', E.plan_for(mod, SETTINGS))
    return mod


@need_mod
def test_mod_is_handled_not_emplaced(esui_mod):
    assert E.handles(esui_mod)
    assert esui_mod.files_for(SETTINGS) == []
    assert E.screens() == ('main', 'load', 'shop', 'battle', 'menus') and E.has_folder('Fullmenu')
    assert E.esui_theme() and C.esui_profile()


@need_mod
def test_option_widths(esui_mod):
    w = E.widths()
    assert len(w) == 256 and w != E.WIDTHS
    assert w[:16] == E.WIDTHS[:16]          # the letters are 00-Spacing's
    # Echo-S's "expanded icons no spacing" zeroes the cave's 914196.. store
    assert w[0x99DDA8 + 0x0 - 0x99DDA8] == 3


@need_mod
def test_option_textures(esui_mod, tmp_path):
    src = E.texture_sources()
    assert 'buster.tex' not in src
    assert src['usfont_a_l.tex'][0][0] == 'Text/Text-XBOX-Gradient'
    import lgp, tex
    a = lgp.Archive(os.path.join(HERE, 'dump', 'romfs', 'ff7', 'workingdir',
                                 'data', 'menu', 'menu_us.lgp'))
    v = a.index['usfont_a_l.tex']['payload']
    out, note = E.build_texture('usfont_a_l.tex', v, src['usfont_a_l.tex'], 1)
    t, tv = tex.parse(out), tex.parse(v)
    assert (t['width'], t['height'], t['num_palettes']) == (256, 256, 16)
    assert tex.logical_scale(out) is None
    # the controller cells are the game's own characters in every palette
    import numpy as np
    import ff7nx_ddstex as DT
    m = E._controller_mask(256, 256)
    for p in (0, 14):
        got = DT.vanilla_palette_rgba(out, p, 256, 256)[m]
        want = DT.vanilla_palette_rgba(v, p, 256, 256)[m]
        assert (np.abs(got[:, :3].astype(int) - want[:, :3]).max(axis=1) <= 8).mean() > 0.97


@need_mod
def test_uncovered_reports_only_the_colour_file(esui_mod):
    assert E.uncovered() == [('ECHOS/MAIN', '06 - ESUI Colours.txt', 2)]


@need_mod
def test_joint_sheet_keeps_every_palettes_picture(esui_mod):
    """btl_win_b: ESUI draws the slot reels only in palette 11 and the stars
    only in 3..10; a palette-0 index map would lose both."""
    import numpy as np
    import lgp, tex, iro
    import ff7nx_ddstex as DT
    src = E.texture_sources()['btl_win_b_l.tex']
    a = lgp.Archive(os.path.join(HERE, 'dump', 'romfs', 'ff7', 'workingdir',
                                 'data', 'menu', 'menu_us.lgp'))
    v = a.index['btl_win_b_l.tex']['payload']
    out, note = E.build_texture('btl_win_b_l.tex', v, src, 1)
    t = tex.parse(out)
    assert (t['num_palettes'], t['colors_per_palette']) == (16, 256)
    pal = np.frombuffer(t['palette'], np.uint8).reshape(16, 256, 4)
    ix = np.frombuffer(t['pixels'], np.uint8).reshape(256, 256)
    for p in (3, 7, 11):
        want = DT._decode(iro.read_one(esui_mod.path, src[p][1]))[0]
        want = DT._resample_area(want, 256, 256)
        got_on = pal[p][ix][:, :, 3] > 0
        want_on = want[:, :, 3] >= 128
        assert (got_on == want_on).mean() > 0.97, p
        m = got_on & want_on
        err = np.abs(pal[p][ix][:, :, [2, 1, 0]][m].astype(int) - want[:, :, :3][m]).mean()
        assert err < 12, (p, err)


@need_mod
def test_avatar_keeps_esui_transparency(esui_mod):
    import struct
    import lgp, tex
    src = E.texture_sources()['cloud.tex']
    a = lgp.Archive(os.path.join(HERE, 'dump', 'romfs', 'ff7', 'workingdir',
                                 'data', 'menu', 'menu_us.lgp'))
    out, note = E.build_texture('cloud.tex', a.index['cloud.tex']['payload'], src, 1)
    assert struct.unpack_from('<I', out, tex.O_COLORKEY)[0] == 1
    t = tex.parse(out)
    import numpy as np
    ix = np.frombuffer(t['pixels'], np.uint8)
    assert 0.5 < (ix == 0).mean() < 0.9       # the margin around the portrait


@need_dump
def test_glyph_width_words_are_the_dump():
    """BUILD 622: the port's M/W/m/w (and 0x73/0xCA) narrow-quad compares,
    in the three glyph builders, all stock in the dump and all neutralised."""
    import nxmap
    m = nxmap.Main(DUMP_MAIN)
    assert len(E.CODE_GLYPHW) == 36
    assert {r[3] for r in E.CODE_GLYPHW} == {0x6D1CC0, 0x6E706D, 0x6F564E}
    for va, old, new, _f, note in E.CODE_GLYPHW:
        assert struct.unpack_from('<I', m.img, va)[0] == old, (hex(va), note)
        assert new == 0x713FFD1F
    assert not ({r[0] for r in E.CODE_GLYPHW}
                & {r[0] for r in E.CODE_MAIN + E.CODE_FULLMENU})


def test_glyph_width_only_with_esui_art(esui_on, monkeypatch):
    monkeypatch.setattr(E, 'CURRENT', None)
    assert not set(E.module_words()) & {r[0] for r in E.CODE_GLYPHW}
    monkeypatch.setattr(E, 'CURRENT', {'mod': None, 'folders': []})
    assert {r[0] for r in E.CODE_GLYPHW} <= set(E.module_words())


# Patrick's settings.json (BUILD 629): stock help/action boxes
PATRICK = dict(SETTINGS, hbb=0, cursor=1)


@pytest.fixture
def esui_patrick(monkeypatch):
    import build
    mod = build.Mod(MOD, os.path.join(HERE, 'cache'))
    mod.ensure_extracted()
    monkeypatch.delenv(E.ENV, raising=False)
    monkeypatch.setattr(E, 'CURRENT', E.plan_for(mod, PATRICK))
    return mod


@need_dump
@need_mod
def test_ported_screens_build(esui_patrick, tmp_path):
    """BUILD 624: the ported screens lay out in the dead body and every
    word they overwrite is the dump's. BUILD 629: under Patrick's options
    every ported function has a verified variant."""
    for s in ('load', 'shop', 'battle', 'menus'):
        assert E.screen_ports(s), s
    out = tmp_path / 'main'
    assert E.apply_to_nso(DUMP_MAIN, str(out))
    import nxmap, ff7nx_esuicave as EC
    m = nxmap.Main(DUMP_MAIN)
    words = E.port_words(DUMP_MAIN, m.text)
    lo, hi = m.extent(EC.DEAD_X86)
    caves = [va for va in words if lo <= va < hi]
    assert caves and min(caves) >= lo + EC.DEAD_GUARD
    b = nxmap.Main(str(out))
    for va, (old, new, _l) in words.items():
        assert struct.unpack_from('<I', b.img, va)[0] == new, hex(va)


@need_mod
def test_variants_follow_settings(esui_patrick, monkeypatch):
    """BUILD 629: the options pick each function's port. Help/action boxes
    No Box (hbb=1) and Stock (hbb=0) are different variants of 6D1CC0 (6DD041's
    code is the same, its tables differ); an ESUI variant no port was
    verified for keeps its screen stock."""
    import ff7nx_esui_ports as PP
    pat = {fn: E.fn_variant(*PP.FN_RANGE[fn]) for fn in (0x6D1CC0, 0x6DD041)}
    mod = esui_patrick
    monkeypatch.setattr(E, 'CURRENT', E.plan_for(mod, dict(PATRICK, hbb=1)))
    nob = {fn: E.fn_variant(*PP.FN_RANGE[fn]) for fn in pat}
    assert pat[0x6D1CC0] != nob[0x6D1CC0]
    assert all(pat[fn] in PP.PORTS['battle'][fn] for fn in pat)
    assert all(nob[fn] in PP.PORTS['battle'][fn] for fn in pat)
    # Stock help boxes: 6D1CC0 is the game's own code (no help-box rewrite)
    assert not any(PP.PORTS['battle'][0x6D1CC0][pat[0x6D1CC0]].values())
    monkeypatch.setattr(E, 'CURRENT', dict(E.CURRENT, folders=E.CURRENT['folders'] + ['BattleView/NBB']))
    E._SCREEN_PORTS.clear()
    if E.fn_variant(*PP.FN_RANGE[0x6DD041]) not in PP.PORTS['battle'][0x6DD041]:
        assert E.screen_ports('battle') is None


def test_aspect_ratio_is_the_switchs():
    """BUILD 629: 7th Heaven's ffnx_aspect_ratio is FFNx's; the Switch draws
    4:3, so battle views with Force 4:3 off still get their 4:3 folders."""
    import iro
    import types
    man = iro.Manifest(os.path.join(HERE, 'tools', 'esui', 'mod.xml')) \
        if os.path.isfile(os.path.join(HERE, 'tools', 'esui', 'mod.xml')) else None
    if man is None:
        pytest.skip('needs the ESUI mod.xml')
    plan = E.plan_for(types.SimpleNamespace(manifest=man), dict(SETTINGS, force43=0))
    assert any(f.lower() == 'battleview/battleviewthree' for f in plan['folders'])


def test_site_delta_composes():
    port = {'sites': {1: (0x6F5B03, {1: ('affd', 22, 67, 184, 184)}, []),
                      2: (0x6F5B03, {0: ('add', -24)}, [])}}
    E.SITE_DELTA['_t'] = {7: {1: (0x6F5B03, {1: -5}), 2: (0x6F5B03, {1: -5}),
                              3: (0x6F9739, {0: 8})}}
    try:
        out = E._with_deltas('_t', 7, port)
    finally:
        del E.SITE_DELTA['_t']
    assert out['sites'][1][1][1] == ('affd', 22, 67, 184, 179)
    assert out['sites'][2][1] == {0: ('add', -24), 1: ('add', -5)}
    assert out['sites'][3] == (0x6F9739, {0: ('add', 8)}, [])
    assert port['sites'][1][1][1] == ('affd', 22, 67, 184, 184)      # the port itself untouched


@need_mod
def test_shop_port(esui_patrick):
    """BUILD 625: the shop's six functions are carried, the store caves are
    real stores, and the shop strings patch only stock bytes."""
    import ff7nx_esui_ports as PP
    import exe_patch
    assert set(PP.PORTS['shop']) == {0x71AAA3, 0x71E415, 0x71EE01, 0x719E90, 0x71A6E6, 0x71FA7D}
    import nxmap, ff7nx_esuicave as EC
    m = nxmap.Main(DUMP_MAIN)
    for fn, variants in PP.PORTS['shop'].items():
        for port in variants.values():
            for site in port.get('stores', {}):
                w = struct.unpack_from('<I', m.img, site)[0]
                assert (w & 0xFFC00000) in EC.STORE_SIZES, hex(site)
    assert 0x922D08 in {d[0] for d in E._sets()['data']}
    x = open('dump/romfs/ff7/resources/ff7_1.02/ff7_en', 'rb').read()
    pe = exe_patch.parse_pe(x)
    out, n = E.patch_exe(x, lambda va: exe_patch.va_to_offset(pe, va))
    assert 0 < n <= len(E._sets()['data'])
    o = exe_patch.va_to_offset(pe, 0x922D08)
    assert out[o:o + 8] == bytes.fromhex('ff25584954ff0000')     # BUILD 628: Exit its own string


def test_battle_fade_keeps_ui_until_black():
    """BUILD 635: the end-of-battle fade quad covers the screen and ESUI's
    hide/transparency flips wait for the fade's last step."""
    import ff7nx_esui as E
    import ff7nx_esuicave as EC
    port = {'store_const': [(0xD1AA08, 0x43F00000), (0xD1AC28, 0x43F00000)],
            'hooks': [(0xD1AC2C, [('st8', 0x91C343, 2), ('st32', 0xDC3CD8, 0)])]}
    out = E._battle_fade(port)
    assert dict(out['store_const']) == E.FADE_QUAD and len(E.FADE_QUAD) == 8
    ops = out['hooks'][0][1]
    assert ops[0] == ('skip_lt8', 0xBFB2E8, 0xF0) and ops[1:] == port['hooks'][0][1]
    assert E._battle_fade(out) == out
    w = EC.hook_cave(0x1000, 0xD1AC2C, 0xB9401728, ops)
    assert any((x & 0xFF00001F) == 0x54000003 for x in w)          # the b.lo
