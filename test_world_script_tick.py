#!/usr/bin/env python3
"""Execute and verify the 60 Hz world scripted-motion timing patch.

    python3 test_world_script_tick.py [--main game_data_files/exefs/main]

The renderer must see motion every invocation while opcode 0x306 consumes its
wait counter every other invocation *for each entity*.  The four script speed
setters share a signed divide-by-two wrapper, matching FFNx rather than
quantising a byte with an unsigned shift.
"""
import argparse
import struct
import sys

import a64
import arm64emu
import cave_space
import ff7nx_60fps as F
import ff7nx_cave
import nxmap


FAIL = []


def ok(cond, what):
    print(('  ok  ' if cond else '  FAIL  ') + what)
    if not cond:
        FAIL.append(what)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--main', default='game_data_files/exefs/main')
    args = ap.parse_args(argv)
    m = nxmap.Main(args.main)

    print('stock fingerprints')
    checks = [
        (F.WORLD_SCRIPT_TICK_HOOK, F.WORLD_SCRIPT_TICK_ORIG,
         'opcode 0x306 branch'),
    ] + [(va, word, label) for va, word, label
         in F.WORLD_SCRIPT_SPEED_CALLS]
    for va, expected, label in checks:
        have = struct.unpack_from('<I', m.img, va)[0]
        ok(have == expected, '%s +0x%X is %08X' % (label, va, have))

    for va, word, label in F.WORLD_SCRIPT_SPEED_CALLS:
        imm = word & 0x3FFFFFF
        if imm & 0x2000000:
            imm -= 0x4000000
        ok(va + imm * 4 == F.WORLD_SCRIPT_POP_TARGET,
           '%s calls pop_world_script_stack' % label)

    data_end = (m.segs[2][1] + m.segs[2][2] + 0xFFF) & ~0xFFF
    table = data_end + m.bss + F.WORLD_ENTITY_PARITY_OFF
    pool = ff7nx_cave.HolePool(
        bytearray(m.text), starts=set(m.arm_starts),
        named=cave_space.named_targets(m.img[cave_space.RODATA:]))
    tick_entry, tick_code = ff7nx_cave.emit_laid_out(
        pool, lambda _entry, addr: F._world_script_tick_words(addr, table))
    speed_entry, speed_code = ff7nx_cave.emit_laid_out(
        pool, lambda _entry, addr: F._world_script_speed_words(addr))

    print('\nplacement')
    all_code = {**tick_code, **speed_code}
    ok(all(struct.unpack_from('<I', m.img, va)[0] == 0 for va in all_code),
       'every helper word occupies verified zero padding')
    ok(len(tick_code) >= 21 and len(speed_code) >= 8,
       'both complete helpers fit in reclaimed padding')
    ok(F.WORLD_ENTITY_PARITY_OFF + F.WORLD_ENTITY_PARITY_BYTES <= 0x4000,
       'bounded entity table fits inside the reserved BSS growth')

    print('\nopcode 0x306 paths')
    tm = arm64emu.Mem()

    def tick(entity, waiting=0):
        tc = arm64emu.Cpu(tm)
        tc.x[8] = entity
        tc.x[22] = waiting
        return tc.run(tick_entry, [], code=tick_code, start_pc=tick_entry)

    a, b = 0x40100000, 0x40200000
    ok([tick(a) for _ in range(4)] == [
        F.WORLD_SCRIPT_CONTINUE_PATH, F.WORLD_SCRIPT_NORMAL_PATH,
        F.WORLD_SCRIPT_CONTINUE_PATH, F.WORLD_SCRIPT_NORMAL_PATH],
       'one entity alternates skip/decrement independently')
    ok([tick(b), tick(a), tick(b), tick(a)] == [
        F.WORLD_SCRIPT_CONTINUE_PATH, F.WORLD_SCRIPT_CONTINUE_PATH,
        F.WORLD_SCRIPT_NORMAL_PATH, F.WORLD_SCRIPT_NORMAL_PATH],
       'interleaved entities cannot steal one another\'s cadence')
    ok(tick(a, 1) == F.WORLD_SCRIPT_REWIND_PATH
       and tick(b, 1) == F.WORLD_SCRIPT_REWIND_PATH,
       'stock waiting state rewinds without changing either parity')

    # This is the Junon failure mode of the retired global gate: an entity
    # serviced only on global odd frames would skip forever.  The keyed gate
    # must continue making progress regardless of that external schedule.
    c = 0x40300000
    scheduled_on_global_odd_only = [tick(c) for _global in (1, 3, 5, 7)]
    ok(scheduled_on_global_odd_only.count(F.WORLD_SCRIPT_NORMAL_PATH) == 2,
       'odd-only external scheduling still decrements every second service')

    full_mem = arm64emu.Mem()
    for i in range(F.WORLD_ENTITY_PARITY_SLOTS):
        full_mem.setu(table + i * 8, 0x50000000 + i * 0x100, 4)
    full_cpu = arm64emu.Cpu(full_mem)
    full_cpu.x[8] = 0x60000000
    full_cpu.x[22] = 0
    ok(full_cpu.run(tick_entry, [], code=tick_code,
                    start_pc=tick_entry) == F.WORLD_SCRIPT_NORMAL_PATH,
       'a full table falls back to progress instead of deadlocking')

    print('\nsigned speed wrapper')
    ctx = 0x71000000
    result = [0]

    def pop_stub(c):
        c.mem.setu(ctx, result[0], 4)

    for value, expected in ((8, 4), (1, 0), (-8, -4), (-3, -1)):
        sm = arm64emu.Mem()
        sc = arm64emu.Cpu(sm, native={F.WORLD_SCRIPT_POP_TARGET: pop_stub})
        result[0] = value
        sc.x[21] = ctx
        sc.x[30] = 0x7F000000
        sc.sp = 0x72000000
        out = sc.run(speed_entry, [], code=speed_code, start_pc=speed_entry)
        got = arm64emu.s32(sm.u(ctx, 4))
        ok(out == 0x7F000000 and got == expected,
           '%d / 2 truncates toward zero -> %d' % (value, got))
        ok(sc.sp == 0x72000000, 'wrapper restores SP for input %d' % value)

    for va, _orig, label in F.WORLD_SCRIPT_SPEED_CALLS:
        word = a64.bl(va, speed_entry)
        imm = word & 0x3FFFFFF
        if imm & 0x2000000:
            imm -= 0x4000000
        ok(va + imm * 4 == speed_entry,
           '%s replacement reaches wrapper' % label)

    print()
    if FAIL:
        print('%d FAILED' % len(FAIL))
        return 1
    print('all good')
    return 0


if __name__ == '__main__':
    sys.exit(main())
