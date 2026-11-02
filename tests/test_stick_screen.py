#!/usr/bin/env python3
"""
test_stick_screen.py -- BUILD 869: the field cave's screen-relative stick.

Runs the ACTUAL ENCODED WORDS of ff7nx_analog_cave.build_screen_cave in
arm64emu, chained through scattered padding holes the way they ship, on the
legacy 360 test's memory model (tests/legacy/test_analog_cave.py), plus the
camera section the new form reads. Also: the 360-only cave's words are
unchanged, and the camera rule against every field in flevel.lgp when the
game dump is present.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'tests', 'legacy'))

import math
import random
import struct
import sys

import ff7nx_analog as AN
import ff7nx_analog_cave as C
import test_analog_cave as T

FAIL = []
CAM_OFF = 0x3A1E           # camera section offset in the file (odd alignment)


def ok(cond, what):
    print('  %s  %s' % ('ok' if cond else 'FAIL', what))
    if not cond:
        FAIL.append(what)


def cam_base(vx_x, vx_y):
    d = AN.dir256(vx_x, vx_y)
    return None if d is None else (d + AN.SCREEN_OFFSET) & 0xFF


def setup(sx, sy, keymask, vx=(2487, 3253), base=-96, cam_off=CAM_OFF,
          **kw):
    mem, cpu = T.setup(sx, sy, keymask, base=base & 0xFF, **kw)
    g = cpu.guest_to_host
    mem.setu(g(C.SECTIONS_GUEST + 4 * C.CAMERA_SECTION), cam_off, 4)
    cam = T.LEVEL_GUEST + cam_off + 4
    raw = struct.pack('<hh', *vx)
    for k in range(4):              # byte by byte: the pages are not adjacent
        mem.setu(g(cam + k), raw[k], 1)
    return mem, cpu


def run(mem, cpu, rot360):
    T.put_tables(mem)

    def build(cave, addr=None):
        return C.build_field_cave(cave, T.SNAP_VA, T.ATAN_VA, addr,
                                  rot360=rot360, screen=True)
    entry, code, _ = T.lay_out(build)
    for va, w in code.items():
        mem.setu(va, w, 4)
    pc = cpu.run(entry, None, code=code)
    return pc, len(code)


def main():
    print('the 360-only cave is unchanged by the new form')
    a = C.build_field_cave(0x1152680, T.SNAP_VA, T.ATAN_VA)
    b = C._build_field_cave_360(0x1152680, T.SNAP_VA, T.ATAN_VA)
    ok(a == b, 'build_field_cave() without `screen` emits the same %d words'
       % len(a))

    for rot in (False, True):
        print()
        print('screen-relative, 360 %s' % ('on' if rot else 'off'))
        want = cam_base(2487, 3253)
        mem, cpu = setup(0.0, 0.9, 0b0010)
        pc, n = run(mem, cpu, rot)
        ok(pc == AN.FIELD_HOOK + 4, 'returns to the hook (%d words)' % n)
        ok(T.eq8(T.written(mem, cpu), want),
           'stick up -> the camera base %d (field own -96)' % want)
        mem, cpu = setup(0.0, 0.0, 0b0010)
        run(mem, cpu, rot)
        ok(T.eq8(T.written(mem, cpu), -96), 'D-pad up -> the field\'s own -96')
        mem, cpu = setup(0.0, 0.3, 0b0010)
        run(mem, cpu, rot)
        ok(T.eq8(T.written(mem, cpu), -96),
           'stick inside the 0.4 deadzone with the D-pad -> the field\'s own')
        mem, cpu = setup(0.0, 0.9, 0b0010, obj=0)
        run(mem, cpu, rot)
        ok(T.eq8(T.written(mem, cpu), -96), 'null input object -> untouched')
        mem, cpu = setup(0.0, 0.9, 0b0010, level=0)
        run(mem, cpu, rot)
        ok(T.eq8(T.written(mem, cpu), -96), 'no level loaded -> untouched')
        # every alignment of the camera within a page, incl. straddling ones
        bad = 0
        for low in range(0xFF8, 0x1008):
            off = (0x3000 + low) - 4
            mem, cpu = setup(0.0, 0.9, 0b0010, cam_off=off, vx=(-2237, -3432))
            run(mem, cpu, rot)
            if not T.eq8(T.written(mem, cpu), cam_base(-2237, -3432)):
                bad += 1
        ok(bad == 0, 'camera bytes read right at every offset across a page '
           'boundary')
        # captured once per field
        mem, cpu = setup(0.0, 0.9, 0b0010)
        run(mem, cpu, rot)
        g = cpu.guest_to_host
        cam = T.LEVEL_GUEST + CAM_OFF + 4
        for k, by in enumerate(struct.pack('<hh', 4096, 0)):
            mem.setu(g(cam + k), by, 1)
        cpu2 = cpu
        cpu2.sp = T.STACK
        run(mem, cpu2, rot)
        ok(T.eq8(T.written(mem, cpu2), want),
           'same field: the base captured on entry is kept')
        mem.setu(g(AN.FIELD_ID_GUEST), 8, 2)
        mem.setu(g(T.TRIG_PTR + AN.CONTROL_DIR_OFF), 0x20, 1)
        cpu2.sp = T.STACK
        run(mem, cpu2, rot)
        ok(T.eq8(T.written(mem, cpu2), cam_base(4096, 0)),
           'new field id: base measured again')
        cpu2.sp = T.STACK
        mem2 = mem
        for code_, val in ((AN.DIK_UP, 0),):
            mem2.setu(AN.KEYBUF + code_, val, 1)
        # random sticks: the 360 offset on top of the camera base
        rng = random.Random(5)
        bad = 0
        for _ in range(200):
            ang = rng.uniform(0, 2 * math.pi)
            sx, sy = 0.95 * math.cos(ang), 0.95 * math.sin(ang)
            km = (1 if sx > 0.4 else 0) | (2 if sy > 0.4 else 0) | \
                 (4 if sx < -0.4 else 0) | (8 if sy < -0.4 else 0)
            vx = (rng.randint(-4096, 4096), rng.randint(-4096, 4096))
            mem, cpu = setup(sx, sy, km, vx=vx)
            run(mem, cpu, rot)
            base = cam_base(*vx)
            if base is None:
                base = -96
            off = AN.offset(int(sx * 4096), int(sy * 4096), km) if rot else 0
            if not T.eq8(T.written(mem, cpu), base + off):
                bad += 1
        ok(bad == 0, '200 random sticks and cameras: camera base%s'
           % (' + the 360 offset' if rot else ', no rotation'))

    print()
    print('the camera rule against the real fields')
    try:
        import build
        import lgp
        d = build.find_game_dump(nxpaths.ROOT)
        arc = lgp.Archive(_nx_os.path.join(d.workingdir, 'data', 'field',
                                           'flevel.lgp'))
    except Exception as exc:                                   # noqa: BLE001
        print('  (skipped: %s)' % exc)
        arc = None
    if arc is not None:
        near = tilted = n = 0
        for e in (arc.index.values() if isinstance(arc.index, dict)
                  else arc.index):
            if not arc.is_field(e):
                continue
            try:
                s = lgp.split_sections(arc.decompressed(e))
            except Exception:                                  # noqa: BLE001
                continue
            vx = struct.unpack_from('<3h', s[1], 0)
            own = struct.unpack_from('<b', s[7], 9)[0]
            b = cam_base(vx[0], vx[1])
            n += 1
            tilted += vx[2] != 0
            dev = ((own - b + 128) % 256) - 128
            near += abs(dev) <= 4
        ok(n > 700, '%d fields read' % n)
        ok(near * 2 > n, 'the designers\' own value is within 4 units of the '
           'camera rule on %d of them' % near)
        print('  (%d fields with any camera roll)' % tilted)

    # BUILD 870: the cave lives in a dead body of its own, not in padding.
    nso = _nx_os.path.join(nxpaths.ROOT, 'dump', 'exefs', 'main')
    if _nx_os.path.exists(nso):
        print()
        print('dead body for the screen-relative cave:')
        import nxmap
        import ff7nx_60fps as F
        import ff7nx_analog_cave as AC
        text = bytearray(nxmap.Main(nso).text)
        lo, hi = F._dead_body_region(nso, F.SCREEN_STICK_DEAD_X86, text)
        n = len(AC.build_field_cave(lo, 0x1200000, 0x1200040, None, False,
                                    True, True))
        ok(lo + 4 * n <= hi, 'x86 0x%X is dead and takes the %d-word cave '
           '(%d words of room)' % (F.SCREEN_STICK_DEAD_X86, n, (hi - lo) // 4))

    print()
    print('all checks passed' if not FAIL else '%d FAILED' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
