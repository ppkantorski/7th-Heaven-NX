"""BUILD 618: junbin21 chair cut-out (ff7nx_cutout).

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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

_DUMP = os.path.join(os.path.dirname(__file__), '..', 'dump', 'romfs', 'ff7',
                     'workingdir', 'data', 'field', 'flevel.lgp')
_BUILT = os.path.expanduser(
    '~/mnt/7th_heaven_nx/sdout/atmosphere/contents/0100A5B00BDC6000/romfs/'
    'ff7/workingdir/data/field/flevel.lgp')


def test_cutout_mask_shape():
    import ff7nx_cutout as C
    m = C.CUTS['junbin21']['mask']
    assert len({len(r) for r in m}) == 1
    assert set(''.join(m)) <= {'.', '#'}


def test_cutout_junbin21_only_trims_the_chair():
    import lgp
    import diag_common as DC
    import ff7nx_cutout as C
    if not os.path.exists(_BUILT):
        pytest.skip('no built flevel')
    arc = lgp.Archive(_BUILT)
    parts = list(lgp.split_sections(arc.decompressed(arc.index['junbin21'])))
    try:
        new9, n = C.plan_field('junbin21', parts[8])
    except ValueError as exc:
        pytest.skip('built flevel already cut: %s' % exc)
    assert 300 < n < 2000
    old = {p.slot: p for p in DC.parse_pages(parts[8])[0] if p is not None}
    new = {p.slot: p for p in DC.parse_pages(new9)[0] if p is not None}
    for s, p in old.items():
        a = np.frombuffer(p.data, np.uint8 if p.depth == 1 else '<u2')
        b = np.frombuffer(new[s].data, np.uint8 if p.depth == 1 else '<u2')
        if s in (16, 26, 27):
            assert ((a == b) | (b == 0)).all()        # only keyed out
        else:
            assert (a == b).all()
    # records untouched
    assert len(new9) == len(parts[8]) or new9[:new9.find(b'TEXTURE')] == \
        parts[8][:parts[8].find(b'TEXTURE')]
    with pytest.raises(ValueError):
        C.plan_field('junbin21', new9)                # not twice


def test_debugwarp_off_by_default_and_parses_pairs():
    import ff7nx_debugwarp as W
    assert W.pairs({}) == []
    assert W.pairs({W.ENV: 'ujunon1=cosmo2, ujunon2 = fr_e'}) == [
        ('ujunon1', 'cosmo2'), ('ujunon2', 'fr_e')]
    # a source twice, or a source that is also a target, is dropped
    assert W.pairs({W.ENV: 'a=b,a=c,b=d,e=f'}) == [('a', 'b'), ('e', 'f')]


def test_debugwarp_reaims_only_gates_into_the_source():
    import ff7nx_debugwarp as W
    trig = bytearray(0x38 + 24 * 12)
    struct.pack_into('<6h3hH', trig, 0x38, 1, 2, 0, 3, 4, 0, 10, 20, 5, 428)
    struct.pack_into('<6h3hH', trig, 0x38 + 24, 1, 2, 0, 3, 4, 0, 7, 8, 9, 99)
    new, n = W.retarget_gates(bytes(trig), 428, (-698, -1331, 215))
    assert n == 1
    assert struct.unpack_from('<3hH', new, 0x38 + 12) == (-698, -1331, 215,
                                                          428)
    assert new[0x38 + 24:] == bytes(trig[0x38 + 24:])
    walk = struct.pack('<I', 2) + struct.pack('<12h', 0, 0, 0, 0, 30, 0, 0, 0,
                                              0, 30, 0, 0) \
        + struct.pack('<12h', 100, 100, 0, 0, 130, 100, 0, 0, 100, 130, 0, 0)
    x, y, t = W.spawn_point(walk)
    assert t in (0, 1)


def test_snowtifa_lift_and_mouth_on_built_files():
    import lgp
    import ff7nx_snowtifa as T
    p = os.path.expanduser(
        '~/mnt/7th_heaven_nx/sdout/atmosphere/contents/0100A5B00BDC6000/'
        'romfs/ff7/workingdir/data/minigame/snowboard-us.lgp')
    if not os.path.exists(p):
        pytest.skip('no built snowboard archive')
    a = lgp.Archive(p)
    d = a.index['for_gs.tmd']['payload']
    t = a.index['tifaeye.tex']['payload']
    if T.face_state(d, 264) != 'stock':
        pytest.skip('already patched')
    n, _ = T.patch_tmd(d)
    assert T.face_state(n, 264) == 'mouth' and T.face_state(n, 279) == 'lifted'
    assert n[:len(d)] != d                     # z changed in place
    assert T.patch_tmd(n)[0] == n              # idempotent
    nt, note = T.patch_tex(t)
    assert nt != t and T.patch_tex(nt)[0] == nt
    lifted, _ = T.patch_tmd(d, mouth=False)
    assert T.face_state(lifted, 264) == 'lifted'


def test_fieldpan_env_and_camera():
    import ff7nx_fieldshift as S
    assert S.pans({}) == {'md_e1': 3}
    assert S.pans({S.ENV: '0'}) == {}
    assert S.pans({S.ENV: 'md_e1=5'}) == {'md_e1': 5.0}


def test_fieldshift_md_e1_tilts_camera_only():
    import lgp
    import ff7nx_fieldshift as S
    if not os.path.exists(_DUMP):
        pytest.skip('no vanilla dump')
    arc = lgp.Archive(_DUMP)
    v = list(lgp.split_sections(arc.decompressed(arc.index['md_e1'])))
    new, dy, dx = S.plan_camera(v[1], 3, v[4])
    assert abs(dy - 3) < 0.25 and dx < 0.5
    old_c = struct.unpack_from(S.CAM_FMT, v[1])
    new_c = struct.unpack_from(S.CAM_FMT, new)
    assert new_c[13:16] == old_c[13:16] == (0, 0, 576)   # pan stays (0, 0)
    assert new_c[0:3] == old_c[0:3]                      # x axis unchanged
    assert new_c[9] == new_c[8]                          # vz.z copy kept
    # md0 has the same camera and must stay untouched (only md_e1 listed)
    assert set(S.TILTS) == {'md_e1'}
    bad = bytearray(v[1])
    struct.pack_into('<hh', bad, 32, 0, 3)
    with pytest.raises(ValueError):
        S.plan_camera(bytes(bad), 3, v[4])


_COS = os.path.expanduser(
    '~/mnt/7th_heaven_nx/mods/CosmosLimitBreak.iro')
_SCR = os.path.join(os.path.dirname(__file__), '..', 'attic', 'dirs', '_scratch', 'cos')


def _cosmos_art():
    os.environ['SEVENTH_NX_FIELD_BG_PAGE_PX'] = '768'
    import field_bg_repack as FR
    import ff7nx_marginart as MA
    src = [(_COS, {'limit break/', 'limit break aa/', 'limit break aaf/'})]
    return MA.provider_source(FR.ArtProvider(src, 768, lambda *a: None))


def test_fxhdlift_off_switch(monkeypatch):
    import ff7nx_fxhdlift as HL
    monkeypatch.setenv(HL.OFF_ENV, '1')
    assert HL.disabled()
    assert HL.apply_to_flevel(None, {}, art=object()) == {
        'names': [], 'refused': []}


def test_fxhdlift_bugin3_meteor_to_truecolor_slot():
    import pickle
    import diag_common as DC
    import ff7nx_fxhdlift as HL
    pk = os.path.join(_SCR, 'bugin3.pkl')
    if not (os.path.exists(pk) and os.path.exists(_COS)):
        pytest.skip('no bugin3 probe or Cosmos archive')
    s9 = pickle.load(open(pk, 'rb'))[8]
    new, (nt, nc, slot) = HL.plan_field('bugin3', s9, (4,), _cosmos_art())
    assert (nt, nc) == (15, 15) and 15 <= slot <= 23
    pages = {p.slot: p for p in DC.parse_pages(new)[0] if p is not None}
    assert pages[slot].depth == 2 and pages[slot].px == 768
    old = {p.slot: p for p in DC.parse_pages(s9)[0] if p is not None}
    for s, p in old.items():                 # every existing page untouched
        assert pages[s].data == p.data
    _l, ts, _te, _px = DC.parse_pages(new)
    moved = [o for _ly, offs in DC.walk_layers(new, new.find(b'BACK'), ts)
             for o in offs if new[o + 28] and new[o + 26] == 4]
    assert len(moved) == 15 and all(new[o + 34] == slot for o in moved)
    with pytest.raises(ValueError):          # second pass refuses
        HL.plan_field('bugin3', new, (4,), _cosmos_art())


def test_cosmosrestore_copies_packed_565():
    import pickle
    import diag_common as DC
    import ff7nx_cosmosrestore as CR
    pk = os.path.join(_SCR, 'cosmo2_built2.pkl')
    ch = os.path.join(CR.CHUNK_DIR, 'cosmo2.chunk.9')
    if not (os.path.exists(pk) and os.path.exists(ch) and
            os.path.exists(_COS)):
        pytest.skip('no cosmo2 probe / chunk / Cosmos archive')
    s9 = pickle.load(open(pk, 'rb'))[8]
    art = _cosmos_art()
    new, n = CR.plan_field('cosmo2', s9, open(ch, 'rb').read(), art)
    assert n > 50
    # every changed 565 value on a truecolor page is one Cosmos's packed
    # page for that slot also contains at the twin cell: spot-check that the
    # changed texels exist and the paletted pages are untouched
    old = {p.slot: p for p in DC.parse_pages(s9)[0] if p is not None}
    nw = {p.slot: p for p in DC.parse_pages(new)[0] if p is not None}
    changed = [s for s in old if old[s].data != nw[s].data]
    assert changed and all(old[s].depth == 2 for s in changed)



def test_beams_pack_or_merge_bounds():
    import ff7nx_beams as B
    flats = [[(1, 2, 3), (1, 2, 4)], [(5, 5, 5), (1, 2, 3)], [(9, 9, 9)]]
    (mixes, assign, remap), merges = B._pack_or_merge(flats, [7, 8], 2)
    for f, q in zip(flats, assign):
        for c in f:
            d = remap.get(c, c)
            assert d in mixes[q]
            assert max(abs(a - b) for a, b in zip(c, d)) <= 1
    assert all(len(m) <= 2 for m in mixes.values())


def test_beams_colne6_no_aliased_tile(monkeypatch):
    import lgp
    import ff7nx_beams as B
    if not os.path.exists(_DUMP):
        pytest.skip('no vanilla dump')
    # the vanilla dump shares the texture id with other pages; the build's
    # repacked field does not -- the record cap is checked at build time
    monkeypatch.setattr(B, 'MAX_RECORDS_PER_PAGE', 10 ** 6)
    arc = lgp.Archive(_DUMP)
    p = list(lgp.split_sections(arc.decompressed(arc.index['colne_6'])))
    _n9, _n4, st = B.plan_field_mode3('colne_6', p[8], p[3])
    assert ('aliased', 0) not in st['modes']
    assert st['modes'].get(('merge_max_levels', 0), 0) <= 1
    assert st['records'] == 207


def test_fxpcstatic_ancnt2_whole_effect_in_place():
    import pickle
    import diag_common as DC
    import ff7nx_fxpcstatic as PS
    pk = os.path.join(os.path.dirname(__file__), '..', 'attic', 'dirs', '_scratch', 'anc',
                      'ancnt2.pkl')
    if not (os.path.exists(pk) and os.path.exists(_COS)):
        pytest.skip('no ancnt2 probe or Cosmos archive')
    s9 = pickle.load(open(pk, 'rb'))[8]
    new, slots = PS.plan_field('ancnt2', s9, _cosmos_art(), mb_cap=35.0,
                               slots=PS.FIELDS['ancnt2'])
    assert slots == [15, 16, 17, 18, 19, 20, 21]
    old = {p.slot: p for p in DC.parse_pages(s9)[0] if p is not None}
    nw = {p.slot: p for p in DC.parse_pages(new)[0] if p is not None}
    assert set(old) == set(nw)                       # no page added
    for s in old:
        if s in slots:
            assert nw[s].depth == 2 and nw[s].px == 768
        else:
            assert nw[s].data == old[s].data
    # every tile record byte-identical: only the texture block changed
    _l, ts, _te, _px = DC.parse_pages(s9)
    assert new[:ts] == s9[:ts]
    with pytest.raises(ValueError):
        PS.plan_field('ancnt2', s9, _cosmos_art(), mb_cap=30.0)


def test_ws_hpin_fship_bridge():
    import ff7nx_ws as WS
    cur = {n: {'left': -170, 'top': -144, 'right': 170, 'bottom': 256}
           for n in ('fship_2', 'fship_22', 'fship_23', 'fship_24',
                     'fship_25', 'fship_3')}
    got = WS.hpin_overrides(cur, env={})
    assert set(got) == {'fship_2', 'fship_22', 'fship_23', 'fship_24',
                        'fship_25'}
    for (old, new) in got.values():
        assert old == (-170, 170)
        assert (new['left'], new['right']) == (-160, 160)
        assert (new['top'], new['bottom']) == (-144, 256)
    assert WS.hpin_overrides(cur, env={'SEVENTH_NX_NO_HPIN': '1'}) == {}
    pinned = {n: dict(v[1]) for n, v in got.items()}
    assert WS.hpin_overrides(pinned, env={}) == {}


def test_fxpcstatic_hyou5_2_blizzard_composite():
    import pickle
    import diag_common as DC
    import ff7nx_fxpcstatic as PS
    pk = os.path.join(os.path.dirname(__file__), '..', 'attic', 'dirs', '_scratch', 'c6',
                      'hyou5_2.pkl')
    if not (os.path.exists(pk) and os.path.exists(_COS)):
        pytest.skip('no hyou5_2 probe or Cosmos archive')
    assert PS.FIELDS['hyou5_2'] == (18,)
    s9 = pickle.load(open(pk, 'rb'))[8]
    new, done = PS.plan_field('hyou5_2', s9, _cosmos_art(), mb_cap=35.0,
                              slots=(18,))
    assert done == [18]
    old = {p.slot: p for p in DC.parse_pages(s9)[0] if p is not None}
    nw = {p.slot: p for p in DC.parse_pages(new)[0] if p is not None}
    assert set(old) == set(nw)
    assert [s for s in old if old[s].data != nw[s].data] == [18]
    assert (nw[18].depth, nw[18].px, nw[18].size_flag) == (2, 768, 1)
    for s in (15, 16, 17):                    # Cosmos-animated: untouched
        assert nw[s].depth == 1
    _l, ts, _te, _px = DC.parse_pages(s9)
    assert new[:ts] == s9[:ts]


def test_trnad4_kuro_1_exact_repeat_with_overlay():
    import lgp
    import diag_common as DC
    import ff7nx_parallaxfill as PF
    import ff7nx_trnad4 as T
    if not os.path.exists(_DUMP):
        pytest.skip('no vanilla dump')
    arc = lgp.Archive(_DUMP)
    v = list(lgp.split_sections(arc.decompressed(arc.index['kuro_1'])))
    n7, n9, st = T.apply_to_sections(v[7], v[8], 'kuro_1')
    assert not st['refusal'] and st['tiles'] == 360
    hdr = PF.trigger_header(n7)
    assert (hdr['bg4_w'], hdr['bg4_h']) == (704, 512)
    m9, st2 = T.relocate_edge_sections(n7, n9, 'kuro_1')
    assert not st2['refusal'] and st2['tiles'] == 80 + 44
    sv = DC.survey(m9)
    lay = [r for r in PF._layers(m9, sv['back_start'], sv['tex_start'])
           if r[0] == 4][0]
    recs = [m9[lay[2] + i * PF.TILE_SIZE:lay[2] + (i + 1) * PF.TILE_SIZE]
            for i in range(lay[3])]
    grid = {struct.unpack_from('<hh', r, 2) for r in recs if not r[26]}
    assert len(grid) == 352                      # no colliding residues
    assert sum(1 for r in recs if r[26]) == 8    # overlay x 4 quadrants
    # the Glacier result is unchanged by the overlay support
    h = list(lgp.split_sections(arc.decompressed(arc.index['hyou3'])))
    a7, a9, s3 = T.apply_to_sections(h[7], h[8], 'hyou3')
    b9, s4 = T.relocate_edge_sections(a7, a9, 'hyou3')
    assert s3['tiles'] == 352 and s4['tiles'] == 80


def test_fxpcstatic_las2_fields():
    import pickle
    import ff7nx_fxpcstatic as PS
    assert PS.FIELDS['las2_1'] is None and PS.FIELDS['las2_3'] == (15,)
    for name, want in (('las2_1', [15, 16, 17, 18, 19]), ('las2_3', [15])):
        pk = os.path.join(os.path.dirname(__file__), '..', 'attic', 'dirs', '_scratch', 'c6',
                          name + '.pkl')
        if not (os.path.exists(pk) and os.path.exists(_COS)):
            pytest.skip('no probe or Cosmos archive')
        s9 = pickle.load(open(pk, 'rb'))[8]
        _new, done = PS.plan_field(name, s9, _cosmos_art(), mb_cap=35.0,
                                   slots=PS.FIELDS[name])
        assert done == want


def test_trnad4_woa_wind_layer3_exact_repeat():
    import lgp
    import ff7nx_parallaxfill as PF
    import ff7nx_trnad4 as T
    if not os.path.exists(_DUMP):
        pytest.skip('no vanilla dump')
    arc = lgp.Archive(_DUMP)
    for name in ('woa_2', 'woa_3'):
        assert T.TARGETS[name] == 3 and name in T.OVERLAY_TARGETS
        assert name not in T.EDGE_TARGETS          # positive speed: no move
        v = list(lgp.split_sections(arc.decompressed(arc.index[name])))
        s9 = PF.apply_to_section9(v[8], v[7], name)[0]
        n7, _n9, st = T.apply_to_sections(v[7], s9, name)
        assert not st['refusal'] and st['tiles'] == 360
        hdr = PF.trigger_header(n7)
        assert (hdr['bg3_w'], hdr['bg3_h']) == (704, 512)
        assert (hdr['bg4_w'], hdr['bg4_h']) == (
            PF.trigger_header(v[7])['bg4_w'], PF.trigger_header(v[7])['bg4_h'])


def test_trnad4_edge_cols_diagnostic_env():
    import ff7nx_trnad4 as T
    env = {T.EDGE_COLS_ENV: 'hyou5_2=7,hyou5_1=0'}
    assert T.edge_move_cols('hyou5_2', env) == 7
    assert T.edge_move_cols('hyou5_1', env) == 0
    assert T.edge_move_cols('hyou3', env) == T.EDGE_MOVE_COLS == 5
    assert T.edge_move_cols('hyou3', {}) == 5
    _frm, cols = T.moved_cols_for(7)
    assert cols[0] == -400 and T.edge_proof(cols) is None


def test_debugwarp_runs_after_every_flevel_pass():
    src = open(nxpaths.src_file('build.py')).read()
    at = src.index('dw_stats = ff7nx_debugwarp.apply_to_flevel(')
    for later in ('ff7nx_trnad4.apply_edge_to_flevel(',
                  'ff7nx_stackorder.apply_to_flevel(',
                  'ff7nx_texpatch.apply_to_flevel(',
                  'ws_stats = _bake_widescreen_ranges('):
        assert src.index(later) < at, later


def test_fxpcstatic_crater_2_pulse_frozen_at_median():
    import pickle
    import diag_common as DC
    import field_bg_native as FN
    import ff7nx_fxpcstatic as PS
    assert PS.FIELDS['crater_2'] is None and 'crater_2' in PS.PULSE_FIELDS
    pk = os.path.join(os.path.dirname(__file__), '..', 'attic', 'dirs', '_scratch', 'c6',
                      'crater_2.pkl')
    if not (os.path.exists(pk) and os.path.exists(_COS)):
        pytest.skip('no probe or Cosmos archive')
    s9 = pickle.load(open(pk, 'rb'))[8]
    art = _cosmos_art()
    new, done = PS.plan_field('crater_2', s9, art, mb_cap=35.0)
    assert done == [15, 16, 17, 18, 19, 20, 21]
    pl, ts, _te, px = DC.parse_pages(new)
    assert {p.slot: p.depth for p in pl if p} == {
        0: 1, 15: 2, 16: 2, 17: 2, 18: 2, 19: 2, 20: 2, 21: 2, 26: 2, 27: 2}
    _old, t0, _t1 = FN.parse_texture_block(s9, px)
    assert new[:t0] == s9[:t0]                    # records/states untouched
    pm = {p.slot: p for p in DC.parse_pages(s9)[0] if p}
    refs = [o for _l, offs in DC.walk_layers(s9, s9.find(b'BACK'), ts)
            for o in offs if s9[o + 28] and s9[o + 34] == 21]
    _img, (h, n) = PS._pulse_composite(art, 'crater_2', s9, 21, pm[21],
                                        px, refs)
    assert n == 12 and h == 'f539605c03a68f69'      # 7th of 12 by luminance


# ---------------------------------------------------------------------------
# BUILD 618k
# ---------------------------------------------------------------------------
_C6 = os.path.join(os.path.dirname(__file__), '..', 'attic', 'dirs', '_scratch', 'c6')


def _cosmos_reader():
    import field_bg_repack as FR
    return FR.IroReader(_COS)


def test_paraudit_finds_the_reported_class_and_passes_the_fixed_fields():
    import pickle
    import lgp
    import ff7nx_paraudit as PA
    if not os.path.exists(_DUMP):
        pytest.skip('no vanilla dump')
    arc = lgp.Archive(_DUMP)
    for name, want in (('sky', True), ('zcoal_1', True)):
        pk = os.path.join(_C6, name + '.pkl')
        if not os.path.exists(pk):
            pytest.skip('no probe')
        v = list(lgp.split_sections(arc.decompressed(arc.index[name])))
        rows = PA.audit_field(name, v, pickle.load(open(pk, 'rb')))
        assert [r['defect'] for r in rows if r['layer'] == 3] == [want]
        fixed = pickle.load(open(os.path.join(_C6, name + '_618k.pkl'),
                                 'rb'))
        rows = PA.audit_field(name, v, fixed)
        assert [(r['built_uncovered'], r['built_doubled'])
                for r in rows if r['layer'] == 3] == [(0.0, 0.0)]


def test_trnad4_auto_targets_cover_16x9_with_no_doubles():
    import lgp
    import ff7nx_paraudit as PA
    import ff7nx_parallaxfill as PF
    import ff7nx_parallaxwide as PW
    import ff7nx_trnad4 as T
    if not (os.path.exists(_DUMP) and os.path.exists(_COS)):
        pytest.skip('no vanilla dump or Cosmos archive')
    assert set(T.AUTO_TARGETS) == {'sky', 'trnad_1', 'trnad_2', 'trnad_3',
                                   'woa_1', 'zcoal_1', 'zcoal_3'}
    arc = lgp.Archive(_DUMP)
    cr = _cosmos_reader()
    moved = {}
    for f in ('trnad_1', 'trnad_2', 'trnad_3', 'woa_1', 'zcoal_1',
              'zcoal_3'):
        v = list(lgp.split_sections(arc.decompressed(arc.index[f])))
        base = list(v)
        base[8] = cr.read('limit break/flevel.lgp/%s.chunk.9' % f) or v[8]
        s9 = PF.apply_to_section9(base[8], base[7], f)[0]
        n7, n9, st = T.apply_to_sections(base[7], s9, f)
        assert not st['refusal'], (f, st['refusal'])
        assert PW.apply_to_section9(n9, n7, f)[0] == n9   # pw skips it now
        r9, est = T.relocate_auto_sections(n7, n9, f, v[0])
        assert not est['refusal']
        moved[f] = est['tiles']
        q = list(base)
        q[7], q[8] = n7, r9
        rows = [r for r in PA.audit_field(f, base, q)
                if r['layer'] == T.AUTO_TARGETS[f]]
        assert [(r['built_uncovered'], r['built_doubled'])
                for r in rows] == [(0.0, 0.0)], f
    # negative-speed fields move columns (and zcoal_3, whose BGSCR speeds
    # are variables, also a row); positive-speed ones need nothing
    assert moved['zcoal_1'] == 32 and moved['zcoal_3'] == 54
    assert moved['trnad_1'] == moved['woa_1'] == 0


def test_trnad4_existing_targets_unchanged_by_618k():
    import ff7nx_trnad4 as T
    for f in ('hyou3', 'kuro_1', 'trnad_4', 'woa_2', 'woa_3'):
        assert f in T.TARGETS and f not in T.AUTO_TARGETS
        assert T.grid_of(f) == (352, 256, -176, -128)
    # the fixed-five-column Glacier relocation keeps exactly its old set
    assert 'hyou3' in T.EDGE_TARGETS and 'kuro_1' in T.EDGE_TARGETS


def test_crater_1_not_filled_and_redrawn_from_chunk():
    import pickle
    import diag_common as DC
    import field_bg_repack as FR
    import ff7nx_chunkparallax as CP
    import ff7nx_framesim as FS
    import ff7nx_parallaxfill as PF
    assert ('crater_1', 3) in PF.NON_TILEABLE_OVERLAYS
    assert ('crater_1', 4) in PF.NON_TILEABLE_OVERLAYS
    pk = os.path.join(_C6, 'crater_1_618k.pkl')
    if not (os.path.exists(pk) and os.path.exists(_COS)):
        pytest.skip('no probe or Cosmos archive')
    p = pickle.load(open(pk, 'rb'))
    pages, _ts, _te, _px = DC.parse_pages(p[8])
    assert all(q.depth == 2 for q in pages if q)          # no 1x page left
    run = sum(FR._page_bytes(q.px, q.depth) for q in pages if q)
    assert run <= 35.0 * 1048576
    f = FS.Field(p)
    rank = {3: 0, 4: 1, 1: 2, 2: 3}                       # BGPDH: wall in front
    for cam in ((-275, 320), (-275, -150), (293, -150), (293, 320), (0, 80)):
        im = f.render(cam=cam, K=1, rank=rank)
        magenta = (im == np.array(FS.MAGENTA, np.uint8)).all(-1)
        assert magenta[:, 1:-1].mean() == 0.0, cam
    # the planner refuses a section that still carries fill copies
    raw = pickle.load(open(os.path.join(_C6, 'crater_1.pkl'), 'rb'))
    with pytest.raises(ValueError):
        CP.plan_field('crater_1', raw[8], _cosmos_reader())


# ---------------------------------------------------------------------------
# BUILD 618l
# ---------------------------------------------------------------------------
_WSCFG = os.path.join(os.path.dirname(__file__), '..', 'cache',
                      'CosmosLimitBreak', 'CONFIG', 'widescreen',
                      'config.toml')


def test_l4offset_matches_ffnx_on_the_coal_train():
    import pickle
    import diag_common as DC
    import ff7nx_l4offset as L
    import ff7nx_ws as WS
    if not os.path.exists(_WSCFG):
        pytest.skip('no Cosmos widescreen config')
    cfg, _mov = WS.load(_WSCFG, None)
    assert L.offsets(cfg) == {'zcoal_1': 56, 'zcoal_3': -54}
    for f, xr in (('zcoal_1', (-96, 64)), ('zcoal_3', (22, 214))):
        pk = os.path.join(_C6, f + '_618k.pkl')
        if not os.path.exists(pk):
            pytest.skip('no probe')
        p = pickle.load(open(pk, 'rb'))
        new9, n = L.plan_field(p, L.offsets(cfg)[f], p[0])
        _pl, ts, _te, _px = DC.parse_pages(new9)
        xs = [struct.unpack_from('<h', new9, o + 2)[0]
              for lay, offs in DC.walk_layers(new9, new9.find(b'BACK'), ts)
              if lay == 4 for o in offs]
        assert len(xs) == n and (min(xs), max(xs) + 32) == xr
    # a wrap/cull difference is refused, not approximated
    p = pickle.load(open(os.path.join(_C6, 'zcoal_3_618k.pkl'), 'rb'))
    with pytest.raises(ValueError):
        L.plan_field(p, -700, p[0])


def test_scrvclip_trnad_1_top_band_and_safety():
    import pickle
    import ff7nx_scrvclip as SV
    pk = os.path.join(_C6, 'trnad_1_618k.pkl')
    if not os.path.exists(pk):
        pytest.skip('no probe')
    p = pickle.load(open(pk, 'rb'))
    edits = SV.plan(p)
    assert [(old, new, op) for _o, old, new, op in edits] == [
        (24, 16, 'SCR2DL'), (24, 16, 'SCR2DL')]
    q, _ = SV.apply_to_parts(p)
    assert SV.plan(q) == []                                # idempotent
    assert len(q[0]) == len(p[0])                          # no op resized
    diff = [i for i in range(len(p[0])) if p[0][i] != q[0][i]]
    assert len(diff) == 2                                  # one byte per op


def test_trnad1fx_transition_copy_fades_and_is_full_width():
    import pickle
    import diag_common as DC
    import ff7nx_framesim as FS
    import ff7nx_marginblack as MB
    import ff7nx_trnad1fx as T
    pk = os.path.join(_C6, 'trnad_1_618k.pkl')
    if not (os.path.exists(pk) and os.path.exists(_COS)):
        pytest.skip('no probe or Cosmos archive')
    p = pickle.load(open(pk, 'rb'))
    new9, new3, info = T.plan_field('trnad_1', p, 63, mb_cap=35.0,
                                    raw_cap=13421772, art=_cosmos_art())
    assert info['removed'] == 840 and info['added'] == 466
    assert info['lights'] == [] and info['slots'] == [18, 19]
    pages, _ts, _te, _px = DC.parse_pages(new9)
    depth = {q.slot: q.depth for q in pages if q}
    assert depth[18] == depth[19] == 1          # paletted: the ramp fades it
    q = list(p)
    q[8], q[3] = new9, new3
    cols, hdr, _n, cpp = MB.palette_colours(new3)
    assert list(cols[1]) == list(cols[2]) and cols[1][0] == 0
    f = FS.Field(q)
    full = f.render(cam=(0, -16), states={63: 2}, K=1, mark=False)
    haze = f.render(cam=(0, -16), states={}, K=1, mark=False)
    assert np.abs(full.astype(int) - haze).mean() > 10   # copy is visible
    dark = bytearray(new3)                       # ramp at -31: all black
    for pal in (1, 2):
        for jj in range(1, 256):
            struct.pack_into('<H', dark, hdr + 2 * (pal * cpp + jj), 0x8000)
    q[3] = bytes(dark)
    gone = FS.Field(q).render(cam=(0, -16), states={63: 2}, K=1, mark=False)
    assert np.abs(gone.astype(int) - haze).mean() < 1.0  # field vanished



# ---------------------------------------------------------------------------
# BUILD 618m
# ---------------------------------------------------------------------------
def test_animdetail_sea_water_moves_like_vanilla():
    import pickle
    import ff7nx_animdetail as AD
    import ff7nx_framesim as FS
    v, b = (os.path.join(_C6, n) for n in ('sea_v.pkl', 'sea.pkl'))
    if not (os.path.exists(v) and os.path.exists(b)):
        pytest.skip('no probe')
    vp, bp = pickle.load(open(v, 'rb')), pickle.load(open(b, 'rb'))
    new9, info = AD.plan_field(vp, bp, 2, 1, mb_cap=35.0, raw_cap=13421772)
    assert info['cells'] == 768 and info['mb'] <= 35.0
    q = list(bp)
    q[8] = new9

    def moving(parts):
        f = FS.Field(parts)
        ims = [f.render(cam=(0, 0), states={1: 1 << k}, K=1,
                        mark=False).astype(int) for k in range(2, 8)]
        return np.mean([(np.abs(ims[0] - x).max(-1) > 12).mean()
                        for x in ims[1:]])
    assert moving(bp) < 0.002                    # before: still water
    assert 0.015 < moving(q) < 0.06              # after: ~ vanilla's 2.8%
    assert abs(moving(q) - moving(vp)) < 0.01


def test_chunkparallax_crater_1_all_layers_and_mound_patch():
    import pickle
    import ff7nx_chunkparallax as CP
    import ff7nx_framesim as FS
    assert CP.FIELDS['crater_1'] == (1, 2, 3, 4)
    pk = os.path.join(_C6, 'crater_1_618m.pkl')
    if not os.path.exists(pk):
        pytest.skip('no probe')
    f = FS.Field(pickle.load(open(pk, 'rb')))
    rank = {3: 0, 4: 1, 1: 2, 2: 3}
    for cam in ((-275, 320), (-275, -150), (293, -150), (330, -150)):
        im = f.render(cam=cam, K=1, rank=rank)
        assert (im == np.array(FS.MAGENTA, np.uint8)).all(-1)[:, 1:-1] \
            .mean() == 0.0


def test_trainlower_zcoal_1_never_reveals_the_bottom():
    import pickle
    import ff7nx_framesim as FS
    import ff7nx_shakepad as S
    import ff7nx_trainlower as TL
    assert S.FIELDS == () and TL.FIELDS == {'zcoal_1': 4, 'zcoal_2': 4}
    pk = os.path.join(_C6, 'zcoal_1_618l.pkl')
    if not os.path.exists(pk):
        pytest.skip('no probe')
    p = pickle.load(open(pk, 'rb'))
    s1, s9, info = TL.plan(p, 4)
    assert abs(info['models_dy'] - 4) < 0.5
    q = list(p)
    q[1], q[8] = s1, s9
    st = {1: 1, 2: 1, 3: 1, 5: 1}
    f, g = FS.Field(q), FS.Field(p)
    cx = (f.hdr['cam_left'] + f.hdr['cam_right']) // 2
    for up in (1, 2, 3, 4):                      # content moved up by `up`
        a = g.render(cam=(cx, 8 + up), states=st, K=1, layers=(1, 2))
        b = f.render(cam=(cx, 8 + up), states=st, K=1, layers=(1, 2))
        mg = np.array(FS.MAGENTA, np.uint8)
        assert (a == mg).all(-1)[-1].mean() == 1.0          # before: gap
        assert (b == mg).all(-1)[-1].mean() < 0.65          # after: art
    # the mountains (layer 3) do not move
    assert g.render(cam=(cx, 8), K=1, layers=(3,)).tobytes() == \
        f.render(cam=(cx, 8), K=1, layers=(3,)).tobytes()



def test_trainlower_zcoal_2_two_cameras():
    import pickle
    import ff7nx_framesim as FS
    import ff7nx_trainlower as TL
    pk = os.path.join(_C6, 'zcoal_2.pkl')
    if not os.path.exists(pk):
        pytest.skip('no probe')
    p = pickle.load(open(pk, 'rb'))
    assert len(p[1]) == 76                       # two cameras
    s1, s9, info = TL.plan(p, 4)
    assert len(s1) == 76 and abs(info['models_dy'] - 4) < 0.5
    q = list(p)
    q[1], q[8] = s1, s9
    st = {1: 1}
    f, g = FS.Field(q), FS.Field(p)
    cx = (f.hdr['cam_left'] + f.hdr['cam_right']) // 2
    mg = np.array(FS.MAGENTA, np.uint8)
    rest = (g.render(cam=(cx, 8), states=st, K=1, layers=(1, 2)) == mg) \
        .all(-1)[-1].mean()
    for up in (3, 4):
        a = g.render(cam=(cx, 8 + up), states=st, K=1, layers=(1, 2))
        b = f.render(cam=(cx, 8 + up), states=st, K=1, layers=(1, 2))
        assert (a == mg).all(-1)[-1].mean() == 1.0
        assert (b == mg).all(-1)[-1].mean() <= rest + 1e-9
    assert g.render(cam=(cx, 8), K=1, layers=(3,)).tobytes() == \
        f.render(cam=(cx, 8), K=1, layers=(3,)).tobytes()


def test_parallaxfill_skips_camera_locked_overlays():
    """618p: las0_3 fog (and the 5 other camera-locked 1024-period layers)
    keep their vanilla record count; a short-period grid is unaffected."""
    import lgp
    import diag_common as DC
    import ff7nx_parallaxfill as PF
    if not os.path.exists(_DUMP):
        pytest.skip('no vanilla dump')
    arc = lgp.Archive(_DUMP)

    def counts(s9):
        sv = DC.survey(s9)
        return {r[0]: r[3] for r in PF._layers(s9, sv['back_start'],
                                               sv['tex_start'])}
    want = {('las0_3', 4), ('del3', 4), ('junair', 3), ('junair', 4),
            ('junonl2', 3), ('junonr2', 3)}
    for name in ('las0_3', 'del3', 'junair', 'junonl2', 'junonr2'):
        v = list(lgp.split_sections(arc.decompressed(arc.index[name])))
        hdr = PF.trigger_header(v[7])
        s9 = PF.apply_to_section9(v[8], v[7], name)[0]
        a, b = counts(v[8]), counts(s9)
        for layer in (3, 4):
            if (name, layer) in want:
                assert PF.camera_locked_overlay(hdr, layer)
                assert a[layer] == b[layer]
    v = list(lgp.split_sections(arc.decompressed(arc.index['trnad_3'])))
    assert not PF.camera_locked_overlay(PF.trigger_header(v[7]), 4)


def test_animdetail_ithill_mirror_under():
    """618p: ithill water gets vanilla-sized motion on the HD layer-1 base;
    the 68 Cosmos-only 16:9 cells are mirrored; sea is unchanged."""
    import pickle
    import ff7nx_animdetail as AD
    import ff7nx_framesim as FS
    pv, pb = (os.path.join(_C6, 'ithill%s.pkl' % s) for s in ('_v', ''))
    if not (os.path.exists(pv) and os.path.exists(pb)):
        pytest.skip('no probe')
    v, b = pickle.load(open(pv, 'rb')), pickle.load(open(pb, 'rb'))
    assert AD.FIELDS['ithill'] == (2, 1) and AD.UNDER['ithill'] == 1
    with pytest.raises(ValueError):
        AD.plan_field(v, b, 2, 1)                 # positions differ
    s9, info = AD.plan_field(v, b, 2, 1, mb_cap=35.0, raw_cap=13421772,
                             mirror=True, under=1)
    assert info['cells'] == 750 and info['mirrored'] == 68
    assert info['mb'] <= 35.0
    q = list(b)
    q[8] = s9

    def changed(parts, a, c):
        f = FS.Field(parts)
        h = f.hdr
        cam = ((h['cam_left'] + h['cam_right']) // 2,
               (h['cam_top'] + h['cam_bottom']) // 2)
        x, y = (f.render(cam=cam, K=1, states={1: s}).astype(int)
                for s in (a, c))
        return float((np.abs(x - y).max(-1) > 24).mean())
    # vanilla: each phase changes 1-5% of the screen; the build was 0% between
    # states and 13% at the on/off step
    for a, c in ((0, 1), (1, 2), (2, 6)):
        assert 0.005 < changed(q, a, c) < 0.06
    assert changed(b, 1, 2) < 0.001


def test_animdetail_sea_unchanged_by_618p():
    import pickle
    import ff7nx_animdetail as AD
    pk = [os.path.join(_C6, 'sea%s.pkl' % s) for s in ('_v', '', '_618n')]
    if not all(os.path.exists(p) for p in pk):
        pytest.skip('no probe')
    v, b, n = (pickle.load(open(p, 'rb')) for p in pk)
    assert AD.plan_field(v, b, 2, 1, mb_cap=35.0)[0] == n[8]


def test_animdetail_ithill_618q_no_motion_on_wood():
    """618q: no vanilla motion on the beams' edges or on the wood in the
    right 16:9 strip (hardware: yellow beam outline, water in the corner)."""
    import pickle
    import ff7nx_animdetail as AD
    import ff7nx_framesim as FS
    pv, pb = (os.path.join(_C6, 'ithill%s.pkl' % s) for s in ('_v', ''))
    if not (os.path.exists(pv) and os.path.exists(pb)):
        pytest.skip('no probe')
    v, b = pickle.load(open(pv, 'rb')), pickle.load(open(pb, 'rb'))
    s9, info = AD.plan_field(v, b, 2, 1, mb_cap=35.0, raw_cap=13421772,
                             mirror=True, under=1)
    q = list(b)
    q[8] = s9
    f = FS.Field(q)
    a = [f.render(cam=(0, 0), K=3, states={1: s}).astype(int)
         for s in (0, 1, 2, 6, 4)]
    d = np.max([np.abs(x - a[0]).max(-1) for x in a[1:]], 0)
    # the wood plank at game (178, 66) and the beam outline at (-80, -63)
    for gx, gy in ((178, 66), (-80, -63)):
        X, Y = int((gx + 213.5) * 3), int((gy + 120) * 3)
        assert d[Y - 6:Y + 6, X - 6:X + 6].max() <= 20


def test_underfill_las0_3_reorder_and_gap():
    import pickle
    import ff7nx_framesim as FS
    import ff7nx_underfill as UF
    pk = os.path.join(_C6, 'las0_3.pkl')
    if not os.path.exists(pk):
        pytest.skip('no probe')
    p = pickle.load(open(pk, 'rb'))
    q = list(p)
    q[8], n = UF.plan_reorder(q, 2, 2, UF.REORDER['las0_3'][2])
    assert n == 99
    q[8], info = UF.plan_field(q, 2, 2, 1)
    assert info['records'] == 99 and info['free_left'] >= 0
    f = FS.Field(q)
    gap = f.render(cam=(0, -16), K=1, states={2: 0}, layers=(1, 2))
    s0 = f.render(cam=(0, -16), K=1, states={2: 1}, layers=(1, 2))
    assert (np.abs(gap.astype(int) - s0.astype(int)).max(-1) > 8).mean() \
        < 0.001
    # page memory unchanged
    import diag_common as DC
    import field_bg_repack as FR
    mb = lambda s9: sum(FR._page_bytes(x.px, x.depth)
                        for x in DC.parse_pages(s9)[0] if x is not None)
    assert mb(q[8]) == mb(p[8])


def test_fxsmooth_del3_outline_only():
    """618r: only the surf's outline changes (under 1.5% of each FX page
    by more than 16/255); memory and records untouched."""
    import pickle
    import ff7nx_framesim as FS
    import ff7nx_fxsmooth as SM
    pk = os.path.join(_C6, 'del3_fx.pkl')
    if not os.path.exists(pk):
        pytest.skip('no probe')
    p = pickle.load(open(pk, 'rb'))
    s9, info = SM.plan_field(p[8])
    assert len(s9) == len(p[8]) and set(info['slots']) == set(range(16, 24))
    q = list(p)
    q[8] = s9
    a, b = FS.Field(p), FS.Field(q)
    for s in info['slots']:
        x, _ = FS._d2(a.arr[s])
        y, _ = FS._d2(b.arr[s])
        assert (np.abs(x - y).max(-1) > 16).mean() < 0.015
    ia = [t for t in a.tiles]
    assert ia == [t for t in b.tiles]


def test_stepsextra_del3_sea_routes():
    """618s: del3's sea triangles route to Cosmo's wading set; the field
    fallback stays Cosmo's sand."""
    import ff7nx_stepsextra as SE
    import ff7nx_fieldsteps_data as FD
    if not os.path.exists(_DUMP):
        pytest.skip('no vanilla dump')
    t = SE.config_text({i: 1 for i in range(5000, 5200)})
    assert t.count('[del3_') == 71
    base = '[del3_159]\nsequential = [ 5110, 5111, 5112, 5113, 5114, 5115 ]\n'
    r = FD.parse_configs([base, t], _DUMP)
    assert r[('del3', None)] == (5110, 5111, 5112, 5113, 5114, 5115)
    assert r[('del3', 300)] == SE.WADE
    assert SE.config_text({5130: 1}) == ''        # wade set missing -> off


