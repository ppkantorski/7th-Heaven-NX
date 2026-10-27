#!/usr/bin/env python3
"""tests/test_phoenixcam.py -- FFNx's "phoenix camera animation glitch".

BUILD 381. FFNx, unconditionally and at every frame rate:

    // phoenix camera animation glitch
    memset_code(ff7_externals.run_phoenix_main_loop_516297 + 0x3A5, 0x90, 49);
    memset_code(ff7_externals.run_phoenix_main_loop_516297 + 0x3F7, 0x90, 49);

Those 49 bytes each are a stray write of g_battle_camera_focal_point and
g_battle_camera_position out of the Phoenix main loop's state machine, over
the top of whatever the registered camera routine produced. At 60 FPS
`camera-hold` then replays the bogus pair for three more frames, which is the
operator's "at one point it flickers between a black screen and the image
multiple times, then the rest looks fine after".

These tests re-derive the whole thing from the shipped binaries rather than
trusting the table in ff7nx_60fps.py:

  * the FFNx pointer chain, against ff7_1.02/ff7_en
  * both 49-byte x86 spans, disassembled, confirmed to be exactly the six
    component stores and nothing else
  * every ARM64 word the group patches, against `main`
  * that nothing branches into either ARM64 range
  * that the retargeted tail branch lands on the translation of x86 0x516AB9
  * that the two addresses are the same pair ff7nx_dispatch.py's camera hold
    derived independently

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
import struct
import sys

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import ff7nx_60fps as F                                        # noqa: E402

capstone = pytest.importorskip('capstone')

PHOENIX_MAIN_LOOP = 0x516297
PHOENIX_TAIL_X86 = 0x516AB9
G_FOCAL = 0xBFB1A0
G_CAMPOS = 0xBF2158
NOP = 0xD503201F


def _find(*parts):
    here = os.path.join(_HERE, *parts)
    if os.path.exists(here):
        return here
    sib = os.path.join(os.path.dirname(os.path.dirname(_HERE)),
                       '7th_heaven_nx', *parts)
    return sib if os.path.exists(sib) else here


EXE = _find('game_data_files', 'ff7_1.02', 'ff7_en')
NSO = _find('exefs', 'main')
DUMP = _find('dump', 'exefs', 'main')

needs_exe = pytest.mark.skipif(not os.path.exists(EXE), reason='needs ff7_en')


def _nso_path():
    for p in (NSO, DUMP):
        if os.path.exists(p):
            return p
    return None


needs_nso = pytest.mark.skipif(_nso_path() is None, reason='needs main')


@pytest.fixture(scope='module')
def exe():
    import ff7nx_resolve as R
    with open(EXE, 'rb') as f:
        return R.Exe(f.read())


@pytest.fixture(scope='module')
def img():
    import nxmap
    return nxmap.Main(_nso_path())


# ------------------------------------------------------------------ the group

def test_the_group_exists_and_is_in_the_shipping_preset():
    assert 'phoenix-camera' in F.NSO_GATED
    assert 'phoenix-camera' in F.RECOMMENDED_ENABLE
    g = F.NSO_GATED['phoenix-camera']
    # 33 + 31 NOPs and one retargeted branch.
    assert len(g) == 65
    assert sum(1 for _l, _va, _s, new in g if new == NOP) == 64


def test_no_word_is_claimed_twice_and_none_collides_with_another_group():
    mine = {va for _l, va, _s, _n in F.NSO_GATED['phoenix-camera']}
    assert len(mine) == 65, 'a word is listed twice'
    for name, patches in F.NSO_GATED.items():
        if name == 'phoenix-camera':
            continue
        theirs = {p[1] for p in patches}
        assert not (mine & theirs), '%s also claims %s' % (
            name, sorted(hex(v) for v in mine & theirs))


def test_the_group_survives_the_non_fps_quarantine():
    # `run_phoenix_main_loop_516297` must not look like one of the symbols
    # _quarantine_non_fps strips, or the group would silently vanish.
    for label, _va, _s, _n in F.NSO_GATED['phoenix-camera']:
        assert not any(label.startswith(s) for s in F.NON_FPS_SYMBOLS)


# ------------------------------------------------------- FFNx's own derivation

@needs_exe
def test_the_ffnx_pointer_chain_reproduces(exe):
    # ff7_data.h, verbatim.
    main = exe.get_relative_call(0x5C0E4B, 0x2AB)
    sub = exe.get_relative_call(main, 0x1A)
    handler = exe.get_relative_call(sub, 0x91)
    assert (main, sub, handler) == (0x515101, 0x515127, 0x5151F8)
    assert exe.get_absolute_value(sub, 0xB2) == PHOENIX_MAIN_LOOP
    assert exe.get_absolute_value(PHOENIX_MAIN_LOOP, 0x310) == 0x518AFF
    assert exe.get_absolute_value(handler, 0x5) == 0x515238


@needs_exe
@pytest.mark.parametrize('off,target,name', [
    (0x3A5, G_FOCAL, 'g_battle_camera_focal_point'),
    (0x3F7, G_CAMPOS, 'g_battle_camera_position'),
])
def test_each_49_byte_span_is_exactly_three_component_stores(exe, off, target,
                                                             name):
    """FFNx NOPs 49 bytes. Prove those 49 bytes are the write and only it."""
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    base = PHOENIX_MAIN_LOOP + off
    body = exe.read(base, 49)
    ins = list(md.disasm(body, base))
    # It must decode to a whole number of instructions -- a memset that ends
    # mid-instruction would corrupt the following code.
    assert sum(i.size for i in ins) == 49, [i.mnemonic for i in ins]
    assert len(ins) == 9, [(i.mnemonic, i.op_str) for i in ins]
    stores = [i for i in ins if i.op_str.startswith('word ptr [0x')]
    assert len(stores) == 3
    for n, i in enumerate(stores):
        assert i.mnemonic == 'mov'
        assert i.op_str.startswith('word ptr [0x%x]' % (target + 2 * n)), \
            '%s component %d is %s' % (name, n, i.op_str)
    # The other six are the reloads of the source pointer -- no calls, no
    # branches, nothing with a side effect outside the three stores.
    for i in ins:
        assert i.mnemonic == 'mov', (i.mnemonic, i.op_str)


@needs_exe
def test_the_byte_after_the_second_span_is_the_jmp_ffnx_keeps(exe):
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    at = PHOENIX_MAIN_LOOP + 0x3F7 + 49
    i = next(md.disasm(exe.read(at, 8), at))
    assert i.mnemonic == 'jmp'
    assert int(i.op_str, 16) == PHOENIX_TAIL_X86


# ---------------------------------------------------------- the ARM64 port

@needs_nso
def test_every_stock_word_in_the_table_matches_the_image(img):
    for label, va, stock, _new in F.NSO_GATED['phoenix-camera']:
        got = struct.unpack_from('<I', img.img, va)[0]
        assert got == stock, '%s: +0x%X is %08X, table says %08X' % (
            label, va, got, stock)


@needs_nso
def test_both_ranges_lie_inside_the_translated_phoenix_main_loop(img):
    lo, hi = img.extent(PHOENIX_MAIN_LOOP)
    for _l, va, _s, _n in F.NSO_GATED['phoenix-camera']:
        assert lo <= va < hi, hex(va)


@needs_nso
def test_the_focal_block_reaches_0xBFB1A0_and_the_campos_block_0xBF2158(img):
    """The literal bases are what identify the two blocks beyond doubt."""
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
    lo, _hi = img.extent(PHOENIX_MAIN_LOOP)
    prologue = {}
    for x in md.disasm(img.img[lo:lo + 0x50], lo):
        if x.mnemonic == 'mov' and '#' in x.op_str:
            d, s = [t.strip() for t in x.op_str.split(',', 1)]
            prologue[d] = int(s[1:], 16)
        elif x.mnemonic == 'movk':
            d, imm, _sh = [t.strip() for t in x.op_str.split(',')]
            prologue[d] = (prologue.get(d, 0) & 0xFFFF) | (int(imm[1:], 16) << 16)
    assert prologue.get('w20') == G_CAMPOS
    assert prologue.get('w23') == 0xBFB718        # focal point sits below it

    def reached(words, want):
        got = set()
        for va, _stock in words:
            x = next(md.disasm(img.img[va:va + 4], va))
            if x.mnemonic == 'mov' and x.op_str == 'w0, w20':
                got.add(prologue['w20'])
            elif x.mnemonic == 'add' and x.op_str.startswith('w0, w20, #'):
                got.add(prologue['w20'] + int(x.op_str.split('#')[1], 16))
            elif x.mnemonic == 'sub' and x.op_str.startswith('w0, w23, #'):
                got.add(prologue['w23'] - int(x.op_str.split('#')[1], 16))
        assert got == want, sorted(hex(v) for v in got)

    reached(F.PHOENIX_FOCAL_WORDS, {G_FOCAL, G_FOCAL + 2, G_FOCAL + 4})
    reached(F.PHOENIX_CAMPOS_WORDS, {G_CAMPOS, G_CAMPOS + 2, G_CAMPOS + 4})
    # The third campos component ADDRESS is computed in the block, but the
    # store itself is in the shared tail the block branches to -- which is
    # the whole reason the branch is retargeted instead of NOP'd.
    last = F.PHOENIX_CAMPOS_WORDS[-1][0]
    x = next(md.disasm(img.img[last:last + 4], last))
    assert (x.mnemonic, x.op_str) == ('add', 'w0, w20, #4')
    assert last + 4 == F.PHOENIX_TAIL_BRANCH[0]


@needs_nso
def test_nothing_in_text_branches_into_either_range(img):
    """A NOP'd range with a live entry point would execute garbage."""
    ranges = {va for _l, va, _s, new in F.NSO_GATED['phoenix-camera']
              if new == NOP}
    text = img.raw[0]
    hits = []
    for off in range(0, len(text) - 4, 4):
        w = struct.unpack_from('<I', text, off)[0]
        if (w & 0xFC000000) not in (0x14000000, 0x94000000):
            continue
        imm = w & 0x3FFFFFF
        if imm & (1 << 25):
            imm -= (1 << 26)
        if off + imm * 4 in ranges:
            hits.append(hex(off))
    assert hits == [], hits

    # Conditional / compare-and-branch forms too, over the function itself.
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
    lo, hi = img.extent(PHOENIX_MAIN_LOOP)
    for x in md.disasm(img.img[lo:hi], lo):
        if not x.mnemonic.startswith(('b.', 'cb', 'tb')):
            continue
        for tok in x.op_str.replace(',', ' ').split():
            if tok.startswith('#0x') and int(tok[1:], 16) in ranges:
                assert x.address in ranges, '%X branches into a NOP range' % x.address


