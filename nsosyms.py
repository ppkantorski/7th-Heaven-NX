#!/usr/bin/env python3
"""nsosyms.py -- the imported functions of `main`: PLT stub -> name (via DT_JMPREL)."""
import struct, sys
import nxmap

def imports(path='dump/exefs/main'):
    m = nxmap.Main(path); img = m.img
    mod0 = struct.unpack_from('<I', img, 4)[0]
    dyn = mod0 + struct.unpack_from('<i', img, mod0 + 4)[0]
    v = {}
    p = dyn
    while True:
        tag, val = struct.unpack_from('<qQ', img, p)
        if tag == 0: break
        v.setdefault(tag, val); p += 16
    symtab, strtab, jmprel, pltsz = v[6], v[5], v[23], v[2]
    got2name = {}
    for i in range(pltsz // 24):
        off, info, add = struct.unpack_from('<QQq', img, jmprel + 24 * i)
        sym = info >> 32
        name_off = struct.unpack_from('<I', img, symtab + 24 * sym)[0]
        e = img.index(b'\0', strtab + name_off)
        got2name[off] = img[strtab + name_off:e].decode()
    # PLT stubs: adrp x16, page; ldr x17, [x16, #off]; add x16..; br x17
    plt = {}
    for a in range(0, len(m.text) - 12, 4):
        w0, w1 = struct.unpack_from('<II', img, a)
        if (w0 & 0x9F00001F) == 0x90000010 and (w1 & 0xFFC003FF) == 0xF9400211:
            immlo, immhi = (w0 >> 29) & 3, (w0 >> 5) & 0x7FFFF
            page = (a & ~0xFFF) + (((immhi << 2) | immlo) << 12)
            slot = page + ((w1 >> 10) & 0xFFF) * 8
            if slot in got2name: plt[a] = got2name[slot]
    return m, plt

if __name__ == '__main__':
    m, plt = imports()
    pat = sys.argv[1] if len(sys.argv) > 1 else ''
    for a, n in sorted(plt.items()):
        if pat in n: print('%x %s' % (a, n))
