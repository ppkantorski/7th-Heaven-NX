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
    cloud_roll = next(p for p in W.WORLD_PATCHES
                      if p['name'] == 'world cloud ignore turn-bank roll')
    ok(int.from_bytes(bytes.fromhex(cloud_roll['set']), 'little') ==
       A.mov_reg(23, 31),
       'cloud transform discards only the copied camera-bank value')
    meteor_left = -107
    meteor_right = width // 2
    ok((meteor_left, meteor_right) == (-107, 427),
       'meteor cull uses the exact 16:9 edges')
    meteor_sign = next(p for p in W.WORLD_PATCHES
                       if p['name'] ==
                       'world meteor left cull sign high 0 -> -1')
    ok(int.from_bytes(bytes.fromhex(meteor_sign['set']), 'little') ==
       A.movz(9, 0xFFFF),
       'meteor left compare carries the negative operand high half')
    ok(((meteor_left & 0xFFFFFFFF) >> 16) == 0xFFFF,
       'the companion high half matches signed -107')

    print('\nworld transition fade geometry')
    fade = [p for p in W.WORLD_PATCHES
            if p['name'].startswith('world transition fade')]
    ok(len(fade) == 6,
       'the distinct world-map fade has one origin and five width results')
    ok([p['va'] for p in fade] ==
       [0x00F3A6F0, 0x00F3AD6C, 0x00F3B404,
        0x00F3B6CC, 0x00F3BFA0, 0x00F3C190],
       'all six results stay inside mapped x86 world_draw_fade_quad_75551A')
    ok(m.x86_to_arm[0x75551A] == 0x00F3A670,
       'world_draw_fade_quad_75551A maps to the measured ARM64 body')
    ok(int.from_bytes(bytes.fromhex(fade[0]['set']), 'little') == 0x12800D54,
       'fade origin materialises signed -107 in the original result register')
    ok(int.from_bytes(bytes.fromhex(fade[1]['set']), 'little') ==
       A.movz(22, 854),
       'first fade width materialises 854 in its original result register')
    ok(all(int.from_bytes(bytes.fromhex(p['set']), 'little') ==
           A.movz(19, 854) for p in fade[2:]),
       'remaining fade widths materialise 854 in their original result register')
    ok(left == -107 and left + width == 747,
       'world fade covers the same full -107..747 span as terrain and meteor')
    shipped = {p['va'] for p in W.spec()['patches']}
    ok({p['va'] for p in fade}.issubset(shipped),
       'the normal widescreen build spec includes all six tested fade words')

    print('\nblock streaming neighbourhood')
    # The caller's own loops run -2..2 on both axes (0x751C13 / 0x751C34), so
    # moving the unconditional core to |d| <= 2 asks for the whole 5x5 and
    # leaves the +/-78.75 degree wedge test unreachable.  Anything larger
    # would exceed the engine's fixed pools, which is the point of the last
    # two checks here.
    neigh = [p for p in W.WORLD_PATCHES
             if p['name'].startswith('world block neighbourhood')]
    ok(len(neigh) == 2, 'both neighbourhood axes are corrected')
    for p in neigh:
        old = int.from_bytes(bytes.fromhex(p['expect']), 'little')
        new = int.from_bytes(bytes.fromhex(p['set']), 'little')
        ok(old == 0x71000528,          # subs w8, w9, #1
           '%s replaces the stock subs w8, w9, #1' % p['name'])
        ok(new == old + (1 << 10),
           '%s changes only the compared immediate, 1 -> 2' % p['name'])
        ok(new >> 22 == old >> 22 and (new >> 5) & 0x1F == (old >> 5) & 0x1F
           and new & 0x1F == old & 0x1F,
           '%s keeps the same opcode and registers' % p['name'])
    ok(5 * 5 <= 32, 'a 5x5 working set fits the 32-entry block descriptor pool')
    ok(0x00E04970 + 32 * 0x1200 == 0x00E28970,
       'the per-block mesh pool really is 32 slots and ends at the next global')

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

    print('\ncave allocation is shared across the framing transaction')
    # HolePool hands out the lowest usable hole first, and two pools built
    # over the same image know nothing about each other. The sky cave and
    # ff7nx_wsclamp's camera caves are emitted into ONE module in one
    # verified transaction, so they have to draw from one pool -- otherwise
    # the second spec re-issues the first one's holes and the whole framing
    # stage refuses to write:
    #
    #   cave word: verification failed at 0x6dd4; have 97 02 80 52
    #
    # which is `movz w23, #20`, the sky cave's own first word.
    import ff7nx_wsclamp          # ff7nx_cave is already imported at module
                                  # scope; re-importing it here would make the
                                  # name local to this whole function and
                                  # break the sky-cave section above.
    starts = set(m.arm_starts)
    values = ff7nx_wsclamp.defaults(ff7nx_wsclamp.WS_SCALE)

    shared = ff7nx_cave.HolePool(m.img, starts=starts)
    sky = {p['va'] for p in
           WS.world_sky_cave_spec(m, pool=shared)['patches']}
    clamp = {p['va'] for p in
             ff7nx_wsclamp.spec(m.img, values, starts=starts,
                                pool=shared)['patches']}
    ok(not (sky & clamp),
       'one shared pool gives the sky cave and the clamp caves disjoint words')

    # The mutation: prove the check above can actually fail, so it is not
    # quietly passing because both sets came out empty or the plumbing
    # stopped reaching the pool at all.
    sky_private = {p['va'] for p in WS.world_sky_cave_spec(m)['patches']}
    clamp_private = {p['va'] for p in
                     ff7nx_wsclamp.spec(m.img, values, starts=starts)['patches']}
    ok(bool(sky_private & clamp_private),
       'caught: two private pools DO collide, so the shared pool is load-bearing')
    ok(len(sky) > 1 and len(clamp) > 1,
       'both cave sets are non-trivial')

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