@needs_nso
def test_the_retargeted_branch_lands_on_the_translation_of_0x516AB9(img):
    site, stock, new = F.PHOENIX_TAIL_BRANCH

    def target(word, pc):
        imm = word & 0x3FFFFFF
        if imm & (1 << 25):
            imm -= (1 << 26)
        return pc + imm * 4

    assert struct.unpack_from('<I', img.img, site)[0] == stock
    assert (stock & 0xFC000000) == 0x14000000
    assert (new & 0xFC000000) == 0x14000000

    # Stock goes to the shared tail: bl TRANSLATE; strh w19,[x0]; b <tail>.
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
    shared = target(stock, site)
    tail = list(md.disasm(img.img[shared:shared + 12], shared))
    assert tail[0].mnemonic == 'bl'
    assert tail[1].mnemonic == 'strh' and tail[1].op_str == 'w19, [x0]'
    assert tail[2].mnemonic == 'b'
    # ...and the new target is exactly where that tail was going anyway.
    assert target(new, site) == int(tail[2].op_str[1:], 16)

    # That destination is the translation of `mov edx,[ebp-8]; mov ax,[edx+2];
    # add ax,1; mov [ecx+2],ax` at x86 0x516AB9.
    dest = target(new, site)
    body = [(x.mnemonic, x.op_str)
            for x in md.disasm(img.img[dest:dest + 0x2C], dest)]
    assert ('add', 'w8, w8, #1') in body
    assert ('ldr', 'w8, [x22, #0x14]') == body[0]


