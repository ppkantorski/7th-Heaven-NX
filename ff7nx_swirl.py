#!/usr/bin/env python3
r"""
ff7nx_swirl.py -- the battle-entry swirl, widened to 16:9.

THE SYMPTOM
===========
Entering a battle, the swirl transition covers only the middle 4:3 of the
frame.  The field behind it is wide; the swirl is not.

THE MECHANISM
=============
The swirl blits a grid of framebuffer tiles.  Three globals set it up, and
`swirl_enter_40164E` writes them in three mutually exclusive branches:

    [0x9A04D8] x       0    160    0
    [0x9A04D4] y       0    120    0
    [0x9A04DC] size   32     32   64

Downstream, `swirl_enter_40164E` reads x and y back to place the grid,
`swirl_enter_sub_401810` reads [0x99F330] (the source width) and halves it,
and `swirl_loop_sub_4026D4` adds x to a running coordinate.  All of that is
sized for 640 units, so the swirl lands in the central 75% -- exactly 4:3.

FFNx's fix (`src/ff7/widescreen.cpp`, and the file-scope constants in
`src/widescreen.h`):

    patch_code_dword(swirl_loop_sub_4026D4  + 0x335, &wide_viewport_x);      -107
    patch_code_dword(swirl_enter_sub_401810 + 0x021, &wide_viewport_width);   854
    patch_code_int  (swirl_enter_40164E     + 0x0E8, 85);                 size 64 -> 85
    patch_code_dword(swirl_enter_40164E     + 0x0EE, &swirl_off_x);           106
    patch_code_dword(swirl_enter_40164E     + 0x0FB, &swirl_off_y);            64
    patch_code_dword(swirl_enter_40164E     + 0x112, &swirl_off_x);           106
    patch_code_dword(swirl_enter_40164E     + 0x11F, &swirl_off_y);            64

85/64 = 1.328 and 854/640 = 1.334: the tile size scales with the width, which
is why HANDOFF-90 §4.4 calls the size the difference between "wider" and
"wider but torn".  It is not optional.

TWO SITES THAT ARE NOT ONE WORD, AND ARE NOT WHERE THEY LOOK
============================================================

1.  THE SIZE (`+0x0E8`).  The x86 has three stores of the size, one per
    branch, and FFNx patches only the third (64).  In this port the
    recompiler HOISTED THE STORE: all three branches converge on a single
    `str w22, [x0]` at +0xED20, and the branch that differs sets w22 out of
    line, in a cold block at the very END of the function:

        +0x0ECB0  mov w22, #0x20     branch A  (160, 120, 32)
        +0x0ECCC  mov w22, #0x20     branch B  (0,   0,   32)
        +0x0F04C  mov w22, #0x40     branch C  (0,   0,   64)   <- FFNx's site
        +0x0F05C  b   #0xecdc          ... rejoining the shared tail

    So the patch is at +0xF04C, not at the store.  Patching the store would
    change all three branches, including the two FFNx leaves alone.  A scan
    that walked forward from the store and took the first constant it found
    reports the value 32 and points at branch B -- confidently wrong.

    This is the third time on this bug that the recompiler's layout has moved
    a site away from where the x86 puts it (hoisted constants, tail-merged
    blocks, and now an out-of-line cold branch).

2.  THE WIDTH (`+0x021` in swirl_enter_sub_401810).  A 32-bit signed load,
    which the recompiler split into two halves plus a sign recompose:

        +0xF4D8  ldrh w8, [x0]              <- the low half
        +0xF4DC  ldrh w9, [x0, #2]          <- the high half
        +0xF4E0  bfi  w8, w9, #16, #16
        +0xF4E4  sbfx w9, w9, #0xf, #1      sign
        +0xF4EC  sub  w8, w8, w9
        +0xF4F8  asr  w8, w8, #1            signed /2

    Writing only the low half leaves w9 holding the RUNTIME high half of
    [0x99F330].  That global is written 0x140/0x280 elsewhere, so its high
    half is zero and the result would come out right -- while silently
    depending on a value this patch does not control.  Both halves are
    written: 854 and 0.

WHAT THIS MODULE DOES NOT TOUCH
===============================
`swirl_enter_40164E + 0x106` also loads [0x99F330], and FFNx leaves it alone.
`swirl_loop_sub_4026D4 + 0x364` loads the y offset, and FFNx leaves that alone
too -- only the horizontal axis widens.  Both are excluded here, which is why
this module identifies sites individually instead of patching every load of a
global: FFNx's list is selective and copying it wholesale is wrong.

No stored global is written, so FINDINGS-97's uncrop leg -- which fires on the
literal battle rect (0, 0, 640, 332) -- is untouched.
"""
import argparse
import os
import re
import shutil
import struct
import sys
import tempfile
from pathlib import Path

