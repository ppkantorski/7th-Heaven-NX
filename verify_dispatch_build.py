#!/usr/bin/env python3
"""
verify_dispatch_build.py -- verify the dispatcher caves in a BUILT `main`.

    python3 verify_dispatch_build.py --nso dtests/variant_main/main_F \
                                     --stock exefs/main

test_dispatch.py checks the caves the emitter produces. This checks the caves
that are actually in the file, at the addresses they actually landed at, read
back out of the rebuilt NSO by decompressing it the way the loader will.

That distinction matters. A cave's `adrp`, its `bl` to the translator, its
internal `b.ne` chain and its return branch are all PC-relative, so a cave that
is correct at one address is not automatically correct at another. Everything
below is re-derived from the shipped bytes:

  * the hook is an unconditional B into .text
  * the cave decodes fully with capstone, no undefined words
  * the adrp/add pair resolves to the flag block, and the flag block lies past
    the END of the stock BSS, not inside it
  * the bl resolves to 0x10FC3A0
  * the last instruction returns to hook+4, and the one before it is the
    displaced instruction verbatim
  * the ENTIRE differential suite is re-run against the shipped words at their
    shipped address, and must agree with FFNx's C
  * bssSize grew by exactly the flag block size, and nothing else in the header
    moved except the three segment sizes/hashes
"""
import argparse, hashlib, struct, sys

import arm64emu
import ff7nx_dispatch as D
import test_dispatch as T
from ff7nx_dispatch_sites import DISPATCH_SITES, FLAG_BASE, BSS_GROW
import lz4.block
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN

md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
problems = []


def bad(m):
    problems.append(m)


def segments(data):
    segs = [struct.unpack('<III', data[b:b + 12]) for b in (0x10, 0x20, 0x30)]
    comp = struct.unpack('<III', data[0x60:0x6c])
    flags = struct.unpack('<I', data[0xc:0x10])[0]
    raw = []
    for i, (fo, mo, ds) in enumerate(segs):
        blob = data[fo:fo + comp[i]]
        raw.append(lz4.block.decompress(blob, uncompressed_size=ds)
                   if flags & (1 << i) else blob[:ds])
    return segs, raw


