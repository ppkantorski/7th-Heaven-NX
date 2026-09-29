#!/usr/bin/env python3
r"""
leakprobe.py -- what grows across fship <-> world-map trips. DIAGNOSTIC, on a
BUILT main. No rebuild.

    python3 leakprobe.py sdout/atmosphere/contents/0100A5B00BDC6000/exefs/main --on
    python3 leakprobe.py <same main> --off        (or just rebuild)

WHAT IT RECORDS (native/leakprobe.c)
====================================
Every frame (native gfx_drv_flip, +0x10DA880), per VISIT (a run of frames
with the same driver mode and field):
  * live texture objects in the port's texture manager (start, end, peak)
    and the deleted ones it keeps parked for reuse
  * v2: every GL error, drained once a frame (the port checks GL errors only
    after its 13 framebuffer calls, never after texture or buffer uploads);
    GL_OUT_OF_MEMORY (0x505) counted separately -- the graphics pool
  * the guest heap's free bytes
v3 also records the Highwind over every world-map visit (y on the first 16
frames, at frames 24..256, and on the last 16; control and view bits; the
highest y; the take-off height) and reads the worldfar module's own
altitude-restore state. The last 8 visits and the last two world traces go
into the report.

v4 (BUILD 605) also times every world-map frame with the hardware counter
and splits it: the game's logic and the wait for the last flip, the terrain
window (the game's own 5x5), the far field (worldfar), and the rest (models,
effects, the engine's draw submission) up to the flip. It keeps the totals,
how many frames ran long, and the five longest frames with their triangles,
draw chunks, zoom and altitude. Needs the 605 worldfar (its timestamps).

HOW YOU USE IT (v4: world-map frame rate)
=========================================
1. `--on` on your built main (texprobe must be off: both hook the flip).
2. Play the world map where it dips -- fly high, zoom out, turn around --
   for a minute or two.
3. Go into the Highwind and click R3 in fship (R3 on the world map is still
   the view toggle).
4. `python3 leakprobe_read.py <atmosphere/crash_reports/....log>` or send it.
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

BLOB = os.path.join(HERE, 'native', 'leakprobe.bin')
ENTRY = os.path.join(HERE, 'native', 'leakprobe.entry')

FLIP = 0x10DA880                   # native gfx_drv_flip
FLIP_WORD = 0xFC1C0FE8             # str d8, [sp, #-0x40]!
G2H = 0x10FC3A0
INPUT_GOT = 0x12CE1D0
MODE_SLOT = 0x12CE1F8
RVAR = 0x12CF4F0                   # -> the texture manager
STATE_BYTES = 9984                 # sizeof(State) in leakprobe.c (v7)
NG = 160                           # v7: GL device-layer vtable slots timed
GACC_OFF = STATE_BYTES - NG * 32   # State.gacc, the last member
# v7: the port's GL device layer (a D3D-style C++ wrapper over GL: buffers,
# programs, uniform blocks, draws) is called through these vtables. Every
# slot pointing into it gets a timing cave (no stack use: x30 and the start
# tick are kept in the slot's own gacc entry, so stack-passed arguments are
# untouched). Main-thread only; a method that recursed into its own slot
# would lose x30 (none does in this layer's world-map path).
GFX_VT = (0x12CC800, 0x12CCCE0)
GFX_CODE = (0x1130000, 0x1140000)
CAVE_WORDS = 18
GLERR = 0x11525A0                  # glGetError (PLT; its slot 0x12CE058)


def entries():
    out = {}
    for line in open(ENTRY):
        name, off = line.split()
        out[name] = int(off, 16)
    return out


def _addr(w, reg, at, value):
    w.append(A.adrp(reg, at + 4 * len(w), value & ~0xFFF))
    w.append(A.add_imm64(reg, reg, value & 0xFFF))


CAMERA_CALL = 0xF2A348             # ff7nx_worldfar.CAMERA_CALL


def _bl_target(va, w):
    if (w >> 26) != 0x25:
        return None
    imm = w & 0x3FFFFFF
    if imm & 0x2000000:
        imm -= 0x4000000
    return va + 4 * imm


def hwblk_of(text):
    """v3: the worldfar Highwind block (its State tail), from the camera
    stub's `adrp x0 / add x0` of the state. None without worldfar."""
    import ff7nx_worldfar as W
    stub = _bl_target(CAMERA_CALL, struct.unpack_from('<I', text, CAMERA_CALL)[0])
    if stub is None or not 0 <= stub < len(text) - 64:
        return None
    for i in range(12):
        a = stub + 4 * i
        w0, w1 = struct.unpack_from('<II', text, a)
        if (w0 & 0x9F00001F) == 0x90000000 and (w1 & 0xFFC003FF) == 0x91000000:
            immlo, immhi = (w0 >> 29) & 3, (w0 >> 5) & 0x7FFFF
            page = ((immhi << 2) | immlo) << 12
            if page & (1 << 32):
                page -= 1 << 33
            state = (a & ~0xFFF) + page + ((w1 >> 10) & 0xFFF)
            return state + W.STATE_BYTES - W.HW_TAIL
    return None


