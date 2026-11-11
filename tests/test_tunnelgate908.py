"""BUILD 908: tunnel_2 laser-gate SPLIT / run-back waits.

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

import pytest  # noqa: E402

import ff7nx_tunnelgate as TG  # noqa: E402

FLEVEL = _nx_os.path.join(
    _nx_root, 'sdout', 'atmosphere', 'contents', '0100A5B00BDC6000', 'romfs',
    'ff7', 'workingdir', 'data', 'field', 'flevel.lgp')
VANILLA = _nx_os.path.join(
    _nx_root, 'dump', 'romfs', 'ff7', 'workingdir', 'data', 'field',
    'flevel.lgp')


def test_scope():
    assert set(TG.FIELDS) == {'tunnel_2'}
    for _what, old, new in TG.FIELDS['tunnel_2']:
        assert len(old) == len(new)
        diff = [i for i in range(len(old)) if old[i] != new[i]]
        assert len(diff) == 1                      # one opcode byte
        assert (old[diff[0]], new[diff[0]]) == (0x01, 0x03)   # REQ -> REQEW


def _sec1(path, name):
    import lgp
    if not _nx_os.path.isfile(path):
        pytest.skip('no %s' % path)
    a = lgp.Archive(path)
    return lgp.split_sections(a.decompressed(a.index[name]))[0]


def test_patch_and_idempotent():
    import field_dis as F
    s1 = _sec1(FLEVEL, 'tunnel_2')
    out, n = TG.fix_sec1('tunnel_2', s1)
    if n == 0:                                     # archive already patched
        assert TG.fix_sec1('tunnel_2', out)[1] == 0
        s1 = out
    else:
        assert n == 2 and len(out) == len(s1)
        assert sum(a != b for a, b in zip(s1, out)) == 2
    assert TG.fix_sec1('tunnel_2', out)[1] == 0
    meta, names, entries = F.parse(out)
    cloud, ballet = names.index('cloud'), names.index('ballet')

    def ops(e, s):
        off, res = entries[e][s], []
        while True:
            nm, size, raw = F._decode_one(out, off)
            res.append((nm, bytes(raw)))
            off += size
            if nm == 'ret':
                return res
    assert ('reqew', bytes([0x03, ballet, 0xC8])) in ops(cloud, 4)
    assert ('reqew', bytes([0x03, names.index('tifa'), 0xC5])) in ops(ballet, 4)


def test_vanilla_untouched():
    s1 = _sec1(VANILLA, 'tunnel_2')
    with pytest.raises(ValueError):
        TG.fix_sec1('tunnel_2', s1)
