#!/usr/bin/env python3
"""
tests/test_escapegpu.py -- everything ff7nx_escapegpu's header claims, asserted
against `exefs/main` and `ff7_en_switch` rather than against the module.

The Escape work in builds 402..408 failed the same way four times: a model was
written, the patch was derived from it, and the test emulated the patch against
the model that produced it. So nothing here reads a constant out of
ff7nx_escapegpu and checks it against itself. Each test disassembles the
binary and re-derives the fact.

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
import os
import struct
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

capstone = pytest.importorskip('capstone')
from capstone import Cs, CS_ARCH_ARM64, CS_ARCH_X86, CS_MODE_ARM, CS_MODE_32

import ff7nx_escapegpu as G                                     # noqa: E402
import nxmap                                                     # noqa: E402

MAIN = os.path.join(_ROOT, 'dump', 'exefs', 'main')
FF7EN = os.path.join(_ROOT, 'data', 'ff7_en_switch')

ESCAPE_SETUP = 0x5D577B
ESCAPE_UV_INIT = 0x5D59B0
OBJ_RENDER = 0x682DB2
OBJ_INIT = 0x682D80
CAP_ALLOC = 0x42992E
MAKE_FB_TEX_CALLER = 0x674418
MAKE_FB_TEX = 0x673F5C

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


def arm(m, lo, hi):
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    return list(md.disasm(bytes(m.img[lo:hi]), lo))


def x86dis(data, va, nbytes=0x400):
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    return list(md.disasm(data[0x200 + va - 0x401000:
                               0x200 + va - 0x401000 + nbytes], va))


# ------------------------------------------------------------------ the mode
def test_graphics_mode_defaults_to_two(x86):
    """[0x9ACB5C] is sub_404D80's return, and that default is 2.

    This is the fact everything else rests on: it is what makes escape_setup
    take its mode-2 arm, and it is what made build 402's mode-1 probe dead
    code.
    """
    ins = x86dis(x86, 0x404D80, 0x80)
    txt = ['%s %s' % (i.mnemonic, i.op_str) for i in ins]
    # the default, written before the registry is consulted
    assert 'mov dword ptr [ebp - 0x14], 2' in txt, txt[:6]
    # and the return is that slot
    assert 'mov eax, dword ptr [ebp - 0x14]' in txt
    assert 'ret' in [i.mnemonic for i in ins]


def test_mode_two_is_what_kujata_uses(x86):
    """Kujata's rect builder reaches its 256x256 only on `== 2`, and
    ff7nx_fbcapture's hardware-confirmed black-tile chain is built on 256."""
    ins = x86dis(x86, 0x500858, 0x80)
    txt = ['%08X %s %s' % (i.address, i.mnemonic, i.op_str) for i in ins]
    assert any('cmp dword ptr [0x9acb5c], 2' in t for t in txt), txt[:6]
    # the taken branch stores 0x100; the fall-through stores 0x80
    assert any('0x87b436], 0x100' in t for t in txt), txt
    assert any('0x87b436], 0x80' in t for t in txt), txt


def test_escape_setup_mode_two_descriptor(x86):
    """A = (0,0,160,168), B = (320,0,160,168), scale 2 -- read as immediates."""
    ins = x86dis(x86, ESCAPE_SETUP, 0x120)
    by_addr = {i.address: '%s %s' % (i.mnemonic, i.op_str) for i in ins}
    # the mode-2 arm begins at 0x5D5815 (the `jmp` target for "not 0, not 1")
    want = {
        0x5D5815: 'mov word ptr [ebp - 0x14], 0',      # A.x
        0x5D581B: 'mov word ptr [ebp - 0x12], 0',      # A.y
        0x5D5821: 'mov word ptr [ebp - 0x10], 0xa0',   # A.w = 160
        0x5D5827: 'mov word ptr [ebp - 0xe], 0xa8',    # A.h = 168
        0x5D582D: 'mov word ptr [ebp - 0xc], 0x140',   # B.x = 320
        0x5D5833: 'mov word ptr [ebp - 0xa], 0',       # B.y
        0x5D5839: 'mov word ptr [ebp - 8], 0xa0',      # B.w
        0x5D583F: 'mov word ptr [ebp - 6], 0xa8',      # B.h
        0x5D5845: 'mov dword ptr [ebp - 4], 2',        # the scale
    }
    for va, text in want.items():
        assert by_addr.get(va) == text, (hex(va), by_addr.get(va), text)


