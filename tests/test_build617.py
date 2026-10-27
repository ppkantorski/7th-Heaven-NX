"""BUILD 617: Great Glacier left-edge band and 16:9 FX margins.

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

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import diag_common as DC                                       # noqa: E402
import ff7nx_parallaxfill as PF                                # noqa: E402
import ff7nx_trnad4 as T                                       # noqa: E402
import lgp                                                     # noqa: E402

VANILLA = os.path.join(ROOT, 'game_data_files', 'field', 'flevel.lgp')
CHUNKS = os.path.join(ROOT, 'cache', 'CosmosLimitBreak', 'LIMIT BREAK',
                      'flevel.lgp')


def test_authored_grid_leaves_the_band_the_truncating_range_reaches():
    # The recorded symptom: a gap at the LEFT picture edge for bg.x < -506.
    gap, dup = T.edge_coverage(T.EDGE_COLS, -530)
    assert gap > 0 and dup == 0
    bad = T.edge_proof(T.EDGE_COLS)
    assert bad is not None


def test_moved_grid_covers_the_whole_truncating_range_without_doubles():
    assert T.edge_proof(T.EDGE_MOVED_COLS) is None
    lo, hi = T.TRUNC_BG_RANGE
    assert T.edge_proof(T.EDGE_MOVED_COLS, lo - 100, hi + 100) is None
    # Still one tile per residue of the 704 period: nothing is duplicated.
    assert sorted(c % 704 for c in T.EDGE_MOVED_COLS) == \
        sorted(c % 704 for c in T.EDGE_COLS)


def _field(name):
    if not os.path.exists(VANILLA):
        pytest.skip('vanilla flevel not present')
    arc = lgp.Archive(VANILLA)
    parts = list(lgp.split_sections(arc.decompressed(arc.index[name])))
    chunk = os.path.join(CHUNKS, name + '.chunk.9')
    if os.path.exists(chunk):
        with open(chunk, 'rb') as fh:
            parts[8] = fh.read()
    new7, new9, st = T.apply_to_sections(parts[7], parts[8], name)
    assert not st['refusal'], st['refusal']
    return new7, new9


@pytest.mark.parametrize('name', ['hyou3', 'move_d'])
def test_relocation_only_rewrites_layer4_dst_x_and_is_idempotent(name):
    s7, s9 = _field(name)
    out, st = T.relocate_edge_sections(s7, s9, name)
    assert st['tiles'] == 80 and not st['refusal']
    assert len(out) == len(s9)
    surv = DC.survey(out)
    l4 = [r for r in PF._layers(out, surv['back_start'], surv['tex_start'])
          if r[0] == 4][0]
    allowed = {l4[2] + i * PF.TILE_SIZE + k
               for i in range(l4[3]) for k in (PF.T_DSTX, PF.T_DSTX + 1)}
    changed = {i for i, (a, b) in enumerate(zip(s9, out)) if a != b}
    assert changed and changed <= allowed
    cols = sorted({struct.unpack_from('<h', out, l4[2] + i * 52 + 2)[0]
                   for i in range(l4[3])})
    assert tuple(cols) == T.EDGE_MOVED_COLS
    again, st2 = T.relocate_edge_sections(s7, out, name)
    assert again == out and st2['tiles'] == 0 and not st2['refusal']


def test_relocation_refuses_non_targets_and_can_be_disabled(monkeypatch):
    s7, s9 = _field('hyou3')
    assert T.relocate_edge_sections(s7, s9, 'trnad_4')[0] == s9
    monkeypatch.setenv(T.EDGE_OFF_ENV, '1')
    assert T.relocate_edge_sections(s7, s9, 'hyou3')[0] == s9


def test_fxart_617_quantiser_keys_black_and_picks_nearest_non_key():
    import numpy as np
    import ff7nx_fxart as FA
    pal = np.zeros((256, 3), np.uint8)
    pal[0] = (200, 0, 200)          # the key's stored colour must never win
    pal[1] = (0, 64, 0)
    pal[2] = (0, 128, 0)
    pal[3:] = 255
    art = np.zeros((16, 16, 3), np.float32)
    art[:8] = (0, 120, 0)
    idx, err = FA._quantise_premul(art, pal)
    assert (idx[8:] == 0).all() and (idx[:8] == 2).all() and err < 5


def test_fxart_617_leaves_proven_fields_and_can_be_disabled(monkeypatch):
    import ff7nx_fxart as FA
    s9 = b'BACK' + b'\0' * 60
    for name in ('sinbil_1', 'ghotel'):
        assert FA.apply_margin_617(name, s9, [0], object())[0] == s9
    monkeypatch.setenv(FA.EXT_OFF_ENV, '1')
    assert FA.apply_margin_617('sninn_2', s9, [0], object())[0] == s9


def test_fxsplit_carries_no_617_margin_code():
    # The HD-clone attempt was withdrawn: margins stay paletted (fxart).
    import ff7nx_fxsplit as S
    assert not hasattr(S, 'margin_clone_section9')


def _integrity_helpers():
    import ast
    import struct
    import lgp
    src = open(nxpaths.src_file('build.py')).read()
    ns = {'lgp': lgp, 'struct': struct}
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name in (
                '_field_payload_matches', 'field_payload_integrity'):
            exec(compile(ast.Module([node], []), nxpaths.src_file('build.py'), 'exec'), ns)
    return ns


def test_encode_cache_rejects_zero_holed_hit_and_integrity_flags_it():
    """Build 382's mtcrl_1: 16 KiB of zeros inside the stored stream."""
    import lgp
    ns = _integrity_helpers()
    body = bytes((i * 7) & 0xFF for i in range(40000)) * 3
    sections = [b'x' * 10] * 8 + [b'BACK' + body + b'END']
    raw = lgp.join_sections(sections)
    enc = lgp.lzs_compress(raw)
    good = len(enc).to_bytes(4, 'little') + enc
    assert ns['_field_payload_matches'](good, raw)
    holed = good[:5000] + bytes(4096) + good[5000 + 4096:]
    assert not ns['_field_payload_matches'](holed, raw)
    bad = ns['field_payload_integrity']({'good': good, 'holed': holed})
    assert [n for n, _r in bad] == ['holed']


