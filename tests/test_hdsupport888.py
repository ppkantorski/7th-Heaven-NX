"""BUILD 885: junin1a / nivgate window waits, cargoin light tile.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)


import os  # noqa: E402
import struct  # noqa: E402

import ff7nx_scenemodel as S  # noqa: E402


def _hrc(bones):
    out = [':HEADER_BLOCK 2', ':SKELETON x_sk', ':BONES %d' % len(bones), '']
    for name, parts in bones:
        out += [name, 'root', '1.0', ' '.join([str(len(parts))] + parts), '']
    return '\r\n'.join(out).encode()


def _getter(files):
    return lambda n: files.get(n)


def _base():
    return {
        'aaaa.hrc': _hrc([('chest', ['AAAD', 'AAAD1']), ('r_hand', ['AADC'])]),
        'bhff.hrc': _hrc([('chest', ['BHGC']), ('r_hand', ['BHJB', 'BHJB1'])]),
        'ibad.hrc': _hrc([('chest', ['IBBA']), ('r_hand', ['IBDF'])]),
        'bhjb1.rsd': b'@RSD940102\r\nPLY=BHJB1.PLY\r\nMAT=BHJB1.MAT\r\n'
                     b'GRP=BHJB1.GRP\r\nNTEX=0\r\n',
        'bhjc1.p': b'p',
    }


def test_chibi_zack_keeps_ejdf1():
    f = _base()
    f['ejdc.hrc'] = _hrc([('chest', ['EJDF', 'EJDF1']), ('r_hand', ['X'])])
    f['ejdf1.rsd'] = b'r'
    notes = []
    out = S.plan_char(_getter(f), notes)
    assert notes == []
    assert S.bone_parts(out['znib.hrc'], 'chest') == ['IBBA', 'EJDF1']
    assert S.bone_parts(out['znaa.hrc'], 'chest') == ['AAAD']
    assert S.bone_parts(out['znbh.hrc'], 'r_hand') == ['BHJB', 'ZNBS']


def test_hd_zack_gets_ibba1():
    f = _base()
    f['ejdc.hrc'] = _hrc([('chest', ['IBBA', 'IBBA1']), ('r_hand', ['X'])])
    f['ibba1.rsd'] = b'r'
    out = S.plan_char(_getter(f))
    assert S.bone_parts(out['znib.hrc'], 'chest') == ['IBBA', 'IBBA1']


def test_unmatched_variant_is_the_general_model():
    f = _base()
    f['aaaa.hrc'] = _hrc([('chest', ['AAAD']), ('r_hand', ['AADC'])])
    f['ejdc.hrc'] = _hrc([('chest', ['EJDF', 'EJDF1']), ('r_hand', ['X'])])
    del f['bhjc1.p']                       # no static sword: ZNBH falls back
    notes = []
    out = S.plan_char(_getter(f), notes)
    assert out['znaa.hrc'] == f['aaaa.hrc']
    assert out['znbh.hrc'] == f['bhff.hrc']
    assert 'znbs.rsd' not in out
    assert {n for n, _w in notes} == {'ZNAA', 'ZNBS', 'ZNBH', 'ZNIB'}
    # every variant the fields name exists either way
    assert {'znaa.hrc', 'znib.hrc', 'znbh.hrc'} <= set(out)


def _fake_lgp(path, names):
    with open(path, 'wb') as fh:
        fh.write(b'\0\0SQUARESOFT')
        fh.write(struct.pack('<i', len(names)))
        for n in names:
            fh.write(n.encode().ljust(20, b'\0') + b'\0' * 7)


class _Arch:
    index = {}


def test_flevel_refuses_without_the_variants(tmp_path):
    p = str(tmp_path / 'char.lgp')
    _fake_lgp(p, ['aaaa.hrc', 'znaa.hrc'])
    st = S.apply_to_flevel(_Arch(), {}, encode=lambda r: r, char_path=p)
    assert st['names'] == [] and st['refused']
    st = S.apply_to_flevel(_Arch(), {}, encode=lambda r: r,
                           char_path=str(tmp_path / 'absent.lgp'))
    assert st['refused']
    _fake_lgp(p, ['znaa.hrc', 'znib.hrc', 'znbh.hrc'])
    st = S.apply_to_flevel(_Arch(), {}, encode=lambda r: r, char_path=p)
    assert st['refused'] == []          # passes the guard (no fields here)


def test_texture_fallback_dir(tmp_path):
    import build
    root = tmp_path / 'Mod'
    (root / 'M' / 'Char').mkdir(parents=True)
    (root / 'M' / 'High').mkdir(parents=True)
    (root / 'M' / 'Char' / 'HTJF1.rsd').write_bytes(
        b'@RSD940102\r\nPLY=HUAA1.PLY\r\nNTEX=1\r\nTEX[0]=AAAA1.TIM\r\n')
    (root / 'M' / 'High' / 'AAAA1.TEX').write_bytes(b'tex')

    class Mod:
        cache = str(root)

    class Plan:
        archive_files = {'char.lgp': {
            'htjf1.rsd': (str(root / 'M' / 'Char' / 'HTJF1.rsd'), None)}}
    n = build._rescue_dangling_model_textures(Plan, {'char.lgp': set()},
                                              [Mod], lambda *_: None)
    assert n == 1
    assert Plan.archive_files['char.lgp']['aaaa1.tex'][0].endswith('AAAA1.TEX')
    # a name present in its own folder never takes the fallback
    (root / 'M' / 'Char' / 'AAAA1.TEX').write_bytes(b'own')
    Plan.archive_files = {'char.lgp': {
        'htjf1.rsd': (str(root / 'M' / 'Char' / 'HTJF1.rsd'), None)}}
    build._rescue_dangling_model_textures(Plan, {'char.lgp': set()},
                                          [Mod], lambda *_: None)
    assert os.path.basename(os.path.dirname(
        Plan.archive_files['char.lgp']['aaaa1.tex'][0])) == 'Char'