try:
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM, CS_OP_REG, CS_OP_IMM
    from capstone.arm64 import ARM64_OP_MEM
except ImportError:                                          # pragma: no cover
    sys.exit('need capstone:  pip install capstone --break-system-packages')

import nxmap
import ff7nx_guestref as gr

FFNX_HEADER = 'repos/FFNx-master/src/widescreen.h'
FFNX_WS_CPP = 'repos/FFNx-master/src/ff7/widescreen.cpp'
EXE = 'ff7_en_switch'

# The one site the recompiler realised as two halfword loads instead of one
# word load.  See the module docstring, section 2.
SPLIT_LOAD_FN = 'swirl_enter_sub_401810'

SWIRL_X = 0x9A04D8          # framebuffer offset x
SWIRL_Y = 0x9A04D4          # framebuffer offset y
SWIRL_SIZE = 0x9A04DC       # tile size: 32 / 32 / 64
SRC_WIDTH = 0x99F330        # the source width swirl_enter_sub_401810 halves

# FFNx values.  Parsed from its tree at verify time; these are the fallback.
V = {'swirl_off_x': 106, 'swirl_off_y': 64,
     'wide_viewport_x': -107, 'wide_viewport_width': 854,
     'swirl_size': 85}

# (x86 fn, FFNx offset, guest, value key, STOCK LOAD WIDTH).
#
# The width is recorded, not inferred, and that is not belt-and-braces.  Once
# a site has been patched it reads `movz wN, #106`, and a MOVZ of a positive
# value is byte-identical whether it replaced a 32-bit `ldr` or a 16-bit
# `ldrh`.  So the patched image does not know what to put back.  Inferring it
# from the sign of the value -- which an earlier version did -- restored
# +0xED90 as `ldrh` when stock is `ldr`, and --revert came back one word short
# of the original.  Every apply/revert cycle then left that word wrong while
# reporting success.
#
# It matches the x86 operand size (`mov ax, word ptr` = 2, `add eax, dword
# ptr` = 4), and `verify` asserts all three agree on a stock image: the table,
# the ARM load, and the x86.
LOADS = [
    ('swirl_loop_sub_4026D4',  0x4026D4, 0x335, SWIRL_X,   'wide_viewport_x',     4),
    ('swirl_enter_sub_401810', 0x401810, 0x021, SRC_WIDTH, 'wide_viewport_width', 2),
    ('swirl_enter_40164E',     0x40164E, 0x0EE, SWIRL_X,   'swirl_off_x',         2),
    ('swirl_enter_40164E',     0x40164E, 0x0FB, SWIRL_Y,   'swirl_off_y',         2),
    ('swirl_enter_40164E',     0x40164E, 0x112, SWIRL_X,   'swirl_off_x',         4),
    ('swirl_enter_40164E',     0x40164E, 0x11F, SWIRL_Y,   'swirl_off_y',         2),
]

