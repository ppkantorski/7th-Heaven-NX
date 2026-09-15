#!/usr/bin/env python3
r"""
ff7nx_escapescale.py -- un-squeeze the Escape ripple.

FOUR WORDS OF ARITHMETIC IN ONE CAVE, ONE HOOK.

THE SYMPTOM, AS REPORTED
========================
Escape "should be taking a screenshot of the entire screen and displaying a
circular ripple from the center outwards on the flat image of the entire
screen". Instead the ripple covers only the 4:3 region above the UI, with black
down both 16:9 margins and below the UI band.

THE CAUSE -- IT IS THE SWIRL'S, EXACTLY
=======================================
`ff7nx_swirlscale` wrote this up for the battle-entry swirl:

    The swirl draws a grid of textured tiles as a 2D overlay. The 2D path uses
    a hardcoded `ortho(0, 640, 480, 0)` (FINDINGS-85), and the vertex shader
    then multiplies gl_Position.x by WS_SCALE = 0.75. So the 640-unit-wide grid
    lands in the central 75% of the frame -- exactly 4:3 -- while the texture
    it samples is the full 16:9 render target.

Escape is the same object on the same path. `escape_mesh` (x86 0x5D602A)
builds a 41 x 22 grid, `x = col * 8` over 0..320 and `y = row * 8` over 0..168,
displaces each point by a radial sine, and finishes each coordinate as

    final = raw * scale[0x9AD1A8] + rect.x[0x9AAD4C]

with scale 2 and rect.x 0 -- a 640-unit-wide grid, landing in the central 4:3.
FINDINGS-288 had already recorded that the swirl builds its captures from the
same ten-field struc91 sequence Escape uses, so this was one mechanism all
along.

THE FIX
=======
Identical to swirlscale: leave the matrix alone, widen the geometry at the
destination. Scale the vertex x about the frame centre by 1 / WS_SCALE = 4/3:

    x' = 320 + (x - 320) * 4/3
       = (4x - 1280 + 960) / 3
       = (4x - 320) / 3

Checked at the corners without fitting anything:

    x =   0  ->  ( 0   - 320)/3 = -106.67   the left edge of the 16:9 frame
    x = 320  ->  (1280 - 320)/3 =  320      the centre, fixed
    x = 640  ->  (2560 - 320)/3 =  746.67   the right edge

Y is deliberately untouched: only the horizontal axis is squeezed, and the
effect's vertical extent is a separate question (the mesh stops at game y 336
because 22 rows x 8 x scale 2 = 336, which is the battle viewport height).

THE SITE
========
In `escape_mesh`'s ARM body the recompiler hoists the guest globals into
callee-saved registers at the prologue:

    +0x83BF60  w23 = 0x00C1FA18     the raw mesh array
    +0x83BF68  w20 = 0x009AD1A8     the scale
    +0x83BF70  w21 = 0x009AAD4C     rect.x

and the x coordinate is finished exactly once:

    +0x83C2B0  ldrsh w8, [x0]          raw x
    +0x83C2C8  mul   w8, w8, w9        * scale
    +0x83C2CC  mov   w0, w21           <- rect.x, the anchor
    +0x83C2E0  add   w8, w8, w9        + rect.x   = the finished x
    +0x83C2E4  str   w8, [x22, #8]     <- THE HOOK

`mov w0, w21` occurs ONCE in the whole body, and rect.y is reached by
`add w0, w21, #4` instead, so the anchor cannot collide with the Y path. One
hook therefore covers all 902 grid points.

REGISTERS
=========
w8 carries the value. w9 is scratch: it is read at +0x83C2E0 and not read again
before the `bl` at +0x83C2F0, which would clobber it as caller-saved regardless.
Checked in `scratch_is_dead`, not assumed.
"""
from __future__ import annotations

import argparse
import os
import shutil
import struct
import sys
import tempfile
from pathlib import Path

try:
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
except ImportError:                                          # pragma: no cover
    sys.exit('need capstone:  pip install capstone --break-system-packages')

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import nxmap                                                   # noqa: E402
import a64 as A                                                # noqa: E402

ESCAPE_MESH = 0x5D602A
RECT_X = 0x9AAD4C            # the guest global whose hoisted register anchors us
RECT_Y = 0x9AAD50            # reached as RECT_X + 4; must NOT be the anchor
CENTRE = 320                 # (-106.67 + 746.67) / 2, the frame centre
NUM, DEN = 4, 3              # 1 / WS_SCALE

ESCAPESCALE_ENV = 'SEVENTH_NX_ESCAPE_SCALE'