def pfblk_of(hwblk):
    """v4: worldfar's frame-timing stamps, the PERF_TAIL bytes before the
    Highwind block (a worldfar without them has no PERF_TAIL)."""
    import ff7nx_worldfar as W
    tail = getattr(W, 'PERF_TAIL', 0)
    return hwblk - tail if hwblk and tail else None


def frame_stub(at, fn, state, hwblk=None, pfblk=None):
    w = [A.stp64_pre(29, 30, 31, -0x60),
         A.stp64_off(0, 1, 31, 0x10), A.stp64_off(2, 3, 31, 0x20),
         A.stp64_off(4, 5, 31, 0x30), A.stp64_off(6, 7, 31, 0x40),
         A.str64(8, 31, 0x50)]
    _addr(w, 0, at, state)
    _addr(w, 1, at, G2H)
    _addr(w, 2, at, INPUT_GOT)
    _addr(w, 3, at, MODE_SLOT)
    _addr(w, 4, at, RVAR)
    _addr(w, 5, at, GLERR)
    if hwblk:
        _addr(w, 6, at, hwblk)
    else:
        w += [A.movz(6, 0), 0xD503201F]          # x6 = 0 ; nop
    if pfblk:
        _addr(w, 7, at, pfblk)
    else:
        w += [A.movz(7, 0), 0xD503201F]          # x7 = 0 ; nop
    w.append(A.bl(at + 4 * len(w), fn))
    w += [A.ldr64(8, 31, 0x50),
          A.ldp64_off(6, 7, 31, 0x40), A.ldp64_off(4, 5, 31, 0x30),
          A.ldp64_off(2, 3, 31, 0x20), A.ldp64_off(0, 1, 31, 0x10),
          A.ldp64_post(29, 30, 31, 0x60), FLIP_WORD]
    w.append(A.b(at + 4 * len(w), FLIP + 4))
    return w


def _img(segs, raw):
    end = max(mo + len(r) for (_fo, mo, _sz), r in zip(segs, raw))
    img = bytearray(end)
    for (_fo, mo, _sz), r in zip(segs, raw):
        img[mo:mo + len(r)] = r
    return img