def test_fxpcstatic_mrkt2_junbin5():
    import pickle
    import diag_common as DC
    import field_bg_repack as FR
    import ff7nx_fxpcstatic as PS
    try:
        import preflight_marginart as PFM
        import ff7nx_marginart as MA
        art = MA.provider_source(PFM.provider(768))
    except Exception:                                          # noqa: BLE001
        pytest.skip('no Cosmos provider')
    for n, want in (('mrkt2', [15]), ('junbin5', [15, 18, 19])):
        pk = os.path.join(_C6, '%s.pkl' % n)
        if not os.path.exists(pk):
            pytest.skip('no probe')
        p = pickle.load(open(pk, 'rb'))
        other = sum(len(x) for x in p) - len(p[8])
        new, done = PS.plan_field(n, p[8], art,
                                  mb_cap=PS.MB_OVERRIDE.get(n, 35.0),
                                  other_bytes=other, raw_cap=13421772,
                                  slots=PS.FIELDS[n], sec3=p[3])
        assert done == want
        pl = DC.parse_pages(new)[0]
        assert all(q.depth == 2 for q in pl if q is not None
                   and 15 <= q.slot < 24 and q.slot in want)
        assert sum(FR._page_bytes(q.px, q.depth) for q in pl
                   if q is not None) <= 35.3 * 1048576


