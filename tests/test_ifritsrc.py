#!/usr/bin/env python3
"""
tests/test_ifritsrc.py -- BUILD 411, the source half of Ifrit's heat warp.

Everything is re-derived from `ff7_en_switch` and `exefs/main`. Nothing reads a
constant out of ff7nx_ifritsrc and checks it against itself, because that is
how builds 389..399 each convinced themselves.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import hashlib
import os
import shutil
import struct
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

capstone = pytest.importorskip('capstone')
from capstone import Cs, CS_ARCH_ARM64, CS_ARCH_X86, CS_MODE_ARM, CS_MODE_32

import ff7nx_battlewide as BW                                   # noqa: E402
import ff7nx_ifritsrc as S                                      # noqa: E402
import nxmap                                                     # noqa: E402

MAIN = os.path.join(_ROOT, 'dump', 'exefs', 'main')
FF7EN = os.path.join(_ROOT, 'data', 'ff7_en_switch')
IFRIT_SETUP = 0x595DE7
IFRIT_X86 = 0x595A05

pytestmark = pytest.mark.skipif(
    not os.path.exists(MAIN), reason='needs exefs/main')


@pytest.fixture(scope='module')
def m():
    return nxmap.Main(MAIN)


@pytest.fixture(scope='module')
def x86():
    if not os.path.exists(FF7EN):
        pytest.skip('needs ff7_en_switch')
    return open(FF7EN, 'rb').read()


def x86dis(data, va, nbytes=0x400):
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    return list(md.disasm(data[0x200 + va - 0x401000:
                               0x200 + va - 0x401000 + nbytes], va))


def dis1(word, va=0):
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    d = next(md.disasm(struct.pack('<I', word), va))
    return '%s %s' % (d.mnemonic, d.op_str)


# ----------------------------------------------- the descriptors, from the x86
def test_the_three_capture_rects_are_what_the_module_says(x86):
    ins = x86dis(x86, IFRIT_SETUP, 0x120)
    by = {i.address: '%s %s' % (i.mnemonic, i.op_str) for i in ins}
    # the prologue: cap1 (0,0,0x100,0xA6), cap2 (0x200,0,0x41,0x58),
    #               cap3 (0x200,0xB0,0x41,0x58)
    want = {
        0x595DE7: 'mov word ptr [ebp - 0x24], 0',
        0x595DED: 'mov word ptr [ebp - 0x22], 0',
        0x595DF3: 'mov word ptr [ebp - 0x20], 0x100',
        0x595DF9: 'mov word ptr [ebp - 0x1e], 0xa6',
        0x595DFF: 'mov word ptr [ebp - 0x2c], 0x200',
        0x595E0B: 'mov word ptr [ebp - 0x28], 0x41',
        0x595E11: 'mov word ptr [ebp - 0x26], 0x58',
        0x595E17: 'mov word ptr [ebp - 0x34], 0x200',
        0x595E1D: 'mov word ptr [ebp - 0x32], 0xb0',
    }
    for va, text in want.items():
        assert by.get(va) == text, (hex(va), by.get(va), text)
    # and the mode-2 arm re-states them
    assert by[0x595E43] == 'jmp 0x595ea6'
    assert by[0x595EC4] == 'mov word ptr [ebp - 0x32], 0xb0'


def test_cap3_y_is_exactly_h_times_the_scale(x86):
    """176 = 0x58 * 2. That is the relation the yscale change has to preserve,
    and 264 = 0x58 * 3 is the only value that does."""
    ins = x86dis(x86, IFRIT_SETUP, 0x120)
    by = {i.address: i.op_str for i in ins}
    h = int(by[0x595E11].rsplit(', ', 1)[1], 16)          # cap3.h = 0x58
    y = int(by[0x595EC4].rsplit(', ', 1)[1], 16)          # cap3.y = 0xb0
    assert y == h * 2
    assert dis1(S.IMM_SITES[0][2]) == 'mov w20, #%#x' % (h * S_YSCALE())


def S_YSCALE():
    return 3


def test_wave3_y_carries_the_same_0x58_offset(x86):
    """`y = (v + 0x58) * scale` -- so wave 3's screen row and its staging row
    move together only if cap3.y is h * yscale."""
    ins = x86dis(x86, IFRIT_X86, 0x400)
    by = {i.address: '%s %s' % (i.mnemonic, i.op_str) for i in ins}
    assert by[0x595D76] == 'add ecx, 0x58'
    assert by[0x595D79] == 'imul ecx, dword ptr [0x9ad1a8]'
    assert by[0x595DA6] == 'add eax, 0x58'
    # waves 1 and 2 have no such offset
    assert by[0x595AF9] == 'imul edx, dword ptr [0x9ad1a8]'
    assert by[0x595C2C] == 'imul ecx, dword ptr [0x9ad1a8]'


def test_xscale_and_yscale_are_two_separate_loads(x86):
    """Which is what lets yscale move while xscale stays 2."""
    ins = x86dis(x86, IFRIT_SETUP, 0x400)
    txt = ['%08X %s %s' % (i.address, i.mnemonic, i.op_str) for i in ins]
    joined = '\n'.join(txt)
    assert joined.count('mov dword ptr [ecx + 0x10c], eax') \
        + joined.count('mov dword ptr [edx + 0x10c], eax') >= 1
    assert '0x110]' in joined
    # two distinct reads of the global per object
    assert joined.count('dword ptr [0x9ad1a8]') >= 6


# ---------------------------------------------------- the ARM sites and words
def test_every_anchor_and_stock_word_is_what_the_binary_holds(m):
    assert S.verify(m.img) == []
    assert S.installed(m.img) == (False, False)
    for va, want in S.ANCHORS.items():
        assert struct.unpack_from('<I', m.img, va)[0] == want, hex(va)


def test_the_yscale_sites_are_loads_that_get_redefined(m):
    """A literal at these sites can only leak if the register is READ before
    it is written again. All three are re-loaded first -- and it matters most
    at the third, where the register is also the hoisted 0x9AD1A8 pointer."""
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    lo, hi = m.extent(IFRIT_X86)
    ins = list(md.disasm(bytes(m.img[lo:hi]), lo))
    at = {i.address: i for i in ins}
    for va, stock, pat, reg, redef in S.YSCALE_SITES:
        assert dis1(stock, va) == 'ldr %s, [x0]' % reg
        assert dis1(pat) == 'mov %s, #3' % reg
        assert dis1(struct.unpack_from('<I', m.img, redef)[0]) \
            == 'ldr %s, [x0]' % reg
        # the store that follows targets +0x110, not +0x10c
        nxt = at[va + 4]
        assert nxt.mnemonic == 'add' and nxt.op_str.endswith('#0x110'), \
            (hex(va), nxt.op_str)
        # nothing reads the register between the site and the redefinition
        for a in range(va + 4, redef, 4):
            i = at[a]
            if i.mnemonic in ('str', 'strh', 'strb'):
                continue                    # a store of the value we replaced
            assert reg not in i.op_str.split(', ')[1:], (hex(a), i.op_str)


def test_the_movz_and_orr_forms_are_not_mixed_up():
    """`mov #0x100`/`#0x200` are ORR-immediates; `#3`/`#0x108`/`#0x14c` are
    MOVZ. Getting that wrong is build 406's revert bug, and this module writes
    both kinds."""
    for word, form in ((S.MOV_W21_3, 'movz'), (S.MOV_W19_3, 'movz'),
                       (0x52802114, 'movz'), (0x52802988, 'movz'),
                       (0x321703F4, 'orr'), (0x321803F4, 'orr')):
        is_movz = (word & 0x7F800000) == 0x52800000
        is_orr = (word & 0x7F800000) == 0x32000000
        assert (is_movz if form == 'movz' else is_orr), '%08X %s' % (word,
                                                                     form)
    # and the patched cap1.w is COPIED, not computed
    assert dict((v, p) for v, _s, p, _w in S.IMM_SITES)[0x70F930] \
        == S.ANCHORS[0x711140]


def test_each_patched_word_decodes_to_the_intended_value():
    assert dis1(S.IMM_SITES[0][2]) == 'mov w20, #0x108'     # cap3.y = 264
    assert dis1(S.IMM_SITES[1][2]) == 'mov w20, #0x200'     # cap1.w = 512
    assert dis1(S.IMM_SITES[2][2]) == 'mov w8, #0x14c'      # cap1.h = 332
    assert 0x108 == 0x58 * 3


def test_the_texel_words_cannot_move_the_picture():
    """`staging = fb_tex.x + u * xscale` with fb_tex.w = xscale * POT(w), and
    the UV divisor is POT(w) -- so POT(w) cancels and raising it is free."""
    def pot(n):
        p = 1
        while p < n:
            p <<= 1
        return p
    for authored in (0x100, 0x200):
        tex_w = pot(authored)
        for u in (1, 64, 128, 254):
            assert u / tex_w * (2 * tex_w) == u * 2


def test_plan_and_revert_round_trip(m, tmp_path):
    dest = str(tmp_path / 'main')
    shutil.copy(MAIN, dest)
    h0 = hashlib.sha256(open(dest, 'rb').read()).hexdigest()
    assert S.apply(dest, log=lambda s: None) == 0
    mm = nxmap.Main(dest)
    assert S.installed(mm.img) == (True, True)
    assert S.verify(mm.img) == []
    assert S.apply(dest, revert=True, log=lambda s: None) == 0
    assert hashlib.sha256(open(dest, 'rb').read()).hexdigest() == h0


def test_mutations_are_caught(m):
    img = bytearray(m.img)
    for va in sorted(S.ANCHORS):
        orig = struct.unpack_from('<I', img, va)[0]
        struct.pack_into('<I', img, va, orig ^ 0x400)
        assert S.verify(bytes(img)), hex(va)
        struct.pack_into('<I', img, va, orig)
    assert S.verify(bytes(img)) == []


# --------------------------------- the frame-0 gate, and 411a's compounding
def test_the_build_pass_runs_only_on_frame_zero(x86, m):
    """The fact build 411a missed, and the reason the y scaling had to move out
    of the draw cave."""
    ins = x86dis(x86, IFRIT_X86, 0x40)
    by = {i.address: '%s %s' % (i.mnemonic, i.op_str) for i in ins}
    assert by[0x595A20] == 'movsx edx, word ptr [ecx + 2]'   # frame counter
    assert by[0x595A24] == 'test edx, edx'
    assert by[0x595A26] == 'jne 0x5960fa'                    # skip the build
    # and the ARM's inverted form, which the module anchors on
    assert struct.unpack_from('<I', m.img, S.FRAME0_GATE)[0] == \
        S.FRAME0_GATE_WORD
    assert dis1(S.FRAME0_GATE_WORD, S.FRAME0_GATE).startswith('cbz w8, ')


def test_y_is_written_only_in_the_build_pass_and_x_every_frame(x86):
    """Six y multiplies before the gate's target, twelve x multiplies after --
    and the halfword stores to match. The cave may only touch the latter."""
    ins = x86dis(x86, IFRIT_X86, 0x1200)
    ys, xs = [], []
    for i in ins:
        if i.mnemonic != 'mov' or not i.op_str.startswith('word ptr'):
            continue
        for off, bucket in ((0xa, ys), (0x12, ys), (0x1a, ys), (0x22, ys),
                            (8, xs), (0x10, xs), (0x18, xs), (0x20, xs)):
            tag = '+ 8]' if off == 8 else '+ %#x]' % off
            if tag in i.op_str and '0xbcc670' not in i.op_str:
                bucket.append((i.address, off))
    # the y stores are all below 0x5960FA (the per-frame entry); the x stores
    # that the draw loops make are all above it
    assert ys and all(a < 0x5960FA for a, _o in ys), ys
    assert any(a > 0x5960FA for a, _o in xs), xs


def test_411a_compounded_and_the_verifier_now_catches_it():
    """332 * 1.5^n overflows int16 in about eleven frames, which is where the
    recording turns to vertical stripes."""
    y = 332
    for n in range(1, 40):
        y = (y * 3) // 2
        y = ((y + 0x8000) & 0xFFFF) - 0x8000
        if y < 0:
            break
    assert n < 15, 'expected the strh to wrap within ~11 frames, took %d' % n

    import ff7nx_battlewide as BW
    sys.path.insert(0, os.path.join(_ROOT, 'tools'))
    import ifrit_verify as IV
    # the shipped cave: every field it touches is rebuilt each frame
    offs = [off for off, _fn in BW._ifrit_ops()]
    assert all(off in IV.DRAW_PASS_WRITES for off in offs), offs
    assert all(v[0] for v in IV.check_idempotence(offs).values())
    # 411a's: the y fields are not, and compound
    legacy = [off for off, _fn in BW._ifrit_ops(True)]
    res = IV.check_idempotence(legacy)
    assert [o for o in IV.BUILD_PASS_WRITES if not res[o][0]] \
        == list(IV.BUILD_PASS_WRITES)


def test_the_cave_no_longer_touches_y():
    import ff7nx_battlewide as BW
    offs = [off for off, _fn in BW._ifrit_ops()]
    assert offs == list(BW.IFRIT_X)
    for off in BW.IFRIT_Y:
        assert off not in offs


def test_the_six_y_multiplies_and_the_twelve_x_ones(m):
    assert S.check_ymul(m.img) == []
    assert len(S.YMUL_SITES) == 6 and len(S.XMUL_GUARD) == 12
    assert not (set(S.YMUL_SITES) & set(S.XMUL_GUARD))
    for va in list(S.YMUL_SITES) + list(S.XMUL_GUARD):
        assert struct.unpack_from('<I', m.img, va)[0] == S.MUL_W8
        assert dis1(S.MUL_W8) == 'mul w8, w8, w9'
    assert dis1(S.ADD_W8_X3) == 'add w8, w8, w8, lsl #1'
    # v * 3 is what `add w8, w8, w8, lsl #1` computes
    for v in (0, 8, 88, 166):
        assert v + (v << 1) == v * 3


def test_a_mutation_at_any_y_site_or_witness_is_caught(m):
    img = bytearray(m.img)
    targets = list(S.YMUL_SITES) + [va + 4 for va in S.YMUL_SITES] \
        + list(S.XMUL_GUARD) + [S.FRAME0_GATE]
    for va in targets:
        orig = struct.unpack_from('<I', img, va)[0]
        struct.pack_into('<I', img, va, orig ^ 0x400)
        assert S.check_ymul(bytes(img)), hex(va)
        struct.pack_into('<I', img, va, orig)
    assert S.check_ymul(bytes(img)) == []


# ------------------------------------------------------------- the pairing
def test_the_height_words_and_the_cave_ship_together():
    old_w = os.environ.get(BW.IFRIT_ENV)
    old_t = os.environ.get(BW.IFRIT_TALL_ENV)
    old_s = os.environ.get(S.SRC_ENV)
    try:
        for k in (BW.IFRIT_ENV, BW.IFRIT_TALL_ENV, S.SRC_ENV):
            os.environ.pop(k, None)
        assert BW.ifrit_mode() == 'wide'
        assert BW.ifrit_tall() is True and S.enabled() is True
        assert S.wanted() == (True, True)
        # stripping the warp turns the height off: the caves never run
        os.environ[BW.IFRIT_ENV] = 'strip'
        assert BW.ifrit_tall() is False
        assert S.wanted() == (False, False)
        # and so does going back to stock
        os.environ[BW.IFRIT_ENV] = '0'
        assert BW.ifrit_tall() is False
        assert S.wanted() == (False, False)
        # the height alone can be turned off for an A/B
        os.environ[BW.IFRIT_ENV] = 'wide'
        os.environ[BW.IFRIT_TALL_ENV] = '0'
        assert BW.ifrit_tall() is False
        assert S.wanted() == (False, True)
    finally:
        for k, v in ((BW.IFRIT_ENV, old_w), (BW.IFRIT_TALL_ENV, old_t),
                     (S.SRC_ENV, old_s)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_build_397s_vcover_stays_off():
    """It raised the band layout instead of scaling geometry and sampling
    together, which is what produced the reported seam."""
    assert BW.ifrit_vcover_enabled() is False


def test_the_verifier_passes():
    import subprocess
    r = subprocess.run([sys.executable,
                        nxpaths.src_file('ifrit_verify.py')],
                       capture_output=True, text=True, cwd=_ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert 'ALL CHECKS PASSED' in r.stdout


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