def test_palettedart_detects_transposed_sky_atlas():
    """fship_22: the repack transposed the sky atlas cells (and UVs)."""
    import numpy as np
    import ff7nx_palettedart as PA
    rng = np.random.default_rng(617)
    cells = rng.integers(0, 255, size=(8, 8, 3))
    target = np.kron(cells, np.ones((32, 32, 1))).astype(np.uint8)
    noise = rng.integers(-2, 3, size=target.shape)
    old_id = np.clip(target + noise, 0, 255).astype(np.uint8)
    old_tr = PA._cell_transpose(old_id)
    assert PA._atlas_layout(old_id, target)[0] is False
    assert PA._atlas_layout(old_tr, target)[0] is True
    assert np.array_equal(PA._cell_transpose(PA._cell_transpose(target)),
                          target)
    with pytest.raises(PA.PalettedArtError):
        PA._atlas_layout(rng.integers(0, 255, size=target.shape)
                         .astype(np.uint8), target)


def test_staticpage_layer2_target_is_fship22_only():
    import ff7nx_staticpage as SP
    assert set(SP.L2_TARGETS) == {'fship_22'}
    assert not set(SP.L2_TARGETS) & set(SP.TARGETS)
    with pytest.raises(SP.StaticPageError):
        SP._promote_layer2_page('fship_22', [b''] * 9, object(), None)


def test_backdrop_key_arm_is_switchable(monkeypatch):
    import ff7nx_lostdetail as LD
    monkeypatch.delenv(LD.BACKDROP_OFF_ENV, raising=False)
    assert not LD.backdrop_disabled()
    monkeypatch.setenv(LD.BACKDROP_OFF_ENV, '1')
    assert LD.backdrop_disabled()
    assert 'BACKDROP KEY' in LD.summarise(
        {'fields': 1, 'pages': 1, 'units': 0, 'components': 0,
         'skipped_large': 0, 'names': ['trnad_2:0'], 'refused': [],
         'backdrop_units': 5, 'backdrop_names': ['trnad_2:5']})


