#!/usr/bin/env python3
r"""
ff7nx_overlay.py -- the battle overlays that read the viewport x as 0.

THE SYMPTOM
===========
In battle, things that should be positioned against the left edge of the
frame are positioned against the left edge of the *4:3* frame instead: damage
numbers and two battle quads sit ~107 game units right of where they belong,
and are clipped on the right.

THE CAUSE
=========
FF7 keeps the battle viewport rect in four globals.  FINDINGS-97 pinned them:

    [0x9AAD4C] x = 0     [0x9AAD50] y = 0
    [0x9AAD5C] w = 640   [0x9AAD68] h = 332

Everything that draws full-frame in battle reads `x` as its left edge.  In a
16:9 build the frame spans game x -107..747, so a left edge of 0 is wrong by
exactly the left margin.  FFNx redirects those reads to its own global,
`wide_viewport_x = -107` (`src/ff7/widescreen.h:26`), via

    patch_code_dword(<site>, (uint32_t)&wide_viewport_x);

which rewrites the disp32 of the load.  This port has no FFNx globals to
point at, so the equivalent is to replace the recompiled load with the
immediate -- one word, no cave.  FINDINGS-98 has the derivation.

WHAT THIS MODULE DOES *NOT* DO, AND WHY
=======================================
FFNx's battle widescreen block covers five functions.  This module ships the
three it can ship COMPLETELY, and deliberately leaves the other two alone.

  battle_sub_5BD050 -- the fade / limit-break / attack blink overlay.  Its 10
  redirects are located (FINDINGS-98 §4) but they are not the whole patch:
  FFNx pairs them with seven `imul` companions in the same function
  (50 -> 72, 33 -> 48) AND a strip count in its caller,
  `battle_sub_5BCF9D + 0x3A` (21 -> 30).  5BCF9D sets the count, 5BD050 draws
  each strip, so widening the strip geometry without raising the count leaves
  the wipe short -- HANDOFF-90 §4.4's "wider but torn", precisely.

  And that count cannot simply be taken from FFNx, because **FFNx patches
  that same word twice, from two different features**:

      animations.cpp:1314  patch_multiply_code<WORD>(+0x3A, battle_frame_multiplier)   21 -> 84
      widescreen.cpp       patch_code_short(+0x3A, 30)                                 21 -> 30

  Our build already ships the 60 FPS one (module +0x7CBA94, 21 -> 84).  So the
  correct combined value is 30, 84, 120, or something else, depending on
  whether that field is a strip count, a frame count, or both -- and guessing
  which is how FINDINGS-95 cost two builds.  Left out until it is measured.

  swirl_enter_40164E / _sub_401810 / swirl_loop_sub_4026D4 -- the swirl.
  Located too, but gated on the `+0xE8` companion (imm 0x40 -> 85), which is
  a register write in this port and has to be tied to the third of three
  stores to 0x9A04DC.  FINDINGS-98 §6.

So: six words, three functions, every one of which FFNx patches exactly once
and nothing else in this tree touches.

THE SITES
=========
Derived by sequence-aligning every absolute-addressed global access in each
function against the x86 -- not by pattern, and not by position within a
guest address, both of which returned confident wrong answers (FINDINGS-98
§2).  Re-derived from the image at --verify time rather than trusted from
this table.

    function                      ARM word     stock            becomes
    battle_sub_58ACB9             +0x06DF0DC   ldr  w8, [x0]    movn w8, #106
    battle_sub_58ACB9             +0x06DF1F8   ldr  w8, [x0]    movn w8, #106
    display_battle_damage_5BB410  +0x07C56A8   ldr  w8, [x0]    movn w8, #106
    display_battle_damage_5BB410  +0x07C5700   ldr  w8, [x0]    movn w8, #106
    battle_draw_quad_5BD473       +0x07CCFCC   ldrh w20,[x0]    movz w20,#0xFF95
    battle_draw_quad_5BD473       +0x07CD0AC   ldrh w19,[x0]    movz w19,#0xFF95

`-107` at a 32-bit load is `movn #106`, because ~106 == -107 exactly.  At a
16-bit load it is `movz #0xFF95`, which reproduces a zero-extending `ldrh` of
the dword 0xFFFFFF95 bit for bit -- the consumer stores 16 bits to the guest
stack and reads them back as a signed short, so 0xFF95 IS -107 there.

The `bl` that translated the guest address into x0 is left in place.  It
still runs and now computes an address nobody reads.  That is deliberate:
removing it would change the instruction count and every branch offset around
it, for no gain.  x0 being dead after each of these six is CHECKED, not
assumed -- see `_x0_dead`.
"""
import argparse
import os
import shutil
import struct
import sys
import tempfile
from pathlib import Path

