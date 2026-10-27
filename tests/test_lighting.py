"""BUILD 675: ff7nx_lighting -- the four call sites, the stubs, the blob.
The full ARM chain (real thunk, stubs, blob, the port's own native routine and
0x66CFE8) runs in unicorn: tests/lighting_unicorn.py (needs unicorn).

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
import os, struct, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import ff7nx_lighting as L, ff7nx_deadspace as DS, nxmap, a64 as A
DUMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'dump', 'exefs', 'main')


def _bl_target(text, at):
    x = struct.unpack_from('<I', text, at)[0]
    assert x >> 26 in (0x05, 0x25), hex(x)
    imm = x & 0x3FFFFFF
    if imm & 0x2000000:
        imm -= 1 << 26
    return at + 4 * imm


def test_stock_sites():
    L.check(nxmap.Main(DUMP).text)


def test_blob_has_four_entries():
    e = L.entries()
    assert set(e) == {'lt_setup', 'lt_vtx', 'lt_mvfix', 'lt_entry'}    # 679
    assert os.path.getsize(L.BLOB) < 0x780


def test_apply():
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, 'main')
        rep = L.apply_to_nso(DUMP, out, DUMP)
        m = nxmap.Main(out)
        lo, hi = DS.part(DUMP, 'light')
        assert lo <= rep['code'] and rep['code'] + rep['bytes'] <= hi
        blob = open(L.BLOB, 'rb').read()
        assert m.text[rep['code']:rep['code'] + len(blob)] == blob
        st = rep['stubs']
        assert _bl_target(m.text, L.SETUP_CALL) == st['setup']
        assert _bl_target(m.text, L.VTX_CALL) == st['vtx']
        for s in L.MV_CALLS:
            assert _bl_target(m.text, s) == st['mv']
        # each stub reaches its real target
        def has_branch(stub, target, n=24):
            for i in range(n):
                x = struct.unpack_from('<I', m.text, stub + 4 * i)[0]
                if x >> 26 in (0x05, 0x25) and _bl_target(m.text, stub + 4 * i) == target:
                    return True
            return False
        assert has_branch(st['setup'], L.SETUP_TARGET)
        assert has_branch(st['mv'], L.MV_TARGET)
        assert has_branch(st['setup'], rep['entries']['lt_setup'])
        assert has_branch(st['vtx'], rep['entries']['lt_vtx'])
        assert has_branch(st['mv'], rep['entries']['lt_mvfix'])
        # 679: each entry hook branches to its stub, which runs the
        # displaced word and returns to the entry's second word
        for site, kind, what in L.ENTRY_SITES:
            st_ = st['entry%X' % site]
            assert _bl_target(m.text, site) == st_
            ws = struct.unpack_from('<6I', m.text, st_)
            assert ws[4] == L.ENTRY_WORD and ws[1] == A.movz(3, kind)
            assert ws[5] == A.b(st_ + 20, site + 4)
            assert ws[2] == A.bl(st_ + 8, st['ecommon'])
        assert has_branch(st['ecommon'], rep['entries']['lt_entry'])
        # nothing else in .text changed outside the part and the four sites
        a, b = nxmap.Main(DUMP).text, m.text
        diff = [i for i in range(0, len(a), 4) if a[i:i + 4] != b[i:i + 4]]
        sites = {L.SETUP_CALL, L.VTX_CALL} | set(L.MV_CALLS) | {x[0] for x in L.ENTRY_SITES}
        assert all(lo <= i < hi or i in sites for i in diff)


def test_sfxpre_part_is_empty_now():
    lo, hi = DS.part(DUMP, 'sfxpre')
    assert lo == hi


def test_env():
    assert L.enabled({})
    assert not L.enabled({L.ENV: 'pc'})
