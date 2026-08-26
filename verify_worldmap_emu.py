#!/usr/bin/env python3
"""
verify_worldmap_emu.py -- run the two world-map decisions under arm64emu.

Neither of the corrections in this pass is a value substitution that can be
checked by eye.  Both live inside translated x86 comparisons whose flag
bookkeeping the recompiler spread over four extra instructions, so the only
honest check is to execute the ACTUAL ENCODED WORDS and watch which way the
branch goes.

    meteor   -- world_submit_draw_clouds_and_meteor_7547A6 + 0x5A3
                x86:  cmp <meteor_right_x>, [viewport_x] ; jle reject
                Swept over the meteor's projected x, stock vs each patch
                state, printing the first x at which the effect is dropped.

    neighbourhood -- world_compute_block_visibility_751AC4 + 0x199
                x86:  if (|dx| <= 1 && |dy| <= 1) want = 1; else <frustum>
                Run for every (dx, dy) in the 5x5 the caller iterates, showing
                which cells take the unconditional "wanted" path.

Run:  python3 verify_worldmap_emu.py [--main path/to/exefs/main]
"""
import argparse
import struct
import sys

import nxmap
import arm64emu

REJECT = 0x00F3A628          # the meteor's common rejection block
METEOR_LO = 0x00F3A350
METEOR_HI = 0x00F3A3B0       # falling through to here means "submitted"

VIEWPORT_X = 0x00E2C424      # game-space left edge global, stock 0
W20_BASE = VIEWPORT_X - 0x880

NEIGH_LO = 0x00F5253C
NEIGH_HI = 0x00F526A0        # the per-block frustum test (x86 0x751CAF)
WANTED = 0x00F528D8          # the unconditional "this block is wanted" store

EBP = 0x00100000
STATE = 0x00800000           # host address of the recompiler's register block


def load(main):
    return nxmap.Main(main)


def code_map(img, lo, hi, overrides=None):
    m = {}
    for va in range(lo, hi, 4):
        m[va] = struct.unpack('<I', img[va:va + 4])[0]
    for va, word in (overrides or {}).items():
        if lo <= va < hi:
            m[va] = word
    return m


def new_cpu():
    mem = arm64emu.Mem()
    cpu = arm64emu.Cpu(mem)
    cpu.x[19] = STATE
    cpu.x[22] = STATE
    return cpu


def guest_w(cpu, va, val, n=4):
    cpu.mem.setu(cpu.guest_to_host(va), val & ((1 << (8 * n)) - 1), n)


# ------------------------------------------------------------------ meteor
def meteor_visible(img, meteor_x, overrides=None):
    """True if the meteor quad survives the left-edge test at this x."""
    code = code_map(img, METEOR_LO, METEOR_HI, overrides)
    cpu = new_cpu()
    cpu.x[20] = W20_BASE
    cpu.mem.setu(STATE + 0x14, EBP, 4)          # ebp
    guest_w(cpu, EBP - 0xC8, meteor_x & 0xFFFFFFFF)
    guest_w(cpu, VIEWPORT_X, 0)                 # stock viewport_x
    out = cpu.run(METEOR_LO, [], code=code, start_pc=METEOR_LO)
    if out == REJECT:
        return False
    if out == METEOR_HI:
        return True
    raise SystemExit('meteor run left the block at unexpected 0x%X' % out)


def meteor_edge(img, overrides=None):
    """Lowest x at which the meteor is still submitted."""
    last = None
    for x in range(-600, 200):
        if meteor_visible(img, x, overrides):
            last = x
            break
    return last


# ----------------------------------------------------------- neighbourhood
def block_wanted(img, dx, dy, overrides=None):
    code = code_map(img, NEIGH_LO, NEIGH_HI, overrides)
    cpu = new_cpu()
    cpu.x[8] = EBP
    cpu.mem.setu(STATE + 0x14, EBP, 4)
    guest_w(cpu, EBP - 0x60, dx & 0xFFFF, 2)
    guest_w(cpu, EBP - 0x64, dy & 0xFFFF, 2)
    out = cpu.run(NEIGH_LO, [], code=code, start_pc=NEIGH_LO)
    if out == WANTED:
        return True
    if out == NEIGH_HI:
        return False                            # falls into the frustum test
    raise SystemExit('neighbourhood run left the block at 0x%X' % out)


def grid(img, overrides=None):
    rows = []
    for dy in range(-2, 3):
        rows.append(''.join('#' if block_wanted(img, dx, dy, overrides)
                            else '.' for dx in range(-2, 3)))
    return rows


# ------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--main', default='dump/exefs/main')
    args = ap.parse_args(argv)
    img = load(args.main).img

    import ff7nx_widescreen as W
    by_name = {p['name']: p for p in W.WORLD_PATCHES}

    def words(*names):
        out = {}
        for n in names:
            p = by_name[n]
            out[p['va']] = struct.unpack(
                '<I', bytes.fromhex(p['set'].replace(' ', '')))[0]
        return out

    right = words('world meteor cull halfwidth 320 -> 427')

    print('meteor left edge -- lowest game-space x still submitted')
    print('  the 16:9 viewport starts at x = -107; 4:3 started at x = 0')
    stock = meteor_edge(img, right)
    print('  stock left test                     : %s' % stock)
    only_214 = dict(right)
    only_214[0x00F3A374] = struct.unpack('<I', bytes.fromhex('A81A8012'))[0]
    print('  build b1f92809 (-214, value only)   : %s' % meteor_edge(img, only_214))
    fixed = words('world meteor left cull sign high 0 -> -1',
                  'world meteor cull left 0 -> -107',
                  'world meteor cull halfwidth 320 -> 427')
    got = meteor_edge(img, fixed)
    print('  two-word signed correction          : %s' % got)
    ok = [got == -106]
    print('  %s expected -106 (first x strictly greater than -107)'
          % ('ok  ' if ok[-1] else 'FAIL'))

    print()
    print('block neighbourhood requested by world_stream_blocks_75164A')
    print('  rows are dy = -2..2, columns dx = -2..2; # = unconditionally wanted')
    print('  stock:')
    for r in grid(img):
        print('    ' + r)
    nb = words('world block neighbourhood dx 1 -> 2',
               'world block neighbourhood dy 1 -> 2')
    print('  patched:')
    after = grid(img, nb)
    for r in after:
        print('    ' + r)
    ok.append(all(c == '#' for r in after for c in r))
    print('  %s expected the whole 5x5 to be unconditional'
          % ('ok  ' if ok[-1] else 'FAIL'))

    print()
    print('all good' if all(ok) else 'FAILURES ABOVE')
    return 0 if all(ok) else 1


if __name__ == '__main__':
    sys.exit(main())
