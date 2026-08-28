#!/usr/bin/env python3
"""Make the SILENT failures crash where they happen, and switch off the one
that is a crash all by itself. No rebuild.

    python3 probe_allocfail.py                 # show what is live
    python3 probe_allocfail.py --gl-quiet      # THE CANDIDATE FIX
    python3 probe_allocfail.py --gl-loud       # put it back
    python3 probe_allocfail.py --arm           # plant the diagnostic traps
    python3 probe_allocfail.py --disarm        # take them out again

THE END-OF-FRAME ERROR REPORTER  (read this first)
--------------------------------------------------
Traps #1 and #2 were armed on hardware and NEITHER FIRED, so no allocation
is failing -- not the guest heap, not the field alias page. Combined with
+128 MB of graphics pool changing nothing, this was never a memory problem.

Following the frame chain instead: the renderer's vtable is the 60-entry
table at `0x12CCAE0` (the only run of that length in the module's
relocations). `gfx_drv_flip` ends with

    +10DAB50  ldr x0, [x21]        ; the renderer
    +10DAB54  bl  #0x1132190       ; -> vtable +0x40  == 0x1136620
    +10DAB58                       ; <- ReturnAddress[04] in every report

`0x1136620` calls `this->vtable[0x180](...)` and then TAIL-CALLS
`0x113C6A0`, which has exactly one caller in the whole module -- that
branch. Tail calls push no frame, so `0x113C6A0` inherits the return address
`+0x10DAB58`, which is why every crash report points there.

What `0x113C6A0` does, in full:

    0113C6C0  bl glBindFramebuffer(GL_DRAW_FRAMEBUFFER /*0x8CA9*/, 0)
    0113C6C4  bl glGetError            <- gate 1, reports line 307
    0113C758  bl glBlitFramebuffer(0,0,w,h, 0,0,w,h, COLOR_BUFFER_BIT, NEAREST)
    0113C75C  bl glGetError            <- gate 2, reports line 309
    0113C7C8  bl glBindFramebuffer(GL_DRAW_FRAMEBUFFER, *(0x3FEB738))
    0113C7CC  bl glGetError            <- gate 3, reports line 311

with `w`/`h` read from `*(0x3FEB730)` / `*(0x3FEB734)`. The middle call is
the PRESENTATION BLIT. Each gate is `bl glGetError; cbz w0, <skip>`, and the
branch it guards formats

    "[OpenGL][ERROR] %s (0x%04X)"
    "\tin %s, l.%d"
    "C:/SQEX/MaterialSX/BaseEngine/Platform/NX/UtilityPlatform.cpp"

through a lazily-built `std::map` at `0x1137C30` and a logger at
`0x1119750`. That is a debug reporter left in a retail build, and it only
runs when a GL error is actually present -- rare, non-fatal, state-dependent,
and equally possible in a field or on the world map.

A CORRECTION THAT COST A HARDWARE TEST
--------------------------------------
The first version of `--gl-quiet` patched gate 1 only. That was wrong.
All three gates report from the same function with the same inherited
return address, so a crash in gate 2 or gate 3 is INDISTINGUISHABLE in the
crash report from a crash in gate 1. The run that came back "still crashes"
therefore proved nothing about the reporter -- it only proved gate 1 was not
the one firing.

`--gl-quiet` now replaces `bl glGetError` with `mov w0, wzr` at ALL THREE
gates, so every `cbz` takes its skip and no part of the reporter can run.
`show` prints each gate separately and says PARTIAL if they disagree, so
this cannot be half-applied again without saying so.

  * crash goes away  -> the reporter was the crash. Something in the frame
    is still producing a GL error and that is the next question, but the
    game survives it as the engine would have without this reporter.
  * crash unchanged  -> the whole reporter is exonerated, and the fault is
    `this` being bad at +0x1136628, which is a different problem.

Traps #3, #4 and #5 answer the same question from the other side: a `brk` on
each of the three error branches, reachable only when that specific GL call
has errored. Arm them INSTEAD of `--gl-quiet` (the fix makes them
unreachable) to find out WHICH call is failing rather than whether skipping
it helps.

WHY (the original traps)
------------------------
Every crash report from this build is the same single path: a CPU fault
inside the renderer's end-of-frame call, reached from `gfx_drv_flip`
(+0x10DAB54). That is the SYMPTOM. The report cannot name the instruction
that faulted, because nnSdk's `UserExceptionHandler` swallows the original
Data Abort and re-raises it as a break, so all we get is the frame chain.

What it CAN name is a `brk` we planted ourselves: the crash report's
Exception Address lands exactly on it, and the Break Reason carries our
immediate. So instead of guessing which resource went missing, we make the
places where a resource can go missing SILENTLY announce themselves.

Each trap sits on a path that is only reachable when an allocation has
already failed. On a healthy run they never execute and the game behaves
exactly as it does today, byte for byte.

THE TRAPS
---------
**#1  brk #0xA11  at +0x10EE8D4 -- the guest heap returned NULL.**

    +10EE8C8  ldr w19, [x22, #0x1c]
    +10EE8CC  cbnz w19, #0x10ee894     ; keep walking the free list
    +10EE8D0  mov  w0, w20             ; the heap descriptor
    +10EE8D4  nop                      ; <- HERE
    +10EE8D8  mov  w19, wzr            ; return NULL

    Stock has `bl #0x10EE660` here -- FF7's heap dump, which `fopen`s a
    Windows path and makes nnSdk abort. `ff7nx_heap.NO_HEAP_DUMP` replaced
    it with a NOP so `HeapAlloc` returns NULL like Win32 does. That was the
    right call, but it also means we can no longer SEE an allocation fail.
    This puts the visibility back without putting the abort back.

    This matters because the heap is FIRST FIT with no compaction, and this
    build asks it for 0x120000-byte contiguous blocks per 768px page. "After
    playing for a bit" is what fragmentation looks like, and raising the
    heap 64 -> 256 MB would not fix fragmentation.

**#2  brk #0xA12  at +0x10DC678 -- a field alias page could not be
allocated, and the code is about to memcpy to NULL.**

    +10DC62C  bl   #0xa060             ; allocate the alias page
    +10DC630  str  w0, [x24, #4]
    +10DC634  cbz  w0, #0x10dc678      ; failed ->
    ...
    +10DC678  mov  x21, xzr            ; <- HERE. destination = NULL
    +10DC67C  ldr  w0, [x26, #4]
    +10DC684  mov  x1, xzr
    +10DC688  mov  w2, #0x10000        ; length
    +10DC68C  mov  x0, x21             ; DESTINATION IS NULL
    +10DC690  bl   #0x1150f70          ; memcpy(NULL, src, 0x10000)

    That is an unconditional write to address zero on a failed allocation,
    in the port's own native `field_load_textures_helper`. It is a real bug
    whether or not it is the bug we are chasing -- but note it would fault
    HERE, at +0x10DC690, not at the flip, so if the trap never fires this
    path is not what we are looking at either. Worth knowing both ways.

READING THE RESULT
------------------
If the next crash report's `Exception Address` is `MaterialSX.nss + 0x10ee8d4`
or `+ 0x10dc678`, that trap fired and we have the cause. If it is the same
old `nnSdk + 0x37eba8` with `ReturnAddress[04] = MaterialSX.nss + 0x10dab58`,
neither fired -- and that ELIMINATES both the guest heap and the field alias
page, which is worth just as much.

The traps turn conditions that currently produce black squares or a missing
page into a hard crash. That is the point of a diagnostic build; take them
out again with `--disarm` when the question is answered.

Only `exefs/main` is touched. A backup is kept as `main.pre-allocprobe`.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import hashlib
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import types


def ensure_lz4():
    try:
        import lz4.block  # noqa: F401
        return
    except ImportError:
        pass
    library = ctypes.util.find_library('lz4')
    if not library:
        raise SystemExit('need python-lz4 or a system liblz4 installation')
    lib = ctypes.CDLL(library)
    lib.LZ4_decompress_safe.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                                        ctypes.c_int, ctypes.c_int]
    lib.LZ4_decompress_safe.restype = ctypes.c_int
    lib.LZ4_compressBound.argtypes = [ctypes.c_int]
    lib.LZ4_compressBound.restype = ctypes.c_int
    lib.LZ4_compress_default.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                                         ctypes.c_int, ctypes.c_int]
    lib.LZ4_compress_default.restype = ctypes.c_int
    block = types.ModuleType('lz4.block')

    def decompress(data, uncompressed_size):
        out = ctypes.create_string_buffer(uncompressed_size)
        n = lib.LZ4_decompress_safe(data, out, len(data), uncompressed_size)
        if n < 0:
            raise RuntimeError('LZ4 decompression failed (%d)' % n)
        return out.raw[:n]

    def compress(data, store_size=False, **_kw):
        if store_size:
            raise RuntimeError('the NSO fallback requires raw LZ4 blocks')
        cap = lib.LZ4_compressBound(len(data))
        out = ctypes.create_string_buffer(cap)
        n = lib.LZ4_compress_default(data, out, len(data), cap)
        if n <= 0:
            raise RuntimeError('LZ4 compression failed (%d)' % n)
        return out.raw[:n]

    block.decompress = decompress
    block.compress = compress
    pkg = types.ModuleType('lz4')
    pkg.block = block
    sys.modules['lz4'] = pkg
    sys.modules['lz4.block'] = block


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
ensure_lz4()

import nso_patcher                                             # noqa: E402

TITLE_ID = '0100A5B00BDC6000'
DEFAULT_MAIN = (HERE / 'sdout' / 'atmosphere' / 'contents' / TITLE_ID /
                'exefs' / 'main')
BACKUP_SUFFIX = '.pre-allocprobe'

NOP = 0xD503201F


def brk(imm16: int) -> int:
    return 0xD4200000 | ((imm16 & 0xFFFF) << 5)


# Each trap: the word that must be there when disarmed, the brk that
# replaces it, and enough surrounding context to prove the hook landed.
# `context` is (va_of_first_word, [words]) and is checked in FULL before
# anything is written, in either direction.
TRAPS = [
    {
        'id': 1,
        'imm': 0xA11,
        'va': 0x10EE8D4,
        'what': 'guest HeapAlloc returned NULL',
        # Their build NOPs this (NO_HEAP_DUMP); a stock module still has
        # the heap-dump call. Disarming restores whichever was there, which
        # is recorded on the way in rather than assumed.
        'disarmed': NOP,
        'also_disarmed': (0x97FFFF63,),
        'note': 'ff7nx_heap.NO_HEAP_DUMP put a NOP here; stock had the '
                'heap-dump call that aborts',
        'context': (0x10EE8C8, [
            0xB9401ED3,   # ldr  w19, [x22, #0x1c]
            0x35FFFE53,   # cbnz w19, #0x10ee894
            0x2A1403E0,   # mov  w0, w20
            None,         # <- the trap word
            0x2A1F03F3,   # mov  w19, wzr
            0x2A1303E0,   # mov  w0, w19
        ]),
    },
    {
        'id': 3,
        'imm': 0xA13,
        'va': 0x113C6CC,
        'what': 'glGetError() was non-zero at end of frame',
        'disarmed': 0x2A0003F7,   # mov w23, w0
        'note': 'only reachable when the frame actually produced an '
                'OpenGL error -- see THE END-OF-FRAME ERROR REPORTER below',
        'context': (0x113C6B8, [
            0x52919520,   # mov  w0, #0x8ca9      GL_DRAW_FRAMEBUFFER
            0x2A1F03E1,   # mov  w1, wzr          framebuffer 0
            0x9400569C,   # bl   #0x1152130       glBindFramebuffer
            # glGetError, OR the `mov w0, wzr` that --gl-quiet writes over
            # it. The two switches sit four bytes apart and each has to
            # tolerate the other, or turning one on makes the tool refuse
            # to touch anything.
            (0x940057B7, 0x2A1F03E0),
            0x340002E0,   # cbz  w0, #0x113c724   no error -> quiet exit
            None,         # <- the trap word (mov w23, w0)
            0xD0000373,   # adrp x19, #0x11aa000  "[OpenGL][ERROR] %s (0x%04X)"
        ]),
    },
    {
        'id': 4,
        'imm': 0xA14,
        'va': 0x113C764,
        'what': 'GL error after glBlitFramebuffer (the presentation blit)',
        'disarmed': 0x2A0003F7,   # mov w23, w0
        'note': 'the second of the three end-of-frame checks',
        'context': (0x113C758, [
            0x94005722,           # bl glBlitFramebuffer
            (0x94005791, 0x2A1F03E0),   # bl glGetError, or --gl-quiet's mov
            0x340002E0,           # cbz w0, #0x113c7bc
            None,                 # <- the trap word
            0xD0000373,           # adrp x19, #0x11aa000
        ]),
    },
    {
        'id': 5,
        'imm': 0xA15,
        'va': 0x113C7D4,
        'what': 'GL error after glBindFramebuffer(saved fbo)',
        'disarmed': 0x2A0003F7,   # mov w23, w0
        'note': 'the third of the three end-of-frame checks',
        'context': (0x113C7C8, [
            0x9400565A,           # bl glBindFramebuffer
            (0x94005775, 0x2A1F03E0),   # bl glGetError, or --gl-quiet's mov
            0x340002E0,           # cbz w0, #0x113c82c
            None,                 # <- the trap word
            0xD0000373,           # adrp x19, #0x11aa000
        ]),
    },
    {
        'id': 2,
        'imm': 0xA12,
        'va': 0x10DC678,
        'what': 'field alias page alloc failed; memcpy to NULL is next',
        'disarmed': 0xAA1F03F5,   # mov x21, xzr
        'note': 'the destination pointer is set to NULL here and used at '
                '+0x10DC68C without a further check',
        'context': (0x10DC678, [
            None,         # <- the trap word (mov x21, xzr)
            0xB9400740,   # ldr  w0, [x26, #4]
            0x35FFFE40,   # cbnz w0, #0x10dc648
            0xAA1F03E1,   # mov  x1, xzr
            0x321003E2,   # mov  w2, #0x10000   (ORR, not MOVZ)
            0xAA1503E0,   # mov  x0, x21
            0x9401D238,   # bl   #0x1150f70   (memcpy)
        ]),
    },
]


# ------------------------------------------------------------------ the fix
# Not a probe. `--gl-quiet` replaces the `glGetError` call at the end of
# the frame with `mov w0, wzr`, so the `cbz` right after it always takes the
# quiet exit and the reporter at +0x113C6CC..+0x113C720 never runs at all.
#
# WHY THAT IS SAFE. +0x113C6A0 has exactly ONE caller in the whole module --
# the tail branch at +0x1136644, out of the renderer's end-of-frame method
# (vtable +0x40 of the class at 0x12CCAE0). Nothing else in the game asks
# for a GL error, so nothing else changes behaviour. GL errors are sticky
# flags; leaving one set costs nothing because no other code reads it.
#
# WHAT IT DOES NOT DO. It does not stop the GL error happening. If the crash
# goes away with this, something in the frame is still failing and we then
# have to find out what -- but the game survives it, which is what the
# unmodified engine would have done if this build of the reporter were not
# in it.
# CORRECTED. The first version of this switch patched ONE gate and was
# tested on hardware as if it had switched the reporter off. It had not:
# +0x113C6A0 checks glGetError THREE times, once after each GL call it
# makes, and each check has its own gate. Silencing one leaves the other
# two live, and because all three report from the same function with the
# same inherited return address, a crash in gate 2 or 3 is INDISTINGUISHABLE
# in the crash report from a crash in gate 1. So that test proved nothing,
# and this is what it should have been.
#
#   0113C6C0  bl glBindFramebuffer(GL_DRAW_FRAMEBUFFER, 0)
#   0113C6C4  bl glGetError            <- gate 1   (reports line 307)
#   0113C758  bl glBlitFramebuffer(0,0,w,h, 0,0,w,h, COLOR_BUFFER_BIT, NEAREST)
#   0113C75C  bl glGetError            <- gate 2   (reports line 309)
#   0113C7C8  bl glBindFramebuffer(GL_DRAW_FRAMEBUFFER, *(0x3FEB738))
#   0113C7CC  bl glGetError            <- gate 3   (reports line 311)
#
# w and h come from `*(0x3FEB730)` / `*(0x3FEB734)`, so the middle call is
# the PRESENTATION BLIT -- the one this build's widescreen and 3x field
# render target work resizes.
GL_GATES = [
    {'va': 0x113C6C4, 'loud': 0x940057B7, 'branch': 0x113C6CC,
     'after': 'glBindFramebuffer(GL_DRAW_FRAMEBUFFER, 0)'},
    {'va': 0x113C75C, 'loud': 0x94005791, 'branch': 0x113C764,
     'after': 'glBlitFramebuffer -- the presentation blit'},
    {'va': 0x113C7CC, 'loud': 0x94005775, 'branch': 0x113C7D4,
     'after': 'glBindFramebuffer(GL_DRAW_FRAMEBUFFER, saved fbo)'},
]
GL_QUIET_WORD = 0x2A1F03E0        # mov w0, wzr
GL_CBZ = 0x340002E0               # cbz w0, <skip> -- identical at all three
GL_BRANCH_WORD = 0x2A0003F7       # mov w23, w0 -- the "on error" word


def verify_glquiet(img):
    """Every gate must be either the stock glGetError call or our mov."""
    bad = []
    for g in GL_GATES:
        have = _word(img, g['va'])
        if have not in (g['loud'], GL_QUIET_WORD):
            bad.append('+0x%07X holds %08X -- expected the stock %08X or our '
                       '%08X' % (g['va'], have, g['loud'], GL_QUIET_WORD))
        nxt = _word(img, g['va'] + 4)
        if nxt != GL_CBZ:
            bad.append('+0x%07X holds %08X, expected the gate branch %08X -- '
                       'the end-of-frame error reporter does not match this '
                       'module' % (g['va'] + 4, nxt, GL_CBZ))
    return bad


def glquiet_on(img):
    return all(_word(img, g['va']) == GL_QUIET_WORD for g in GL_GATES)


def glquiet_state(img):
    n = sum(1 for g in GL_GATES if _word(img, g['va']) == GL_QUIET_WORD)
    if n == 0:
        return 'loud   ', 'all %d checks live (stock)' % len(GL_GATES)
    if n == len(GL_GATES):
        return 'QUIET  ', 'all %d checks SKIPPED (--gl-quiet)' % len(GL_GATES)
    return 'PARTIAL', ('%d of %d skipped -- this is the state the first '
                       'version of this tool left, and it proves NOTHING'
                       % (n, len(GL_GATES)))


def _image(nso):
    end = max(seg.va + len(seg.data) for seg in nso.segments)
    img = bytearray(end)
    for seg in nso.segments:
        img[seg.va:seg.va + len(seg.data)] = seg.data
    return bytes(img)


def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def _hex(word):
    return ' '.join('%02X' % b for b in struct.pack('<I', word))


def verify(img, trap):
    """Complaints about this trap's site. Empty means it is ours to patch."""
    base, words = trap['context']
    bad = []
    for i, want in enumerate(words):
        va = base + 4 * i
        if va + 4 > len(img):
            bad.append('+0x%07X is past the end of the module' % va)
            continue
        have = _word(img, va)
        if want is None:
            ok = (trap['disarmed'], brk(trap['imm'])) \
                + tuple(trap.get('also_disarmed', ()))
            if have not in ok:
                bad.append('+0x%07X holds %08X -- expected either the stock '
                           '%08X or our brk #0x%X (%08X)'
                           % (va, have, trap['disarmed'], trap['imm'],
                              brk(trap['imm'])))
                continue
            continue
        if isinstance(want, tuple):
            if have not in want:
                bad.append('+0x%07X holds %08X, expected one of %s -- trap '
                           '%d does not match this module'
                           % (va, have, ' / '.join('%08X' % w for w in want),
                              trap['id']))
            continue
        if have != want:
            bad.append('+0x%07X holds %08X, expected %08X -- trap %d does '
                       'not match this module'
                       % (va, have, want, trap['id']))
    return bad


