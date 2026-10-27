"""BUILD 619: folder mods, 7th Heaven's name order, Idle Animations.

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
import importlib.util
import os
import struct
import sys

import pytest

HERE = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, HERE)

_IDLE = os.path.expanduser(
    '~/mnt/7th_heaven_nx/mods/Tsunamods__Idle_Animations_2.2_60fps')

XML = '''<?xml version="1.0" encoding="utf-8"?>
<ModInfo>
  <Name>[Tsunamods] Idle Animations - 60fps</Name>
  <Category>Animations</Category>
  <ModFolder Folder="cloud" ActiveWhen="CloudAnim=1"/>
  <Conditional Folder="barret" ActiveWhen="BarretAnim=1">
    <Or>
      <RuntimeVar Var="FieldID" Values="1..9"/>
      <RuntimeVar Var="FieldID" Values="11..20"/>
    </Or>
  </Conditional>
  <Conditional Folder="cloudanimflevelsecho" ActiveWhen="CloudAnim = 1">
    <Or><RuntimeVar Var="Byte:0x719C60" Values="233" /></Or>
  </Conditional>
  <Conditional Folder="cloudanimflevels" ActiveWhen="CloudAnim = 1">
    <Not><Or><RuntimeVar Var="Byte:0x719C60" Values="233" /></Or></Not>
  </Conditional>
  <!--ConfigOption-->
    <ID>A-Pose_Fix</ID>
    <Name>A-Pose Fix</Name>
  <!--/ConfigOption-->
  <ConfigOption><Type>List</Type><Default>1</Default><ID>CloudAnim</ID>
    <Name>Cloud</Name><Option Value="0" Name="None"/>
    <Option Value="1" Name="One"/></ConfigOption>
  <ConfigOption><Type>List</Type><Default>1</Default><ID>BarretAnim</ID>
    <Name>Barret</Name><Option Value="0" Name="None"/>
    <Option Value="1" Name="One"/></ConfigOption>
</ModInfo>
'''


def _anim(frames, bones):
    return struct.pack('<III', 1, frames, bones) + bytes(8 * frames)


def _entry(name, flag=1):
    n = name.encode()
    return struct.pack('<H', len(n)) + n + struct.pack('<H', flag)


def _rec(name, hrc, anims):
    n = name.encode()
    out = struct.pack('<H', len(n)) + n + struct.pack('<H', 0)
    out += hrc.encode().ljust(8, b'\0') + b'512\0'
    out += struct.pack('<H', len(anims)) + bytes(30)
    for a in anims:
        out += _entry(a)
    return out


def _sec3(recs):
    return struct.pack('<HHH', 0, len(recs), 1) + b''.join(recs)


@pytest.fixture
def idle_mod(tmp_path):
    root = tmp_path / 'Idle_Folder_2.2_60fps'
    (root / 'Cloud' / 'char').mkdir(parents=True)
    (root / 'barret' / 'char').mkdir(parents=True)
    for v in ('cloudanimflevels', 'cloudanimflevelsecho'):
        (root / v / 'flevel.lgp').mkdir(parents=True)
    (root / 'mod.xml').write_text(XML)
    (root / 'Cloud' / 'char' / 'ORIC.A').write_bytes(_anim(30, 21))
    (root / 'barret' / 'char' / 'adcb.a').write_bytes(_anim(40, 21))
    (root / 'barret' / 'char' / 'NOTES.TXT').write_text('x')
    chunk = _sec3([_rec('a_cloud.char', 'AAAA.HRC', ['ORIC', 'AAFF.aki']),
                   _rec('b_cloud.char', 'AAAA.HRC', ['ACFE.aki']),
                   _rec('npc.char', 'ASBF.HRC', ['ACFE.aki'])])
    (root / 'cloudanimflevelsecho' / 'flevel.lgp' / 'f1.chunk.3') \
        .write_bytes(chunk)
    return str(root)


def test_folder_mod_reads_like_an_iro(idle_mod, tmp_path):
    import build
    import iro
    assert iro.is_folder_mod(idle_mod)
    ents = iro.list_entries(idle_mod)
    assert 'mod.xml' in ents and 'Cloud\\char\\ORIC.A' in ents
    assert iro.read_one(idle_mod, 'cloud/CHAR/oric.a')[:4] == b'\1\0\0\0'
    assert iro.read_one(idle_mod, 'nope.bin') is None
    before = sorted(os.walk(idle_mod))
    m = build.Mod(idle_mod, str(tmp_path / 'cache'))
    assert m.is_folder and m.cache == idle_mod
    assert m.filename == m.stem == 'Idle_Folder_2.2_60fps'
    assert m.ensure_extracted() is False
    assert sorted(os.walk(idle_mod)) == before          # never written to
    assert not os.path.exists(tmp_path / 'cache')
    # first <Name> wins; a non-GUID stray <ID> is not the mod id
    assert m.display_name == '[Tsunamods] Idle Animations - 60fps'
    assert m.manifest.mod_id == ''
    assert [o.id for o in m.manifest.options] == ['CloudAnim', 'BarretAnim']
    # handled by the idle pass, never file by file
    assert m.files_for({}) == []


def test_generic_folder_mod_files(tmp_path):
    import build
    root = tmp_path / 'plain'
    (root / 'Opt' / 'char').mkdir(parents=True)
    (root / 'mod.xml').write_text(
        '<ModInfo><Name>Plain</Name><ID>11111111-2222-3333-4444-555555555555'
        '</ID><ModFolder Folder="opt" ActiveWhen="o=1"/><ConfigOption>'
        '<Type>Bool</Type><Default>1</Default><ID>o</ID><Name>O</Name>'
        '</ConfigOption></ModInfo>')
    (root / 'Opt' / 'char' / 'X.P').write_bytes(b'p')
    m = build.Mod(str(root), str(tmp_path / 'c'))
    m.ensure_extracted()
    assert m.manifest.mod_id == '11111111-2222-3333-4444-555555555555'
    got = m.files_for({'o': 1})
    assert [g[0].replace(os.sep, '/') for g in got] == ['Opt/char/X.P']
    assert m.files_for({'o': 0}) == []


def _nx():
    spec = importlib.util.spec_from_file_location(
        'nx619', os.path.join(HERE, '7th_heaven_nx.py'))
    nx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nx)
    return nx


def test_7h_name_order_brackets_before_digits():
    nx = _nx()
    k = nx._name_key
    assert k('[Tsunamods] Idle Animations - 60fps') < k('60/30 FPS Gameplay')
    assert k('[Tsunamods] Avalanche') < k("AxlRose's Blender v1")
    assert k('Ninostyle Chibi') < k('Ninostyle Chibi Fixes and Additions')
    assert k('cosmos a') < k('Cosmos B') < k('cosmos c')


def test_idle_plan_and_loader(idle_mod, tmp_path):
    import build
    import ff7nx_idleanim as IA
    m = build.Mod(idle_mod, str(tmp_path / 'c'))
    m.ensure_extracted()
    p = IA.plan_for(m, {}, echo=True)
    assert [c[:3] for c in p['chars']] == [('barret', 'ADCB', 'OIBA')]
    assert p['cloud'][1].endswith('cloudanimflevelsecho')
    assert IA.plan_for(m, {}, echo=False)['cloud'][1].endswith(
        'cloudanimflevels')
    assert IA.plan_for(m, {'BarretAnim': 0, 'CloudAnim': 0},
                       echo=True) == {'chars': [], 'cloud': None,
                                      'notes': [], 'mod': idle_mod}
    with open(os.path.join(p['cloud'][1], 'flevel.lgp', 'f1.chunk.3'),
              'rb') as f:
        flags, oric, by_name = IA.cloud_flags(f.read())
    assert flags == [True, False] and oric == _entry('ORIC')
    assert by_name == {b'a_cloud.char': True, b'b_cloud.char': False}
    built = _sec3([_rec('a_cloud.char', 'AAAA.HRC', ['ACFE.aki', 'AAFF.aki']),
                   _rec('b_cloud.char', 'AAAA.HRC', ['ACFE.aki']),
                   _rec('barret.char', 'ACGD.HRC', ['ADCB.aki', 'adcb.aki']),
                   _rec('npc.char', 'FRGD.HRC', ['ACFE.aki'])])
    new, done = IA.plan_field(built, 5, p['chars'], flags, oric)
    assert done == {'OIBA': 2, 'ORIC': 1}
    want = _sec3([_rec('a_cloud.char', 'AAAA.HRC', ['ORIC.aki', 'AAFF.aki']),
                  _rec('b_cloud.char', 'AAAA.HRC', ['ACFE.aki']),
                  _rec('barret.char', 'ACGD.HRC', ['OIBA.aki', 'oiba.aki']),
                  _rec('npc.char', 'FRGD.HRC', ['ACFE.aki'])])
    assert new == want
    assert IA.plan_field(new, 5, p['chars'], flags, oric)[1] == {}
    # field 10 is outside Barret's FieldID gate: his pose is untouched
    new10, done10 = IA.plan_field(built, 10, p['chars'], None, None)
    assert new10 == built and done10 == {}
    # different AAAA count: matched by record name, else all-or-nothing
    one = _sec3([_rec('b_cloud.char', 'AAAA.HRC', ['ACFE.aki'])])
    assert IA.plan_field(one, 5, [], flags, oric, by_name)[1] == {}
    one = _sec3([_rec('a_cloud.char', 'AAAA.HRC', ['ACFE.aki'])])
    assert IA.plan_field(one, 5, [], flags, oric, by_name)[1] == {'ORIC': 1}
    with pytest.raises(ValueError):
        IA.plan_field(_sec3([_rec('zz', 'AAAA.HRC', ['ACFE.aki'])]), 5, [],
                      flags, oric, by_name)


def test_idle_char_bones(idle_mod, tmp_path):
    import build
    import ff7nx_idleanim as IA
    m = build.Mod(idle_mod, str(tmp_path / 'c'))
    m.ensure_extracted()
    IA.CURRENT = IA.plan_for(m, {}, echo=True)
    try:
        lib = {'adcb.a': _anim(1, 21), 'acfe.a': _anim(2, 21)}
        want = IA.plan_char(lib.get)
        assert sorted(want) == ['oiba.a', 'oric.a']
        assert IA.fingerprint()
        lib['adcb.a'] = _anim(1, 23)
        with pytest.raises(ValueError):
            IA.plan_char(lib.get)
    finally:
        IA.CURRENT = None
    assert IA.fingerprint() == ''


def test_idle_real_mod_defaults():
    import build
    import ff7nx_idleanim as IA
    if not os.path.isdir(_IDLE):
        pytest.skip('Idle Animations folder not installed')
    m = build.Mod(_IDLE, '/tmp/unused-cache')
    m.ensure_extracted()
    assert IA.handles(m)
    assert m.display_name == '[Tsunamods] Idle Animations - 60fps'
    p = IA.plan_for(m, {}, echo=True)
    assert sorted(c[0] for c in p['chars']) == sorted(IA.NEW)
    assert all(c[4] is not None for c in p['chars'])
    assert os.path.basename(p['cloud'][1]) == 'cloudanimflevelsecho'
    assert os.path.basename(p['cloud'][0]).upper() == 'ORIC.A'
    assert len(set(IA.NEW.values())) == len(IA.NEW)


_C6 = os.path.join(HERE, 'attic', 'dirs', '_scratch', 'c6')


def test_wheelfix_619_rim_static():
    """ztruck: the tyre's rim (EDGE texels) comes from the static copy, so
    the light outline between tyre and paint no longer changes per frame;
    the tread inside still turns."""
    import pickle
    import numpy as np
    import ff7nx_framesim as FS
    import ff7nx_wheelfix as WF
    pk = os.path.join(_C6, 'ztruck_393.pkl')
    if not os.path.exists(pk):
        pytest.skip('no ztruck fixture')
    P = pickle.load(open(pk, 'rb'))
    Q = list(P)
    Q[8], info = WF.plan('ztruck', bytes(P[8]))
    assert info['cleared'] > 33000
    F = FS.Field(Q)
    cam = (-80, 40)
    imgs = np.stack([F.render(cam=cam, K=3, states={1: b}, layers=(2,),
                              mark=False).astype(float) for b in (1, 2, 4)])
    sd = imgs.std(0).mean(-1)
    # the wheel's canvas: x -112..-48, y 88..120 in field units
    x0 = int(round((-112 - cam[0] + 213.5) * 3))
    y0 = int(round((88 - cam[1] + 120) * 3))
    region = sd[y0:y0 + 96, x0:x0 + 240]
    base = F.render(cam=cam, K=3, states={}, layers=(2,),
                    mark=False).astype(float)[y0:y0 + 96, x0:x0 + 240]
    sat = base.max(-1) - base.min(-1)
    from scipy import ndimage as ND
    paint = sat >= WF.TYRE_SAT                        # the orange arch
    far = ND.distance_transform_edt(~paint)          # px from the paint
    moving = region > 2
    assert moving.sum() > 2000                        # the tread turns
    # nothing that changes between frames touches the paint/tyre boundary
    # (618z14: 487 such pixels within 3 px of the paint, 7 %; now ~16)
    assert (moving & (far < 3)).sum() < 0.01 * moving.sum()


_DUMP_MAIN = os.path.join(HERE, 'dump', 'exefs', 'main')
_DI = os.path.expanduser('~/mnt/7th_heaven_nx/mods/Double-input-fix')


def test_difix_620_classify():
    import ff7nx_difix as D
    hx = '..\\\\ff7.exe\nDelay=3000\n'
    assert D.classify(hx + '0041B09E = 34 87\n0041B0AA = C6\n') == ('difix',
                                                                    None)
    assert D.classify(hx + '0041B15F = BA 3C 00 00 00 90\n') == ('rate', 60)
    assert D.classify(hx + '0041B15F = BA FF FF FF FF 90\n') == ('rate', D.INF)
    assert D.classify(hx + '0041B128 = B8 E8 03 00 00\n') == ('delay', 1000)
    with pytest.raises(ValueError):
        D.classify(hx + '0041B09E = 34 88\n0041B0AA = C6\n')


def test_difix_620_arm64_words(tmp_path):
    """The two fix words and the two value loads are found by signature in
    the translated 0x41B099 / 0x41B108, and patch to the x86 mod's meaning."""
    import struct
    import nxmap
    import ff7nx_difix as D
    if not os.path.exists(_DUMP_MAIN):
        pytest.skip('no exefs/main')
    m = nxmap.Main(_DUMP_MAIN)
    w = {k: v for k, v in D.build_patches(
        m, {'difix': True, 'rate': D.INF, 'delay': 250}).items()}
    assert w == {0x8C39C: (0x11004260, 0x11058260),   # add w0,w19,#0x10->0x160
                 0x8C520: (0x2A1303E0, 0x11003260),   # mov w0,w19 -> add #0xc
                 0x8966C: (0xB9400016, 0x52801F56),   # delay: mov w22,#250
                 0x89730: (0xB9400016, 0x12800016)}   # rate: mov w22,#-1
    # x19 really is 0x9A85D4 there, and +0x160 is the repeat flag 0x9A8734
    assert struct.unpack_from('<I', m.img, 0x8C37C)[0] == 0x5290BA93
    out = tmp_path / 'main'
    assert D.apply_to_nso(_DUMP_MAIN, str(out), {'difix': True, 'rate': None,
                                                 'delay': None})
    mm = nxmap.Main(str(out))
    changed = [i for i in range(0, len(m.img), 4)
               if m.img[i:i + 4] != mm.img[i:i + 4]]
    assert changed == [0x8C39C, 0x8C520]
    # already applied -> nothing written
    assert not D.apply_to_nso(str(out), str(tmp_path / 'again'),
                              {'difix': True, 'rate': None, 'delay': None})


