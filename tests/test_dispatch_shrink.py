#!/usr/bin/env python3
"""
test_dispatch_shrink.py -- the compact dispatcher cave against the original.

    python3 test_dispatch_shrink.py

WHY THIS EXISTS SEPARATELY FROM test_dispatch.py
================================================
test_dispatch.py checks the compact cave against a Python transcription of
FFNx's C. That is the stronger check for the paths it sweeps, but it sweeps
the paths that DO something. The shrink rearranged control flow -- cases were
merged and reordered, one op was hoisted into shared code, and two branches
were deleted in favour of falling through -- and the ways that goes wrong are
mostly on the paths that should do NOTHING:

  * a function the dispatcher does not name falling into the shared op
  * a slot whose first-frame flag is already clear being scaled anyway
  * a guarded case whose guard fails falling into the shared op
  * the last group's failing compare landing on SHARED instead of OUT

So this runs BOTH cave shapes -- `build_cave_reference`, the layout that has
been on hardware, and `build_cave`, the compact one -- on identical machine
state and compares everything observable afterwards: the exit address, every
byte of the slot table, the whole flag block, all 31 general registers, and
sp. Any difference at all is a failure.

The two builders are separate code, not one parameterised function, so a
mistake in the compact layout cannot quietly propagate into its own reference.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import struct
import sys

import a64 as A
import arm64emu
import ff7nx_dispatch as D
from ff7nx_dispatch_sites import DISPATCH_SITES

CAVE = 0x1152660
HOST = 0x700000000
CTX = 0x900000000
STACK = 0x800000000
K = 2

FAIL = []


def fail(msg):
    FAIL.append(msg)
    print('  FAIL  %s' % msg)


def run(words, site, mask_bits, data_base, stride, slots,
        idx, slot_bytes, fn_value, flag_value):
    """Execute one cave and return everything an observer could notice."""
    mem = arm64emu.Mem()
    cpu = arm64emu.Cpu(mem, host_base=HOST)
    cpu.sp = STACK
    for r in range(31):
        cpu.x[r] = arm64emu.GARBAGE
    cpu.x[site['ctx_reg']] = CTX
    mem.setu(CTX + site['idx_off'], idx, 4)
    cpu.x[site['fn_reg']] = fn_value

    n = slots + 2
    table = bytearray()
    for i in range(n):
        table += bytes(slot_bytes) if i == idx else bytes([0x5A] * stride)
    mem.write(HOST + data_base, bytes(table))

    block = 1 << mask_bits
    mem.write(site['_flag'], bytes([0x7E] * (block + 16)))
    mem.setu(site['_flag'] + (idx & (block - 1)), flag_value, 1)

    exit_pc = cpu.run(CAVE, words)
    return {
        'exit': exit_pc,
        'table': bytes(mem.read(HOST + data_base, n * stride)),
        'flags': bytes(mem.read(site['_flag'], block + 16)),
        # x16/x17 are IP0/IP1 and are the cave's declared scratch: the hook
        # sits on the instruction after a `bl` returns, so they are dead by
        # the AAPCS and BOTH shapes clobber them. Comparing them would only
        # assert that the two layouts leave the same rubbish behind, which is
        # not a property anything depends on -- reordering the ops changes
        # which field's value happens to be sitting there last. Every other
        # register, including x0 and the ctx/fn registers, must match exactly.
        'regs': tuple(cpu.x[r] for r in range(31) if r not in (16, 17)),
        'scratch': (cpu.x[16], cpu.x[17]),
        'sp': cpu.sp,
    }


def compare(tag, label, a, b):
    a = dict(a); b = dict(b)
    a.pop('scratch'); b.pop('scratch')
    if a == b:
        return True
    for key in ('exit', 'sp'):
        if a[key] != b[key]:
            fail('%s %s: %s reference 0x%X, compact 0x%X'
                 % (tag, label, key, a[key], b[key]))
    if a['table'] != b['table']:
        for i, (x, y) in enumerate(zip(a['table'], b['table'])):
            if x != y:
                fail('%s %s: slot table byte %d is 0x%02X, reference has '
                     '0x%02X' % (tag, label, i, y, x))
                break
    if a['flags'] != b['flags']:
        fail('%s %s: flag block differs' % (tag, label))
    if a['regs'] != b['regs']:
        names = [r for r in range(31) if r not in (16, 17)]
        for i, (x, y) in enumerate(zip(a['regs'], b['regs'])):
            if x != y:
                fail('%s %s: x%d is 0x%X, reference has 0x%X'
                     % (tag, label, names[i], y, x))
                break
    return False


def slot_pattern(stride, seed):
    """A slot filled so that a wrong offset or width moves visible bytes."""
    out = bytearray(stride)
    v = seed
    for i in range(stride):
        v = (v * 1103515245 + 12345) & 0xFFFFFFFF
        out[i] = (v >> 16) & 0xFF
    return bytes(out)


def main():
    print('reference layout vs compact layout, executed side by side\n')
    total = 0
    for tag in ('effect10', 'effect100', 'camera'):
        d = DISPATCH_SITES[tag]
        if d['flag'] is None or not d['cases']:
            continue
        sym = {n: va for n, va, _s, _g in d['cases']}
        cases = [(n, s, g) for n, _v, s, g in d['cases']]
        site = dict(d['disp_hook'])
        site['_flag'] = d['flag']

        args = (site, d['flag'], d['mask_bits'], d['data_base'], d['stride'],
                d['fields'], cases, sym, K)
        ref = D.build_cave_reference(CAVE, *args)
        new = D.build_cave(CAVE, None, *args)

        # Every named function, plus addresses that must match nothing: one
        # below the lowest case, one above the highest, one in between, and
        # zero. These are the inputs that exercise the "fall out of the
        # dispatch entirely" path the shrink rewrote.
        named = sorted(sym.values())
        strangers = [0, named[0] - 1, named[-1] + 1,
                     (named[0] + named[-1]) // 2 | 1]
        strangers = [s for s in strangers if s not in set(named)]

        checks = 0
        for fn_value in named + strangers:
            for flag_value in (0, 1, 0xFF):
                for idx in (0, 1, d['slots'] - 1):
                    for seed in (1, 0x7FFF, 0xDEAD):
                        slot = slot_pattern(d['stride'], seed)
                        a = run(ref, site, d['mask_bits'], d['data_base'],
                                d['stride'], d['slots'], idx, slot,
                                fn_value, flag_value)
                        b = run(new, site, d['mask_bits'], d['data_base'],
                                d['stride'], d['slots'], idx, slot,
                                fn_value, flag_value)
                        compare(tag, 'fn=0x%X flag=%d idx=%d seed=0x%X'
                                % (fn_value, flag_value, idx, seed), a, b)
                        checks += 1
                        if FAIL:
                            return 1

        # n_frames values that straddle the guarded case's `> 1` test, so the
        # guard-fails path is exercised on purpose rather than by luck.
        off, width, _s = d['fields']['n_frames']
        for fn_value in named:
            for nf in (0, 1, 2, 3, 0x7FFF, 0x8000, 0xFFFF):
                slot = bytearray(slot_pattern(d['stride'], 5))
                slot[off:off + width // 8] = (nf & ((1 << width) - 1)
                                              ).to_bytes(width // 8, 'little')
                a = run(ref, site, d['mask_bits'], d['data_base'],
                        d['stride'], d['slots'], 0, bytes(slot), fn_value, 1)
                b = run(new, site, d['mask_bits'], d['data_base'],
                        d['stride'], d['slots'], 0, bytes(slot), fn_value, 1)
                compare(tag, 'fn=0x%X n_frames=%d' % (fn_value, nf), a, b)
                checks += 1
                if FAIL:
                    return 1

        total += checks
        print('  ok    %-10s %3d -> %3d words (-%2d, %3d bytes freed)  '
              '%d state comparison(s)'
              % (tag, len(ref), len(new), len(ref) - len(new),
                 4 * (len(ref) - len(new)), checks))

    print('\n  %d full-state comparison(s), 0 difference(s)' % total)

    # The compact cave must still be a well-formed cave, not just an
    # equivalent one: same displaced word, same return, branches in range.
    print('\nstructure:')
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    for tag in ('effect10', 'effect100', 'camera'):
        d = DISPATCH_SITES[tag]
        sym = {n: va for n, va, _s, _g in d['cases']}
        cases = [(n, s, g) for n, _v, s, g in d['cases']]
        w = D.build_cave(CAVE, None, d['disp_hook'], d['flag'], d['mask_bits'],
                         d['data_base'], d['stride'], d['fields'], cases,
                         sym, K)
        blob = struct.pack('<%dI' % len(w), *w)
        decoded = list(md.disasm(blob, CAVE))
        ok = len(decoded) == len(w)
        if not ok:
            fail('%s: only %d of %d words decode' % (tag, len(decoded), len(w)))
        if w[-2] != d['disp_hook']['displaced']:
            fail('%s: displaced word not replayed' % tag)
        bls = [i for i, x in enumerate(w) if (x & 0xFC000000) == 0x94000000]
        if len(bls) != 1:
            fail('%s: %d bl instructions, want exactly 1' % (tag, len(bls)))
        print('  ok    %-10s %d words decode, one bl, displaced replayed'
              % (tag, len(w)))

    if FAIL:
        print('\nFAILED: %d' % len(FAIL))
        return 1

    # ---------------------------------------------------------- mutations
    # A comparison that cannot fail proves nothing. Each mutation below is a
    # plausible way to get the compact layout wrong; every one must be caught.
    print('\nmutations (each must be caught):')
    d = DISPATCH_SITES['effect10']
    sym = {n: va for n, va, _s, _g in d['cases']}
    cases = [(n, s, g) for n, _v, s, g in d['cases']]
    site = dict(d['disp_hook'])
    site['_flag'] = d['flag']
    args = (site, d['flag'], d['mask_bits'], d['data_base'], d['stride'],
            d['fields'], cases, sym, K)
    ref = D.build_cave_reference(CAVE, *args)

    def last_bne(words):
        idx = [i for i, x in enumerate(words)
               if (x & 0xFF00001F) == (0x54000000 | 1)]
        return idx[-1]

    def shared_start(words):
        # the shared op is the last three words before <displaced>, <b back>
        return len(words) - 2 - 3

    def mut_stranger_falls_into_shared(words):
        """The bug this layout is most likely to have: the final failing
        compare landing on SHARED, so an unnamed function gets scaled."""
        i = last_bne(words)
        return [A.bcond(CAVE + 4 * i, CAVE + 4 * shared_start(words), A.NE)
                if j == i else x for j, x in enumerate(words)]

    def mut_shared_dropped(words):
        """Shared op deleted: every case stops scaling n_frames."""
        s = shared_start(words)
        return words[:s] + [0xD503201F] * 3 + words[s + 3:]

    def mut_guard_shares(_words):
        """Let the guarded case fall into the shared op, so its scale runs
        even when `n_frames > 1` is false."""
        plan = D._plan_dispatch

        def patched(fields, cs, symtab):
            groups, shared = plan(fields, cs, symtab)
            if shared:
                for g in groups:
                    if g[2] is not None and shared in g[1]:
                        g[1].remove(shared)
                        g[2] = None
            return groups, shared
        D._plan_dispatch = patched
        try:
            return D.build_cave(CAVE, None, *args)
        finally:
            D._plan_dispatch = plan

    caught = 0
    muts = [('unnamed function falls into the shared op',
             mut_stranger_falls_into_shared),
            ('shared op dropped', mut_shared_dropped),
            ('guarded case allowed to share', mut_guard_shares)]
    for name, fn in muts:
        before = len(FAIL)
        broken = fn(D.build_cave(CAVE, None, *args))
        for fn_value in list(sym.values()) + [0, 0x999999]:
            for nf in (0, 1, 2, 5):
                slot = bytearray(slot_pattern(d['stride'], 3))
                off, width, _s = d['fields']['n_frames']
                slot[off:off + width // 8] = nf.to_bytes(width // 8, 'little')
                a = run(ref, site, d['mask_bits'], d['data_base'], d['stride'],
                        d['slots'], 0, bytes(slot), fn_value, 1)
                try:
                    b = run(broken, site, d['mask_bits'], d['data_base'],
                            d['stride'], d['slots'], 0, bytes(slot),
                            fn_value, 1)
                except Exception:
                    FAIL.append('%s raised' % name)
                    break
                a2 = dict(a); b2 = dict(b)
                a2.pop('scratch'); b2.pop('scratch')
                if a2 != b2:
                    FAIL.append('%s differs' % name)
                    break
            if len(FAIL) > before:
                break
        if len(FAIL) == before:
            print('  FAIL  NOT caught: %s' % name)
        else:
            del FAIL[before:]
            print('  ok    caught: %s' % name)
            caught += 1

    print('\n%d/%d mutation(s) caught' % (caught, len(muts)))
    if caught != len(muts):
        FAIL.append('a mutation went undetected')
    print('\n%s' % ('FAILED: %d' % len(FAIL) if FAIL else 'all checks passed'))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
