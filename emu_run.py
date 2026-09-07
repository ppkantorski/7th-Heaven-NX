#!/usr/bin/env python3
"""
emu_run.py -- run a recompiled x86 function offline and log what it calls.

    python3 emu_run.py 43FB75
    python3 emu_run.py 43FB75 --steps 400000

WHY THIS EXISTS
===============
Every wrong answer this project has produced about KOTR came from READING the
recompiled code and inferring what it does. This runs it instead.

`arm64emu` already models the recompiler's shape: the guest register file
reached through `[adrp 0x12CE000 + 0x2b0]`, and `TRANSLATE` (0x10FC3A0),
which turns a guest 32-bit address into a host pointer. So a recompiled
function will execute if you:

  * map ff7_en's sections into the emulated address space at `host_base`,
    so translated reads see the real .text/.rdata/.data;
  * point the guest register file slot at some scratch memory and give the
    guest an ESP/EBP;
  * stub every `bl` that leaves the function -- the emulator refuses any
    other call, which is the safety property: nothing runs that you did not
    model;
  * start AFTER the prologue. `str x27,[sp,#-0x60]!` (pre-index) is not
    modelled, and the prologue only saves callee registers and loads the
    register-file pointer, which this sets directly.

WHAT IT PROVED
==============
That `0x43FB75` really does draw KOTR's background with slot 18 (`BG_1.TIM`):

    matcopy(0x7D77D0) / scale / matmul / norm / push
    slotfetch(0x12)                       <- BG_1.TIM
    DRAW(0x7D77B0, [0xBE1128]+0x70, 0xC)  <- and immediately draws it
    matcopy(0x7D77D8) / scale / ...
    slotfetch(0x13)
    DRAW(0x7D77C0, [0xBE1128]+0x70, 0xC)
    slotfetch(0x12)                       <- a third fetch

That contradicts the hardware slot-swap test, which redirected slot 18 to 17
at both of this function's fetch sites and changed nothing on screen. One of
the two is wrong, and resolving that is where the next session should start --
see FINDINGS-277.

The `native` stubs return fixed values, so branches that depend on real state
take a default path. Read the call sequence as "what this function does when
nothing stops it", not as a frame-accurate trace.
"""
import argparse
import struct
import sys
from pathlib import Path

import arm64emu
import nxmap
from ff7nx_resolve import Exe

HOST = 0x700000000
RF = 0x600000            # scratch for the guest register file
ESP0 = 0x500000
SP = HOST + 0x480000
RF_SLOT = 0x12CE000 + 0x2B0

#: x86 -> (name, value the stub returns). Anything a function calls must be
#: listed, or the emulator refuses rather than guessing.
STUBS = (
    (0x68A115, 'slotfetch', 0x900000), (0x5D1BA4, 'DRAW', 0),
    (0x663390, 'scale', 0), (0x662AD8, 'matcopy', 0),
    (0x661E85, 'matmul', 0), (0x663673, 'norm', 0), (0x663707, 'push', 0),
    (0x662538, 'sin', 0x1000), (0x6624FD, 'cos', 0x1000),
    (0x661000, 'alloc', 0x910000), (0x667297, 'objalloc', 0x920000),
    (0x5BEC50, 'spawn', 0), (0x673C30, 'reg', 0), (0x66335F, 'colour', 0),
    (0x666F87, 'blend', 0), (0x66A47E, 'quad', 0), (0x7AE9C0, 'rand', 0x1234),
    (0x6628DE, 'matmul2', 0), (0x661D3F, 'rot', 0), (0x5D1B44, 'setblend', 0),
    (0x5D1B1B, 'setpos', 0), (0x5E35AB, 'stagepart', 0),
    (0x429343, 'palcol', 0xFF), (0x66101A, 'curcol', 0x930000),
    (0x66C3BF, 'visible', 1), (0x4302E8, 'bgsub', 0),
    (0x42F126, 'vscroll', 0), (0x5BDC4F, 'rain', 0),
    (0x42F088, 'shake', 0), (0x66100D, 'curobj', 0x940000),
    (0x676578, 'ctx', 0x950000), (0x68F860, 'meshfind', 0x960000),
    (0x5E383E, 'texbind', 0x970000), (0x6617E9, 'matcpy2', 0),
    (0x42992E, 'entryalloc', 0x980000), (0x663AC0, 'x663AC0', 0),
)