def test_difix_620_plan_from_folder_mod():
    import build
    import ff7nx_difix as D
    if not os.path.isdir(_DI):
        pytest.skip('Double Input Bugfix not installed')
    m = build.Mod(_DI, '/tmp/unused-cache')
    m.ensure_extracted()
    assert D.handles(m)
    st = dict(m.manifest.defaults())
    plan = build.build_plan([m], {m.filename: st}, {})
    assert D.CURRENT['difix'] and D.CURRENT['rate'] is None \
        and D.CURRENT['delay'] is None
    assert plan.hext_files == []                 # nothing left for the exe
    st.update(rate=1, delay=1000)
    build.build_plan([m], {m.filename: st}, {})
    assert (D.CURRENT['rate'], D.CURRENT['delay']) == (D.INF, 1000)
    st.update(difix=0, rate=50, delay=200)
    build.build_plan([m], {m.filename: st}, {})
    assert D.CURRENT is None


def test_artpatch_620_nvmkin_stairs(tmp_path):
    """nvmkin21/22: the staircase export (layer 1 + only the layer-2 tiles
    where layer 1 has none); one edit makes a patch for each field, and
    both end with the same edited colours."""
    import pickle
    import numpy as np
    from PIL import Image
    import ff7nx_artpatch as AP
    pk = os.path.join(HERE, 'attic', 'dirs', '_scratch', 'c7', 'nvmkin21_hw.pkl')
    pk2 = os.path.join(HERE, 'attic', 'dirs', '_scratch', 'c7', 'nvmkin22_hw.pkl')
    if not (os.path.exists(pk) and os.path.exists(pk2)):
        pytest.skip('no nvmkin fixtures')
    P = pickle.load(open(pk, 'rb'))
    P2 = pickle.load(open(pk2, 'rb'))
    org = tmp_path / 'org.png'
    assert AP.export(P, 'nvmkin21', str(org)) == (1200, 768)
    raw, own, *_ = AP._composite(bytes(P[8]), AP.REGIONS['nvmkin21'], True)
    assert (own < 0).sum() < 50              # no holes where stairs are
    o = np.asarray(Image.open(org)).copy()
    o[300:400, 330:430] //= 2                 # on the upper flight
    Image.fromarray(o).save(tmp_path / 'ed.png')
    n, _d = AP.make(P, 'nvmkin21', str(org), str(tmp_path / 'ed.png'),
                    dest=str(tmp_path / 'a.npz'))
    n2, _d = AP.make_also(P2, 'nvmkin22', str(org), str(tmp_path / 'ed.png'),
                          P, 'nvmkin21', dest=str(tmp_path / 'b.npz'))
    assert n == 10000 and 0 < n2 <= 10000
    outs = []
    for f, parts, asset in (('nvmkin21', P, 'a.npz'), ('nvmkin22', P2,
                                                       'b.npz')):
        Q = list(parts)
        Q[8], _info = AP.plan_field(f, Q, AP.load(f, str(tmp_path / asset)))
        AP.export(Q, f, str(tmp_path / (f + '.png')))
        outs.append(np.asarray(Image.open(tmp_path / (f + '.png'))))
    ed = AP._rgb(AP._enc(np.asarray(Image.open(tmp_path / 'ed.png'))))
    for out in outs:
        assert (out[300:400, 330:430] == ed[300:400, 330:430]).all()


# ------------------------------------------------------------ BUILD 620e
def _cat_char():
    import pickle
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(
        __file__))), 'attic', 'dirs', '_scratch', 'c7', 'cat_char.pkl')
    if not os.path.exists(p):
        pytest.skip('no cat char fixture')
    return pickle.load(open(p, 'rb'))


def test_catshades_620e_glasses_cover_the_eyes():
    """NKDA = BDGA with only the head bone's RSD swapped; NKDC = BDHA plus
    the glasses (two windings, black + white), fitted in front of the eyes
    and centred on them; the source entries are untouched."""
    import numpy as np
    import ff7nx_catshades as CS
    if not CS.available():
        pytest.skip('no glasses asset')
    C = _cat_char()
    want, note = CS.plan_char(C.get)
    assert note.startswith('glasses (')
    assert sorted(want) == ['nkda.hrc', 'nkdb.rsd', 'nkdc.p']
    old = C['bdga.hrc'].decode('latin1').splitlines()
    new = want['nkda.hrc'].decode('latin1').splitlines()
    diff = [(a, b) for a, b in zip(old, new) if a != b]
    assert len(old) == len(new) and len(diff) == 1
    assert diff[0][1].split() == ['1', 'NKDB']
    rsd = want['nkdb.rsd'].decode('latin1')
    assert rsd.count('=NKDC.') == 3 and 'BDHA' not in rsd
    h0, S0, _ = CS.parse_p(C['bdha.p'])
    h1, S1, _ = CS.parse_p(want['nkdc.p'])
    nv0 = len(S0['verts']) // 12
    V = np.frombuffer(S1['verts'], '<f4').reshape(-1, 3)
    assert S1['verts'][:len(S0['verts'])] == S0['verts']
    vc = np.frombuffer(S1['vcol'], '<u4')
    rgb = np.stack([(vc >> 16) & 255, (vc >> 8) & 255, vc & 255], 1)
    eye = (rgb[:nv0] == CS.EYE_RGB).all(1)
    G = V[nv0:]
    bones = CS.hrc_bones(want['nkda.hrc'].decode('latin1'))
    M, _ = CS.pose(bones, C['faac.a'], 0)[CS.HEAD_BONE]
    R = M.T @ np.array([1.0, 0, 0])
    U = M.T @ np.array([0, -1.0, 0])
    F = M.T @ np.array([0, 0, -1.0])
    E = V[:nv0][eye]
    # every eye vertex is behind the glasses and inside their outline
    assert (G @ F).max() > (E @ F).max()
    assert (G @ R).min() < (E @ R).min() and (G @ R).max() > (E @ R).max()
    assert (G @ U).min() < (E @ U).min() and (G @ U).max() > (E @ U).max()
    # centred on the eyes left-right
    mid = ((G @ R).min() + (G @ R).max()) / 2 - E.mean(0) @ R
    assert abs(mid) < 0.05
    n_poly = len(S1['polys']) // 24 - len(S0['polys']) // 24
    assert n_poly == 824


