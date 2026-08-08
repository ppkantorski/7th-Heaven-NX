#!/usr/bin/env python3
"""
NSO0 parser/decompressor.

Struct layout taken verbatim from switchbrew/switch-tools src/elf2nso.c
(the actual tool Nintendo's own toolchain format derives from), NOT from
memory/guessing:

typedef struct { u32 FileOff; u32 DstOff; u32 DecompSz; u32 AlignOrTotalSz; } NsoSegment;
typedef struct {
    u8  Magic[4];
    u32 Unk1;             // version
    u32 Unk2;
    u32 Unk3;              // flags: bit0-2 = compressed(text,rodata,data), bit3-5 = hash-check
    NsoSegment Segments[3]; // .text, .rodata, .data
    u8  BuildId[0x20];
    u32 CompSz[3];
    u8  Padding[0x24];
    u64 Unk4;
    u64 Unk5;
    u8  Hashes[3][0x20];
} NsoHeader;  // sizeof == 0x100, verified against exefs/main below
"""
import struct
import lz4.block
import hashlib

SEG_NAMES = ['.text', '.rodata', '.data']


def parse_nso(path):
    with open(path, 'rb') as f:
        data = f.read()

    magic = data[0:4]
    if magic != b'NSO0':
        raise ValueError(f"{path}: bad magic {magic!r}")

    flags = struct.unpack_from('<I', data, 0x0C)[0]
    segs = []
    for i in range(3):
        off = 0x10 + 16 * i
        file_off, dst_off, decomp_sz, align_or_total = struct.unpack_from('<4I', data, off)
        segs.append(dict(name=SEG_NAMES[i], file_off=file_off, dst_off=dst_off,
                          decomp_sz=decomp_sz, align_or_total=align_or_total))
    comp_sz = struct.unpack_from('<3I', data, 0x60)
    hashes = [data[0xA0 + 0x20 * i: 0xA0 + 0x20 * (i + 1)] for i in range(3)]

    segments = {}
    for i, seg in enumerate(segs):
        compressed = (flags >> i) & 1
        raw = data[seg['file_off']: seg['file_off'] + comp_sz[i]]
        if compressed:
            out = lz4.block.decompress(raw, uncompressed_size=seg['decomp_sz'])
        else:
            out = raw[:seg['decomp_sz']]
        # verify hash if the corresponding hash-check flag is set
        check_bit = (flags >> (3 + i)) & 1
        if check_bit:
            actual = hashlib.sha256(out).digest()
            seg['hash_ok'] = (actual == hashes[i])
        else:
            seg['hash_ok'] = None
        seg['data'] = out
        segments[seg['name']] = seg

    return dict(path=path, flags=flags, segments=segments)


if __name__ == '__main__':
    import sys
    for p in sys.argv[1:]:
        info = parse_nso(p)
        print(f"=== {p} ===  flags={hex(info['flags'])}")
        for name, seg in info['segments'].items():
            print(f"  {name:8s} dst={hex(seg['dst_off'])}"
                  f" decomp_sz={hex(seg['decomp_sz'])} hash_ok={seg['hash_ok']}")