# FFNx sites this module deliberately does NOT take, and why.
LEFT_ALONE = {
    (0x40164E, 0x106): 'swirl_enter_40164E loads the source width here too, '
                       'and FFNx leaves it alone',
    (0x4026D4, 0x364): 'swirl_loop reads the Y offset here; only the '
                       'horizontal axis widens',
}


def enc_movz(rd, imm16):
    assert 0 <= imm16 <= 0xFFFF, imm16
    return (0x52800000 | (imm16 << 5) | rd) & 0xFFFFFFFF


def enc_movn(rd, imm16):
    assert 0 <= imm16 <= 0xFFFF, imm16
    return (0x12800000 | (imm16 << 5) | rd) & 0xFFFFFFFF


def encode(reg, width, value):
    rd = int(reg[1:])
    if width == 2 or value >= 0:
        return enc_movz(rd, value & 0xFFFF)
    return enc_movn(rd, ~value)


def stock_load(reg, width):
    return ((0xB9400000 if width == 4 else 0x79400000) | int(reg[1:])) & 0xFFFFFFFF


def stock_ldrh_off2(reg):
    """`ldrh wRt, [x0, #2]` -- imm12 = 1 for a halfword scale."""
    return (0x79400000 | (1 << 10) | int(reg[1:])) & 0xFFFFFFFF


# The tile size in branch C, as the recompiler actually encoded it:
#
#     +0x0F04C   321A03F6   orr w22, wzr, #0x40
#
# NOT a MOVZ.  The recompiler used the LOGICAL IMMEDIATE form, and capstone
# prints both forms as `mov`, so nothing in the disassembly says which it is.
# That matters for --revert and only for --revert: 85 is not encodable as an
# AArch64 logical immediate (0x00000055 is not a replicated bit pattern), so
# --apply must change the instruction FORM to MOVZ, and the original cannot be
# rebuilt from the register number the way the loads can.  Reconstructing it
# as `movz w22, #0x40` produces a functionally identical instruction with
# different bytes, and --revert stops being byte-exact -- which this project
# treats as making the next test result unattributable.
#
# So it is recorded, and `verify` asserts it against a stock image rather than
# trusting it.  A different build that encodes this differently fails loudly
# instead of being silently half-reverted.
SIZE_STOCK_WORD = 0x321A03F6
SIZE_STOCK_REG = 'w22'
SIZE_STOCK_VALUE = 0x40


def _md():
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    md.detail = True
    return md


def ffnx_values(root='.'):
    """FFNx's constants, read from its tree.  None if the tree is absent."""
    h = Path(root) / FFNX_HEADER
    c = Path(root) / FFNX_WS_CPP
    if not h.exists() or not c.exists():
        return None
    out = {}
    ht, ct = h.read_text(), c.read_text()
    for key, nm, txt in (('wide_viewport_x', 'wide_viewport_x', ht),
                         ('wide_viewport_width', 'wide_viewport_width', ht),
                         ('swirl_off_x', 'swirl_framebuffer_offset_x_widescreen_fix', ct),
                         ('swirl_off_y', 'swirl_framebuffer_offset_y_widescreen_fix', ct)):
        mo = re.search(r'^\s*int\s+%s\s*=\s*(-?\d+)\s*;' % nm, txt, re.M)
        if mo:
            out[key] = int(mo.group(1))
    mo = re.search(r'patch_code_int\s*\(\s*ff7_externals\.swirl_enter_40164E\s*\+\s*0x[eE]8\s*,\s*(\d+)\s*\)', ct)
    if mo:
        out['swirl_size'] = int(mo.group(1))
    return out or None


