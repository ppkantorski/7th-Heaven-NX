#!/usr/bin/env python3
r"""
ff7nx_scissorprobe.py -- A DIAGNOSTIC. NOT A FIX. DO NOT SHIP.

It answers exactly one question, in one look, on the title screen:

    Does anything in this renderer actually call glScissor?

WHY IT EXISTS
=============
Two builds of `ff7nx_movieclip` came back wrong, and the second came back
*silent*, which is worse. What those two builds established between them:

  * v1 narrowed the rect handed to `bl +0x11320E0` (vtable +0x188). The whole
    picture scaled by exactly 0.75 -- Cloud moved x~50 -> x~198, which is
    `640 + (50-640)*0.75`. So +0x188 is the VIEWPORT, the frame rect at that
    draw was 1280x720 (a 4:3 frame would have tripped v1's own no-op guard),
    and **Cloud scaled along with the movie** -- same helper, same target,
    same frame, `is_playing` set. The draw path and the flag are correct.
  * v2 asked for the identical rectangle, 160..1120, on the paired call
    `bl +0x11320F0` (vtable +0x190). Nothing moved at all.

The image says vtable +0x190 is the glScissor path:

    GOT +0xE60 glViewport   <- +0x1133F10 <- +0x1137640 <- vtable +0x188
    GOT +0xE68 glScissor    <- +0x1133F80 <- +0x1137730 <- vtable +0x190
    (vtable object base +0x12CCAE0; names from .rela.dyn + DT_SYMTAB. Note
     nxmap does NOT apply R_AARCH64_RELATIVE to its image, so none of this is
     visible until you write m.rel into m.img yourself.)

Those two cannot both be true. The most likely reconciliation is that the
renderer in use is a DIFFERENT class implementing the same interface, so
+0x188 is still a viewport but +0x190 is not +0x1137730. That cannot be
settled statically: the object is `[[[0x12CE510]]]`, and `gfx_drv_init`
(+0x10D5194) fills it from `[[0x12CE188]]` -- two indirections that only
exist at runtime.

WHAT THIS PATCH DOES
====================
It stops arguing about vtable slots and hooks the ONLY function in the module
that tail-calls the glScissor PLT stub:

    +0x1133F80   (this, const int32 rect[4])   rect = {x, y, w, h}
      ... early-returns if the four ints match the cache at [this+0x2D0] ...
      +0x1133FE0  ldp w0, w8, [x1]
      +0x1133FE4  ldp w2, w3, [x1, #8]
      +0x1133FE8  mov w1, w8              <- HOOK
      +0x1133FEC  b   +0x11521C0          glScissor(x, y, w, h)

and clamps the box to the MIDDLE HALF of whatever it was about to be:

    x += w/4        w /= 2

unconditionally -- no movie gate, no target arithmetic, nothing that can
quietly evaluate to a no-op. Three words plus the displaced one.

Registers: w9 is scratch and dead (the next instruction is a tail call to a
PLT stub, which clobbers x0-x18). w0 is the box x, w2 the box width. w1/w3/w8
are untouched. The cache at [this+0x2D0] keeps the UNclamped rect, so a
repeat call early-returns and GL simply stays clamped -- which for a probe is
a feature.

HOW TO READ THE RESULT -- one look, no need to reach the reactor
===============================================================
* **Everything is cut to the middle half of the screen** -- the sides go
  black, or stop updating. Then glScissor is live in this path, the renderer
  honours it, and the fix is a scissor after all. v2 failed because it
  changed a vtable slot this object does not use, and the next build clamps
  HERE instead, gated on `is_playing`, to the 4:3 band.

* **Nothing changes anywhere. Title screen, field, battle, menus all normal.**
  Then glScissor is never called while anything is drawn, and the scissor
  route is dead for good -- no rect, no slot, no gate will ever clip a model.
  Stop patching the renderer and cull the models by x instead.

Anything else -- clipped in some places and not others -- is still useful:
whichever screens ARE clipped are the ones that go through this path, and the
field's behaviour is the one that matters.

    python3 ff7nx_scissorprobe.py <exefs/main | sdout> --show
    python3 ff7nx_scissorprobe.py <exefs/main | sdout> --apply
    python3 ff7nx_scissorprobe.py <exefs/main | sdout> --revert
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import a64 as A                                                  # noqa: E402

TITLE_ID = '0100A5B00BDC6000'
SDOUT_MAIN = os.path.join('atmosphere', 'contents', TITLE_ID, 'exefs', 'main')

HOOK_VA = 0x1133FE8
HOOK_ORIG = 0x2A0803E1                  # mov w1, w8

ANCHORS = [
    (0x1133F80, 0xB942D009, 'ldr w9, [x0, #0x2D0]   the glScissor cache'),
    (0x1133FE0, 0x29402020, 'ldp w0, w8, [x1]       x, y'),
    (0x1133FE4, 0x29410C22, 'ldp w2, w3, [x1, #8]   w, h'),
    (0x1133FE8, 0x2A0803E1, 'mov w1, w8             THE HOOK SITE'),
    (0x1133FEC, 0x14007875, 'b +0x11521C0           -> glScissor'),
    (0x11377E8, 0x97FFF1E6, 'bl +0x1133F80          its only caller'),
]

DISASM = ['lsr w9, w2, #2', 'add w0, w0, w9', 'lsr w2, w2, #1',
          'mov w1, w8', 'b #return']


def cave_body():
    """x += w/4 ; w /= 2 -- the middle half of whatever box was asked for."""
    return [
        A.lsr(9, 2, 2),            # lsr w9, w2, #2      w/4
        A.add_reg(0, 0, 9),        # add w0, w0, w9      x += w/4
        A.lsr(2, 2, 1),            # lsr w2, w2, #1      w /= 2
    ]


def check_encoding(log=print):
    try:
        from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
    except ImportError:
        log('  (capstone not installed -- encodings NOT checked)')
        return True
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    words = cave_body() + [HOOK_ORIG, A.b(0x1010, 0x1000)]
    blob = b''.join(struct.pack('<I', w) for w in words)
    got = [(i.mnemonic + ' ' + i.op_str).strip() for i in md.disasm(blob, 0x1000)]
    ok = len(got) == len(words)
    for k, (g, want) in enumerate(zip(got, DISASM)):
        same = g.split()[0] == want.split()[0] if '#return' in want else g == want
        if not same:
            log('  ! word %d encodes `%s`, meant `%s`' % (k, g, want))
            ok = False
    return ok


def _hex(w):
    return ' '.join('%02X' % b for b in struct.pack('<I', w))


def _word(text, va):
    return struct.unpack_from('<I', text, va)[0]


def state(text):
    got = _word(text, HOOK_VA)
    if got == HOOK_ORIG:
        return 'stock'
    if (got & 0xFC000000) == 0x14000000:
        return 'patched'
    return 'unknown'


def verify_anchors(text, log=print):
    ok = True
    for va, want, what in ANCHORS:
        got = _word(text, va)
        if va == HOOK_VA and (got & 0xFC000000) == 0x14000000:
            continue
        if got != want:
            log('  ! +0x%07X %s: expected %08X, found %08X'
                % (va, what, want, got))
            ok = False
    return ok


def resolve_main(path):
    if os.path.isdir(path):
        cand = os.path.join(path, SDOUT_MAIN)
        if os.path.exists(cand):
            return cand
        raise SystemExit('no %s under %s' % (SDOUT_MAIN, path))
    return path


def show(path, log=print):
    import nxmap
    m = nxmap.Main(path)
    log('module %s' % path)
    log('  scissor probe: %s'
        % {'stock': 'NOT installed',
           'patched': 'INSTALLED  (+0x%07X is a branch)' % HOOK_VA,
           'unknown': 'UNRECOGNISED -- do not patch'}[state(m.text)])
    log('  anchors: %s'
        % ('pass' if verify_anchors(m.text, lambda *_: None) else 'FAIL'))
    log('')
    log('  DIAGNOSTIC ONLY -- clamps EVERY glScissor box to the middle half.')
    log('  the cave:')
    for k, w in enumerate(cave_body() + [HOOK_ORIG]):
        log('    %d  %08X  %s' % (k, w, DISASM[k]))


def apply_to_nso(src, dest, log=lambda *_: None):
    try:
        import ff7nx_cave
        import nso_patcher
        import nxmap
    except ImportError as exc:                                 # noqa: BLE001
        log('! scissor probe: cannot import %s' % exc)
        return False
    try:
        m = nxmap.Main(src)
        if state(m.text) == 'patched':
            log('  already installed; nothing to do')
            return True
        if not verify_anchors(m.text, log) or not check_encoding(log):
            log('! scissor probe: refusing to patch')
            return False
        pool = ff7nx_cave.HolePool(m.img, starts=set(m.arm_starts))
        words, entry = ff7nx_cave.emit_hooked(pool, HOOK_VA, HOOK_ORIG,
                                              cave_body())
        log('  scissor probe cave: 5 words in padding, entry +%#x' % entry)
        nso = nso_patcher.read_nso(Path(src))
        applied = nso_patcher.apply_spec(nso, {
            'name': 'scissor probe (DIAGNOSTIC)',
            'patches': [
                {'name': 'hook -> cave' if va == HOOK_VA else 'cave word',
                 'va': '0x%X' % va,
                 'expect': _hex(struct.unpack_from('<I', m.img, va)[0]),
                 'set': _hex(w)}
                for va, w in sorted(words.items())
            ],
        })
        Path(dest).write_bytes(nso_patcher.rebuild(nso))
    except Exception as exc:                                   # noqa: BLE001
        log('! scissor probe: %s' % exc)
        return False
    log('  %d word(s) verified and applied' % len(applied))
    log('  THIS IS A DIAGNOSTIC BUILD. Every scissor box is clamped to the')
    log('  middle half of the screen. Do not keep it.')
    return True


def revert(src, dest, log=print):
    import nso_patcher
    import nxmap
    m = nxmap.Main(src)
    got = _word(m.text, HOOK_VA)
    if got == HOOK_ORIG:
        log('  not installed; nothing to do')
        return True
    if (got & 0xFC000000) != 0x14000000:
        log('! +0x%07X is neither the stock word nor a branch' % HOOK_VA)
        return False
    nso = nso_patcher.read_nso(Path(src))
    nso_patcher.apply_spec(nso, {
        'name': 'remove the scissor probe',
        'patches': [{'name': 'restore mov w1, w8', 'va': '0x%X' % HOOK_VA,
                     'expect': _hex(got), 'set': _hex(HOOK_ORIG)}]})
    Path(dest).write_bytes(nso_patcher.rebuild(nso))
    log('  probe removed (its cave words are left inert)')
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().split('\n')[0])
    ap.add_argument('main')
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--revert', action='store_true')
    ap.add_argument('-o', '--out')
    a = ap.parse_args(argv)
    src = resolve_main(a.main)
    if a.apply:
        return 0 if apply_to_nso(src, a.out or src, log=print) else 1
    if a.revert:
        return 0 if revert(src, a.out or src) else 1
    show(src)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
