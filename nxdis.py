import struct, sys, lz4.block
from capstone import *

def nso_text(path):
    d=open(path,'rb').read()
    segs=[struct.unpack('<III',d[b:b+12]) for b in (0x10,0x20,0x30)]
    comp=struct.unpack('<III',d[0x60:0x6c]); flags=struct.unpack('<I',d[0xc:0x10])[0]
    raw=[]
    for i,(fo,mo,ds) in enumerate(segs):
        blob=d[fo:fo+comp[i]]
        raw.append(lz4.block.decompress(blob,uncompressed_size=ds) if flags&(1<<i) else blob[:ds])
    return raw, segs, d

raw,segs,d = nso_text('exefs/main')
text=raw[0]
md=Cs(CS_ARCH_ARM64,CS_MODE_LITTLE_ENDIAN)
start=int(sys.argv[1],16); n=int(sys.argv[2]) if len(sys.argv)>2 else 80
for i in md.disasm(text[start:start+4*n], start):
    print("+%08X  %08X  %-10s %s" % (i.address, struct.unpack('<I',bytes(i.bytes))[0], i.mnemonic, i.op_str))
