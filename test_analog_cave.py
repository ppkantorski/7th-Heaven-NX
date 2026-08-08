#!/usr/bin/env python3
"""
test_analog_cave.py -- the 360 degree movement caves, executed for real.

    python3 test_analog_cave.py

WHAT IT DOES
============
Builds the ACTUAL ENCODED WORDS of both caves and runs them in arm64emu with
the port's input object, the DirectInput key buffer, the field level data and
the field id all modelled in memory, then compares the control direction the
cave wrote against the Python reference in ff7nx_analog.py.

Two things about the model are load-bearing.

1. The translator is modelled the way the rest of this project models it:
   every `bl` to it destroys x0..x18, x30 and the flags. The cave makes FOUR
   translator calls and everything it computed before the first one has to
   survive them.

2. `paged=True`. The real translator is a 4 KB page table, so a host pointer
   it hands back is only good for the page it was asked about. The FIRST
   version of this cave did `translate(level_data) + triggers_offset` -- a host
   pointer plus a 25-55 KB guest offset -- and read and wrote its control
   direction six to thirteen pages away from the field data, every frame. The
   harness did not catch it because its translator was flat, so `HOST + guest`
   happened to be true. It is not true on hardware. `flat_model_would_hide_it`
   below is the regression test for exactly that.

The sweep covers every one of the eight key masks at one degree steps -- 2880
whole-cave executions -- plus the mutations at the end, each of which must
change the answer.
"""
import math
import struct
import sys

import arm64emu
import ff7nx_cave
import ff7nx_analog as AN
import ff7nx_analog_cave as C

HOST = 0x700000000
POLL_CAVE = 0x1152660
FIELD_CAVE = 0x1152680
# The two lookup tables live in .rodata, not in the cave -- a chained cave is
# cut into two- and three-word runs and a table has to be contiguous.
SNAP_VA = 0x1190000
ATAN_VA = SNAP_VA + len(AN.SNAP_TAB)
LEVEL_GUEST = 0x1000000
# Measured across flevel.lgp, the triggers section starts 25-55 KB into the
# field file. Using a realistic value rather than a token one is the whole
# point: anything under 0x1000 would sit in the level pointer's own page and
# the paging would not be exercised.
TRIGGERS = 0xD568                       # ancnt1's, for the record
TRIG_PTR = LEVEL_GUEST + TRIGGERS + 4   # what the game caches at 0xCFF454
STACK = 0x800000000

FAIL = []


def fail(m):
    FAIL.append(m)
    print('  FAIL  %s' % m)


def ok(cond, what):
    if cond:
        print('  ok  %s' % what)
    else:
        fail(what)


def f32(x):
    return struct.unpack('<I', struct.pack('<f', x))[0]


# The port's singleton chain, modelled at made-up but plausible host
# addresses. `obj=0` means a link is null, which is the cave's bail path.
# NODES[0] is what the GOT slot points at; each subsequent node is reached by
# `ldr x9, [x9, #off]` with the offsets the port itself uses.
NODES = [0x500000000 + 0x2000 * (k + 1) for k in range(len(AN.INPUT_CHAIN))]


def wire_chain(mem, obj):
    """[0x12CE1D0] -> ... -> obj, exactly the five loads the cave does."""
    mem.setu(AN.INPUT_GOT, NODES[0], 8)
    for k, off in enumerate(AN.INPUT_CHAIN):
        nxt = NODES[k + 1] if k + 1 < len(NODES) else obj
        mem.setu(NODES[k] + off, nxt, 8)


def break_link(mem, k):
    """Null out the k'th pointer in the chain (0 = the GOT slot's target)."""
    if k == 0:
        mem.setu(AN.INPUT_GOT, 0, 8)
    else:
        mem.setu(NODES[k - 1] + AN.INPUT_CHAIN[k - 1], 0, 8)


