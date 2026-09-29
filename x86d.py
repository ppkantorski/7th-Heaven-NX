#!/usr/bin/env python3
"""x86d.py <va> <len> -- disassemble the PC x86 exe (ff7_en_switch)."""
import os, sys, struct
from capstone import Cs, CS_ARCH_X86, CS_MODE_32
HERE = os.path.dirname(os.path.abspath(__file__))
d = open(os.path.join(HERE, 'ff7_en_switch'), 'rb').read()
pe = struct.unpack_from('<I', d, 0x3c)[0]
ns = struct.unpack_from('<H', d, pe + 6)[0]; so = struct.unpack_from('<H', d, pe + 20)[0]
base = struct.unpack_from('<I', d, pe + 52)[0]
secs = []
for i in range(ns):
    o = pe + 24 + so + 40 * i
    vsz, va, rsz, ro = struct.unpack_from('<IIII', d, o + 8)
    secs.append((base + va, vsz, ro, rsz))
def off(a):
    for va, vs, ro, rs in secs:
        if va <= a < va + max(vs, rs): return ro + a - va
cs = Cs(CS_ARCH_X86, CS_MODE_32); cs.skipdata = True
def dis(a, n):
    return list(cs.disasm(d[off(a):off(a) + n], a))
if __name__ == '__main__':
    a = int(sys.argv[1], 16); n = int(sys.argv[2], 0)
    for i in dis(a, n):
        print('%x: %s %s' % (i.address, i.mnemonic, i.op_str))
        if len(sys.argv) > 3 and i.mnemonic == 'ret': break
