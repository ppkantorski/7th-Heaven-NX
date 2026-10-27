#!/usr/bin/env python3
"""BUILD 761 diagnostic: make every failure site in GL_Init (+0x113BD30)
fault through its own stack frame, so an Atmosphere crash report names the
call that failed (ReturnAddress just above 'MaterialSX.nss + 0x1135f0c').

    python3 tools/diag/glinit_sites.py IN_MAIN OUT_MAIN

Diagnostic only -- never part of a normal build.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os, sys, struct
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'src'))
import nxpaths; nxpaths.install(tools=True)
from pathlib import Path
import a64 as A, ff7nx_cave, nxmap, nso_patcher

LO, HI = 0x113BD30, 0x113C694
ASSERT = 0x1151610
TRAP = 0xE7FFDEFE

def bl_target(va, w):
    if (w & 0xFC000000) != 0x94000000: return None
    imm = w & 0x3FFFFFF
    if imm & 0x2000000: imm -= 0x4000000
    return va + imm * 4

def main(src, dst):
    m = nxmap.Main(src); img = m.img
    pool = ff7nx_cave.HolePool(bytearray(img), starts=set(m.arm_starts))
    stub, cw = ff7nx_cave.emit_laid_out(pool, lambda e, a: [0xA9BF7BFD, 0x910003FD, TRAP])
    words = dict(cw); sites = []
    for va in range(LO, HI, 4):
        w = struct.unpack_from('<I', img, va)[0]
        if w == TRAP or bl_target(va, w) == ASSERT:
            words[va] = A.bl(va, stub); sites.append((va, 'trap' if w == TRAP else 'assert'))
    hx = lambda w: ' '.join('%02X' % b for b in struct.pack('<I', w))
    spec = {'name': 'glinit diag', 'patches': [
        {'name': '+0x%X' % va, 'va': hex(va),
         'expect': hx(struct.unpack_from('<I', img, va)[0]), 'set': hx(words[va])}
        for va in sorted(words)]}
    nso = nso_patcher.read_nso(Path(src)); nso_patcher.apply_spec(nso, spec)
    open(dst, 'wb').write(nso_patcher.rebuild(nso))
    print('stub +0x%X; %d sites' % (stub, len(sites)))
    for va, k in sites: print('  +0x%X %s  -> report shows +0x%X' % (va, k, va + 4))

if __name__ == '__main__':
    main(*sys.argv[1:3])