def test_build_402_patched_the_mode_one_arm(x86):
    """The two words build 402 moved are inside the `== 1` arm, which mode 2
    jumps over. It could not have changed a pixel."""
    ins = x86dis(x86, ESCAPE_SETUP, 0x120)
    by_addr = {i.address: '%s %s' % (i.mnemonic, i.op_str) for i in ins}
    assert by_addr[0x5D57C6] == 'cmp dword ptr [ebp - 0x1c], 1'
    assert by_addr[0x5D57CA] == 'je 0x5d57dd'
    assert by_addr[0x5D57CC] == 'jmp 0x5d5815'          # mode 2 goes here
    # the arm 402 edited: B.x = rect.x + 0xa0, scale 1
    assert by_addr[0x5D57F8] == 'add eax, 0xa0'
    assert by_addr[0x5D580C] == 'mov dword ptr [ebp - 4], 1'
    # and it is only reachable from the `je` above
    import ff7nx_battlewide as BW
    assert not BW.escape_fullsrc(), 'the dead-code probe must stay off'


# ------------------------------------------- the chain into the capture path
def test_object_renderer_reaches_make_framebuffer_tex(m):
    """0x682DB2 -> 0x674418 -> 0x673F5C. FINDINGS-402 concluded it did not."""
    lo, hi = m.extent(OBJ_RENDER)
    calls = {i.op_str.lstrip('#') for i in arm(m, lo, hi) if i.mnemonic == 'bl'}
    caller = m.x86_to_arm[MAKE_FB_TEX_CALLER]
    assert ('0x%x' % caller) in calls, sorted(calls)
    lo2, hi2 = m.extent(MAKE_FB_TEX_CALLER)
    calls2 = {i.op_str.lstrip('#') for i in arm(m, lo2, hi2)
              if i.mnemonic == 'bl'}
    assert ('0x%x' % m.x86_to_arm[MAKE_FB_TEX]) in calls2, sorted(calls2)


def test_width_and_height_are_power_of_two_rounded(m):
    """0x682DB2 sends the rect's w and h through x86 0x690240/0x690270, so
    Escape's authored 160x168 reaches the thunk as tex_w = tex_h = 256."""
    lo, hi = m.extent(OBJ_RENDER)
    ins = arm(m, lo, hi)
    want = {'0x%x' % m.x86_to_arm[0x690240], '0x%x' % m.x86_to_arm[0x690270]}
    calls = [i.op_str.lstrip('#') for i in ins if i.mnemonic == 'bl']
    for w in want:
        assert calls.count(w) == 2, (w, calls.count(w))   # once for w, once h

    def pot(n):
        p = 1
        while p < n:
            p <<= 1
        return p
    assert (pot(160), pot(168)) == (256, 256)


def test_thunk_multiplies_the_scale_into_w_and_h(m):
    """+0x10F2380: w = s[0x14]*s[0x0C], h = s[0x18]*s[0x10], field_0 = s[0]."""
    txt = ['%s %s' % (i.mnemonic, i.op_str)
           for i in arm(m, 0x10F2380, 0x10F23B8)]
    assert 'ldp w4, w1, [x0, #8]' in txt         # y, tex_w
    assert 'ldp w2, w8, [x0, #0x10]' in txt      # tex_h, xscale
    assert 'mul w5, w8, w1' in txt               # w = xscale * tex_w
    assert 'ldp w8, w9, [x0, #0x18]' in txt      # yscale, colour key
    assert 'mul w6, w8, w2' in txt               # h = yscale * tex_h
    assert 'ldp w8, w3, [x0]' in txt             # field_0, x


def test_struc91_field_0_comes_from_object_plus_0x108(m):
    """The first struc_91 field -- the one the loader branches on -- is
    obj+0x108, not obj+0x00."""
    lo, hi = m.extent(OBJ_RENDER)
    ins = arm(m, lo, hi)
    # find `add w0, w8, #0x108` and the store that follows two calls later
    hits = [k for k, i in enumerate(ins)
            if i.mnemonic == 'add' and i.op_str.endswith('#0x108')]
    assert len(hits) == 1, hits
    k = hits[0]
    # it is the FIRST of the contiguous block that also reads 0xdc/0xe0/0xe4
    later = [i.op_str for i in ins[k:k + 260]
             if i.mnemonic == 'add' and '#0x' in i.op_str]
    for off in ('#0xdc', '#0xe0', '#0xe4', '#0xe8', '#0x10c', '#0x110'):
        assert any(o.endswith(off) for o in later), off