def x0_dead(text, addr, md, window=10, allow_off2=False):
    """
    True if x0 is not READ before it is next written.

    `allow_off2` tolerates the second half of a split 32-bit load, because
    that instruction is itself one of this module's sites and is patched in
    the same pass.
    """
    first = True
    for i in md.disasm(text[addr + 4: addr + 4 + window * 4], addr + 4):
        r, w = i.regs_access()
        if any(i.reg_name(x) in ('x0', 'w0') for x in r):
            mem = next((o for o in i.operands if o.type == ARM64_OP_MEM), None)
            if (allow_off2 and first and mem is not None
                    and i.mnemonic == 'ldrh' and mem.mem.disp == 2):
                first = False
                continue
            return False
        if any(i.reg_name(x) in ('x0', 'w0') for x in w):
            return True
        first = False
    return True


class Site(object):
    __slots__ = ('kind', 'name', 'addr', 'reg', 'width', 'mnemonic', 'op_str',
                 'word', 'value', 'patched', 'note')

    def __init__(self, kind, name, ins, reg, width, word, value, patched, note=''):
        self.kind, self.name, self.note = kind, name, note
        self.addr, self.reg, self.width = ins.address, reg, width
        self.mnemonic, self.op_str = ins.mnemonic, ins.op_str
        self.word, self.value, self.patched = word, value, patched


