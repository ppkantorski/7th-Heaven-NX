"""
tests/test_ifrit_waves.py -- Ifrit's heat-wave effect, against FFNx's wrapper.

BUILD 389. FFNx's only call-WRAPPER entry in widescreen.cpp: three
`replace_call_function` hooks that nudge four shorts in the wave's vertex data
and then make the original draw call.

The cave is emulated under arm64emu and compared against a Python
transcription of the two C++ wrappers, for every site and a range of starting
vertex values. It is not enough to check that the words look right -- the
whole point is what the four shorts contain when the draw finally happens.

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
import hashlib
import math
import shutil
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import a64 as A                      # noqa: E402
import arm64emu as E                 # noqa: E402
import ff7nx_battlewide as B         # noqa: E402
import ff7nx_dispatch as D           # noqa: E402
import nxmap                         # noqa: E402

MAIN = ROOT / 'exefs' / 'main'
FFNX = ROOT / 'repos' / 'FFNx-master' / 'src' / 'ff7' / 'widescreen.cpp'
WSH = ROOT / 'repos' / 'FFNx-master' / 'src' / 'widescreen.h'

needs_main = pytest.mark.skipif(not MAIN.exists(), reason='no exefs/main')
needs_ffnx = pytest.mark.skipif(not FFNX.exists(), reason='no FFNx checkout')

HOST = 0x40000000
GUEST_CTX = 0x30000000
WAVE_PTR = 0x00BCD000          # a plausible guest pointer for the vertex data


def word(img, va):
    return struct.unpack('<I', img[va:va + 4])[0]


# ------------------------------------------------------- FFNx, in Python

def _wrapper_deltas(kind, x=-107, w=854, game_width=640):
    fix1 = math.ceil(255.0 / game_width * w) - 255
    fix2 = math.ceil(65.0 / game_width * w) - 65
    if kind == 'first':
        return (x, x + fix1 * 2, x, x + fix1 * 2)
    return (x + fix1 * 2, x + (fix1 + fix2) * 2,
            x + fix1 * 2, x + (fix1 + fix2) * 2)


def _short(v):
    v &= 0xFFFF                              # C++ short arithmetic wraps
    return v - 0x10000 if v & 0x8000 else v


def ffnx_wrapper(kind, shorts, x=-107, w=854, game_width=640):
    """What FFNx has ON SCREEN, given what THIS port's game code computed.

    BUILD 394. Build 390's version of this function added `x` a second time,
    on the premise that FFNx rewrites the battle rect to x = -107 while this
    port keeps it at 0. That premise is false -- see
    test_ffnx_does_not_rewrite_the_stored_battle_rect below -- so the two
    engines do NOT split the work differently here:

        FFNx        final = raw*scale + rect.x(0)  then + wrapper
        this port   final = raw*scale + rect.x(0)  then + cave

    `shorts` is what the game code produced, identically on both sides, and
    the cave must add exactly FFNx's wrapper and nothing else.
    """
    d = _wrapper_deltas(kind, x, w, game_width)
    return tuple(_short(s + k) for s, k in zip(shorts, d))


@needs_ffnx
def test_the_deltas_are_ffnxs_formula_not_typed_in():
    """Re-derive both wrappers' arithmetic from FFNx's own source."""
    import re
    src = FFNX.read_text()
    assert 'ceil(255.f / game_width * wide_viewport_width) - 255' in src
    assert 'ceil(65.f / game_width * wide_viewport_width) - 65' in src
    hdr = WSH.read_text()
    x = int(re.search(r'int wide_viewport_x = (-?\d+);', hdr).group(1))
    w = int(re.search(r'int wide_viewport_width = (\d+);', hdr).group(1))
    # FFNx's wrapper, unchanged and still derived from its source...
    wf, wr = B.ifrit_ffnx_wrapper_deltas(x, w)
    assert wf == (x, x + 172, x, x + 172), wf
    assert wr == (x + 172, x + 216, x + 172, x + 216), wr
    # ...and what this port's cave adds, which is EXACTLY that. No origin
    # compensation: FFNx does not move the stored rect (see below).
    first, rest = B._ifrit_deltas(x, w)
    assert first == wf, first
    assert rest == wr, rest
    assert B._ifrit_deltas() == (first, rest)


@needs_ffnx
def test_ffnx_does_not_rewrite_the_stored_battle_rect():
    """The premise build 390 was built on, checked against FFNx's own source.

    390 asserted that FFNx rewrites the battle rect to x = -107. It does not.
    Of the four `battle_enter` patches FFNx applies, the two that name x and
    width are patch_code_DWORD and land on a memory OPERAND of a later READ,
    not on the immediate of the store. Map each patch offset onto the
    instruction that owns it and the rect's own x, y, w and scale stores come
    out untouched.
    """
    import re
    src = FFNX.read_text()
    got = {}
    for m in re.finditer(
            r'patch_code_(int|dword|short)\(\s*ff7_externals\.battle_enter'
            r'\s*\+\s*(0x[0-9A-Fa-f]+)\s*,([^;]*)\)\s*;', src):
        arg = re.findall(r'[A-Za-z_][A-Za-z_0-9]*', m.group(3))
        # skip a leading cast such as (uint32_t)
        arg = [a for a in arg
               if a not in ('uint32_t', 'int32_t', 'uint16_t', 'short')]
        got[int(m.group(2), 16)] = (m.group(1), arg[-1] if arg else None)
    assert got, 'no battle_enter patches found in FFNx'

    # (instruction offset, what it is, where its patchable field sits)
    OWN = {0x1E2: ('letterbox y  [0x9A89E4] = 74',  0x1E8),
           0x1EC: ('STORE rect.x [0x9AAD4C] = 0',   0x1F2),
           0x1F6: ('STORE rect.y [0x9AAD50] = 0',   0x1FC),
           0x200: ('STORE rect.w [0x9AAD5C] = 640', 0x206),
           0x214: ('STORE rect.h [0x9AAD68] = 332', 0x21A),
           0x21E: ('STORE scale  [0x9AD1A8] = 2',   0x224),
           0x228: ('READ  rect.w',                  0x229),
           0x22D: ('READ  rect.x',                  0x22F)}
    hit = {ins: got.get(field) for ins, (_d, field) in OWN.items()}

    # The rect's origin, width and scale stores keep their stock immediates.
    assert hit[0x1EC] is None, 'FFNx now rewrites the stored rect.x: %r' % (
        hit[0x1EC],)
    assert hit[0x1F6] is None, hit[0x1F6]
    assert hit[0x200] is None, hit[0x200]
    assert hit[0x21E] is None, hit[0x21E]
    # What it does patch: the height, the letterbox y, and the two reads.
    assert hit[0x214] == ('int', 'wide_viewport_height'), hit[0x214]
    assert hit[0x1E2] == ('int', 'wide_viewport_y'), hit[0x1E2]
    assert hit[0x228] == ('dword', 'wide_viewport_width'), hit[0x228]
    assert hit[0x22D] == ('dword', 'wide_viewport_x'), hit[0x22D]


@needs_ffnx
def test_those_offsets_really_are_those_instructions():
    """The mapping above is only meaningful if the x86 matches it."""
    import ff7nx_resolve as R
    from capstone import Cs, CS_ARCH_X86, CS_MODE_32
    exe = R.Exe((ROOT / 'game_data_files' / 'ff7_1.02' / 'ff7_en').read_bytes())
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    BE = 0x41B300
    seen = {}
    for i in md.disasm(exe.read(BE + 0x1E2, 0x60), BE + 0x1E2):
        seen[i.address - BE] = (i.mnemonic, i.op_str, i.size)
    # stores: `mov dword ptr [abs], imm32` is 10 bytes, immediate at +6
    for off, addr, imm in ((0x1EC, 0x9aad4c, 0), (0x1F6, 0x9aad50, 0),
                           (0x200, 0x9aad5c, 640), (0x214, 0x9aad68, 332),
                           (0x21E, 0x9ad1a8, 2)):
        mn, ops, size = seen[off]
        assert mn == 'mov' and hex(addr) in ops and size == 10, (off, ops)
        assert int(ops.rsplit(',', 1)[1], 0) == imm, (off, ops)
    # reads: the two FFNx redirects
    assert seen[0x228][:2] == ('mov', 'eax, dword ptr [0x9aad5c]'), seen[0x228]
    assert seen[0x22D][:2] == ('mov', 'ecx, dword ptr [0x9aad4c]'), seen[0x22D]
    assert seen[0x228][2] == 5 and seen[0x22D][2] == 6   # operand at +1 / +2


def test_the_cave_adds_ffnxs_wrapper_and_nothing_else():
    """Build 390's compensation, stated as a value and rejected."""
    wf, wr = B.ifrit_ffnx_wrapper_deltas()
    cf, cr = B._ifrit_deltas()
    assert [c - w for c, w in zip(cf, wf)] == [0] * 4, cf
    assert [c - w for c, w in zip(cr, wr)] == [0] * 4, cr
    assert cf == (-107, 65, -107, 65), cf
    assert cr == (65, 109, 65, 109), cr


