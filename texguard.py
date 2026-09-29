#!/usr/bin/env python3
r"""
texguard.py -- the fship_2 speckle fix, installed into a BUILT main. No rebuild.

    python3 texguard.py sdout/atmosphere/contents/0100A5B00BDC6000/exefs/main --on
    python3 texguard.py <same main> --off        (or just rebuild)
    python3 texguard.py <same main> --show

WHAT IT FIXES (PROBE-599, texprobe v3)
======================================
The port's texture loader (+0x10D6BC0) skips the upload when the texture
set's slot already holds a texture id. On the second fship_2 entry from the
world map the game hands it five sets whose slots hold ids that belonged to
world-map textures and had been DELETED. The loader binds those dead ids;
the texture manager then hands the same ids out to the next textures it
creates, and the crew and Cloud are drawn with whatever lands on them.

texguard makes the shortcut check what the slot claims (native/texguard.c):
the id is kept only if it is live in the manager's table AND the loader
itself stored it in that exact slot AND nothing deleted or re-created it
since. Otherwise the slot is cleared (the id is not deleted -- it is not the
set's) and the texture is uploaded as for a new one.

Four hooks, every original word checked before anything is written:
  +0x10D6E48  the shortcut's slot read           -> tg_check
  +0x10D7C20  the loader's store of a new id     -> tg_store
  +0x42D0     texture manager delete (entry)     -> tg_forget
  +0x4604     texture manager create (the id)    -> tg_forget

Code goes at the top of the dead-space 'probe' part (texprobe uses the
bottom; the two can be on together), state (8 KB) in new BSS.
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

BLOB = os.path.join(HERE, 'native', 'texguard.bin')
ENTRY = os.path.join(HERE, 'native', 'texguard.entry')
STOCK = os.path.join(HERE, 'dump', 'exefs', 'main')

CHECK_SITE, CHECK_WORD = 0x10D6E48, 0xB8757808   # ldr w8, [x0, x21, lsl #2]
STORE_SITE, STORE_WORD = 0x10D7C20, 0xB8357A96   # str w22, [x20, x21, lsl #2]
DEL_SITE, DEL_WORD = 0x42D0, 0xF81C0FF7          # str x23, [sp, #-64]!
NEW_SITE, NEW_WORD = 0x4604, 0xB9600260          # ldr w0, [x19, #8192]
RVAR = 0x12CF4F0                                 # -> the renderer / texture manager
STATE_BYTES = 8224                               # sizeof(TG)
CODE_ROOM = 0x800                                # top of the probe part
MOV_W8_W0 = 0x2A0003E8
MOV_W3_W22 = 0x2A1603E3


def entries():
    out = {}
    for line in open(ENTRY):
        name, off = line.split()
        out[name] = int(off, 16)
    return out


def _addr(w, reg, at, value):
    w.append(A.adrp(reg, at + 4 * len(w), value & ~0xFFF))
    w.append(A.add_imm64(reg, reg, value & 0xFFF))


def check_stub(at, fn, state):
    """bl here from CHECK_SITE: x0 = host slot array, x21 = palette index.
    Returns with w8 = the id to use (what the replaced ldr produced)."""
    w = [A.stp64_pre(29, 30, 31, -0x10),
         A.mov_reg64(1, 0), A.mov_reg64(2, 21)]
    _addr(w, 0, at, state)
    _addr(w, 3, at, RVAR)
    w.append(A.bl(at + 4 * len(w), fn))
    w += [MOV_W8_W0, A.ldp64_post(29, 30, 31, 0x10), 0xD65F03C0]
    return w


def store_stub(at, fn, state):
    """bl here from STORE_SITE: x20 = host slot array, x21 = index, w22 = id."""
    w = [A.stp64_pre(29, 30, 31, -0x10),
         A.mov_reg64(1, 20), A.mov_reg64(2, 21), MOV_W3_W22]
    _addr(w, 0, at, state)
    w.append(A.bl(at + 4 * len(w), fn))
    w += [A.ldp64_post(29, 30, 31, 0x10), 0xD65F03C0]
    return w


def del_stub(at, fn, state):
    """b here from DEL_SITE (entry of delete(x0 mgr, w1 id)); x0..x7 kept."""
    w = [A.stp64_pre(29, 30, 31, -0x50),
         A.stp64_off(0, 1, 31, 0x10), A.stp64_off(2, 3, 31, 0x20),
         A.stp64_off(4, 5, 31, 0x30), A.stp64_off(6, 7, 31, 0x40),
         A.mov_reg64(2, 1), A.mov_reg64(1, 0)]
    _addr(w, 0, at, state)
    _addr(w, 3, at, RVAR)
    w.append(A.bl(at + 4 * len(w), fn))
    w += [A.ldp64_off(6, 7, 31, 0x40), A.ldp64_off(4, 5, 31, 0x30),
          A.ldp64_off(2, 3, 31, 0x20), A.ldp64_off(0, 1, 31, 0x10),
          A.ldp64_post(29, 30, 31, 0x50), DEL_WORD]
    w.append(A.b(at + 4 * len(w), DEL_SITE + 4))
    return w


def new_stub(at, fn, state):
    """b here from NEW_SITE: do its ldr w0, [x19, #8192], forget that id."""
    w = [NEW_WORD, A.stp64_pre(29, 30, 31, -0x20), A.str64(0, 31, 0x10),
         A.mov_reg64(2, 0), A.mov_reg64(1, 19)]
    _addr(w, 0, at, state)
    _addr(w, 3, at, RVAR)
    w.append(A.bl(at + 4 * len(w), fn))
    w += [A.ldr64(0, 31, 0x10), A.ldp64_post(29, 30, 31, 0x20)]
    w.append(A.b(at + 4 * len(w), NEW_SITE + 4))
    return w


SITES = ((CHECK_SITE, CHECK_WORD, 'loader shortcut slot read'),
         (STORE_SITE, STORE_WORD, 'loader id store'),
         (DEL_SITE, DEL_WORD, 'texture manager delete entry'),
         (NEW_SITE, NEW_WORD, 'texture manager create id'))


def apply(src, dest, stock=None):
    blob = open(src, 'rb').read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    for va, want, what in SITES:
        got = struct.unpack_from('<I', text, va)[0]
        if got != want:
            raise ValueError('%s at +0x%X is %08X, expected %08X -- already '
                             'guarded?' % (what, va, got, want))
    lo, hi = DS.part(src, 'probe')        # texprobe may own the bottom
    top = hi & ~15
    at = (top - CODE_ROOM) & ~15
    if at < lo:
        raise ValueError('probe part too small')
    if stock is not None:
        # dead x86 body, not zeros: it must still be the stock bytes
        _s, sraw = AC.segments(open(stock, 'rb').read())
        if bytes(text[at:top]) != sraw[0][at:top]:
            raise ValueError('the top of the probe part (+0x%X) was already '
                             'written by another pass' % at)
    code = open(BLOB, 'rb').read()
    ent = entries()
    base = AC.scratch_base(blob, segs)
    state = (base + 15) & ~15
    growth = ((state - base) + STATE_BYTES + 0xFF) & ~0xFF
    stubs = {}
    for name in ('check', 'store', 'del', 'new'):
        stubs[name] = at
        at = (at + 4 * 24 + 15) & ~15
    entry = at
    if entry + len(code) > top:
        raise ValueError('texguard does not fit its room')
    words = {}
    for name, fn, maker in (('check', 'tg_check', check_stub),
                            ('store', 'tg_store', store_stub),
                            ('del', 'tg_forget', del_stub),
                            ('new', 'tg_forget', new_stub)):
        for i, w in enumerate(maker(stubs[name], entry + ent[fn], state)):
            words[stubs[name] + 4 * i] = w
    for va, w in words.items():
        struct.pack_into('<I', text, va, w)
    text[entry:entry + len(code)] = code
    struct.pack_into('<I', text, CHECK_SITE, A.bl(CHECK_SITE, stubs['check']))
    struct.pack_into('<I', text, STORE_SITE, A.bl(STORE_SITE, stubs['store']))
    struct.pack_into('<I', text, DEL_SITE, A.b(DEL_SITE, stubs['del']))
    struct.pack_into('<I', text, NEW_SITE, A.b(NEW_SITE, stubs['new']))
    raw[0] = bytes(text)
    open(dest, 'wb').write(AC.pack(blob, raw, growth))
    return {'part': (lo, hi), 'stubs': stubs, 'entry': entry,
            'code': len(code), 'state': state, 'bss': growth}


def is_on(path):
    blob = open(path, 'rb').read()
    _segs, raw = AC.segments(blob)
    return struct.unpack_from('<I', raw[0], CHECK_SITE)[0] != CHECK_WORD


def main(argv):
    if len(argv) < 3 or argv[2] not in ('--on', '--off', '--show'):
        print(__doc__)
        return 2
    path = argv[1]
    backup = path + '.pre-texguard'
    if argv[2] == '--show':
        print('  texguard is %s' % ('ON' if is_on(path) else 'off'))
        return 0
    if argv[2] == '--off':
        if not os.path.exists(backup):
            print('  no %s -- rebuild instead' % os.path.basename(backup))
            return 1
        shutil.copyfile(backup, path)
        os.remove(backup)
        print('  texguard -> off (the main from before --on is back)')
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
    print('  texguard -> on: code +0x%X (%d bytes), stubs +0x%X, state +0x%X '
          '(+%d KB BSS)' % (r['entry'], r['code'], min(r['stubs'].values()),
                            r['state'], r['bss'] // 1024))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