def sites(m, values=None, md=None, window=10):
    """
    Every patchable word, findable in either state.

    Load sites are anchored on the call to the address translator, which
    survives patching; the size site is anchored structurally (see below).
    """
    values = values or dict(V)
    md = md or _md()
    out, problems = [], []

    # ---- the six load redirects -----------------------------------------
    per_fn = {}
    for name, fn, off, guest, key, width in LOADS:
        per_fn.setdefault((name, fn), []).append((off, guest, key, width))

    for (name, fn), want in sorted(per_fn.items(), key=lambda t: t[0][1]):
        if fn not in m.x86_to_arm:
            problems.append('%s: 0x%X is not a recompilation map key' % (name, fn))
            continue
        a, b = m.extent(fn)
        _, stats = gr.scan(m.text, a, b, md)
        by_guest = {}
        for off, guest, key, width in sorted(want):
            by_guest.setdefault(guest, []).append((key, width))

        seen = {}
        for tap, guest in stats.get('taps', []):
            if guest not in by_guest:
                continue
            v = values[by_guest[guest][0][0]]
            for i in md.disasm(m.text[tap + 4: tap + 4 + window * 4], tap + 4):
                mem = next((o for o in i.operands if o.type == ARM64_OP_MEM), None)
                word = struct.unpack('<I', m.text[i.address:i.address + 4])[0]
                if (i.mnemonic in ('ldr', 'ldrh') and mem is not None
                        and i.reg_name(mem.mem.base) in ('x0', 'w0')
                        and mem.mem.disp == 0):
                    seen.setdefault(guest, []).append(
                        Site('load', name, i, i.reg_name(i.operands[0].reg),
                             4 if i.mnemonic == 'ldr' else 2, word, v, False))
                    break
                if (i.mnemonic in ('movz', 'movn', 'mov')
                        and len(i.operands) == 2
                        and i.operands[1].type == CS_OP_IMM
                        and i.operands[1].imm in (v, v & 0xFFFF)):
                    n = len(seen.get(guest, []))
                    w = (by_guest[guest][n][1]
                         if n < len(by_guest[guest]) else 2)
                    seen.setdefault(guest, []).append(
                        Site('load', name, i, i.reg_name(i.operands[0].reg),
                             w, word, v, True))
                    break
                if mem is not None and i.reg_name(mem.mem.base) in ('x0', 'w0'):
                    break
        for guest, keys in by_guest.items():
            got = seen.get(guest, [])
            for n, s_ in enumerate(got):
                if n < len(keys) and not s_.patched and s_.width != keys[n][1]:
                    problems.append(
                        '%s +0x%X: stock load is %d bytes, the table says %d'
                        % (name, s_.addr, s_.width, keys[n][1]))
            # swirl_enter_40164E loads the source width twice and FFNx takes
            # neither of the extra ones; only count what we asked for.
            if len(got) != len(keys):
                problems.append(
                    '%s: %d site(s) for guest 0x%X, FFNx names %d'
                    % (name, len(got), guest, len(keys)))
            out.extend(got[:len(keys)])

    # ---- the high half of the split 32-bit width load -------------------
    lo = next((s for s in out if s.name == 'swirl_enter_sub_401810'), None)
    if lo is not None:
        for i in md.disasm(m.text[lo.addr + 4: lo.addr + 8], lo.addr + 4):
            mem = next((o for o in i.operands if o.type == ARM64_OP_MEM), None)
            word = struct.unpack('<I', m.text[i.address:i.address + 4])[0]
            if i.mnemonic == 'ldrh' and mem is not None and mem.mem.disp == 2:
                out.append(Site('hi', 'swirl_enter_sub_401810', i,
                                i.reg_name(i.operands[0].reg), 2, word, 0,
                                False, 'high half of the split 32-bit load'))
            elif (i.mnemonic in ('movz', 'mov') and len(i.operands) == 2
                  and i.operands[1].type == CS_OP_IMM
                  and i.operands[1].imm == 0):
                out.append(Site('hi', 'swirl_enter_sub_401810', i,
                                i.reg_name(i.operands[0].reg), 2, word, 0,
                                True, 'high half of the split 32-bit load'))
            else:
                problems.append('swirl_enter_sub_401810 +0x%X: expected the '
                                'high half at [x0,#2], found %s %s'
                                % (i.address, i.mnemonic, i.op_str))

    # ---- the tile size, in the out-of-line branch ------------------------
    a, b = m.extent(0x40164E)
    _, stats = gr.scan(m.text, a, b, md)
    size_regs = set()
    for x in gr.scan(m.text, a, b, md)[0]:
        if x.guest == SWIRL_SIZE and not x.is_load:
            size_regs.add(x.reg)
    if not size_regs:
        problems.append('swirl_enter_40164E: no store to the tile size found')
    else:
        # Addresses already claimed as load sites are NOT size candidates.
        #
        # This exclusion is load-bearing and its absence only shows up on a
        # PATCHED module, which is the worst place for a bug to hide.  The
        # swirl_off_y redirect at +0xEDB4 targets the same register w22 that
        # carries the tile size, and its value is 64 -- so once applied it
        # reads `mov w22, #0x40`, exactly like stock branch C.  Discovery then
        # saw two candidates, refused, and --revert silently left the module
        # half-patched.  Verify passed the whole time, because it only ever
        # ran against stock.
        claimed = {s.addr for s in out}
        want = (0x40, values['swirl_size'])
        hits = [i for i in md.disasm(m.text[a:b], a)
                if i.address not in claimed
                and i.mnemonic in ('mov', 'movz') and len(i.operands) == 2
                and i.operands[0].type == CS_OP_REG
                and i.reg_name(i.operands[0].reg) in size_regs
                and i.operands[1].type == CS_OP_IMM
                and i.operands[1].imm in want]
        if len(hits) != 1:
            problems.append('swirl_enter_40164E: %d candidate(s) set the tile '
                            'size register to 64 or %d, want exactly 1'
                            % (len(hits), values['swirl_size']))
        else:
            i = hits[0]
            word = struct.unpack('<I', m.text[i.address:i.address + 4])[0]
            out.append(Site('size', 'swirl_enter_40164E', i,
                            i.reg_name(i.operands[0].reg), 2, word,
                            values['swirl_size'],
                            i.operands[1].imm == values['swirl_size'],
                            'out-of-line branch C; the store is shared'))
    out.sort(key=lambda s: s.addr)
    return out, problems


