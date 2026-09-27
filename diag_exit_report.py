#!/usr/bin/env python3
"""
diag_exit_report.py -- read an Atmosphere crash report taken with
`diag_toggle.py <main> exit-report --on` and say which engine exit() it was.

    python3 diag_exit_report.py <crash_report.log> [exefs/main]

The module defaults to the stock dump (dump/exefs/main): every exit site and
every recompiled x86 function sits at the same offset in the built module.

It prints:
  * the trapping site and the [ASSERT] text the engine would have printed
    before closing;
  * for the x86 -> ARM64 dispatcher (+0x9CA4 / +0xA2A0), the x86 address the
    guest tried to call (X0 / X19) -- a corrupted or never-translated
    function pointer -- plus the x86 function that made the call (from LR);
  * the x86 function behind each ARM64 return address on the stack trace.
"""
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import nxmap                                                    # noqa: E402

EXIT_PLT = 0x1150EC0
DISPATCH = {0x9CA4: 'X[00]', 0xA2A0: 'X[19]'}


def _module_offsets(text, key):
    """Every '<Module> + 0x...' offset on the lines labelled `key`."""
    out = []
    for line in text.splitlines():
        if key in line:
            m = re.search(r'\+\s*0x([0-9a-fA-F]+)\)', line)
            if m:
                out.append(int(m.group(1), 16))
    return out


def _reg(text, name):
    m = re.search(re.escape(name) + r':\s+([0-9a-fA-F]{16})', text)
    return int(m.group(1), 16) if m else None


def _cstr(img, a):
    if not 0 <= a < len(img):
        return None
    e = img.find(b'\0', a, a + 300)
    if e < 0:
        return None
    try:
        s = img[a:e].decode('utf-8')
    except UnicodeDecodeError:
        return None
    return s if len(s) >= 4 and all(32 <= ord(c) < 127 or c in '\n\t'
                                    for c in s) else None


def assert_text(img, site, back=0x180):
    """Strings the code loads (adrp+add) just before `site`."""
    regs, out = {}, []
    for a in range(max(0, site - back), site, 4):
        x = struct.unpack_from('<I', img, a)[0]
        if (x & 0x9F000000) == 0x90000000:
            imm = (((x >> 5) & 0x7FFFF) << 2) | ((x >> 29) & 3)
            if imm & (1 << 20):
                imm -= 1 << 21
            regs[x & 31] = (a & ~0xFFF) + (imm << 12)
        elif (x & 0xFF800000) == 0x91000000:
            rn = (x >> 5) & 31
            if rn in regs:
                s = _cstr(img, regs[rn] + ((x >> 10) & 0xFFF))
                if s:
                    out.append(s.strip().replace('\n', ' '))
    return ' | '.join(out[-3:]) or '(no message string)'


def x86_of(m, arm):
    """(x86 function, its ARM64 start) whose translated body holds `arm`."""
    import bisect
    starts = m.arm_starts
    i = bisect.bisect_right(starts, arm) - 1
    if i < 0:
        return None
    start = starts[i]
    for va, ptr in m.x86_to_arm.items():
        if ptr == start:
            # Native engine code lies past the translated bodies; only claim
            # an address that is really inside this function's body.
            # The map's last record is the x86 .text END sentinel, and its
            # "body" would run to the end of the module.
            if va >= nxmap.X86_TEXT[1] or arm >= m.extent(va)[1]:
                return None
            return va, start
    return None


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    report = open(argv[1], errors='replace').read()
    module = argv[2] if len(argv) > 2 else os.path.join(HERE, 'dump', 'exefs',
                                                        'main')
    m = nxmap.Main(module)
    img = m.img

    pcs = _module_offsets(report, 'PC:')
    if not pcs:
        print('no "PC: ... (<module> + 0x...)" line found -- is this the '
              'FF7 report (program 0100A5B00BDC6000)?')
        return 1
    pc = pcs[0]
    word = struct.unpack_from('<I', img, pc)[0] if pc < len(img) else None
    print('crashed at +%#x' % pc)
    if pc in DISPATCH:
        tgt = _reg(report, DISPATCH[pc])
        print('  x86 -> ARM64 DISPATCHER: the guest called an x86 address '
              'that has no translated body')
        print('  bad call target   : %s' % ('%#x' % (tgt & 0xFFFFFFFF)
                                           if tgt is not None else '?'))
        lr = _module_offsets(report, 'LR:')
        if lr:
            hit = x86_of(m, lr[0])
            print('  called from        : +%#x%s' % (
                lr[0], ('  = x86 function %#x' % hit[0]) if hit else ''))
    else:
        nxt = struct.unpack_from('<I', img, pc + 4)[0]
        is_exit = (nxt >> 26) == 0x25 and \
            pc + 4 + (((nxt & 0x3FFFFFF) ^ 0x2000000) - 0x2000000) * 4 == EXIT_PLT
        here = struct.unpack_from('<I', img, pc)[0]
        is_exit = is_exit or ((here >> 26) == 0x25)
        print('  engine exit() site: %s' % assert_text(img, pc))
        if word not in (None,) and not is_exit:
            print('  (note: +%#x is not an exit site in this module -- was '
                  'exit-report on, and is this the right main?)' % pc)
    print('\n  stack, as x86 functions:')
    for off in _module_offsets(report, 'ReturnAddress'):
        hit = x86_of(m, off)
        print('    +%#09x  %s' % (off, ('x86 %#x' % hit[0]) if hit else
                                  '(native engine code)'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