try:
    from capstone import (Cs, CS_ARCH_ARM64, CS_MODE_ARM, CS_OP_REG)
    from capstone.arm64 import ARM64_OP_MEM
except ImportError:                                          # pragma: no cover
    sys.exit('need capstone:  pip install capstone --break-system-packages')

import nxmap
import ff7nx_guestref as gr

WIDE_VIEWPORT_X = -107          # FFNx src/ff7/widescreen.h:26
BATTLE_RECT_X = 0x9AAD4C        # FINDINGS-97 §2

# x86 entry -> (name, how many FFNx redirects of BATTLE_RECT_X it has)
FUNCTIONS = [
    (0x58ACB9, 'battle_sub_58ACB9', 2),
    (0x5BB410, 'display_battle_damage_5BB410', 2),
    (0x5BD473, 'battle_draw_quad_5BD473', 2),
]

# The exact `patch_code_dword(fn + off, &wide_viewport_x)` offsets, transcribed
# from FFNx src/ff7/widescreen.cpp ff7_widescreen_hook_init().  These are the
# module's only tie to something outside itself, and they are load-bearing:
# see `_check_against_x86`.
FFNX_OFFSETS = {
    0x58ACB9: (0x055, 0x065),
    0x5BB410: (0x23F, 0x24C),
    0x5BD473: (0x0DA, 0x112),
}

FFNX_HEADER = 'repos/FFNx-master/src/widescreen.h'

# functions FFNx also patches that this module refuses to touch, and why
EXCLUDED = {
    0x5BD050: 'battle_sub_5BD050 -- needs the 5BCF9D+0x3A strip count, which '
              'the 60 FPS pass already owns',
    0x40164E: 'swirl_enter_40164E -- needs the +0xE8 companion',
    0x401810: 'swirl_enter_sub_401810 -- two words, not one (signed split load)',
    0x4026D4: 'swirl_loop_sub_4026D4 -- ships with the rest of the swirl',
}


# --------------------------------------------------------------- encodings

def enc_movz(rd, imm16):
    assert 0 <= imm16 <= 0xFFFF
    return (0x52800000 | (imm16 << 5) | rd) & 0xFFFFFFFF


def enc_movn(rd, imm16):
    assert 0 <= imm16 <= 0xFFFF
    return (0x12800000 | (imm16 << 5) | rd) & 0xFFFFFFFF


def _stock_word(site):
    """
    Rebuild the load a patched site replaced: `ldr`/`ldrh` wRt, [x0].

    LDR  (32-bit, unsigned offset, imm12=0, Rn=x0) = 0xB9400000 | Rt
    LDRH (16-bit,      "        "            "   ) = 0x79400000 | Rt

    --revert cannot read the original out of the module, because the original
    is exactly what --apply overwrote.  So it is reconstructed, and `verify`
    asserts on a STOCK module that the reconstruction equals the word actually
    there.  Without that assertion this function would be an untested guess
    sitting in the one code path nobody exercises until something has already
    gone wrong.
    """
    rd = int(site.reg[1:])
    return ((0xB9400000 if site.width == 4 else 0x79400000) | rd) & 0xFFFFFFFF


def _ffnx_wide_viewport_x(root='.'):
    """
    `wide_viewport_x` as FFNx declares it, or None if the tree is absent.

    Read rather than hard-coded, because a constant this module types itself
    cannot falsify this module.  Mutating WIDE_VIEWPORT_X from -107 to -106
    passed every check in the first version of `verify` -- the encoder, the
    planner and the checker all took the value from the same place, so the
    mutant simply moved all three together.  That is FINDINGS-97 §5.1's
    "an emulator that takes its inputs from the thing under test cannot
    falsify it", one level up and in a different disguise.
    """
    p = Path(root) / FFNX_HEADER
    if not p.exists():
        return None
    import re
    mo = re.search(r'^\s*int\s+wide_viewport_x\s*=\s*(-?\d+)\s*;',
                   p.read_text(), re.M)
    return int(mo.group(1)) if mo else None