def test_catshades_620e_easter_cat_names_nkda_only_when_ready():
    import pickle
    import ff7nx_catshades as CS
    import ff7nx_easteregg as EG
    pu = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(
        __file__))), 'attic', 'dirs', '_scratch', 'c6', 'utmin1_394.pkl')
    if not os.path.exists(pu):
        pytest.skip('no utmin1 probe')
    src3 = bytes(pickle.load(open(pu, 'rb'))[2])
    light = bytes(30)
    was = CS.READY
    try:
        CS.READY = False
        a = EG.cat_record(src3, light)
        CS.READY = True
        b = EG.cat_record(src3, light)
    finally:
        CS.READY = was
    assert len(a) == len(b)
    d = [i for i in range(len(a)) if a[i] != b[i]]
    assert d[-1] - d[0] < 4
    assert a[d[0]:d[0] + 8] == b'BDGA.HRC' and b[d[0]:d[0] + 8] == b'NKDA.HRC'


# ------------------------------------------------------------ BUILD 620f
_C6F = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(
    __file__))), 'attic', 'dirs', '_scratch', 'c6')
_C7F = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(
    __file__))), 'attic', 'dirs', '_scratch', 'c7')


def test_easteregg_620f_cat_only_with_tsuna():
    """Models go to entities in CHAR order, so the cat runs CHAR only under
    Tsuna's own condition (else it would wear his model): both halves of
    its s0 are gated by a copy of his IF, each jumping to that half's RET;
    the cat faces 17 degrees toward him."""
    import pickle
    import field_dis as FD
    import ff7nx_easteregg as EG
    pw = os.path.join(_C6F, 'mds7_w2_394.pkl')
    pu = os.path.join(_C6F, 'utmin1_394.pkl')
    if not (os.path.exists(pw) and os.path.exists(pu)):
        pytest.skip('no mds7_w2 / utmin1 probe')
    P = pickle.load(open(pw, 'rb'))
    U = pickle.load(open(pu, 'rb'))
    s1 = bytes(P[0])
    m, n, e = FD.parse(s1)
    gates = EG.char_gates(s1, m, n, e)
    tsuna = e[n.index('Tsuna')][0]
    assert gates == [s1[tsuna:tsuna + 6]] and gates[0][0] == 0x14
    new, _s3, _info = EG.plan(P[0], P[2], bytes(P[4]), bytes(U[2]))
    m2, n2, e2 = FD.parse(new)
    at = e2[n2.index('nekodev')][0]
    ws = FD.walk(new, at, m2['string_table'])
    ops = [w[3][0] for w in ws[:12]]
    assert ops[:6] == [0x14, 0xA1, 0xA5, 0xB3, 0x7E, 0x00]
    assert ops[6:9] == [0x14, 0xA2, 0x00]
    for w in (ws[0], ws[6]):
        assert w[3][1:5] == gates[0][1:5]
        tgt = w[0] + 5 + w[3][5]
        assert new[tgt] == 0x00                    # each gate lands on RET
    assert ws[0][0] + 5 + ws[0][3][5] == ws[5][0]
    assert ws[3][3][2] == 212                       # dir
    assert EG.DIR == 212


def test_catshades_620f_glasses_higher():
    import ff7nx_catshades as CS
    assert CS.LIFT == 0.04 and CS.GAP == 0.0 and CS.WIDTH == 2.3


def test_destrobe_620f_anfrst_1():
    """anfrst_1's klight loop (ADPAL -1/0 every frame on palettes 12/13)
    becomes ADPAL 0/0: 6 bytes, same size, no strobe left; a field without
    the loop is refused."""
    import pickle
    import field_dis as FD
    import ff7nx_destrobe as DS
    pk = os.path.join(_C7F, 'anfrst_1_hw.pkl')
    if not os.path.exists(pk):
        pytest.skip('no anfrst_1 probe')
    s1 = bytes(pickle.load(open(pk, 'rb'))[0])
    sites = DS.strobe_sites(s1)
    assert len(sites) == 1 and len(sites[0][2]) == 2
    new, _s = DS.plan(s1)
    assert len(new) == len(s1)
    diff = [i for i in range(len(s1)) if s1[i] != new[i]]
    assert len(diff) == 6 and all(s1[i] == 0xFF and new[i] == 0
                                  for i in diff)
    assert not DS.strobe_sites(new)
    FD.parse(new)
    with pytest.raises(ValueError):
        DS.plan(new)
    pw = os.path.join(_C6F, 'mds7_w2_394.pkl')
    if os.path.exists(pw):
        with pytest.raises(ValueError):
            DS.plan(bytes(pickle.load(open(pw, 'rb'))[0]))


def test_palstates_620f_blin68_2_script():
    """light2's MPPAL pulse becomes BGOFF/PLUS|MINUS/BGON/WAIT on params
    8..19; same length; every routine decodes; palette 13 is never loaded."""
    import pickle
    import echo_s_flevel as ES
    import field_dis as FD
    import ff7nx_palstates as P
    vk = os.path.join(_C6F, 'blin68_2_v.pkl')
    if not os.path.exists(vk):
        pytest.skip('no blin68_2 probe')
    s0 = bytes(pickle.load(open(vk, 'rb'))[0])
    new = P.patch_script('blin68_2', s0)
    assert len(new) == len(s0) and new != s0
    for a, b in ES._routine_blocks(new):
        assert not ES._decode_block(new, a, b)[1]
    m, n, e = FD.parse(new)
    at = e[n.index('light2')][0]
    body = new[at:at + 0x4D]
    assert b'\xea' not in body[5:0x4A] and b'\xe6\x00\x30\x0d' not in body
    assert body[:4] == bytes.fromhex('80500b1f')
    assert body.count(bytes.fromhex('e1500b00')) == 2
    assert body.count(bytes.fromhex('e0500b00')) == 2
    assert body[0x1C:0x1E] == b'\x12\x12' and body[0x3C:0x3E] == b'\x12\x12'
    assert b'\x24' not in body[0x0A:0x1C]          # no WAIT: one value a tick
    assert body[0x4A:0x4C] == b'\x12\x40'
    sp = P.SPECS['blin68_2'][0]
    # 620i picked every Cosmos frame; 620j places 24 levels at equal light
    # steps between Cosmos's first and 24th frame -- still one per value
    assert sp.get('even_light') and tuple(sp['even_frames']) == (0, 23)
    assert tuple(sp['params']) == tuple(range(8, 32))


def test_palstates_620f_blin68_2_plan():
    """Palette 13's 93 FX records become 12 HD levels (Cosmos's own frames)
    on truecolor FX pages, one param each; nothing samples palette 13 from
    a paletted page; caps hold; the event palettes 6/7 stay as they were."""
    import pickle
    import diag_common as DC
    import field_bg_pagecap as PC
    import ff7nx_palstates as P
    pk = os.path.join(_C6F, 'blin68_2_hw.pkl')
    vk = os.path.join(_C6F, 'blin68_2_v.pkl')
    if not (os.path.exists(pk) and os.path.exists(vk)):
        pytest.skip('no blin68_2 probe')
    sys.path.insert(0, _C6F)
    try:
        import buildart
        art = buildart.build_art(768)
    except Exception:                                          # noqa: BLE001
        pytest.skip('no Cosmos provider')
    p = list(pickle.load(open(pk, 'rb')))
    v = list(pickle.load(open(vk, 'rb')))
    s0, s9, info = P.plan_field_auto('blin68_2', p, v, art, mb_cap=35.3,
                                     raw_cap=13421772)
    assert info['removed'] == 93 and info['mb'] <= 35.3
    assert info['animated_cells'] >= 55
    pl, ts, _te, px = DC.parse_pages(s9)
    pm = {g.slot: g for g in pl if g is not None}
    lv = {}
    ev = 0
    for _l, offs in DC.walk_layers(s9, s9.find(b'BACK'), ts):
        for o in offs:
            if not s9[o + 28]:
                continue
            if s9[o + 22] == 13:
                assert pm[s9[o + 34]].depth == 2
                lv[s9[o + 26]] = lv.get(s9[o + 26], 0) + 1
            elif s9[o + 22] in (6, 7):
                assert pm[s9[o + 34]].depth == 1
                ev += 1
    assert sorted(k for k in lv if k) == list(range(8, 32))
    assert ev == 397
    assert max(PC.effective_counts(s9, px).values()) <= 256


# ------------------------------------------------------------ BUILD 620g
def test_voice_620g_services_write_the_heartbeat():
    import ff7nx_voice as V
    base, scratch = 0x00200000, 0x00500000
    words = V._build_field_clocked_voice_service(
        base, lambda i: base + 4 * i, scratch)
    hb = [i for i, w in enumerate(words[:12]) if w & 0xFFFFFFE0 == V.MRS_CNTPCT]
    assert len(hb) == 1
    import a64 as A
    assert words[hb[0] + 1] == A.str64(words[hb[0]] & 31, 19,
                                       V.VOICE_HEARTBEAT_OFF)
    assert V.VOICE_HEARTBEAT_OFF % 8 == 0
    assert V.VOICE_HEARTBEAT_OFF + 8 <= V.VOICE_PAGE_OFF + 256


def test_palstates_620g_match_1x_retired_for_blin68_2():
    """620j: blin68_2 no longer scales per channel to the 1x palette (that
    pushed the core into the ceiling: 'too bright ... brightness spreads
    out'); _match_1x stays available to specs that ask for it."""
    import ff7nx_palstates as P
    sp = P.SPECS['blin68_2'][0]
    assert 'match_1x' not in sp and sp.get('even_light')
    assert callable(P._match_1x)


