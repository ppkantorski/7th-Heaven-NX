#!/usr/bin/env python3
"""
test_shared_prologue.py -- ff7nx_shared_prologue.py's stub+bl+shared
mechanism against build_cave_reference's inline prologue, the shape that has
been on hardware. Same rule as test_dispatch_shrink.py: any difference at
all in observable state is a failure.

    python3 tests/test_shared_prologue.py

TWO LEVELS, BOTH REAL
----------------------
PART 1 -- prologue only, isolated. build_cave_reference(cases=[]) reduces
to exactly the flag check + consume + translated-pointer computation, with
the cbz on "not set" and the fallthrough on "set" landing on the SAME `out`
(because there is nothing to dispatch on). That is compared against
stub+bl SHARED+cbz, across every combination of idx / flag byte /
register poison, for all three real sites.

PART 2 -- a full, real cave: stub+SHARED followed by the site's ACTUAL
dispatch cases, using the exact same case-emission code build_cave_reference
itself calls (D._ops, the same A.* encoders) -- not a re-derivation of it --
diffed against the real build_cave_reference output for that site, with its
real field table and real case list. This is the end-to-end proof that a
cave built this way, with real dispatch logic appended afterwards, behaves
identically to the hardware-verified shape, and it reports the real byte
count so the savings claim is measured, not asserted.

Register comparison excludes x16/x17, for the same reason
test_dispatch_shrink.py excludes them: they are the cave's declared
scratch (IP0/IP1), dead by the AAPCS at the hook site, and the shared
mechanism deliberately uses a DIFFERENT one of the two to hold the flag
pointer than the reference does (x16 vs x17) -- comparing them would only
assert the two shapes leave the same rubbish behind, which nothing depends
on.

x30/LR is ALSO excluded, which test_dispatch_shrink.py did not need to do
(its cave never issues an *unconditional* bl -- build_cave_reference only
calls TRANSLATE on the branch where the flag is set). The shared mechanism
must reach SHARED with a real `bl` on every call, set or not, since the
flag test itself lives inside SHARED -- so x30 gets written even on the
path where the reference makes no call at all. Whether that is actually
safe rests on the reference's OWN behavior: on every frame where the flag
IS set, the reference already lets `bl TRANSLATE` clobber x30 with no
save/restore, and that has shipped and works. That is direct hardware
evidence x30 is dead-in at this hook, not an assumption -- the same
standard x16/x17's exclusion already rests on. Every other register,
x0 included, must match exactly.
"""
import struct
import sys

import a64 as A
import arm64emu
import ff7nx_dispatch as D
import ff7nx_shared_prologue as SP
from ff7nx_dispatch_sites import DISPATCH_SITES

CAVE = 0x1152660
HOST = 0x700000000
CTX = 0x900000000
STACK = 0x800000000
# Real address of the .rodata tail gap found and verified against the
# shipped main NSO (0x126cc38 .. 0x126d000, 968 bytes, R--). Site records
# are placed 12 bytes apart starting there, same order build.py would use.
TABLE_BASE = 0x126CC38
K = 2

FAIL = []


def fail(msg):
    FAIL.append(msg)
    print('  FAIL  %s' % msg)


def site_of(tag):
    d = DISPATCH_SITES[tag]
    site = dict(d['disp_hook'])
    site['_flag'] = d['flag']
    return d, site