def _check_against_x86(exe_path='ff7_en_switch'):
    """
    [(fn, off, disp32)] -- what FFNx's own offsets actually read, in the x86.

    The independent evidence that BATTLE_RECT_X names the rect's X and not one
    of its neighbours.  Pointing this module at 0x9AAD50 -- the rect's Y, four
    bytes along, and touched by all three of these functions -- produced the
    right site COUNT in every function and passed the entire suite silently.
    Only the x86 can settle which field FFNx meant, because only the x86 has
    FFNx's offsets in it.
    """
    p = Path(exe_path)
    if not p.exists():
        return None
    from probe_overlay import Exe, x86_site
    from capstone import Cs as _Cs, CS_ARCH_X86, CS_MODE_32
    exe = Exe(str(p))
    md = _Cs(CS_ARCH_X86, CS_MODE_32)
    md.detail = True
    out = []
    for fn, offs in sorted(FFNX_OFFSETS.items()):
        for off in offs:
            s = x86_site(exe, md, fn, off, 4)
            out.append((fn, off, (s['field'] & 0xFFFFFFFF) if s else None))
    return out


def _text(main):
    return nxmap.Main(str(main))


def _md():
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    md.detail = True
    return md


def _x0_dead(text, addr, md, window=10):
    """
    True if x0 is not READ before it is next written, starting after `addr`.

    An instruction merely mentioning w0 is not a read -- `sub w0, w21, #0xc`
    and `mov w0, w19` both DEFINE it.  Checking for the substring 'w0' instead
    of the access flags reports 11 of these 6+15 sites as hazards when only
    one genuinely is, which would have sent the whole patch back for a cave it
    does not need.
    """
    for i in md.disasm(text[addr + 4: addr + 4 + window * 4], addr + 4):
        r, w = i.regs_access()
        if any(i.reg_name(x) in ('x0', 'w0') for x in r):
            return False
        if any(i.reg_name(x) in ('x0', 'w0') for x in w):
            return True
    return True


# --------------------------------------------------------------- discovery

class Site(object):
    """One patchable word, findable whether or not it is already patched."""

    __slots__ = ('addr', 'reg', 'width', 'mnemonic', 'op_str', 'word',
                 'patched', 'tap')

    def __init__(self, addr, reg, width, ins, word, patched, tap):
        self.addr, self.reg, self.width = addr, reg, width
        self.mnemonic, self.op_str = ins.mnemonic, ins.op_str
        self.word, self.patched, self.tap = word, patched, tap


def sites(m, md=None, window=8):
    """
    Re-derive the six words FROM THE IMAGE, in either state.

    Nothing here reads a hard-coded address.  Each function is located through
    the recompilation map and its guest accesses by constant propagation.

    The anchor is the CALL TO THE ADDRESS TRANSLATOR whose argument is the
    battle rect's x -- not the load.  That distinction is not cosmetic: the
    first version of this function keyed on the load, which meant that the
    moment --apply replaced a load with an immediate, discovery found zero
    sites, and --show reported an empty list while --revert cheerfully said
    "already in the requested state" on a fully patched module.  A patch you
    cannot find again is a patch you cannot back out, and this project's own
    rule is that unrevertable changes make the next result unattributable.

    The translator call survives the patch, so it anchors both states.  From
    it we walk forward for either the original load through x0, or the
    immediate that replaced it.
    """
    md = md or _md()
    out = []
    for fn, name, want in FUNCTIONS:
        a, b = m.extent(fn)
        _, stats = gr.scan(m.text, a, b, md)
        found = []
        for tap, guest in stats.get('taps', []):
            if guest != BATTLE_RECT_X:
                continue
            for i in md.disasm(m.text[tap + 4: tap + 4 + window * 4], tap + 4):
                mem = next((o for o in i.operands
                            if o.type == ARM64_OP_MEM), None)
                word = struct.unpack('<I', m.text[i.address:i.address + 4])[0]
                if (i.mnemonic in ('ldr', 'ldrh') and mem is not None
                        and i.reg_name(mem.mem.base) in ('x0', 'w0')
                        and mem.mem.disp == 0):
                    found.append(Site(i.address, i.reg_name(i.operands[0].reg),
                                      4 if i.mnemonic == 'ldr' else 2,
                                      i, word, False, tap))
                    break
                if i.mnemonic in ('movz', 'movn', 'mov') and \
                        len(i.operands) == 2 and \
                        i.operands[1].type != CS_OP_REG and \
                        i.operands[1].imm in (WIDE_VIEWPORT_X,
                                              WIDE_VIEWPORT_X & 0xFFFF):
                    found.append(Site(i.address, i.reg_name(i.operands[0].reg),
                                      4 if i.operands[1].imm < 0 else 2,
                                      i, word, True, tap))
                    break
                if mem is not None and i.reg_name(mem.mem.base) in ('x0', 'w0'):
                    break       # x0 used for something else: not our site
        out.append({'fn': fn, 'name': name, 'want': want, 'loads': found,
                    'arm': (a, b)})
    return out