def test_fxseam_colour_roundtrip_and_switches(monkeypatch):
    import numpy as np
    import ff7nx_fxseam as S
    v = np.array([0x0841, 0xFFDF, 0x7BCF, 0x3186], np.uint16)
    assert np.array_equal(S._pack(S._unpack(v)), v)
    monkeypatch.setenv(S.OFF_ENV, '1')
    assert S.disabled()
    monkeypatch.delenv(S.ANIM_OFF_ENV, raising=False)
    assert not S.anim_disabled()      # on by default since post-384
    monkeypatch.setenv(S.ANIM_OFF_ENV, '1')
    assert S.anim_disabled()
    assert 'mds5_1' in S.SKIP_FIELDS
    assert S.summarise({'fields': 0}) == ''


def test_camfit_edge_speck_is_not_the_picture_edge():
    """fship_22: stray lit units beyond black filler must not extend art."""
    import ff7nx_camfit as CF
    right = [True] * 6 + [False] * 9 + [True]          # tile at x=208
    assert CF._inner_run(208, right) == (208, 214)
    left = [True] + [False] * 10 + [True] * 5          # tile at x=-224
    assert CF._inner_run(-224, left) == (-213, -208)
    full = [True] * 16
    assert CF._inner_run(208, full) == (208, 224)
    assert CF._inner_run(-224, full) == (-224, -208)


# ------------------------------------------------------------ BUILD 617b
def _vanilla_parts(name, cosmos9=False):
    if not os.path.exists(VANILLA):
        pytest.skip('vanilla flevel not present')
    arc = lgp.Archive(VANILLA)
    parts = list(lgp.split_sections(arc.decompressed(arc.index[name])))
    if cosmos9:
        chunk = os.path.join(CHUNKS, name + '.chunk.9')
        if not os.path.exists(chunk):
            pytest.skip('Cosmos chunk not present')
        with open(chunk, 'rb') as fh:
            parts[8] = fh.read()
    return parts


def test_pagecap_leaves_fx_bound_tiles_on_their_texture_id(monkeypatch):
    # las4_0: 1680 additive tiles name page 0 as texture id but bind their
    # fx pages. Splitting them onto a copy of page 0 made the right-edge
    # steam vanish on hardware (the copy has no live palettes).
    import field_bg_pagecap as P
    parts = _vanilla_parts('las4_0', cosmos9=True)
    monkeypatch.setattr(P, 'FX_TEXID_EXEMPT', True)
    out, st = P.cap_section9(parts[8])
    assert st.pages_added == 0 and out == parts[8]
    monkeypatch.setattr(P, 'FX_TEXID_EXEMPT', False)
    out, st = P.cap_section9(parts[8])
    assert st.pages_added > 0


def test_tvnotch_squares_mktpb_corners_on_the_paletted_page_only():
    import ff7nx_tvnotch as TN
    parts = _vanilla_parts('mktpb')
    plans, st = TN.plan_field('mktpb', parts[8], parts[3])
    assert st['states'] == 4 and 0 < st['texels'] < 200
    assert {p[0] for p in plans} == {15}
    out = TN.apply_plans(parts[8], plans)
    assert len(out) == len(parts[8])
    assert TN.plan_field('mktpb', out, parts[3])[1]['texels'] == 0
    assert TN.plan_field('md1_1', parts[8], parts[3])[0] == []


def test_siblingmargin_range_follows_sibling_only_when_filled(monkeypatch):
    import ff7nx_siblingmargin as SM
    fin = {'fship_22': {'left': -161, 'right': 159, 'top': -144,
                        'bottom': 256},
           'fship_24': {'left': -170, 'right': 170, 'top': -144,
                        'bottom': 256}}
    SM.FILLED.clear()
    assert SM.range_overrides(fin) == {}
    SM.FILLED.add('fship_22')
    r = SM.range_overrides(fin)['fship_22']
    assert (r['left'], r['right'], r['top'], r['bottom']) == \
        (-170, 170, -144, 256)
    monkeypatch.setenv(SM.OFF_ENV, '1')
    assert SM.range_overrides(fin) == {}
    SM.FILLED.clear()


