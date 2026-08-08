#!/usr/bin/env python3
r"""
ff7nx_swirlscale.py -- un-squeeze the battle-entry swirl.

THE SYMPTOM, AS REPORTED
========================
Entering battle, the swirl takes a freeze frame of the 16:9 picture and
SQUEEZES it into a 4:3 window, then swirls.

THE CAUSE
=========
The swirl draws a grid of textured tiles as a 2D overlay.  The 2D path uses a
hardcoded `ortho(0, 640, 480, 0)` (FINDINGS-85), and the vertex shader then
multiplies gl_Position.x by WS_SCALE = 0.75.  So the 640-unit-wide grid lands
in the central 75% of the frame -- exactly 4:3 -- while the texture it samples
is the full 16:9 render target.  Hence: the whole wide picture, squeezed.

WHY FFNx's FIX IS NOT THE FIX HERE
=================================
`ff7nx_swirl.py` transcribed FFNx's seven-constant swirl fix and it was wrong
on hardware in two visible ways, both traced to specific words:

    tile size 64 -> 85    ZOOMED IN.  [0x9A04DC] is one square tile size used
                          on BOTH axes.  Scaling it enlarges the grid
                          uniformly without touching the texture mapping.
    swirl_loop += -107    SHIFTED LEFT.  It biases the vertex x directly.
    offset x/y -> 106/64  compensate for an inset into FFNx's OWN 854-wide
                          framebuffer, which this port does not have.

FFNx widens the swirl at the SOURCE, because FFNx renders the swirl source
wide.  This port's source is already the wide render target, so the fix has to
be at the DESTINATION -- and `swirl_framebuffer_offset_y = 64`, which moves
the picture vertically, was the tell that the mechanisms did not match.

THE FIX
=======
The same shape `ff7nx_letterbox` already uses for the field fade quad: leave
the matrix alone, widen the geometry.  Scale the vertex x about the centre of
the frame by 1 / WS_SCALE = 4/3, and leave y untouched.

Working in the 2D overlay's own space, the visible frame spans game x
-106.67 .. 746.67, whose centre is 320.  So

    x' = 320 + (x - 320) * 4/3
       = (4x - 1280)/3 + 320
       = (4x - 1280 + 960)/3
       = (4x - 320) / 3

which is one shift, one subtract and one divide.  It checks at all three
corners without fitting anything:

    x =   0  ->  ( 0   - 320)/3 = -106.67   the left edge of the 16:9 frame
    x = 320  ->  (1280 - 320)/3 =  320      the centre, fixed
    x = 640  ->  (2560 - 320)/3 =  746.67   the right edge

THE SITE
========
`swirl_loop_sub_4026D4` builds each vertex as

    +0x13C34  ldrsh w8, [x21]        rot      (guest AX from the trig call)
    +0x13C44  ldr   w8, [x0]         centre x (guest 0x99F340)
    +0x13C4C  add   w8, w8, w9       rot + centre
    +0x13C5C  ldr   w8, [x0]         offset x (guest 0x9A04D8, = 0)
    +0x13C64  add   w8, w8, w9       + offset
    +0x13C68  str   w8, [x21]        <-- HOOK.  w8 is the finished x.
    ...
    +0x13CC0  ldrh  w25, [x21]       truncate to 16 bits
    +0x13CCC  strh  w25, [x0]        into the vertex array

The hook is the FINAL x, after both addends, so the scale sees the whole
coordinate and does not care what the centre global happens to hold -- which
matters, because `[0x99F340]` is `width/2 + [0x9A04B0]*25` and evaluates to
370, not 320, and I could not establish statically why.  Scaling about the
literal frame centre sidesteps that question entirely.

`0x9A04D8` is loaded exactly ONCE in this function, so the hook is unique, and
the grid is one loop, so one hook covers every tile.

The Y vertex is built by the same idiom 168 bytes later off `[0x99F33C]`, and
is deliberately not touched: only the horizontal axis is squeezed.

REGISTERS
=========
w8 carries the value.  w9 is scratch: its next definition is +0x13C94
(`ldr w9, [x21, #4]`) with no read in between, and a `bl` at +0x13C74 would
clobber it as caller-saved regardless.  Both are checked in `verify`, not
assumed.
"""
import argparse
import os
import shutil
import struct
import sys
import tempfile
from pathlib import Path