def plan(m, revert=False, md=None):
    """(patches, notes, problems) -- never writes."""
    md = md or _md()
    patches, notes, bad = [], [], []

    for s in sites(m, md):
        if len(s['loads']) != s['want']:
            bad.append('%s: expected %d load(s) of the battle rect x, found %d'
                       % (s['name'], s['want'], len(s['loads'])))
            continue
        for ld in s['loads']:
            rd = int(ld.reg[1:])
            stock = ld.word
            if ld.width == 4:
                new = enc_movn(rd, ~WIDE_VIEWPORT_X)
                asm = 'movn %s, #%d' % (ld.reg, ~WIDE_VIEWPORT_X)
            elif ld.width == 2:
                new = enc_movz(rd, WIDE_VIEWPORT_X & 0xFFFF)
                asm = 'movz %s, #0x%X' % (ld.reg, WIDE_VIEWPORT_X & 0xFFFF)
            else:
                bad.append('%s +0x%X: unexpected load width %d'
                           % (s['name'], ld.addr, ld.width))
                continue

            if not _x0_dead(m.text, ld.addr, md):
                bad.append('%s +0x%X: x0 is read again after this load -- a '
                           'one-word swap would break it' % (s['name'], ld.addr))
                continue

            cur = struct.unpack('<I', m.text[ld.addr:ld.addr + 4])[0]
            # `stock` is only the stock word when the site is unpatched.  On a
            # patched module the site IS the immediate, so a revert has to
            # restore the load, and that word has to come from the recorded
            # original rather than from whatever is sitting there now.
            if ld.patched:
                stock = _stock_word(ld)
            frm, to = (new, stock) if revert else (stock, new)
            if cur == to:
                continue
            if cur != frm:
                bad.append('%s +0x%X: word is 0x%08X, expected 0x%08X'
                           % (s['name'], ld.addr, cur, frm))
                continue
            # nso_patcher takes BYTES in image order, not a word.  Formatting
            # the word with %08x hands it the big-endian spelling and the
            # verify step rejects it -- which is the right failure, but it is
            # the module's bug, not the image's.
            patches.append({
                'name': '%s battle viewport x -> %d @ +0x%X'
                        % (s['name'], WIDE_VIEWPORT_X, ld.addr),
                'va': hex(ld.addr),
                'expect': struct.pack('<I', frm).hex(),
                'set': struct.pack('<I', to).hex(),
            })
            notes.append('  %-30s +0x%07X  %-16s -> %s'
                         % (s['name'], ld.addr,
                            '%s %s' % (ld.mnemonic, ld.op_str),
                            asm if not revert else '%s %s' % (ld.mnemonic,
                                                              ld.op_str)))
    return patches, notes, bad


# ------------------------------------------------------------------ apply