def new_cave_words(d, site, entry_va, with_cases):
    """
    Build stub+SHARED(+real dispatch cases, if with_cases) as ONE contiguous
    blob starting at CAVE, exactly the layout a real build would use for a
    single site (shared would only be emitted once across all sites in a
    real build; testing each site with its own private copy is faithful
    because the shared body is position-independent and carries no
    site-specific constant -- see its docstring).

    Returns (words, table_entry_bytes).
    """
    stub_len = SP.stub_word_count()
    shared_va = CAVE + 4 * stub_len
    shared_len = SP.shared_word_count()
    dispatch_start = shared_va + 4 * shared_len

    stub, cbz_i = SP.build_dispatch_stub(CAVE, site, d['mask_bits'], entry_va,
                                         shared_va)
    assert len(stub) == stub_len
    shared = SP.build_shared_prologue(shared_va)
    assert len(shared) == shared_len

    w = list(stub) + list(shared)

    if with_cases:
        sym = {n: va for n, va, _s, _g in d['cases']}
        cases = [(n, s, g) for n, _v, s, g in d['cases']]
        fields = d['fields']

        def pc():
            return CAVE + 4 * len(w)

        out_jumps = []
        F = site['fn_reg']
        for name, spec, guard in cases:
            target = sym[name]
            w.append(A.movz(17, target & 0xFFFF))
            w.append(A.movk_hi(17, (target >> 16) & 0xFFFF))
            w.append(A.cmp_reg(F, 17))
            bne_i = len(w)
            w.append(0)
            guard_i = None
            if guard == 'n_frames>1':
                off, width, _s = fields['n_frames']
                w.append(A.ldrsh(17, 0, off))
                w.append(A.cmp_imm(17, 1))
                guard_i = len(w)
                w.append(0)
            elif guard is not None:
                raise SystemExit('unknown guard %r' % guard)
            w += D._ops(fields, spec, K)
            if guard_i is not None:
                w[guard_i] = A.bcond(CAVE + 4 * guard_i, pc(), A.LE)
            out_jumps.append(len(w))
            w.append(0)
            w[bne_i] = A.bcond(CAVE + 4 * bne_i, pc(), A.NE)
        out = pc()
        for i in out_jumps:
            w[i] = A.b(CAVE + 4 * i, out)
    else:
        out = CAVE + 4 * len(w)

    w[cbz_i] = A.cbz(16, CAVE + 4 * cbz_i, out)
    w.append(site['displaced'])
    w.append(A.b(CAVE + 4 * (len(w)), site['hook'] + 4))

    entry_bytes = SP.pack_table_entry(d['flag'], entry_va, d['data_base'],
                                      d['stride'])
    return w, entry_bytes


def ref_cave_words(d, site, with_cases):
    if with_cases:
        sym = {n: va for n, va, _s, _g in d['cases']}
        cases = [(n, s, g) for n, _v, s, g in d['cases']]
    else:
        sym, cases = {}, []
    return D.build_cave_reference(CAVE, site, d['flag'], d['mask_bits'],
                                  d['data_base'], d['stride'], d['fields'],
                                  cases, sym, K)


def run(words, site, mask_bits, data_base, stride, n_slots, idx, slot_bytes,
       fn_value, flag_value, entry_va=None, entry_bytes=None,
       base=CAVE, start_pc=None, extra_entries=None):
    mem = arm64emu.Mem()
    cpu = arm64emu.Cpu(mem, host_base=HOST)
    cpu.sp = STACK
    for r in range(31):
        cpu.x[r] = arm64emu.GARBAGE
    cpu.x[site['ctx_reg']] = CTX
    mem.setu(CTX + site['idx_off'], idx, 4)
    cpu.x[site['fn_reg']] = fn_value

    n = n_slots + 2
    table = bytearray()
    for i in range(n):
        table += bytes(slot_bytes) if i == idx else bytes([0x5A] * stride)
    mem.write(HOST + data_base, bytes(table))

    block = 1 << mask_bits
    mem.write(site['_flag'], bytes([0x7E] * (block + 16)))
    mem.setu(site['_flag'] + (idx & (block - 1)), flag_value, 1)

    if entry_va is not None:
        mem.write(entry_va, entry_bytes)
    # extra_entries: (entry_va, entry_bytes) pairs for OTHER sites that share
    # this same combined image but are not the one under test this call --
    # written so the memory image is fully realistic (a real build has all
    # three live at once), even though only one site's flag/ctx is armed.
    if extra_entries:
        for eva, ebytes in extra_entries:
            mem.write(eva, ebytes)

    exit_pc = cpu.run(base, words, start_pc=start_pc)
    return {
        'exit': exit_pc,
        'table': bytes(mem.read(HOST + data_base, n * stride)),
        'flags': bytes(mem.read(site['_flag'], block + 16)),
        'regs': tuple(cpu.x[r] for r in range(31) if r not in (16, 17, 30)),
        'scratch': (cpu.x[16], cpu.x[17], cpu.x[30]),
        'sp': cpu.sp,
    }


