#!/usr/bin/env python3
"""Execute and verify the 60 Hz world scripted-motion timing pair.

    python3 test_world_script_tick.py [--main game_data_files/exefs/main]

The renderer must see motion every frame while opcode 0x306 consumes its
wait counter every other world frame. The four script speed setters share a
signed divide-by-two wrapper, matching FFNx rather than quantising a byte with
an unsigned shift.
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
        (F.WORLD_FRAME_TICK_HOOK, F.WORLD_FRAME_TICK_ORIG,
         'world-loop entry'),
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
    counter = data_end + m.bss + F.WORLD_FRAME_COUNTER_OFF
    pool = ff7nx_cave.HolePool(
        bytearray(m.text), starts=set(m.arm_starts),
        named=cave_space.named_targets(m.img[cave_space.RODATA:]))
    frame_entry, frame_code = ff7nx_cave.emit_laid_out(
        pool, lambda _entry, addr: F._world_frame_tick_words(addr, counter))
    tick_entry, tick_code = ff7nx_cave.emit_laid_out(
        pool, lambda _entry, addr: F._world_script_tick_words(addr, counter))
    speed_entry, speed_code = ff7nx_cave.emit_laid_out(
        pool, lambda _entry, addr: F._world_script_speed_words(addr))

    print('\nplacement')
    all_code = {**frame_code, **tick_code, **speed_code}
    ok(all(struct.unpack_from('<I', m.img, va)[0] == 0 for va in all_code),
       'every helper word occupies verified zero padding')
    ok(len(frame_code) >= 6 and len(tick_code) >= 7 and len(speed_code) >= 8,
       'all three complete helpers fit in reclaimed padding')

    print('\nworld-frame source')
    mem = arm64emu.Mem()
    cpu = arm64emu.Cpu(mem)
    cpu.sp = 0x70010000
    for n in range(1, 5):
        out = cpu.run(frame_entry, [], code=frame_code, start_pc=frame_entry)
        ok(out == F.WORLD_FRAME_TICK_HOOK + 4,
           'frame %d returns after the displaced prologue' % n)
        ok(mem.u(counter, 4) == n, 'frame counter is %d' % n)

    print('\nopcode 0x306 paths')
    def tick(phase, waiting=0):
        tm = arm64emu.Mem()
        tm.setu(counter, phase, 4)
        tc = arm64emu.Cpu(tm)
        tc.x[22] = waiting
        return tc.run(tick_entry, [], code=tick_code, start_pc=tick_entry)

    ok(tick(0) == F.WORLD_SCRIPT_NORMAL_PATH,
       'even world frame enters wait_frames--')
    ok(tick(1) == F.WORLD_SCRIPT_CONTINUE_PATH,
       'odd world frame skips only wait_frames-- and still reaches movement')
    ok(tick(0, 1) == F.WORLD_SCRIPT_REWIND_PATH
       and tick(1, 1) == F.WORLD_SCRIPT_REWIND_PATH,
       'stock waiting state rewinds on both phases')
    ok([tick(1) for _ in range(4)] == [F.WORLD_SCRIPT_CONTINUE_PATH] * 4,
       'multiple actors share frame parity without toggling one another')

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
