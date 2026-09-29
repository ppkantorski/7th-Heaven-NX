#!/usr/bin/env python3
"""
ff7nx_shadowdepth.py -- world-map shadows get the world's depth (BUILD 604).

THE FAULT (hardware, 2026-09-28: Ultimate Weapon over the crater's river)
A shadow shows through terrain that stands in front of it: over the water
behind the crater lip it is drawn on the lip; turning the camera spreads it
onto the land; moving back makes it pop in and out at one distance.

WHY
BUILD 591 gave every world-map draw one depth, ndc z = 0.99 (1 - 32/Z) from
its true view distance Z = 1/rhw (tlmain_vv / lmain_vv), with no far plane.
The shadows never took it. The world shadow routine (x86 0x75D544, called
from 0x75DEAA for every entity) projects its quad on the CPU and writes four
transformed vertices with rhw = 1.0 (x86 0x75DC65/0x75DD04/0x75DDA3/0x75DE42)
-- the value tlmain_vv reads as "2D, keep your own depth". So the shadow
kept the game's old z (its nearest corner through the game's projection), a
different scale from the terrain around it: which of the two is in front
depends on the distance, and the crater lip "turns transparent".

THE FIX
The routine has already picked the nearest corner and its 1/w into
[ebp-0x160] (x86 0x75DB75..0x75DBF8: vanilla uses it for the z it writes).
Its recompiled body loads the constant 1.0 once into w19 (callee-saved) at
+0xF68E90 (`mov w19, #0x3f800000`) and stores w19 as all four rhw. That one
word becomes `bl` to a 9-word cave that loads w19 = [ebp-0x160] instead.

Then tlmain_vv gives the shadow the same depth formula as the ground, from
the nearest corner's distance -- exactly vanilla's rule (one depth for the
whole quad, the nearest corner's, so it sits on slopes and water), now on the
world's scale: terrain truly in front hides it, terrain behind does not.
The quad's x/y are unchanged (the shader multiplies by w and divides it out),
and one w for all four corners keeps its texture mapping as it was.

The site is in straight-line code: x0 (the vertex address) is saved around
the call, w8 is reloaded right after, and the preceding instruction was
itself a call to g2h, so nothing else is live.

BUILD 604b: THE HIGHWIND'S SHADOW TURNED INSIDE OUT HIGH UP
The per-entity shadow setup (x86 0x75DEAA) sizes the quad as
    size = 0x64 - ((y - ground) >> 6)
with no floor. Vanilla's Highwind never flies more than 6400 above the
ground; with the 587 ceiling (30767) it does, the size goes negative and the
quad is drawn mirrored and growing. (The darkness, 0x20 - (h >> 8), IS
clamped at 0 by the game.) Its recompiled `sub w20, w9, w8` (+0xF858B0)
becomes `bl` to a 4-word cave: the same sub, then max(0, it). The shadow
shrinks to nothing at 6400 above the ground and stays gone; every size below
that is unchanged. w20 feeds only [ebp-4] and ecx; the flags are not read
before the function's own `cmp` at +0xF8593C.

SEVENTH_NX_SHADOWDEPTH=0 leaves both sites stock.
"""
import os
import struct

import a64 as A
import ff7nx_cave

ENV = 'SEVENTH_NX_SHADOWDEPTH'
SITE = 0xF68E90                               # in x86 0x75D544's body
STOCK_WORD = 0x32091BF3                       # orr w19, wzr, #0x3f800000
G2H = 0x10FC3A0
EBP_SLOT = 0x14                               # guest ebp in the context (x20)
RHW_SLOT = 0x160                              # [ebp-0x160] = 1/w of the nearest corner
# the words around the site that make the patch safe (checked before writing)
CONTEXT = {SITE - 4: 0x94064D45,              # bl g2h
           SITE + 4: 0xB9000013}              # str w19, [x0]


def enabled(env=None):
    env = os.environ if env is None else env
    return env.get(ENV, '1').strip().lower() not in ('0', 'off', 'no', 'false')