# ------------------------------------------------------------ BUILD 620h
def test_floordepth_620h_mds7pb_1():
    """Floor squares move to layer 2 at the floor's depth: same record count
    and section size, background pixels identical, nothing standing on the
    walkable floor newly hidden, points under the floor beside the shaft
    hidden far more often, Cloud inside the shaft not hidden more."""
    import pickle
    import struct as _s
    import numpy as np
    import ff7nx_floordepth as F
    import ff7nx_framesim as FS
    import ff7nx_ripplefade as RF
    pk = os.path.join(_C6F, 'mds7pb_1_fd_probe.pkl')
    if not os.path.exists(pk):
        pytest.skip('no mds7pb_1 probe')
    parts, _v = pickle.load(open(pk, 'rb'))
    s9_old = bytes(parts[8])
    s9, info = F.plan('mds7pb_1', parts[1], s9_old)
    # 620k5b: + the three lip-split copies
    assert len(s9) == len(s9_old) + 52 * info['lip_split']
    assert info['moved'] > 450   # 620j: 469
    A, B = info['curve']
    assert abs(A - 1.0163) < 1e-3 and abs(B - 130.1) < 0.5
    n_old = {r[0]: r[3] for r in RF._rows(s9_old)}
    n_new = {r[0]: r[3] for r in RF._rows(s9)}
    assert n_old[1] + n_old[2] + info['lip_split'] == n_new[1] + n_new[2]
    N = list(parts)
    N[8] = s9
    a = FS.Field(parts).render(cam=(0, 0), K=1, mark=False)
    b = FS.Field(N).render(cam=(0, 0), K=1, mark=False)
    assert not (a != b).any()
    R, o, zoom = F._camera(parts[1])

    def idmap(buf):
        m = np.full((600, 800), 99999)
        for layer, _c, first, n in RF._rows(buf):
            if layer != 2:
                continue
            for i in range(n):
                r = buf[first + i * 52:first + (i + 1) * 52]
                x, y = _s.unpack_from('<hh', r, 2)
                idv = _s.unpack_from('<h', r, 24)[0]
                if idv <= 16 or r[26]:
                    continue
                sl = (slice(y + 300, y + 316), slice(x + 400, x + 416))
                m[sl] = np.minimum(m[sl], idv)
        return m

    def hidden(P, m):
        C = (R @ np.asarray(P, float).T).T + o
        sx = np.floor(zoom * C[:, 0] / C[:, 2]).astype(int) + 400
        sy = np.floor(zoom * C[:, 1] / C[:, 2]).astype(int) + 300
        return C[:, 2] / 4.0 > m[sy, sx]
    m0, m1 = idmap(s9_old), idmap(s9)
    walk = bytes(parts[4])
    nt = _s.unpack_from('<I', walk, 0)[0]
    T = np.array([_s.unpack_from('<12h', walk, 4 + 24 * i)
                  for i in range(nt)]).reshape(nt, 3, 4)[:, :, :3]
    stand = [t.mean(0) + np.array([0, 0, h]) for t in T
             if t[:, 2].min() >= 0 for h in (-10, 0, 2, 40, 120)]  # 667c: soles at -10
    assert not (hidden(stand, m1) & ~hidden(stand, m0)).any()
    under = [(x, y, z) for z in range(-25, -100, -10)   # 667c: below the soles
             for x in range(-120, 50, 8) for y in range(110, 290, 8)
             if not (-78 <= x <= -3 and 148 <= y <= 254)
             and not (-79 <= x <= -3 and 288 <= y <= 338)]
    vis0 = (~hidden(under, m0)).sum()
    vis1 = (~hidden(under, m1)).sum()
    # 620h: 1303 -> 600; 620j leaves the pinball strip (y 254+) vanilla: 753
    assert vis1 < 0.6 * vis0
    shaft = [(x, y, z) for z in range(-5, -200, -15)
             for x in range(-72, -6, 8) for y in range(152, 252, 8)]
    # 620k5: the three front-left lip squares sit behind the plate's front
    # face, so the shaft just behind that lip (first few units down, front
    # rows) may show; everything deeper or further back is as before.
    shaft = np.array(shaft)
    near = (shaft[:, 2] >= -35) & (shaft[:, 1] <= 176)
    h0, h1 = hidden(shaft, m0), hidden(shaft, m1)
    assert (h0 == h1)[~near].all()
    assert not (h1 & ~h0).any()
    assert (h0 & ~h1).sum() <= 0.5 * near.sum()


# ------------------------------------------------------------ BUILD 620i
def _pause_rig(paused, state, stale, same=True, hb=True):
    import arm64emu as E
    import ff7nx_voice as V
    base, scratch = 0x00200000, 0x00500000
    words = V._build_worker_pause(base, lambda i: base + 4 * i, scratch)
    mem = E.Mem()
    calls = []
    PAUSE, RESUME = 0x00900000, 0x00900100
    player, rend, vt = 0x00600000, 0x00610000, 0x00620000

    def tick(cpu):
        calls.append('now')
        for r in range(1, 19):
            cpu.x[r] = 0xDEAD
        cpu.x[0] = 777

    def pause(cpu):
        assert cpu.x[0] == rend
        calls.append('pause')
        for r in range(1, 19):
            cpu.x[r] = 0xDEAD
    cpu = E.Cpu(mem, native={V.HOST_TICK_NOW: tick, PAUSE: pause})
    mem.setu(scratch + V.VOICE_PLAYER_OFF, player if same else 1, 8)
    mem.setu(scratch + V.VOICE_PAUSED_OFF, paused, 4)
    now = 100 * V.CNTPCT_HZ
    mem.setu(scratch + V.VOICE_HEARTBEAT_OFF,
             (now - V.VOICE_STALL_TICKS - 5 if stale else now - 1000)
             if hb else 0, 8)
    mem.setu(player + 0x80, rend, 8)
    mem.setu(player + 0x98, state, 4)
    mem.setu(rend, vt, 8)
    mem.setu(vt + V.RENDERER_PAUSE_VTBL_OFF, PAUSE, 8)
    cpu.cntpct = now
    cpu.x[19] = player
    cpu.x[20], cpu.x[21], cpu.x[22] = 0x20, 0x21, 0x22
    out = cpu.run(base, words)
    return (out, calls, mem.u(scratch + V.VOICE_PAUSED_OFF, 4),
            mem.u(scratch + V.VOICE_PAUSE_T0_OFF, 8), cpu)


def test_voice_620i_worker_pauses_only_when_the_game_is_frozen():
    import ff7nx_voice as V
    out, calls, flag, t0, c = _pause_rig(0, 2, True)
    assert out == V.WORKER_HEAD_RESUME and calls == ['now', 'pause']
    assert flag == 1 and t0 == 777
    assert c.x[8] == 2                       # displaced ldr w8,[x19,#0x98]
    assert (c.x[19], c.x[20], c.x[21], c.x[22]) == (
        0x00600000, 0x20, 0x21, 0x22)
    for args in ((0, 2, False), (1, 2, True), (0, 8, True), (0, 1, True)):
        out, calls, flag, _t, c = _pause_rig(*args)
        assert out == V.WORKER_HEAD_RESUME and calls == [] and c.x[8] in (
            2, 8, 1)
        assert flag == args[0]
    assert _pause_rig(0, 2, True, same=False)[1] == []
    assert _pause_rig(0, 2, True, hb=False)[1] == []


def test_voice_620i_service_resume_extends_the_deadline():
    import arm64emu as E
    import ff7nx_voice as V
    base, scratch = 0x00200000, 0x00500000
    words = V._build_service_resume(base, lambda i: base + 4 * i, scratch)
    RESUME = 0x00900100
    player, rend, vt = 0x00600000, 0x00610000, 0x00620000
    for paused in (1, 0):
        mem = E.Mem()
        calls = []

        def tick(cpu):
            calls.append('now')
            cpu.x[0] = 5000

        def resume(cpu):
            assert cpu.x[0] == rend
            calls.append('resume')
        cpu = E.Cpu(mem, native={V.HOST_TICK_NOW: tick, RESUME: resume})
        mem.setu(scratch + V.VOICE_PAUSED_OFF, paused, 4)
        mem.setu(scratch + V.VOICE_PAUSE_T0_OFF, 3000, 8)
        mem.setu(scratch + V.VOICE_DEADLINE_OFF, 10000, 8)
        mem.setu(scratch + V.VOICE_PLAYER_OFF, player, 8)
        mem.setu(player + 0x80, rend, 8)
        mem.setu(rend, vt, 8)
        mem.setu(vt + V.RENDERER_RESUME_VTBL_OFF, RESUME, 8)
        cpu.sp = 0x00800000
        cpu.x[30] = 0x00C0FFEE
        cpu.x[19] = 0x1919
        out = cpu.run(base, words)
        assert out == 0x00C0FFEE and cpu.sp == 0x00800000
        assert cpu.x[19] == 0x1919
        if paused:
            assert calls == ['now', 'resume']
            assert mem.u(scratch + V.VOICE_DEADLINE_OFF, 8) == 12000
            assert mem.u(scratch + V.VOICE_PAUSED_OFF, 4) == 0
        else:
            assert calls == []
            assert mem.u(scratch + V.VOICE_DEADLINE_OFF, 8) == 10000


def test_fxrequant_620i_ghotel_fog_is_dithered():
    """ghotel's fog: same entries count, every frame still a palette pulse,
    but texels are error-diffused, so neighbouring texels of a smooth
    gradient use different entries far more often than nearest-colour."""
    import pickle
    import numpy as np
    import ff7nx_fxrequant as RQ
    pk = os.path.join(_C6F, 'ghotel_hw.pkl')
    if not os.path.exists(pk):
        pytest.skip('no ghotel probe')
    sys.path.insert(0, _C6F)
    try:
        import buildart
        art = buildart.build_art(768)
    except Exception:                                          # noqa: BLE001
        pytest.skip('no Cosmos provider')
    P = pickle.load(open(pk, 'rb'))
    # 620j: ghotel is split by palstates and back on nearest-colour here;
    # the dither path itself stays and is exercised explicitly
    assert 'ghotel' not in RQ.DITHER_FIELDS
    was = RQ.DITHER_FIELDS
    near, st0 = RQ.plan_field('ghotel', list(P), art)
    try:
        RQ.DITHER_FIELDS = frozenset(('ghotel',))
        dith, st1 = RQ.plan_field('ghotel', list(P), art)
    finally:
        RQ.DITHER_FIELDS = was
    assert st0['cells'] == st1['cells'] == 348
    # a dithered page changes entry between horizontal neighbours far more
    def changes(parts):
        import diag_common as DC
        pl, _ts, _te, _px = DC.parse_pages(parts[8])
        a = np.frombuffer([p for p in pl if p and p.slot == 15][0].data,
                          np.uint8, count=65536).reshape(256, 256)
        live = (a[:, 1:] > 0) & (a[:, :-1] > 0)
        return float((a[:, 1:] != a[:, :-1])[live].mean())
    assert changes(dith) > 1.5 * changes(near)