def test_fxrequant_refuses_cross_palette_loads():
    """618t: mtnvl3 loads palette 9's buffer into palette 10; requantising
    palette 10 drew garbage (hardware). ghotel still qualifies."""
    import lgp
    import ff7nx_fxrequant as RQ
    if not os.path.exists(_DUMP):
        pytest.skip('no vanilla dump')
    arc = lgp.Archive(_DUMP)
    sc = lambda n: list(lgp.split_sections(arc.decompressed(arc.index[n])))[0]
    assert RQ.uniform_palettes(sc('mtnvl3')) == {}
    assert RQ.uniform_palettes(sc('ghotel')) == {8: 255, 11: 255}
    assert RQ.uniform_palettes(sc('uutai1'))          # short loads kept


def _cosmos_art768():
    """The provider the BUILD uses: only the option folders 7th Heaven
    activates (618u: an all-folders provider sees `AA REMOVED` frames the
    build never installs, which is how 618t's dry run disagreed with the
    real build)."""
    import sys
    sys.path.insert(0, _C6)
    try:
        import buildart
        return buildart.build_art(768)
    except Exception:                                          # noqa: BLE001
        pytest.skip('no Cosmos provider')


def test_fxcomplete_618u_rules():
    import lgp
    import ff7nx_fxcomplete as FC
    import ff7nx_palanim as PA
    art = _cosmos_art768()
    if not os.path.exists(_DUMP):
        pytest.skip('no vanilla dump')
    arc = lgp.Archive(_DUMP)

    def plan(n):
        p = list(lgp.split_sections(arc.decompressed(arc.index[n])))
        s9 = p[8]
        if n in FC.BLANK:
            s9, _g = FC.drop_blank(s9, FC.BLANK[n])
        return FC.plan_field(n, s9, art, PA.animated_palettes(p[0]), 999.0,
                             rot=FC.rotates(p[0]), sec3=p[3],
                             once=FC.set_once(p[0]))
    _o, done, refused, _mb = plan('icedun_2')
    assert set(done) == {15, 16} and not refused
    _o, done, refused, _mb = plan('eals_1')
    assert 15 in refused and 'cycle' in refused[15]
    with pytest.raises(ValueError):
        plan('mtnvl3')
    assert {'kuro_1', 'mrkt2', 'junbin5', 'del3'} <= FC.CONFIRMED