@needs_ffnx
def test_three_sites_and_which_wrapper_each_uses():
    import re
    src = FFNX.read_text()
    hooks = re.findall(
        r'replace_call_function\(ff7_externals\.ifrit_sub_595A05\s*\+\s*'
        r'(0x[0-9A-Fa-f]+),\s*ifrit_(\w+?)_wave', src)
    assert [(int(o, 16), k) for o, k in hooks] == [
        (0x930, 'first'), (0xAEC, 'second_third'), (0xCC0, 'second_third')]
    assert [s[3] for s in B.IFRIT_SITES] == ['first', 'rest', 'rest']


# --------------------------------------------------------- against the image

@needs_main
def test_every_site_is_a_stock_call_to_the_translated_draw():
    m = nxmap.Main(str(MAIN))
    lo, hi = m.extent(B.IFRIT_X86)
    draw_lo, _ = m.extent(0x66A47E)
    assert draw_lo == B.IFRIT_ENGINE_DRAW
    for label, hook, stock, _k, _r in B.IFRIT_SITES:
        assert lo <= hook < hi, label
        assert word(m.img, hook) == stock, label
        assert stock == A.bl(hook, B.IFRIT_ENGINE_DRAW), label


@needs_main
def test_there_are_exactly_three_such_calls_in_the_function():
    """If the recompiler ever emits a fourth, the mapping is ambiguous."""
    m = nxmap.Main(str(MAIN))
    lo, hi = m.extent(B.IFRIT_X86)
    found = [va for va in range(lo, hi, 4)
             if word(m.img, va) == A.bl(va, B.IFRIT_ENGINE_DRAW)]
    assert found == [s[1] for s in B.IFRIT_SITES], [hex(v) for v in found]


@needs_main
def test_each_site_carries_the_guest_push_signature():
    m = nxmap.Main(str(MAIN))
    for label, hook, _s, _k, reg in B.IFRIT_SITES:
        for off, want in B._ifrit_signature(reg):
            assert word(m.img, hook + off) == want, (
                '%s: +0x%X' % (label, hook + off))


# -------------------------------------------------- the cave, executed

def _build_cave(hook, legacy_tall=False, base=0x950000):
    body = B._ifrit_body(hook, legacy_tall)
    addrs = [base + 4 * i for i in range(len(body))]
    return B._ifrit_fixup(body, addrs, hook), addrs


@needs_main
@pytest.mark.parametrize('label,hook', [(s[0], s[1]) for s in B.IFRIT_SITES])
@pytest.mark.parametrize('tall', [False, True])
@pytest.mark.parametrize('start', [
    (0, 0, 0, 0),
    (0, 510, 0, 510),                # a stock wave-1 band
    (510, 640, 510, 640),            # a stock wave-2/3 band
    (100, 200, 300, 400),
    (-300, -200, -100, 0),
    (32000, -32000, 1, -1),          # near the short limits, must wrap as C++
])
def test_the_cave_computes_the_destination_transform(label, hook, tall, start):
    """BUILD 411. This replaced the FFNx-delta comparison, and the delta model
    it compared against.

    FFNx widens at the SOURCE because FFNx's source really is 642 game units
    wide. This port's source is the whole anamorphic frame already -- see
    _ifrit_ops -- so the fix is the destination transform ff7nx_swirlscale and
    ff7nx_escapescale use, and it is imported from escapescale rather than
    re-derived. What is checked here is that the installed words compute it,
    through the emulator, including the C++ short wrap.
    """
    words, addrs = _build_cave(hook, tall)

    mem = E.Mem()
    cpu = E.Cpu(mem, host_base=HOST)
    cpu.sp = 0x800000000
    for r in range(31):
        cpu.x[r] = E.GARBAGE
    cpu.x[B.IFRIT_CTX] = GUEST_CTX
    mem.setu(GUEST_CTX + B.IFRIT_ECX, WAVE_PTR, 4)
    wave_h = cpu.guest_to_host(WAVE_PTR)
    for off, v in zip(B.IFRIT_X, start):
        mem.setu(wave_h + off, v & 0xFFFF, 2)
    for off, v in zip(B.IFRIT_Y, start):
        mem.setu(wave_h + off, v & 0xFFFF, 2)

    # The final two words call the draw and branch back; stop before the call
    # so the emulator does not have to model the whole renderer.
    cpu.run(addrs[0], words[:-2])

    def rd(off):
        v = mem.u(wave_h + off, 2)
        return v - 0x10000 if v & 0x8000 else v

    def s16(v):
        v &= 0xFFFF
        return v - 0x10000 if v & 0x8000 else v

    for off, v in zip(B.IFRIT_X, start):
        assert rd(off) == s16(B.ifrit_expected_x(v)), (
            '%s x+0x%X: %d -> %d, model says %d'
            % (label, off, v, rd(off), s16(B.ifrit_expected_x(v))))
    for off, v in zip(B.IFRIT_Y, start):
        want = s16(B.ifrit_expected_y(v)) if tall else v
        assert rd(off) == want, (
            '%s y+0x%X: %d -> %d, expected %d'
            % (label, off, v, rd(off), want))