def plan(m, revert=False, md=None, values=None):
    values = values or (ffnx_values() or V)
    md = md or _md()
    found, problems = sites(m, values, md)
    patches, notes = [], []
    for s in found:
        if s.kind == 'load':
            old = stock_load(s.reg, s.width) if s.patched else s.word
            new = encode(s.reg, s.width, s.value)
            if not x0_dead(m.text, s.addr, md,
                           allow_off2=(s.name == 'swirl_enter_sub_401810')):
                problems.append('%s +0x%X: x0 is read again after the site'
                                % (s.name, s.addr))
                continue
        elif s.kind == 'hi':
            old = stock_ldrh_off2(s.reg) if s.patched else s.word
            new = enc_movz(int(s.reg[1:]), 0)
        else:                                    # the tile size immediate
            if s.patched:
                if s.reg != SIZE_STOCK_REG:
                    problems.append('size site is in %s, not %s: the recorded '
                                    'stock word does not apply'
                                    % (s.reg, SIZE_STOCK_REG))
                    continue
                old = SIZE_STOCK_WORD
            else:
                old = s.word
            new = enc_movz(int(s.reg[1:]), s.value)
        cur = struct.unpack('<I', m.text[s.addr:s.addr + 4])[0]
        frm, to = (new, old) if revert else (old, new)
        if cur == to:
            continue
        if cur != frm:
            problems.append('%s +0x%X: word is 0x%08X, expected 0x%08X'
                            % (s.name, s.addr, cur, frm))
            continue
        patches.append({'name': '%s %s -> %d' % (s.name, s.kind, s.value),
                        'va': hex(s.addr),
                        'expect': struct.pack('<I', frm).hex(),
                        'set': struct.pack('<I', to).hex()})
        notes.append('    %-24s %-5s +0x%07X  %-18s -> %s'
                     % (s.name, s.kind, s.addr,
                        '%s %s' % (s.mnemonic, s.op_str),
                        ('movz %s, #%d' % (s.reg, s.value)) if not revert
                        else 'the original'))
    return patches, notes, problems


REFUSAL = r"""
  ff7nx_swirl.py WILL NOT APPLY.  FFNx's constants do not transfer to this
  port, and the hardware result says exactly why.

  REPORTED, unpatched:  the swirl takes a freeze frame of the 16:9 picture and
                        SQUEEZES it into a 4:3 window, then swirls.
  REPORTED, patched:    the picture is SHIFTED LEFT and ZOOMED IN.

  Both halves are accounted for, word for word:

    +0x0F04C  tile size 64 -> 85    the ZOOM.  [0x9A04DC] is a single square
                                    tile size used on BOTH axes.  Scaling it
                                    1.33x enlarges the grid uniformly without
                                    touching the texture mapping, so the same
                                    picture is spread over a larger area.
    +0x13C5C  swirl_loop += -107    the LEFT SHIFT.  It biases the vertex x
                                    directly.
    +0x0ED30 / +0x0ED90  x -> 106   push the grid corner right; in FFNx these
    +0x0ED4C / +0x0EDB4  y -> 64    compensate for an inset into ITS OWN wide
                                    framebuffer, which this port does not have.

  WHY IT CANNOT WORK AS A CONSTANT SET.  FFNx renders the swirl source into an
  854-wide framebuffer, so a uniformly larger tile grid IS the right widening
  there.  In this port the source is already the full 16:9 render target, and
  it is being mapped onto a grid whose DESTINATION is 640 game units wide --
  which the vertex shader then multiplies by WS_SCALE = 0.75, landing it in
  the central 75%.  That is the squeeze.

  So what this port needs is a HORIZONTAL-ONLY widening of the destination
  grid, and FFNx's constants cannot express that: the tile size is one number
  for both axes, and `swirl_framebuffer_offset_y = 64` explicitly moves the
  picture vertically, which here would be a new bug rather than a fix.

  THE SHAPE THAT DOES WORK IN THIS PORT is the one ff7nx_letterbox already
  uses for the field fade quad: leave the matrix alone and widen the quad's
  own rect, (0, 16, 640, 448) -> (-107, 0, 854, 480).  For the swirl that
  means scaling the vertex x about the centre by 1/WS_SCALE = 1.3333 at the
  store in swirl_loop_sub_4026D4, and leaving y untouched.  That is a cave,
  not a constant swap, and it is the next thing to write.

  --revert works and is byte-exact.  Run it.
"""


