"""BUILD 680: ff7nx_fxtunnel against the dump's ff7_en.

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
import os, struct, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import exe_patch
import ff7nx_fxtunnel as T
EXE = os.path.join(os.path.dirname(__file__), '..', 'dump', 'romfs', 'ff7', 'resources', 'ff7_1.02', 'ff7_en')


def _load():
    data = open(EXE, 'rb').read()
    pe = exe_patch.parse_pe(data)
    return data, (lambda va: exe_patch.va_to_offset(pe, va))


def test_patch_and_idempotent():
    data, v2o = _load()
    new, n = T.patch_exe(data, v2o)
    assert n == 9                     # + BUILD 749e KOTR curtain step
    for va, fn, what in T.VECTORS:
        x, y, z, w = struct.unpack_from('<4i', new, v2o(va))
        assert (x, y, z, w) == (T.WIDE, T.STOCK, T.WIDE, 0), what
    diff = [i for i in range(len(data)) if data[i] != new[i]]
    allowed = set()
    for va, _, _ in T.VECTORS:
        for k in (0, 8):
            allowed |= set(range(v2o(va + k), v2o(va + k) + 4))
    for va, _, _ in T.GOSPEL_WORDS:
        allowed |= set(range(v2o(va), v2o(va) + 2))
    allowed |= set(range(v2o(T.KOTR_STEP_VA), v2o(T.KOTR_STEP_VA) + 4))
    assert struct.unpack_from('<I', new, v2o(T.KOTR_STEP_VA))[0] == T.KOTR_STEP_WIDE
    assert set(diff) <= allowed
    again, n2 = T.patch_exe(new, v2o)
    assert n2 == 0 and again == new


def test_each_vector_has_one_reader_its_frame_function():
    data, v2o = _load()
    pe = exe_patch.parse_pe(data)
    text = [s for s in pe['sections'] if s[0].startswith('.text')][0]
    name, sva, vs, ro, rs = text
    code = data[ro:ro + rs]
    for va, fn, what in T.VECTORS:
        hits = []
        i = 0
        while True:
            i = code.find(struct.pack('<I', va), i)
            if i < 0:
                break
            hits.append(sva + i)
            i += 1
        assert len(hits) == 1 and fn < hits[0] < fn + 0x100, (what, [hex(h) for h in hits])
        for k in (4, 8):     # nothing else addresses the vector's other words
            assert code.find(struct.pack('<I', va + k)) < 0 or k == 4


def test_refuses_foreign_value():
    data, v2o = _load()
    bad = bytearray(data)
    struct.pack_into('<i', bad, v2o(T.VECTORS[0][0]), 0x1234)
    try:
        T.patch_exe(bytes(bad), v2o)
    except ValueError:
        return
    raise AssertionError('foreign value accepted')


def test_705_great_gospel_sky_dome_widened_in_its_plane():
    data, v2o = _load()
    m = struct.unpack_from('<9h', data, v2o(T.GOSPEL_SKY))
    assert m == (0x1000, 0, 0, 0, 0, -0x1000, 0, 0x1000, 0)
    new, _ = T.patch_exe(data, v2o)
    m = struct.unpack_from('<9h', new, v2o(T.GOSPEL_SKY))
    assert m == (0x1800, 0, 0, 0, 0, -0x1800, 0, 0x1000, 0)   # bulge (model y) kept


def test_705_great_gospel_sky_matrix_has_one_reader():
    data, v2o = _load()
    pe = exe_patch.parse_pe(data)
    name, sva, vs, ro, rs = [s for s in pe['sections'] if s[0].startswith('.text')][0]
    code = data[ro:ro + rs]
    hits = []
    i = 0
    while True:
        i = code.find(struct.pack('<I', T.GOSPEL_SKY), i)
        if i < 0:
            break
        hits.append(sva + i)
        i += 1
    assert hits == [T.GOSPEL_READER]              # push 0x7F36C8 (its imm)
    for k in range(2, 0x12, 2):                   # no code addresses the rest
        assert code.find(struct.pack('<I', T.GOSPEL_SKY + k)) < 0