def setup(sx, sy, keymask, base=100, field_id=7, obj=0x600000000,
          captured=False, saved_id=7, saved_base=100, level=LEVEL_GUEST,
          triggers=TRIG_PTR):
    mem = arm64emu.Mem()
    cpu = arm64emu.Cpu(mem, host_base=HOST, paged=True)
    cpu.sp = STACK
    g = cpu.guest_to_host
    wire_chain(mem, obj)   # obj=0 -> the last link is null
    # scratch (a module bss address -- NOT guest space, no translation)
    mem.setu(AN.ANALOG_BASE, saved_id, 4)
    mem.setu(AN.ANALOG_BASE + 4, 1 if captured else 0, 4)
    mem.setu(AN.ANALOG_BASE + 8, saved_base & 0xFFFFFFFF, 4)
    # the port's input object: split positive/negative axes, host memory
    if obj:
        mem.setu(obj + AN.OBJ_UP, f32(max(sy, 0.0)), 4)
        mem.setu(obj + AN.OBJ_DOWN, f32(max(-sy, 0.0)), 4)
        mem.setu(obj + AN.OBJ_RIGHT, f32(max(sx, 0.0)), 4)
        mem.setu(obj + AN.OBJ_LEFT, f32(max(-sx, 0.0)), 4)
    # the port's DirectInput key state -- it writes 0x80, not 1. Also module
    # bss, also not translated.
    for bit, code in ((0, AN.DIK_RIGHT), (1, AN.DIK_UP),
                      (2, AN.DIK_LEFT), (3, AN.DIK_DOWN)):
        mem.setu(AN.KEYBUF + code, 0x80 if keymask & (1 << bit) else 0, 1)
    # guest globals, every one of them through the page map
    mem.setu(g(AN.LEVEL_PTR_GUEST), level, 4)
    mem.setu(g(AN.TRIGGERS_PTR_GUEST), triggers if level else triggers, 4)
    mem.setu(g(AN.FIELD_ID_GUEST), field_id, 2)
    mem.setu(g(triggers + AN.CONTROL_DIR_OFF), base & 0xFF, 1)
    return mem, cpu


def scattered_runs(n_words, start=0x300000, span=0x7F000, seed=1):
    """
    A layout that looks like the real padding pool: 2- and 3-word holes
    scattered across half a megabyte, in address order, never adjacent.

    Deterministic and self-contained, so this test needs no game dump. The
    point is not the exact addresses -- it is that they are NOT contiguous
    and NOT close together, which is the only condition under which a
    chained cave's own branches, adrp pages and label arithmetic can be
    wrong. `ff7nx_cave.slots`/`link` do the layout, the same functions the
    build uses.
    """
    runs, va, placed, r = [], start, 0, seed
    while placed < n_words:
        r = (r * 1103515245 + 12345) & 0x7FFFFFFF
        ln = 2 + (r >> 16) % 2                     # 2- or 3-word hole
        runs.append((va, ln))
        placed += ln - 1
        va += 16 + ((r >> 8) & 0xFF) * 16          # a gap of real code between
        if va - start > span:
            raise AssertionError('layout overran its window')
    return runs


def lay_out(build, n_probe_args=()):
    """(entry, {address: word}) for a cave chained over `scattered_runs`."""
    n = len(build(0, lambda i: 4 * i))
    runs = scattered_runs(n)
    addrs = ff7nx_cave.slots(runs, n)
    words = build(addrs[0], lambda i: addrs[i])
    assert len(words) == n
    return addrs[0], ff7nx_cave.link(runs, words), runs


def field_build(cave, addr=None):
    return C.build_field_cave(cave, SNAP_VA, ATAN_VA, addr)


def diag_build(cave, addr=None):
    return C.build_field_cave(cave, SNAP_VA, ATAN_VA, addr, True)


def put_tables(mem):
    mem.write(SNAP_VA, bytes(AN.SNAP_TAB))
    mem.write(ATAN_VA, bytes(AN.ATAN_TAB))


def run_field(mem, cpu):
    """Execute the cave AS IT SHIPS: chained through scattered holes."""
    put_tables(mem)
    entry, code, _ = lay_out(field_build)
    for va, w in code.items():
        mem.setu(va, w, 4)
    return cpu.run(entry, None, code=code), code


