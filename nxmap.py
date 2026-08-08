#!/usr/bin/env python3
"""
nxmap.py -- the x86 -> ARM64 recompilation map in `main`, and the NSO image.

`main` carries a table of 16-byte records at module offset 0x126D3A8:

    +0  u32  x86 virtual address of a function entry point
    +4  u32  padding
    +8  u64  pointer to the translated ARM64 body

The u64 at +8 is NOT stored in the file as a usable value -- it is zero there
and filled in at load time by an `R_AARCH64_RELATIVE` relocation whose ADDEND is
the module offset we want. Reading the field directly gives 0 and silently loses
the whole map; the addend has to come from `.rela.dyn`.

The table ends at the first record whose key is outside ff7_en's .text span.

This module is only needed to REGENERATE ff7nx_dispatch_sites.py. A build does
not import it.
"""
import bisect
import struct
import sys

try:
    import lz4.block
except ImportError:                                          # pragma: no cover
    sys.exit('need lz4:  pip install lz4 --break-system-packages')

RECOMP_MAP = 0x126D3A8              # module offset of the record table
X86_TEXT = (0x401000, 0x7B562B)     # ff7_en .text -- the span map keys live in
R_AARCH64_RELATIVE = 1027


class Main:
    """`main` as a module-offset-addressable image plus the recompilation map."""

    def __init__(self, path):
        with open(path, 'rb') as f:
            data = f.read()
        if data[:4] != b'NSO0':
            raise SystemExit('nxmap: %s is not an NSO (magic %r)'
                             % (path, data[:4]))
        self.data = data
        self.segs = [struct.unpack('<III', data[b:b + 12])
                     for b in (0x10, 0x20, 0x30)]
        comp = struct.unpack('<III', data[0x60:0x6C])
        flags = struct.unpack('<I', data[0x0C:0x10])[0]
        self.bss = struct.unpack('<I', data[0x3C:0x40])[0]

        raw = []
        for i, (fo, mo, ds) in enumerate(self.segs):
            blob = data[fo:fo + comp[i]]
            raw.append(lz4.block.decompress(blob, uncompressed_size=ds)
                       if flags & (1 << i) else blob[:ds])
        self.raw = raw
        self.text = raw[0]

        end = self.segs[2][1] + self.segs[2][2]
        img = bytearray(end)
        for i, (fo, mo, ds) in enumerate(self.segs):
            img[mo:mo + ds] = raw[i]
        self.img = bytes(img)

        self._relocs()
        self._map()

    # ------------------------------------------------------------ relocations
    def _relocs(self):
        img = self.img
        mod0 = struct.unpack('<I', img[4:8])[0]
        dyn = mod0 + struct.unpack('<i', img[mod0 + 4:mod0 + 8])[0]
        want = {7: 'RELA', 8: 'RELASZ', 9: 'RELAENT'}
        v = {}
        p = dyn
        while True:
            tag, val = struct.unpack('<qQ', img[p:p + 16])
            if tag == 0:
                break
            if tag in want:
                v[want[tag]] = val
            p += 16
        missing = [k for k in ('RELA', 'RELASZ', 'RELAENT') if k not in v]
        if missing:
            raise SystemExit('nxmap: .dynamic has no %s' % ', '.join(missing))
        self.rel = {}
        n = v['RELASZ'] // v['RELAENT']
        base = v['RELA']
        ent = v['RELAENT']
        for i in range(n):
            o, info, add = struct.unpack('<QQq', img[base + i * ent:
                                                     base + (i + 1) * ent])
            if (info & 0xFFFFFFFF) == R_AARCH64_RELATIVE:
                self.rel[o] = add

    # -------------------------------------------------------------- the map
    def _map(self):
        self.x86_to_arm = {}
        p = RECOMP_MAP
        while True:
            va = struct.unpack('<I', self.img[p:p + 4])[0]
            if not (X86_TEXT[0] <= va <= X86_TEXT[1]):
                break
            ptr = self.rel.get(p + 8)
            if ptr is None:
                raise SystemExit('nxmap: map record at 0x%X has no '
                                 'R_AARCH64_RELATIVE reloc' % p)
            self.x86_to_arm[va] = ptr
            p += 16
        if len(self.x86_to_arm) < 1000:
            raise SystemExit('nxmap: only %d map records -- the table layout '
                             'is not what this build expects'
                             % len(self.x86_to_arm))
        self.x86_keys = sorted(self.x86_to_arm)
        self.arm_starts = sorted(self.x86_to_arm.values())

    # ------------------------------------------------------------- lookups
    def containing(self, va):
        """(x86_start, x86_end) of the mapped function containing `va`."""
        i = bisect.bisect_right(self.x86_keys, va) - 1
        if i < 0:
            return None
        start = self.x86_keys[i]
        end = (self.x86_keys[i + 1] if i + 1 < len(self.x86_keys)
               else X86_TEXT[1])
        return start, end

    def extent(self, x86_start):
        """
        (arm64_start, arm64_end) of one translated function body.

        `x86_start` must be a map KEY -- a real function entry point. Passing an
        address that merely lies inside a function is refused rather than
        silently resolved to the enclosing body, because every caller here is
        handing over a chain-derived function address and a near miss means the
        chain drifted.
        """
        if x86_start not in self.x86_to_arm:
            raise SystemExit('nxmap: x86 0x%X is not a function entry in the '
                             'recompilation map' % x86_start)
        a = self.x86_to_arm[x86_start]
        j = bisect.bisect_right(self.arm_starts, a)
        end = (self.arm_starts[j] if j < len(self.arm_starts)
               else len(self.text))
        return a, end


if __name__ == '__main__':
    m = Main(sys.argv[1] if len(sys.argv) > 1 else 'exefs/main')
    print('records      %d' % len(m.x86_to_arm))
    print('.text        0x%X bytes' % m.segs[0][2])
    print('.rodata at   0x%X' % m.segs[1][1])
    print('.data at     0x%X size 0x%X' % (m.segs[2][1], m.segs[2][2]))
    print('bssSize      0x%X' % m.bss)
    for va in sys.argv[2:]:
        x = int(va, 16)
        print('x86 0x%X -> arm64 0x%X..0x%X' % ((x,) + m.extent(x)))
