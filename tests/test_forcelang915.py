"""BUILD 915: Force English -- the language setter skips the console query
and takes its own English case.

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

import struct  # noqa: E402

import pytest  # noqa: E402

import a64 as A  # noqa: E402
import arm64emu as E  # noqa: E402
import ff7nx_forcelang as FL  # noqa: E402

MAIN = _nx_os.path.join(_nx_root, 'dump', 'exefs', 'main')
RET = 0x5000


def _code():
    if not _nx_os.path.exists(MAIN):
        pytest.skip('no dump main')
    import nxmap
    img = nxmap.Main(MAIN).img
    return {a: struct.unpack_from('<I', img, a)[0]
            for a in range(FL.SETTER, FL.SETTER + 0xA4, 4)}


def test_patch_word_matches_stock():
    code = _code()
    [(_why, site, orig, new)] = FL.patch()
    assert code[site] == orig == A.bl(site, FL.PLATFORM_LANGUAGE)
    assert new == A.b(site, FL.ENGLISH_CASE)


@pytest.mark.parametrize('before', [3, 1, 4, 0x55])
def test_setter_stores_english_and_returns(before):
    code = _code()
    [(_why, site, _orig, new)] = FL.patch()
    code[site] = new
    mem = E.Mem()
    mem.write(FL.LANG_GLOBAL, struct.pack('<I', before))
    cpu = E.Cpu(mem, native={FL.PLATFORM_LANGUAGE: lambda c: (_ for _ in
                                                              ()).throw(
        AssertionError('console language was asked'))})
    cpu.sp = 0x7000000
    for q in range(31):
        cpu.x[q] = 0x1000 + q
    cpu.x[30] = RET
    keep = list(cpu.x)
    end = cpu.run(FL.SETTER, code, start_pc=FL.SETTER)
    assert end == RET and cpu.sp == 0x7000000
    assert struct.unpack('<I', mem.read(FL.LANG_GLOBAL, 4))[0] == 0
    assert all(cpu.x[q] == keep[q] for q in range(19, 29))