REFUSAL = """
  ff7nx_overlay.py WILL NOT APPLY.  It targets the wrong category of site.

  The six words this module patches are in three functions that read the
  battle rect's ORIGIN (x, y) and never its EXTENT (w, h).  Measured across
  every body in the module that materialises a battle-rect constant:

      0x5BD050  x 4  y 5  w 5  h 1   <- sizes a full-frame quad   (fade/flash)
      0x58ACB9  x 2  y 2  w 0  h 0   <- positions content
      0x5BB410  x 2  y 2  w 0  h 0   <- positions content
      0x5BD473  x 2  y 2  w 0  h 0   <- positions content

  A full-screen overlay needs a corner AND an extent, so it reads w.  A thing
  that is merely placed in the frame reads only the origin.  All three of this
  module's functions are in the second group -- damage numbers and UI -- which
  the report says are already in the right place.  Moving them to -107 would
  be a regression, not a fix.

  WHY COPYING FFNx's LIST WAS THE ERROR.  FFNx redirects the positioning sites
  too, and it is right to: FFNx moves the whole battle coordinate space out to
  the wide edges, so content anchored to viewport-left belongs at -107.  This
  port does the opposite on purpose.  FINDINGS-97 kept the battle rect at
  (0, 0, 640, 332) and opened the DEVICE CLIP instead, and WS_SCALE puts game
  x=0 at the left edge of the central 4:3 region.  So here the UI is meant to
  stay in the 4:3 core while the picture and the effects widen around it.

  FFNx's redirect list is therefore not transferable wholesale to this port.
  Only the extent-reading half of it is.

  The right target set is the 14 bodies that read w or h -- 0x5BD050 first,
  then the summon and limit-break effect quads (0x48C2A1, 0x490F2A, 0x4700F7,
  0x470438, 0x4A3A2E, 0x4A4BE6, 0x4D7044, 0x4DB15F, 0x5096F3, 0x41B300,
  0x41BAB3, 0x4825E0, 0x487BD2).  Those are the screen flashes.
"""