try:
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM, CS_OP_REG
    from capstone.arm64 import ARM64_OP_MEM
except ImportError:                                          # pragma: no cover
    sys.exit('need capstone:  pip install capstone --break-system-packages')

import nxmap
import a64 as A
import ff7nx_guestref as gr

SWIRLSCALE_ENV = 'SEVENTH_NX_SWIRL_SCALE'


def enabled() -> bool:
    """
    ON with 16:9, OFF at 4:3, overridable for an A/B.

    The scale is 1 / WS_SCALE.  At 4:3 WS_SCALE is 1.0, so the correction is
    not merely unnecessary -- applying 4/3 there would stretch the swirl off
    both edges of a frame that was never squeezed.
    """
    import os as _os
    v = _os.environ.get(SWIRLSCALE_ENV)
    if v is not None:
        return v not in ('', '0', 'off', 'false')
    try:
        import ff7nx_ws
        return ff7nx_ws.enabled()
    except Exception:                                          # noqa: BLE001
        return False


SWIRL_LOOP = 0x4026D4
OFFSET_X = 0x9A04D8          # the guest global whose load anchors the hook
CENTRE = 320                 # (-106.67 + 746.67) / 2, the frame centre
NUM, DEN = 4, 3              # 1 / WS_SCALE


def sdiv(rd, rn, rm):
    return (0x1AC00C00 | (rm << 16) | (rn << 5) | rd) & 0xFFFFFFFF


def sub_imm(rd, rn, imm):
    return (0x51000000 | (imm << 10) | (rn << 5) | rd) & 0xFFFFFFFF


def _md():
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    md.detail = True
    return md


def find_hook(m, md=None):
    """
    (hook_va, displaced_word, value_reg, scratch_reg) or (None, why).

    Anchored on the unique load of the swirl's x offset, then the two words
    the recompiler always emits after it.  Nothing is hard-coded.
    """
    md = md or _md()
    a, b = m.extent(SWIRL_LOOP)
    acc, _ = gr.scan(m.text, a, b, md)
    loads = [x for x in acc if x.guest == OFFSET_X and x.is_load]
    if len(loads) != 1:
        return None, ('swirl_loop has %d load(s) of 0x%X, want exactly 1'
                      % (len(loads), OFFSET_X))
    site = loads[0].addr
    seq = list(md.disasm(m.text[site: site + 16], site))
    if len(seq) < 4:
        return None, 'could not decode the four words at the anchor'
    ld, ld2, add, st = seq[:4]
    if not (ld2.mnemonic == 'ldr' and add.mnemonic == 'add'):
        return None, ('expected ldr/ldr/add after the anchor, found %s/%s'
                      % (ld2.mnemonic, add.mnemonic))
    scratch = ld2.reg_name(ld2.operands[0].reg)

    # The fourth word is the hook, and it reads DIFFERENTLY in the two states:
    # `str wN, [x21]` when stock, `b <cave>` once installed.  Keying on `str`
    # alone -- which the first version did -- makes discovery fail on exactly
    # the modules that need it most: --show reported an error and --revert
    # left the cave in place, on a module this same code had just patched.
    # Third time this asymmetry has bitten, so it is now the thing the tests
    # check first.
    if st.mnemonic == 'str':
        mem = next((o for o in st.operands if o.type == ARM64_OP_MEM), None)
        if mem is None or mem.mem.disp != 0:
            return None, 'the store is not [xN] with zero displacement'
        return (st.address,
                struct.unpack('<I', m.text[st.address:st.address + 4])[0],
                st.reg_name(st.operands[0].reg), scratch), None

    if st.mnemonic == 'b':
        # Installed.  The displaced store was carried into the cave; recover
        # it from there rather than reconstructing it, so --revert restores
        # the real bytes.
        cave, why = walk_cave(m, st.address, md)
        if cave is None:
            return None, why
        stores = []
        for va in sorted(cave):
            i = list(md.disasm(m.text[va:va + 4], va))
            if i and i[0].mnemonic == 'str':
                mem = next((o for o in i[0].operands
                            if o.type == ARM64_OP_MEM), None)
                if mem is not None and mem.mem.disp == 0:
                    stores.append((va, i[0]))
        if len(stores) != 1:
            return None, ('the cave holds %d candidate displaced store(s), '
                          'want 1' % len(stores))
        va, i = stores[0]
        return (st.address,
                struct.unpack('<I', m.text[va:va + 4])[0],
                i.reg_name(i.operands[0].reg), scratch), None

    return None, ('the hook word is %s, neither the stock store nor a branch'
                  % st.mnemonic)