# ------------------------------------------------------------ BUILD 620j
def test_floordepth_620j_pinball_machine_never_hidden():
    """mds7pb_1, hardware 10-07: the wall and the squares beside / under
    the pinball lift drew over the rising machine. No point of the machine's
    whole travel (z -280..+180 over its shaft) is hidden by a converted
    square, nothing standing on the floor is newly hidden, and the squares
    around Cloud's lift keep their depth."""
    import pickle
    import struct as _s
    import numpy as np
    import ff7nx_floordepth as F
    import ff7nx_ripplefade as RF
    pk = os.path.join(_C6F, 'mds7pb_1_fd_probe.pkl')
    if not os.path.exists(pk):
        pytest.skip('no mds7pb_1 probe')
    parts, _v = pickle.load(open(pk, 'rb'))
    s9_old = bytes(parts[8])
    s9, info = F.plan('mds7pb_1', parts[1], s9_old)
    R, o, zoom = F._camera(parts[1])

    def idmap(buf):
        m = np.full((600, 800), 99999)
        for layer, _c, first, n in RF._rows(buf):
            if layer != 2:
                continue
            for i in range(n):
                r = buf[first + i * 52:first + (i + 1) * 52]
                x, y = _s.unpack_from('<hh', r, 2)
                idv = _s.unpack_from('<h', r, 24)[0]
                if idv <= 16 or r[26]:
                    continue
                sl = (slice(y + 300, y + 316), slice(x + 400, x + 416))
                m[sl] = np.minimum(m[sl], idv)
        return m

    def hidden(P, m):
        C = (R @ np.asarray(P, float).T).T + o
        sx = np.floor(zoom * C[:, 0] / C[:, 2]).astype(int) + 400
        sy = np.floor(zoom * C[:, 1] / C[:, 2]).astype(int) + 300
        return C[:, 2] / 4.0 > m[sy, sx]
    m0, m1 = idmap(s9_old), idmap(s9)
    g = np.mgrid[-85:4:4, 282:345:4, -280:181:5].reshape(3, -1).T
    assert not (hidden(g, m1) & ~hidden(g, m0)).any()
    under = [(x, y, z) for z in range(-25, -100, -10)   # 667c: below the soles
             for x in range(-120, 50, 8) for y in range(110, 250, 8)
             if not (-78 <= x <= -3 and 148 <= y <= 254)]
    assert (~hidden(under, m1)).sum() < 0.6 * (~hidden(under, m0)).sum()


def _blin68_2_planned():
    import pickle
    import ff7nx_palstates as P
    pk = os.path.join(_C6F, 'blin68_2_hw.pkl')
    vk = os.path.join(_C6F, 'blin68_2_v.pkl')
    if not (os.path.exists(pk) and os.path.exists(vk)):
        pytest.skip('no blin68_2 probe')
    sys.path.insert(0, _C6F)
    try:
        import buildart
        art = buildart.build_art(768)
    except Exception:                                          # noqa: BLE001
        pytest.skip('no Cosmos provider')
    p = list(pickle.load(open(pk, 'rb')))
    v = list(pickle.load(open(vk, 'rb')))
    return P.plan_field_auto('blin68_2', p, v, art, mb_cap=35.3,
                             raw_cap=13421772)


def test_palstates_620j_blin68_2_levels_rise_evenly_without_ceiling():
    """Hardware 10-07 (620i): 'too bright ... one part gets brighter, stays
    that brightness, then that brightness spreads out ... steps up again'.
    Every animated record's 24 levels, as stored: no texel ever dims by more
    than one 565 step from one level to the next, no channel reaches the
    565 ceiling (248), and the light added per level is even."""
    import collections
    import struct as _s
    import numpy as np
    import diag_common as DC
    _s0, s9, info = _blin68_2_planned()
    assert info.get('even_light')
    pl, ts, _te, px = DC.parse_pages(s9)
    pm = {g.slot: g for g in pl if g is not None}
    k = px // 16
    lev = collections.defaultdict(dict)
    for _l, offs in DC.walk_layers(s9, s9.find(b'BACK'), ts):
        for o in offs:
            if s9[o + 22] != 13 or not s9[o + 28] or not 8 <= s9[o + 26] <= 31:
                continue
            u, v = _s.unpack_from('<II', s9, o + 42)
            cx, cy = u // 625000, v // 625000
            a = np.frombuffer(pm[s9[o + 34]].data, '<u2').reshape(px, px)[
                cy * k:(cy + 1) * k, cx * k:(cx + 1) * k].astype(np.int32)
            rgb = np.stack([(a >> 11) & 31, (a >> 6) & 31, a & 31], -1) * 8
            key = (_s.unpack_from('<hh', s9, o + 2), s9[o + 38:o + 42])
            lev[key][s9[o + 26]] = rgb
    full = [d for d in lev.values() if sorted(d) == list(range(8, 32))]
    assert len(full) >= 55
    tot = np.zeros(24)
    for d in full:
        st = np.stack([d[p] for p in range(8, 32)])
        assert st.max() < 248
        assert (np.diff(st, axis=0) >= -8).all()
        tot += st.sum((1, 2, 3))
    steps = np.diff(tot)
    assert steps.min() > 0.75 * steps.mean() and \
        steps.max() < 1.25 * steps.mean()


def test_palstates_620j_dither_is_keyed_to_the_screen():
    """The same record's image stored at two page cells encodes to the same
    565 words (its levels no longer shimmer by a dither step)."""
    import numpy as np
    import ff7nx_palstates as P
    rng = np.random.default_rng(3)
    img = rng.uniform(0, 255, (48, 48, 3)).astype(np.float32)
    enc = P._encode_cells({5: img, 77: img}, 768, {5: (-40, 32),
                                                   77: (-40, 32)})
    a = enc[0:48, 5 * 48:6 * 48]
    b = enc[4 * 48:5 * 48, 13 * 48:14 * 48]
    assert (a == b).all()
    enc2 = P._encode_cells({5: img}, 768, {5: (-24, 32)})
    assert (enc2[0:48, 5 * 48:6 * 48] != a).any()     # noise does vary


def test_palstates_620j_ghotel_split():
    """ghotel's fog: a static truecolor base for every record + the original
    paletted records in blend 3 carrying the pulse; light2 rebased to
    2*(v-40); pulse entries never overflow 5 bits; cells proven in place."""
    import pickle
    import diag_common as DC
    import ff7nx_fxrequant as RQ
    import ff7nx_marginblack as MB
    import ff7nx_palstates as P
    pk = os.path.join(_C6F, 'ghotel_hw.pkl')
    vk = os.path.join(_C6F, 'ghotel_v.pkl')
    if not (os.path.exists(pk) and os.path.exists(vk)):
        pytest.skip('no ghotel probe')
    sys.path.insert(0, _C6F)
    try:
        import buildart
        art = buildart.build_art(768)
    except Exception:                                          # noqa: BLE001
        pytest.skip('no Cosmos provider')
    near, _st = RQ.plan_field('ghotel', list(pickle.load(open(pk, 'rb'))),
                              art)
    v = list(pickle.load(open(vk, 'rb')))
    s0, s9, info = P.plan_field_auto('ghotel', list(near), v, art,
                                     mb_cap=35.3)
    assert info['split'] == 348 and info['removed'] == 0
    assert info['split_check'][1] < P.SPLIT_MAX_ERR
    # 620k2: fitted through the port's transfer (c = 1 shows 0)
    assert info['split_pulse_err'][1] <= 6.0
    assert 0.0 < info['split_depth'] <= 1.0
    old, new = P._ghotel_patches()[0][:2]
    assert near[0].count(old) == 1 and s0.count(new) == 1
    pl, ts, _te, _px = DC.parse_pages(s9)
    pm = {g.slot: g for g in pl if g is not None}
    blend = {}
    for _l, offs in DC.walk_layers(s9, s9.find(b'BACK'), ts):
        for o in offs:
            if s9[o + 22] == 8 and s9[o + 28]:
                d = pm[s9[o + 34]].depth
                blend[(d, s9[o + 30])] = blend.get((d, s9[o + 30]), 0) + 1
    assert blend[(1, 3)] == 348 and blend[(2, 1)] >= 280
    assert (1, 1) not in blend
    cols, _h, npg, cpp = MB.palette_colours(info['sec3'])
    c = cols.reshape(npg, cpp)[8].astype(int)
    emax = max(int(((c[1:] >> sh) & 31).max()) for sh in (0, 5, 10))
    assert emax * P.GHOTEL_STEP * 22 >> 6 <= 31


