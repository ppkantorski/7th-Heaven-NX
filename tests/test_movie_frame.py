#!/usr/bin/env python3
"""
test_movie_frame.py -- the movie-frame divider cave.

    python3 test_movie_frame.py

WHAT IT IS FOR
==============
FF7 drives scripted cutscene events off `get_movie_frame` -- a FRAME INDEX,
with every vanilla movie at 15 fps. The opening's music cue is a hardcoded
`cmp edx, 0x6E0` in the field loop; flevel scripts time their model overlays
the same way. A 30 fps replacement makes that counter run twice as fast and
every cue fires at half its intended moment.

The cave divides the counter so the game keeps seeing 15 fps numbering while
the picture runs at 30.

WHAT IS CHECKED
===============
The ACTUAL ENCODED WORDS are executed in arm64emu.py with the port's native
dispatcher modelled as a callback that writes a frame index into guest EAX,
exactly as the real one does. Then: the value the game reads back, that the
stack is balanced, that control returns to the caller rather than the
dispatcher, and that nothing outside guest EAX is disturbed.

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
import tempfile

import a64 as A
import arm64emu
import ff7nx_dispatch as D

CAVE = 0x1152EC8
CTX = 0x900000000          # where the fake guest register context sits
RETURN_TO = 0x947D1C       # the real caller's next instruction
STACK = 0x800000000

FAIL = []


def check(cond, msg):
    print(('  ok    ' if cond else '  FAIL  ') + msg)
    if not cond:
        FAIL.append(msg)


def run(words, native_returns, shift_ok=True):
    """
    Execute the cave once. `native_returns` is what the port's native
    get_movie_frame leaves in guest EAX.

    Returns (value the caller reads, exit address, cpu, mem).
    """
    mem = arm64emu.Mem()
    mem.setu(D.GUEST_CTX_SLOT, CTX, 8)          # ctx pointer, as relocated
    mem.setu(CTX, 0xDEADBEEF, 4)                # EAX, overwritten by the call
    mem.setu(CTX + 4, 0x5A5A5A5A, 4)            # ECX -- must not be touched
    mem.setu(CTX + 0x10, 0x11223344, 4)         # guest ESP -- likewise

    def native(cpu):
        """Stand-in for the dispatcher at 0xA510: it writes the frame index
        into guest EAX and clobbers the caller-saved registers on the way."""
        cpu.mem.setu(CTX, native_returns, 4)
        for r in list(range(0, 19)) + [30]:
            if r not in (30,):
                cpu.x[r] = arm64emu.GARBAGE

    cpu = arm64emu.Cpu(mem, native={D.MOVIE_FRAME_DISPATCH: native})
    cpu.sp = STACK
    cpu.x[30] = RETURN_TO
    for r in range(30):
        cpu.x[r] = arm64emu.GARBAGE
    exit_pc = cpu.run(CAVE, words)
    return mem.u(CTX, 4), exit_pc, cpu, mem


print('the stub this replaces\n')
check(D.MOVIE_FRAME_TAILCALL == D.MOVIE_FRAME_STUB + 8,
      'the hooked word is the third of the three-word stub')
check(D.GUEST_EAX_OFF == 0,
      'guest EAX is at context offset 0, where both call sites read it')

words = D.build_movie_frame_cave(CAVE)
print('\ncave: %d words, %d bytes\n' % (len(words), 4 * len(words)))

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
decoded = list(md.disasm(struct.pack('<%dI' % len(words), *words), CAVE))
check(len(decoded) == len(words),
      'every word decodes (%d of %d)' % (len(decoded), len(words)))
check(decoded[-1].mnemonic == 'ret',
      'it ends in `ret`, turning the stub\'s tail call into a real one')
bls = [i for i in words if (i & 0xFC000000) == 0x94000000]
check(len(bls) == 1, 'exactly one bl -- to the native dispatcher')

print('\nhalving, over the whole range a movie frame can take\n')
cases = [0, 1, 2, 3, 241, 242, 483, 484, 1759, 1760, 3519, 3520,
         0x7FFE, 0x7FFF, 0x8000, 0xFFFE, 0xFFFF, 0x10000, 0x7FFFFFFF,
         0xFFFFFFFF]
bad = 0
for v in cases:
    got, exit_pc, cpu, mem = run(words, v)
    want = v >> 1
    if got != want:
        check(False, 'native returned %d -> game saw %d, wanted %d'
              % (v, got, want))
        bad += 1
check(bad == 0, 'all %d values halved exactly (0 -> 0, 3520 -> 1760, '
                '0xFFFFFFFF -> 0x7FFFFFFF)' % len(cases))

print('\nthe cue that was firing early\n')
got, _e, _c, _m = run(words, 3520)
check(got == 1760,
      'a 30 fps opening at decoded frame 3520 reads as 1760 -- the exact '
      'value the field loop compares against')
got, _e, _c, _m = run(words, 1760)
check(got == 880,
      'and at 1760 (halfway, where it used to fire) it reads 880, so the '
      'cue does NOT trigger')

print('\nmachine state\n')
got, exit_pc, cpu, mem = run(words, 242)
check(exit_pc == RETURN_TO,
      'returns to the caller (0x%X), not into the dispatcher' % RETURN_TO)
check(cpu.sp == STACK, 'stack pointer balanced (0x%X)' % cpu.sp)
check(mem.u(CTX + 4, 4) == 0x5A5A5A5A,
      'guest ECX untouched -- only EAX is rewritten')
check(mem.u(CTX + 0x10, 4) == 0x11223344,
      'guest ESP untouched -- the dispatcher owns that, not us')
check(mem.u(D.GUEST_CTX_SLOT, 8) == CTX,
      'the context pointer slot is read, never written')

print('\nmutations (each must be caught)\n')


def no_divide(w):
    return [x for x in w if x != A.lsr(17, 17, 1)]


def divide_by_four(w):
    return [A.lsr(17, 17, 2) if x == A.lsr(17, 17, 1) else x for x in w]


def no_writeback(w):
    return [x for x in w if x != A.str_(17, 16, 0)]


def sixteen_bit(w):
    # strh instead of str: the world-map caller reads EAX as 32 bits, so a
    # 16-bit write leaves the top half stale
    return [A.strh(17, 16, 0) if x == A.str_(17, 16, 0) else x for x in w]


caught = 0
muts = [('divide removed', no_divide, 484, 242),
        ('divided by 4 instead of 2', divide_by_four, 484, 242),
        ('result never written back', no_writeback, 484, 242),
        ('written back 16 bits wide', sixteen_bit, 0x20000, 0x10000)]
for name, fn, feed, want in muts:
    broken = fn(list(words))
    try:
        got, _e, _c, _m = run(broken, feed)
        differs = (got != want)
    except Exception:
        differs = True
    if differs:
        print('  ok    caught: %s' % name)
        caught += 1
    else:
        print('  FAIL  NOT caught: %s' % name)
        FAIL.append(name)
print('\n  %d/%d mutations caught' % (caught, len(muts)))

print('\nit must never turn itself on\n')
# The round-9 failure mode: a brand-new code-injecting group riding into the
# default preset. `--enable-all` derives from the set of KNOWN group names, so
# simply registering the name was enough to enable it everywhere -- the first
# build after doing so came out identical whether the feature was requested or
# not. It is excluded explicitly, and that exclusion is checked here.
import ff7nx_60fps as F

# The shipping preset's module hash. It moved from
# 5605e053b6937102d813f389b338422f when `tifa-slots` joined the preset --
# the two-word Tifa reel patch (see test_tifa_slots.py, which proves the
# delta between the two presets is exactly those two words and nothing
# else). This constant is the regression anchor for everything BELOW it;
# it is only ever updated alongside a test that accounts for the change.
PRESET_MAIN_MD5 = 'fc1a7cc3657a1fb47d765da0f51a59dd'

check(F.MOVIE_FPS_GROUP not in F.RECOMMENDED_ENABLE,
      'not in the shipping preset')


HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(HERE, 'ff7_en')
NSO = os.path.join(HERE, 'dump', 'exefs', 'main')
if os.path.exists(EXE) and os.path.exists(NSO):
    import hashlib
    import shutil as _sh

    def build_main(extra):
        out = tempfile.mkdtemp()
        argv = F.recommended_argv(out, exe=EXE, nso=NSO)
        if extra:
            argv = [a + ',' + extra if k and argv[k - 1] == '--enable' else a
                    for k, a in enumerate(argv)]
        F.run(argv, log=lambda *a: None)
        path = os.path.join(out, 'atmosphere', 'contents',
                            '0100A5B00BDC6000', 'exefs', 'main')
        h = hashlib.md5(open(path, 'rb').read()).hexdigest()
        _sh.rmtree(out, ignore_errors=True)
        return h

    off = build_main(None)
    on = build_main(F.MOVIE_FPS_GROUP)
    check(off != on,
          'asking for it changes the module (%s -> %s)' % (off[:8], on[:8]))
    check(off == PRESET_MAIN_MD5,
          'and NOT asking reproduces the known-good module exactly (%s) -- '
          'this is the check that caught the group enabling itself through '
          '--enable-all' % off)

print('\n%s' % ('FAILED: %d' % len(FAIL) if FAIL else 'all checks passed'))
sys.exit(1 if FAIL else 0)