def test_fxrequant_mtnvl3_group():
    import pickle
    import ff7nx_fxrequant as RQ
    art = _cosmos_art768()
    pk = os.path.join(_C6, 'mtnvl3_hw.pkl')
    if not os.path.exists(pk):
        pytest.skip('no probe')
    p = pickle.load(open(pk, 'rb'))
    assert RQ.single_source_group(p[0]) == (9, {9, 10})
    out, st = RQ.plan_field('mtnvl3', p, art)
    assert st['palettes'] == [9] and st['cells'] == 496
    assert st['err_after'] < st['err_before']


def test_fxpcstatic_junbin5_lamp_stays_paletted():
    """618u: every palette-9 (lamp) record ends on a paletted page."""
    import pickle
    import ff7nx_framesim as FS
    import ff7nx_fxpcstatic as PS
    art = _cosmos_art768()
    pk = os.path.join(_C6, 'junbin5.pkl')
    if not os.path.exists(pk):
        pytest.skip('no probe')
    p = pickle.load(open(pk, 'rb'))
    other = sum(len(x) for x in p) - len(p[8])
    new, done = PS.plan_field('junbin5', p[8], art, mb_cap=35.3,
                              other_bytes=other, raw_cap=13421772,
                              slots=PS.FIELDS['junbin5'], sec3=p[3])
    q = list(p)
    q[8] = new
    f = FS.Field(q)
    s9 = f.sec9
    lamp = [f.pm[s9[o + 34]].depth for _l, o in f.tiles
            if s9[o + 28] and s9[o + 22] == 9]
    assert len(lamp) == 15 and set(lamp) == {1}