def test_subparallax_620j_caves_execute():
    """The 12 caves, installed in the real module and executed from their
    sites (the translator garbles x0..x18/x30 as its contract allows):
    capture = the engine's sbfx + the dropped byte saved; pick = the store
    minus ((byte + (pos & 15) * 16) * mult) / 256, callee-saved registers
    and sp intact."""
    import random
    import struct as _s
    import arm64emu as E
    import ff7nx_audio_cave as AC
    import ff7nx_subparallax as SP
    import nxmap
    src = os.path.join(HERE, 'dump', 'exefs', 'main')
    if not os.path.exists(src):
        pytest.skip('no exefs/main')
    m0 = nxmap.Main(src)
    assert SP.check_sites(m0.img) == []
    blob = open(src, 'rb').read()
    segs, raw = AC.segments(blob)
    bss = AC.scratch_base(blob, segs)
    words = SP.build_patches(m0.img, set(m0.arm_starts), bss,
                             log=lambda *_: None)
    img = bytearray(m0.img)
    for va, w in words.items():
        _s.pack_into('<I', img, va, w)

    def code_from(site):
        w = _s.unpack_from('<I', img, site)[0]
        imm = (w & 0x3FFFFFF) - ((1 << 26) if w & (1 << 25) else 0)
        code = {site: w}
        va = site + 4 * imm
        for _ in range(200):
            w = _s.unpack_from('<I', img, va)[0]
            code[va] = w
            if (w & 0xFFFFFC1F) == 0xD65F0000:
                break
            if (w >> 26) == 5:
                imm = (w & 0x3FFFFFF) - ((1 << 26) if w & (1 << 25) else 0)
                va += 4 * imm
                continue
            va += 4
        return code
    rng = random.Random(7)
    for paged in (False, True):
        for _t in range(40):
            mem = E.Mem()
            cpu = E.Cpu(mem, paged=paged)
            cpu.sp = 0x7FFF0000
            got = {}
            for site, slot in SP.CAPTURE:
                p = rng.getrandbits(32)
                cpu.x[8] = p
                assert cpu.run(site, code_from(site)) == site + 4
                want = ((E.s32(p) >> 8) & 0xFFFF ^ 0x8000) - 0x8000
                assert E.s32(cpu.x[8] & 0xFFFFFFFF) == want
                assert mem.u(bss + slot, 1) == p & 0xFF
                got[slot] = p & 0xFF
            H = 0x00D80000 + rng.randrange(0, 0x1000, 2)
            for site, slot, off in SP.PICK:
                pos = rng.randint(-32768, 32767)
                mult = rng.choice((1, 2))
                mem.setu(cpu.guest_to_host(SP.HEADER_PTR), H, 4)
                mem.setu(cpu.guest_to_host(H + off), pos & 0xFFFF, 2)
                mem.setu(cpu.guest_to_host(SP.MULT), mult, 4)
                f = _s.unpack('<f', _s.pack('<f', rng.uniform(-400, 400)))[0]
                cpu.fp[8] = _s.unpack('<I', _s.pack('<f', f))[0]
                host = 0x55550000
                cpu.x[0] = host
                keep = {r: rng.getrandbits(64) for r in range(19, 29)}
                for r, val in keep.items():
                    cpu.x[r] = val
                sp0 = cpu.sp
                assert cpu.run(site, code_from(site)) == site + 4
                out = _s.unpack('<f', mem.read(host, 4))[0]
                want = f - ((got[slot] + (pos & 15) * 16) * mult) / 256.0
                assert abs(out - want) <= 1e-4 + 1e-6 * abs(want)
                assert cpu.sp == sp0 and cpu.x[0] == host
                assert all(cpu.x[r] == val for r, val in keep.items())


# ------------------------------------------------------------ BUILD 620k
def test_floordepth_620k_pinball_lift_script():
    """620k5c: the machine stands UP at (-40, 315); Cloud and Tifa ride
    straight up at (-35, 170) (shaft triangle 1 -> triangle 20) and the
    machine's collision radius is 100, so a rider with radius up to 45 is
    outside it when the walk-off MOVE starts (hardware 10-08: Tifa ran in
    place); applying twice changes nothing; nothing else moves."""
    import math
    import pickle
    import ff7nx_floordepth as F
    pk = os.path.join(HERE, 'attic', 'dirs', '_scratch', 'c7', 'shades', 'mds7pb_1.pkl')
    if not os.path.exists(pk):
        pytest.skip('no mds7pb_1 probe')
    parts, v = pickle.load(open(pk, 'rb'))
    for s0 in (bytes(parts[0]), bytes(v[0])):
        new, n = F.plan_script('mds7pb_1', s0)
        assert n == 8 and len(new) == len(s0)
        assert new.count(bytes.fromhex('a50000D8FF3B0100000000')) == 2
        assert new.count(bytes.fromhex('c20000D8FF3B0100000000')) == 1
        assert new.count(bytes.fromhex('a50000DDFFAA0001FF0100')) == 2
        assert new.count(bytes.fromhex('c20000DDFFAA0000001400')) == 2
        assert new.count(bytes.fromhex('c60064')) == 1
        assert bytes.fromhex('c60078') not in new
        diff = sum(a != b for a, b in zip(new, s0))
        assert diff <= 16
        again, n2 = F.plan_script('mds7pb_1', new)
        assert n2 == 0 and again == new
    assert math.hypot(-35 + 40, 170 - 315) > 100 + 45


def test_floordepth_620k5_plate_front_left_lip_squares():
    """620k5: exactly the six layer-2 records at tiles (48, 0) (48, 16)
    (64, 16) -- the opening's front-left lip -- move behind the plate's
    front/left faces (face depth + FACE_MARGIN, order kept, IDBig rewritten); the planks
    at (80, *) and everything else keep their IDs; Cloud's ride column under
    the floor shows new points only in its first 35 units below the floor; a second
    plan gives the same section."""
    import pickle
    import struct as _s
    import numpy as np
    import ff7nx_floordepth as F
    pk = os.path.join(HERE, 'attic', 'dirs', '_scratch', 'c7', 'shades', 'mds7pb_1.pkl')
    if not os.path.exists(pk):
        pytest.skip('no mds7pb_1 probe')
    parts, _v = pickle.load(open(pk, 'rb'))
    van = bytes(parts[8])
    assert F.FACE_MARGIN == 4
    split_new, info = F.plan('mds7pb_1', parts[1], van)
    assert info['plate_faces'] == 6 and info['lip_split'] == 4
    saved_split = F._lip_split
    F._lip_split = lambda *a: (a[-1], 0)
    try:
        new, _i = F.plan('mds7pb_1', parts[1], van)
    finally:
        F._lip_split = saved_split
    cfg = F.FIELDS['mds7pb_1']
    saved = cfg['plate_faces']
    try:
        cfg['plate_faces'] = ()
        ref, _i = F.plan('mds7pb_1', parts[1], van)
    finally:
        cfg['plate_faces'] = saved
    assert len(new) == len(ref)
    moved = {}
    for layer, _c, first, n in F._rows(new):
        for i in range(n):
            o = first + i * 52
            if new[o:o + 52] != ref[o:o + 52]:
                assert new[o:o + 24] == ref[o:o + 24]
                assert new[o + 26:o + 38] == ref[o + 26:o + 38]
                xy = _s.unpack_from('<hh', new, o + 2)
                moved.setdefault(xy, []).append(
                    (_s.unpack_from('<h', ref, o + 24)[0],
                     _s.unpack_from('<h', new, o + 24)[0]))
    assert sorted(moved) == [(48, 0), (48, 16), (64, 16)]
    for xy, v in moved.items():
        assert len(v) == 2
        old = sorted(v)
        assert all(b > a for a, b in old)
        assert old[0][1] < old[1][1]            # order kept
        assert 1374 <= min(b for _a, b in old) <= 1384
    R, o, zoom = F._camera(parts[1])

    def idmap(buf):
        m = np.full((600, 800), 99999)
        st = {2: 1, 3: 1, 5: 1, 6: 1}
        for layer, _c, first, n in F._rows(buf):
            if layer != 2:
                continue
            for i in range(n):
                r = buf[first + i * 52:first + (i + 1) * 52]
                x, y = _s.unpack_from('<hh', r, 2)
                w = max(_s.unpack_from('<HH', r, 18)) or 16
                idv = _s.unpack_from('<h', r, 24)[0]
                if idv <= 16 or (r[26] and not (st.get(r[26], 0) & r[27])):
                    continue
                sl = (slice(y + 300, y + 300 + w), slice(x + 400, x + 400 + w))
                m[sl] = np.minimum(m[sl], idv)
        return m

    def visible(P, m):
        C = (R @ np.asarray(P, float).T).T + o
        ix = np.floor(zoom * C[:, 0] / C[:, 2]).astype(int) + 400
        iy = np.floor(zoom * C[:, 1] / C[:, 2]).astype(int) + 300
        return C[:, 2] / 4 <= m[iy, ix]
    ride = np.array([(x, y, z) for x in range(-50, -19, 3)
                     for y in range(172, 206, 3) for z in range(-255, 1, 5)])
    newly = visible(ride, idmap(new)) & ~visible(ride, idmap(ref))
    assert newly.sum() <= 80 and (ride[newly][:, 2] >= -35).all()
    again, info2 = F.plan('mds7pb_1', parts[1], van)
    assert again == split_new and info2 == info


