#!/usr/bin/env python3
"""Focused regression test for Neo Bahamut's 16:9 backdrop geometry.

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
import hashlib
import shutil
import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import a64 as A
import arm64emu as E
import ff7nx_battlewide as B
import nxmap


def check(ok, what):
    if not ok:
        raise AssertionError(what)
    print('  ok  ' + what)


def word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def scaled(mov_word, mul_word, value, out_reg):
    cpu = E.Cpu(E.Mem(), host_base=0x700000000)
    cpu.sp = 0x800000000
    cpu.x[8] = value & 0xFFFFFFFF
    cpu.run(0x200000, [mov_word, mul_word])
    return cpu.x[out_reg] & 0xFFFFFFFF


def main():
    stock = ROOT / 'game_data_files/exefs/main'
    check(stock.exists(), 'stock NSO exists')

    source = (ROOT / 'repos/FFNx-master/src/ff7/widescreen.cpp').read_text()
    for off in (0x58, 0x88):
        check(('neo_bahamut_effect_sub_490F2A + 0x%X, '
               'wide_viewport_width / 4' % off) in source,
              'FFNx owns Neo Bahamut width/4 at x86 +0x%X' % off)

    sm = nxmap.Main(str(stock))
    rect, _notes, problems = B.plan(sm, 4, False)
    check(not problems, 'stock Neo Bahamut rect consumers are unambiguous')
    check(len(rect) == 20, 'all 20 isolated Switch rect consumers are found')
    scale, _notes, problems = B.neo_bahamut_scale_plan(sm, False)
    check(not problems, 'stock segment multipliers and anchors are exact')
    check(len(scale) == 4, 'both x160 strength-reduced pairs are found')

    wide = {
        0x00280238: A.movz(9, B.NEO_BAHAMUT_SEGMENT),
        0x00280248: A.mul(8, 8, 9),
        0x002802C8: A.movz(9, B.NEO_BAHAMUT_SEGMENT),
        0x002802CC: A.mul(22, 8, 9),
    }

    with tempfile.TemporaryDirectory(prefix='neo-bahamut-wide-') as td:
        work = Path(td) / 'main'
        shutil.copy2(stock, work)
        original = digest(work)

        check(B.apply(work, 4, log=lambda *_: None) == 0,
              'consumer-local backdrop geometry applies')
        check(B.apply_neo_bahamut_scale(work, log=lambda *_: None) == 0,
              'width/4 segment scaling applies')
        once = digest(work)

        pm = nxmap.Main(str(work))
        rect, _notes, problems = B.plan(pm, 4, False)
        check(not problems and not rect,
              'all 20 consumers are already wide after apply')
        scale, _notes, problems = B.neo_bahamut_scale_plan(pm, False)
        check(not problems and not scale,
              'both segment pairs are already wide after apply')
        for va, expected in wide.items():
            check(word(pm.img, va) == expected,
                  'segment instruction +0x%X is exact' % va)
        for value in (0, 1, 2, 7, 31, 0x1234, 0xFFFF):
            expected = (value * 213) & 0xFFFFFFFF
            check(scaled(wide[0x00280238], wide[0x00280248], value, 8)
                  == expected, 'first segment maps %d -> 213*x' % value)
            check(scaled(wide[0x002802C8], wide[0x002802CC], value, 22)
                  == expected, 'second segment maps %d -> 213*x' % value)

        check(B.apply(work, 4, log=lambda *_: None) == 0 and
              B.apply_neo_bahamut_scale(work, log=lambda *_: None) == 0 and
              digest(work) == once, 'reapplying is byte-identical')

        check(B.apply_neo_bahamut_scale(work, revert=True,
                                        log=lambda *_: None) == 0,
              'segment scaling reverts')
        check(B.apply(work, 4, revert=True, log=lambda *_: None) == 0,
              'consumer geometry reverts')
        check(digest(work) == original, 'revert restores the stock NSO exactly')

    print('Neo Bahamut widescreen regression test passed.')


if __name__ == '__main__':
    main()