def live(img, trap):
    return _word(img, trap['va']) == brk(trap['imm'])


def show(img, target):
    print('module: %s' % target)
    any_bad = False
    for trap in TRAPS:
        bad = verify(img, trap)
        any_bad = any_bad or bool(bad)
        state = 'ARMED  ' if live(img, trap) else 'off    '
        print('  #%d  %s  +0x%07X  brk #0x%03X   %s'
              % (trap['id'], state, trap['va'], trap['imm'], trap['what']))
        print('        %s' % trap['note'])
        for line in bad:
            print('      ! ' + line)
    bad = verify_glquiet(img)
    state, detail = glquiet_state(img)
    print('  GL  %s  end-of-frame OpenGL error reporter: %s' % (state, detail))
    for g in GL_GATES:
        print('        +0x%07X  %-8s after %s'
              % (g['va'],
                 'skipped' if _word(img, g['va']) == GL_QUIET_WORD else 'live',
                 g['after']))
    for line in bad:
        print('      ! ' + line)
    return any_bad or bool(bad)


def _write(nso, target, args, patches, check):
    """Apply `patches`, write atomically, and re-read to confirm."""
    try:
        applied = nso_patcher.apply_spec(nso, {
            'name': 'probe_allocfail', 'patches': patches})
        rebuilt = nso_patcher.rebuild(nso)
    except Exception as exc:                                   # noqa: BLE001
        raise SystemExit('refused: %s' % exc)
    print('')
    for line in applied:
        print('  ' + line)
    if args.dry_run:
        print('dry run complete; no files changed')
        return 0
    backup = target.with_name(target.name + BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copy2(target, backup)
        print('backup: %s' % backup)
    fd, tmp = tempfile.mkstemp(prefix='.probe-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(rebuilt)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    if not check(_image(nso_patcher.read_nso(target))):
        raise SystemExit('post-write check failed; restore %s' % backup)
    print('sha256: %s' % hashlib.sha256(target.read_bytes()).hexdigest())
    print('\nCopy atmosphere/contents/%s/exefs/main to the SD card.'
          % TITLE_ID)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--main', type=Path, default=DEFAULT_MAIN)
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--arm', action='store_true', help='plant every trap')
    g.add_argument('--disarm', action='store_true', help='remove every trap')
    g.add_argument('--gl-quiet', action='store_true',
                   help='THE CANDIDATE FIX: skip the end-of-frame OpenGL '
                        'error reporter entirely')
    g.add_argument('--gl-loud', action='store_true',
                   help='put the error reporter back')
    ap.add_argument('--only', type=int, action='append',
                    help='act on this trap id only (repeatable)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)

    target = args.main.resolve()
    if not target.is_file():
        raise SystemExit('no sdout module: %s' % target)
    nso = nso_patcher.read_nso(target)
    img = _image(nso)

    if show(img, target):
        raise SystemExit('refusing to write over a module whose trap sites '
                         'do not match')
    if args.gl_quiet or args.gl_loud:
        ps = []
        for g in GL_GATES:
            want = GL_QUIET_WORD if args.gl_quiet else g['loud']
            cur = _word(img, g['va'])
            if cur == want:
                continue
            ps.append({'name': 'GL error check @ +0x%07X (after %s) -> %s'
                               % (g['va'], g['after'],
                                  'SKIPPED' if args.gl_quiet else 'live'),
                       'va': g['va'], 'expect': _hex(cur), 'set': _hex(want)})
        if not ps:
            print('\nall %d checks already %s; nothing to do.'
                  % (len(GL_GATES), 'quiet' if args.gl_quiet else 'loud'))
            return 0
        want_quiet = bool(args.gl_quiet)
        return _write(nso, target, args, ps,
                      check=lambda i2: glquiet_on(i2) == want_quiet)

    if not (args.arm or args.disarm):
        print('\nnothing changed. Pass --arm, --disarm, --gl-quiet or '
              '--gl-loud.')
        return 0

    want_armed = bool(args.arm)
    chosen = [t for t in TRAPS
              if not args.only or t['id'] in args.only]
    patches = []
    for trap in chosen:
        cur = _word(img, trap['va'])
        # `disarm` writes back trap['disarmed'], so a site that is currently
        # holding one of the ALTERNATIVE resting words (trap #1 on a stock
        # module still has the heap-dump call, not the NOP) must not be
        # armed -- disarming it afterwards would quietly write the other
        # variant and change behaviour behind your back.
        if cur in tuple(trap.get('also_disarmed', ())):
            print('\n! trap #%d: +0x%07X holds %08X, not the %08X this tool '
                  'would put back on --disarm.' % (trap['id'], trap['va'],
                                                   cur, trap['disarmed']))
            print('  That is a module this build did not produce (trap #1 '
                  'expects ff7nx_heap\'s NO_HEAP_DUMP NOP). Refusing, '
                  'because arming it would make --disarm a one-way change.')
            return 1
        new = brk(trap['imm']) if want_armed else trap['disarmed']
        if cur == new:
            continue
        patches.append({
            'name': 'trap #%d %s @ +0x%07X (%s)'
                    % (trap['id'], 'ARM' if want_armed else 'disarm',
                       trap['va'], trap['what']),
            'va': trap['va'],
            'expect': _hex(cur),
            'set': _hex(new),
        })
    if not patches:
        print('\nalready %s; nothing to do.'
              % ('armed' if want_armed else 'disarmed'))
        return 0
    rc = _write(nso, target, args, patches,
                check=lambda img2: all(live(img2, tr) == want_armed
                                       for tr in chosen))
    if rc:
        return rc
    if want_armed:
        print('Play until it crashes, then send the report. Look at '
              '"Exception Info -> Address":')
        for trap in chosen:
            print('    MaterialSX.nss + 0x%x  ->  %s'
                  % (trap['va'], trap['what']))
        print('  Anything else -- in particular the usual '
              'ReturnAddress[04] = MaterialSX.nss + 0x10dab58 -- means '
              'neither fired, which rules both of them out.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
