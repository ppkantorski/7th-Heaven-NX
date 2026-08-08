#!/usr/bin/env python3
"""Verify a cave-arena-expanded FF7 Switch ``main`` against its stock input.

This is intentionally a structural verifier, not a game-behaviour claim.  It
checks every rebased ADRP and static module-offset pointer, the MOD0 metadata,
segment geometry, and the NSO segment hashes after decompressing both files.

    python3 verify_nso_arena.py --stock exefs/main --built sdout/.../exefs/main
"""
import argparse
import hashlib
import struct
import sys

import lz4.block


def align_up(value, alignment=0x1000):
    return (value + alignment - 1) & -alignment


def sign_extend(value, bits):
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


def segments(blob):
    if blob[:4] != b'NSO0':
        raise ValueError('not an NSO0 file')
    flags, = struct.unpack_from('<I', blob, 0x0C)
    desc = [struct.unpack_from('<III', blob, off) for off in (0x10, 0x20, 0x30)]
    comp = struct.unpack_from('<III', blob, 0x60)
    raw = []
    for i, (file_off, _memory_off, size) in enumerate(desc):
        stored = blob[file_off:file_off + comp[i]]
        data = (lz4.block.decompress(stored, uncompressed_size=size)
                if flags & (1 << i) else stored[:size])
        if len(data) != size:
            raise ValueError('segment %d has the wrong decompressed size' % i)
        want = blob[0xA0 + 0x20 * i:0xC0 + 0x20 * i]
        if hashlib.sha256(data).digest() != want:
            raise ValueError('segment %d hash mismatch' % i)
        raw.append(data)
    return desc, raw


def adrp_target(word, pc):
    if (word & 0x9F000000) != 0x90000000:
        return None
    imm21 = (((word >> 5) & 0x7FFFF) << 2) | ((word >> 29) & 3)
    return (pc & ~0xFFF) + (sign_extend(imm21, 21) << 12)


def fail(problems, message):
    problems.append(message)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--stock', required=True, help='stock 1.0.3 exefs/main')
    ap.add_argument('--built', required=True, help='arena-expanded exefs/main')
    args = ap.parse_args(argv)

    stock = open(args.stock, 'rb').read()
    built = open(args.built, 'rb').read()
    ss, sr = segments(stock)
    bs, br = segments(built)
    problems = []
    old_ro, old_data = ss[1][1], ss[2][1]
    new_ro, new_data = bs[1][1], bs[2][1]
    delta = new_ro - old_ro
    old_bss, = struct.unpack_from('<I', stock, 0x3C)
    old_image_end = align_up(old_data + ss[2][2]) + old_bss

    if delta <= 0 or delta & 0xFFF:
        fail(problems, 'rodata delta 0x%X is not a positive page count' % delta)
    if bs[0][2] != new_ro:
        fail(problems, 'text size 0x%X does not end at new rodata 0x%X'
             % (bs[0][2], new_ro))
    if new_data != old_data + delta:
        fail(problems, 'data moved by 0x%X, want 0x%X' %
             (new_data - old_data, delta))
    if bs[1][2] != ss[1][2] or bs[2][2] != ss[2][2]:
        fail(problems, 'rodata/data sizes changed')
    if br[0][8:12] != b'MOD0':
        fail(problems, 'built text has no MOD0 at +0x8')

    adrp_count = 0
    for pc in range(0, len(sr[0]), 4):
        old_word, = struct.unpack_from('<I', sr[0], pc)
        old_target = adrp_target(old_word, pc)
        if old_target is None or not old_ro <= old_target < old_image_end:
            continue
        new_word, = struct.unpack_from('<I', br[0], pc)
        new_target = adrp_target(new_word, pc)
        if new_target != old_target + delta:
            fail(problems, 'ADRP +0x%X -> 0x%X, want 0x%X' %
                 (pc, new_target if new_target is not None else -1,
                  old_target + delta))
            if len(problems) > 20:
                break
        adrp_count += 1

    pointer_count = 0
    for name, old, new in zip(('.rodata', '.data'), sr[1:], br[1:]):
        for off in range(0, len(old) - 3, 4):
            before, = struct.unpack_from('<I', old, off)
            after, = struct.unpack_from('<I', new, off)
            expected = before + delta if old_ro <= before < old_image_end else before
            if after != expected:
                fail(problems, '%s+0x%X is 0x%X, want 0x%X' %
                     (name, off, after, expected))
                if len(problems) > 20:
                    break
            if expected != before:
                pointer_count += 1

    mod0_count = 0
    for off in range(0x0C, 0x24, 4):
        before, = struct.unpack_from('<I', sr[0], off)
        after, = struct.unpack_from('<I', br[0], off)
        expected = before + delta if old_ro <= before < old_image_end else before
        if after != expected:
            fail(problems, 'MOD0+0x%X is 0x%X, want 0x%X' %
                 (off, after, expected))
        if expected != before:
            mod0_count += 1

    print('stock %s' % hashlib.sha256(stock).hexdigest())
    print('built %s' % hashlib.sha256(built).hexdigest())
    print('arena: +0x%X; usable RX 0x%X bytes' %
          (delta, new_ro - len(sr[0])))
    print('checked: %d ADRP, %d pointer words, %d MOD0 fields'
          % (adrp_count, pointer_count, mod0_count))
    if problems:
        print('%d PROBLEM(S):' % len(problems))
        for item in problems[:20]:
            print('  ' + item)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
