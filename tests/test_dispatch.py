#!/usr/bin/env python3
"""
test_dispatch.py -- differential test of the dispatcher caves.

For every dispatcher, every registered function, and a large sweep of field
values, the ACTUAL EMITTED ARM64 WORDS are executed on an interpreter and the
resulting slot struct is compared against a Python transcription of FFNx's C.

What this catches that inspection does not:

  * a wrong field offset, width or signedness
  * a divide that rounds toward negative infinity instead of toward zero
  * a multiply that fails to truncate to the narrow type
  * an encoding error in any instruction in the cave
  * a dependency on a caller-saved register surviving `bl` (the emulator fills
    x1-x18 and x30 with garbage on return from the translator)
  * a stray write outside the slot being scaled, or outside the flag block
  * a cave that does not end at hook+4, or that leaves sp unbalanced

Run:  python3 test_dispatch.py

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
import random
import struct
import sys

import a64 as A
import arm64emu
import ff7nx_dispatch as D
from ff7nx_dispatch_sites import DISPATCH_SITES, FLAG_BASE

MULT = 4                  # battle_frame_multiplier at 60 FPS
K = 2                     # log2(MULT)
CAVE = 0x1152660          # representative placement; exact value is irrelevant
HOST = 0x700000000
CTX = 0x900000000
STACK = 0x800000000

fails = []


def note(msg):
    fails.append(msg)


# ---------------------------------------------------------------- reference

def c_mul(val, width):
    """C `x *= 4` on an unsigned narrow type: multiply, then truncate."""
    return (val * MULT) & ((1 << width) - 1)


def c_div_signed(val, width):
    """C `x /= 4` on a signed narrow type: truncate toward zero, then store."""
    sv = val - (1 << width) if val & (1 << (width - 1)) else val
    q = abs(sv) // MULT
    if sv < 0:
        q = -q
    return q & ((1 << width) - 1)


def reference(fields, spec, guard, slot):
    """
    Apply FFNx's first-frame block to a copy of the slot bytes.

    `slot` is a bytearray of the struct. Every write goes through the declared
    width, so a byte field wraps at 256 exactly as it does in FFNx.
    """
    out = bytearray(slot)

    def rd(off, width):
        return int.from_bytes(out[off:off + width // 8], 'little')

    def wr(off, width, v):
        out[off:off + width // 8] = (v & ((1 << width) - 1)).to_bytes(
            width // 8, 'little')

    if guard == 'n_frames>1':
        off, width, _s = fields['n_frames']
        v = rd(off, width)
        sv = v - (1 << width) if v & (1 << (width - 1)) else v
        if not sv > 1:
            return out
    elif guard is not None:
        raise AssertionError('unknown guard %r' % guard)

    for name, how in spec:
        off, width, sign = fields[name]
        v = rd(off, width)
        wr(off, width, c_mul(v, width) if how == 'mul'
           else c_div_signed(v, width))
    return out


# ---------------------------------------------------------------- emulation

def run_cave(words, site, slot_bytes, stride, data_base, idx, flag_base,
             fn_value, flag_value, extra_slots=2, max_slots=128):
    """
    Execute a dispatcher cave once. Returns
    (exit_pc, slot_after, flag_after, mem, cpu, neighbours_after).
    """
    mem = arm64emu.Mem()
    cpu = arm64emu.Cpu(mem, host_base=HOST)
    cpu.sp = STACK

    # Guest context: idx lives in one of the eight guest register slots.
    cpu.x[site['ctx_reg']] = CTX
    mem.setu(CTX + site['idx_off'], idx, 4)

    # The whole slot table, so a stray write to a neighbour is visible.
    # `idx` may deliberately be absurd (the out-of-range test uses 0x7FFFFFFF),
    # so the table is bounded and an out-of-range focus slot simply is not
    # placed -- what matters then is that the table stays untouched.
    n = min(idx + 1 + extra_slots, max_slots + extra_slots)
    table = bytearray()
    for i in range(n):
        table += bytes(slot_bytes) if i == idx else bytes([0x5A] * stride)
    mem.write(HOST + data_base, bytes(table))

    # Flag block: one byte per slot, sentinel elsewhere so overruns show up.
    block = 1 << site_mask_bits(site)
    mem.write(flag_base, bytes([0x7E] * (block + 16)))
    mem.setu(flag_base + (idx & (block - 1)), flag_value, 1)

    cpu.x[site['fn_reg']] = fn_value

    exit_pc = cpu.run(CAVE, words)
    after = bytes(mem.read(HOST + data_base + idx * stride, stride))
    flags = bytes(mem.read(flag_base, block + 16))
    neigh = [bytes(mem.read(HOST + data_base + i * stride, stride))
             for i in range(n) if i != idx]
    cpu.table_after = bytes(mem.read(HOST + data_base, n * stride))
    return exit_pc, after, flags, mem, cpu, neigh


def site_mask_bits(site):
    return site['_mask_bits']


# -------------------------------------------------------------------- values

def sweep_values(width):
    """
    Boundary-heavy value set for a field of the given width.

    Deliberately small: test_arithmetic_identities() already checks the scaling
    primitives against C over their ENTIRE value domain, so this sweep's job is
    only to prove the right field is selected, at the right offset and width,
    with the right guard -- and for that, boundaries plus a couple of ordinary
    values are enough.
    """
    top = 1 << width
    vals = {0, 1, 2, MULT, MULT + 1, top - 1, top >> 1, (top >> 1) - 1,
            top // MULT + 1}
    return sorted(v & (top - 1) for v in vals)


def build(tag, kind):
    d = DISPATCH_SITES[tag]
    sym = {n: va for n, va, _o, _g in d['cases']}
    cases = [(n, o, g) for n, _v, o, g in d['cases']]
    if kind == 'add':
        site = dict(d['add_hook'])
        site['_mask_bits'] = d['mask_bits']
        return site, D.build_addfn_cave(CAVE, d['add_hook'], d['flag'],
                                        d['mask_bits']), d
    site = dict(d['disp_hook'])
    site['_mask_bits'] = d['mask_bits']
    words = D.build_cave(CAVE, None, d['disp_hook'], d['flag'], d['mask_bits'],
                         d['data_base'], d['stride'], d['fields'],
                         cases, sym, K)
    return site, words, d


# ---------------------------------------------------------------- the tests

def test_dispatcher(tag):
    site, words, d = build(tag, 'disp')
    stride = d['stride']
    fields = d['fields']
    flag_base = d['flag']
    block = 1 << d['mask_bits']
    want_exit = d['disp_hook']['hook'] + 4
    rng = random.Random(0xC0FFEE ^ hash(tag) & 0xFFFF)
    checks = 0

    # ---- 1. every case, exhaustive per-field sweeps ---------------------
    for name, fn_va, spec, guard in d['cases']:
        touched = [n for n, _h in spec]
        for focus in touched:
            fw = fields[focus][1]
            for v in sweep_values(fw):
                for idx in (0, d['slots'] - 1):
                    slot = bytearray(stride)
                    # Fill every field with something non-trivial so a wrong
                    # offset moves the wrong bytes and the diff shows it.
                    for fn2, (off2, w2, _s2) in fields.items():
                        val = rng.getrandbits(w2)
                        slot[off2:off2 + w2 // 8] = val.to_bytes(w2 // 8,
                                                                 'little')
                    off, wid, _s = fields[focus]
                    slot[off:off + wid // 8] = v.to_bytes(wid // 8, 'little')

                    exp = reference(fields, spec, guard, slot)
                    ex, got, flags, mem, cpu, neigh = run_cave(
                        words, site, slot, stride, d['data_base'], idx,
                        flag_base, fn_va, 1)
                    checks += 1
                    if ex != want_exit:
                        note('%s/%s: exited to 0x%X, want hook+4 0x%X'
                             % (tag, name, ex, want_exit))
                        return checks
                    if got != bytes(exp):
                        note('%s/%s idx=%d %s=0x%X: slot mismatch\n'
                             '     got %s\n     want %s'
                             % (tag, name, idx, focus, v, got.hex(' '),
                                bytes(exp).hex(' ')))
                        return checks
                    if flags[idx & (block - 1)] != 0:
                        note('%s/%s: first-frame flag not consumed' % (tag, name))
                        return checks
                    if any(b != 0x7E for i, b in enumerate(flags)
                           if i != (idx & (block - 1))):
                        note('%s/%s: wrote outside its own flag byte'
                             % (tag, name))
                        return checks
                    if any(nb != bytes([0x5A] * stride) for nb in neigh):
                        note('%s/%s: wrote into a neighbouring slot'
                             % (tag, name))
                        return checks
                    if cpu.sp != STACK:
                        note('%s/%s: sp unbalanced (0x%X != 0x%X)'
                             % (tag, name, cpu.sp, STACK))
                        return checks

    # ---- 2. flag clear -> nothing happens at all ------------------------
    for name, fn_va, spec, guard in d['cases']:
        slot = bytearray(rng.getrandbits(8) for _ in range(stride))
        ex, got, flags, mem, cpu, neigh = run_cave(
            words, site, slot, stride, d['data_base'], 3, flag_base, fn_va, 0)
        checks += 1
        if got != bytes(slot):
            note('%s/%s: modified the slot with the flag clear' % (tag, name))
            return checks
        if ex != want_exit:
            note('%s/%s: flag-clear path exited to 0x%X' % (tag, name, ex))
            return checks
        if cpu.translate_calls:
            note('%s/%s: called the translator with the flag clear'
                 % (tag, name))
            return checks

    # ---- 3. an unlisted function -> flag consumed, nothing scaled -------
    unlisted = 0x004ABCDE
    assert unlisted not in [c[1] for c in d['cases']]
    for idx in range(min(d['slots'], 8)):
        slot = bytearray(rng.getrandbits(8) for _ in range(stride))
        ex, got, flags, mem, cpu, neigh = run_cave(
            words, site, slot, stride, d['data_base'], idx, flag_base,
            unlisted, 1)
        checks += 1
        if got != bytes(slot):
            note('%s: scaled a slot for an unlisted function' % tag)
            return checks
        if flags[idx & (block - 1)] != 0:
            note('%s: unlisted function left the flag set' % tag)
            return checks
        if ex != want_exit:
            note('%s: unlisted path exited to 0x%X' % (tag, ex))
            return checks

    # ---- 4. out-of-range index cannot escape the flag block -------------
    for idx in (d['slots'], block, block + 1, 0xFFFF, 0x7FFFFFFF):
        slot = bytearray(stride)
        ex, got, flags, mem, cpu, neigh = run_cave(
            words, site, slot, stride, d['data_base'], idx, flag_base,
            unlisted, 0, extra_slots=0, max_slots=d['slots'])
        checks += 1
        touched = [i for i, b in enumerate(flags) if b != 0x7E]
        if any(i >= block for i in touched):
            note('%s: index %d wrote outside the flag block at +%s'
                 % (tag, idx, touched))
            return checks
        if cpu.table_after != bytes([0x5A] * len(cpu.table_after)):
            note('%s: out-of-range index %d wrote into the slot table'
                 % (tag, idx))
            return checks
        if ex != want_exit:
            note('%s: out-of-range index %d exited to 0x%X' % (tag, idx, ex))
            return checks
    return checks


def test_addfn(tag):
    site, words, d = build(tag, 'add')
    block = 1 << d['mask_bits']
    want_exit = d['add_hook']['hook'] + 4
    checks = 0
    for idx in list(range(d['slots'])) + [d['slots'], block, 0xFFFF,
                                          0x7FFFFFFF]:
        mem = arm64emu.Mem()
        cpu = arm64emu.Cpu(mem, host_base=HOST)
        cpu.sp = STACK
        cpu.x[site['ctx_reg']] = CTX
        mem.setu(CTX + site['idx_off'], idx, 4)
        mem.write(d['flag'], bytes([0x7E] * (block + 16)))
        # x0 and the value register must survive: the displaced store uses them.
        cpu.x[0] = HOST + 0x1000
        cpu.x[site['fn_val_reg']] = 0x00426A26
        ex = cpu.run(CAVE, words)
        checks += 1
        flags = bytes(mem.read(d['flag'], block + 16))
        if ex != want_exit:
            note('%s add_fn: exited to 0x%X, want 0x%X' % (tag, ex, want_exit))
            return checks
        if flags[idx & (block - 1)] != 1:
            note('%s add_fn: idx %d did not set its flag' % (tag, idx))
            return checks
        if any(b != 0x7E for i, b in enumerate(flags)
               if i != (idx & (block - 1))):
            note('%s add_fn: idx %d wrote outside its flag byte' % (tag, idx))
            return checks
        # The displaced `str wFN,[x0]` must still have happened.
        if mem.u(HOST + 0x1000, 4) != 0x00426A26:
            note('%s add_fn: displaced store did not run' % tag)
            return checks
        if cpu.sp != STACK:
            note('%s add_fn: sp unbalanced' % tag)
            return checks
        if cpu.translate_calls:
            note('%s add_fn: called the translator (should not need to)' % tag)
            return checks
    return checks


def test_field_tables():
    """
    The field offsets in ff7nx_dispatch.py must match FFNx's structs. Rebuild
    each struct from its declaration and check that every offset we scale lands
    on the field we think it does.
    """
    layouts = {
        'effect10': [('field_0', 2), ('field_2', 2), ('n_frames', 2),
                     ('field_6', 2), ('field_8', 2), ('field_A', 2),
                     ('field_C', 2), ('field_E', 2), ('field_10', 4),
                     ('field_14', 4), ('field_18', 1), ('field_19', 1),
                     ('field_1A', 1), ('field_1B', 5)],
        'effect100': [('field_0', 2), ('field_2', 2), ('n_frames', 2),
                      ('field_6', 2), ('field_8', 2), ('field_A', 2),
                      ('field_C', 2), ('field_E', 2), ('field_10', 4),
                      ('field_14', 4), ('field_18', 1), ('field_19', 1),
                      ('field_1A', 2), ('field_1C', 4)],
        'camera': [('field_0', 2), ('field_2', 2), ('n_frames', 2),
                   ('field_6', 2), ('field_8', 2), ('field_A', 2),
                   ('field_C', 2), ('field_E', 2), ('field_10', 2),
                   ('unused_12', 6), ('field_18', 1), ('unused_19', 15)],
    }
    n = 0
    for tag, decl in layouts.items():
        off = 0
        real = {}
        for name, size in decl:
            real[name] = (off, size)
            off += size
        d = DISPATCH_SITES[tag]
        if off != d['stride']:
            note('%s: struct declaration sums to 0x%X, stride says 0x%X'
                 % (tag, off, d['stride']))
        for fname, (foff, fwidth, _sign) in d['fields'].items():
            n += 1
            if fname not in real:
                note('%s: %s is not in the struct' % (tag, fname))
                continue
            if real[fname][0] != foff:
                note('%s: %s at 0x%X, struct says 0x%X'
                     % (tag, fname, foff, real[fname][0]))
            if real[fname][1] * 8 != fwidth:
                note('%s: %s width %d bits, struct says %d'
                     % (tag, fname, fwidth, real[fname][1] * 8))
    return n


def test_arithmetic_identities():
    """
    The divide must match C for the whole int16 domain and the multiply must
    match for the whole uint16 and uint8 domains -- checked directly against
    the emitted primitive sequences rather than against the reference model, so
    the two are genuinely independent.
    """
    mem = arm64emu.Mem()
    n = 0
    for width, off in ((16, 0xC), (32, 0x14)):
        words = D._div_s(off, K, width)
        for v in range(0, 1 << 16):
            raw = v if width == 16 else (v << 16) | 0x1234
            cpu = arm64emu.Cpu(mem, host_base=0)
            cpu.sp = STACK
            cpu.x[0] = 0x10000
            mem.setu(0x10000 + off, raw, width // 8)
            cpu.run(0x2000, words)
            got = mem.u(0x10000 + off, width // 8)
            want = c_div_signed(raw, width)
            n += 1
            if got != want:
                note('div width %d: %d/%d -> 0x%X, C gives 0x%X'
                     % (width, raw, MULT, got, want))
                return n
    for width, off in ((8, 0x18), (16, 0x04)):
        words = D._mul_u(off, K, width)
        for v in range(1 << width):
            cpu = arm64emu.Cpu(mem, host_base=0)
            cpu.sp = STACK
            cpu.x[0] = 0x20000
            mem.setu(0x20000 + off, v, width // 8)
            cpu.run(0x2000, words)
            got = mem.u(0x20000 + off, width // 8)
            want = c_mul(v, width)
            n += 1
            if got != want:
                note('mul width %d: %d*%d -> 0x%X, C gives 0x%X'
                     % (width, v, MULT, got, want))
                return n
    return n


def test_cave_structure():
    """
    Structural invariants, checked on the words themselves:
      * every word decodes with capstone
      * the last word is an unconditional B to hook+4
      * the second-to-last word is the displaced instruction, verbatim
      * every internal branch stays inside the cave
      * exactly one bl, and only to the translator (dispatcher caves)
      * no bl at all in the add_fn caves
    """
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    n = 0
    for tag in DISPATCH_SITES:
        d0 = DISPATCH_SITES[tag]
        # A site may be present for the pause-throttle alone: no first-frame
        # flag block and no arithmetic cases. There is no scaler cave to check
        # for one of those, and asking for one is an error, not a pass.
        if d0['flag'] is None or not d0['cases']:
            continue
        for kind in ('add', 'disp'):
            site, words, d = build(tag, kind)
            hook = site['hook']
            blob = struct.pack('<%dI' % len(words), *words)
            decoded = list(md.disasm(blob, CAVE))
            n += 1
            if len(decoded) != len(words):
                note('%s/%s: only %d of %d words decode'
                     % (tag, kind, len(decoded), len(words)))
                continue
            if words[-2] != site['displaced']:
                note('%s/%s: word[-2] is %08X, displaced is %08X'
                     % (tag, kind, words[-2], site['displaced']))
            last = words[-1]
            if (last & 0xFC000000) != 0x14000000:
                note('%s/%s: does not end in an unconditional B' % (tag, kind))
            else:
                imm = last & 0x3FFFFFF
                if imm & 0x2000000:
                    imm -= 0x4000000
                tgt = CAVE + 4 * (len(words) - 1) + imm * 4
                if tgt != hook + 4:
                    note('%s/%s: returns to 0x%X, want hook+4 0x%X'
                         % (tag, kind, tgt, hook + 4))
            bls = 0
            for i, w in enumerate(words):
                pc = CAVE + 4 * i
                if (w & 0xFC000000) == 0x94000000:
                    bls += 1
                    imm = w & 0x3FFFFFF
                    if imm & 0x2000000:
                        imm -= 0x4000000
                    if pc + imm * 4 != arm64emu.TRANSLATE:
                        note('%s/%s: bl to 0x%X' % (tag, kind, pc + imm * 4))
                    continue
                br = D._branch(w, pc)
                if br is None or i == len(words) - 1:
                    continue
                for t in br[0]:
                    if not (CAVE <= t < CAVE + 4 * len(words)):
                        note('%s/%s: internal branch at +%d leaves the cave '
                             '(-> 0x%X)' % (tag, kind, i, t))
            want_bls = 0 if kind == 'add' else 1
            if bls != want_bls:
                note('%s/%s: %d bl(s), want %d' % (tag, kind, bls, want_bls))
    return n


def main():
    stages = set(sys.argv[1:]) or {'struct', 'arith', 'cave', 'diff'}
    print('battle_frame_multiplier = %d (shift %d)\n' % (MULT, K))

    if 'struct' in stages:
        n = test_field_tables()
        print('struct field offsets vs FFNx ff7.h:   %d field(s) checked' % n)

    if 'cave' in stages:
        n = test_cave_structure()
        print('cave structure (capstone + branches): %d cave(s) checked' % n)

    if 'arith' in stages:
        n = test_arithmetic_identities()
        print('scaling primitives vs C semantics:    %d value(s) checked' % n)

    if 'diff' in stages:
        total = 0
        for tag in ('effect10', 'effect100', 'camera'):
            c = test_addfn(tag)
            e = test_dispatcher(tag)
            total += c + e
            print('%-10s add_fn %4d case(s), dispatcher %6d case(s)'
                  % (tag, c, e))
        print('differential vs FFNx C:               %d execution(s)' % total)

    if fails:
        print('\n%d FAILURE(S):' % len(fails))
        for f in fails:
            print('  ' + f)
        return 1
    print('\nALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