def test_floordepth_620k5b_lip_split_keeps_the_wood_in_front():
    """620k5b: the three pushed lip squares are split along the opening's
    outer rim: the pushed record keeps only texels inside it, a copy at the
    old ID and IDBig (in a free cell of the same page, UV pair set) keeps
    the wood outside; the background renders pixel-identical at three
    cameras; nothing else in the field changes."""
    import pickle
    import struct as _s
    import numpy as np
    import ff7nx_floordepth as F
    import ff7nx_framesim as FS
    pk = os.path.join(HERE, 'attic', 'dirs', '_scratch', 'c7', 'shades', 'mds7pb_1.pkl')
    if not os.path.exists(pk):
        pytest.skip('no mds7pb_1 probe')
    parts, _v = pickle.load(open(pk, 'rb'))
    van = bytes(parts[8])
    new, info = F.plan('mds7pb_1', parts[1], van)
    assert info['lip_split'] == 4         # 3 lip squares + the corner piece
    assert len(new) == len(van) + 4 * 52
    olds = {(o, old, big) for o, old, _n, big in F.PUSHED}
    copies = []
    for layer, _c, first, n in F._rows(new):
        for i in range(n):
            o = first + i * 52
            r = new[o:o + 52]
            xy = _s.unpack_from('<hh', r, 2)
            if layer == 2 and xy in ((48, 0), (48, 16), (64, 16)) \
                    and not r[26]:
                idv = _s.unpack_from('<h', r, 24)[0]
                if idv in (1370, 1372) and r[10:14] != van[o:o + 52][10:14] \
                        if False else idv in (1370, 1372):
                    copies.append(r)
                    u, v = _s.unpack_from('<II', r, 42)
                    sx, sy = _s.unpack_from('<hh', r, 10)
                    assert (u, v) == (sx // 16 * 625000, sy // 16 * 625000)
                    big = _s.unpack_from('<I', r, 38)[0]
                    assert any(old == idv and b == big for _o, old, b in olds)
    assert len(copies) == 4
    st = {2: 1, 3: 1, 5: 1, 6: 1}
    for cam in ((-23.667, 45), (1, 45), (17.333, 33)):
        a = FS.Field(list(parts)).render(cam=cam, states=st, K=3,
                                         mark=False)
        b = FS.Field([*parts[:8], new]).render(cam=cam, states=st, K=3,
                                               mark=False)
        assert (a == b).all()


def test_floordepth_620k_squares_by_the_table_take_the_floor_depth():
    """The 1997 layer-2 floor squares in front of Cloud's lift carried IDs
    up to 26 past the floor: they are pulled up to it (same cell order,
    background identical), nothing standing on the floor or the machine is
    newly hidden, and the body sinking just under the floor in front of the
    opening is hidden where it showed before."""
    import pickle
    import struct as _s
    import numpy as np
    import ff7nx_floordepth as F
    import ff7nx_ripplefade as RF
    pk = os.path.join(HERE, 'attic', 'dirs', '_scratch', 'c7', 'shades', 'mds7pb_1.pkl')
    wk = os.path.join(HERE, 'attic', 'dirs', '_scratch', 'c7', 'shades', 'pb_walk.npy')
    if not (os.path.exists(pk) and os.path.exists(wk)):
        pytest.skip('no mds7pb_1 probe')
    parts, _v = pickle.load(open(pk, 'rb'))
    van = bytes(parts[8])
    new, info = F.plan('mds7pb_1', parts[1], van)
    assert info['relayered'] >= 12          # 667c: 16 with the sole sink
    R, o, zoom = F._camera(parts[1])

    def idmap(buf):
        m = np.full((600, 800), 99999)
        for layer, _c, first, n in RF._rows(buf):
            if layer != 2:
                continue
            for i in range(n):
                r = buf[first + i * 52:first + (i + 1) * 52]
                x, y = _s.unpack_from('<hh', r, 2)
                w = max(_s.unpack_from('<HH', r, 18)) or 16
                idv = _s.unpack_from('<h', r, 24)[0]
                if idv <= 16 or r[26] not in (0, 4):
                    continue
                sl = (slice(y + 300, y + 300 + w), slice(x + 400, x + 400 + w))
                m[sl] = np.minimum(m[sl], idv)
        return m

    def hidden(P, m):
        C = (R @ np.asarray(P, float).T).T + o
        ix = np.floor(zoom * C[:, 0] / C[:, 2]).astype(int) + 400
        iy = np.floor(zoom * C[:, 1] / C[:, 2]).astype(int) + 300
        return C[:, 2] / 4 > m[iy, ix]
    mv, mn = idmap(van), idmap(new)
    T = np.load(wk)
    rng = np.random.default_rng(1)
    pts = []
    for t in T:
        if t[:, 2].min() < 0:
            continue
        for _ in range(40):
            a, b = rng.random(2)
            if a + b > 1:
                a, b = 1 - a, 1 - b
            p = t[0] + a * (t[1] - t[0]) + b * (t[2] - t[0])
            for h in (-10, 0, 5, 80):           # 667c: soles at -10
                pts.append(p + np.array([0, 0, h]))
    pts = np.array(pts)
    assert not (hidden(pts, mn) & ~hidden(pts, mv)).any()
    g = np.mgrid[-88:2:3, 282:345:3, -285:181:5].reshape(3, -1).T
    assert not (hidden(g, mn) & ~hidden(g, mv)).any()
    und = np.array([(x, y, z) for z in range(-25, -120, -5)   # 667c
                    for x in range(-100, 40, 4) for y in range(90, 150, 4)])
    saved = F.FIELDS['mds7pb_1']['plate_faces']
    try:
        F.FIELDS['mds7pb_1']['plate_faces'] = ()
        n0, _i = F.plan('mds7pb_1', parts[1], van)
    finally:
        F.FIELDS['mds7pb_1']['plate_faces'] = saved
    assert (~hidden(und, idmap(n0))).sum() < 0.2 * (~hidden(und, mv)).sum()


def test_palstates_620k_ghotel_state_cells_meet_the_margin():
    """ghotel's tombstone state 1 stepped up to 55/255 at x = -160 where the
    16:9 margin begins; after the edge fade it steps no more than the plain
    ground does there (state 2)."""
    import pickle
    import numpy as np
    import ff7nx_framesim as FS
    import ff7nx_palstates as P
    pk = os.path.join(HERE, 'attic', 'dirs', '_scratch', 'c8', 'ghotel_new.pkl')
    if not os.path.exists(pk):
        pytest.skip('no ghotel 620j build')
    H = list(pickle.load(open(pk, 'rb')))
    H2 = list(H)
    H2[8], n = P.edge_feather('ghotel', H[8])
    assert n >= 8 and len(H2[8]) == len(H[8])

    def jumps(parts, st):
        f = FS.Field(parts)
        s9 = f.sec9
        f.tiles = [(l, o) for l, o in f.tiles if not s9[o + 28]]
        im = f.render(cam=(0, 128), K=3, states=st, mark=False,
                      bg_color=(0, 0, 0)).astype(float)
        c = int(round((-160 + 213.5) * 3))
        out = []
        for y in range(136, 232, 16):
            r = int(round((y - 128 + 120) * 3))
            out.append(np.abs(im[r:r + 48, c:c + 3].mean((0, 1))
                              - im[r:r + 48, c - 3:c].mean((0, 1))).max())
        return np.array(out)
    before, after, ground = jumps(H, {2: 1}), jumps(H2, {2: 1}), \
        jumps(H, {2: 2})
    assert before.max() > 40
    assert (after <= ground + 1.0).all()


# ------------------------------------------------------------ BUILD 620k2
def test_palstates_620k2_ghotel_pulse_through_the_port_transfer():
    """Fitted through the port's blended-palette transfer (618b), the pulse
    entries rise by vanilla's own on-screen swing: the faint first values
    of an entry show 0, the top reaches the target within 6/255."""
    import numpy as np
    import ff7nx_palstates as P
    # ghotel's fog entries are 5-bit values up to ~12 (brighter ones would
    # pass blend 3's ceiling, 64/255)
    pal = np.array([0] + [((e & 31) << 5) | (e & 31) for e in range(1, 13)],
                   np.int64)
    codes, rms, worst = P._pulse_entries_hw(pal, 2, 40, 23)
    assert worst <= 6.0 and rms < 2.0
    g = (codes >> 5) & 31
    assert (np.diff(g[1:]) >= 0).all()          # brighter entry, more pulse
    tgt = P.hw_light(8.0 * 10, 62) - P.hw_light(8.0 * 10, 40)
    c = (int(g[10]) * 2 * 22) >> 6
    assert abs(max(0.0, P.HW_GAIN * c - P.HW_OFF) / 4.0 - tgt) < 6.0


def test_palstates_620k2_soften_keeps_the_fog_level():
    """The static fog is blurred in field space, normalised over its own
    cells: a flat fog stays flat (no darkened border), a step is spread."""
    import struct as _s
    import numpy as np
    import ff7nx_palstates as P
    k = 48
    tg, vc, base = [], {}, {}
    for i, (x, y) in enumerate([(0, 0), (16, 0), (0, 16), (16, 16)]):
        r = bytearray(52)
        _s.pack_into('<hh', r, 2, x, y)
        r = bytes(r) + bytes([i])
        tg.append((2, i, r))
        vc[r] = ('c', i)
        base[('c', i)] = np.full((k, k, 3), 40.0 if y == 0 else 60.0,
                                 np.float32)
    out = P._soften_split(base, vc, tg, k, 7.0)
    top = out[tg[0][2]]
    assert abs(top[0, 0, 0] - 40.0) < 0.5          # corner keeps its level
    bot = out[tg[2][2]]
    assert 40.0 < bot[0, 24, 0] < 60.0 < 60.5      # the step is spread
    assert abs(bot[-1, 24, 0] - 60.0) < 0.5


def test_edgeclean_620k2_mtcrl_4_dirt_only():
    """mtcrl_4: the silhouette is untouched (same opaque texels apart from
    specks), dark blotches inside the wood take their surroundings' colour,
    near-black slots (the rails' holes) are kept."""
    import pickle
    import numpy as np
    import diag_common as DC
    import ff7nx_edgeclean as E
    pk = os.path.join(HERE, 'attic', 'dirs', '_scratch', 'c9', 'mtcrl_4_620k.pkl')
    if not os.path.exists(pk):
        pytest.skip('no mtcrl_4 build')
    p = pickle.load(open(pk, 'rb'))
    s9, st = E.plan_field(bytes(p[8]), 'mtcrl_4', None, None)
    assert st['rim'] == 0 and st['whiskers'] == 0 and st['dirt'] > 1000
    a = {q.slot: np.frombuffer(q.data, '<u2') for q in
         DC.parse_pages(bytes(p[8]))[0] if q is not None and q.depth == 2}
    b = {q.slot: np.frombuffer(q.data, '<u2') for q in
         DC.parse_pages(s9)[0] if q is not None and q.depth == 2}
    lost = sum(int(((a[s] != 0) & (b[s] == 0)).sum()) for s in a)
    gained = sum(int(((a[s] == 0) & (b[s] != 0)).sum()) for s in a)
    assert gained == 0 and lost == st['specks']
    for s in a:
        ch = a[s] != b[s]
        if ch.any():
            v = a[s][ch].astype(np.int64)
            lum = (((v >> 11) & 31) * 8 * 0.299 + ((v >> 5) & 63) * 4 * 0.587
                   + (v & 31) * 8 * 0.114)
            assert (lum[b[s][ch] != 0] >= E.DIRT_MIN_LUM - 8).all()


def _c9(name):
    pk = os.path.join(HERE, 'attic', 'dirs', '_scratch', 'c9', name)
    if not os.path.exists(pk):
        pytest.skip('no %s extract' % name)
    import pickle
    return pickle.load(open(pk, 'rb'))


def test_edgesmooth_620k3_mtcrl_4_stubs_and_thick_edges_only():
    """mtcrl_4: 1x stub blocks above the tracks go, the rail edges are
    redrawn, and the lattice's thin struts and holes keep their shape
    (gains stay a small fraction of the opaque area)."""
    import numpy as np
    import diag_common as DC
    import ff7nx_edgesmooth as ES
    p = _c9('mtcrl_4_k2.pkl')
    assert 'mtcrl_4' in ES.STUBS and ES.THICK['mtcrl_4'] >= 3.0
    s9, info = ES.plan('mtcrl_4', bytes(p[8]), p)
    assert len(s9) == len(bytes(p[8]))
    a = {q.slot: np.frombuffer(q.data, '<u2') for q in
         DC.parse_pages(bytes(p[8]))[0] if q is not None and q.depth == 2}
    b = {q.slot: np.frombuffer(q.data, '<u2') for q in
         DC.parse_pages(s9)[0] if q is not None and q.depth == 2}
    opaque = sum(int((a[s] != 0).sum()) for s in a)
    gained = sum(int(((a[s] == 0) & (b[s] != 0)).sum()) for s in a)
    lost = sum(int(((a[s] != 0) & (b[s] == 0)).sum()) for s in a)
    # 620k5b: + the along-the-edge re-cut (straight bars, no nubs): 1.5 %
    assert 0 < gained < 0.015 * opaque and 0 < lost < 0.01 * opaque


def test_edgesmooth_620k3_stub_detector():
    """A uniform 1x block (3x3 HD texels of one colour) sticking out of a
    shape into open space is a stub; a block of real (varying) texture, or a
    uniform block inside the shape, is not."""
    import numpy as np
    import ff7nx_edgesmooth as ES
    k = ES.K
    rs = np.random.RandomState(1)
    t = np.zeros((30 * k, 30 * k), np.uint16)
    t[12 * k:, :] = rs.randint(0x0841, 0xFFFF, (18 * k, 30 * k))
    t[20 * k:21 * k, 10 * k:11 * k] = 0x8410            # uniform, inside
    t[11 * k:12 * k, 5 * k:6 * k] = 0x8410              # the stub
    t[11 * k:12 * k, 20 * k:21 * k] = rs.randint(0x0841, 0xFFFF, (k, k))
    m = t != 0
    m2, n = ES._stubs(t, m)
    assert n == k * k
    assert not m2[11 * k + 1, 5 * k + 1] and m2[11 * k + 1, 20 * k + 1]
    assert m2[20 * k + 1, 10 * k + 1]


def test_laserhd_620k3_semkin_4_beams():
    """semkin_4: the static palette-9 beam records are redrawn as straight
    HD lines on truecolor FX cells, keeping their light; positions, IDs,
    blend and record count are unchanged; the raw size does not grow."""
    import struct as _s
    import diag_common as DC
    import ff7nx_laserhd as L
    p = _c9('semkin_4_k2.pkl')
    s9 = bytes(p[8])
    new, info = L.plan_field('semkin_4', list(p))
    assert info['beams'] >= 4 and info['records'] >= 40
    assert 0.85 < info['light'] < 1.15
    assert len(new) == len(s9)
    pm = {q.slot: q for q in DC.parse_pages(new)[0] if q is not None}
    ts = DC.parse_pages(new)[1]
    moved = 0
    for layer, offs in DC.walk_layers(new, new.find(b'BACK'), ts):
        for o in offs:
            assert new[o:o + 10] == s9[o:o + 10]
            assert new[o + 24:o + 32] == s9[o + 24:o + 32]
            if new[o + 34] != s9[o + 34]:
                moved += 1
                assert 0x0F <= new[o + 34] < 0x18
                assert pm[new[o + 34]].depth == 2
    assert moved == info['records']


def test_halohd_620k4_las4_42():
    """las4_42: the halo is drawn by HD copies (param 1) with the 1x records
    kept under param 2 for the lead-in fade; the highlights become five
    tile states (params 3..7, level 0 empty); shade1/shade2 drive them; the
    halo keeps its light; nothing over the binding or memory caps; layer 1
    untouched; a second run refuses."""
    import struct as _s
    import collections
    import diag_common as DC
    import field_bg_pagecap as PC
    import field_bg_repack as FR
    import ff7nx_halohd as H
    p = _c9('las4_42_k2.pkl')
    s9 = bytes(p[8])
    s0, new, info = H.plan_field('las4_42', list(p))
    assert info['halo'] > 150 and info['hl'][0] == 0
    assert min(info['hl'][1:]) > 20
    assert 0.85 < info['light'] < 1.15 and info['edge_texels'] > 1000
    assert len(s0) == len(bytes(p[0]))
    assert bytes.fromhex('e0000100e1000200805002') in s0
    assert bytes.fromhex('8050030700e5000320ff14500307031b') in s0
    pl, ts, _te, px = DC.parse_pages(new)
    pm = {q.slot: q for q in pl if q is not None}
    assert pm[15].depth == 1 and pm[16].depth == 1
    assert all(pm[s].depth == 2 for s in info['slots'])
    assert sum(FR._page_bytes(q.px, q.depth) for q in pl
               if q is not None) < 35.0 * 1048576
    assert max(PC.effective_counts(new, px).values()) <= 256
    seen = collections.Counter()
    for layer, offs in DC.walk_layers(new, new.find(b'BACK'), ts):
        for o in offs:
            if new[o + 28]:
                seen[(new[o + 22], new[o + 26], pm[new[o + 34]].depth)] += 1
    assert seen[(2, 2, 1)] == 261                  # the 1x halo, for the fade
    assert seen[(2, 1, 2)] == info['halo']
    assert not any(k[0] == 3 and k[1] == 0 for k in seen)
    assert sum(v for k, v in seen.items() if k[0] == 3) == sum(info['hl'])
    q = list(p)
    q[0], q[8] = s0, new
    with pytest.raises(ValueError):
        H.plan_field('las4_42', q)


def test_halohd_620k4_script_patterns_are_exact():
    import ff7nx_halohd as H
    for old, new in (H._shade1_patch(59), H._shade2_patch(5)):
        assert len(old) == len(new)


def test_halohd_620k3_off_switch(monkeypatch):
    import ff7nx_halohd as H
    import ff7nx_laserhd as L
    monkeypatch.setenv(H.OFF_ENV, '1')
    monkeypatch.setenv(L.OFF_ENV, '1')
    assert H.apply_to_flevel(None, {}) == {'names': [], 'refused': []}
    assert L.apply_to_flevel(None, {}) == {'names': [], 'refused': []}


def test_laserhd_620k4_beams_never_dip_back():
    """semkin_4: each beam's light along its length is monotone (no thin ->
    thick -> thin), apart from the soft END_FADE at its two ends."""
    import numpy as np
    import ff7nx_laserhd as L
    p = _c9('semkin_4_k2.pkl')
    cap = {}
    orig = L.beams
    try:
        L.beams = lambda img: cap.setdefault('b', orig(img))
        L.plan_field('semkin_4', list(p))
    finally:
        L.beams = orig
    for _c, _u, _t0, _t1, prof in cap['b']:
        lum = prof.max(1)
        idx = np.nonzero(lum > 0)[0]
        core = lum[idx[0] + 4:idx[-1] - 3]
        d = np.diff(core)
        assert (d >= -1e-6).all() or (d <= 1e-6).all()


def test_edgesmooth_620k4_mtcrl_4_ring_only_on_the_outer_edge():
    """mtcrl_4: the background-swept ring softens only the art's own outer
    edge texels (never a texel outside the art, never inside a rail hole)."""
    import ff7nx_edgesmooth as ES
    assert ES.AA['mtcrl_4']['thick_only'] and 'sweep' in ES.AA['mtcrl_4']
    p = _c9('mtcrl_4_k2.pkl')
    _s9, info = ES.plan('mtcrl_4', bytes(p[8]), p)
    st = info[2]
    assert st['ring'] == 0 and 500 < st['ring_in'] < 20000


# ------------------------------------------------------------ BUILD 620k5
def test_edgesmooth_620k5_mtcrl_4_black_matte_recoloured():
    """mtcrl_4: near-black texels on the bars' cut edge take the nearest
    wood colour (never a new colour, never a texel made transparent or
    opaque by this step); dark-through parts and rail holes keep theirs;
    the step is a single pass."""
    import struct as _s
    import numpy as np
    import diag_common as DC
    import ff7nx_edgesmooth as ES
    p = _c9('mtcrl_4_k2.pkl')
    s9 = bytes(p[8])
    pl, ts, _te, px = DC.parse_pages(s9)
    pm = {q.slot: q for q in pl if q is not None}
    recs = ES._records(s9, ts, pm, 2)
    tex, *_r = ES._canvas(s9, recs, pm, px)
    out, _st = ES.smooth(tex, sigma=2.4, thick=4.0, stubs=True)
    out, _o = ES.orient_smooth(out, None)
    new, n = ES.deoutline(out, None)
    assert 40000 < n < 90000
    assert ((new != 0) == (out != 0)).all()
    ch = new != out
    lum = ES._rgbf(out).mean(-1)
    assert (lum[ch] < ES.DEOUT_BLACK).all()
    assert (ES._rgbf(new).mean(-1)[ch] >= ES.DEOUT_WOOD).all()
    assert set(np.unique(new[ch]).tolist()) <= set(np.unique(out).tolist())
    again, n2 = ES.deoutline(new, None)
    assert n2 < 0.35 * n          # one pass: a 2-texel matte is done from outside in, once
    _s9, info = ES.plan('mtcrl_4', s9, p)
    assert abs(info[2]['deoutline'] - n) < 0.02 * n   # frozen texels aside
    assert info[2]['orient_add'] > 10000


def test_edgesmooth_620k5_las4_4_pyramids():
    """las4_4: the pyramids' layer-2 truecolor silhouette is re-cut and its
    own edge texels blended toward the measured aurora colour; the floor
    blocks (layer 1) and the blended glow records are untouched; section
    size and record table unchanged."""
    import pickle
    import ff7nx_edgesmooth as ES
    import ff7nx_floordepth as F
    pk = os.path.join(HERE, 'attic', 'dirs', '_scratch', 'c11', 'las4_4_k4.pkl')
    if not os.path.exists(pk):
        pytest.skip('no las4_4 probe')
    p = pickle.load(open(pk, 'rb'))
    s9 = bytes(p[8])
    assert ES.AA['las4_4']['sky'] == (134, 255, 254)
    assert ES.AA['las4_4']['thick_only']
    new, info = ES.plan('las4_4', s9, p)
    st = info[2]
    assert len(new) == len(s9)
    assert st['ring'] == 0 and 2000 < st['ring_in'] < 20000
    assert st['records'] > 40 and st['skipped_shared'] == 0
    rows = lambda b: [(l_, n) for l_, _c, _f, n in F._rows(b)]
    assert rows(new) == rows(s9)
    t0 = s9.find(b'TEXTURE') if b'TEXTURE' in s9 else 0
    assert new[:t0] == s9[:t0]


def test_edgesmooth_620k5b_orient_smooth_straightens_a_wobbly_bar():
    """A diagonal bar whose edge wobbles by a texel comes out with a
    straight edge; a crossing (no single direction) keeps its shape."""
    import numpy as np
    import ff7nx_edgesmooth as ES
    yy, xx = np.mgrid[0:80, 0:80]
    d = (yy - 0.5 * xx - 10).astype(float)
    wob = ((xx // 3) % 2).astype(float)          # 1-texel nubs every 3
    bar = (d > -4 - wob) & (d < 4)
    tex = np.where(bar, 0x8410, 0).astype(np.uint16)
    out, (add, cut) = ES.orient_smooth(tex, None)
    m = out != 0
    top = [int(np.argmax(m[:, x])) for x in range(20, 60)]
    ideal = [int(np.ceil(10 + 0.5 * x - 4)) for x in range(20, 60)]
    assert max(abs(a - b) for a, b in zip(top, ideal)) <= 1
    assert cut > 0