def test_anim_close_targets_mds5_1_screen_only(monkeypatch):
    import ff7nx_fxseam as S
    assert set(S.CLOSE_FIELDS) == {'mds5_1'}
    assert S.CLOSE_FIELDS['mds5_1'] == frozenset({1})
    assert S.plan_anim_close('md1_1', b'')[0] == []
    monkeypatch.setenv(S.CLOSE_OFF_ENV, '1')
    assert S.plan_anim_close('mds5_1', b'')[0] == []


def test_rim_despike_targets_mds5_1_grate_only(monkeypatch):
    import ff7nx_fxseam as S
    assert set(S.RIM_TARGETS) == {'mds5_1'}
    assert S.plan_rim_despike('md1_1', b'')[0] == []
    monkeypatch.setenv(S.RIM_OFF_ENV, '1')
    assert S.plan_rim_despike('mds5_1', b'')[0] == []


def test_siblingmargin_colour_map_is_smooth_between_rows():
    import ff7nx_siblingmargin as SM
    assert SM.SMOOTH_PASSES >= 1 and SM.STRIP == 10


def test_grate_faces_target_mds5_1_only(monkeypatch):
    import ff7nx_fxseam as S
    assert set(S.GRATE_TARGETS) == {'mds5_1'}
    assert S.plan_grate_faces('md1_1', b'')[0] == []
    monkeypatch.setenv(S.GRATE_OFF_ENV, '1')
    assert S.plan_grate_faces('mds5_1', b'')[0] == []


def test_texpatch_565_roundtrip_and_switch(monkeypatch):
    import numpy as np
    import ff7nx_texpatch as TP
    v = np.arange(1, 65536, 97, dtype=np.uint16)
    v = v[(v & 0x20) == 0]                 # 555 layout: bit 5 unused
    assert (TP._pack(TP._unpack(v)) == v).all()
    monkeypatch.setenv(TP.OFF_ENV, '1')
    assert TP.apply_to_flevel(None, {}) == {'patches': [], 'refused': []}


# ------------------------------------------------ BUILD 617: snowboard mouth
_SNOWVAN = os.path.join(ROOT, 'cache', '_vanilla', 'snowboard-us.lgp')


def _snowfile(name):
    p = os.path.join(_SNOWVAN, name)
    if not os.path.exists(p):
        pytest.skip('vanilla snowboard files not present')
    return open(p, 'rb').read()


@pytest.mark.parametrize('name', ['for_ev.tmd', 'for_gs.tmd'])
def test_snowmouth_on_the_lifted_face(name):
    import ff7nx_snowface as SF
    import ff7nx_snowmouth as SM
    lifted, _n = SF.patch(name, _snowfile(name))
    new, note = SM.patch_tmd(name, lifted)
    assert SM.state(name, new) == 'patched', note
    assert new[:len(lifted)] == lifted[:12 + 28 * SM.FACE] + \
        new[12 + 28 * SM.FACE:12 + 28 * (SM.FACE + 1)] + \
        lifted[12 + 28 * (SM.FACE + 1):len(lifted)]
    assert SM.patch_tmd(name, new)[0] == new          # idempotent
    # every face triangle (eyes and mouth) winds the same way; the first
    # hardware test culled a mouth wound against the eyes
    vt, nv, _nt, _nn, pt, npri, _sc = SM._table(new, SM.FACE)
    fv = SM._verts(new, vt, nv)
    signs = set()
    for raw in SM._prims(new, pt, npri):
        a, b, c = [fv[i] for i in struct.unpack_from('<HHH', raw, 20)]
        signs.add((b[0] - a[0]) * (c[1] - a[1])
                  - (b[1] - a[1]) * (c[0] - a[0]) > 0)
    assert npri == SM.PATCHED_NPRI and len(signs) == 1
    verts, tris = SM.mouth_geometry(lifted)
    assert len(verts) == 6 and all(v[2] < -9 for v in verts)
    for _t, uvs in tris:
        for u, v in uvs:
            assert SM.U0 <= u <= SM.U1 and SM.V0 <= v <= SM.V1
            # outside every eye triangle's UV footprint
            assert u > 45 or v > 163 + (44 - u) * 8 / 36.0