def test_the_globals_are_the_pair_the_camera_hold_holds():
    """Two independent derivations of the same two addresses must agree.

    `camera-hold` got them from set_battle_camera_sub_5C22BD +0x17/+0x5E in
    ff7_en; this group got them from the Phoenix main loop's own ARM64
    prologue literals. If those ever disagree, one of the two is wrong.
    """
    assert F.CAM_POS_GUEST == G_CAMPOS
    assert F.CAM_FOC_GUEST == G_FOCAL


# ------------------------------------------------------- the shipping default

@pytest.mark.parametrize('env', ['SEVENTH_NX_FX_MODELINTERP',
                                 'SEVENTH_NX_FX_CAMERAHOLD'])
def test_the_summon_fixes_are_on_when_nothing_is_set(monkeypatch, env):
    """BUILD 381: both are defaults now, not opt-ins."""
    fn = (F.modelinterp_enabled if 'MODEL' in env else F.camerahold_enabled)
    monkeypatch.delenv(env, raising=False)
    assert fn() is True, '%s must default to ON' % env
    monkeypatch.setenv(env, '')
    assert fn() is True, 'an empty %s must not read as off' % env
    for on in ('1', 'true', 'yes', 'on'):
        monkeypatch.setenv(env, on)
        assert fn() is True, on
    for off in ('0', 'off', 'no', 'false', 'stock', 'none', 'OFF', ' 0 '):
        monkeypatch.setenv(env, off)
        assert fn() is False, off


def test_camerahold_all_is_still_reachable_and_is_not_off(monkeypatch):
    monkeypatch.setenv('SEVENTH_NX_FX_CAMERAHOLD', 'all')
    assert F._fx_switch('SEVENTH_NX_FX_CAMERAHOLD') == 'all'
    assert F.camerahold_enabled() is True
    monkeypatch.delenv('SEVENTH_NX_FX_CAMERAHOLD')
    assert F._fx_switch('SEVENTH_NX_FX_CAMERAHOLD') == 'on'