def build(main='game_data_files/exefs/main',
          exe_path='game_data_files/ff7_1.02/ff7_en'):
    m = nxmap.Main(main)
    exe = Exe(Path(exe_path).read_bytes())
    mem = arm64emu.Mem()
    for _name, base, raw, rsize, _v in exe.sections:
        if rsize:
            mem.write(HOST + base, exe.data[raw:raw + rsize])
    for off in range(0, 0x40, 4):
        mem.setu(HOST + RF + off, 0, 4)
    mem.setu(HOST + RF + 0x10, ESP0, 4)     # guest ESP
    mem.setu(HOST + RF + 0x14, ESP0, 4)     # guest EBP
    mem.setu(HOST + RF_SLOT, HOST + RF, 8)
    return m, mem


def prologue_end(m, arm_start):
    """First address after the `ldr x?, [x?, #0x2b0]` that loads the RF."""
    for va in range(arm_start, arm_start + 0x60, 4):
        w = struct.unpack_from('<I', m.img, va)[0]
        if (w & 0xFFC00000) == 0xF9400000 and ((w >> 10) & 0xFFF) * 8 == 0x2B0:
            return va + 4
    return arm_start


def run(x86, steps=400000, quiet=False):
    m, mem = build()
    if x86 not in m.x86_to_arm:
        raise SystemExit('0x%X has no recompiled body' % x86)
    a, b = m.extent(x86)
    cpu = arm64emu.Cpu(mem, host_base=HOST, paged=False)
    cpu.sp = SP
    calls = []

    def mk(name, ret):
        def f(c):
            esp = c.mem.u(HOST + RF + 0x10, 4)
            args = [c.mem.u(HOST + esp + 4 * i, 4) for i in range(6)]
            calls.append((name, args))
            c.set(0, ret, w=True)
        return f

    cpu.native = {m.x86_to_arm[x]: mk(n, r) for x, n, r in STUBS
                  if x in m.x86_to_arm}
    # The register-file pointer the prologue would have loaded. Which register
    # holds it is read out of the prologue rather than assumed.
    start = prologue_end(m, a)
    for va in range(a, start, 4):
        w = struct.unpack_from('<I', m.img, va)[0]
        if (w & 0xFFC00000) == 0xF9400000 and ((w >> 10) & 0xFFF) * 8 == 0x2B0:
            cpu.set(w & 0x1F, HOST + RF)
    code = {va: struct.unpack_from('<I', m.img, va)[0] for va in range(a, b, 4)}
    err = None
    try:
        cpu.run(a, None, max_steps=steps, code=code, start_pc=start)
    except Exception as exc:                       # noqa: BLE001
        err = '%s: %s' % (type(exc).__name__, exc)
    if not quiet:
        print('x86 0x%X -> ARM 0x%X..0x%X, started at 0x%X'
              % (x86, a, b, start))
        print('%d instruction(s) executed%s'
              % (cpu.executed, '; stopped on ' + err if err else ''))
        print()
        print('call sequence (%d):' % len(calls))
        for name, args in calls:
            print('   %-10s %s' % (name,
                                   ' '.join('0x%X' % v for v in args[1:5])))
    return calls, cpu, err


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('func', help='x86 address, hex, e.g. 43FB75')
    ap.add_argument('--steps', type=int, default=400000)
    a = ap.parse_args()
    run(int(a.func, 16), a.steps)
    return 0


if __name__ == '__main__':
    sys.exit(main())