def test_snowmouth_texture_draws_once_in_empty_corner():
    import ff7nx_snowmouth as SM
    tex = _snowfile('eyes.tex')
    new, note = SM.patch_tex(tex)
    assert new != tex and len(new) == len(tex), note
    assert SM.patch_tex(new)[0] == new


def test_snowmouth_switch(monkeypatch):
    import ff7nx_snowmouth as SM
    monkeypatch.setenv(SM.ENV, '0')
    assert not SM.enabled()


# ------------------------------------------------ BUILD 617: sninn_2 glow fill
_BUILT = os.environ.get('SEVENTH_NX_TEST_BUILT_FLEVEL', os.path.expanduser(
    '~/mnt/7th_heaven_nx/sdout/atmosphere/contents/0100A5B00BDC6000/romfs/'
    'ff7/workingdir/data/field/flevel.lgp'))


def test_glowfill_fills_sninn_2_hole_and_4_3_edge():
    import lgp
    import ff7nx_glowfill as GF
    import ff7nx_palanim
    if not os.path.exists(_BUILT):
        pytest.skip('no built flevel')
    arc = lgp.Archive(_BUILT)
    parts = list(lgp.split_sections(arc.decompressed(arc.index['sninn_2'])))
    anim = ff7nx_palanim.animated_palettes(parts[0])
    try:
        plans, rt, st = GF.plan_field('sninn_2', parts[8], parts[3], anim)
    except ValueError:
        pytest.skip('built flevel already carries a different layout')
    if not plans:
        pytest.skip('built flevel already filled')
    assert {p[0] for p in plans} == {15} and st['cells'] > 10
    out = GF.apply_plans(parts[8], plans, rt)
    assert len(out) == len(parts[8])
    # the hole is gone: nothing dark at the region's bottom row
    import render_field as RF
    import diag_common as DC
    pal = RF._pal_rgb(parts[3])
    pages = {p.slot: p for p in DC.parse_pages(out)[0] if p is not None}
    page = np.frombuffer(pages[15].data, np.uint8).reshape(256, 256)
    for slot, py, px, v in plans:
        assert page[py, px] == v
    # every written cell is lit
    lit = pal[2][[v for *_x, v in plans]].max(-1)
    assert (lit > 0).mean() > 0.95
    assert GF.plan_field('sninn_2', out, parts[3], anim)[2]['texels'] < \
        st['texels'] // 4


def test_glowfill_leaves_other_fields_and_can_be_disabled(monkeypatch):
    import ff7nx_glowfill as GF
    assert GF.plan_field('md1_1', b'', b'')[0] == []
    monkeypatch.setenv(GF.OFF_ENV, '1')
    assert GF.apply_to_flevel(None, {})['fields'] == 0


# ------------------------------------------------ BUILD 617: games lamp heads
def test_lampfill_lights_the_heads_not_the_poles_or_the_sign():
    import lgp
    import ff7nx_lampfill as LF
    import ff7nx_fxseam as FS
    if not os.path.exists(_BUILT):
        pytest.skip('no built flevel')
    arc = lgp.Archive(_BUILT)
    parts = list(lgp.split_sections(arc.decompressed(arc.index['games'])))
    plans, st = LF.plan_field('games', parts[8])
    if not plans:
        pytest.skip('built flevel already filled')
    assert 6 <= st['heads'] <= 10          # two per lamp, not the sign
    assert len({p[0] for p in plans}) == 1
    out = FS.apply_plans(parts[8], plans)
    assert len(out) == len(parts[8])
    assert LF.plan_field('games', out)[1]['texels'] == 0     # idempotent
    # one colour for every head (the feathered rim aside)
    vals = [v for *_x, v in plans]
    top = max(set(vals), key=vals.count)
    assert vals.count(top) > 0.6 * len(vals)


def test_lampfill_other_fields_and_switch(monkeypatch):
    import ff7nx_lampfill as LF
    assert LF.plan_field('md1_1', b'')[0] == []
    monkeypatch.setenv(LF.OFF_ENV, '1')
    assert LF.apply_to_flevel(None, {})['fields'] == 0