def run_field_contiguous(mem, cpu):
    words = field_build(FIELD_CAVE)
    # The cave's two lookup tables live in its own tail and are read with
    # ordinary loads, so the cave has to be present in MEMORY as well as in
    # the interpreter's word list. Leaving it out made every table read
    # return zero -- and SNAP_TAB[right] happens to BE zero, so the snapped
    # direction still looked right while the atan lookup silently returned 0.
    put_tables(mem)
    mem.write(FIELD_CAVE, struct.pack('<%dI' % len(words), *words))
    return cpu.run(FIELD_CAVE, words), words


def written(mem, cpu, triggers=TRIG_PTR):
    """The control direction byte, raw. The game reads it with `movsx`, so
    0..255 and -128..127 are the same value; every comparison here is mod
    256 for that reason -- a direction is an angle, and 133 and -123 are the
    same angle."""
    return mem.u(cpu.guest_to_host(triggers + AN.CONTROL_DIR_OFF), 1)


def eq8(got, want):
    return (got & 0xFF) == (want & 0xFF)


def check_encodings():
    """
    The forms this cave needs that a64.py does not carry are encoded by hand
    in ff7nx_analog_cave.py. Same rule as a64: capstone decides, not me.
    """
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    cases = [
        (C.ldr_s(0, 9, 0x30), 'ldr s0, [x9, #0x30]'),
        (C.ldr_s(3, 9, 0x3c), 'ldr s3, [x9, #0x3c]'),
        (C.fsub_s(0, 0, 1), 'fsub s0, s0, s1'),
        (C.fcvtzs_fix(11, 0, 12), 'fcvtzs w11, s0, #0xc'),
        (C.sdiv(4, 4, 5), 'sdiv w4, w4, w5'),
        (C.eor_reg(0, 10, 2), 'eor w0, w10, w2'),
        (C.orr_reg(4, 0, 1), 'orr w4, w0, w1'),
        (C.sub_reg(0, 0, 2), 'sub w0, w0, w2'),
        (C.sub_imm(22, 22, 256), 'sub w22, w22, #0x100'),
        (C.add_shifted(12, 12, 14, 1), 'add w12, w12, w14, lsl #1'),
        (C.csel(6, 0, 1, C.COND_GE), 'csel w6, w0, w1, ge'),
        (C.ldrsb_w(5, 0, 0), 'ldrsb w5, [x0]'),
        (C.ldrsb_w(5, 3, 9), 'ldrsb w5, [x3, #9]'),
        (C.cbz64(9, 0x1000, 0x1080), 'cbz x9, #0x1080'),
    ]
    bad = 0
    for word, want in cases:
        got = [(i.mnemonic + ' ' + i.op_str).strip()
               for i in md.disasm(struct.pack('<I', word), 0x1000)]
        if got != [want]:
            fail('encoding %08X -> %r, wanted %r' % (word, got, want))
            bad += 1
    ok(bad == 0, '%d hand-rolled encodings agree with capstone' % len(cases))