def scratch_is_dead(m, hook, scratch, md=None, window=16):
    """True if `scratch` is written before it is read after the hook."""
    md = md or _md()
    for i in md.disasm(m.text[hook + 4: hook + 4 + window * 4], hook + 4):
        r, w = i.regs_access()
        if any(i.reg_name(x) == scratch for x in r):
            return False
        if any(i.reg_name(x) == scratch for x in w):
            return True
        if i.mnemonic == 'bl':          # caller-saved: clobbered anyway
            return True
    return True


def body(val_reg, scratch_reg):
    """x' = (NUM*x - CENTRE*(NUM-DEN)) / DEN, in four words."""
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


def walk_cave(m, hook, md=None, limit=64):
    """
    Every word of the installed cave, by following the branch chain.

    Returns (set_of_addresses, None) or (None, why).  The walk stops at the
    branch back into the host function, and refuses anything that leaves the
    padding it was allocated from.
    """
    md = md or _md()
    first = list(md.disasm(m.text[hook:hook + 4], hook))
    if not first or first[0].mnemonic != 'b':
        return None, 'the hook at +0x%X is not a branch' % hook
    pc = first[0].operands[0].imm
    out, host = set(), hook + 4
    for _ in range(limit):
        if pc == host:
            return out, None
        ins = list(md.disasm(m.text[pc:pc + 4], pc))
        if not ins:
            return None, 'undecodable cave word at +0x%X' % pc
        out.add(pc)
        i = ins[0]
        if i.mnemonic == 'b':
            nxt = i.operands[0].imm
            if nxt == host:
                return out, None
            pc = nxt
            continue
        pc += 4
    return None, 'the cave chain did not return to +0x%X within %d words' % (
        host, limit)


def plan(m, revert=False, md=None, pool=None):
    import ff7nx_cave
    md = md or _md()
    found, why = find_hook(m, md)
    if found is None:
        return [], [], [why]
    hook, displaced, val, scratch = found
    cur = struct.unpack('<I', m.text[hook:hook + 4])[0]
    patched = (cur != displaced)

    if revert:
        if not patched:
            return [], [], []
        # Unhooking alone is not a revert.  It would restore the displaced
        # instruction and leave eight non-zero words sitting in padding holes
        # -- which the next cave allocator refuses to reuse (it only takes
        # words that are zero), so every apply/revert cycle would quietly eat
        # more of the pool, and the module would stop being byte-exact against
        # its own baseline.  This project's rule is that a change you cannot
        # fully back out makes the next test result unattributable.
        #
        # The cave is self-describing: the hook is a `b`, and every block ends
        # in a `b`, so the chain can be walked back out of the patched image.
        cave, why2 = walk_cave(m, hook, md)
        if cave is None:
            return [], [], [why2]
        ps = [{'name': 'swirl x scale: unhook', 'va': hex(hook),
               'expect': struct.pack('<I', cur).hex(),
               'set': struct.pack('<I', displaced).hex()}]
        for va in sorted(cave):
            ps.append({'name': 'swirl x scale: clear cave word +0x%x' % va,
                       'va': hex(va),
                       'expect': struct.pack(
                           '<I', struct.unpack('<I', m.text[va:va + 4])[0]).hex(),
                       'set': '00000000'})
        return ps, ['  swirl x scale removed @ +0x%07X (%d cave word(s) '
                    'returned to the pool)' % (hook, len(cave))], []

    if patched:
        return [], ['  swirl x scale already installed @ +0x%07X' % hook], []
    if not scratch_is_dead(m, hook, scratch, md):
        return [], [], ['%s is live after +0x%X; the cave would corrupt it'
                        % (scratch, hook)]

    pool = pool or ff7nx_cave.HolePool(m.img, starts=set(m.arm_starts))
    out, entry = ff7nx_cave.emit_hooked(pool, hook, displaced,
                                        body(val, scratch))
    patches = []
    for va in sorted(out):
        old = struct.unpack('<I', m.text[va:va + 4])[0]
        if va != hook and old != 0:
            return [], [], ['cave word +0x%X is not padding (0x%08X)'
                            % (va, old)]
        patches.append({'name': 'swirl x scale +0x%x' % va, 'va': hex(va),
                        'expect': struct.pack('<I', old).hex(),
                        'set': struct.pack('<I', out[va]).hex()})
    notes = ['  swirl x scale  x -> (%dx - %d)/%d  about %d'
             % (NUM, CENTRE * (NUM - DEN), DEN, CENTRE),
             '  hook +0x%07X (%s carries x, %s scratch), cave entry +0x%X, '
             '%d word(s)' % (hook, val, scratch, entry, len(out) - 1)]
    return patches, notes, []


