#!/usr/bin/env python3
"""
test_movie_update.py -- the movie-update multiplier cave.

    python3 test_movie_update.py

WHAT IT IS FOR
==============
On Switch, x86 `update_movie_sample` (0x417F39) is not recompiled: it is a
sixteen-byte native stub that tail-calls the trampoline at 0xA510, which
dispatches id 0xB00B0004 to the port's own `fw_movie_update` at 0x10F1590.

`fw_movie_update` BLOCKS until one new decoded frame is ready, and the field
calls it once per loop iteration, so THE FIELD TICKS ONCE PER MOVIE FRAME.
With the movie set emplaced at 30 fps that is twice vanilla, and every
per-tick clock in the field doubles with it: `WAIT` countdowns, the MVIEF
poll count, `MOVE` steps, animation. `movie-fps` corrects the frame counter
and `movie-poll` corrected the poll count; neither touches `WAIT`, which is
what times `md1stin`'s guards onto the opening FMV.

The cave consumes the extra decoded frames inside the stub so the guest sees
one update per vanilla-rate frame.

WHAT IS CHECKED
===============
The ACTUAL ENCODED WORDS are executed in arm64emu.py with BOTH the native
function and the trampoline modelled as callbacks -- the trampoline including
the guest-ESP pop it performs, which is the thing that makes a naive "call
the stub twice" wrong. Then: how many frames were consumed, that the guest
stack was popped exactly once, that the id handed to the trampoline is
correct, that x30 survives, and that the cave returns to the real caller.

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

CAVE = 0x1152EE8
RETURN_TO = 0x947D1C           # a translated caller's next instruction
STACK = 0x800000000
GUEST_ESP = 0x11223344

FAIL = []


def check(cond, msg):
    print(('  ok    ' if cond else '  FAIL  ') + msg)
    if not cond:
        FAIL.append(msg)


def run(words):
    """
    Execute the cave once.

    Returns (frames_consumed, guest_esp_after, ids_seen, exit_pc, cpu).

    `frames_consumed` counts EVERY entry into fw_movie_update -- the extra
    ones the cave makes plus the one the trampoline dispatches -- because
    each entry consumes exactly one decoded frame from the player.
    """
    mem = arm64emu.Mem()
    state = {'frames': 0, 'esp': GUEST_ESP, 'ids': [], 'lr': [],
             'entry_lr': []}

    def native(cpu):
        """fw_movie_update: consumes one decoded frame, returns 1, and
        clobbers the caller-saved registers like any AAPCS callee."""
        state['frames'] += 1
        state['lr'].append(cpu.x[30])
        for r in range(0, 18):
            cpu.x[r] = arm64emu.GARBAGE
        cpu.x[0] = 1

    def trampoline(cpu):
        """0xA510: dispatch by id, then pop the guest return address.
        Modelled faithfully because popping it twice is the failure this
        cave exists to avoid."""
        state['entry_lr'].append(cpu.x[30])
        state['ids'].append(cpu.x[0] & 0xFFFFFFFF)
        state['frames'] += 1
        state['esp'] += 4
        for r in range(0, 18):
            cpu.x[r] = arm64emu.GARBAGE

    cpu = arm64emu.Cpu(mem, native={D.MOVIE_UPDATE_NATIVE: native})
    cpu.sp = STACK
    cpu.x[30] = RETURN_TO
    for r in range(30):
        cpu.x[r] = arm64emu.GARBAGE
    exit_pc = cpu.run(CAVE, words)
    # The cave leaves by BRANCHING to the trampoline, so the emulator stops
    # there rather than stepping into it. Run the model by hand, exactly as
    # hardware would, and only when control actually arrived there -- a cave
    # that fell out somewhere else must not be credited with the dispatch.
    if exit_pc == D.MOVIE_UPDATE_DISPATCH:
        trampoline(cpu)
    return (state['frames'], state['esp'], state['ids'], exit_pc, cpu,
            state['entry_lr'])


print('the stub this replaces\n')
check(D.MOVIE_UPDATE_TAILCALL == D.MOVIE_UPDATE_STUB + 8,
      'the hooked word is the third of the three-word stub')
check(D.MOVIE_UPDATE_ID == 0xB00B0004,
      'the id is 0xB00B0004 -- group 0xB slot 4, fw_movie_update')

HERE = os.path.dirname(os.path.abspath(__file__))
NSO = os.path.join(HERE, 'dump', 'exefs', 'main')
if os.path.exists(NSO):
    import nxmap
    m = nxmap.Main(NSO)
    a, b = m.extent(0x417F39)
    check((a, b - a) == (D.MOVIE_UPDATE_STUB, 16),
          'x86 0x417F39 really is a 16-byte body at 0x%X -- a stub, not a '
          'recompiled function' % D.MOVIE_UPDATE_STUB)
    got = struct.unpack('<3I', m.text[a:a + 12])
    check(got == (0x52800080, 0x72B60160, 0x17FF37AA),
          'and its three words are mov/movk/b as the site signature asserts')
    check(struct.unpack('<I', m.text[D.MOVIE_UPDATE_DISPATCH:
                                     D.MOVIE_UPDATE_DISPATCH + 4])[0]
          == 0xF81E0FF3,
          'the trampoline at 0xA510 begins with `str x19, [sp, #-0x20]!`')

words = D.build_movie_update_cave(CAVE)
print('\ncave: %d words, %d bytes\n' % (len(words), 4 * len(words)))

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
decoded = list(md.disasm(struct.pack('<%dI' % len(words), *words), CAVE))
check(len(decoded) == len(words),
      'every word decodes (%d of %d)' % (len(decoded), len(words)))
check(decoded[-1].mnemonic == 'b',
      'it ends in `b`, keeping the stub\'s tail call a tail call')
bls = [i for i in words if (i & 0xFC000000) == 0x94000000]
check(len(bls) == 1,
      'exactly one bl at the shipping ratio -- one EXTRA native call')

print('\nframes consumed per guest call\n')
frames, esp, ids, exit_pc, cpu, state_lr = run(words)
check(frames == 2,
      'two decoded frames consumed per guest update, so the field ticks '
      'once per vanilla-rate frame (got %d)' % frames)

for ratio in (2, 4, 8):
    w = D.build_movie_update_cave(CAVE, ratio - 1)
    f = run(w)[0]
    check(f == ratio,
          'ratio %d consumes %d frames' % (ratio, f))

print('\nthe guest stack -- the reason this is not just "call the stub twice"'
      '\n')
check(esp == GUEST_ESP + 4,
      'the guest return address is popped EXACTLY once (0x%X -> 0x%X). '
      'Re-entering 0xA510 for the extra frame would pop it twice and '
      'corrupt the translated caller.' % (GUEST_ESP, esp))
check(ids == [D.MOVIE_UPDATE_ID],
      'the trampoline runs once, with the right id (%s)'
      % ', '.join('0x%08X' % i for i in ids))

print('\nmachine state\n')
check(exit_pc == D.MOVIE_UPDATE_DISPATCH,
      'control leaves through the trampoline at 0x%X'
      % D.MOVIE_UPDATE_DISPATCH)
check(state_lr and state_lr[0] == RETURN_TO,
      'x30 still holds the caller (0x%X) when the trampoline is entered, so '
      'its `ret` lands in the right place' % RETURN_TO)
check(cpu.sp == STACK, 'stack pointer balanced (0x%X)' % cpu.sp)

print('\nmutations (each must be caught)\n')


def no_extra_call(w):
    return [x for x in w if (x & 0xFC000000) != 0x94000000]


def two_extra_calls(w):
    out = []
    for x in w:
        out.append(x)
        if (x & 0xFC000000) == 0x94000000:
            out.append(x)
    return out


def no_lr_save(w):
    return [x for x in w
            if x not in (A.stp64_pre(29, 30, A.SP, -16),
                         A.ldp64_post(29, 30, A.SP, 16))]


def wrong_id(w):
    return [A.movz(0, 5) if x == A.movz(0, D.MOVIE_UPDATE_ID & 0xFFFF) else x
            for x in w]


def id_not_rebuilt(w):
    return [x for x in w
            if x not in (A.movz(0, D.MOVIE_UPDATE_ID & 0xFFFF),
                         A.movk_hi(0, (D.MOVIE_UPDATE_ID >> 16) & 0xFFFF))]


def extra_via_trampoline(w):
    # the tempting wrong version: reuse 0xA510 for the extra frame
    out = []
    for x in w:
        out.append(A.bl(0, 0) | (((D.MOVIE_UPDATE_DISPATCH - CAVE - 4 * len(out)) >> 2) & 0x3FFFFFF)
                   if (x & 0xFC000000) == 0x94000000 else x)
    return out


def baseline(w):
    f, e, i, x, c, lr = run(w)
    return (f, e, tuple(i), x, tuple(lr), c.sp)


good = baseline(words)
muts = [('extra native call removed', no_extra_call),
        ('two extra calls instead of one', two_extra_calls),
        ('x30 not saved across the call', no_lr_save),
        ('wrong stub id handed to the trampoline', wrong_id),
        ('id not rebuilt after the call clobbers w0', id_not_rebuilt),
        ('extra frame taken through 0xA510 (double ESP pop)',
         extra_via_trampoline)]
caught = 0
for name, fn in muts:
    broken = fn(list(words))
    try:
        differs = (baseline(broken) != good)
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
import ff7nx_60fps as F

# The shipping preset's module hash. It moved from
# 5605e053b6937102d813f389b338422f when `tifa-slots` joined the preset --
# the two-word Tifa reel patch (see test_tifa_slots.py, which proves the
# delta between the two presets is exactly those two words and nothing
# else). This constant is the regression anchor for everything BELOW it;
# it is only ever updated alongside a test that accounts for the change.
PRESET_MAIN_MD5 = 'fc1a7cc3657a1fb47d765da0f51a59dd'
# The same preset plus `movie-fps`. Moved from
# 7ce9df2cba9e53171b6736c3fd3bede8 for the same reason and by the same
# two words.
PRESET_PLUS_MOVIE_FPS_MD5 = '631f5130c0925747dc285d10cd7937c9'

check(F.MOVIE_UPDATE_GROUP not in F.RECOMMENDED_ENABLE,
      'not in the shipping preset')

print('\nmovie-poll is now an over-correction and must be refused\n')
try:
    F.run(['--exe', '/nonexistent', '--nso', '/nonexistent', '--out', '/tmp/x',
           '--enable', '%s,%s' % (F.MOVIE_POLL_GROUP, F.MOVIE_UPDATE_GROUP)],
          log=lambda *a: None)
except SystemExit:
    pass
src = open(nxpaths.src_file('ff7nx_60fps.py')).read()
check('MOVIE_POLL_GROUP in want and MOVIE_UPDATE_GROUP in want' in src,
      'ff7nx_60fps.py refuses movie-poll together with movie-update')
build_src = open(nxpaths.src_file('build.py')).read()
# HARDWARE RESULT, 2026-07-28: with this group on, the picture correctly
# dropped to 15 fps and md1stin's early model overlay DID NOT MOVE. The field
# tick rate is therefore NOT what times it, and shipping this group by default
# is a regression in the only way the operator can see. It stays available by
# name for bisecting; build.py must not enable it.
check('fps.MOVIE_UPDATE_GROUP' not in build_src,
      'build.py does NOT ship movie-update -- the hardware test said it does '
      'not help, and it costs half the FMV frame rate')

EXE = os.path.join(HERE, 'ff7_en')
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

    print('\nthe OFF path must not move\n')
    off = build_main(None)
    check(off == PRESET_MAIN_MD5,
          'the shipping preset still reproduces the known-good module (%s)'
          % off)
    fps_only = build_main(F.MOVIE_FPS_GROUP)
    check(fps_only == PRESET_PLUS_MOVIE_FPS_MD5,
          'movie-fps alone still reproduces its known module (%s)' % fps_only)
    both = build_main('%s,%s' % (F.MOVIE_FPS_GROUP, F.MOVIE_UPDATE_GROUP))
    check(both not in (off, fps_only),
          'and the shipping FMV combination is a distinct module (%s)'
          % both)
    print('\n  shipping 30 FPS FMV module: %s' % both)

print('\n%s' % ('FAILED: %d' % len(FAIL) if FAIL else 'all checks passed'))
sys.exit(1 if FAIL else 0)