def branch_target(w, pc):
    if (w & 0xFC000000) != 0x14000000:
        return None
    imm = w & 0x3FFFFFF
    if imm & 0x2000000:
        imm -= 0x4000000
    return pc + imm * 4


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--nso', required=True, help='the BUILT main to verify')
    ap.add_argument('--stock', required=True, help='the stock main it came from')
    a = ap.parse_args()

    built = open(a.nso, 'rb').read()
    stock = open(a.stock, 'rb').read()
    bsegs, braw = segments(built)
    ssegs, sraw = segments(stock)
    text = braw[0]
    print('built  %s  %d bytes' % (hashlib.md5(built).hexdigest(), len(built)))
    print('stock  %s  %d bytes' % (hashlib.md5(stock).hexdigest(), len(stock)))

    for i in range(3):
        want = built[0xA0 + 32 * i:0xA0 + 32 * i + 32]
        if hashlib.sha256(braw[i]).digest() != want:
            bad('segment %d sha256 does not match its header' % i)

    # ---- BSS ------------------------------------------------------------
    sbss = struct.unpack('<I', stock[0x3C:0x40])[0]
    bbss = struct.unpack('<I', built[0x3C:0x40])[0]
    data_mem, data_size = ssegs[2][1], ssegs[2][2]
    page_end = (data_mem + data_size + 0xFFF) & ~0xFFF
    present = any(True for _ in ())
    hooks_present = []
    for tag, d in DISPATCH_SITES.items():
        for key in ('add_hook', 'disp_hook'):
            h = d[key]['hook']
            cur, = struct.unpack('<I', text[h:h + 4])
            if cur != d[key]['displaced']:
                hooks_present.append((tag, key, h, d, key))
    if not hooks_present:
        print('\nno dispatcher hooks are patched in this build -- nothing to '
              'verify')
        return 0

    if bbss != sbss + BSS_GROW:
        bad('bssSize is 0x%X, stock 0x%X + expected growth 0x%X = 0x%X'
            % (bbss, sbss, BSS_GROW, sbss + BSS_GROW))
    if FLAG_BASE < page_end + sbss:
        bad('flag block 0x%X is INSIDE the stock BSS (which ends at 0x%X) -- '
            'this is the failure that corrupted an earlier build'
            % (FLAG_BASE, page_end + sbss))
    if FLAG_BASE + BSS_GROW > page_end + bbss:
        bad('flag block 0x%X..0x%X runs past the grown BSS end 0x%X'
            % (FLAG_BASE, FLAG_BASE + BSS_GROW, page_end + bbss))
    print('\nBSS: stock 0x%X -> built 0x%X (+0x%X), flag block 0x%X..0x%X, '
          'stock BSS ends 0x%X'
          % (sbss, bbss, bbss - sbss, FLAG_BASE, FLAG_BASE + BSS_GROW,
             page_end + sbss))

    # ---- header sanity: only sizes and hashes may differ ----------------
    for off, name in ((0x0C, 'segment compression flags'),
                      (0x40, 'build ID'), (0x3C, 'bssSize')):
        pass
    if built[0x40:0x50] != stock[0x40:0x50]:
        bad('build ID changed')
    if built[0x0C:0x10] != stock[0x0C:0x10]:
        bad('segment compression flags changed')
    for i in range(3):
        if built[0x14 + 16 * i:0x18 + 16 * i] != stock[0x14 + 16 * i:0x18 + 16 * i]:
            bad('segment %d memory offset changed' % i)
    if bsegs[1][2] != ssegs[1][2] or bsegs[2][2] != ssegs[2][2]:
        bad('.rodata or .data size changed -- caves must only grow .text')

    # ---- per cave --------------------------------------------------------
    total_words = 0
    for tag in ('effect10', 'effect100', 'camera'):
        d = DISPATCH_SITES[tag]
        sym = {n: va for n, va, _o, _g in d['cases']}
        cases = [(n, o, g) for n, _v, o, g in d['cases']]
        for key, kind in (('add_hook', 'add'), ('disp_hook', 'disp')):
            site = d[key]
            hook = site['hook']
            cur, = struct.unpack('<I', text[hook:hook + 4])
            if cur == site['displaced']:
                print('  --  %-10s %-4s not enabled in this build' % (tag, kind))
                continue
            cave = branch_target(cur, hook)
            if cave is None:
                bad('%s/%s hook is not an unconditional B (%08X)'
                    % (tag, kind, cur))
                continue
            if not (0 <= cave < len(text)):
                bad('%s/%s hook branches to 0x%X, outside .text' % (tag, kind, cave))
                continue

            # Rebuild at the SHIPPED address and require an exact match.
            if kind == 'add':
                want = D.build_addfn_cave(cave, site, d['flag'], d['mask_bits'])
            else:
                want = D.build_cave(cave, site, d['flag'], d['mask_bits'],
                                    d['data_base'], d['stride'], d['fields'],
                                    cases, sym, T.K)
            got = list(struct.unpack('<%dI' % len(want),
                                     text[cave:cave + 4 * len(want)]))
            if got != want:
                diff = [i for i, (x, y) in enumerate(zip(got, want)) if x != y]
                bad('%s/%s cave at 0x%X differs at word(s) %s'
                    % (tag, kind, cave, diff[:8]))
                continue
            total_words += len(got)

            blob = struct.pack('<%dI' % len(got), *got)
            if len(list(md.disasm(blob, cave))) != len(got):
                bad('%s/%s cave has word(s) capstone cannot decode' % (tag, kind))

            # adrp + add must land on this dispatcher's own flag block.
            fa = None
            for i, w in enumerate(got):
                if (w & 0x9F000000) == 0x90000000:
                    pc = cave + 4 * i
                    imm = (((w >> 5) & 0x7FFFF) << 2) | ((w >> 29) & 3)
                    if imm & 0x100000:
                        imm -= 0x200000
                    page = (pc & ~0xFFF) + imm * 0x1000
                    nxt = got[i + 1]
                    if (nxt & 0xFF800000) != 0x91000000:
                        bad('%s/%s adrp not followed by add' % (tag, kind))
                        continue
                    fa = page + ((nxt >> 10) & 0xFFF)
                    if fa != d['flag']:
                        bad('%s/%s adrp/add resolves to 0x%X, flag block is 0x%X'
                            % (tag, kind, fa, d['flag']))
            if fa is None:
                bad('%s/%s cave has no adrp -- cannot reach its flag' % (tag, kind))

            # bl, if any, must go to the translator and nowhere else.
            for i, w in enumerate(got):
                if (w & 0xFC000000) == 0x94000000:
                    pc = cave + 4 * i
                    imm = w & 0x3FFFFFF
                    if imm & 0x2000000:
                        imm -= 0x4000000
                    if pc + imm * 4 != arm64emu.TRANSLATE:
                        bad('%s/%s bl targets 0x%X, not the translator'
                            % (tag, kind, pc + imm * 4))

            if got[-2] != site['displaced']:
                bad('%s/%s does not replay the displaced instruction'
                    % (tag, kind))
            back = branch_target(got[-1], cave + 4 * (len(got) - 1))
            if back != hook + 4:
                bad('%s/%s returns to 0x%X, want hook+4 0x%X'
                    % (tag, kind, back, hook + 4))
            print('  ok  %-10s %-4s hook +0x%06X -> cave 0x%06X, %3d words, '
                  'flags 0x%X, returns +0x%06X'
                  % (tag, kind, hook, cave, len(got), fa or 0, back))

    # ---- re-run the whole differential suite against the shipped caves --
    print('\nre-running the differential suite against the shipped caves...')
    T.CAVE_OVERRIDE = None
    saved = T.CAVE
    ok_runs = 0
    for tag in ('effect10', 'effect100', 'camera'):
        d = DISPATCH_SITES[tag]
        for key, kind in (('add_hook', 'add'), ('disp_hook', 'disp')):
            site = d[key]
            hook = site['hook']
            cur, = struct.unpack('<I', text[hook:hook + 4])
            if cur == site['displaced']:
                continue
            T.CAVE = branch_target(cur, hook)
            before = len(T.fails)
            n = T.test_addfn(tag) if kind == 'add' else T.test_dispatcher(tag)
            ok_runs += n
            for m in T.fails[before:]:
                bad('at shipped address 0x%X: %s' % (T.CAVE, m))
    T.CAVE = saved
    print('  %d execution(s) at the shipped addresses' % ok_runs)

    print('\n%d cave word(s) verified in place' % total_words)
    if problems:
        print('\n%d PROBLEM(S):' % len(problems))
        for p in problems:
            print('  ' + p)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