SIZE_SITE = 0xF858B0                          # in x86 0x75DEAA's body
SIZE_STOCK = 0x4B080134                       # sub w20, w9, w8  (0x64 - (h >> 6))
SIZE_CONTEXT = {SIZE_SITE - 8: 0x52800C89,    # mov w9, #0x64
                SIZE_SITE - 4: 0x13067D08,    # asr w8, w8, #6
                SIZE_SITE + 4: 0x29005268}    # stp w8, w20, [x19]
SIZE_BODY = [SIZE_STOCK,                      # sub  w20, w9, w8
             0x7100029F,                      # cmp  w20, #0
             0x1A9FC294,                      # csel w20, w20, wzr, gt
             0xD65F03C0]                      # ret


def size_body(_addr=None):
    return list(SIZE_BODY)


def body(addr):
    """The cave; addr(i) is the address of word i."""
    w = [A.stp64_pre(29, 30, 31, -32),       # stp x29, x30, [sp, #-32]!
         A.str64(0, 31, 16),                 # str x0, [sp, #16]
         A.ldr(8, 20, EBP_SLOT),             # ldr w8, [x20, #0x14]   (ebp)
         A.sub_imm(0, 8, RHW_SLOT)]          # sub w0, w8, #0x160
    w.append(A.bl(addr(len(w)), G2H))        # bl  g2h
    w += [A.ldr(19, 0, 0),                   # ldr w19, [x0]
          A.ldr64(0, 31, 16),                # ldr x0, [sp, #16]
          A.ldp64_post(29, 30, 31, 32),      # ldp x29, x30, [sp], #32
          A.ret()]
    return w


def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def _bl_target(va, w):
    if (w >> 26) != 0x25:
        return None
    imm = w & 0x3FFFFFF
    if imm & 0x2000000:
        imm -= 0x4000000
    return va + 4 * imm


def _context_ok(img):
    return (_bl_target(SITE - 4, _word(img, SITE - 4)) == G2H
            and _word(img, SITE + 4) == CONTEXT[SITE + 4])


def _walk(img, va, n):
    """The n words of a cave laid out by ff7nx_cave (runs joined by `b`).
    The body's only branch is a `bl`, never a plain `b`."""
    out = []
    guard = 0
    while len(out) < n and guard < 4 * n:
        guard += 1
        w = _word(img, va)
        if (w >> 26) == 0x05:
            imm = w & 0x3FFFFFF
            if imm & 0x2000000:
                imm -= 0x4000000
            va += 4 * imm
            continue
        out.append((va, w))
        va += 4
    return out


def cave_ok(img, entry):
    got = _walk(img, entry, len(body(lambda i: 4 * i)))
    if not got:
        return False
    addrs = {i: a for i, (a, _w) in enumerate(got)}
    return [w for _a, w in got] == body(lambda i: addrs[i])


def _size_context_ok(img):
    return all(_word(img, a) == w for a, w in SIZE_CONTEXT.items())


def depth_state(img):
    w = _word(img, SITE)
    if w == STOCK_WORD:
        return 'stock'
    tgt = _bl_target(SITE, w)
    if tgt is not None and cave_ok(img, tgt):
        return 'on'
    return 'unknown'


def size_state(img):
    w = _word(img, SIZE_SITE)
    if w == SIZE_STOCK:
        return 'stock'
    tgt = _bl_target(SIZE_SITE, w)
    if tgt is not None and [x for _a, x in _walk(img, tgt, len(SIZE_BODY))] == SIZE_BODY:
        return 'on'
    return 'unknown'


def read_state(img):
    """'on' (both), 'stock' (both), 'depth-only' (the 604 build), else
    'unknown'."""
    d, z = depth_state(img), size_state(img)
    if d == z and d in ('on', 'stock'):
        return d
    if d == 'on' and z == 'stock':
        return 'depth-only'
    return 'unknown'