def enabled() -> bool:
    """ON with 16:9, OFF at 4:3, overridable for an A/B.

    At 4:3 WS_SCALE is 1.0, so the grid was never squeezed and applying 4/3
    would push the ripple off both edges of the frame.
    """
    v = os.environ.get(ESCAPESCALE_ENV)
    if v is not None:
        return v not in ('', '0', 'off', 'false', 'no')
    # Ships as one bundle with ff7nx_escapegpu and ff7nx_escapeuv.
    return True


def sdiv(rd, rn, rm):
    return (0x1AC00C00 | (rm << 16) | (rn << 5) | rd) & 0xFFFFFFFF


def sub_imm(rd, rn, imm):
    return (0x51000000 | (imm << 10) | (rn << 5) | rd) & 0xFFFFFFFF


def body(val_reg, scratch_reg):
    """x' = (NUM*x - CENTRE*(NUM-DEN)) / DEN, in four words.

    The same arithmetic ff7nx_swirlscale uses, for the same reason.
    """
    v, s = int(val_reg[1:]), int(scratch_reg[1:])
    bias = CENTRE * (NUM - DEN)          # 320 * 1 = 320
    return [A.lsl(v, v, 2),              # x * 4
            sub_imm(v, v, bias),         # - 320
            A.movz(s, DEN),              # 3
            sdiv(v, v, s)]               # / 3


def expected(x):
    """The model, in Python, for the verifier to check the cave against."""
    n = NUM * x - CENTRE * (NUM - DEN)
    q = abs(n) // DEN
    return q if n >= 0 else -q           # sdiv truncates toward zero


def _md():
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    md.detail = False
    return md


def _hoisted_reg(ins, guest):
    """Which register the prologue loaded `guest` into, or None."""
    lo16, hi16 = guest & 0xFFFF, guest >> 16
    for k, i in enumerate(ins[:-1]):
        if i.mnemonic not in ('mov', 'movz') or '#' not in i.op_str:
            continue
        try:
            if int(i.op_str.rsplit('#', 1)[1], 0) != lo16:
                continue
        except ValueError:
            continue
        nxt = ins[k + 1]
        if nxt.mnemonic != 'movk' or ('#%#x' % hi16) not in nxt.op_str:
            continue
        rd = i.op_str.split(',')[0].strip()
        if nxt.op_str.split(',')[0].strip() == rd:
            return rd
    return None


def find_hook(m, md=None):
    """((hook_va, displaced, val_reg, scratch_reg), None) or (None, why).

    Anchored on the ONE `mov w0, <rect.x register>` in the mesh body, then the
    add/store the recompiler always emits after it. Nothing is hard-coded.
    """
    md = md or _md()
    lo, hi = m.extent(ESCAPE_MESH)
    ins = list(md.disasm(bytes(m.img[lo:hi]), lo))
    rx = _hoisted_reg(ins, RECT_X)
    if rx is None:
        return None, 'rect.x (0x%X) is not hoisted in escape_mesh' % RECT_X
    anchors = [k for k, i in enumerate(ins)
               if i.mnemonic == 'mov' and i.op_str == 'w0, %s' % rx]
    if len(anchors) != 1:
        return None, ('expected exactly one `mov w0, %s`, found %d'
                      % (rx, len(anchors)))
    k = anchors[0]
    # rect.y must be reached differently, or the anchor is ambiguous
    if not any(i.mnemonic == 'add' and i.op_str == 'w0, %s, #4' % rx
               for i in ins):
        return None, 'rect.y is not `add w0, %s, #4`; anchor unsafe' % rx
    for j in range(k, min(k + 10, len(ins) - 1)):
        i = ins[j]
        if i.mnemonic != 'add':
            continue
        parts = [p.strip() for p in i.op_str.split(',')]
        if len(parts) != 3 or parts[0] != parts[1] or not parts[2].startswith('w'):
            continue
        st = ins[j + 1]
        # Unpatched the next word is the store; patched it is our own branch.
        # Accept either, so the hook is locatable in both states -- otherwise
        # revert cannot find what it has to undo.
        if st.mnemonic == 'str' and st.op_str.startswith(parts[0] + ','):
            return (st.address, parts[0], parts[2]), None
        if st.mnemonic == 'b':
            return (st.address, parts[0], parts[2]), None
    return None, 'no `add v,v,s` + store/branch within 10 of the anchor'


def scratch_is_dead(m, hook, scratch, md=None, window=16):
    """The scratch register must not be read before it is next written."""
    md = md or _md()
    ins = list(md.disasm(bytes(m.img[hook + 4:hook + 4 + window * 4]), hook + 4))
    for i in ins:
        if i.mnemonic == 'bl':
            return True                  # caller-saved; clobbered anyway
        ops = [p.strip() for p in i.op_str.split(',')]
        if not ops:
            continue
        if any(scratch in p for p in ops[1:]):
            return False                 # read as a source
        if ops[0] == scratch:
            return True                  # written first
    return False


