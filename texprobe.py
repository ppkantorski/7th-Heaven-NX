#!/usr/bin/env python3
r"""
texprobe.py -- install the texture-load probe into a BUILT main. DIAGNOSTIC.
No rebuild.

    python3 texprobe.py sdout/atmosphere/contents/0100A5B00BDC6000/exefs/main --on
    python3 texprobe.py <same main> --off        (or just rebuild)

WHAT IT RECORDS (native/texprobe.c)
===================================
v2. Every call of the port's native gfx_drv_load_texture (+0x10D6BC0), one
record per texture (texture_set + palette index; the game's ~12000 repeat
calls per visit only bump its count): the texture's size, a hash of its
image data and of its palette, the texture_set the loader RETURNED, and the
GPU handle for its palette BEFORE the first call (non-zero = the loader
takes its shortcut and uploads NOTHING) and AFTER it.

Loads are grouped into VISITS: a run of loads with the same driver mode and
field id. When you leave, the visit is kept as the reference for that
field/mode (the 8 most recent).

HOW YOU USE IT
==============
1. `--on` on your built main, copy it to the card as usual.
2. Play exactly your repro: fship_2 from the sky (clean), back to the world
   map, straight back in (speckled).
3. In fship_2, with the speckles on screen, click R3. The game stops and
   Atmosphere writes a crash report (atmosphere/crash_reports/*.log).
   (R3 on the world map is still the view toggle; the probe ignores it there.)
4. `python3 texprobe_read.py <that report>` -- or send me the .log.

WHAT THE REPORT ANSWERS
=======================
Every texture of THIS visit is compared, by content, with the previous visit
of the same field:
  UNMATCHED  no texture with that size/image/palette in the clean visit
             -> the DATA differs: corrupted before or during the load
  CHANGED    the image data in memory no longer hashes as it did at load
             -> something overwrote it after the load
  SKIPPED    the handle was already set, nothing was uploaded
             -> the GPU shows whatever that handle held
  DUPHANDLE  its handle is also the handle of a different texture
  TSREUSED   its texture_set address was used in another field/mode's visit
             (e.g. the world map) -> reused memory, stale state
  STALE      its handle was a DIFFERENT texture's handle in another visit
             (e.g. the world map) -> the GPU texture was freed and reused
  CARRIED    the first call already had a texture_set: made before this visit
  REMADE     a new set was made again for the same texture
  HCHANGED   the handle changed between calls

It changes nothing in the game while R3 is not clicked outside the world map
(it hashes each texture as it loads, so loads take a little longer).
"""
import os
import shutil
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import a64 as A                              # noqa: E402
import ff7nx_audio_cave as AC                # noqa: E402
import ff7nx_deadspace as DS                 # noqa: E402

BLOB = os.path.join(HERE, 'native', 'texprobe.bin')
ENTRY = os.path.join(HERE, 'native', 'texprobe.entry')
STOCK = os.path.join(HERE, 'dump', 'exefs', 'main')

LOAD = 0x10D6BC0                   # native gfx_drv_load_texture
LOAD_WORD = 0xD10203FF             # sub sp, sp, #0x80
LOAD_RET = 0x10D7048               # its only ret
RET = 0xD65F03C0
FLIP = 0x10DA880                   # native gfx_drv_flip
FLIP_WORD = 0xFC1C0FE8             # str d8, [sp, #-0x40]!
G2H = 0x10FC3A0                    # guest -> host
INPUT_GOT = 0x12CE1D0              # ff7nx_analog.INPUT_GOT
MODE_SLOT = 0x12CE1F8              # -> driver mode (4 = world map)
STATE_BYTES = 344368              # sizeof(State) in texprobe.c


def entries():
    out = {}
    for line in open(ENTRY):
        name, off = line.split()
        out[name] = int(off, 16)
    return out


def _addr(w, reg, at, value):
    w.append(A.adrp(reg, at + 4 * len(w), value & ~0xFFF))
    w.append(A.add_imm64(reg, reg, value & 0xFFF))


def load_stub(at, fn, state):
    w = [A.stp64_pre(29, 30, 31, -0x40),
         A.stp64_off(0, 1, 31, 0x10),
         A.stp64_off(2, 3, 31, 0x20),
         A.mov_reg64(4, 2), A.mov_reg64(3, 1), A.mov_reg64(2, 0)]
    _addr(w, 5, at, MODE_SLOT)
    _addr(w, 0, at, state)
    _addr(w, 1, at, G2H)
    w.append(A.bl(at + 4 * len(w), fn))
    w += [A.ldp64_off(2, 3, 31, 0x20), A.ldp64_off(0, 1, 31, 0x10),
          A.ldp64_post(29, 30, 31, 0x40), LOAD_WORD]
    w.append(A.b(at + 4 * len(w), LOAD + 4))
    return w


