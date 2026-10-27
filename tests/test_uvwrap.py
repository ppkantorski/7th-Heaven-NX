"""BUILD 749: no per-vertex UV wrap in x86 0x68D07E (ARM +0xB84CB0).

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
import os, sys, struct
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import a64 as A
import nxmap
import ff7nx_uvwrap as U

STOCK = os.path.join(ROOT, 'dump', 'exefs', 'main')


def _decode_cbnz(w, at):
    assert (w & 0x7F000000) == 0x35000000          # cbnz w
    imm = (w >> 5) & 0x7FFFF
    if imm & 0x40000:
        imm -= 0x80000
    return at + 4 * imm


def test_sites_are_the_stock_wrap_branches():
    m = nxmap.Main(STOCK)
    assert m.x86_to_arm[0x68D07E] == U.FUNC
    for at, stock, target, _ in U.SITES:
        w, = struct.unpack_from('<I', m.text, at)
        assert w == stock
        # the patch takes the very branch the "below 1.0" case takes
        assert _decode_cbnz(w, at) == target
        assert U.FUNC < at < U.FUNC + 0x1000


def test_patch_idempotent_and_refuses_foreign():
    text = bytearray(nxmap.Main(STOCK).text)
    assert U.patch_text(text) == 2
    for at, _s, target, _ in U.SITES:
        assert struct.unpack_from('<I', text, at)[0] == A.b(at, target)
    assert U.patch_text(text) == 0
    struct.pack_into('<I', text, U.SITES[0][0], 0xD503201F)
    try:
        U.patch_text(text)
    except ValueError:
        pass
    else:
        raise AssertionError('foreign word accepted')


def test_env():
    os.environ[U.ENV] = 'stock'
    try:
        assert not U.enabled()
    finally:
        del os.environ[U.ENV]
    assert U.enabled()


def test_nearclip_sites_are_the_six_vertex_loop_masks():
    """BUILD 749e: each `mov wN, #0x4100` feeds exactly the tst/and of its
    loop's `fcomp w, 0.0; test ah, 0x41` -- C3 only keeps the marker for
    w == 0 and projects w < 0."""
    m = nxmap.Main(STOCK)
    lo, hi = m.extent(0x6B27A9)
    for at, stock, want in U.NEARCLIP_SITES:
        assert lo <= at < hi
        assert struct.unpack_from('<I', m.text, at)[0] == stock
        reg = stock & 31
        assert want == (stock & ~(0xFFFF << 5)) | (0x4000 << 5)
        # the mask register's next readers are the tst + and of the loop
        uses = []
        for off in range(at + 4, hi, 4):
            w = struct.unpack_from('<I', m.text, off)[0]
            if (w & 0x7F200000) in (0x6A000000, 0x0A000000) and \
                    ((w >> 16) & 31) == reg:
                uses.append(off)
            if len(uses) == 2:
                break
        assert len(uses) == 2 and uses[1] == uses[0] + 4
    text = bytearray(m.text)
    assert U.patch_nearclip(text) == 6 and U.patch_nearclip(text) == 0