def walk_cave(m, hook, md=None, limit=64):
    """Recover the cave words from a patched image, for an exact revert.

    The hook is a `b` into the first block; blocks are chained by a trailing
    `b`, and the last one branches back to hook + 4.
    """
    md = md or _md()
    first = next(md.disasm(bytes(m.img[hook:hook + 4]), hook), None)
    if first is None or first.mnemonic != 'b':
        return None, 'hook +0x%X is not a branch' % hook
    va = int(first.op_str.lstrip('#'), 0)
    out = {}
    for _ in range(limit):
        if va in out:
            return None, 'cave loops at +0x%X' % va
        w = struct.unpack_from('<I', m.img, va)[0]
        i = next(md.disasm(bytes(m.img[va:va + 4]), va), None)
        if i is None:
            return None, 'undecodable cave word at +0x%X' % va
        out[va] = w
        if i.mnemonic == 'b':
            tgt = int(i.op_str.lstrip('#'), 0)
            if tgt == hook + 4:
                return out, None         # returned home
            va = tgt
        else:
            va += 4
    return None, 'cave did not terminate within %d words' % limit


def plan(m, revert=False, md=None, pool=None):
    import ff7nx_cave
    md = md or _md()
    found, why = find_hook(m, md)
    if found is None:
        return [], [], [why]
    hook, val, scratch = found
    cur = struct.unpack_from('<I', m.img, hook)[0]
    curi = next(md.disasm(struct.pack('<I', cur), hook), None)
    patched = (curi is not None and curi.mnemonic == 'b')

    if revert:
        if not patched:
            return [], [], []
        cave, why2 = walk_cave(m, hook, md)
        if cave is None:
            return [], [], [why2]
        # The displaced store is the one `str` the cave carries: the four
        # body words are lsl/sub/movz/sdiv and every block ends in `b`.
        stores = []
        for _va, w in sorted(cave.items()):
            d = next(md.disasm(struct.pack('<I', w), 0), None)
            if d is not None and d.mnemonic == 'str':
                stores.append(w)
        if len(stores) != 1:
            return [], [], ['expected exactly one store in the cave, found %d'
                            % len(stores)]
        displaced = stores[0]
        ps = [{'name': 'escape x scale: unhook', 'va': hex(hook),
               'expect': struct.pack('<I', cur).hex(),
               'set': struct.pack('<I', displaced).hex()}]
        for va in sorted(cave):
            ps.append({'name': 'escape x scale: clear cave word +0x%x' % va,
                       'va': hex(va),
                       'expect': struct.pack('<I', cave[va]).hex(),
                       'set': '00000000'})
        return ps, ['  escape x scale removed @ +0x%07X (%d cave word(s) '
                    'returned to the pool)' % (hook, len(cave))], []

    if patched:
        return [], ['  escape x scale already installed @ +0x%07X' % hook], []
    displaced = cur
    if not scratch_is_dead(m, hook, scratch, md):
        return [], [], ['%s is live after +0x%X; the cave would corrupt it'
                        % (scratch, hook)]

    pool = pool or ff7nx_cave.HolePool(m.img, starts=set(m.arm_starts))
    out, entry = ff7nx_cave.emit_hooked(pool, hook, displaced,
                                        body(val, scratch))
    patches = []
    for va in sorted(out):
        old = struct.unpack_from('<I', m.img, va)[0]
        if va != hook and old != 0:
            return [], [], ['cave word +0x%X is not padding (0x%08X)'
                            % (va, old)]
        patches.append({'name': 'escape x scale +0x%x' % va, 'va': hex(va),
                        'expect': struct.pack('<I', old).hex(),
                        'set': struct.pack('<I', out[va]).hex()})
    notes = ['  escape ripple x scale  x -> (%dx - %d)/%d  about %d -- the '
             'grid now spans the whole 16:9 frame instead of the central 4:3'
             % (NUM, CENTRE * (NUM - DEN), DEN, CENTRE),
             '  hook +0x%07X (%s carries x, %s scratch), cave entry +0x%X, '
             '%d word(s)' % (hook, val, scratch, entry, len(out) - 1)]
    return patches, notes, []


def apply(main, revert=False, log=print) -> int:
    import nso_patcher
    main = Path(main)
    m = nxmap.Main(str(main))
    patches, notes, problems = plan(m, revert=revert)
    if problems:
        for p in problems:
            log('  ! escape x scale: %s' % p)
        log('  refusing to touch the Escape mesh.')
        return 1
    for n in notes:
        log(n)
    if not patches:
        return 0
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'ff7nx_escapescale', 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.escscale-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('main')
    ap.add_argument('--revert', action='store_true')
    a = ap.parse_args(argv)
    return apply(a.main, revert=a.revert)


if __name__ == '__main__':
    raise SystemExit(main())