# ------------------------------------------------ BUILD 617b: mds7st1 closes
def test_ambient_loops_above_96k_are_converted_by_default(monkeypatch):
    import ff7nx_ambient as AM
    monkeypatch.delenv(AM.RATE_ENV, raising=False)
    assert AM.convert_threshold() == 96000          # 1525.ogg (192k) goes
    assert AM.convert_target() == 96000             # ... to 96k
    monkeypatch.setenv(AM.RATE_ENV, 'native')
    assert AM.convert_threshold() is None and not AM.convert_enabled()
    monkeypatch.setenv(AM.RATE_ENV, '48k')
    assert AM.convert_threshold() == AM.MAX_RATE
    assert AM.convert_target() == AM.MAX_RATE


def test_stage_loops_converts_only_the_192k_loop(monkeypatch, tmp_path):
    import ff7nx_ambient as AM
    monkeypatch.delenv(AM.RATE_ENV, raising=False)
    rates = {'1525': 192000, '1503': 96000, '468': 88200, '1501': 44100}
    src = {}
    for k in rates:
        p = tmp_path / ('%s.ogg' % k)
        p.write_bytes(b'x' * 10)
        src[k] = str(p)
    conv = tmp_path / 'c.ogg'
    conv.write_bytes(b'y' * 5)
    monkeypatch.setattr(AM, '_rate_channels',
                        lambda path: (rates.get(os.path.basename(path).split('.')[0],
                                                48000), 2))
    asked = []
    monkeypatch.setattr(AM, '_converted_copy',
                        lambda s, r=None: asked.append(r) or str(conv))
    AM.stage_loops(src, str(tmp_path / 'out'), [int(k) for k in rates])
    assert AM.CONVERTED == [(1525, 192000)]
    assert asked == [96000]                     # to the proven rate, not 48k
    assert (tmp_path / 'out' / '1525.ogg').read_bytes() == b'y' * 5
    assert (tmp_path / 'out' / '1503.ogg').read_bytes() == b'x' * 10


# ------------------------------------------------ BUILD 617c: colne_6 beams
def test_beams_redraw_whole_cones_and_extend(monkeypatch):
    import lgp
    import ff7nx_beams as BM
    import diag_common as DC
    if not os.path.exists(_BUILT):
        pytest.skip('no built flevel')
    arc = lgp.Archive(_BUILT)
    parts = list(lgp.split_sections(arc.decompressed(arc.index['colne_6'])))
    try:
        new9, st = BM.plan_field('colne_6', parts[8], parts[3])
    except ValueError as exc:
        pytest.skip('built flevel already converted: %s' % exc)
    assert st['cones'] == 3 and st['fit'] < 1.5
    assert st['texid_records'] <= 256
    pages = {p.slot: p for p in DC.parse_pages(new9)[0] if p is not None}
    assert pages[15].depth == 2 and pages[15].px == 768
    xs = []
    for layer, offs in DC.walk_layers(new9, new9.find(b'BACK'),
                                      DC.parse_pages(new9)[1]):
        for o in offs:
            if new9[o + 28] and new9[o + 34] == 15:
                xs.append(struct.unpack_from('<h', new9, o + 2)[0])
    assert min(xs) == -224 and max(xs) == 208      # to both screen edges
    assert len(xs) == st['records']
    # everything outside page 15 and the beam records is untouched
    old_pages = {p.slot: p for p in DC.parse_pages(parts[8])[0]
                 if p is not None}
    for s_ in (0, 26, 27, 28):
        assert pages[s_].data == old_pages[s_].data
    with pytest.raises(ValueError):
        BM.plan_field('colne_6', new9, parts[3])   # not twice