def ret_stub(at, fn, state):
    w = [A.stp64_pre(29, 30, 31, -0x20), A.str64(0, 31, 0x10),
         A.mov_reg64(2, 0)]                 # x2 = the returned texture_set
    _addr(w, 0, at, state)
    _addr(w, 1, at, G2H)
    w.append(A.bl(at + 4 * len(w), fn))
    w += [A.ldr64(0, 31, 0x10), A.ldp64_post(29, 30, 31, 0x20), RET]
    return w


def frame_stub(at, fn, state):
    w = [A.stp64_pre(29, 30, 31, -0x60),
         A.stp64_off(0, 1, 31, 0x10), A.stp64_off(2, 3, 31, 0x20),
         A.stp64_off(4, 5, 31, 0x30), A.stp64_off(6, 7, 31, 0x40),
         A.str64(8, 31, 0x50)]
    _addr(w, 0, at, state)
    _addr(w, 1, at, G2H)
    _addr(w, 2, at, INPUT_GOT)
    _addr(w, 3, at, MODE_SLOT)
    w.append(A.bl(at + 4 * len(w), fn))
    w += [A.ldr64(8, 31, 0x50),
          A.ldp64_off(6, 7, 31, 0x40), A.ldp64_off(4, 5, 31, 0x30),
          A.ldp64_off(2, 3, 31, 0x20), A.ldp64_off(0, 1, 31, 0x10),
          A.ldp64_post(29, 30, 31, 0x60), FLIP_WORD]
    w.append(A.b(at + 4 * len(w), FLIP + 4))
    return w


def apply(src, dest, stock=None):
    blob = open(src, 'rb').read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    for va, want, what in ((LOAD, LOAD_WORD, 'load_texture entry'),
                           (LOAD_RET, RET, 'load_texture ret'),
                           (FLIP, FLIP_WORD, 'gfx_drv_flip entry')):
        got = struct.unpack_from('<I', text, va)[0]
        if got != want:
            raise ValueError('%s at +0x%X is %08X, expected %08X -- already '
                             'probed, or the frame probe owns it'
                             % (what, va, got, want))
    lo, hi = DS.part(src, 'probe', stock)
    code = open(BLOB, 'rb').read()
    ent = entries()
    base = AC.scratch_base(blob, segs)
    state = (base + 15) & ~15
    growth = ((state - base) + STATE_BYTES + AC.bss_tail_slack(segs) + 0xFF) & ~0xFF
    at = (lo + 15) & ~15
    stubs = {}
    for name in ('load', 'ret', 'frame'):
        stubs[name] = at
        at = (at + 4 * 32 + 15) & ~15
    entry = at
    if entry + len(code) > hi:
        raise ValueError('texprobe does not fit the probe part')
    words = {}
    for name, fn, maker in (('load', 'tp_load', load_stub),
                            ('ret', 'tp_ret', ret_stub),
                            ('frame', 'tp_frame', frame_stub)):
        for i, w in enumerate(maker(stubs[name], entry + ent[fn], state)):
            words[stubs[name] + 4 * i] = w
    for va, w in words.items():
        struct.pack_into('<I', text, va, w)
    text[entry:entry + len(code)] = code
    struct.pack_into('<I', text, LOAD, A.b(LOAD, stubs['load']))
    struct.pack_into('<I', text, LOAD_RET, A.b(LOAD_RET, stubs['ret']))
    struct.pack_into('<I', text, FLIP, A.b(FLIP, stubs['frame']))
    raw[0] = bytes(text)
    open(dest, 'wb').write(AC.pack(blob, raw, growth))
    return {'part': (lo, hi), 'stubs': stubs, 'entry': entry,
            'code': len(code), 'state': state, 'bss': growth}


def main(argv):
    if len(argv) < 3 or argv[2] not in ('--on', '--off'):
        print(__doc__)
        return 2
    path = argv[1]
    backup = path + '.pre-texprobe'
    if argv[2] == '--off':
        if not os.path.exists(backup):
            print('  no %s -- rebuild instead' % os.path.basename(backup))
            return 1
        shutil.copyfile(backup, path)
        os.remove(backup)
        print('  texprobe -> off (the main from before --on is back)')
        return 0
    if os.path.exists(backup):
        print('  already on (%s exists); --off first' % os.path.basename(backup))
        return 1
    shutil.copyfile(path, backup)
    try:
        r = apply(backup, path, STOCK if os.path.exists(STOCK) else None)
    except Exception:
        shutil.copyfile(backup, path)
        os.remove(backup)
        raise
    print('  texprobe -> on: code +0x%X (%d bytes) in the probe part '
          '+0x%X..+0x%X, state +0x%X (+%d KB BSS)'
          % (r['entry'], r['code'], r['part'][0], r['part'][1], r['state'],
             r['bss'] // 1024))
    print('  repro, then click R3 in fship_2 with the speckles showing; send '
          'the crash report (atmosphere/crash_reports/*.log)')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