def build_patches(img, starts=None, log=lambda *_: None):
    """The words for whichever of the two sites is still stock."""
    pool = ff7nx_cave.HolePool(bytearray(img), starts=starts)
    words = {}
    if depth_state(img) == 'stock':
        if not _context_ok(img):
            raise ValueError('the shadow rhw site +0x%X is not the expected '
                             'mov w19, #1.0 between a g2h call and str w19, [x0]'
                             % SITE)
        entry, cave = ff7nx_cave.emit_laid_out(pool, lambda _e, addr: body(addr))
        words.update(cave)
        words[SITE] = A.bl(SITE, entry)
        log('  shadowdepth cave: %d words in verified padding, entry +0x%X'
            % (len(cave), entry))
    if size_state(img) == 'stock':
        if not _size_context_ok(img):
            raise ValueError('the shadow size site +0x%X is not the expected '
                             'mov w9, #0x64 ; asr w8, w8, #6 ; sub w20, w9, w8'
                             % SIZE_SITE)
        entry, cave = ff7nx_cave.emit_laid_out(pool, lambda _e, addr: size_body(addr))
        words.update(cave)
        words[SIZE_SITE] = A.bl(SIZE_SITE, entry)
        log('  shadow size floor cave: %d words in verified padding, entry +0x%X'
            % (len(cave), entry))
    return words


def apply_to_nso(src, dest, log=lambda *_: None):
    """Write `dest` from `src`. False when nothing was written."""
    from pathlib import Path as _P
    import nxmap
    import nso_patcher
    module = nxmap.Main(str(src))
    state = read_state(module.img)
    if state == 'on':
        log('  shadowdepth: already on; nothing to write')
        return False
    if state not in ('stock', 'depth-only'):
        log('! shadowdepth: +0x%X is %08X and +0x%X is %08X -- neither stock '
            'nor this build\'s calls; nothing was written'
            % (SITE, _word(module.img, SITE), SIZE_SITE,
               _word(module.img, SIZE_SITE)))
        return False
    try:
        words = build_patches(module.img, set(module.arm_starts), log)
    except Exception as exc:                                  # noqa: BLE001
        log('! shadowdepth: %s: %s' % (type(exc).__name__, exc))
        log('  nothing was written; the module is unchanged')
        return False

    def hx(v):
        return ' '.join('%02X' % b for b in struct.pack('<I', v))
    spec = {'name': 'shadowdepth: world shadows take the world depth',
            'patches': [{'name': '+0x%X' % va, 'va': hex(va),
                         'expect': hx(_word(module.img, va)),
                         'set': hx(words[va])} for va in sorted(words)]}
    nso = nso_patcher.read_nso(_P(str(src)))
    nso_patcher.apply_spec(nso, spec)
    data = nso_patcher.rebuild(nso)
    os.makedirs(os.path.dirname(os.path.abspath(str(dest))), exist_ok=True)
    with open(str(dest), 'wb') as f:
        f.write(data)
    log('  shadowdepth: world-map shadows carry the nearest corner\'s 1/w, so '
        'tlmain_vv gives them the terrain\'s depth (terrain in front hides them);'
        ' a shadow\'s size stops at 0 instead of turning inside out high up')
    return True


def main(argv):
    """Try it on a BUILT main without a rebuild:
        python3 ff7nx_shadowdepth.py <exefs/main> --on | --off | --show"""
    import shutil
    import sys
    if len(argv) < 3 or argv[2] not in ('--on', '--off', '--show'):
        print(main.__doc__)
        return 2
    import nxmap
    path, backup = argv[1], argv[1] + '.pre-shadowdepth'
    if argv[2] == '--show':
        print('  shadowdepth is %s' % read_state(nxmap.Main(path).img))
        return 0
    if argv[2] == '--off':
        if not os.path.exists(backup):
            print('  no %s -- rebuild instead' % os.path.basename(backup))
            return 1
        shutil.copyfile(backup, path)
        os.remove(backup)
        print('  shadowdepth -> off')
        return 0
    if os.path.exists(backup):
        print('  already on (%s exists); --off first' % os.path.basename(backup))
        return 1
    if read_state(nxmap.Main(path).img) == 'on':
        print('  already on')
        return 1
    shutil.copyfile(path, backup)
    tmp = path + '.shadowdepth-tmp'
    if not apply_to_nso(backup, tmp, print):
        os.remove(backup)
        return 1
    os.replace(tmp, path)
    print('  shadowdepth -> on')
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main(sys.argv))