def apply(main, revert=False, log=print) -> int:
    import nso_patcher
    if not revert:
        log(REFUSAL)
        return 1
    main = Path(main)
    m = nxmap.Main(str(main))
    patches, notes, problems = plan(m, revert)
    if problems:
        for p in problems:
            log('  ! ' + p)
        log('  refusing to write.')
        return 1
    log('  battle-entry swirl -> 16:9:')
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(nso, {'name': 'ff7nx_swirl',
                                             'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.swirl-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    log('  %d word(s) written' % len(patches))
    return 0


def show(main, log=print):
    m = nxmap.Main(str(main))
    values = ffnx_values() or V
    found, problems = sites(m, values, _md())
    log('  battle-entry swirl (%d word(s)):' % len(found))
    for s in found:
        log('    +0x%07X  %-24s %-5s %-18s %s%s'
            % (s.addr, s.name, s.kind, '%s %s' % (s.mnemonic, s.op_str),
               'PATCHED (%d)' % s.value if s.patched else 'stock',
               '   ' + s.note if s.note else ''))
    for p in problems:
        log('    ! ' + p)
    log('  left alone on purpose:')
    for (fn, off), why in sorted(LEFT_ALONE.items()):
        log('    0x%06X+0x%03X  %s' % (fn, off, why))


def verify(main=None, log=print) -> int:
    main = Path(main or 'exefs/main')
    m = nxmap.Main(str(main))
    md = _md()
    ok = fail = 0

    def chk(c, what):
        nonlocal ok, fail
        if c:
            ok += 1
            log('    ok    ' + what)
        else:
            fail += 1
            log('    FAIL  ' + what)

    log('  constants, read out of the FFNx tree:')
    fv = ffnx_values()
    if fv is None:
        log('    ----  FFNx tree absent; using built-in defaults')
        fv = dict(V)
    else:
        for k in sorted(V):
            if k in fv:
                chk(fv[k] == V[k], '%s is %d in FFNx' % (k, fv[k]))
    values = dict(V)
    values.update(fv)

    log('  the size ratio matches the width ratio:')
    chk(abs(values['swirl_size'] / 64.0
            - values['wide_viewport_width'] / 640.0) < 0.02,
        'size %d/64 = %.3f vs width %d/640 = %.3f'
        % (values['swirl_size'], values['swirl_size'] / 64.0,
           values['wide_viewport_width'], values['wide_viewport_width'] / 640.0))

    log('  every FFNx offset names the guest this module assumes:')
    if not Path(EXE).exists():
        log('    ----  %s absent; cannot cross-check' % EXE)
    else:
        from probe_overlay import Exe, x86_site
        from capstone import Cs as _Cs, CS_ARCH_X86, CS_MODE_32
        exe = Exe(EXE)
        mdx = _Cs(CS_ARCH_X86, CS_MODE_32)
        mdx.detail = True
        for name, fn, off, guest, key, width in LOADS:
            s = x86_site(exe, mdx, fn, off, 4)
            chk(s and (s['field'] & 0xFFFFFFFF) == guest,
                '%s +0x%03X reads [0x%X]' % (name, off, guest))
            # A 4-byte x86 access may legitimately come out as a 2-byte ARM
            # load -- but ONLY if the recompiler split it, in which case this
            # module must also own the high half.  Asserting flat equality
            # here flags the one site that is correct; asserting nothing would
            # miss a genuinely truncated load.  So the invariant is: the
            # widths agree, or the narrowing is a split we cover.
            xw = 4 if (s and 'dword ptr' in s['ins']) else 2
            if xw == width:
                chk(True, '%s +0x%03X is %d bytes in both the x86 and the '
                          'table' % (name, off, xw))
            else:
                chk(xw == 4 and width == 2 and name == SPLIT_LOAD_FN,
                    '%s +0x%03X is %d bytes in the x86 and %d in the ARM -- '
                    'accounted for by the split load' % (name, off, xw, width))
        s = x86_site(exe, mdx, 0x40164E, 0x0E8, 4)
        chk(s and s['field'] == 64,
            'swirl_enter_40164E +0x0E8 holds the immediate 64 (found %s)'
            % (s['field'] if s else None))

    log('  the sites, re-derived from the image:')
    found, problems = sites(m, values, md)
    chk(not problems, 'discovery is clean (%s)' % (problems[0] if problems else 'ok'))
    chk(len(found) == len(LOADS) + 2,
        '%d site(s) found, want %d (6 loads + the split high half + the size)'
        % (len(found), len(LOADS) + 2))
    for s in found:
        if s.kind == 'load':
            chk(x0_dead(m.text, s.addr, md,
                        allow_off2=(s.name == 'swirl_enter_sub_401810')),
                '%s +0x%X: x0 is dead after the site' % (s.name, s.addr))
            if not s.patched:
                chk(stock_load(s.reg, s.width) == s.word,
                    '%s +0x%X: --revert restores the real word' % (s.name, s.addr))

    log('  the size site is the out-of-line branch, not the shared store:')
    sz = [s for s in found if s.kind == 'size']
    chk(len(sz) == 1, 'exactly one size site')
    if sz:
        a, b = m.extent(0x40164E)
        st = [x for x in gr.scan(m.text, a, b, md)[0]
              if x.guest == SWIRL_SIZE and not x.is_load]
        chk(len(st) == 1, 'the tile-size store is shared (%d store site)' % len(st))
        chk(all(sz[0].addr != x.addr for x in st),
            'the patch is NOT at the shared store')
        chk(sz[0].addr > (st[0].addr if st else 0),
            'the size constant is out of line, after the store (+0x%X > +0x%X)'
            % (sz[0].addr, st[0].addr if st else 0))
        if not sz[0].patched:
            chk(sz[0].word == SIZE_STOCK_WORD and sz[0].reg == SIZE_STOCK_REG,
                'the recorded stock word 0x%08X (%s) is what is really there '
                '(0x%08X, %s) -- --revert depends on it'
                % (SIZE_STOCK_WORD, SIZE_STOCK_REG, sz[0].word, sz[0].reg))
            d = list(md.disasm(struct.pack('<I', sz[0].word), 0))
            chk(d and d[0].operands[1].type == CS_OP_IMM
                and d[0].operands[1].imm == SIZE_STOCK_VALUE,
                'and it decodes to the immediate %d' % SIZE_STOCK_VALUE)

    log('  the split 32-bit load has both halves covered:')
    hi = [s for s in found if s.kind == 'hi']
    lo = [s for s in found if s.name == 'swirl_enter_sub_401810' and s.kind == 'load']
    chk(len(hi) == 1 and len(lo) == 1, 'low and high half both located')
    if hi and lo:
        chk(hi[0].addr == lo[0].addr + 4, 'the high half immediately follows the low')
        chk(hi[0].reg != lo[0].reg, 'they land in different registers')

    log('  no stored global is written (the uncrop leg keeps its rect):')
    chk(all(s.kind != 'store' for s in found),
        'every site is a load redirect or a private branch constant')

    log('')
    log('  %d check(s) pass, %d fail' % (ok, fail))
    return 1 if fail else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('main', nargs='?', default='exefs/main')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--revert', action='store_true')
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--verify', action='store_true')
    a = ap.parse_args(argv)
    if a.verify:
        return verify(a.main)
    if a.apply or a.revert:
        return apply(a.main, revert=a.revert)
    show(a.main)
    return 0


if __name__ == '__main__':
    sys.exit(main())