@needs_main
def test_the_x_transform_is_escapescales_own_words():
    """Not a second invention: the same function, byte for byte."""
    import ff7nx_escapescale as ES
    body = B._ifrit_body(B.IFRIT_SITES[0][1], False)
    want = ES.body('w9', 'w10')
    assert any(body[i:i + len(want)] == want for i in range(len(body))), body
    for x in (0, 320, 640, -50):
        assert B.ifrit_expected_x(x) == ES.expected(x)


@needs_main
def test_the_y_transform_is_escapetalls_own_words():
    import ff7nx_escapetall as ET
    body = B._ifrit_body(B.IFRIT_SITES[0][1], True)
    want = ET.body('w9', 'w10')
    assert any(body[i:i + len(want)] == want for i in range(len(body))), body
    for y in (0, 166, 332):
        assert B.ifrit_expected_y(y) == ET.expected(y)


@needs_main
def test_the_cave_does_not_disturb_the_guest_context_or_the_pointer():
    first, _rest = B._ifrit_deltas()
    words, addrs = _build_cave(B.IFRIT_SITES[0][1], first)
    mem = E.Mem()
    cpu = E.Cpu(mem, host_base=HOST)
    cpu.sp = 0x800000000
    for r in range(31):
        cpu.x[r] = E.GARBAGE
    cpu.x[B.IFRIT_CTX] = GUEST_CTX
    mem.setu(GUEST_CTX + B.IFRIT_ECX, WAVE_PTR, 4)
    sp_before = cpu.sp
    cpu.run(addrs[0], words[:-2])
    assert cpu.x[B.IFRIT_CTX] == GUEST_CTX, 'the guest context was clobbered'
    assert mem.u(GUEST_CTX + B.IFRIT_ECX, 4) == WAVE_PTR, 'ECX was clobbered'
    assert cpu.sp == sp_before, 'the cave left the stack unbalanced'


@needs_main
def test_the_last_two_words_are_the_call_and_the_way_back():
    for label, hook, _s, kind, _r in B.IFRIT_SITES:
        first, rest = B._ifrit_deltas()
        deltas = first if kind == 'first' else rest
        words, addrs = _build_cave(hook, deltas)
        assert words[-2] == A.bl(addrs[-2], B.IFRIT_ENGINE_DRAW), label
        assert words[-1] == A.b(addrs[-1], hook + 4), label


# ------------------------------------------------------------ apply/revert

@needs_main
def test_apply_then_revert_restores_main_byte_for_byte(tmp_path):
    dst = tmp_path / 'main'
    shutil.copy(MAIN, dst)
    before = hashlib.sha256(dst.read_bytes()).hexdigest()
    assert B.apply_ifrit(dst, log=lambda *a: None) == 0
    assert hashlib.sha256(dst.read_bytes()).hexdigest() != before

    m = nxmap.Main(str(dst))
    for label, hook, stock, _k, _r in B.IFRIT_SITES:
        cur = word(m.img, hook)
        assert cur != stock, label
        assert B._branch_target(hook, cur) is not None, label

    assert B.apply_ifrit(dst, log=lambda *a: None) == 0        # idempotent
    assert B.apply_ifrit(dst, revert=True, log=lambda *a: None) == 0
    assert hashlib.sha256(dst.read_bytes()).hexdigest() == before


@needs_main
def test_the_applied_caves_are_the_verified_sequence_at_their_real_addresses(
        tmp_path):
    """
    The applied cave is scattered across padding holes, so it cannot be run as
    one block the way the modelled one can. What CAN be checked is the thing
    that differs between the two: every PC-relative word.

    The instruction sequence itself is proved correct by
    test_the_cave_produces_ffnxs_shorts above, and that sequence is
    layout-independent by construction. So this asserts the patched image
    holds exactly that sequence, fixed up for the addresses it actually landed
    on -- and then decodes all three branch kinds to be sure the fixup went to
    the right places rather than merely to consistent ones.
    """
    dst = tmp_path / 'main'
    shutil.copy(MAIN, dst)
    assert B.apply_ifrit(dst, log=lambda *a: None) == 0
    m = nxmap.Main(str(dst))
    for label, hook, _s, kind, _r in B.IFRIT_SITES:
        body = B._ifrit_body(hook)
        walked, why = B._ifrit_walk(m.img, hook, len(body))
        assert walked is not None, '%s: %s' % (label, why)

        payload = [a for a in walked
                   if B._branch_target(a, word(m.img, a)) is None
                   or a == walked[-1]]
        assert len(payload) == len(body), label
        want = B._ifrit_fixup(body, payload, hook)
        got = [word(m.img, a) for a in payload]
        assert got == want, '%s: the cave is not the verified sequence' % label

        # ...and the branches point where they should, not merely consistently.
        n_translate = 0
        for a, wd in zip(payload, got):
            if (wd & 0xFC000000) == 0x94000000:        # bl
                imm = wd & 0x3FFFFFF
                if imm & (1 << 25):
                    imm -= (1 << 26)
                tgt = a + imm * 4
                assert tgt in (D.TRANSLATE, B.IFRIT_ENGINE_DRAW), (
                    '%s: bl at +0x%X goes to 0x%X' % (label, a, tgt))
                n_translate += tgt == D.TRANSLATE
        # One per vertex short the cave touches: four x, and four more y in
        # `tall`. BUILD 411 raised this from a flat 4.
        want_tr = len(B._ifrit_ops())
        assert n_translate == want_tr, (
            '%s: %d translator calls, expected %d'
            % (label, n_translate, want_tr))
        assert B._branch_target(payload[-1], got[-1]) == hook + 4, label

        # every chaining link lands on another word of this same cave
        for a in walked:
            tgt = B._branch_target(a, word(m.img, a))
            if tgt is not None and a != walked[-1]:
                assert tgt in walked, (
                    '%s: link at +0x%X leaves the cave' % (label, a))


