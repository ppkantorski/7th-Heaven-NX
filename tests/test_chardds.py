"""BUILD 580: Cosmos's char/<tex>_<pal>.dds props reach char.lgp.

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
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
PROJ = os.path.expanduser('~/mnt/7th_heaven_nx')

import iro  # noqa: E402
import tex  # noqa: E402
import ff7nx_chardds as C  # noqa: E402

IRO = os.path.join(PROJ, 'mods', 'CosmosLimitBreak.iro')
VCHAR = os.path.join(PROJ, 'dump', 'romfs', 'ff7', 'workingdir', 'data',
                     'field', 'char.lgp')


class _Mod:
    filename = 'CosmosLimitBreak.iro'
    path = IRO

    def __init__(self):
        self.manifest = iro.Manifest(os.path.join(PROJ, 'cache',
                                                  'CosmosLimitBreak',
                                                  'mod.xml'))

    def entries(self):
        return [e if isinstance(e, str) else e[0]
                for e in iro.list_entries(IRO)]


def _vanilla(names):
    out = {}
    d = tempfile.mkdtemp()
    with open(VCHAR, 'rb') as h:
        h.read(12)
        n = struct.unpack('<i', h.read(4))[0]
        toc = [h.read(27) for _ in range(n)]
        allnames = set()
        for e in toc:
            nm = e[:20].split(b'\0')[0].decode().lower()
            allnames.add(nm)
            if nm in names:
                off = struct.unpack('<I', e[20:24])[0]
                h.seek(off)
                sz = struct.unpack('<I', h.read(24)[20:24])[0]
                p = os.path.join(d, nm)
                open(p, 'wb').write(h.read(sz))
                out[nm] = p
    return out, allnames, d


def test_gondola_is_collected_and_converted_at_768():
    if not (os.path.exists(IRO) and os.path.exists(VCHAR)):
        return
    _v, allnames, d = _vanilla(set())
    shutil.rmtree(d)
    dds, mod = C.collect([_Mod()], {'CosmosLimitBreak.iro': {
        'LIMITBREAK': 1, 'ATA': 1, 'UNCROP': 1}}, allnames)
    assert ('gcjf.tex', 0) in dds and mod is not None
    assert all(n in allnames for n, _p in dds)
    van, _a, d = _vanilla({'gcjf.tex'})
    try:
        out, st = C.convert({}, van, {k: v for k, v in dds.items()
                                      if k[0] == 'gcjf.tex'}, mod)
        assert st['converted'] == 1
        made = tex.parse(open(out['gcjf.tex'][0], 'rb').read())
        v = tex.parse(open(van['gcjf.tex'], 'rb').read())
        assert (made['width'], made['height']) == (768, 768)
        assert made['num_palettes'] == v['num_palettes']
        assert tex.logical_scale(open(out['gcjf.tex'][0], 'rb').read()) \
            is None                                  # no UV bridge marker
        # a TEX another mod already replaces is left to that mod
        out2, st2 = C.convert({'gcjf.tex': ('x', mod)}, van,
                              {('gcjf.tex', 0): dds[('gcjf.tex', 0)]}, mod)
        assert out2['gcjf.tex'] == ('x', mod) and st2['kept_mod'] == 1
    finally:
        shutil.rmtree(d)


def test_switches():
    assert not C.enabled({C.ENV: '0'}) and C.enabled({})
    assert C.cap({}) == 768 and C.cap({C.CAP_ENV: '512'}) == 512