def apply(main, revert=False, log=print) -> int:
    import nso_patcher
    if not revert:
        log(REFUSAL)
        return 1
    main = Path(main)
    m = _text(main)
    patches, notes, bad = plan(m, revert)

    if bad:
        for b in bad:
            log('  ! ' + b)
        log('  refusing to write.')
        return 1
    for n in notes:
        log(n)
    if not patches:
        log('  nothing to do -- module already in the requested state')
        return 0

    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(nso, {'name': 'ff7nx_overlay',
                                             'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.overlay-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    log('  read back from the written module:')
    m2 = _text(main)
    md = _md()
    for s in sites(m2, md):
        for ld in s['loads']:
            w = struct.unpack('<I', m2.text[ld.addr:ld.addr + 4])[0]
            d = list(md.disasm(m2.text[ld.addr:ld.addr + 4], ld.addr))
            log('    +0x%07X  %-30s %s' % (ld.addr, s['name'],
                                           '%s %s' % (d[0].mnemonic, d[0].op_str)
                                           if d else '%08X' % w))
    return 0


def show(main, log=print):
    m = _text(main)
    md = _md()
    log('  battle overlay viewport x (%d words):' % sum(f[2] for f in FUNCTIONS))
    for s in sites(m, md):
        for ld in s['loads']:
            # State comes from `ld.patched`, which discovery already decided
            # structurally.  Deriving it here from the mnemonic instead reads
            # 'mov' for both MOVZ and MOVN -- the same alias that broke the
            # first site scan and then the encoding check -- and labels every
            # patched word "stock".  Three times is a pattern: never branch on
            # an AArch64 mnemonic.
            state = 'PATCHED (viewport x = %d)' % WIDE_VIEWPORT_X \
                if ld.patched else 'stock'
            log('    +0x%07X  %-30s %-18s %s'
                % (ld.addr, s['name'], '%s %s' % (ld.mnemonic, ld.op_str),
                   state))
    log('')
    log('  NOT covered by this module, on purpose:')
    for fn, why in sorted(EXCLUDED.items()):
        log('    0x%06X  %s' % (fn, why))


# ----------------------------------------------------------------- verify

def verify(main=None, log=print) -> int:
    main = Path(main or 'exefs/main')
    m = _text(main)
    md = _md()
    ok = fail = 0

    def chk(cond, what):
        nonlocal ok, fail
        if cond:
            ok += 1
            log('    ok    ' + what)
        else:
            fail += 1
            log('    FAIL  ' + what)

    log('  encodings, against capstone:')
    # Compare the VALUE the instruction produces and the register it lands in,
    # never the printed text.  Capstone prints both MOVZ and MOVN through the
    # MOV alias, so an assertion on the mnemonic fails on three correct
    # encodings -- the same aliasing that made the first site scan find zero
    # of twenty-one sites (FINDINGS-98 §1.1).  Twice in one module is enough.
    for rd, want in ((8, -107), (20, 0xFF95), (19, 0xFF95)):
        w = enc_movn(rd, 106) if want == -107 else enc_movz(rd, want)
        d = list(md.disasm(struct.pack('<I', w), 0))
        if not d:
            chk(False, '0x%08X does not decode' % w)
            continue
        i = d[0]
        got_rd = i.reg_name(i.operands[0].reg)
        got_v = i.operands[1].imm
        chk(got_rd == 'w%d' % rd and got_v == want,
            '0x%08X -> %s = %d  (capstone prints "%s %s")'
            % (w, got_rd, got_v, i.mnemonic, i.op_str))

    log('  -107 is reproduced at both widths:')
    chk(struct.unpack('<i', struct.pack('<I', (~106) & 0xFFFFFFFF))[0] == -107,
        'movn #106 is -107 as a signed 32-bit value')
    chk(struct.unpack('<h', struct.pack('<H', 0xFF95))[0] == -107,
        '0xFF95 is -107 as a signed 16-bit value')
    chk((-107 & 0xFFFFFFFF) & 0xFFFF == 0xFF95,
        'a zero-extending ldrh of the dword -107 yields 0xFF95')

    log('  against evidence OUTSIDE this module:')
    ffx = _ffnx_wide_viewport_x()
    if ffx is None:
        log('    ----  FFNx tree absent (%s); cannot cross-check the value'
            % FFNX_HEADER)
    else:
        chk(ffx == WIDE_VIEWPORT_X,
            'wide_viewport_x is %d in FFNx and %d here' % (ffx, WIDE_VIEWPORT_X))
    x86 = _check_against_x86()
    if x86 is None:
        log('    ----  ff7_en_switch absent; cannot cross-check the guest field')
    else:
        chk(len(x86) == sum(f[2] for f in FUNCTIONS),
            'FFNx names %d redirect(s) across these functions' % len(x86))
        for fn, off, disp in x86:
            chk(disp == BATTLE_RECT_X,
                'x86 0x%06X+0x%03X reads [0x%X], and this module targets '
                '[0x%X]' % (fn, off, disp or 0, BATTLE_RECT_X))
        chk({f for f, _, _ in x86} == {f[0] for f in FUNCTIONS},
            'the function set matches FFNx exactly')

    log('  the sites, re-derived from the image (anchored on the translator):')
    found = sites(m, md)
    for s in found:
        chk(len(s['loads']) == s['want'],
            '%s: %d site(s) of the battle rect x (want %d)'
            % (s['name'], len(s['loads']), s['want']))
        for ld in s['loads']:
            chk(_x0_dead(m.text, ld.addr, md),
                '%s +0x%X: x0 is dead after the site' % (s['name'], ld.addr))
            if not ld.patched:
                chk(_stock_word(ld) == ld.word,
                    '%s +0x%X: --revert would restore 0x%08X, and the word '
                    'really is 0x%08X' % (s['name'], ld.addr,
                                          _stock_word(ld), ld.word))

    log('  the excluded functions are untouched:')
    for fn, why in sorted(EXCLUDED.items()):
        try:
            a, b = m.extent(fn)
        except SystemExit:
            continue
        acc, _ = gr.scan(m.text, a, b, md)
        n = sum(1 for x in acc if x.is_load and x.mnemonic in ('movz', 'movn'))
        chk(n == 0, '0x%06X has no immediate where a guest load should be' % fn)

    log('  round trip:')
    patches, _, bad = plan(m)
    chk(not bad, 'plan() reports no problems (%s)' % (bad[0] if bad else 'clean'))
    chk(len(patches) in (0, 6),
        'plan() yields 6 patches from stock, or 0 if already applied (got %d)'
        % len(patches))

    log('')
    log('  %d check(s) pass, %d fail' % (ok, fail))
    return 1 if fail else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.split('\n')[1],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('main', nargs='?', default='exefs/main')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--revert', action='store_true')
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--verify', action='store_true')
    a = ap.parse_args(argv)

    if a.verify:
        return verify(a.main)
    if a.show:
        show(a.main)
        return 0
    if a.apply or a.revert:
        return apply(a.main, revert=a.revert)
    show(a.main)
    return 0


if __name__ == '__main__':
    sys.exit(main())