@needs_main
def test_the_three_caves_do_not_overlap(tmp_path):
    dst = tmp_path / 'main'
    shutil.copy(MAIN, dst)
    assert B.apply_ifrit(dst, log=lambda *a: None) == 0
    m = nxmap.Main(str(dst))
    seen = set()
    for label, hook, _s, kind, _r in B.IFRIT_SITES:
        body = B._ifrit_body(hook)
        walked, _why = B._ifrit_walk(m.img, hook, len(body))
        assert not (set(walked) & seen), '%s reuses another cave word' % label
        seen |= set(walked)


@needs_main
def test_all_three_or_none(tmp_path):
    """A first wave adjusted to a wide frame with two unadjusted behind it
    would be worse than stock, which is at least self-consistent."""
    dst = tmp_path / 'main'
    shutil.copy(MAIN, dst)
    m = nxmap.Main(str(dst))
    img = bytearray(m.img)
    struct.pack_into('<I', img, B.IFRIT_SITES[1][1] - 0x10, 0xDEADBEEF)

    class Fake:
        pass
    fake = Fake()
    fake.img = bytes(img)
    fake.arm_starts = m.arm_starts
    fake.extent = m.extent
    patches, _n, problems = B.ifrit_plan(fake)
    assert problems, 'a broken signature was not noticed'
    assert not patches, 'it planned to patch the other two anyway'



# ------------------------------------------- what a "wave" actually is
#
# BUILD 394. Build 393 shipped a "cover" mode that pinned the right x to 747
# and the bottom y to 480 so the warp would reach past the UI. On hardware it
# produced a full-screen smear, because each draw is ONE BAND OF A LOOP, not
# one full-screen quad. These tests pin that structure down so the mistake
# cannot be repeated from the same wrong mental model.

def _x86():
    import ff7nx_resolve as R
    from capstone import Cs, CS_ARCH_X86, CS_MODE_32
    exe = R.Exe((ROOT / 'game_data_files' / 'ff7_1.02' / 'ff7_en').read_bytes())
    return exe, Cs(CS_ARCH_X86, CS_MODE_32)


def test_each_wave_is_a_loop_of_bands_not_one_quad():
    exe, md = _x86()
    counts = {}
    for lbl, va, n in (('first', 0x596186, 0x30), ('second', 0x596351, 0x20)):
        for i in md.disasm(exe.read(va, n), va):
            if i.mnemonic == 'cmp' and 'ebp - 0xc' in i.op_str:
                counts[lbl] = int(i.op_str.rsplit(',', 1)[1], 0)
                break
    assert counts['first'] == 0x15, counts    # 21 bands
    assert counts['second'] == 0xB, counts    # 11 bands
    # A band is 8 raw units tall; the last of wave 1 is clamped to 0xA6.
    body = exe.read(0x5961A2, 0x30)
    assert b'\xa6\x00\x00\x00' in body, 'the 0xA6 band clamp is gone'


def test_the_three_waves_tile_the_stock_viewport_exactly():
    """255 + 65 raw = 320 texels; x2 scale = 640 game units. y tops at 0xA6."""
    exe, md = _x86()
    adds = []
    for va, n in ((0x596204, 0x60), (0x5963b0, 0x70)):
        for i in md.disasm(exe.read(va, n), va):
            if i.mnemonic == 'add' and i.op_str.startswith(('ecx, 0x', 'eax, 0x')):
                adds.append(int(i.op_str.rsplit(',', 1)[1], 0))
    assert 0xFF in adds, adds          # wave 1 is 255 raw units wide
    assert 0x41 in adds, adds          # waves 2/3 are 65, starting at 255
    assert 0xFF + 0x41 == 0x140        # 320 texels
    assert 0x140 * 2 == 640            # ...which is the stock viewport width
    assert 0xA6 * 2 == 332             # ...and its height


def test_the_capture_widths_are_hard_coded_and_cannot_be_widened():
    """The source the waves sample is 0x100 + 0x41 raw units. Immediates.

    This is why widening the quads stretches instead of revealing: the capture
    stays 642 game units wide, and the u coordinates that address it are 8-bit
    literals (1..254 and 1..64) which cannot reach further even if it grew.
    FFNx has exactly the same limit, so FFNx stretches too.
    """
    exe, md = _x86()
    setup = {}
    # 0x595DE7..0x595E2F: the defaults, before the mode switch rewrites x/y
    for i in md.disasm(exe.read(0x595DE7, 0x48), 0x595DE7):
        if i.mnemonic == 'mov' and i.op_str.startswith('word ptr [ebp - '):
            slot = int(i.op_str.split('[ebp - ')[1].split(']')[0], 16)
            setup[slot] = int(i.op_str.rsplit(',', 1)[1], 0)
    assert setup[0x20] == 0x100, setup   # capture 1 width
    assert setup[0x1E] == 0xA6, setup    # capture 1 height
    assert setup[0x28] == 0x41, setup    # capture 2 width
    assert setup[0x26] == 0x58, setup    # capture 2 height
    assert setup[0x30] == 0x41, setup    # capture 3 width
    assert setup[0x2E] == 0x58, setup    # capture 3 height
    assert (0x100 + 0x41) * 2 == 642     # the whole source, in game units


def test_the_battle_branch_takes_its_origin_from_the_rect_and_nothing_else():
    """Capture origin = (rect.x, rect.y) and (rect.x + 0x100, ...).

    The widths above never appear here, which is the point: moving the battle
    rect moves the capture but cannot resize it.
    """
    exe, md = _x86()
    ops = [(i.mnemonic, i.op_str)
           for i in md.disasm(exe.read(0x595E59, 0x4B), 0x595E59)]
    text = ' | '.join('%s %s' % o for o in ops)
    assert '0x9aad4c' in text and '0x9aad50' in text, text
    assert 'eax, 0x100' in text, text     # capture 2/3 x = rect.x + 0x100
    assert 'eax, 0x58' in text, text      # capture 3   y = rect.y + 0x58
    assert '0x9aad5c' not in text, 'the capture now reads rect.w -- recheck'