def test_fxpcstatic_junbin5_ramp_bloom():
    """618t: palette 5/6 bloom states light the cells (vanilla does)."""
    import pickle
    import ff7nx_framesim as FS
    import ff7nx_fxpcstatic as PS
    art = _cosmos_art768()
    pk = os.path.join(_C6, 'junbin5.pkl')
    if not os.path.exists(pk):
        pytest.skip('no probe')
    p = pickle.load(open(pk, 'rb'))
    other = sum(len(x) for x in p) - len(p[8])
    new, done = PS.plan_field('junbin5', p[8], art, mb_cap=35.3,
                              other_bytes=other, raw_cap=13421772,
                              slots=PS.FIELDS['junbin5'], sec3=p[3])
    q = list(p)
    q[8] = new
    f = FS.Field(q)
    h = f.hdr
    cam = ((h['cam_left'] + h['cam_right']) // 2,
           (h['cam_top'] + h['cam_bottom']) // 2)
    st = lambda b: {1: 1, 7: 2, 2: 2, 3: b, 4: b, 5: b, 6: b}
    a = f.render(cam=cam, K=1, states=st(1), mark=False).astype(int)
    b = f.render(cam=cam, K=1, states=st(16), mark=False).astype(int)
    assert (np.abs(a - b).max(-1) > 24).mean() > 0.01     # states differ


def test_d1_lift_runs_after_every_page_pass():
    """618v: the 512px depth-1 lift must follow every pass that parses
    depth-1 pages at 256 (it used to sit ~45 passes too early)."""
    src = open(nxpaths.src_file('build.py')).read()
    lift = src.index('    _lift_depth1_payloads(archive, payloads, log)')
    for p in ('ff7nx_fxpcstatic.apply_to_flevel', 'ff7nx_fxcomplete.apply_to_flevel',
              'ff7nx_fxtrial.apply_to_flevel', 'ff7nx_fxsmooth.apply_to_flevel',
              'ff7nx_texpatch.apply_to_flevel', 'ff7nx_trainlower.apply_to_flevel'):
        assert src.index(p) < lift, p
    assert lift < src.index('ff7nx_debugwarp.apply_to_flevel(')


def test_fxd1art_off_switch_returns_base(monkeypatch):
    import ff7nx_fxd1art as D1
    monkeypatch.setenv(D1.OFF_ENV, '1')
    sentinel = object()
    assert D1.compose('x', [b''] * 9, base=sentinel) is sentinel


@pytest.mark.parametrize('name,pk', [('junbin5', 'junbin5_hw2'),
                                     ('mtnvl3', 'mtnvl3_hw2'),
                                     ('eals_1', 'eals_1_hw2')])
def test_fxd1art_lift_keeps_walk_and_only_touches_fx(name, pk):
    """618v: lifted with the FX art, the section re-parses at 512 with the
    same pages; only FX pages differ from 2x replication; eals_1 (MPPAL2
    cycle) gets neighbourhood-only indices."""
    import pickle
    import diag_common as DC
    import field_bg_native as FN
    import ff7nx_fxd1art as D1
    path = os.path.join(_C6, pk + '.pkl')
    if not os.path.exists(path):
        pytest.skip('no probe')
    p = list(pickle.load(open(path, 'rb')))
    D1.arm(_cosmos_art768())
    D1.STATS.clear()
    pl, _ts, _te, px = DC.parse_pages(p[8])
    new9, k = FN.lift_depth1(p[8], px, 256, 512, art=D1.compose(name, p))
    assert k
    keep = FN.D1_PAGE_PX
    FN.D1_PAGE_PX = 512
    try:
        pl2 = DC.parse_pages(new9)[0]
    finally:
        FN.D1_PAGE_PX = keep
    fx = 0
    for a, b in zip(pl, pl2):
        assert (a is None) == (b is None)
        if a is None:
            continue
        assert (a.slot, a.depth) == (b.slot, b.depth)
        if a.depth == 2:
            assert a.data == b.data
            continue
        x = np.frombuffer(a.data, np.uint8, count=65536).reshape(256, 256)
        y = np.frombuffer(b.data, np.uint8, count=262144).reshape(512, 512)
        if not (np.repeat(np.repeat(x, 2, 0), 2, 1) == y).all():
            assert 15 <= a.slot < 24
            fx += 1
    assert fx
    if name == 'eals_1':
        assert D1.STATS['full'] == 0 and D1.STATS['neigh'] > 0


@pytest.mark.parametrize('name', ['junbin5', 'eals_1'])
def test_palstates_script_patches_apply_to_vanilla(name):
    """618w: every palette-op patch matches exactly once (twice for eals_1's
    spray) in the vanilla script, keeps its length, and the patched script
    still decodes routine by routine."""
    import pickle
    import echo_s_flevel as ES
    import ff7nx_palstates as P
    path = os.path.join(_C6, name + '_v.pkl')
    if not os.path.exists(path):
        pytest.skip('no vanilla probe')
    s0 = pickle.load(open(path, 'rb'))[0]
    new = P.patch_script(name, s0)
    assert len(new) == len(s0) and new != s0
    for a, b in ES._routine_blocks(new):
        assert not ES._decode_block(new, a, b)[1]
    assert b'\xe9\x00\x55\x50\x20\x30' not in new          # junbin5 ADPAL
    assert b'\xdf\x05\x55' not in new and b'\xdf\x50' not in new


@pytest.mark.parametrize('name,slots_gone', [('junbin5', (16, 17)),
                                             ('eals_1', ())])
def test_palstates_plan_moves_effects_to_truecolor(name, slots_gone):
    """618w: no FX record samples the animated palettes any more; each state
    has its own param/state copies on truecolor FX pages; no page binds more
    than 256 tiles; no paletted FX page is left."""
    import pickle
    import struct as _st
    import diag_common as DC
    import field_bg_pagecap as PC
    import ff7nx_palstates as P
    pk = os.path.join(_C6, name + '_hw2.pkl')
    vk = os.path.join(_C6, name + '_v.pkl')
    if not (os.path.exists(pk) and os.path.exists(vk)):
        pytest.skip('no probe')
    p = list(pickle.load(open(pk, 'rb')))
    v = list(pickle.load(open(vk, 'rb')))
    s0, s9, info = P.plan_field(name, p, v, _cosmos_art768(), mb_cap=35.3,
                                raw_cap=13421772)
    pl, ts, _te, px = DC.parse_pages(s9)
    pm = {g.slot: g for g in pl if g is not None}
    qs = {sp['palette'] for sp in P.SPECS[name]}
    params = {}
    for _l, offs in DC.walk_layers(s9, s9.find(b'BACK'), ts):
        for o in offs:
            if s9[o + 28] and s9[o + 22] in qs:
                assert pm[s9[o + 34]].depth == 2
                params[(s9[o + 26], s9[o + 27])] = params.get(
                    (s9[o + 26], s9[o + 27]), 0) + 1
    for sp in P.SPECS[name]:
        if sp['mode'] == 'levels':
            for st in sp['states']:
                assert params.get((sp['param'], 1 << st))
        else:
            for a in range(sp['na']):
                assert params.get((sp['pa'] + a, 1))
            for b in range(sp['nb']):
                assert params.get((sp['pb'] + b, 1))
    assert not [s for s, g in pm.items() if g.depth == 1 and 15 <= s < 24]
    for s in slots_gone:
        assert s not in pm
    assert max(PC.effective_counts(s9, px).values()) <= 256
    assert info['mb'] <= 35.3


def test_opbanks_layouts_match_known_ops():
    """618x: the generated operand table agrees with layouts used by hand."""
    import ff7nx_opbanks as OB
    assert OB.OPS[0x80] == (4, ((2, 1, 1, 'hi'), (3, 1, 1, 'lo')))
    assert OB.OPS[0x14][0] == 6 and OB.OPS[0x15][0] == 7
    assert OB.OPS[0x95] == (3, ((2, 1, 1, 'byte'),))
    assert OB.OPS[0xDF][0] == 11 and OB.OPS[0xE9][0] == 10
    assert 0x0F not in OB.OPS and 0x28 not in OB.OPS


def test_palauto_renumber_keeps_comparisons():
    import ff7nx_palauto as A
    # v in 54..74 step 4 -> 0..5; `v > 56` keeps exactly {58..74}
    assert A._cmp_map(56, 2, 54, 4, 'l') == 0
    assert A._cmp_map(74, 3, 54, 4, 'l') == 5
    assert A._cmp_map(70, 0, 54, 4, 'l') == 4
    assert A._cmp_map(71, 0, 54, 4, 'l') is None
    core = A.bg_multi(20, 0x01, 0xFF, 7)
    assert len(core) == 20 and core[:4] == bytes((0xE1, 0x50, 0xFF, 0))
    assert len(A.bg_multi(15, 1, 0xFF, 7)) == 15
    assert len(A.bg_fill(15, 8, 0x29)) == 15


def test_palauto_reproduces_junbin5_by_itself():
    """618x: the automatic pass finds junbin5's lamp (palette 9, 6 states)
    without the hand-made spec, with a script equivalent to it."""
    import pickle
    import echo_s_flevel as ES
    import ff7nx_palauto as A
    pk = os.path.join(_C6, 'junbin5_hw2.pkl')
    vk = os.path.join(_C6, 'junbin5_v.pkl')
    if not (os.path.exists(pk) and os.path.exists(vk)):
        pytest.skip('no probe')
    p = list(pickle.load(open(pk, 'rb')))
    v = list(pickle.load(open(vk, 'rb')))
    s0, s9, info = A.plan_field('junbin5', p, v, _cosmos_art768(),
                                mb_cap=35.3, raw_cap=13421772)
    assert info['auto'] == ['palette 9: var5[41] 250..255 step 1 -> 6 '
                            'states, param 8']
    assert b'\xe9\x00\x55\x50\x20\x30' not in s0
    for a, b in ES._routine_blocks(s0):
        assert not ES._decode_block(s0, a, b)[1]


def test_palauto_gldelev_one_param_per_state():
    """618x: a 30-state breathing pulse (gldelev) becomes params 34..63 and
    a counter renumbered onto them, from the vanilla script."""
    import lgp
    import ff7nx_palauto as A
    van = os.path.join(os.path.dirname(__file__), '..',
                       'dump/romfs/ff7/workingdir/data/field/flevel.lgp')
    if not os.path.exists(van):
        pytest.skip('no vanilla flevel')
    arc = lgp.Archive(van)
    s0 = lgp.split_sections(arc.decompressed(arc.index['gldelev']))[0]
    ef = A.find_effects(s0)
    assert [e['q'] for e in ef] == [2] and len(ef[0]['sites']) == 2
    uses = A.var_uses(s0, ef[0]['var'], skip={o for o, _l in ef[0]['sites']})
    step, anchor, lo, hi, sets = A.lattice(s0, uses)
    assert (step, lo, hi) == (2, 8, 66)


def test_edgeclean_las4_2_removes_specks_keeps_inside():
    """618y: specks off, rim recoloured, interior texels untouched."""
    import pickle
    import diag_common as DC
    import ff7nx_edgeclean as EC
    pk = os.path.join(_C6, 'las4_2_built.pkl')
    if not os.path.exists(pk):
        pytest.skip('no probe')
    p = pickle.load(open(pk, 'rb'))
    new, st = EC.plan_field(p[8])
    assert st['specks'] > 0 and st['rim'] > 0
    a = {g.slot: g for g in DC.parse_pages(p[8])[0] if g}
    b = {g.slot: g for g in DC.parse_pages(new)[0] if g}
    assert set(a) == set(b)
    assert len(new) == len(p[8])


def test_edgeclean_las4_2_green_lsb_clear_no_blue_cast():
    """618z: las4_2's stones came out blue on hardware (build 386): the
    Cosmos-colour path wrote odd-green texels, and the port's 565->1555
    widening moves green's low bit into blue's top bit. Every texel the
    pass writes keeps that bit clear, and the stones' darks stay neutral."""
    import pickle
    import numpy as np
    import diag_common as DC
    import ff7nx_edgeclean as EC
    pk = os.path.join(_C6, 'las4_2_built.pkl')
    pv = os.path.join(_C6, 'las4_2_386.pkl')
    if not (os.path.exists(pk) and os.path.exists(pv)):
        pytest.skip('no probe')
    p = pickle.load(open(pk, 'rb'))
    v = pickle.load(open(pv, 'rb'))['v']
    new, st = EC.plan_field(p[8], 'las4_2', v, _cosmos_art768())
    assert st['cosmos'] > 100
    for g in DC.parse_pages(new)[0]:
        if g is not None and g.depth == 2:
            a = np.frombuffer(g.data, '<u2')
            assert not ((a & 0x20) != 0).any(), g.slot
            if g.slot == 26:
                w = (((a & 0xF800) >> 1) | ((a & 0x07E0) >> 1)
                     | (a & 0x1F))          # what the port draws (1555)
                b = (w & 31).astype(float)
                r = ((w >> 10) & 31).astype(float)
                lit = a != 0
                assert b[lit].mean() < r[lit].mean() + 2


def test_moviefill_cave_and_bars_gate():
    """618y: with the fill on, the movie-quad cave tests BGMOVIE_flag and
    writes the four x slots; the bars cave skips on the same flag."""
    import ff7nx_moviealign as MA
    import ff7nx_moviebars as MB
    words = MA.fill_cave(0.75)(0x1000, lambda i: 0x1000 + 4 * i)
    assert words[:10] == MA.cave_body()
    assert words[-2] == MA.DISPLACED
    assert abs(MA.fill_margin(0.75) - 106.6667) < 1e-3
    assert MB.N_WORDS == 91 and MB.L_FLAG == 12


def _pk(name):
    import pickle
    path = os.path.join(_C6, name + '.pkl')
    if not os.path.exists(path):
        pytest.skip('no probe ' + name)
    return list(pickle.load(open(path, 'rb')))


def test_fxrestore_whitein_beams_come_back():
    """618y: whitein's lost beam cells (lit in vanilla, painted from Cosmos)
    are restored; nothing else in the section changes size."""
    import ff7nx_fxrestore as R
    b, v = _pk('whitein_618x'), _pk('whitein_v')
    s9, st = R.plan_field('whitein', b, v, _cosmos_art768())
    assert st['cells'] >= 55 and len(s9) == len(b[8])
    assert 'whitein' not in R._skip() and 'junbin5' in R._skip()


def test_fxholes_woa_1_dashes_filled():
    import ff7nx_fxholes as H
    s9, n = H.plan_field(_pk('woa_1_618x'))
    assert 20 <= n <= 140             # 618z: + the index-109 glyph (48 max)


def test_trnad4_trnad_3_repeat_now_applies():
    """618y: the exact repeat is no longer refused by an unrelated page."""
    import ff7nx_paraudit as PA
    import ff7nx_trnad4 as T
    b, v = _pk('trnad_3_618x'), _pk('trnad_3_v')
    a7, a9, st = T.apply_to_sections(b[7], b[8], 'trnad_3')
    assert not st['refusal'] and st['tiles'] == 396
    q = list(b)
    q[7], q[8] = a7, a9
    row = PA.audit_field('trnad_3', v, q)[0]
    assert row['built_uncovered'] == 0.0 and row['built_doubled'] == 0.0


def test_chardds_grcf_strip_clean_only_touches_its_band():
    import numpy as np
    import ff7nx_chardds as C
    rng = np.random.default_rng(1)
    img = np.full((1024, 1024, 4), 200, np.uint8)
    img[30, 100:110, :3] = 0                       # a squiggle in the band
    img[600, 100:110, :3] = 0                      # dark texels elsewhere
    out = C.PREPROCESS['grcf.tex'](img)
    assert out[30, 100:110, :3].min() > 150
    assert (out[600, 100:110, :3] == 0).all()


def test_moviefill_zooms_y_too():
    import ff7nx_moviealign as MA
    import a64 as A
    words = MA.fill_cave(0.75)(0x1000, lambda i: 0x1000 + 4 * i)
    assert A.fmul_s(4, 4, 3) in words and A.fadd_s(2, 0, 1) in words


# ---------------------------------------------------------------- BUILD 618z

def _pk386(name):
    import pickle
    pk = os.path.join(_C6, '%s_386.pkl' % name)
    if not os.path.exists(pk):
        pytest.skip('no 386 probe')
    return pickle.load(open(pk, 'rb'))


def test_fxholes_618z_glyph_filled_wedges_kept():
    """woa_1's steam frame (page 15 cell (0,11), palette 2) had a ~30-texel
    glyph of index 109, which palette 2 maps to colour 0: filled. An
    index-0 wedge of the same size (zz6's sparkle) is not."""
    import numpy as np
    import ff7nx_fxholes as H
    vals = np.full((16, 16), 5, np.uint8)
    lit = np.ones((16, 16), bool)
    vals[4:9, 4:9] = 109
    lit[4:9, 4:9] = False
    br = np.full((16, 16), 90.0, np.float32)
    new, n = H._fill_cell(vals, lit, br, H.MAX_HOLE)
    assert n == 25 and (new[4:9, 4:9] == 5).all()
    vals[4:9, 4:9] = 0
    new, n = H._fill_cell(vals, lit, br, H.MAX_HOLE)
    assert n == 0
    d = _pk386('woa_1')
    s9, n = H.plan_field(d['b'], d['v'])
    assert n >= 30


def test_fxrestore_618z_whitein_animated_beams_from_vanilla():
    """whitein's beam palettes are animated (Cosmos hash frames): the dark
    and the over-bright cells come back from vanilla's indices; scoped to
    whitein (uutai1 etc. untouched)."""
    import ff7nx_fxrestore as R
    d = _pk386('whitein')
    s9, st = R.plan_field('whitein', d['b'], d['v'], _cosmos_art768())
    assert st['animated'] >= 100 and len(s9) == len(d['b'][8])
    assert R.ANIM_FIELDS == ('whitein',)


def test_bandbake_whitein_same_picture():
    """The glass band's additive overlay is baked into layer 1's black hole:
    tens of thousands of texels move, the software picture does not
    (only the 8/255 lift under the band changes)."""
    import numpy as np
    import ff7nx_bandbake as BB
    import ff7nx_framesim as FS
    d = _pk386('whitein')
    s9, n = BB.plan_field(d['b'])
    assert n > 20000
    q = list(d['b'])
    q[8] = s9
    a = FS.Field(d['b']).render(cam=(32, 60), states={1: 2}, K=1, mark=False)
    b = FS.Field(q).render(cam=(32, 60), states={1: 2}, K=1, mark=False)
    assert np.abs(a.astype(int) - b).max() <= 8
    assert BB.FIELDS == ('whitein', 'ujun_w')


def test_fxrestore_618z_whitein_margin_palettes():
    """whitein's state-2 margin beam records (no vanilla twin) carried state
    1's palette 9 and a dim Cosmos frame: they take palette 10 and the beam
    continued along its own direction; the band's lower edge gets new
    records where it leaves the old rows (no cut at y = 96)."""
    import struct
    import numpy as np
    import diag_common as DC
    import ff7nx_fxrestore as R
    import ff7nx_framesim as FS
    d = _pk386('whitein')
    s9, n = R.margin_palettes('whitein', d['b'], d['v'])
    assert 28 <= n <= 60                 # 20 state-1 + 28 state-2 + new
    pal_of = {}
    pl, ts, _te, _px = DC.parse_pages(s9)
    for layer, offs in DC.walk_layers(s9, s9.find(b'BACK'), ts):
        for o in offs:
            if s9[o + 26] == 1 and s9[o + 27] == 2:
                pal_of[struct.unpack_from('<hh', s9, o + 2)] = s9[o + 22]
    assert set(pal_of.values()) == {10}
    q = list(d['b'])
    q[8] = s9
    F = FS.Field(q)
    a = (F.render(cam=(32, 60), states={1: 2}, K=1, mark=False).astype(int)
         - F.render(cam=(32, 60), states={1: 0}, K=1, mark=False).astype(int))
    a = np.clip(a, 0, None).sum(-1)
    # the beam goes on past the 4:3 edge without a step (it fades along its
    # axis at the in-picture rate)
    assert 0.65 <= a[:, 374:377].sum() / a[:, 370:373].sum() <= 1.35
    assert a[:, 380:386].sum() > 0


def test_floorclip_woa_1_floor_in_front_of_haze():
    """woa_1's margin floor becomes layer-2 records cut along its edge: with
    the port's order (layer 1, haze, layer 2) the bottom-right margin floor
    is no longer hazed, and nothing above the floor changes."""
    import numpy as np
    import ff7nx_floorclip as FC
    import ff7nx_framesim as FS
    d = _pk386('woa_1')
    s9, info = FC.plan_field('woa_1', d['b'])
    assert 6 <= info['records'] <= 20 and info['page'] == 27
    q = list(d['b'])
    q[8] = s9
    rk = {4: 0, 1: 1, 3: 2, 2: 3}
    a = FS.Field(d['b']).render(cam=(0, 0), K=1, mark=False, rank=rk)
    b = FS.Field(q).render(cam=(0, 0), K=1, mark=False, rank=rk)
    noh = FS.Field(d['b']).render(cam=(0, 0), K=1, mark=False, rank=rk,
                                  layers=(1, 2))
    floor = (slice(205, 240), slice(380, 420))         # margin floor
    assert (np.abs(b[floor].astype(int) - noh[floor]).mean()
            < 0.3 * np.abs(a[floor].astype(int) - noh[floor]).mean())
    assert np.array_equal(a[:150], b[:150])            # sky / waterfall


def test_floorclip_woa_3_patch():
    import ff7nx_floorclip as FC
    import pickle
    pk = os.path.join(_C6, 'woa_3_387.pkl')
    if not os.path.exists(pk):
        pytest.skip('no probe')
    d = pickle.load(open(pk, 'rb'))
    s9, info = FC.plan_field('woa_3', d['b'])
    assert 1 <= info['records'] <= 4 and info['texels'] > 800


def test_pointers_618z_chained_exit_segments():
    """woa_3: exit line split into gateways 1 and 2 (same destination, shared
    endpoint); the explicit arrow hides 1, and now 2 too."""
    import pickle
    import ff7nx_pointers as P
    pk = os.path.join(_C6, 'woa_3_387.pkl')
    if not os.path.exists(pk):
        pytest.skip('no probe')
    d = pickle.load(open(pk, 'rb'))
    assert P.hidden_gateways(d['b'][1], d['b'][7]) == {1, 2}


def test_ghostedge_trnad_4_only_near_rocks():
    import pickle
    import numpy as np
    import ff7nx_ghostedge as G
    import ff7nx_framesim as FS
    pk = os.path.join(_C6, 'trnad_4_387.pkl')
    if not os.path.exists(pk):
        pytest.skip('no probe')
    d = pickle.load(open(pk, 'rb'))
    s9, n = G.plan_field(d['b'][8], d['v'])
    assert 500 < n < 6000
    q = list(d['b'])
    q[8] = s9
    a = FS.Field(d['b']).render(cam=(0, 0), K=3, states={}, mark=False)
    b = FS.Field(q).render(cam=(0, 0), K=3, states={}, mark=False)
    dd = np.abs(a.astype(int) - b).max(-1)
    assert (dd > 0).sum() < 3000
    assert (b[448:454, 1040:1052].astype(int) - a[448:454, 1040:1052]).max() <= 0


def test_beamhd_whitein_smooth_beams_continue_to_edge():
    """whitein's four beams are rebuilt from Cosmos's brightest frame into
    dithered 127-entry palettes (entries 128+ untouched, so nothing else the
    palettes feed changes), and the 16:9 margin continues the new beam at
    its own angle and fade: no step at the 4:3 edge, still lit at the screen
    edge."""
    import struct
    import numpy as np
    import ff7nx_beamhd as BH
    import ff7nx_fxrestore as R
    import ff7nx_framesim as FS
    import ff7nx_marginblack as MB
    if not os.path.exists(_COS):
        pytest.skip('no Cosmos')
    d = _pk386('whitein')
    art = _cosmos_art()
    b = list(d['b'])
    b[3], b[8], st = BH.plan_field('whitein', b, d['v'], art)
    assert set(st) == {9, 10, 11, 12}
    assert all(e >= 40 and 0.8 <= s <= 1.5 for _c, s, e in st.values())
    old, _h, npg, cpp = MB.palette_colours(d['b'][3])
    new, _h2, _n2, _c2 = MB.palette_colours(b[3])
    old = old.reshape(npg, cpp)
    new = new.reshape(npg, cpp)
    for q in range(npg):
        assert (old[q][128:] == new[q][128:]).all()
        if q not in st:
            assert (old[q] == new[q]).all()
    b[8], n = R.margin_palettes('whitein', b, d['v'])
    assert n >= 28
    F = FS.Field(b)
    a = (F.render(cam=(32, 60), states={1: 2}, K=1, mark=False).astype(int)
         - F.render(cam=(32, 60), states={1: 0}, K=1, mark=False).astype(int))
    a = np.clip(a, 0, None).sum(-1)
    last, first = a[:, 370:373].sum(), a[:, 374:377].sum()
    assert 0.65 <= first / last <= 1.35          # no step at the 4:3 edge
    assert a[:, 420:427].sum() > 0.1 * a[:, 374:381].sum()   # reaches the edge
    assert a[:, 420:427].sum() < a[:, 374:381].sum()         # and fades


def test_bandbake_618z2_no_margin_specks():
    """618z (2nd): only solid black holes inside the 4:3 picture are baked;
    the scattered dark-sky texels in the left margin (rows of dark specks in
    the ribbon loop on hardware) are left alone."""
    import struct
    import numpy as np
    import diag_common as DC
    import ff7nx_bandbake as BB
    d = _pk386('whitein')
    s9, n = BB.plan_field(d['b'])
    assert n > 20000
    old = d['b'][8]

    def cells(sec):
        pl, ts, _te, px = DC.parse_pages(sec)
        pm = {p.slot: p for p in pl if p is not None}
        k = px // 16
        out = {}
        for layer, offs in DC.walk_layers(sec, sec.find(b'BACK'), ts):
            for o in offs:
                if layer != 1 or sec[o + 28] or sec[o + 26]:
                    continue
                x, y = struct.unpack_from('<hh', sec, o + 2)
                p = pm[sec[o + 32]]
                if p.depth != 2:
                    continue
                sx, sy = struct.unpack_from('<hh', sec, o + 10)
                out[(x, y)] = np.frombuffer(p.data, '<u2').reshape(px, px)[
                    sy // 16 * k:sy // 16 * k + k,
                    sx // 16 * k:sx // 16 * k + k].copy()
        return out
    a, b = cells(old), cells(s9)
    for xy, v in a.items():
        if xy[0] < -160 or xy[0] >= 160:
            assert (b[xy] == v).all(), xy


def test_bgscr60_woa_2_band_keeps_travelling():
    """woa_2's layer-4 band: travel speed halved (FFNx divides BGSCR at 60),
    the loop's snap-back (in view at 16:9) removed, the start offset kept;
    layer 3's haze halved; a start/stop layer (hill) left alone."""
    import lgp
    import ff7nx_bgscr60 as G
    path = os.path.join(os.path.dirname(__file__), '..', 'dump', 'romfs',
                        'ff7', 'workingdir', 'data', 'field', 'flevel.lgp')
    if not os.path.exists(path):
        pytest.skip('no dump')
    A = lgp.Archive(path)
    sec = list(lgp.split_sections(A.decompressed(A.index['woa_2'])))[0]
    new, edits = G.plan_field('woa_2', sec)
    assert edits == [(3, (640, 80), (320, 40)), (4, (160, 320), (80, 160)),
                     (4, (-6400, 0), (0, 0))]
    ops = [o[3] for o in G._ops(new) if o[2] == 4]
    assert (-3840, 0) in ops                     # one-time start offset
    hill = list(lgp.split_sections(A.decompressed(A.index['hill'])))[0]
    with pytest.raises(ValueError):
        G.plan_field('hill', hill)
    assert 'woa_2' in G.SWEEP and 'woa_3' in G.SWEEP


def test_floorclip_woa_2_floor_in_front():
    """woa_2: the margin floor goes to layer 2 (its floor page is full, so
    another truecolor page takes it) and the seam at the 4:3 edge closes."""
    import pickle
    import ff7nx_floorclip as FC
    pk = os.path.join(_C6, 'woa_2_387.pkl')
    if not os.path.exists(pk):
        pytest.skip('no probe')
    d = pickle.load(open(pk, 'rb'))
    s9, info = FC.plan_field('woa_2', d['b'])
    assert 2 <= info['records'] <= 8 and info['texels'] > 3000
    assert info['page'] != 26 and info['seam'] > 0


def test_wishift_whitein_city_centred():
    """whitein: without the painted strip nothing changes (Cosmos framing);
    with it, the camera range moves 38 units left (the city centred), every
    beam record moves with it (same place ON SCREEN), and the strip x
    -224..-176 is the painted art, except the ribbon tip's cut-out."""
    import struct
    import numpy as np
    import diag_common as DC
    import ff7nx_wishift as WS
    import ff7nx_framesim as FS
    import ff7nx_parallaxfill as PF
    d = _pk386('whitein')
    with pytest.raises(ValueError):
        WS.plan_field('whitein', list(d['b']), None)
    img, rib = WS.picture(d['b'])
    assert img.shape == (1536, 1440, 3)
    fill = img.copy()
    fill[:, :144] = (10, 40, 60)                 # a flat stand-in painting
    q = list(d['b'])
    q[7], q[8], info = WS.plan_field('whitein', q, fill)
    h0, h1 = PF.trigger_header(d['b'][7]), PF.trigger_header(q[7])
    assert (h1['cam_left'], h1['cam_right']) == (h0['cam_left'] - 38,
                                                 h0['cam_right'] - 38)
    assert info['cells'] >= 30 and info['beams'] >= 600

    def beams(sec):
        pl, ts, _te, _px = DC.parse_pages(sec)
        return [struct.unpack_from('<hh', sec, o + 2)
                for layer, offs in DC.walk_layers(sec, sec.find(b'BACK'), ts)
                for o in offs if sec[o + 26] == 1]
    assert [(x - 38, y) for x, y in beams(d['b'][8])] == beams(q[8])
    b = FS.Field(q).render(cam=(-6, 0), states={1: 0}, K=1, mark=True,
                           layers=(1,))
    strip = b[:56, :42].reshape(-1, 3)           # x -219.5..-178, y <= -64
    flat = (np.abs(strip.astype(int) - (8, 40, 56)).max(-1) <= 8).mean()
    assert flat > 0.95                           # the painting, nothing else
    a = FS.Field(d['b']).render(cam=(32, 0), states={1: 0}, K=1,
                                mark=True, layers=(1,))
    assert (a[:, 6:389] == b[:, 44:427]).all()   # right of -176: unchanged
    pages, ts, _te, _px = DC.parse_pages(q[8])
    d2 = {p.slot for p in pages if p is not None and p.depth == 2
          and 0x0F <= p.slot < 0x1A}
    assert not any(q[8][o + 32] in d2 for _l, offs in DC.walk_layers(
        q[8], q[8].find(b'BACK'), ts) for o in offs)
    with pytest.raises(ValueError):            # idempotent
        WS.plan_field('whitein', q, fill)


def test_specks_las4_3():
    """las4_3: the two stray bright debris specks become transparent
    (90 texels), nothing else changes."""
    import pickle
    import numpy as np
    import ff7nx_specks as SP
    import ff7nx_framesim as FS
    pk = os.path.join(_C6, 'las4_3_388.pkl')
    if not os.path.exists(pk):
        pytest.skip('no probe')
    d = pickle.load(open(pk, 'rb'))
    q = list(d['b'])
    q[8], n = SP.plan_field('las4_3', q)
    assert 60 <= n <= 150
    a = FS.Field(d['b']).render(cam=(0, 0), states={1: 1}, K=1, mark=True)
    b = FS.Field(q).render(cam=(0, 0), states={1: 1}, K=1, mark=True)
    diff = np.nonzero(np.abs(a.astype(int) - b).max(-1))
    assert diff[0].size and diff[0].min() >= 85 and diff[0].max() <= 115
    assert diff[1].min() >= 265 and diff[1].max() <= 330
    with pytest.raises(ValueError):            # idempotent: specks gone
        SP.plan_field('las4_3', q)


def test_wishift_painted_strip_asset():
    """The shipped painting loads at the export geometry and lands: the
    strip is painted (no black border column, no mirror), the city view is
    centred."""
    import ff7nx_wishift as WS
    if not os.path.exists(WS.fill_path()):
        pytest.skip('no painted strip')
    fill = WS.load_fill()
    assert fill.shape == (1536, 1440, 3)
    d = _pk386('whitein')
    q = list(d['b'])
    q[7], q[8], info = WS.plan_field('whitein', q, fill)
    assert info['cells'] >= 30


def test_wishift_618z8_ships_only_own_strip():
    """10-05 (user): "im not trying to distribute artwork i dont have rights
    to". The shipped file is the painted strip alone (144 x 1536), it holds
    no pixel of the game's / Cosmos's picture (the ribbon's tip is black;
    its texels come from the field at build time), only the strip is
    non-black once loaded, and no full picture or video is in assets/."""
    import numpy as np
    import ff7nx_wishift as WS
    from PIL import Image
    here = WS.HERE
    assert not os.path.exists(os.path.join(here, 'assets',
                                           'whitein_left_fill.png'))
    assert not os.path.exists(os.path.join(here, 'assets', 'movies'))
    for root, _dirs, files in os.walk(os.path.join(here, 'assets')):
        for f in files:
            if f.lower().endswith(('.png', '.jpg', '.dds')):
                im_ = Image.open(os.path.join(root, f))
                if len(np.unique(np.asarray(im_.convert('L')))) <= 2:
                    continue                  # a mask: a shape, no art
                assert im_.size[0] <= 144, (f, im_.size)
    im = Image.open(WS.fill_path())
    assert im.size == (144, 1536)
    fill = WS.load_fill()
    assert fill[:, 144:].max() == 0
    d = _pk386('whitein')
    img, rib = WS.picture(d['b'])
    assert fill[:, :144][rib[:, :144]].max() == 0
    own = ~rib[:, :144] & (img[:, :144].max(-1) > 8)
    same = (fill[:, :144] == img[:, :144]).all(-1) & own
    assert same.sum() < 20, int(same.sum())


def test_wishift_strip_has_no_reflected_ribbon():
    """10-05 (user): "why am i seeing a repeat of the bridge here?" -- 52c's
    seam fade mirrored x 145..289, which the ribbon crosses (rows 316..637),
    so the strip carried an X-shaped reflection of the ribbon's tip. 52d
    replaced the mirror with a Poisson join. Outside the ribbon's own mask
    the strip must hold no ribbon-coloured (bright lavender) pixels."""
    import ff7nx_wishift as WS
    import cv2
    from PIL import Image
    mp = os.path.join(WS.HERE, 'assets', 'whitein_paint',
                      'whitein_ribbon_mask.png')
    if not os.path.exists(WS.fill_path()) or not os.path.exists(mp):
        pytest.skip('no painted strip')
    fill = WS.load_fill().astype(int)
    rib = np.asarray(Image.open(mp).convert('L')) > 0
    near = cv2.dilate(rib.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
    s = fill[280:700, :144]
    lav = (s.min(-1) > 140) & (s[..., 2] > s[..., 1]) & (s[..., 0] > s[..., 1] - 20)
    lav &= ~near[280:700, :144]
    assert int(lav.sum()) < 20, int(lav.sum())


def test_bandbake_618z5_spire_filler_row_baked():
    """10-05 (user): horizontal lines across whitein's central spire at
    tile edges, wider or thinner with the camera. Layer 1 at y = -32 under
    the spire was a SHARED 1x filler cell on paletted page 1, so the bake
    skipped that row while baking the rows above and below: baked meets
    additive on a tile edge. The filler records now get their own truecolor
    cells first, the whole spire bakes, and the picture is unchanged."""
    import struct
    import numpy as np
    import diag_common as DC
    import ff7nx_bandbake as BB
    import ff7nx_framesim as FS
    d = _pk386('whitein')
    _s, npro = BB._promote_fillers(d['b'], d['b'][8])
    assert 3 <= npro <= 12
    s9, n = BB.plan_field(d['b'])
    pl, ts, _te, px = DC.parse_pages(s9)
    pm = {p.slot: p for p in pl if p is not None}
    k = px // 16
    left = 0
    for layer, offs in DC.walk_layers(s9, s9.find(b'BACK'), ts):
        for o in offs:
            x, y = struct.unpack_from('<hh', s9, o + 2)
            if layer == 1 and not s9[o + 28] and (x, y) in (
                    (-16, -32), (0, -32)):
                assert pm[s9[o + 32]].depth == 2
                assert not (15 <= s9[o + 32] < 26)          # 608b gate
            if (layer == 2 and s9[o + 28] and not s9[o + 26]
                    and (x, y) in ((-16, -32), (0, -32))):
                sx, sy = struct.unpack_from('<hh', s9, o + 14)
                left += int(np.frombuffer(pm[s9[o + 34]].data, '<u2').reshape(
                    px, px)[sy // 16 * k:sy // 16 * k + k,
                            sx // 16 * k:sx // 16 * k + k].astype(bool).sum())
    assert left == 0
    q = list(d['b'])
    q[8] = s9
    a = FS.Field(d['b']).render(cam=(32, 60), states={1: 2}, K=1, mark=False)
    b = FS.Field(q).render(cam=(32, 60), states={1: 2}, K=1, mark=False)
    assert np.abs(a.astype(int) - b).max() <= 8


def test_fxcut_618z7_psdun_4_glow_hd_edge():
    """psdun_4 (hardware 10-05): the 1x green glow's staircase against the
    rock. The rock is redrawn over the glow on layer 2 (truecolor page
    outside the FX band, depth just in front of the glow), the glow's dim
    edge texels take their inner neighbour's index, and with the glow off
    only the anti-aliased ring along the rock differs."""
    import pickle
    import numpy as np
    import ff7nx_fxcut as C
    import ff7nx_framesim as FS
    pk = os.path.join(_C6, 'psdun_4_389.pkl')
    if not os.path.exists(pk):
        pytest.skip('no psdun_4 probe')
    parts = pickle.load(open(pk, 'rb'))
    s9, info = C.plan_field(parts)
    assert 20 <= info['records'] <= 120 and not (15 <= info['page'] < 26)
    assert info['states'] == 6
    q = list(parts)
    q[8] = s9
    A, B = FS.Field(parts), FS.Field(q)
    a = A.render(cam=(0, 0), K=3, states={}, mark=False)
    b = B.render(cam=(0, 0), K=3, states={}, mark=False)
    # glow off: only the rock's anti-aliased ring differs (it carries part
    # of the glow, which the script never switches fully off)
    ch = (np.abs(a.astype(int) - b).max(-1) > 0).mean()
    assert ch < 0.01, ch
    # with the glow on, the rock directly under the platform's edge is the
    # layer-1 art (no glow spill) along most of the edge
    d = {}
    C.plan_field(parts, debug=d)
    assert d['cut'].sum() > 1000


def test_glowhd_618z8_psdun_4_truecolor_glow_keeps_frames():
    """psdun_4 (hardware 10-05: "can that be true color and animated
    safely? ... i dont want to lose frames"): the glow's pages become
    truecolor, every glow record keeps its param/state/cell, the 6 states
    still differ from each other, layer 1 is untouched and the texture
    memory stays well under the 37.44 MB proven on hardware."""
    import pickle
    import struct
    import numpy as np
    import diag_common as DC
    import ff7nx_glowhd as G
    pk = os.path.join(_C6, 'psdun_4_389.pkl')
    if not os.path.exists(pk):
        pytest.skip('no psdun_4 probe')
    parts = pickle.load(open(pk, 'rb'))
    d = {}
    s9, info = G.plan_field(parts, debug=d)
    assert info['states'] == 6 and info['records'] > 500
    pl0, ts0, _e, _px = DC.parse_pages(bytes(parts[8]))
    pl1, ts1, _e, _px = DC.parse_pages(s9)
    dep = {p.slot: p.depth for p in pl1 if p is not None}
    assert all(dep[s] == 2 for s in info['pages'])
    mb = sum(3.375 if p.depth == 2 else 0.3125 for p in pl1 if p is not None)
    assert mb < 30.0, mb

    def recs(sec9, ts):
        out = []
        for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
            out += [(layer, sec9[o:o + 52]) for o in offs]
        return out
    assert recs(bytes(parts[8]), ts0) == recs(s9, ts1)   # records unchanged
    hd = d['hd']
    st = sorted(hd)
    diffs = [float(np.abs(hd[a] - hd[b]).mean())
             for a, b in zip(st, st[1:])]
    assert min(diffs) > 0.5, diffs
    # smooth: no 3-px staircase -- the glow's alpha edge has partial values
    g = hd[st[0]].max(-1)
    edge = (g > 0) & (g < g.max() * 0.25)
    assert edge.sum() > 1000
    # 618z9: no glow on the rock (2 px inside rock_mask's outline it is
    # black in every state), and the edge is a curve: along the outline
    # the coverage takes intermediate values, not only 0 / 1
    from scipy import ndimage as ND
    deep = ND.distance_transform_edt(d['rock']) > 2.5 + G.EDGE_BAND
    for s_ in st:
        assert hd[s_].max(-1)[deep].max() < 8, s_
    ring = (ND.distance_transform_edt(d['rock']) <= 2) & d['rock'] | (
        (ND.distance_transform_edt(~d['rock']) <= 2) & ~d['rock'])
    c = d['cover'][ring]
    assert ((c > 0.1) & (c < 0.9)).mean() > 0.3


def test_fxtrial_618z9_las0_2_frames():
    """las0_2 (hardware 10-05/10-06, builds 392/393). The script shows two
    frames at once (BGON n+1 / WAIT / BGOFF n-1). The left margin's base
    and the boulder band follow the median frame (no drifting base, no
    ghost tail), the cone frames show the last puff fading out, the right
    4:3 edge has no straight line where the margin is empty, no fill
    triangle under the left margin, and alternate frames carry inverted
    dither patterns."""
    import pickle
    import struct
    import numpy as np
    import diag_common as DC
    import ff7nx_fxtrial as T
    pk = os.path.join(_C6, 'las0_2_hd3f.pkl')
    if not os.path.exists(pk):
        pytest.skip('no las0_2 probe')
    s9 = pickle.load(open(pk, 'rb'))
    s9, _ = T._las0_2_polish(s9)
    d = {}
    new, info = T._las0_2_frames(s9, debug=d)
    assert info['dropped'] >= 150 and info['added'] <= 40
    xs, ys = d['xs'], d['ys']
    box = (ys > -186)[:, None] & (ys < -164)[:, None] & (xs < -180)[None, :]
    before = np.stack([a[box] for a in d['img'].values()])
    after = np.stack([a[box] for a in d['want'].values()])
    assert np.abs(after - np.median(after, 0)).mean() < \
        0.3 * np.abs(before - np.median(before, 0)).mean()
    # the cone: frame 1/2 at the right 4:3 edge is a faint copy of 3/16
    cone = (ys < -170)[:, None] & (xs > 140)[None, :] & (xs < 156)[None, :]
    r = d['want'][(1, 2)][cone].sum() / d['img'][(3, 16)][cone].sum()
    assert 0.1 < r < 0.25, r
    # no steam under the left margin below y -160 (the old fill triangle)
    tri = (ys > -150)[:, None] & (xs < -196)[None, :]
    assert max(a[tri].max() for a in d['want'].values()) < 3.0
    # dither: two neighbouring frames' rounding is complementary
    f0, f1 = (1, 1 << 5), (1, 1 << 6)
    w0, w1 = d['want'][f0], d['want'][f1]
    flat = (np.abs(w0 - w1).max(-1) < 0.05) & (w0.max(-1) > 4)
    assert flat.sum() > 1000
    pl, ts, _te, px = DC.parse_pages(new)
    assert sum(1 for p in pl if p is not None and p.depth == 2) >= 9



def test_bandbake_618z7_no_island_split_by_43_cut():
    """whitein (hardware 10-05): a dotted ring on the ribbon at x -160..-156,
    y -86..-82 -- a 115-texel baked island whose piece of overlay-on-black
    continued unbaked outside 4:3. Such a piece is now left additive: layer
    1's cell at x = -160, y = -96 (y -96..-81) is not touched by the bake."""
    import struct
    import numpy as np
    import diag_common as DC
    import ff7nx_bandbake as BB
    d = _pk386('whitein')
    s9, n = BB.plan_field(d['b'])
    assert n > 20000

    def cells(sec):
        pl, ts, _te, px = DC.parse_pages(sec)
        pm = {p.slot: p for p in pl if p is not None}
        k = px // 16
        out = {}
        for layer, offs in DC.walk_layers(sec, sec.find(b'BACK'), ts):
            for o in offs:
                if layer != 1 or sec[o + 28] or sec[o + 26]:
                    continue
                xy = struct.unpack_from('<hh', sec, o + 2)
                p = pm[sec[o + 32]]
                if p.depth != 2:
                    continue
                sx, sy = struct.unpack_from('<hh', sec, o + 10)
                out[xy] = np.frombuffer(p.data, '<u2').reshape(px, px)[
                    sy // 16 * k:sy // 16 * k + k,
                    sx // 16 * k:sx // 16 * k + k].copy()
        return out
    a, b = cells(d['b'][8]), cells(s9)
    for xy in ((-160, -96),):
        if xy in a:
            assert (a[xy] == b[xy]).all(), xy


def test_fxtrial_618z8_las0_2_polish_and_bottom():
    """las0_2 (hardware 10-05, build 391): the mirrored plume frames at the
    right 4:3 edge are re-squeezed, the low jet's haze over the rock fades,
    and the left margin's steam gets a new row down to the boulder (no flat
    edge), on free cells of truecolor FX page 23 (<= 256 binds)."""
    import pickle
    import collections
    import struct
    import diag_common as DC
    import ff7nx_fxtrial as T
    pk = os.path.join(_C6, 'las0_2_hd3f.pkl')
    if not os.path.exists(pk):
        pytest.skip('no las0_2 probe')
    s9 = pickle.load(open(pk, 'rb'))
    p1, i1 = T._las0_2_polish(s9)
    assert i1['mirror_frames'] >= 4 and i1['haze_cells'] >= 50
    p2, i2 = T._las0_2_bottom(p1)
    assert i2['new_records'] >= 10
    lo, hi = i2['edge_y']
    assert -165 <= lo <= hi <= -140
    pl, ts, _te, _px = DC.parse_pages(p2)
    binds = collections.Counter()
    low = 0
    for layer, offs in DC.walk_layers(p2, p2.find(b'BACK'), ts):
        for o in offs:
            bl = p2[o + 28]
            binds[p2[o + 34] if bl else p2[o + 32]] += 1
            x, y = struct.unpack_from('<hh', p2, o + 2)
            if layer == 2 and bl and y == -160 and x < -192:
                low += 1
    assert max(binds.values()) <= 256 and low >= 10


def test_fxtrial_618z7_las0_2_hd_steam():
    """las0_2 (hardware 10-05): the steam's 16:9 margin pages 21-23 go
    truecolor from Cosmos's DDS under a per-field trial ceiling and raw cap
    (field buffer 32 MB); the right column fades out instead of ending in a
    vertical line."""
    import numpy as np
    import ff7nx_fxtrial as T
    import ff7nx_fieldbg as F
    assert T.TRIALS['las0_2'] >= 44.2 and 'las0_2' in T.FIELDS
    assert T.TRIALS['del3'] == T.TRIALS['las0_3'] == 37.5
    assert T.RAW_CAPS['las0_2'] > 15_600_000
    assert F.field_buffer_bytes(15_575_766) == 32 * 1024 * 1024
    assert F.mov_imm_word(23, 32 * 1024 * 1024) is not None
    col, a, b = T.EDGE_FADE['las0_2']
    assert (col, a, b) == (192, 196, 208)


def test_artpatch_618z10_only_painted_pixels(tmp_path):
    """mds7pb_1 (user retouch): the asset is a patch, not a picture -- a
    mask of the changed texels, their XOR against the installed art and a
    SHA-1 of that art. Applied, exactly the painted texels become the
    user's (bit for bit), nothing else changes; other art refuses it."""
    import pickle
    import numpy as np
    from PIL import Image
    import ff7nx_artpatch as AP
    import ff7nx_framesim as FS
    pk = os.path.join(_C6, 'mds7pb_1_393.pkl')
    if not os.path.exists(pk):
        pytest.skip('no mds7pb_1 probe')
    parts = pickle.load(open(pk, 'rb'))
    org = str(tmp_path / 'o.png')
    AP.export(parts, 'mds7pb_1', org)
    o = np.asarray(Image.open(org)).copy()
    o[172:207, 170:190] = (40, 30, 20)
    ed = str(tmp_path / 'e.png')
    Image.fromarray(o).save(ed)
    n, dest = AP.make(parts, 'mds7pb_1', org, ed,
                      dest=str(tmp_path / 'a.npz'))
    assert dest.endswith('.npz') and 0 < n <= 35 * 20
    patch = AP.load('mds7pb_1', dest)
    assert patch['mask'].sum() == n == len(patch['delta'])
    s9, info = AP.plan_field('mds7pb_1', parts, patch)
    raw, *_ = AP._layer1(s9, AP.REGIONS['mds7pb_1'])
    m = patch['mask']
    assert (raw[m] == AP._enc(o)[m]).all()
    q = list(parts)
    q[8] = s9
    a = FS.Field(parts).render(cam=(-35, -17), K=3, states={}, mark=True)
    b = FS.Field(q).render(cam=(-35, -17), K=3, states={}, mark=True)
    assert int((np.abs(a.astype(int) - b).max(-1) > 0).sum()) == n
    bad = dict(patch, sha1='0' * 40)
    with pytest.raises(ValueError):
        AP.plan_field('mds7pb_1', parts, bad)
    # the shipped asset: a patch file, no image in assets/artpatch
    d = os.path.join(AP.ASSET_DIR)
    if os.path.isdir(d):
        assert all(f.endswith('.npz') for f in os.listdir(d))


def test_hrepeat_618z11_ztruck_scroll_fills_the_zoomed_view():
    """ztruck: layers 3/4 become one exact 704-unit period (the generic
    fill's folding copies gone), the camera is pinned at y +16, and with
    FFNx's zoom (350 units across) nothing is undrawn at any scroll
    position, shake included."""
    import pickle
    import ff7nx_hrepeat as H
    import ff7nx_fieldzoom as Z
    import ff7nx_framesim as FS
    import ff7nx_parallaxfill as PF
    pk = os.path.join(_C6, 'ztruck_393.pkl')
    if not os.path.exists(pk):
        pytest.skip('no ztruck probe')
    P = list(pickle.load(open(pk, 'rb')))
    s7, s9, info = H.plan('ztruck', P[7], P[8])
    h = PF.trigger_header(s7)
    assert h['bg3_w'] == h['bg4_w'] == 704
    assert sorted((h['cam_bottom'], h['cam_top'])) == [-104, 136]
    assert info['layers'][3]['records'] == 110
    assert info['layers'][4]['records'] == 88
    q = list(P)
    q[7], q[8] = s7, s9
    F = FS.Field(q)
    z = Z.factor('ztruck', {})
    hw, hh = 213.5 / z, 120 / z
    for cy in (12, 16, 20):                    # pinned +16, shake 4
        for s in range(0, 704, 32):
            img = F.render(cam=(0, cy), K=1, states={}, mark=True,
                           scroll3=(s, 0), scroll4=(s * 10 % 704, 0))
            c = img[int(120 - hh):int(120 + hh) + 1,
                    int(213.5 - hw):int(213.5 + hw) + 1]
            mag = (c[..., 0] == 255) & (c[..., 1] == 0) & (c[..., 2] == 255)
            assert not mag.any(), (cy, s, int(mag.sum()))
    with pytest.raises(ValueError):            # already one period
        H.plan('ztruck', s7, s9)


def test_scenemodel_618z11_variants_and_loader():
    """ztruck/midgal: Cloud without the chest weapon part, Zack (IBAD)
    wearing EJDF1 in ztruck only, BHFF's hand part static, EJDC without
    the back sword in midgal; same bone count, loader names swapped."""
    import pickle
    import ff7nx_scenemodel as S
    hrc = ('\r\n'.join([':HEADER_BLOCK 2', ':SKELETON x_sk', ':BONES 3', '',
                        'hip', 'root', '1.0', '1 HHHA', '',
                        'chest', 'hip', '2.0', '2 CCCA CCCA1', '',
                        'r_hand', 'chest', '1.0', '0', ''])).encode()
    out = S.variant_hrc(hrc, 'chest', 'drop', 'CCCA1', None)
    assert b'\r\n1 CCCA\r\n' in out and b'CCCA1' not in out
    out = S.variant_hrc(hrc, 'r_hand', 'add', 'EJDF1', None)
    assert b'\r\n1 EJDF1\r\n' in out
    out = S.variant_hrc(hrc, 'chest', 'swap', 'CCCA1', 'ZNBS')
    assert b'2 CCCA ZNBS' in out
    with pytest.raises(ValueError):
        S.variant_hrc(hrc, 'chest', 'drop', 'NOPE', None)
    rsd = b'@RSD940102\r\nPLY=Z00C00F00.PLY\r\nMAT=BHJC1.MAT\r\nGRP=BHJC1.GRP\r\n'
    assert S.variant_rsd(rsd, 'BHJC1').count(b'BHJC1.') == 3
    for v in S.SCENES.values():
        assert all(len(k) == len(n) == 4 for k, n in v.items())
    assert all(n.lower().startswith(S.PREFIX) for n in
               list(S.VARIANTS) + list(S.RSDS))
    pk = os.path.join(_C6, 'ztruck_393.pkl')
    if not os.path.exists(pk):
        pytest.skip('no ztruck probe')
    P = list(pickle.load(open(pk, 'rb')))
    s3, done = S.plan_field('ztruck', P[2])
    assert done == {'AAAA': 'ZNAA', 'IBAD': 'ZNIB'}
    assert len(s3) == len(P[2])
    names = [h for _o, h in S.loader_hrcs(s3)]
    assert names[0].upper() == 'ZNAA.HRC' and names[2].upper() == 'ZNIB.HRC'
    assert sum(a != b for a, b in zip(s3, P[2])) <= 8


def test_edgesmooth_618z12_ztruck_curves_seam_and_green():
    """ztruck: layer 4's staircase silhouettes become curves (most 3x3
    blocks on the edge partial instead of all-or-nothing), the period-seam
    slit is closed, changes stay at the edges, and every texel written is
    one that already existed (green even, never 0 where opaque)."""
    import pickle
    import numpy as np
    from scipy import ndimage as ND
    import diag_common as DC
    import ff7nx_hrepeat as H
    import ff7nx_edgesmooth as E
    pk = os.path.join(_C6, 'ztruck_393.pkl')
    if not os.path.exists(pk):
        pytest.skip('no ztruck probe')
    P = list(pickle.load(open(pk, 'rb')))
    P[7], P[8], _ = H.plan('ztruck', P[7], P[8])
    s9, info = E.plan('ztruck', P[8])
    assert info[4]['seam'] > 0 and info[4]['added'] > 0
    assert info[2]['fringe'] > 0

    def canvas(buf, layer, xr):
        pl, ts, _te, px = DC.parse_pages(buf)
        pm = {p.slot: p for p in pl if p is not None}
        return E._canvas(buf, E._records(buf, ts, pm, layer), pm, px, xr)[0]
    a = canvas(bytes(P[8]), 4, (-176, 176))
    b = canvas(s9, 4, (-176, 176))

    def partial(t):
        m = t != 0
        h, w = m.shape
        bl = m[:h // 3 * 3, :w // 3 * 3].reshape(
            h // 3, 3, w // 3, 3).sum((1, 3))
        return ((bl > 0) & (bl < 9)).sum()
    assert partial(b) > 1.5 * partial(a)          # 301 -> 507
    rows = (b[:, -1] != 0)
    assert (b[rows, 0] != 0).mean() > 0.99          # the seam slit
    ch = a != b
    near = ND.binary_dilation(
        (a != 0) ^ ND.binary_erosion(a != 0), iterations=6)
    assert not (ch & ~near).any()
    new = b[ch & (b != 0)]
    assert not ((new >> 5) & 1).any()
    assert set(np.unique(new).tolist()) <= set(np.unique(a).tolist())


def test_edgesmooth_618z13_truck_antialias():
    """ztruck's truck (layer 2, camera pinned): silhouette texels blended
    toward the mean of the scrolling layers behind them, painted outlines
    anti-aliased; every written texel green-even and non-zero, and only
    near an edge or a painted line."""
    import pickle
    import numpy as np
    import diag_common as DC
    import ff7nx_hrepeat as H
    import ff7nx_edgesmooth as E
    pk = os.path.join(_C6, 'ztruck_393.pkl')
    if not os.path.exists(pk):
        pytest.skip('no ztruck probe')
    P = list(pickle.load(open(pk, 'rb')))
    P[7], P[8], _ = H.plan('ztruck', P[7], P[8])
    s9, info = E.plan('ztruck', P[8], P)
    assert info[2]['ring'] > 1000 and info[2]['lines'] > 1000
    assert info[2]['line_comps'] >= 3

    def canvas(buf):
        pl, ts, _te, px = DC.parse_pages(buf)
        pm = {p.slot: p for p in pl if p is not None}
        return E._canvas(buf, E._records(buf, ts, pm, 2), pm, px)[0]
    a, b = canvas(bytes(P[8])), canvas(s9)
    ch = (a != b) & (b != 0)
    assert ch.sum() > 5000
    assert not ((b[ch] >> 5) & 1).any()
    # the silhouette grew by at most the ring (2 texels)
    from scipy import ndimage as ND
    grown = (b != 0) & (a == 0)
    assert not (grown & ~ND.binary_dilation(a != 0, iterations=3)).any()


def test_easteregg_618z14_cat_on_tsunas_crate():
    """mds7_w2 (Echo-S): one entity and one model appended; every existing
    script disassembles identically; the cat sits on walkmesh triangle 2 at
    the crate-top height; a non-Echo-S script is refused."""
    import io
    import contextlib
    import re
    import pickle
    import field_dis as FD
    import ff7nx_easteregg as EG
    pw = os.path.join(_C6, 'mds7_w2_394.pkl')
    pu = os.path.join(_C6, 'utmin1_394.pkl')
    if not (os.path.exists(pw) and os.path.exists(pu)):
        pytest.skip('no mds7_w2 / utmin1 probe')
    P = pickle.load(open(pw, 'rb'))
    U = pickle.load(open(pu, 'rb'))
    if 'nekodev' in FD.parse(bytes(P[0]))[1]:
        pytest.skip('built flevel already has the cat')
    s1, s3, info = EG.plan(P[0], P[2], bytes(P[4]), bytes(U[2]))
    assert info['model'] == 4 and info['anim'] == 'FAAC'

    def dis(s):
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            FD.cmd_dis(bytes(s))
        return re.sub(r'@ \+[0-9A-F]+', '@', f.getvalue()).strip()
    old, new = dis(P[0]), dis(s1)
    assert new.startswith(old)
    assert 'nekodev' in new and 'xyzi     00 00 80 00 47 00 9C 00 02 00' in new
    assert 'dfanm    00 01' in new
    with pytest.raises(ValueError):
        EG.plan(s1, s3, bytes(P[4]), bytes(U[2]))      # only once
    with pytest.raises(ValueError):
        EG.plan(U[0], U[2], bytes(U[4]), bytes(U[2]))  # not Echo-S's


def test_wheelfix_618z14_paint_steady():
    """ztruck's rear wheel: across its three frames nothing outside the tyre
    changes any more (the paint and the arch come from the static copy)."""
    import pickle
    import numpy as np
    import ff7nx_hrepeat as H
    import ff7nx_wheelfix as WF
    import ff7nx_framesim as FS
    pk = os.path.join(_C6, 'ztruck_393.pkl')
    if not os.path.exists(pk):
        pytest.skip('no ztruck probe')
    P = list(pickle.load(open(pk, 'rb')))
    P[7], P[8], _ = H.plan('ztruck', P[7], P[8])
    s9, info = WF.plan('ztruck', P[8])
    assert info['tiles'] == 10 and info['states'] == 3 and info['cleared']
    Q = list(P)
    Q[8] = s9

    def spread(parts):
        im = [FS.Field(parts).render(cam=(0, 16), K=3, states={1: s},
                                     mark=False).astype(int)
              for s in (1, 2, 4)]
        return np.max([np.abs(im[i] - im[j]).max(-1)
                       for i in range(3) for j in range(i + 1, 3)], 0), im[0]
    d0, _ = spread(P)
    d1, c = spread(Q)
    sat = c.max(-1) - c.min(-1)
    paint = (sat >= 40) & (c.mean(-1) > 60)
    assert (d1 > 0).sum() < 0.6 * (d0 > 0).sum()
    assert ((d1 > 0) & paint).sum() < 0.01 * ((d0 > 0) & paint).sum() + 50


def test_ripplefade_619_levels_and_script():
    """las2_2: the three ripple fades found; each pass is the 7-opcode body
    written in place (one tick under the interpreter's 8-opcode limit, which
    is what made 618z14 flicker); the 8 states of one parameter reproduce the
    held frame's light times the level factor; the blob cells are repaired."""
    import pickle
    import numpy as np
    import lgp
    import field_dis as FD
    import field_bg_repack as FR
    import ff7nx_marginart as MA
    import ff7nx_ripplefade as RF
    import ff7nx_framesim as FS
    pk = os.path.join(_C6, 'las2_2_394.pkl')
    van = os.path.join(os.path.dirname(__file__), '..', 'game_data_files',
                       'field', 'flevel.lgp')
    iro_ = os.path.join(os.path.dirname(__file__), '..', 'mods',
                        'CosmosLimitBreak.iro')
    if not (os.path.exists(pk) and os.path.exists(van)
            and os.path.exists(iro_)):
        pytest.skip('no las2_2 probe / vanilla / Cosmos')
    P = pickle.load(open(pk, 'rb'))
    loops = RF.find_loops(bytes(P[0]))
    assert [lp['q'] for lp in loops] == [8, 10, 14]
    va = lgp.Archive(van)
    V = list(lgp.split_sections(va.decompressed(va.index['las2_2'])))
    art = MA.provider_source(FR.ArtProvider([(iro_, None)], 768))
    s0, s9, info = RF.plan_field('las2_2', P, V, art)
    L, D, base = info['levels'], info['step'], info['params'][2]
    assert (L, D) == (8, 9) and len(s0) == len(P[0])
    m, _n, _e = FD.parse(s0)
    end = m['string_table']
    for lp in loops:
        at = lp['at']
        assert [i[1] for i in FD.walk(s0, at, end)[:1]] == ['skip']
        assert [i[1] for i in FD.walk(s0, at + 4, end)[:7]] == [
            'if', 'mins!', 'set', 'div', 'bgclr', 'bgon', 'back']
        assert s0[at + 29] == 0x12 and at + 29 - s0[at + 30] == at + 4
    s9, rep, kept = RF.repair_cells(s9, V[8])
    assert rep == 7 and kept == 0
    Q = list(P)
    Q[0], Q[8] = s0, s9
    FP, FQ, FV = FS.Field(P), FS.Field(Q), FS.Field(V)
    cam = (41, 12)
    bP = FP.render(cam=cam, K=1, states={}, mark=False).astype(float)
    bQ = FQ.render(cam=cam, K=1, states={}, mark=False).astype(float)
    held = (FP.render(cam=cam, K=1, states={6: 1 << 5}, mark=False)
            - bP).clip(0).sum()
    for j in range(L):
        lit = (FQ.render(cam=cam, K=1, states={base: 1 << j}, mark=False)
               - bQ).clip(0).sum()
        f = min(1.0, (j + 0.5) * D / 64.0)
        assert abs(lit / held - f) < 0.05, (j, lit / held, f)
    # the blob: p5 s6 at x 160..176 now matches vanilla there
    bV = FV.render(cam=cam, K=1, states={}, mark=False).astype(float)
    for st in ({5: 1 << 5}, {5: 1 << 6}):
        q = (FQ.render(cam=cam, K=1, states=st, mark=False) - bQ).clip(0)
        v = (FV.render(cam=cam, K=1, states=st, mark=False) - bV).clip(0)
        y0, x0 = 100 - cam[1] + 120, int(155 - cam[0] + 213.5)
        assert abs(q[y0:y0 + 30, x0:x0 + 30].mean()
                   - v[y0:y0 + 30, x0:x0 + 30].mean()) < 3