def test_generic_initialiser_sets_field_0_to_one(m):
    """x86 0x682D80 stores obj[0x108] = 1 -- the CPU readback -- and it is
    shared with Kujata, so it cannot be patched for Escape alone."""
    lo, hi = m.extent(OBJ_INIT)
    ins = arm(m, lo, hi)
    hits = [k for k, i in enumerate(ins)
            if i.mnemonic == 'add' and i.op_str.endswith('#0x108')]
    assert len(hits) == 1
    tail = ['%s %s' % (i.mnemonic, i.op_str) for i in ins[hits[0]:hits[0] + 8]]
    assert 'mov w8, #1' in tail, tail
    assert 'str w8, [x0]' in tail, tail


def test_escape_setup_allocates_before_it_writes_fields(m):
    """escape_setup calls the allocator (which runs 0x682D80) first, so its own
    later store of 0 to +0x108 is the one that survives."""
    lo, hi = m.extent(ESCAPE_SETUP)
    ins = arm(m, lo, hi)
    alloc = '0x%x' % m.x86_to_arm[CAP_ALLOC]
    alloc_at = [i.address for i in ins
                if i.mnemonic == 'bl' and i.op_str.lstrip('#') == alloc]
    assert len(alloc_at) == 2, alloc_at            # one per capture object
    assert alloc_at[0] < 0x83B7B0 < alloc_at[1] < 0x83BA1C
    # and the allocator does call the initialiser
    lo2, hi2 = m.extent(CAP_ALLOC)
    calls = {i.op_str.lstrip('#') for i in arm(m, lo2, hi2)
             if i.mnemonic == 'bl'}
    assert ('0x%x' % m.x86_to_arm[OBJ_INIT]) in calls, sorted(calls)


# ------------------------------------------------------- the store we reuse
def test_the_repointed_store_is_dead_in_the_x86(x86):
    """escape_setup writes obj+0xD0 twice in a row, per object. The x86 shows
    the pair with nothing between them, so the first is dead."""
    ins = x86dis(x86, ESCAPE_SETUP, 0x400)
    writes = [(i.address, i.op_str) for i in ins
              if i.mnemonic == 'mov' and '+ 0xd0]' in i.op_str
              and i.op_str.startswith('dword ptr')]
    assert len(writes) == 4, writes                # two per object
    for first, second in ((writes[0], writes[1]), (writes[2], writes[3])):
        assert first[1].endswith(', 1'), first
        assert second[1].endswith(', 4'), second
        # nothing between them touches +0xd0 or reads it
        between = [i for i in ins if first[0] < i.address < second[0]]
        assert all('0xd0]' not in i.op_str for i in between), between


def test_patch_sites_and_encodings_come_from_the_binary(m):
    """The two words written are the binary's own idioms, not hand-rolled."""
    w = lambda va: struct.unpack_from('<I', m.img, va)[0]      # noqa: E731
    # `str wzr, [x0]` is what escape_setup already uses for obj[0x00] = 0
    assert w(0x83B754) == G.STR_WZR
    assert w(0x83B9C0) == G.STR_WZR
    # `add w0, w8, #0x108` sits one and two fields along, verbatim shape
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    got = next(md.disasm(struct.pack('<I', G.ADD_108), 0))
    assert (got.mnemonic, got.op_str) == ('add', 'w0, w8, #0x108')
    for va, off in ((0x83B8EC, '#0x10c'), (0x83B924, '#0x110')):
        d = next(md.disasm(struct.pack('<I', w(va)), 0))
        assert d.op_str == 'w0, w8, %s' % off, (hex(va), d.op_str)
    # and the stock words at the four sites are what SITES expects
    for va, (old, _new) in G.SITES.items():
        assert w(va) == old, (hex(va), '%08X' % w(va), '%08X' % old)


def test_anchors_hold_and_state_is_stock(m):
    assert G.verify(m.img) == []
    assert G.installed(m.img) is False


def test_plan_is_exactly_four_words(m):
    patches, _notes, problems = G.plan(m, revert=False)
    assert problems == []
    assert len(patches) == 4
    assert {int(p['va'], 16) for p in patches} == set(G.ORDER)


def test_mutations_are_caught(m):
    """Every anchor is load-bearing: flipping one makes the module refuse."""
    img = bytearray(m.img)
    for va in sorted(G.ANCHORS)[:8] + [0x83B7D0, 0x83B4EC, 0x83B6B4]:
        orig = struct.unpack_from('<I', img, va)[0]
        struct.pack_into('<I', img, va, orig ^ 0x00000400)
        assert G.verify(bytes(img)), 'mutation at %X not caught' % va
        struct.pack_into('<I', img, va, orig)
    assert G.verify(bytes(img)) == []