def test_beams_mode3_fine_steps_on_the_paletted_page():
    import lgp
    import ff7nx_beams as BM
    import diag_common as DC
    import ff7nx_marginblack as MB
    if not os.path.exists(_BUILT):
        pytest.skip('no built flevel')
    arc = lgp.Archive(_BUILT)
    parts = list(lgp.split_sections(arc.decompressed(arc.index['colne_6'])))
    try:
        new9, new4, st = BM.plan_field_mode3('colne_6', parts[8], parts[3])
    except ValueError as exc:
        pytest.skip('built flevel already converted: %s' % exc)
    assert st['cones'] == 3 and st['texid_records'] <= 256
    assert st['cells'] <= 256
    pages = {p.slot: p for p in DC.parse_pages(new9)[0] if p is not None}
    old_pages = {p.slot: p for p in DC.parse_pages(parts[8])[0]
                 if p is not None}
    assert pages[15].depth == 1 and pages[15].px == 256   # stock page kind
    for s_ in (0, 26, 27, 28):
        assert pages[s_].data == old_pages[s_].data
    xs, modes = [], {}
    for layer, offs in DC.walk_layers(new9, new9.find(b'BACK'),
                                      DC.parse_pages(new9)[1]):
        for o in offs:
            if new9[o + 28] and new9[o + 34] == 15:
                xs.append(struct.unpack_from('<h', new9, o + 2)[0])
                modes[new9[o + 30]] = modes.get(new9[o + 30], 0) + 1
    assert min(xs) == -224 and max(xs) == 208       # to both screen edges
    assert set(modes) <= {1, 3} and modes.get(3, 0) > modes.get(1, 0)
    # palettes 5/9/10 become ramps; every other palette is untouched
    old = MB.palette_colours(parts[3])[0]
    new = MB.palette_colours(new4)[0]
    for q in range(len(old)):
        if q in (5, 9, 10):
            assert (new[q][1:32] & 0x8000).all()
            used = new[q][32:]
            assert ((used == 0) | (used & 0x8000 != 0)).all()
        else:
            assert (new[q] == old[q]).all()
    with pytest.raises(ValueError):
        BM.plan_field_mode3('colne_6', new9, new4)    # not twice


def test_beams_mode3_one_record_per_position(monkeypatch):
    # vanilla colne_6 (the build moves the ordinary tiles off texture id 0,
    # vanilla still shares it, so the per-id cap is lifted here)
    import collections
    import lgp
    import ff7nx_beams as BM
    import diag_common as DC
    van = os.path.join(os.path.dirname(__file__), '..', 'dump', 'romfs',
                       'ff7', 'workingdir', 'data', 'field', 'flevel.lgp')
    if not os.path.exists(van):
        pytest.skip('no vanilla flevel')
    monkeypatch.setattr(BM, 'MAX_RECORDS_PER_PAGE', 10 ** 6)
    arc = lgp.Archive(van)
    parts = list(lgp.split_sections(arc.decompressed(arc.index['colne_6'])))
    new9, new4, st = BM.plan_field_mode3('colne_6', parts[8], parts[3])
    assert st['records'] <= 250 and st['cells'] <= 250
    pos = collections.Counter()
    xs = []
    for layer, offs in DC.walk_layers(new9, new9.find(b'BACK'),
                                      DC.parse_pages(new9)[1]):
        for o in offs:
            if new9[o + 28] and new9[o + 34] == 15:
                xy = struct.unpack_from('<hh', new9, o + 2)
                pos[xy] += 1
                xs.append(xy[0])
                # src mirrors src2 on every beam record
                assert new9[o + 10:o + 14] == new9[o + 14:o + 18]
    assert max(pos.values()) == 1          # never two records on one spot
    assert min(xs) == -224 and max(xs) == 208


def test_beam_model_matches_a_synthetic_cone():
    import ff7nx_beams as BM
    H, W, x0, y0 = 120, 200, -100, -60
    yy, xx = np.mgrid[0:H, 0:W]
    xs, ys = xx + x0 + 0.5, yy + y0 + 0.5
    ax, ay = 90.0, 0.0                       # apex on the right, beam left
    t = (ys - ay) / np.maximum(ax - xs, 1e-3)
    inside = (t > -0.15) & (t < 0.25) & (xs < ax)
    lum = np.where(inside, 20 + 0.2 * (ax - xs), 0).astype(np.float32)
    light = np.stack([lum, lum, lum * 0.1], -1)
    m = BM.fit_cone(light, x0, y0)
    assert abs(m['apex'][0] - ax) < 4 and abs(m['apex'][1] - ay) < 2
    assert m['left']
