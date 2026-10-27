#!/usr/bin/env python3
"""BUILD 809: the W-Item fix, executed.

Runs the REAL recompiled code around both sites under arm64emu with the
translator's paged mapping (pages scattered), stock and patched:

  * stock reproduces the duplication; patched conserves the count over 50
    "pick a second item -> back" loops;
  * an emptied slot gets its item back, the 99 cap holds, a non-W-Item or
    first-stage cancel is untouched;
  * the item-list cancel refunds the first pick once (stage 0), and still
    closes the window and leaves guest esp/ebp exactly as stock does.

    python3 tests/test_witem.py

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install()
import os
import struct

import arm64emu
import ff7nx_witem as W
import nxmap

MAIN = os.path.join(_nx_root, 'dump', 'exefs', 'main')
PASS = [0]
REGS = 0x50000000           # host address of the guest register file
EBP = 0x00700100            # a guest stack frame
ESP = 0x007000F0


def ok(value, message):
    if not value:
        raise AssertionError(message)
    PASS[0] += 1


m = nxmap.Main(MAIN)
patches, notes, problems = W.plan(m)
ok(not problems, 'plans cleanly on the stock module: %r' % problems)
ok(len(patches) == 9 + W.F2_WORDS, 'patch size %d' % len(patches))
img = bytearray(m.img)
for p in patches:
    va = int(p['va'], 16)
    ok(bytes(img[va:va + 4]).hex() == p['expect'], 'expect at %s' % p['va'])
    img[va:va + 4] = bytes.fromhex(p['set'])


class _M:
    """The module view plan() needs, over a patched image."""
    def __init__(self, img):
        self.img, self.extent = img, m.extent


_p2, notes2, prob2 = W.plan(_M(img))
ok(not _p2 and not prob2 and all('already' in n for n in notes2),
   're-planning a patched module is a no-op: %r %r' % (notes2, prob2))
# anything else in the guarded ranges refuses
bad = bytearray(m.img)
lo1, _ = m.extent(W.TARGET_X86)
bad[lo1 + W.F1_GUARD[0] + 8:lo1 + W.F1_GUARD[0] + 12] = b'\x1f\x20\x03\xd5'
ok(W.plan(_M(bad))[2], 'a foreign word in the guard refuses')
os.environ[W.ENV] = '0'
ok(not W.enabled(), 'SEVENTH_NX_WITEM_FIX=0 turns it off')
os.environ.pop(W.ENV)


def code(image, a, b):
    return {va: struct.unpack_from('<I', image, va)[0] for va in range(a, b, 4)}


def world(stage, kind, slot, first_id, entry_id, count):
    mem = arm64emu.Mem()
    cpu = arm64emu.Cpu(mem, paged=True, native={W.TICK: lambda c: None})
    g = cpu.guest_to_host
    mem.setu(g(W.STAGE), stage, 1)
    mem.setu(g(W.KIND), kind, 1)
    mem.setu(g(W.SLOT), slot, 2)
    mem.setu(g(W.FIRST_ID), first_id, 2)
    e = W.INVENTORY + 6 * slot
    mem.setu(g(e), entry_id, 2)
    mem.setu(g(e + 2), count, 1)
    mem.setu(g(EBP - 0xC), W.INVENTORY, 4)   # the x86 local holding the base
    return mem, cpu, g, e


def read(mem, g, e):
    return (mem.u(g(W.STAGE), 1), mem.u(g(e), 2), mem.u(g(e + 2), 1),
            mem.u(g(W.WINDOW_CLOSE), 1))


# ---- fix 1: cancel while targeting the second item -----------------------
F1_START, F1_END = lo1 + W.F1_GUARD[0], lo1 + W.F1_CONT


def target_cancel(image, **kw):
    mem, cpu, g, e = world(**kw)
    cpu.x[25] = REGS
    mem.setu(REGS + 0x14, EBP, 4)
    mem.setu(REGS + 0x10, ESP, 4)
    exit_pc = cpu.run(F1_START, code(image, F1_START, F1_END + 4),
                      stop_at=F1_END)
    ok(exit_pc == F1_END, 'fix-1 path leaves at the continuation (0x%X)'
       % exit_pc)
    return read(mem, g, e)


W_ITEM = dict(kind=0xA, slot=7, first_id=0x10, entry_id=0x10)
stock = target_cancel(m.img, stage=2, count=3, **W_ITEM)
ok(stock[:3] == (2, 0x10, 4),
   'STOCK reproduces the bug: count 3 -> 4 and still stage 2 (%r)' % (stock,))
fixed = target_cancel(img, stage=2, count=3, **W_ITEM)
ok(fixed[:3] == (1, 0x10, 4),
   'patched: the reserved item comes back once and the stage is 1 (%r)'
   % (fixed,))
ok(target_cancel(img, stage=2, count=0, kind=0xA, slot=7, first_id=0x10,
                 entry_id=0xFFFF)[:3] == (1, 0x10, 1),
   'an emptied slot gets its item id back')
ok(target_cancel(img, stage=2, count=99, **W_ITEM)[:3] == (1, 0x10, 99),
   'the 99 cap holds')
for st, kd in ((1, 0xA), (2, 3), (0, 0xA)):
    ok(target_cancel(img, stage=st, count=5, kind=kd, slot=7, first_id=0x10,
                     entry_id=0x10)[:3] == (st, 0x10, 5),
       'stage %d / kind %d cancel is untouched' % (st, kd))

# The glitch loop: first pick (the game's own confirm: count - 1, stage 2),
# then "pick a second item -> back", fifty times.
for image, name, want in ((m.img, 'stock', 3 + 50), (img, 'patched', 3)):
    count, stage = 3, 1
    for _ in range(50):
        if stage == 1:                      # (re)confirm the first target
            count, stage = count - 1, 2
        stage, _id, count, _w = target_cancel(
            image, stage=stage, count=count, **W_ITEM)
    owned = count + (1 if stage == 2 else 0)    # + the reservation, if held
    ok(owned == want, '%s: owns %d after 50 loops (want %d)'
       % (name, owned, want))

# ---- fix 2: cancel out of the item list ----------------------------------
lo2, _ = m.extent(W.ITEMLIST_X86)
F2_START, F2_END = lo2 + W.F2_SITE, lo2 + W.F2_JOIN


def list_cancel(image, **kw):
    mem, cpu, g, e = world(**kw)
    cpu.x[23] = REGS
    cpu.x[19] = 0xDC2088
    mem.setu(REGS + 0x14, EBP, 4)
    mem.setu(REGS + 0x10, ESP, 4)
    exit_pc = cpu.run(F2_START, code(image, F2_START, F2_START + 4 * 33),
                      stop_at=F2_END)
    ok(exit_pc == F2_END, 'fix-2 path rejoins at 0x%X (0x%X)' % (F2_END, exit_pc))
    return read(mem, g, e) + (cpu.x[8] & 0xFFFFFFFF,
                              mem.u(REGS + 0x14, 4))


st = list_cancel(m.img, stage=2, count=3, **W_ITEM)
ok(st[:3] == (2, 0x10, 3) and st[3] == 3,
   'STOCK: backing out loses the reserved item (count stays 3) %r' % (st,))
fx = list_cancel(img, stage=2, count=3, **W_ITEM)
ok(fx[:3] == (0, 0x10, 4), 'patched: refunded once, stage 0 %r' % (fx,))
ok(fx[3:] == st[3:], 'window closed and esp/ebp exactly as stock %r %r'
   % (fx[3:], st[3:]))
ok(list_cancel(img, stage=2, count=0, kind=0xA, slot=7, first_id=0x10,
               entry_id=0xFFFF)[:3] == (0, 0x10, 1),
   'item list: an emptied slot gets its item id back')
ok(list_cancel(img, stage=2, count=99, **W_ITEM)[:3] == (0, 0x10, 99),
   'item list: the 99 cap holds')
for st_, kd in ((1, 0xA), (0, 0xA), (2, 3)):
    r = list_cancel(img, stage=st_, count=5, kind=kd, slot=7, first_id=0x10,
                    entry_id=0x10)
    s = list_cancel(m.img, stage=st_, count=5, kind=kd, slot=7, first_id=0x10,
                    entry_id=0x10)
    ok(r == s, 'stage %d / kind %d: identical to stock %r' % (st_, kd, r))
# fix 1 then fix 2 never refunds twice
after1 = target_cancel(img, stage=2, count=2, **W_ITEM)
after2 = list_cancel(img, stage=after1[0], count=after1[2], **W_ITEM)
ok(after2[:3] == (1, 0x10, 3), 'cancel target then back out: one refund %r'
   % (after2,))
print('test_witem: %d checks passed' % PASS[0])
