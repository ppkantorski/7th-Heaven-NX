#!/usr/bin/env python3
"""Disassembly helper for the world-map investigation.

Usage:
    python3 wmdis.py <main> arm <start_va> <end_va>
    python3 wmdis.py <main> x86 <exe> <start> <end>
    python3 wmdis.py <main> owner <arm_va>          # which x86 fn owns it
    python3 wmdis.py <main> fn <x86_va>             # arm extent of an x86 fn
"""
import sys
import bisect
import capstone
import nxmap


def arm_dis(m, lo, hi, base=None):
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
    md.detail = False
    out = []
    for i in md.disasm(m.img[lo:hi], lo if base is None else base):
        out.append('%08X  %-8s %s' % (i.address, i.mnemonic, i.op_str))
    return '\n'.join(out)


def x86_dis(path, lo, hi, imgbase=0x400000):
    # ff7.exe is a PE; map file offsets via section headers.
    import struct
    data = open(path, 'rb').read()
    pe = struct.unpack('<I', data[0x3C:0x40])[0]
    nsec = struct.unpack('<H', data[pe + 6:pe + 8])[0]
    opt = struct.unpack('<H', data[pe + 20:pe + 22])[0]
    base = struct.unpack('<I', data[pe + 24 + 28:pe + 24 + 32])[0]
    sh = pe + 24 + opt
    secs = []
    for i in range(nsec):
        e = sh + i * 40
        name = data[e:e + 8].rstrip(b'\0').decode('latin1')
        vsz, va, rsz, ro = struct.unpack('<IIII', data[e + 8:e + 24])
        secs.append((name, base + va, max(vsz, rsz), ro))

    def off(va):
        for name, sva, sz, ro in secs:
            if sva <= va < sva + sz:
                return ro + (va - sva)
        return None

    o = off(lo)
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    out = []
    for i in md.disasm(data[o:o + (hi - lo)], lo):
        out.append('%08X  %-8s %s' % (i.address, i.mnemonic, i.op_str))
    return '\n'.join(out)


def main(argv):
    path = argv[1]
    m = nxmap.Main(path)
    cmd = argv[2]
    if cmd == 'arm':
        print(arm_dis(m, int(argv[3], 16), int(argv[4], 16)))
    elif cmd == 'x86':
        print(x86_dis(argv[3], int(argv[4], 16), int(argv[5], 16)))
    elif cmd == 'owner':
        va = int(argv[3], 16)
        j = bisect.bisect_right(m.arm_starts, va) - 1
        a = m.arm_starts[j]
        rev = {v: k for k, v in m.x86_to_arm.items()}
        nxt = (m.arm_starts[j + 1] if j + 1 < len(m.arm_starts)
               else len(m.text))
        print('arm 0x%X is inside body 0x%X..0x%X  (x86 0x%X)'
              % (va, a, nxt, rev[a]))
    elif cmd == 'fn':
        x = int(argv[3], 16)
        a, b = m.extent(x)
        print('x86 0x%X -> arm 0x%X..0x%X (0x%X bytes)' % (x, a, b, b - a))
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main(sys.argv)