def test_the_cave_writes_y_only_when_the_sampling_moves_with_it():
    """Build 393's cover mode pinned y to 480 and build 397 raised the band
    layout; both wrote y without moving the capture, and both came back wrong.
    So y is only ever touched in `tall`, which ff7nx_ifritsrc refuses to let
    run without its three companion words."""
    offs = [off for off, _fn in B._ifrit_ops(False)]
    assert offs == list(B.IFRIT_X), offs
    for off in B.IFRIT_Y:
        assert off not in offs, 'the wide-only cave writes y at +0x%X' % off
    offs = [off for off, _fn in B._ifrit_ops(True)]
    assert offs == list(B.IFRIT_X) + list(B.IFRIT_Y), offs


# ------------------------------------------------- the un-squeeze, tested
#
# BUILD 395. Patrick's observation, which is the thing builds 389-394 were all
# missing:
#
#   "its taking the entire image of the region above the ui from the left end
#    of the 16:9 screen to the right side of the screen and squeezing the image
#    along the center vertical axis from both ends while maintaining the same
#    height into the 4:3 region right above the ui... if I copy the entire heat
#    warped capture and stretch it left to right while maintaining the same
#    height to fill the screen it aligns perfectly with the no effect image"
#
# So the capture already holds the WHOLE wide frame; it is the DRAWING that is
# too narrow. That is the opposite of what build 394's note concluded, and it
# makes FFNx's widening the exact inverse of the squeeze rather than a stretch.
#
# The three waves tile 320 texels (255 + 65), so texel t carries the content of
# game x = -107 + t * 854/320.

FRAME_X, FRAME_W, TILED_TEXELS = -107, 854, 320


def _content_x(texel):
    """Where the content in this texel belongs on a 16:9 frame."""
    return FRAME_X + texel * FRAME_W / float(TILED_TEXELS)


# (label, texel span, which wrapper, which end of the delta tuple)
_SPANS = (('wave 1 left',   0,   'first', 0),
          ('wave 1 right',  255, 'first', 1),
          ('wave 2/3 left', 255, 'rest',  0),
          ('wave 2/3 right', 320, 'rest', 1))


def _drawn(texel, kind, end, widened):
    first, rest = B._ifrit_deltas()
    d = (first if kind == 'first' else rest)[end] if widened else 0
    return texel * 2 + d                     # raw*scale + rect.x(0) + delta


@pytest.mark.parametrize('label,texel,kind,end', _SPANS)
def test_widening_puts_each_texel_where_its_content_belongs(label, texel,
                                                            kind, end):
    """FFNx's widening is the inverse of the squeeze, to within 2 units."""
    err = _drawn(texel, kind, end, widened=True) - _content_x(texel)
    assert abs(err) <= 2.0, '%s: off by %.1f game units' % (label, err)


@pytest.mark.parametrize('label,texel,kind,end', _SPANS)
def test_stock_is_squeezed_inwards_by_107_at_each_end(label, texel, kind, end):
    """And the unpatched drawing is exactly the reported squeeze."""
    err = _drawn(texel, kind, end, widened=False) - _content_x(texel)
    if texel == 0:
        assert round(err) == 107, err          # left end pulled right
    elif texel == TILED_TEXELS:
        assert round(err) == -107, err         # right end pulled left
    else:
        assert abs(err) < 107, err             # interior, proportionally less


def test_the_squeeze_is_the_wide_to_narrow_ratio():
    """Drawn span 640, content span 854: widening restores the ratio."""
    first, rest = B._ifrit_deltas()
    stock = TILED_TEXELS * 2
    wide = (TILED_TEXELS * 2 + rest[1]) - first[0]
    assert stock == 640
    assert abs(wide / float(stock) - FRAME_W / 640.0) < 0.005, wide


def test_the_effect_stops_at_the_battle_rect_bottom():
    """Why the vertical half is still open: nothing below y=332 is drawn.

    The record builder computes y = (v + offset) * scale + rect.y with v
    topping out at 0xA6, so the warp covers game y 0..332 and the UI band
    below it is untouched. Covering it needs a taller capture AND more bands.
    """
    exe, md = _x86()
    seen = [i for i in md.disasm(exe.read(0x595D73, 0x20), 0x595D73)]
    text = ' | '.join('%s %s' % (i.mnemonic, i.op_str) for i in seen)
    assert 'add ecx, 0x58' in text, text        # wave 3's v -> y offset
    assert 'imul ecx, dword ptr [0x9ad1a8]' in text, text
    assert 'add ecx, dword ptr [0x9aad50]' in text, text   # + rect.y
    assert 0xA6 * 2 == 332                      # the bottom, in game units
    assert 332 < 480                            # ...short of the frame


# ------------------------------------- full-frame bands (BUILD 397)
#
# "its stopping at the ui's top, not extending to the sides of the ui and
# below it". The record array has exactly 42 slots and the 4:3 layout uses all
# 42; 16:9 at 8-texel bands would need 60. Doubling the band height to 16
# covers the whole frame in 30.

VC_STEP, VC_SCALE = 16, 2
VC_WAVES = (('wave 1 left',        15, 0,   240),
            ('wave 2 right upper',  8, 0,   128),
            ('wave 3 right lower',  7, 128, 240))


def test_the_bands_tile_the_whole_frame_with_no_gap_and_no_overlap():
    for label, n, start, end in VC_WAVES:
        edges = [start + i * VC_STEP for i in range(n)] + [end]
        assert edges[0] == start, label
        assert edges[-1] == end, '%s: %d bands of %d from %d reach %d, not %d' % (
            label, n, VC_STEP, start, edges[-2] + VC_STEP, end)
        for a, b in zip(edges, edges[1:]):
            assert 0 < b - a <= VC_STEP, (label, a, b)
    left = VC_WAVES[0]
    right = [(s, e) for _l, _n, s, e in VC_WAVES[1:]]
    assert right[0][1] == right[1][0], 'right column has a seam'
    assert (left[2], left[3]) == (right[0][0], right[1][1]), 'columns disagree'


def test_the_frame_is_actually_covered_top_to_bottom():
    assert 240 * VC_SCALE == 480, 'the wide frame is 480 game units tall'
    assert 0xA6 * VC_SCALE == 332, 'the 4:3 viewport it used to stop at'
    assert 332 < 480


