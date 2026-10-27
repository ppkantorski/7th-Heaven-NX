#!/usr/bin/env python3
"""ff7nx_audiopool: the sound-buffer pool cave, on the real module.

    python3 tests/test_audiopool.py [path/to/main]

Installs the patch, decodes the result, then EXECUTES the placed cave under
arm64emu at its real scattered addresses for both outcomes of the heap
allocation, and checks every register the init function relies on.

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
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import a64 as A                                                  # noqa: E402
import arm64emu as EM                                            # noqa: E402
import ff7nx_audio_cave as AC                                    # noqa: E402
import ff7nx_audiopool as P                                      # noqa: E402

src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, 'dump',
                                                         'exefs', 'main')
FAIL = []


def check(name, ok):
    print('  %s  %s' % ('ok  ' if ok else 'FAIL', name))
    if not ok:
        FAIL.append(name)


SP0 = 0x7300001000
ALLOCATOR = 0x5D12345678


def run_cave(code, entry, heap_result, calls):
    mem = EM.Mem()

    def aligned_alloc(cpu):
        calls.append((cpu.x[0], cpu.x[1]))
        for r in range(1, 18):                # a callee may clobber these
            cpu.x[r] = 0xDEAD0000 + r
        cpu.x[0] = heap_result

    cpu = EM.Cpu(mem, native={P.ALIGNED_ALLOC: aligned_alloc})
    cpu.x = [0x740000000 + i * 0x100 for i in range(32)]
    cpu.x[0] = ALLOCATOR
    cpu.x[19], cpu.x[21] = 0x1919191919, 0x2121212121
    cpu.x[29] = 0x2929292929
    cpu.x[30] = P.BASE_SITE + 4               # the `bl` at the site set this
    cpu.sp = SP0
    out = cpu.run(entry, code)
    return cpu, out


with tempfile.TemporaryDirectory() as d:
    dest = os.path.join(d, 'main')
    for mb in (96, 64, 128):
        rep = P.apply_to_nso(src, dest, mb)
        segs, raw = AC.segments(open(dest, 'rb').read())
        text = raw[0]
        check('%d MB: site state is patched' % mb,
              P.state(text) == 'patched')
        site = struct.unpack_from('<I', text, P.BASE_SITE)[0]
        check('%d MB: +0x1127DA0 is a bl to the cave' % mb,
              site == A.bl(P.BASE_SITE, rep['cave_entry']))
        check('%d MB: add x20,x20,#0 and both calls untouched' % mb,
              not P.verify(text))
        code = {va: struct.unpack_from('<I', text, va)[0]
                for va in rep['code'] if va not in
                (P.BASE_SITE, P.SIZE_SITE_1, P.SIZE_SITE_2)}
        size = mb << 20

        calls = []
        heap = 0x5DAB000000                      # 4 KB aligned, > 4 GB
        cpu, out = run_cave(code, rep['cave_entry'], heap, calls)
        check('%d MB heap ok: returns to the site + 4' % mb,
              out == P.BASE_SITE + 4)
        check('%d MB heap ok: asked for (size, 4 KB)' % mb,
              calls == [(size, 0x1000)])
        check('%d MB heap ok: x20 = heap block, w22 = size' % mb,
              cpu.x[20] == heap and cpu.x[22] & 0xFFFFFFFF == size)
        check('%d MB heap ok: x0 allocator, sp, x19, x21, x29 intact' % mb,
              cpu.x[0] == ALLOCATOR and cpu.sp == SP0
              and cpu.x[19] == 0x1919191919 and cpu.x[21] == 0x2121212121
              and cpu.x[29] == 0x2929292929)

        calls = []
        cpu, out = run_cave(code, rep['cave_entry'], 0, calls)
        check('%d MB heap NULL: falls back to the stock static pool' % mb,
              out == P.BASE_SITE + 4 and cpu.x[20] == P.STATIC_POOL
              and cpu.x[22] & 0xFFFFFFFF == P.STOCK_BYTES)
        check('%d MB heap NULL: x0/sp intact' % mb,
              cpu.x[0] == ALLOCATOR and cpu.sp == SP0)

        # After the cave the stock `add x20, x20, #0` runs, then the patched
        # size words read w22: model them to show the two consumers agree.
        check('%d MB: both size sites read w22' % mb,
              struct.unpack_from('<I', text, P.SIZE_SITE_1)[0]
              == A.mov_reg(2, 22)
              and struct.unpack_from('<I', text, P.SIZE_SITE_2)[0]
              == A.mov_reg(3, 22))
    check('stock size installs nothing', P.apply_to_nso(src, dest, 32) is None)
    try:
        P.apply_to_nso(dest if os.path.exists(dest) else src, dest + '2', 96)
        rep2 = True
    except ValueError:
        rep2 = False
    check('refuses a module that is not at the stock init',
          (not rep2) or P.state(AC.segments(open(src, 'rb').read())[1][0])
          == 'stock')

print('\n%d failure(s)' % len(FAIL))
print('PASS' if not FAIL else 'FAIL')
sys.exit(1 if FAIL else 0)