def apply(main, revert=False, log=print) -> int:
    import nso_patcher
    main = Path(main)
    m = nxmap.Main(str(main))
    patches, notes, problems = plan(m, revert)
    if problems:
        for p in problems:
            log('  ! ' + p)
        log('  refusing to write.')
        return 1
    for n in notes:
        log(n)
    if not patches:
        log('  nothing to do -- already in the requested state')
        return 0
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(nso, {'name': 'ff7nx_swirlscale',
                                             'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.swirlscale-')
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
    md = _md()
    found, why = find_hook(m, md)
    if found is None:
        log('  ! ' + why)
        return
    hook, displaced, val, scratch = found
    cur = struct.unpack('<I', m.text[hook:hook + 4])[0]
    log('  swirl x scale  hook +0x%07X  %s' % (hook,
        'INSTALLED' if cur != displaced else 'stock'))
    log('    x carried in %s, scratch %s' % (val, scratch))
    log('    model: x -> (%dx - %d)/%d, centre %d'
        % (NUM, CENTRE * (NUM - DEN), DEN, CENTRE))
    for x, want in ((0, -106), (CENTRE, CENTRE), (640, 746)):
        log('      x %4d -> %5d' % (x, expected(x)))


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

    log('  the model, at the three corners of the frame:')
    for x, want, why in ((0, -106, 'the left edge, game -106.67'),
                         (CENTRE, CENTRE, 'the centre, fixed'),
                         (640, 746, 'the right edge, game 746.67')):
        chk(expected(x) == want, 'x %4d -> %5d   (%s)' % (x, expected(x), why))
    chk(expected(CENTRE) == CENTRE, 'the centre is a fixed point')
    chk(expected(640) - expected(0) == 852,
        'the span becomes %d units wide (640 * 4/3 = 853, sdiv truncates)'
        % (expected(640) - expected(0)))

    log('  the hook, located from the image:')
    found, why = find_hook(m, md)
    chk(found is not None, 'the anchor resolves (%s)' % (why or 'ok'))
    if found is None:
        log('')
        log('  %d check(s) pass, %d fail' % (ok, fail))
        return 1
    hook, displaced, val, scratch = found
    chk(val != scratch, 'the value register %s and scratch %s differ'
        % (val, scratch))
    chk(scratch_is_dead(m, hook, scratch, md),
        '%s is dead after the hook' % scratch)

    # Scan a view with the hook word put back.  Once installed, the hook is a
    # `b` out of the function, so the constant-propagation walk stops dead
    # there and never reaches the Y vertex -- making the "Y is not hooked"
    # check fail on precisely the modules where it matters.  Restoring the
    # displaced word in a scratch copy keeps the structural checks meaningful
    # in both states without touching the file.
    a, b = m.extent(SWIRL_LOOP)
    view = bytearray(m.text)
    view[hook:hook + 4] = struct.pack('<I', displaced)
    acc, _ = gr.scan(bytes(view), a, b, md)
    chk(sum(1 for x in acc if x.guest == OFFSET_X and x.is_load) == 1,
        'the anchor global 0x%X is loaded exactly once' % OFFSET_X)
    chk(any(x.guest == 0x99F340 for x in acc),
        'the x centre 0x99F340 is read in this function')
    chk(any(x.guest == 0x99F33C for x in acc),
        'the y centre 0x99F33C is read too, and is NOT hooked')

    # The Y vertex is built by the IDENTICAL idiom -- trig, add centre, add
    # offset, store -- so an anchor pointed at the Y offset global resolves
    # just as cleanly and hooks the wrong axis.  Nothing above catches that:
    # the counts, the registers and the body are all equally valid there.
    # What distinguishes them is which centre feeds the addition, so that is
    # what gets asserted.
    before = [x for x in acc if hook - 0x40 <= x.addr < hook and x.is_load]
    chk(any(x.guest == 0x99F340 for x in before),
        'the X centre 0x99F340 is read in the 16 words before the hook')
    chk(not any(x.guest == 0x99F33C for x in before),
        'the Y centre 0x99F33C is NOT read there -- this is the X vertex')

    log('  the cave body, executed:')
    words = body(val, scratch)
    chk(len(words) == 4, 'the body is %d words' % len(words))
    dis = [list(md.disasm(struct.pack('<I', w), 0)) for w in words]
    chk(all(d for d in dis), 'every word decodes')
    if all(d for d in dis):
        names = [d[0].mnemonic for d in dis]
        chk(names == ['lsl', 'sub', 'mov', 'sdiv'],
            'the body is lsl/sub/mov/sdiv (%s)' % '/'.join(names))
        for d in dis:
            log('      %s %s' % (d[0].mnemonic, d[0].op_str))

    log('  the ENCODED words, executed, against the model:')
    # Interpret the actual instruction encodings.  The first version of this
    # check re-stated the intent in Python -- `v = x * 4` -- so mutating the
    # body's shift from #2 to #1 changed the cave and the check agreed with
    # it anyway.  An emulator that takes its inputs from the thing under test
    # cannot falsify it (FINDINGS-97 5.1); this one takes them from the words.
    def run_body(x):
        reg = {val: x & 0xFFFFFFFF, scratch: 0}
        for w in words:
            d = list(md.disasm(struct.pack('<I', w), 0))
            if not d:
                raise ValueError('undecodable body word 0x%08X' % w)
            i = d[0]
            ops = i.operands
            rd = i.reg_name(ops[0].reg)
            if i.mnemonic == 'lsl':
                reg[rd] = (reg[i.reg_name(ops[1].reg)] << ops[2].imm) & 0xFFFFFFFF
            elif i.mnemonic == 'sub':
                reg[rd] = (reg[i.reg_name(ops[1].reg)] - ops[2].imm) & 0xFFFFFFFF
            elif i.mnemonic in ('mov', 'movz'):
                reg[rd] = ops[1].imm & 0xFFFFFFFF
            elif i.mnemonic in ('sdiv', 'udiv'):
                n = reg[i.reg_name(ops[1].reg)]
                dv = reg[i.reg_name(ops[2].reg)]
                if i.mnemonic == 'sdiv':
                    n = n - (1 << 32) if n >> 31 else n
                    dv = dv - (1 << 32) if dv >> 31 else dv
                q = 0 if dv == 0 else (abs(n) // abs(dv)) * (1 if (n < 0) == (dv < 0) else -1)
                reg[rd] = q & 0xFFFFFFFF
            else:
                raise ValueError('unmodelled body instruction %s' % i.mnemonic)
        v = reg[val]
        return v - (1 << 32) if v >> 31 else v
    try:
        bad = [x for x in range(-200, 900) if run_body(x) != expected(x)]
        chk(not bad, 'the encoded body reproduces the model over -200..900 '
                     '(%d disagreement(s))' % len(bad))
        for x in (0, CENTRE, 640):
            chk(run_body(x) == expected(x),
                'executing the real words: x %4d -> %5d' % (x, run_body(x)))
    except ValueError as e:
        chk(False, 'the encoded body could not be executed: %s' % e)

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