def test_it_fits_the_42_slot_array():
    total = sum(n for _l, n, _s, _e in VC_WAVES)
    assert total == 30, total
    assert total <= 42, 'the record array at 0xBCBFE0 holds 42'
    at_8 = sum(-(-(e - s) // 8) for _l, _n, s, e in VC_WAVES)
    assert at_8 == 30 + 16 + 14 > 42, 'which is why the step had to double'


def test_vertical_texel_density_is_unchanged():
    """The only cost is band height. Texels per game unit must not move."""
    before = 0xA6 / 332.0
    after = 240 / 480.0
    assert abs(before - after) < 1e-9, (before, after)


def test_the_right_column_halves_are_equal_because_they_share_a_register():
    """cap2 and cap3 tex_h both come from w23 at +0x70F990."""
    _l, _n, s2, e2 = VC_WAVES[1]
    _l, _n, s3, e3 = VC_WAVES[2]
    assert e2 - s2 == 128 and s3 == 128
    # cap3 covers 128..256 but wave 3 stops at 240: the tail is never sampled.
    assert e3 == 240 < 256


@needs_main
def test_every_site_matches_the_real_binary():
    """The strongest check here: all 38 'before' words are what is in main."""
    m = nxmap.Main(str(MAIN))
    patches, _notes, problems = B.ifrit_vcover_plan(m)
    assert problems == [], problems
    assert len(patches) == len(B.IFRIT_VC_SITES) == 38, len(patches)


@needs_main
def test_the_sites_are_inside_ifrits_body_and_distinct():
    m = nxmap.Main(str(MAIN))
    lo, hi = m.extent(B.IFRIT_X86)
    seen = set()
    for label, va, _o, _n in B.IFRIT_VC_SITES:
        assert lo <= va < hi, (label, hex(va))
        assert va not in seen, 'duplicate site %s' % hex(va)
        seen.add(va)


@needs_main
def test_no_site_collides_with_the_widening_caves():
    """The two Ifrit patches must not fight over a word."""
    m = nxmap.Main(str(MAIN))
    hooks = {h for _l, h, _s, _k, _r in B.IFRIT_SITES}
    for label, va, _o, _n in B.IFRIT_VC_SITES:
        assert va not in hooks, (label, hex(va))


def test_the_new_words_decode_to_the_intended_instructions():
    import struct as _s
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)

    def dis(w):
        i = next(md.disasm(_s.pack('<I', w), 0x700000))
        return '%s %s' % (i.mnemonic, i.op_str)
    by = {}
    for label, _va, old, new in B.IFRIT_VC_SITES:
        by.setdefault(label, (dis(old), dis(new)))
    assert by['band step x8->x16'][1].endswith('#4'), by['band step x8->x16']
    assert by['band bottom +8->+16'][1].endswith('#0x10')
    assert by['loop bound 21->15'][1].endswith('#0xf')
    assert by['loop bound 11->8'][1].endswith('#8')
    assert by['loop bound 10->7'][1].endswith('#7')
    assert by['clamp index 20->14'][1].endswith('#0xe')
    assert by['clamp index 9->6'][1].endswith('#6')
    assert by['clamp value 166->240'][1].endswith('#0xf0')
    assert by['clamp value 78->112'][1].endswith('#0x70')
    assert by['cap1 tex_h 166->240'][1].endswith('#0xf0')
    assert by['cap2+3 tex_h 88->128'][1].endswith('#0x80')
    assert by['cap3 y 88->128'][1].endswith('#0x80')
    for _lbl, (before, after) in by.items():
        assert before.split()[0] == after.split()[0], (before, after)


@needs_main
def test_apply_revert_and_idempotence(tmp_path):
    dst = tmp_path / 'main'
    shutil.copy(MAIN, dst)
    before = hashlib.sha256(dst.read_bytes()).hexdigest()
    assert B.apply_ifrit_vcover(dst, log=lambda *a: None) == 0
    once = hashlib.sha256(dst.read_bytes()).hexdigest()
    assert once != before
    assert B.apply_ifrit_vcover(dst, log=lambda *a: None) == 0
    assert hashlib.sha256(dst.read_bytes()).hexdigest() == once, 'not idempotent'
    assert B.apply_ifrit_vcover(dst, revert=True, log=lambda *a: None) == 0
    assert hashlib.sha256(dst.read_bytes()).hexdigest() == before


@needs_main
def test_it_refuses_rather_than_writing_a_half_layout(tmp_path):
    """One unexpected word must abort the whole thing."""
    dst = tmp_path / 'main'
    shutil.copy(MAIN, dst)
    m = nxmap.Main(str(dst))
    label, va, _o, _n = B.IFRIT_VC_SITES[7]
    m.img = bytearray(m.img)
    struct.pack_into('<I', m.img, va, 0xD503201F)      # nop
    patches, _n2, problems = B.ifrit_vcover_plan(m)
    assert patches == [], 'wrote patches despite a bad site'
    assert problems and hex(va)[2:] in problems[0].lower(), problems


def test_the_switch_defaults_off_because_397_was_retracted():
    import os
    old = os.environ.get(B.IFRIT_VC_ENV)
    try:
        os.environ.pop(B.IFRIT_VC_ENV, None)
        assert B.ifrit_vcover_enabled() is False
        for v in ('0', 'off', 'no', 'false'):
            os.environ[B.IFRIT_VC_ENV] = v
            assert B.ifrit_vcover_enabled() is False, v
        os.environ[B.IFRIT_VC_ENV] = '1'
        assert B.ifrit_vcover_enabled() is True
    finally:
        if old is None:
            os.environ.pop(B.IFRIT_VC_ENV, None)
        else:
            os.environ[B.IFRIT_VC_ENV] = old


def _decoded_layout():
    """Reconstruct the band layout from the words the module actually ships."""
    import struct as _s
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    out = {}
    for label, va, _old, new in B.IFRIT_VC_SITES:
        i = next(md.disasm(_s.pack('<I', new), 0x700000))
        imm = int(i.op_str.rsplit('#', 1)[1], 0)
        out.setdefault(label, set()).add(imm)
    one = {k: (v.pop() if len(v) == 1 else sorted(v)) for k, v in out.items()}
    return one


def test_the_shipped_words_really_do_tile_the_frame():
    """Derived from IFRIT_VC_SITES, not from constants typed in this file."""
    d = _decoded_layout()
    step = 1 << d['band step x8->x16']          # lsl #4 -> 16
    assert step == 16, step
    assert d['band bottom +8->+16'] == step, 'band bottom must match the step'

    n1 = d['loop bound 21->15']
    n2 = d['loop bound 11->8']
    n3 = d['loop bound 10->7']
    top = d['cap1 tex_h 166->240']
    half = d['cap2+3 tex_h 88->128']
    start3 = d['cap3 y 88->128']

    # left column: n1 bands of `step`, last one clamped, must reach cap1 tex_h
    assert d['clamp index 20->14'] == n1 - 1, (d['clamp index 20->14'], n1)
    assert d['clamp value 166->240'] == top
    assert (n1 - 1) * step < top <= n1 * step, (n1, step, top)
    assert n1 * step == top, 'left column does not tile exactly'

    # right column: upper n2 bands from 0, lower n3 bands from start3
    assert n2 * step == half == start3, (n2, step, half, start3)
    assert d['clamp index 9->6'] == n3 - 1
    assert d['draw3 v_top  +0x58->+0x80'] == start3
    assert d['draw3 v_bot  +0x60->+0x90'] == start3 + step
    assert d['build3 y origin 88->128'] == start3
    assert start3 + n3 * step == top, 'right column does not reach the bottom'
    assert d['clamp value 78->112'] == n3 * step

    # and the whole thing fits the array
    assert n1 + n2 + n3 == 30 <= 42, (n1, n2, n3)
    # ...covering the full 16:9 frame
    assert top * 2 == 480


# The layout only holds together if sites that must agree actually agree, so
# check them by ADDRESS -- a label is part of what a mutation can change.
VC_BY_VA = {
    0x0070F008: ('build1 clamp value', 240),
    0x007102D8: ('draw1  clamp value', 240),
    0x00711004: ('draw3  clamp value', 240),
    0x0070F944: ('cap1 tex_h', 240),
    0x0070F73C: ('build3 clamp value', 112),
    0x0070F990: ('cap2+3 tex_h', 128),
    0x0070FAFC: ('cap3 y', 128),
    0x00710FA4: ('draw3 v_top origin', 128),
    0x0071101C: ('draw3 v_bot origin', 128 + 16),
    0x0070F500: ('build3 y origin a', 128),
    0x0070F5B4: ('build3 y origin b', 128),
}


def test_every_layout_constant_by_address():
    """Pinned per-site, so renaming or moving one cannot hide a mismatch."""
    import struct as _s
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    got = {}
    for _label, va, _old, new in B.IFRIT_VC_SITES:
        i = next(md.disasm(_s.pack('<I', new), 0x700000))
        got[va] = int(i.op_str.rsplit('#', 1)[1], 0)
    for va, (what, want) in VC_BY_VA.items():
        assert va in got, '%s (+0x%X) is no longer patched' % (what, va)
        assert got[va] == want, '%s: %d, expected %d' % (what, got[va], want)
    # the three that must be the same number as the left column's height
    assert got[0x0070F008] == got[0x007102D8] == got[0x00711004] == got[0x0070F944]
    # the right column's two halves must meet exactly
    assert got[0x0070F990] == got[0x0070FAFC] == got[0x00710FA4]
    # and the lower half must finish where the left column does
    assert got[0x0070FAFC] + 7 * 16 == got[0x0070F944]



@needs_ffnx
def test_this_port_renders_the_battle_scene_332_tall_not_480():
    """Why the warp SHOULD stop at the UI here, and does not in FFNx.

    The effect samples the composited frame. Below rect.h there is no scene,
    only UI, so a taller warp redraws the UI over itself.
    """
    import ff7nx_resolve as R
    from capstone import Cs, CS_ARCH_X86, CS_MODE_32
    exe = R.Exe((ROOT / 'game_data_files' / 'ff7_1.02' / 'ff7_en').read_bytes())
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    stores = {}
    for i in md.disasm(exe.read(0x41B4EC, 0x40), 0x41B4EC):
        if i.mnemonic == 'mov' and 'dword ptr [0x9aad' in i.op_str:
            va = int(i.op_str.split('[')[1].split(']')[0], 16)
            stores[va] = int(i.op_str.rsplit(',', 1)[1], 0)
    assert stores[0x9AAD68] == 332, 'battle rect height is no longer 332'
    # FFNx changes it; this port does not.
    src = FFNX.read_text()
    assert 'battle_enter + 0x21A, wide_viewport_height' in src.replace(
        'ff7_externals.', ''), 'FFNx no longer raises the battle rect height'
    assert 0xA6 * 2 == 332, 'the stock wave covers exactly the 3D viewport'


# ------------------------------------------------ removal (BUILD 399)
#
# "lets just remove this warp capture effect for ifrit ... it will look much
#  better". The effect swaps the battle viewport for a 320x166-TEXEL copy of
#  itself -- 1:1 on a 320-wide PSX screen, about 1:4 here.

def test_strip_zeroes_all_three_draw_loop_bounds():
    import struct as _s
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    assert len(B.IFRIT_STRIP_SITES) == 3
    olds = []
    for _label, va, old, new in B.IFRIT_STRIP_SITES:
        i = next(md.disasm(_s.pack('<I', new), 0x700000))
        assert i.mnemonic == 'sub' and i.op_str.endswith('#0'), i.op_str
        olds.append(int(next(md.disasm(_s.pack('<I', old), 0x700000))
                        .op_str.rsplit('#', 1)[1], 0))
    assert sorted(olds) == [0xA, 0xB, 0x15], olds   # the 10/11/21 band loops
    # every strip site is one of the six loop bounds, and a DRAW one
    draws = {0x00710244, 0x00710930, 0x00710F6C}
    assert {va for _l, va, _o, _n in B.IFRIT_STRIP_SITES} == draws


def test_strip_leaves_the_build_loops_and_captures_alone():
    """Minimal patch: same timing, same display-list traffic, nothing drawn."""
    builds = {0x0070EF78, 0x0070F274, 0x0070F6AC}
    caps = {0x0070F944, 0x0070F990, 0x0070FAFC}
    touched = {va for _l, va, _o, _n in B.IFRIT_STRIP_SITES}
    assert not (touched & builds), 'strip should not touch the build loops'
    assert not (touched & caps), 'strip should not touch the captures'


def test_flat_zeroes_the_wobble_but_keeps_the_column_offsets():
    import struct as _s
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    assert len(B.IFRIT_FLAT_SITES) == 6
    for _label, _va, old, new in B.IFRIT_FLAT_SITES:
        o = next(md.disasm(_s.pack('<I', old), 0x700000))
        n = next(md.disasm(_s.pack('<I', new), 0x700000))
        assert o.mnemonic == 'asr' and o.op_str.endswith('#0xa'), o.op_str
        assert n.op_str.replace(' ', '') in ('w8,wzr', 'w8,wzr,wzr'), n.op_str
    # the +0xff that puts waves 2/3 in the right column must NOT be a site
    assert 0x00710418 not in {va for _l, va, _o, _n in B.IFRIT_FLAT_SITES}


@needs_main
def test_strip_and_flat_match_the_real_binary_and_round_trip(tmp_path):
    m = nxmap.Main(str(MAIN))
    for sites, what in ((B.IFRIT_STRIP_SITES, 'strip'),
                        (B.IFRIT_FLAT_SITES, 'flat')):
        patches, problems = B._ifrit_word_plan(m, sites, what)
        assert problems == [], problems
        assert len(patches) == len(sites)
    for fn in (B.apply_ifrit_strip, B.apply_ifrit_flat):
        dst = tmp_path / ('main_%s' % fn.__name__)
        shutil.copy(MAIN, dst)
        before = hashlib.sha256(dst.read_bytes()).hexdigest()
        assert fn(dst, log=lambda *a: None) == 0
        assert hashlib.sha256(dst.read_bytes()).hexdigest() != before
        assert fn(dst, log=lambda *a: None) == 0          # idempotent
        assert fn(dst, revert=True, log=lambda *a: None) == 0
        assert hashlib.sha256(dst.read_bytes()).hexdigest() == before


def test_the_modes_and_what_each_one_wants():
    import os
    old = os.environ.get(B.IFRIT_ENV)
    try:
        want = {None: ('wide', True, False, False),
                'wide': ('wide', True, False, False),
                '1': ('wide', True, False, False),
                'strip': ('strip', False, True, False),
                'flat': ('flat', True, False, True),
                '0': ('off', False, False, False),
                'off': ('off', False, False, False)}
        for v, (mode, widen, strip, flat) in want.items():
            if v is None:
                os.environ.pop(B.IFRIT_ENV, None)
            else:
                os.environ[B.IFRIT_ENV] = v
            assert B.ifrit_mode() == mode, (v, B.ifrit_mode())
            assert B.ifrit_enabled() is widen, v
            assert B.ifrit_stripped() is strip, v
            assert B.ifrit_flat() is flat, v
    finally:
        if old is None:
            os.environ.pop(B.IFRIT_ENV, None)
        else:
            os.environ[B.IFRIT_ENV] = old


def test_stripping_does_not_also_leave_the_widening_caves_in():
    """Nothing draws, so the caves would be dead weight in the padding pool."""
    import os
    old = os.environ.get(B.IFRIT_ENV)
    try:
        os.environ[B.IFRIT_ENV] = 'strip'
        assert B.ifrit_enabled() is False
        os.environ[B.IFRIT_ENV] = 'flat'
        assert B.ifrit_enabled() is True, 'flat needs the widening to align'
    finally:
        if old is None:
            os.environ.pop(B.IFRIT_ENV, None)
        else:
            os.environ[B.IFRIT_ENV] = old


# ------------------------------------------- Escape captures (BUILD 402)

def test_escape_sites_are_two_words_and_decode_as_intended():
    import struct as _s
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)

    def dis(w):
        i = next(md.disasm(_s.pack('<I', w), 0x800000))
        return '%s %s' % (i.mnemonic, i.op_str)
    assert len(B.ESCAPE_SITES) == 2
    by = {lbl: (dis(o), dis(n)) for lbl, _va, o, n in B.ESCAPE_SITES}
    org = by['capture B origin 160 -> 320']
    assert org[0].endswith('#0xa0') and org[1].endswith('#0x140'), org
    sc = by['capture scale 1 -> 2']
    assert sc[0].endswith('#1') and sc[1].endswith('#2'), sc
    for before, after in by.values():
        assert before.split()[0] == after.split()[0], (before, after)