def compare(tag, label, a, b):
    a = dict(a); b = dict(b)
    a.pop('scratch'); b.pop('scratch')
    if a == b:
        return True
    for key in ('exit', 'sp'):
        if a[key] != b[key]:
            fail('%s %s: %s reference 0x%X, new 0x%X'
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
        names = [r for r in range(31) if r not in (16, 17, 30)]
        for i, (x, y) in enumerate(zip(a['regs'], b['regs'])):
            if x != y:
                fail('%s %s: x%d is 0x%X, reference has 0x%X'
                     % (tag, label, names[i], y, x))
                break
    return False


def slot_pattern(stride, seed):
    out = bytearray(stride)
    v = seed
    for i in range(stride):
        v = (v * 1103515245 + 12345) & 0xFFFFFFFF
        out[i] = (v >> 16) & 0xFF
    return bytes(out)


def part1_prologue_only():
    print('PART 1 -- prologue only (stub+SHARED vs build_cave_reference, '
         'cases=[])\n')
    total = 0
    for idx_i, tag in enumerate(('effect10', 'effect100', 'camera')):
        d, site = site_of(tag)
        entry_va = TABLE_BASE + 12 * idx_i
        ref = ref_cave_words(d, site, with_cases=False)
        new, entry_bytes = new_cave_words(d, site, entry_va, with_cases=False)

        checks = 0
        for fn_value in (0, 0xDEAD0000 | 1, 0x7D26B8):
            for flag_value in (0, 1, 0xFF):
                for idx in (0, 1, d['slots'] - 1):
                    for seed in (1, 0x7FFF, 0xDEAD):
                        slot = slot_pattern(d['stride'], seed)
                        a = run(ref, site, d['mask_bits'], d['data_base'],
                               d['stride'], d['slots'], idx, slot,
                               fn_value, flag_value)
                        b = run(new, site, d['mask_bits'], d['data_base'],
                               d['stride'], d['slots'], idx, slot,
                               fn_value, flag_value, entry_va, entry_bytes)
                        compare(tag, 'fn=0x%X flag=%d idx=%d seed=0x%X'
                               % (fn_value, flag_value, idx, seed), a, b)
                        checks += 1
                        if FAIL:
                            return False
        total += checks
        print('  ok    %-10s %3d comparison(s), reference %2d words, '
             'stub+shared %2d words' % (tag, checks, len(ref), len(new)))
    print('\n  %d full-state comparison(s), 0 difference(s)\n' % total)
    return True


def part2_full_cave():
    print('PART 2 -- full cave, real dispatch cases appended\n')
    total = 0
    saved_words = 0
    for idx_i, tag in enumerate(('effect10', 'effect100', 'camera')):
        d, site = site_of(tag)
        entry_va = TABLE_BASE + 12 * idx_i
        ref = ref_cave_words(d, site, with_cases=True)
        new, entry_bytes = new_cave_words(d, site, entry_va, with_cases=True)

        sym = {n: va for n, va, _s, _g in d['cases']}
        named = sorted(sym.values())
        strangers = [0, named[0] - 1, named[-1] + 1,
                    (named[0] + named[-1]) // 2 | 1]
        strangers = [x for x in strangers if x not in set(named)]

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
                               fn_value, flag_value, entry_va, entry_bytes)
                        compare(tag, 'fn=0x%X flag=%d idx=%d seed=0x%X'
                               % (fn_value, flag_value, idx, seed), a, b)
                        checks += 1
                        if FAIL:
                            return False

        off, width, _s = d['fields']['n_frames']
        for fn_value in named:
            for nf in (0, 1, 2, 3, 0x7FFF, 0x8000, 0xFFFF):
                slot = bytearray(slot_pattern(d['stride'], 5))
                slot[off:off + width // 8] = (
                    nf & ((1 << width) - 1)).to_bytes(width // 8, 'little')
                a = run(ref, site, d['mask_bits'], d['data_base'], d['stride'],
                       d['slots'], 0, bytes(slot), fn_value, 1)
                b = run(new, site, d['mask_bits'], d['data_base'], d['stride'],
                       d['slots'], 0, bytes(slot), fn_value, 1,
                       entry_va, entry_bytes)
                compare(tag, 'fn=0x%X n_frames=%d' % (fn_value, nf), a, b)
                checks += 1
                if FAIL:
                    return False

        total += checks
        # Per-site word delta at the SITE, not counting the shared body
        # (paid once, not per site): stub_word_count() replaces the
        # reference's own ~19-word inline prologue.
        ref_prologue_words = len(ref_cave_words(d, site, with_cases=False)) - 2
        new_prologue_words = SP.stub_word_count()
        saved_words += ref_prologue_words - new_prologue_words
        print('  ok    %-10s %4d comparison(s), reference %3d words, new-site '
             '%3d words (prologue -%d)'
             % (tag, checks, len(ref), len(new) - SP.shared_word_count(),
                ref_prologue_words - new_prologue_words))

    print('\n  %d full-state comparison(s), 0 difference(s)' % total)
    print('  shared body paid once: %d words (%d bytes)'
         % (SP.shared_word_count(), 4 * SP.shared_word_count()))
    print('  per-site prologue savings: %d words (%d bytes) total across 3 '
         'sites' % (saved_words, 4 * saved_words))
    net_words = saved_words - SP.shared_word_count()
    print('  net, once the shared body itself is paid for: %d words (%d '
         'bytes)\n' % (net_words, 4 * net_words))
    return True


def part3_multi_site_shared():
    """
    The scenario PART 1/2 deliberately don't cover: ONE real SHARED body,
    placed once, called via `bl` from THREE different real sites' stubs at
    three different real addresses in the same image -- exactly what a real
    build does (SHARED emitted once into the cave, every dispatcher stub
    reached from wherever build.py places it pointing at that one copy).

    Diffed against ff7nx_dispatch.build_cave directly -- the real,
    already-shipping, case-consolidated dispatcher cave -- not
    build_cave_reference, so this is the actual combination that would ship:
    the shared-prologue optimization stacked on the existing case-
    consolidation optimization.
    """
    print('PART 3 -- ALL THREE real sites sharing ONE real SHARED body '
         '(the actual multi-site shape a real build ships), vs the real '
         'production build_cave\n')
    tags = ('effect10', 'effect100', 'camera')
    shared_len = SP.shared_word_count()
    SHARED_VA = CAVE

    dsite = {}
    entry_va = {}
    entry_bytes = {}
    site_va = {}
    site_words = {}
    cursor = SHARED_VA + 4 * shared_len
    for i, tag in enumerate(tags):
        d, site = site_of(tag)
        dsite[tag] = (d, site)
        entry_va[tag] = TABLE_BASE + 12 * i
        sym = {n: va for n, va, _s, _g in d['cases']}
        cases = [(n, s, g) for n, _v, s, g in d['cases']]
        w = SP.build_cave_shared(cursor, site, d['mask_bits'], entry_va[tag],
                                 SHARED_VA, d['data_base'], d['stride'],
                                 d['fields'], cases, sym, K)
        site_va[tag] = cursor
        site_words[tag] = w
        entry_bytes[tag] = SP.pack_table_entry(d['flag'], entry_va[tag],
                                               d['data_base'], d['stride'])
        cursor += 4 * len(w)

    shared_words = SP.build_shared_prologue(SHARED_VA)
    assert len(shared_words) == shared_len
    full = list(shared_words)
    for tag in tags:
        full += site_words[tag]

    print('  layout: SHARED @0x%X (%d words), %s'
         % (SHARED_VA, shared_len,
            ', '.join('%s @0x%X (%d words)' % (t, site_va[t], len(site_words[t]))
                      for t in tags)))

    total = 0
    ref_total = 0
    for tag in tags:
        d, site = dsite[tag]
        sym = {n: va for n, va, _s, _g in d['cases']}
        cases = [(n, s, g) for n, _v, s, g in d['cases']]
        ref = D.build_cave(CAVE, site, d['flag'], d['mask_bits'],
                           d['data_base'], d['stride'], d['fields'], cases,
                           sym, K)
        ref_total += len(ref)

        named = sorted(sym.values())
        strangers = [0, named[0] - 1, named[-1] + 1,
                    (named[0] + named[-1]) // 2 | 1]
        strangers = [x for x in strangers if x not in set(named)]
        others = [(entry_va[t], entry_bytes[t]) for t in tags if t != tag]

        checks = 0
        for fn_value in named + strangers:
            for flag_value in (0, 1, 0xFF):
                for idx in (0, 1, d['slots'] - 1):
                    seed = (fn_value * 2654435761 + flag_value * 97
                           + idx * 131) & 0xFFFF or 1
                    slot = slot_pattern(d['stride'], seed)
                    a = run(ref, site, d['mask_bits'], d['data_base'],
                           d['stride'], d['slots'], idx, slot, fn_value,
                           flag_value)
                    b = run(full, site, d['mask_bits'], d['data_base'],
                           d['stride'], d['slots'], idx, slot, fn_value,
                           flag_value, entry_va[tag], entry_bytes[tag],
                           base=SHARED_VA, start_pc=site_va[tag],
                           extra_entries=others)
                    compare(tag, 'fn=0x%X flag=%d idx=%d'
                           % (fn_value, flag_value, idx), a, b)
                    checks += 1
                    if FAIL:
                        return False

        off, width, _s = d['fields']['n_frames']
        for fn_value in named:
            for nf in (0, 1, 2, 3, 0x7FFF, 0x8000, 0xFFFF):
                slot = bytearray(slot_pattern(d['stride'], 5))
                slot[off:off + width // 8] = (
                    nf & ((1 << width) - 1)).to_bytes(width // 8, 'little')
                a = run(ref, site, d['mask_bits'], d['data_base'], d['stride'],
                       d['slots'], 0, bytes(slot), fn_value, 1)
                b = run(full, site, d['mask_bits'], d['data_base'],
                       d['stride'], d['slots'], 0, bytes(slot), fn_value, 1,
                       entry_va[tag], entry_bytes[tag], base=SHARED_VA,
                       start_pc=site_va[tag], extra_entries=others)
                compare(tag, 'fn=0x%X n_frames=%d' % (fn_value, nf), a, b)
                checks += 1
                if FAIL:
                    return False

        total += checks
        print('  ok    %-10s %4d comparison(s) at its real multi-site '
             'address 0x%X, reference %3d words'
             % (tag, checks, site_va[tag], len(ref)))

    total_bytes = 4 * len(full)
    print('\n  %d full-state comparison(s), 0 difference(s)' % total)
    print('  real combined image: SHARED once (%d words) + 3 site caves = '
         '%d words (%d bytes) total' % (shared_len, len(full), total_bytes))
    print('  vs 3x independent build_cave_reference-shaped inline prologues: '
         '%d words (%d bytes)' % (ref_total, 4 * ref_total))
    print('  net saved once SHARED is paid for exactly once: %d words '
         '(%d bytes)\n' % (ref_total - len(full), 4 * (ref_total - len(full))))
    return True


def mutation_checks():
    """
    Each of these must be caught, or the differential comparison above is
    not actually testing anything -- same discipline test_dispatch_shrink.py
    and test_mutations.py apply to their own diffs.
    """
    print('mutations (each must be caught):')
    d, site = site_of('effect10')
    entry_va = TABLE_BASE
    ref = ref_cave_words(d, site, with_cases=False)
    caught = 0
    total = 0

    def probe(new_words, entry_bytes, label):
        nonlocal caught, total
        total += 1
        before = len(FAIL)
        a = run(ref, site, d['mask_bits'], d['data_base'], d['stride'],
               d['slots'], 2, slot_pattern(d['stride'], 9), 0x7D26B8, 1)
        b = run(new_words, site, d['mask_bits'], d['data_base'], d['stride'],
               d['slots'], 2, slot_pattern(d['stride'], 9), 0x7D26B8, 1,
               entry_va, entry_bytes)
        ok = not compare('mutation', label, a, b)
        del FAIL[before:]
        if ok:
            caught += 1
            print('  ok    caught: %s' % label)
        else:
            print('  FAIL  NOT caught: %s' % label)

    # 1. Wrong stride baked into the table entry (mask_bits*4 instead of the
    #    real stride) -- the array index computed inside SHARED would be
    #    wrong, so the wrong slot's bytes come back.
    bad_stride = SP.pack_table_entry(d['flag'], entry_va, d['data_base'],
                                     d['stride'] + 4)
    good, _ = new_cave_words(d, site, entry_va, with_cases=False)
    probe(good, bad_stride, 'wrong stride in the table entry')

    # 2. Table entry's delta_flag off by one byte -- flag block read/write
    #    lands one byte short, so the WRONG flag byte is tested/consumed and
    #    the real one is left untouched (a stuck-on or stuck-off bug).
    bad_delta = bytearray(SP.pack_table_entry(d['flag'], entry_va,
                                              d['data_base'], d['stride']))
    bad_delta[0:4] = struct.pack('<I',
                                 struct.unpack('<I', bad_delta[0:4])[0] - 1)
    probe(good, bytes(bad_delta), 'delta_flag off by one byte')

    # 3. SHARED never consumes the flag (comment out the strb) -- a second
    #    identical dispatch would fire again. Simulated by patching a live
    #    copy of the shared body's strb into a nop-equivalent (mov wzr,wzr).
    stub_len = SP.stub_word_count()
    shared_va = CAVE + 4 * stub_len
    stub, cbz_i = SP.build_dispatch_stub(CAVE, site, d['mask_bits'], entry_va,
                                         shared_va)
    shared = SP.build_shared_prologue(shared_va)
    strb_i = None
    for i, word in enumerate(shared):
        if (word & 0xFFC00000) == 0x39000000:      # strb Wt,[Xn,#0]
            strb_i = i
            break
    assert strb_i is not None, 'could not locate the flag-consuming strb'
    broken_shared = list(shared)
    broken_shared[strb_i] = A.mov_reg(31, 31)        # never clears the flag
    broken = list(stub) + broken_shared
    out = CAVE + 4 * len(broken)
    broken[cbz_i] = A.cbz(16, CAVE + 4 * cbz_i, out)
    broken.append(site['displaced'])
    broken.append(A.b(CAVE + 4 * len(broken) + 4, site['hook'] + 4))
    # this mutation is only visible in the FLAG BLOCK after execution, since
    # a single dispatch's register/exit trace looks identical either way --
    # so compare flag bytes directly rather than through compare()/run().
    mem = arm64emu.Mem()
    cpu = arm64emu.Cpu(mem, host_base=HOST)
    cpu.sp = STACK
    for r in range(31):
        cpu.x[r] = arm64emu.GARBAGE
    cpu.x[site['ctx_reg']] = CTX
    mem.setu(CTX + site['idx_off'], 2, 4)
    cpu.x[site['fn_reg']] = 0x7D26B8
    block = 1 << d['mask_bits']
    mem.write(site['_flag'], bytes([0x7E] * (block + 16)))
    mem.setu(site['_flag'] + 2, 1, 1)
    mem.write(entry_va, SP.pack_table_entry(d['flag'], entry_va,
                                            d['data_base'], d['stride']))
    n = d['slots'] + 2
    mem.write(HOST + d['data_base'], bytes([0x5A] * (n * d['stride'])))
    cpu.run(CAVE, broken)
    flag_after = mem.u(site['_flag'] + 2, 1)
    total += 1
    if flag_after == 0:
        print('  FAIL  NOT caught: flag left set after dispatch')
    else:
        caught += 1
        print('  ok    caught: flag left set after dispatch (got %d, want 0)'
             % flag_after)

    print('\n%d/%d mutation(s) caught\n' % (caught, total))
    return caught == total


def main():
    ok1 = part1_prologue_only()
    if not ok1:
        return 1
    ok2 = part2_full_cave()
    if not ok2:
        return 1
    ok2b = part3_multi_site_shared()
    if not ok2b:
        return 1
    ok3 = mutation_checks()
    if not ok3:
        return 1
    if FAIL:
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())