def gfx_slots(segs, raw):
    """[(rela entry VA, slot VA, target)] for the GL layer's vtable slots."""
    img = _img(segs, raw)
    mod0 = struct.unpack_from('<I', img, 4)[0]
    dyn = mod0 + struct.unpack_from('<i', img, mod0 + 4)[0]
    v = {}
    p = dyn
    while True:
        tag, val = struct.unpack_from('<qQ', img, p)
        if tag == 0:
            break
        v.setdefault(tag, val)
        p += 16
    rela, relasz, relaent = v[7], v[8], v[9]
    out = []
    for i in range(relasz // relaent):
        e = rela + i * relaent
        off, info, add = struct.unpack_from('<QQq', img, e)
        if ((info & 0xFFFFFFFF) == 1027 and GFX_VT[0] <= off < GFX_VT[1]
                and GFX_CODE[0] <= add < GFX_CODE[1]):
            out.append((e, off, add))
    out.sort(key=lambda t: t[1])
    return out[:NG]


def _poke64(segs, raw, va, value):
    for k, ((_fo, mo, _sz), r) in enumerate(zip(segs, raw)):
        if mo <= va and va + 8 <= mo + len(r):
            b = bytearray(r)
            struct.pack_into('<Q', b, va - mo, value)
            raw[k] = bytes(b)
            return
    raise ValueError('va +0x%X is in no segment' % va)


# v7c: the three glBufferData methods also add their size argument (x2) and a
# call to a spare gacc row (ticks column = bytes). Rows 145.. are never methods
# (there are 142), so the reader prints them as bytes.
UPLOADS = {0x11324D0: (145, 'vertex upload'),
           0x11326E0: (146, 'index upload'),
           0x1132A60: (147, 'uniform upload')}


def gfx_cave(at, target, acc, bytes_acc=None):
    """Time one GL-layer method: acc = &gacc[slot] (ticks, calls, x30, t0).
    bytes_acc: also add x2 (the size) and a call to that row."""
    w = []

    def pc():
        return at + 4 * len(w)
    if bytes_acc is not None:
        w.append(A.adrp(16, pc(), bytes_acc & ~0xFFF))
        w.append(A.add_imm64(16, 16, bytes_acc & 0xFFF))
        w.append(A.ldr64(15, 16, 0))
        w.append(A.add_reg64(15, 15, 2))
        w.append(A.str64(15, 16, 0))
        w.append(A.ldr64(15, 16, 8))
        w.append(0x910005EF)                      # add x15, x15, #1
        w.append(A.str64(15, 16, 8))
    w.append(A.adrp(16, pc(), acc & ~0xFFF))
    w.append(A.add_imm64(16, 16, acc & 0xFFF))
    w.append(A.str64(30, 16, 16))
    w.append(0xD53BE031)                          # mrs x17, cntpct_el0
    w.append(A.str64(17, 16, 24))
    w.append(A.bl(pc(), target))
    w.append(A.adrp(16, pc(), acc & ~0xFFF))
    w.append(A.add_imm64(16, 16, acc & 0xFFF))
    w.append(0xD53BE031)                          # mrs x17, cntpct_el0
    w.append(A.ldr64(15, 16, 24))
    w.append(A.sub_reg64(17, 17, 15))
    w.append(A.ldr64(15, 16, 0))
    w.append(A.add_reg64(15, 15, 17))
    w.append(A.str64(15, 16, 0))
    w.append(A.ldr64(15, 16, 8))
    w.append(0x910005EF)                          # add x15, x15, #1
    w.append(A.str64(15, 16, 8))
    w.append(A.ldr64(30, 16, 16))
    w.append(A.ret())
    return w


def apply(src, dest):
    blob = open(src, 'rb').read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    got = struct.unpack_from('<I', text, FLIP)[0]
    if got != FLIP_WORD:
        raise ValueError('gfx_drv_flip entry +0x%X is %08X, expected %08X -- '
                         'texprobe or another probe owns it; --off that first'
                         % (FLIP, got, FLIP_WORD))
    lo, _hi = DS.part(src, 'probe')
    hwblk = hwblk_of(text)
    code = open(BLOB, 'rb').read()
    ent = entries()
    base = AC.scratch_base(blob, segs)
    state = (base + 15) & ~15
    growth = ((state - base) + STATE_BYTES + AC.bss_tail_slack(segs) + 0xFF) & ~0xFF
    stub = (lo + 15) & ~15
    entry = (stub + 4 * 40 + 15) & ~15
    if entry + len(code) > (lo + 0x2000):
        raise ValueError('leakprobe does not fit')
    for i, w in enumerate(frame_stub(stub, entry + ent['lp_frame'], state, hwblk, pfblk_of(hwblk))):
        struct.pack_into('<I', text, stub + 4 * i, w)
    text[entry:entry + len(code)] = code
    struct.pack_into('<I', text, FLIP, A.b(FLIP, stub))
    # v7: the GL layer's vtable slots -> timing caves
    slots = gfx_slots(segs, raw)
    at = (lo + 0x2000 + 15) & ~15
    if len(slots) > min(r for r, _n in UPLOADS.values()):
        raise ValueError('%d GL methods overlap the upload-size rows' % len(slots))
    if sorted(t for _e, _s, t in slots if t in UPLOADS) != sorted(UPLOADS):
        raise ValueError('the three upload methods are not all in the vtables')
    lo_, hi_ = DS.part(src, 'probe')
    for k, (_e, _slot, target) in enumerate(slots):
        up = UPLOADS.get(target)
        words = gfx_cave(at, target, state + GACC_OFF + 32 * k,
                         state + GACC_OFF + 32 * up[0] if up else None)
        if at + 4 * len(words) > hi_:
            raise ValueError('the GL timing caves do not fit the probe part')
        for i, w in enumerate(words):
            struct.pack_into('<I', text, at + 4 * i, w)
        slots[k] = (_e, _slot, target, at)
        at += 4 * len(words)
    raw[0] = bytes(text)
    for e, _slot, _target, cave in slots:
        _poke64(segs, raw, e + 16, cave)            # the slot's RELATIVE addend
    open(dest, 'wb').write(AC.pack(blob, raw, growth))
    return {'stub': stub, 'entry': entry, 'code': len(code), 'state': state,
            'bss': growth, 'hwblk': hwblk, 'gfx': len(slots)}


def main(argv):
    if len(argv) < 3 or argv[2] not in ('--on', '--off'):
        print(__doc__)
        return 2
    path = argv[1]
    backup = path + '.pre-leakprobe'
    if argv[2] == '--off':
        if not os.path.exists(backup):
            print('  no %s -- rebuild instead' % os.path.basename(backup))
            return 1
        shutil.copyfile(backup, path)
        os.remove(backup)
        print('  leakprobe -> off (the main from before --on is back)')
        return 0
    if os.path.exists(backup):
        print('  already on (%s exists); --off first' % os.path.basename(backup))
        return 1
    shutil.copyfile(path, backup)
    try:
        r = apply(backup, path)
    except Exception:
        shutil.copyfile(backup, path)
        os.remove(backup)
        raise
    print('  leakprobe -> on: code +0x%X (%d bytes), state +0x%X (+%d B BSS)'
          % (r['entry'], r['code'], r['state'], r['bss']))
    print('  v7: %d GL device-layer methods timed' % r['gfx'])
    print('  Highwind block: %s' % ('+0x%X (worldfar)' % r['hwblk'] if r['hwblk']
                                    else 'none (no worldfar in this main)'))
    print('  v7: fly the world map where it slows down (high, zoomed out), then '
          'go into the Highwind and click R3 in fship; send the crash report')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
