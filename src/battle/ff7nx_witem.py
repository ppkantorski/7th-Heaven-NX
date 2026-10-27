#!/usr/bin/env python3
"""
ff7nx_witem.py -- BUILD 809: the W-Item duplication, fixed in place.

THE BUG (x86 ff7_en, the code the port recompiled)
===================================================
The battle keeps its own inventory, 320 x 6 bytes at 0x9AC354 (+0 u16 item,
+2 u8 count), copied over the real one (0xDC0234) when the battle ends
(0x430DD0) -- so whatever the battle menu leaves there is what you keep.

W-Item tracks its pick in byte 0xDC3C88 (1 choosing the first item, 2 the
second); 0xDC3C74 == 0xA is W-Item; the first pick is saved at 0xDC38F4
(item) and 0xDC3638 (slot). Confirming the FIRST target takes one of that
item (a reservation) and sets the stage to 2.

1. Duplication -- 0x6DB0EE, cancel while targeting:
       if stage == 2 and W-Item:
           first item count += 1   (restores the id if the slot emptied, cap 99)
       back to the item list
   The first item is given back but the stage stays 2, so it is still the
   committed first pick. Every "pick a second item -> back" adds one more,
   and the battle end writes it into the real inventory.

2. Loss -- 0x6D98E3 (battle menu state 5, the item list), cancel: back to the
   command menu with no refund at all, so a first pick made and then backed
   out of is gone for the rest of the battle, and then for good.

THE CHANGE (exefs/main, in place -- no cave space taken)
=========================================================
Cave space is shared by every other feature, so neither fix uses any. Both
rewrite words the recompiler spent on something much smaller:

1. In the recompiled 0x6DB0EE the refund ends with a 34-word block that only
   caps the count at 99 (+0xD16098 in the 1.0.x module). It becomes: the cap
   (4 words), then stage = 1 -- the first pick is undone and the item list is
   for the first item again -- then the original continuation. 9 words; the
   rest of the old block is left as it was, unreachable.

2. In the recompiled 0x6D98E3 the cancel path ends with an inlined copy of
   0x6D0BE1(5): 33 words of emulated push/frame/pop around ONE store,
   [0xDC2068 + 5] = 3 (close the item window). It becomes, in the same 33
   words: if stage == 2 and W-Item, refund the first item exactly as the
   game's own refund does (id restored if the slot emptied, +1, cap 99) and
   set stage 0; then the same window store, the recompiler's per-return tick
   (0xA550), and the original join. Guest esp/ebp come out as before; eax
   (the handler's return value) is not used by the dispatcher.

Each guest page is translated ONCE and indexed inside it: the four menu
globals share page 0xDC3000 and the whole battle inventory (0x9AC354 ..
0x9ACAD4) sits in page 0x9AC000. The translator (0x10FC3A0) maps whole 4 KB
pages, so an offset that stays inside the page is exact; tests run it under
arm64emu's paged model, which scatters pages to catch anything that is not.

SAFETY
======
Every word the fixes rely on is checked against the stock bytes first (the
stage/kind tests, the register setup, both blocks and the join); anything
else in those ranges -- another feature, a different module -- refuses and
writes nothing. Re-applying is a no-op. SEVENTH_NX_WITEM_FIX=0 leaves stock.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os as _nx_os, sys as _nx_sys  # BUILD 755: run directly, find src/
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
if _nx_os.path.join(_nx_root, 'src') not in _nx_sys.path:
    _nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install()
import hashlib
import os
import shutil
import struct
import tempfile
from pathlib import Path

import a64 as A  # noqa: E402

VERSION = 809
ENV = 'SEVENTH_NX_WITEM_FIX'
TRANSLATE = 0x10FC3A0
TICK = 0xA550                   # the recompiler's per-return counter
LS = 0x9                        # unsigned lower or same

# guest addresses
STAGE, KIND, SLOT, FIRST_ID = 0xDC3C88, 0xDC3C74, 0xDC3638, 0xDC38F4
INVENTORY = 0x9AC354
WINDOW_CLOSE = 0xDC2068 + 5     # 0x6D0BE1(5): [0xDC2068 + 5] = 3

# --- fix 1: the recompiled 0x6DB0EE ---------------------------------------
TARGET_X86 = 0x6DB0EE
#: ARM offsets from the function start (1.0.x). GUARD covers the stage == 2
#: test (w20 = 0xDC3C74), the W-Item test, w22 = 0xDC3638, the whole refund
#: and the cap block; CAP is the cap block; CONT is where it continues.
F1_GUARD = (0xD54, 0xFF4,
            'f0f0f5cf1229eab0a5c4441035d32d5a7de6871794c9caca06172479cbaa2cc2')
F1_CAP = 0xF68                  # 34 words: count capping only
F1_CONT = 0xFF0                 # push 4 / call the cancel sound
F1_W20 = 0x3C74                 # mov/movk w20 at the guard's start

# --- fix 2: the recompiled 0x6D98E3 (battle menu state 5, item list) ------
ITEMLIST_X86 = 0x6D98E3
#: the prologue that sets x23 (guest registers) and w19 = 0xDC2088, and the
#: cancel path from the button test through the inlined 0x6D0BE1(5).
F2_PROLOGUE = (0x14, 0x34,
               'e99bbe44ac78ea51e08dfbe407cff2d97e15a589973b38dc5d6b5934fdfec54c')
F2_GUARD = (0x458, 0x5C8,
            '19ae5740a106b3a4c2248a7e8b90704066093d59731bf283106d4efd1c2b6f43')
F2_SITE = 0x544                 # 33 words: the inlined 0x6D0BE1(5)
F2_WORDS = 33
F2_JOIN = 0x804                 # str w8, [x23, #0x10]
F2_JOIN_WORD = 0xB90012E8


def enabled(env=None):
    env = os.environ if env is None else env
    return env.get(ENV, '1').strip().lower() not in ('0', 'off', 'no', 'false')


def _w(img, va):
    return struct.unpack_from('<I', img, va)[0]


def fix1_words(site):
    """The 9 words at the cap block: cap at 99, stage = 1, continue."""
    cont = site - F1_CAP + F1_CONT
    out = [
        A.cmp_imm(8, 0x63),                 # w8 = the count just written
        None,                               # b.ls set_stage
        A.movz(9, 0x63),
        A.strb(9, 0, 0),                    # x0 = host address of that count
        A.add_imm(0, 20, STAGE - KIND),     # w20 = 0xDC3C74
        A.bl(site + 4 * 5, TRANSLATE),
        A.movz(8, 1),
        A.strb(8, 0, 0),                    # stage = 1: first pick undone
        A.b(site + 4 * 8, cont),
    ]
    out[1] = A.bcond(site + 4, site + 4 * 4, LS)
    return out


def fix2_words(site, join):
    """The 33 words at the inlined window close: refund once, then close."""
    w = []

    def at(k):
        return site + 4 * k
    w += A.movz_movk(0, SLOT)                         # 0, 1
    w.append(A.bl(at(2), TRANSLATE))                  # 2  x0 = host(0xDC3638)
    w.append(A.mov_reg64(20, 0))                      # 3  x20: page 0xDC3000
    w.append(A.ldrb(8, 20, STAGE - SLOT))             # 4
    w.append(A.cmp_imm(8, 2))                         # 5
    w.append(None)                                    # 6  b.ne close
    w.append(A.ldrb(8, 20, KIND - SLOT))              # 7
    w.append(A.cmp_imm(8, 0xA))                       # 8
    w.append(None)                                    # 9  b.ne close
    w += A.movz_movk(0, INVENTORY)                    # 10, 11
    w.append(A.bl(at(12), TRANSLATE))                 # 12 x0 = host(0x9AC354)
    w.append(A.ldrh(9, 20, 0))                        # 13 slot
    w.append(A.add_reg_lsl(9, 9, 9, 1))               # 14 slot * 3
    w.append(A.add_reg64_lsl(21, 0, 9, 1))            # 15 x21 = entry (same page)
    w.append(A.ldrb(8, 21, 2))                        # 16 count
    w.append(None)                                    # 17 cbnz count, inc
    w.append(A.ldrh(9, 20, FIRST_ID - SLOT))          # 18
    w.append(A.strh(9, 21, 0))                        # 19 slot had emptied
    w.append(A.add_imm(8, 8, 1))                      # 20 inc:
    w.append(A.cmp_imm(8, 0x63))                      # 21
    w.append(A.movz(9, 0x63))                         # 22
    w.append(A.csel(8, 8, 9, LS))                     # 23 cap at 99
    w.append(A.strb(8, 21, 2))                        # 24
    w.append(A.strb(A.WZR, 20, STAGE - SLOT))         # 25 stage = 0
    w.append(A.sub_imm(0, 19, 0xDC2088 - WINDOW_CLOSE))   # 26 close:
    w.append(A.bl(at(27), TRANSLATE))                 # 27
    w.append(A.movz(8, 3))                            # 28
    w.append(A.strb(8, 0, 0))                         # 29 [0xDC206D] = 3
    w.append(A.bl(at(30), TICK))                      # 30
    w.append(A.ldr(8, 23, 0x10))                      # 31 w8 = esp, as before
    w.append(A.b(at(32), join))                       # 32
    w[6] = A.bcond(at(6), at(26), A.NE)
    w[9] = A.bcond(at(9), at(26), A.NE)
    w[17] = A.cbnz(8, at(17), at(20))
    assert len(w) == F2_WORDS and None not in w
    return w


def _sha(img, a, b):
    return hashlib.sha256(bytes(img[a:b])).hexdigest()


def plan(m):
    """(patches, notes, problems)."""
    patches, notes, problems = [], [], []
    lo1, _hi = m.extent(TARGET_X86)
    lo2, _hi = m.extent(ITEMLIST_X86)
    sites = (
        ('duplication', lo1 + F1_CAP,
         fix1_words(lo1 + F1_CAP),
         [(lo1 + F1_GUARD[0], lo1 + F1_GUARD[1], F1_GUARD[2])]),
        ('lost first item', lo2 + F2_SITE,
         fix2_words(lo2 + F2_SITE, lo2 + F2_JOIN),
         [(lo2 + F2_PROLOGUE[0], lo2 + F2_PROLOGUE[1], F2_PROLOGUE[2]),
          (lo2 + F2_GUARD[0], lo2 + F2_GUARD[1], F2_GUARD[2])]),
    )
    if _w(m.img, lo2 + F2_JOIN) != F2_JOIN_WORD:
        problems.append('item list: the join at +0x%X is not the stock store'
                        % (lo2 + F2_JOIN))
    for name, site, words, guards in sites:
        have = [_w(m.img, site + 4 * k) for k in range(len(words))]
        if have == words:
            notes.append('    W-Item %s: already' % name)
            continue
        bad = [(a, b) for a, b, digest in guards
               if _sha(m.img, a, b) != digest]
        if bad:
            problems.append('W-Item %s: +0x%X..+0x%X is not the stock code '
                            '(another patch, or a different module)'
                            % ((name,) + bad[0]))
            continue
        for k, wd in enumerate(words):
            va = site + 4 * k
            patches.append({'name': 'W-Item %s' % name, 'va': hex(va),
                            'expect': struct.pack('<I', have[k]).hex(),
                            'set': struct.pack('<I', wd).hex()})
        notes.append('    W-Item %s: %d word(s) in place at +0x%X, no cave'
                     % (name, len(words), site))
    if problems:
        return [], notes, problems
    return patches, notes, problems


def apply_to_nso(src, dest, log=print):
    """Write `src` patched to `dest`. True if dest was written."""
    import nso_patcher
    import nxmap
    m = nxmap.Main(str(src))
    patches, notes, problems = plan(m)
    for n in notes:
        log(n)
    if problems:
        for p in problems:
            log('  ! ' + p)
        log('  W-Item fix: nothing written')
        return False
    if not patches:
        return False
    nso = nso_patcher.read_nso(Path(src))
    nso_patcher.apply_spec(nso, {'name': 'w-item fix', 'patches': patches})
    fd, tmp = tempfile.mkstemp(dir=str(Path(dest).parent), prefix='.witem-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(dest))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    log('  W-Item fix: %d word(s), no duplication and no lost first item'
        % len(patches))
    return True


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('src')
    ap.add_argument('dest')
    a = ap.parse_args()
    raise SystemExit(0 if apply_to_nso(a.src, a.dest) else 1)