@needs_main
def test_the_scale_word_is_the_binarys_own_encoding():
    """Copied from the default branch's `mov w20, #2`, not hand-encoded."""
    m = nxmap.Main(str(MAIN))
    own = struct.unpack_from('<I', m.img, 0x0083B6B4)[0]
    ours = [n for lbl, _v, _o, n in B.ESCAPE_SITES if 'scale' in lbl][0]
    assert own == ours, (hex(own), hex(ours))


@needs_main
def test_escape_sites_match_the_binary_and_sit_in_escape_setup():
    m = nxmap.Main(str(MAIN))
    lo, hi = m.extent(B.ESCAPE_X86)
    patches, problems = B.escape_plan(m)
    assert problems == [], problems
    assert len(patches) == 2
    for _lbl, va, _o, _n in B.ESCAPE_SITES:
        assert lo <= va < hi, hex(va)


@needs_main
def test_escape_apply_revert_round_trip(tmp_path):
    dst = tmp_path / 'main'
    shutil.copy(MAIN, dst)
    before = hashlib.sha256(dst.read_bytes()).hexdigest()
    assert B.apply_escape(dst, log=lambda *a: None) == 0
    once = hashlib.sha256(dst.read_bytes()).hexdigest()
    assert once != before
    assert B.apply_escape(dst, log=lambda *a: None) == 0
    assert hashlib.sha256(dst.read_bytes()).hexdigest() == once
    assert B.apply_escape(dst, revert=True, log=lambda *a: None) == 0
    assert hashlib.sha256(dst.read_bytes()).hexdigest() == before


@needs_main
def test_escape_refuses_on_an_unexpected_word(tmp_path):
    dst = tmp_path / 'main'
    shutil.copy(MAIN, dst)
    m = nxmap.Main(str(dst))
    m.img = bytearray(m.img)
    struct.pack_into('<I', m.img, B.ESCAPE_SITES[0][1], 0xD503201F)
    patches, problems = B.escape_plan(m)
    assert patches == [] and problems, (patches, problems)


def test_escape_probe_does_not_touch_the_mesh_or_the_draw_loop():
    """Coverage and the hitch are separate jobs; this only moves the source."""
    vas = {va for _l, va, _o, _n in B.ESCAPE_SITES}
    # escape_mesh and escape_draw live well above escape_setup's ARM body
    assert all(0x0083B460 <= va < 0x0083BC20 for va in vas), vas


def test_the_escape_probe_is_off_after_403_retracted_it():
    """It made the left tile noise on hardware. Negative result, kept off."""
    import os
    old = os.environ.get(B.ESCAPE_ENV)
    try:
        os.environ.pop(B.ESCAPE_ENV, None)
        assert B.escape_fullsrc() is False
    finally:
        if old is not None:
            os.environ[B.ESCAPE_ENV] = old