def main():
    print('the encodings a64.py does not carry')
    check_encodings()

    print()
    print()
    print('the field cave is inert when it must be')
    mem, cpu = setup(0.9, 0.0, 0b0001, base=100, obj=0)
    exit_pc, _ = run_field(mem, cpu)
    ok(eq8(written(mem, cpu), 100),
       'a null link in the input chain -> control direction untouched')
    ok(exit_pc == AN.FIELD_HOOK + 4, 'and it still returns to the hook')

    for k in range(len(AN.INPUT_CHAIN) + 1):
        mem, cpu = setup(0.9, 0.0, 0b0001, base=100)
        break_link(mem, k)
        run_field(mem, cpu)
        if not eq8(written(mem, cpu), 100):
            fail('a null at chain link %d was not caught' % k)
    ok(True, 'and every one of the %d links is null-checked'
       % (len(AN.INPUT_CHAIN) + 1))

    mem, cpu = setup(0.0, 0.0, 0b0001, base=100, captured=True)
    run_field(mem, cpu)
    ok(eq8(written(mem, cpu), 100), 'centred stick -> writes the base back unchanged')

    mem, cpu = setup(0.9, 0.3, 0b0101, base=100, captured=True)
    run_field(mem, cpu)
    ok(eq8(written(mem, cpu), 100), 'left+right at once -> base, no rotation')

    mem, cpu = setup(0.9, 0.3, 0b0001, base=100, captured=True, level=0)
    run_field(mem, cpu)
    ok(eq8(written(mem, cpu), 100),
       'no level loaded -> the stale triggers pointer is left alone')

    mem, cpu = setup(0.9, 0.3, 0b0001, base=100, captured=True, triggers=0)
    run_field(mem, cpu)
    ok(True, 'a null triggers pointer does not fault')

    print()
    print('it writes ONE byte, where the game reads one byte')
    mem, cpu = setup(0.0, 0.0, 0b0001, base=100, captured=True)
    g = cpu.guest_to_host
    mem.setu(g(TRIG_PTR + 0x0A), 0x1234, 2)          # focus_height
    run_field(mem, cpu)
    ok(mem.u(g(TRIG_PTR + 0x0A), 2) == 0x1234,
       'focus_height at +0x0A is untouched (a 16-bit store would corrupt it)')

    mem, cpu = setup(-0.9, -0.4, 0b1100, base=-100, captured=True,
                     saved_base=-100)
    run_field(mem, cpu)
    o = AN.offset(int(-0.9 * 4096), int(-0.4 * 4096), 0b1100)
    ok(eq8(written(mem, cpu), -100 + o),
       'a negative base round-trips through the signed byte')

    print()
    print('the sweep: 8 key masks x 360 degrees, full cave each time')
    bad = 0
    worst = 0
    n = 0
    for km in sorted(AN.SNAP):
        for deg in range(360):
            th = math.radians(deg)
            sx, sy = math.cos(th), math.sin(th)
            ix = int(sx * 4096)
            iy = int(sy * 4096)
            want = AN.offset(ix, iy, km)
            mem, cpu = setup(sx, sy, km, base=100, captured=True)
            run_field(mem, cpu)
            got = arm64emu.s8(written(mem, cpu) - 100)
            n += 1
            if not eq8(written(mem, cpu), 100 + want):
                d = abs(got - want)
                worst = max(worst, d)
                bad += 1
                if bad <= 3:
                    print('      km=%s deg=%3d  cave %+4d  model %+4d'
                          % (format(km, '04b'), deg, got, want))
    ok(bad == 0, '%d of %d exact against the reference model%s'
       % (n - bad, n, '' if bad == 0 else ' (worst delta %d)' % worst))

    print()
    print('the base is captured per field, not per frame')
    mem, cpu = setup(0.9, 0.3, 0b0001, base=55, field_id=9,
                     captured=False, saved_id=9)
    run_field(mem, cpu)
    o = AN.offset(int(0.9 * 4096), int(0.3 * 4096), 0b0001)
    ok(eq8(written(mem, cpu), 55 + o),
       'first frame in a field captures the field\'s own value')
    ok(mem.u(AN.ANALOG_BASE + 8, 4) == 55, 'and remembers it')
    ok(mem.u(AN.ANALOG_BASE + 4, 4) == 1, 'and marks it captured')
    ok(mem.u(AN.ANALOG_BASE, 4) == 9, 'against the field id')

    # second frame: the slot now holds base+offset; the cave must NOT re-read it
    mem2, cpu2 = setup(0.9, 0.3, 0b0001, base=55 + o, field_id=9,
                       captured=True, saved_id=9, saved_base=55)
    run_field(mem2, cpu2)
    ok(eq8(written(mem2, cpu2), 55 + o),
       'second frame reuses the saved base instead of compounding')

    mem3, cpu3 = setup(0.9, 0.3, 0b0001, base=120, field_id=10,
                       captured=True, saved_id=9, saved_base=55)
    run_field(mem3, cpu3)
    ok(eq8(written(mem3, cpu3), 120 + o), 'a NEW field id re-captures')

    print()
    print('the cave is chained through scattered holes, and still works')
    n = len(field_build(0, lambda i: 4 * i))
    runs = scattered_runs(n)
    _, code, _ = lay_out(field_build)
    ok(len(runs) > 20, '%d words laid out across %d separate holes' % (n, len(runs)))
    ok(max(code) - min(code) > 0x10000,
       'spread over 0x%X bytes of .text, not one block' % (max(code) - min(code)))
    ok(len(set(code)) == len(code) and all(a % 4 == 0 for a in code),
       'every word lands on its own aligned address')
    mem, cpu = setup(0.6, 0.8, 0b0011, base=100, captured=True)
    run_field(mem, cpu)
    chained = written(mem, cpu)
    mem2, cpu2 = setup(0.6, 0.8, 0b0011, base=100, captured=True)
    run_field_contiguous(mem2, cpu2)
    ok(chained == written(mem2, cpu2),
       'and gives the same answer as the same cave laid out contiguously')

    print()
    print('it survives the translator destroying x0..x18 four times')
    mem, cpu = setup(0.6, 0.8, 0b0011, base=100, captured=True)
    _, words = run_field(mem, cpu)
    ok(cpu.translate_calls == 4, 'four translator calls were made')
    want = AN.offset(int(0.6 * 4096), int(0.8 * 4096), 0b0011)
    ok(eq8(written(mem, cpu), 100 + want),
       'and the offset computed before them still reached the write')

    print()
    print('the page map is real, and the old bug would fail here')
    mem, cpu = setup(0.6, 0.8, 0b0011, base=100, captured=True)
    flat = HOST + LEVEL_GUEST + TRIGGERS + AN.CONTROL_DIR_OFF + 4
    stale = mem.u(flat, 1)
    run_field(mem, cpu)
    ok(cpu.guest_to_host(TRIG_PTR + AN.CONTROL_DIR_OFF)
       != cpu.guest_to_host(LEVEL_GUEST) + TRIGGERS + AN.CONTROL_DIR_OFF + 4,
       'translate(level) + triggers_offset is NOT translate(control direction)')
    ok(mem.u(flat, 1) == stale,
       'and nothing was written at the address the flat model would have used')

    print()
    print('callee-saved registers come back')
    mem, cpu = setup(0.6, 0.8, 0b0011, base=100, captured=True)
    marks = {r: 0x1111000000000000 + r for r in range(19, 29)}
    for r, v in marks.items():
        cpu.x[r] = v
    cpu.x[25] = 0xDEADBEEFCAFE
    marks[25] = 0xDEADBEEFCAFE
    run_field(mem, cpu)
    for r, v in marks.items():
        if cpu.x[r] != v:
            fail('x%d was clobbered (%X -> %X)' % (r, v, cpu.x[r]))
    ok(all(cpu.x[r] == v for r, v in marks.items()),
       'x19..x28 are all restored, including x25 which the caller needs')
    ok(cpu.sp == STACK, 'and the stack is balanced')

    print()
    print('the diagnostic build ignores the stick and rotates a fixed 45 deg')
    mem, cpu = setup(0.0, 0.0, 0b0000, base=100, captured=True, obj=0)
    put_tables(mem)
    entry, code, _ = lay_out(diag_build)
    for va, w in code.items():
        mem.setu(va, w, 4)
    exit_pc = cpu.run(entry, None, code=code)
    ok(eq8(written(mem, cpu), 132),
       'writes base+32 with no input object, no keys and a centred stick')
    ok(exit_pc == AN.FIELD_HOOK + 4, 'and still returns to the hook')

    print()
    print('mutations (each must change the answer)')
    base_mem, base_cpu = setup(0.6, 0.8, 0b0011, base=100, captured=True)
    run_field(base_mem, base_cpu)
    good = written(base_mem, base_cpu)
    for name, kw in (('the stick moved', dict(sx=0.8, sy=0.6)),
                     ('a different key mask', dict(keymask=0b0010)),
                     ('a different base', dict(base=120))):
        a = dict(sx=0.6, sy=0.8, keymask=0b0011, base=100, captured=True)
        a.update(kw)
        m2, c2 = setup(a['sx'], a['sy'], a['keymask'], base=a['base'],
                       captured=True, saved_base=a['base'])
        run_field(m2, c2)
        ok(written(m2, c2) != good, 'caught: %s' % name)

    print()
    if FAIL:
        print('%d FAILED' % len(FAIL))
        return 1
    print('all good')
    return 0


if __name__ == '__main__':
    sys.exit(main())
