#!/usr/bin/env python3
r"""
probe_exhaustive.py -- is FFNx's redirect list EXHAUSTIVE per function?

WHY THIS QUESTION DECIDES THE PATCH SHAPE
-----------------------------------------
probe_overlay.py matched ARM sites to x86 sites by position within each guest
address.  That is not sound: in battle_sub_5BD050 the x86 sites alternate
5C,4C,5C,4C... and the ARM body has a block that branches away
(+0x7CC178 `b #0x7cc2d0`), so address order and program order are not the
same list.  Matching by position silently paired x86 +0x16A with an ARM site
that actually reads a different field, and reported the WRONG site as
missing.  Exactly the "an exact fit is not identification" trap that
HANDOFF-83 §2.4 and FINDINGS-95 were both written about.

So the ordering question is dropped.  If FFNx redirects EVERY read of guest G
inside function F, then the rule is

    in F, every load of G becomes an immediate

which needs no ordering at all, and the ARM/x86 count comparison is the whole
verification.  This script establishes whether that premise holds, by
enumerating every x86 reference to those globals across the WHOLE executable
and intersecting with FFNx's list.
"""
import argparse
import collections
import struct

from capstone import Cs, CS_ARCH_X86, CS_MODE_32
from capstone.x86 import X86_OP_MEM

import nxmap
from probe_overlay import Exe, SPEC

GLOBALS = {
    0x9AAD4C: 'battle rect x',
    0x9AAD50: 'battle rect y',
    0x9AAD5C: 'battle rect w',
    0x9AAD68: 'battle rect h',
    0x9A04D4: 'swirl framebuffer offset y',
    0x9A04D8: 'swirl framebuffer offset x',
    0x9A04DC: 'swirl (written 0x40 -> 85)',
    0x99F330: 'swirl enter width source',
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--exe', default='ff7_en_switch')
    ap.add_argument('--main', default='exefs/main')
    args = ap.parse_args()

    exe = Exe(args.exe)
    m = nxmap.Main(args.main)
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    md.detail = True

    text = next(s for s in exe.sections if s[0] == '.text')
    _, base, raw, rsize, _ = text
    blob = exe.data[raw:raw + rsize]

    # every instruction in .text with a disp32 naming one of our globals
    refs = collections.defaultdict(list)
    for ins in md.disasm(blob, base):
        for op in ins.operands:
            if op.type != X86_OP_MEM:
                continue
            if op.mem.base or op.mem.index:
                continue
            d = op.mem.disp & 0xFFFFFFFF
            if d in GLOBALS:
                fn = m.containing(ins.address)
                refs[d].append((ins.address, ins.mnemonic, ins.op_str,
                                fn[0] if fn else None))

    ffnx = collections.defaultdict(set)          # guest -> {x86 site addr}
    ffnx_fn = {}
    for name, fn, off, op, kind in SPEC:
        ffnx_fn[fn] = name

    # recover the exact x86 instruction address for each FFNx site
    from probe_overlay import x86_site
    md32 = Cs(CS_ARCH_X86, CS_MODE_32)
    md32.detail = True
    site_of = {}
    for name, fn, off, op, kind in SPEC:
        s = x86_site(exe, md32, fn, off, 4)
        if s and op != 'int':
            g = s['field'] & 0xFFFFFFFF
            ffnx[g].add(s['addr'])
            site_of[(fn, off)] = (s['addr'], g)

    print('=== every .text reference to the globals FFNx redirects ===')
    print()
    for g in sorted(GLOBALS):
        rs = refs.get(g, [])
        print('  0x%X  %-30s  %d reference(s) in .text'
              % (g, GLOBALS[g], len(rs)))
        byfn = collections.defaultdict(list)
        for addr, mn, ops, fn in rs:
            byfn[fn].append((addr, mn, ops))
        for fn in sorted(byfn, key=lambda x: (x is None, x)):
            inlist = fn in ffnx_fn
            hits = byfn[fn]
            patched = sum(1 for a, _, _ in hits if a in ffnx[g])
            tag = ''
            if inlist:
                tag = ('   <== FFNx function, %d/%d redirected%s'
                       % (patched, len(hits),
                          '  ** EXHAUSTIVE **' if patched == len(hits)
                          else '  ** PARTIAL **'))
            print('      fn 0x%06X %-32s %d ref(s)%s'
                  % (fn or 0, ffnx_fn.get(fn, ''), len(hits), tag))
            if inlist and patched != len(hits):
                for a, mn, ops in hits:
                    print('          %s +0x%03X  %-30s %s'
                          % ('PATCHED  ' if a in ffnx[g] else 'left alone',
                             a - fn, '%s %s' % (mn, ops), ''))
        print()


if __name__ == '__main__':
    main()
