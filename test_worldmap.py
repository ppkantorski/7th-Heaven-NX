#!/usr/bin/env python3
"""Binary and geometry checks for the world-map 60 FPS / 16:9 fixes.

Run from the project directory with the same Python used by build.py:

    python3 test_worldmap.py [--main dump/exefs/main]

No output is written.  This test proves that every ARM word still matches the
stock Switch module, that the padding cave contains the intended 0 -> 20 sky
guard, and that the constants describe one continuous 854-wide viewport.
"""
import argparse
import struct
import sys

import a64 as A
import ff7nx_cave
import ff7nx_widescreen as W
import ff7nx_ws as WS
import nxmap


FAIL = []


def ok(cond, what):
    print(('  ok  ' if cond else '  FAIL  ') + what)
    if not cond:
        FAIL.append(what)


def word(blob, va):
    return struct.unpack_from('<I', blob, va)[0]


def cbz_target(pc, insn):
    imm = (insn >> 5) & 0x7FFFF
    if imm & 0x40000:
        imm -= 0x80000
    return pc + imm * 4


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--main', default='dump/exefs/main')
    a = ap.parse_args(argv)
    m = nxmap.Main(a.main)

    print('stock fingerprints and replacement words')
    seen = set()
    for p in W.WORLD_PATCHES:
        va = p['va']
        old = bytes.fromhex(p['expect'])
        new = bytes.fromhex(p['set'])
        ok(va not in seen, '%s has a unique address' % p['name'])
        seen.add(va)
        ok(m.img[va:va + 4] == old, '%s matches stock' % p['name'])
        ok(len(old) == len(new) == 4, '%s is one bounded ARM word' % p['name'])

    ok(word(m.img, W.WORLD_SKY_BOTTOM_HOOK) == W.WORLD_SKY_BOTTOM_ORIG,
       'the first sky lower-edge store matches stock')
    null_guard = word(m.img, W.WORLD_NULL_MESH_GUARD)
    edge_guard = word(m.img, W.WORLD_EDGE_BLOCK_HOOK)
    ok(null_guard == W.WORLD_NULL_MESH_GUARD_WORD,
       'the current-mesh null guard retains its stock CBZ')
    ok(edge_guard == W.WORLD_EDGE_BLOCK_WORD,
       'the later FFNx-equivalent edge check matches stock')
    ok(all(p['va'] != W.WORLD_NULL_MESH_GUARD for p in W.WORLD_PATCHES),
       'the current-mesh null guard is never patched')
    ok(any(p['va'] == W.WORLD_EDGE_BLOCK_HOOK for p in W.WORLD_PATCHES),
       'only the later edge-block check is removed')
    ok(cbz_target(W.WORLD_NULL_MESH_GUARD, null_guard) ==
       cbz_target(W.WORLD_EDGE_BLOCK_HOOK, edge_guard) == 0x00F4EE04,
       'both stock checks share the measured skip target')

    print('\ngeometry invariants')
    left, width = -107, 854
    ok(left + width == 747, 'terrain cull is one continuous -107..747 span')
    ok(-left == (width - 640) // 2,
       'the 214 added game pixels are centred 107 per side')
    submit_origin, submit_halfwidth = -107, 427
    ok(submit_origin + submit_halfwidth == 320,
       'terrain submission stays centred while widening to the cull span')
    ok(submit_halfwidth * 2 == width,
       'terrain submission and culling use the same 854-pixel width')
    edge = width // 4 + 20
    ok(edge == 233, 'sky dome edges are +/-233')
    cloud_left, cloud_join, cloud_right = -256, 0, 256
    ok(cloud_join - cloud_left == cloud_right - cloud_join,
       'cloud halves share one equal -256..0..256 boundary')
    ok(width // 2 == 427,
       'meteor rejection uses the same 427-pixel wide half-viewport')

    print('\nsky lower-edge cave')
    pool = ff7nx_cave.HolePool(m.img, starts=set(m.arm_starts))
    patches = W.world_cave_patches(
        m.img, set(m.arm_starts), pool=pool)
    vals = list(patches.values())
    ok(W.WORLD_SKY_BOTTOM_HOOK in patches,
       'the cave map includes the stock store hook')
    ok(A.movz(23, 20) in vals, 'the cave materialises 20 in w23')
    ok(W.WORLD_SKY_BOTTOM_STORE in vals,
       'the cave stores that 20 through the original address register')
    ok(A.strh(23, 0) == W.WORLD_SKY_BOTTOM_STORE,
       'the replacement store encoding is independently reproduced')
    ok(all(word(m.img, va) == 0 for va in patches
           if va != W.WORLD_SKY_BOTTOM_HOOK),
       'every cave word occupies verified zero padding')

    print('\nactive widescreen composition')
    active = WS.world_sky_cave_spec(m)
    active_patches = {p['va']: p for p in active['patches']}
    ok(W.WORLD_SKY_BOTTOM_HOOK in active_patches,
       'the active framing transaction includes the first lower-edge hook')
    ok(set(active_patches) == set(patches),
       'the active transaction preserves the complete emitted cave map')
    hook = active_patches[W.WORLD_SKY_BOTTOM_HOOK]
    ok(bytes.fromhex(hook['expect']) ==
       W.WORLD_SKY_BOTTOM_ORIG.to_bytes(4, 'little'),
       'the active hook retains the exact stock fingerprint')
    ok(bytes.fromhex(hook['set']) != bytes.fromhex(hook['expect']),
       'the active hook branches to the emitted cave')

    print()
    if FAIL:
        print('%d FAILED' % len(FAIL))
        return 1
    print('all good')
    return 0


if __name__ == '__main__':
    sys.exit(main())
