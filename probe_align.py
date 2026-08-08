#!/usr/bin/env python3
r"""
probe_align.py -- align x86 and ARM64 global accesses as SEQUENCES.

THE PROBLEM THIS SOLVES
-----------------------
To patch FFNx site `fn + off` we must name one ARM64 word.  Two weaker methods
were tried and both are unsound:

  * match by position within a guest address.  battle_sub_5BD050's x86 sites
    alternate w,x,w,x,... and the ARM body branches away mid-function, so
    address order and program order differ.  This paired x86 +0x16A with a
    site that reads a different field and then reported the wrong site as
    missing.

  * assume FFNx's list is exhaustive per function, so no matching is needed.
    MEASURED FALSE.  swirl_loop_sub_4026D4 reads offset_x at +0x335 and
    offset_y at +0x364; FFNx redirects the first and leaves the second alone,
    because only the horizontal axis is widened.  swirl_enter_40164E loads
    0x99F330 at +0x106 and FFNx leaves that alone too.  FFNx is selective, so
    per-site identification is mandatory.

THE METHOD
----------
Both images access a guest global only through an absolute address: on x86 a
disp32 with no base and no index, on ARM a constant materialised into w0 and
handed to the translator.  Collect ALL of them per function -- not just the
handful FFNx names -- and compare the two lists as sequences.

That is the point.  battle_sub_5BD050 has ~40 such accesses, not 10.  If two
sequences of 40 (guest address, load/store) pairs agree element for element,
the correspondence is pinned by far more evidence than the 10 sites need, and
any single site's identity is a lookup rather than a guess.  A disagreement
anywhere invalidates the whole function rather than silently shifting the
mapping by one -- which is exactly the failure the positional match had.

Sequence alignment is done with a plain LCS so that a recompiler-dropped or
recompiler-added access shows up as a reported gap at a known index instead of
corrupting every pairing after it.
"""
import argparse
import collections
import difflib
import struct

from capstone import (Cs, CS_ARCH_X86, CS_MODE_32, CS_ARCH_ARM64, CS_MODE_ARM,
                      CS_AC_READ, CS_AC_WRITE)
from capstone.x86 import X86_OP_MEM

import nxmap
import ff7nx_guestref as gr
from probe_overlay import Exe, SPEC


def x86_accesses(exe, md, fn, end):
    """
    Every absolute-addressed global access in one x86 function, in address
    order, as (addr, guest, is_load, width, text).

    Read/write comes from capstone's per-operand access flags, NOT from a
    mnemonic list.  An earlier version special-cased only the `mov` family and
    therefore classified `fstp dword ptr [0xbb25d0]` -- an x87 STORE -- as a
    load.  That single misclassification desynchronised the whole sequence
    alignment for battle_sub_58ACB9 and made a site that resolves perfectly
    well look unresolvable.  The flags know about all 1500 opcodes; a
    hand-written list only ever knows about the ones already seen going wrong.
    """
    out = []
    blob = exe.read(fn, end - fn)
    for ins in md.disasm(blob, fn):
        if ins.address >= end:
            break
        for op in ins.operands:
            if op.type != X86_OP_MEM:
                continue
            if op.mem.base or op.mem.index or op.mem.segment:
                continue
            d = op.mem.disp & 0xFFFFFFFF
            if not (0x400000 <= d < 0x1000000):
                continue
            is_load = bool(op.access & CS_AC_READ)
            if op.access & CS_AC_WRITE and not (op.access & CS_AC_READ):
                is_load = False
            out.append((ins.address, d, is_load, op.size,
                        '%s %s' % (ins.mnemonic, ins.op_str)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--exe', default='ff7_en_switch')
    ap.add_argument('--main', default='exefs/main')
    ap.add_argument('--fn', default=None, help='hex x86 entry, default: all')
    ap.add_argument('-v', '--verbose', action='store_true')
    args = ap.parse_args()

    exe = Exe(args.exe)
    m = nxmap.Main(args.main)
    md32 = Cs(CS_ARCH_X86, CS_MODE_32)
    md32.detail = True
    md64 = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    md64.detail = True

    from probe_overlay import x86_site
    # FFNx site -> exact x86 instruction address
    want = {}
    for name, fn, off, op, kind in SPEC:
        s = x86_site(exe, md32, fn, off, 4)
        want.setdefault(fn, []).append(
            (s['addr'], off, op, kind, name, s['ins']))

    fns = ([int(args.fn, 16)] if args.fn
           else sorted({s[1] for s in SPEC}))

    resolved = {}
    for fn in fns:
        name = next(s[0] for s in SPEC if s[1] == fn)
        x_end = m.containing(fn)[1]
        a, b = m.extent(fn)

        xs = x86_accesses(exe, md32, fn, x_end)
        ys, stats = gr.scan(m.text, a, b, md64)

        xk = [(g, ld) for _, g, ld, _, _ in xs]
        yk = [(y.guest, y.is_load) for y in ys]

        sm = difflib.SequenceMatcher(a=xk, b=yk, autojunk=False)
        blocks = sm.get_matching_blocks()
        pair = {}
        for i, j, n in blocks:
            for k in range(n):
                pair[xs[i + k][0]] = ys[j + k]
        matched = sum(n for _, _, n in blocks)

        print('=== %s  x86 0x%06X..0x%06X  ARM +0x%X..+0x%X ==='
              % (name, fn, x_end, a, b))
        print('    x86 global accesses %3d   ARM guest accesses %3d   '
              'aligned %3d  (%.0f%%)'
              % (len(xk), len(yk), matched,
                 100.0 * matched / max(1, len(xk))))
        if args.verbose:
            for i, j, n in blocks:
                if n:
                    print('      x86[%d:%d] <-> ARM[%d:%d]  %d'
                          % (i, i + n, j, j + n, n))

        for addr, off, op, kind, _, itext in sorted(want[fn], key=lambda t: t[1]):
            if op == 'int':
                print('      +0x%03X  %-34s  IMMEDIATE -> %s'
                      % (off, itext, kind))
                continue
            y = pair.get(addr)
            if y is None:
                print('      +0x%03X  %-34s  *** NOT ALIGNED ***' % (off, itext))
                continue
            print('      +0x%03X  %-34s  ->  ARM +0x%07X  %s %s   [%s]'
                  % (off, itext, y.addr, y.mnemonic, y.op_str, kind))
            resolved[(fn, off)] = y
        print()

    print('%d of %d dword site(s) resolved to a unique ARM word'
          % (len(resolved), sum(1 for s in SPEC if s[3] != 'int')))
    return resolved


if __name__ == '__main__':
    main()
