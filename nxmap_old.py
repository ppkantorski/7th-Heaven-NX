import struct, lz4.block, bisect
class Main:
    def __init__(self, path='exefs/main'):
        d=open(path,'rb').read(); self.hdr=d
        segs=[struct.unpack('<III',d[b:b+12]) for b in (0x10,0x20,0x30)]
        comp=struct.unpack('<III',d[0x60:0x6c]); flags=struct.unpack('<I',d[0xc:0x10])[0]
        raw=[]
        for i,(fo,mo,ds) in enumerate(segs):
            blob=d[fo:fo+comp[i]]
            raw.append(lz4.block.decompress(blob,uncompressed_size=ds) if flags&(1<<i) else blob[:ds])
        self.segs=segs; self.raw=raw
        self.image=bytearray(segs[2][1]+segs[2][2])
        for i in range(3):
            self.image[segs[i][1]:segs[i][1]+len(raw[i])]=raw[i]
        self.text=raw[0]
        self.bss=struct.unpack('<I',d[0x3C:0x40])[0]
        self._relocs()
        self._build_map()
    def u32(self,o): return struct.unpack('<I',self.image[o:o+4])[0]
    def u64(self,o): return struct.unpack('<Q',self.image[o:o+8])[0]
    def _relocs(self):
        # .dynamic -> DT_RELA; RELATIVE addends carry the map's pointer values
        self.rel={}
        off,size,ent=0x1153040,0x4BC78,0x18
        for p in range(off,off+size,ent):
            r_off,r_info,r_add=struct.unpack('<QQq',self.image[p:p+ent])
            if (r_info & 0xFFFFFFFF)==1027:      # R_AARCH64_RELATIVE
                self.rel[r_off]=r_add
    def _build_map(self):
        base=0x126D3A8; self.m={}
        p=base
        while True:
            x86=self.u32(p)
            if not (0x401000<=x86<=0x7B562B): break
            ptr=self.u64(p+8) or self.rel.get(p+8,0)
            self.m[x86]=ptr
            p+=16
        self.keys=sorted(self.m)
    def arm(self,x86):
        return self.m.get(x86)
    def extent(self,x86):
        """(arm64_start, arm64_end) for one x86 function. `end` is the next
        translated body in ARM64 address order, so the window never runs into
        another function's code."""
        a=self.m.get(x86)
        if a is None: return None
        if not hasattr(self,'_sorted'):
            self._sorted=sorted(set(self.m.values()))
        import bisect as _b
        i=_b.bisect_right(self._sorted,a)
        end=self._sorted[i] if i<len(self._sorted) else self.segs[0][2]
        return a,end
    def owner(self,x86):
        i=bisect.bisect_right(self.keys,x86)-1
        return self.keys[i] if i>=0 else None
if __name__=='__main__':
    import sys
    m=Main()
    print('map records:',len(m.m),'range 0x%X..0x%X'%(m.keys[0],m.keys[-1]))
    print('unresolved ptrs:',sum(1 for v in m.m.values() if not v))
    print('bssSize 0x%X  .data mem 0x%X size 0x%X'%(m.bss,m.segs[2][1],m.segs[2][2]))
    print('.text size 0x%X  .rodata mem 0x%X'%(m.segs[0][2],m.segs[1][1]))
    for a in sys.argv[1:]:
        v=int(a,16); print('x86 0x%X -> arm64 0x%X'%(v,m.arm(v) or 0))