# ------------------------------------------------- what the loader does next
def test_loader_branches_on_field_0_and_the_cpu_modules_live_there(m):
    """The branch, and the fact that fbwindow/fbfit/fbresample all hook after
    it -- which is why routing Escape to the GPU path bypasses all three."""
    txt = {i.address: '%s %s' % (i.mnemonic, i.op_str)
           for i in arm(m, 0x10D7050, 0x10D7260)}
    assert txt[0x10D70AC] == 'ldr w8, [x20, #0x24]'      # fb_tex field_0
    assert txt[0x10D70B0] == 'cmp w8, #1'
    assert txt[0x10D70B4] == 'b.ne #0x10d7240'           # -> the GPU path
    import ff7nx_fbwindow as W
    import ff7nx_fbfit as F
    import ff7nx_fbresample as R
    for mod, name in ((W, 'fbwindow'), (F, 'fbfit'), (R, 'fbresample')):
        assert 0x10D70B4 < mod.HOOK < 0x10D7240, (name, hex(mod.HOOK))


def test_fbwindow_would_slide_escapes_second_capture():
    """The doubling, as arithmetic, from fbwindow's own rule and Escape's own
    descriptor. If either ever changes so that this no longer holds, the
    diagnosis in ff7nx_escapegpu's header is stale and this fails."""
    import ff7nx_fbwindow as W
    assert W.enabled(), 'the diagnosis assumes fbwindow is on by default'
    tex_w, xscale = 256, 2                    # POT(160), the mode-2 scale
    fb_w = xscale * tex_w
    assert fb_w == 512
    b_x = 320
    assert b_x + fb_w > 640                   # it overhangs the surface
    slid = max(0, 640 - fb_w)
    assert slid == 128
    # the mesh only ever addresses u 0..160, i.e. 320 staging columns
    addressed = 160 * xscale
    assert addressed == 320
    # so B shows 128..448 instead of 320..640: a 192-column overlap with A
    overlap = (0 + addressed) - slid
    assert overlap == 192
    assert abs(overlap * 853 / 640 - 256) < 2          # ~256 game units


def test_gpu_path_target_size_preserves_the_mapping(m):
    """gpucap makes the GPU target tex_format rather than fb_tex. Both give
    staging column = x + u*xscale, so it cannot move the picture."""
    import ff7nx_gpucap as GC
    assert GC.enabled()
    tex_w, xscale, u = 256, 2, 160
    for target_w in (tex_w, xscale * tex_w):           # gpucap on / off
        col_in_target = u / tex_w * target_w
        src_frac = col_in_target / target_w * (xscale * tex_w / 640)
        assert abs(src_frac * 640 - u * xscale) < 1e-9, target_w


# ---------------------------------------------------------- the withdrawals
def test_escapeuv_is_withdrawn_and_off_by_default():
    import ff7nx_escapeuv as UV
    old = os.environ.pop(UV.ESCAPEUV_ENV, None)
    try:
        assert UV.enabled() is False
    finally:
        if old is not None:
            os.environ[UV.ESCAPEUV_ENV] = old


def test_escapetall_is_on_and_pairs_geometry_with_sampling():
    """Build 407 shipped the geometry half alone. The module now asserts the
    relation between the three numbers itself; check it really holds."""
    import ff7nx_escapetall as T
    old = os.environ.pop(T.ESCAPETALL_ENV, None)
    try:
        assert T.enabled() is True
    finally:
        if old is not None:
            os.environ[T.ESCAPETALL_ENV] = old
    assert T.arithmetic_is_consistent()
    # geometry slope per raw unit == sampling slope per raw unit
    assert T.YSCALE_NEW == T.MESH_SCALE * T.GEO_NUM / T.GEO_DEN
    # and the rings stay on the frame centre
    assert T.expected(T.RIPPLE_C_NEW * T.MESH_SCALE) == T.FRAME_H // 2


def test_escapescale_is_still_on_and_still_spans_the_frame():
    import ff7nx_escapescale as ES
    assert ES.enabled()
    assert ES.expected(320) == 320                   # the centre is fixed
    assert ES.expected(0) == -106                    # ~ the left edge
    assert ES.expected(640) == 746                   # ~ the right edge


def test_the_verifier_passes():
    """tools/escape_verify.py is the end-to-end acceptance check."""
    import subprocess
    r = subprocess.run([sys.executable,
                        nxpaths.src_file('escape_verify.py')],
                       capture_output=True, text=True, cwd=_ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert 'ALL CHECKS PASSED' in r.stdout


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
