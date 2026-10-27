"""Hand-derived edits: fn -> [(arm addr, expected old asm, new asm, note)]

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
MANUAL = {
    0x6ca1ef: [(0xcff228, 'mov w19, #0x7e3e', 'movz w19, #0xee7e', 'cap-1 lo'),
               (0xcff22c, 'movk w19, #5, lsl #16', 'movk w19, #0x36, lsl #16', 'cap-1 hi')],
    0x6ca23f: [(0xcff4e8, 'mov w19, #0x7e3e', 'movz w19, #0xee7e', 'cap-1 lo'),
               (0xcff4ec, 'movk w19, #5, lsl #16', 'movk w19, #0x36, lsl #16', 'cap-1 hi')],
    0x6ca2a9: [(0xcff898, 'mov w19, #0x7e3e', 'movz w19, #0xee7e', 'cap-1 lo'),
               (0xcff89c, 'movk w19, #5, lsl #16', 'movk w19, #0x36, lsl #16', 'cap-1 hi')],
    0x6f62f7: [(0xdacaec, 'mov w22, #0xd', 'mov w22, #0xb', '6f6375 (port keeps 0xf for its flag-set mode)')],
    0x6f9739: [(0xce43f0, 'sub w8, w8, #0xc', 'sub w8, w8, #0xb', '6f984a (x-1)*c folded'),
               (0xce50dc, 'sub w8, w8, #0xc', 'sub w8, w8, #0xb', '6f9a85 (x-1)*c folded')],
    0x70212a: [(0xdc2c30, 'add w21, w9, #0x52', 'add w21, w9, #0x4d', '7022e7 lea +0x52->0x4d'),
               (0xdc2da8, 'add w8, w8, w8, lsl #4', 'mov w10, #0x1a', '70236e imul 0x22->0x1a'),
               (0xdc2dac, 'lsl w8, w8, #1', 'mul w8, w8, w10', '70236e'),
               (0xdc3608, 'add w8, w8, w8, lsl #4', 'mov w10, #0x1a', '702699 imul 0x22->0x1a'),
               (0xdc360c, 'lsl w8, w8, #1', 'mul w8, w8, w10', '702699')],
    0x6ca346: [
        # 6ca94b/952: mov edx,0xa8 ; add edx,0x1a4 (folded 0x24c) -> 0x1b0 (0x258)
        # 6ca880 and 6ca8db are identical blocks; the port kept two copies
        (0xd0251c, 'add w19, w8, #0x12', 'add w19, w8, #0x11', '6ca8e9 copy'),
        (0xd02548, 'mov w8, #0x1c6', 'mov w8, #0x1d2', '6ca8ed copy'),
        (0xd02608, 'mov w9, #0x24c', 'mov w9, #0x258', '6ca952 folded'),
        # 6ca9d6 push 2 -> 3: w23=2 is shared by later pushes; store 3 then
        # restore 2; the freed slot comes from re-deriving 0xdc08b8 from w25
        (0xd02870, 'mov w23, #2', 'mov w23, #3', '6ca9d6'),
        (0xd02874, 'add w21, w25, #0x688', 'str w23, [x0]', '6ca9d6'),
        (0xd02878, 'str w23, [x0]', 'mov w23, #2', '6ca9d6'),
        (0xd0287c, 'mov w0, w21', 'add w0, w25, #0x688', '6ca9d6'),
        (0xd02a38, 'mov w0, w21', 'add w0, w25, #0x688', 'w21 freed'),
        (0xd02bd0, 'mov w0, w21', 'add w0, w25, #0x688', 'w21 freed'),
        # 6caa02..11: push (timer<0x8000 ? 7 : 0) -> push 7
        (0xd02964, 'csel w22, w20, wzr, lo', 'mov w22, w20', '6caa11 push 7'),
        # 6cac53: shl edx,1 -> imul dx,dx,0x3d ; shr edx,5  (dl*61>>5)
        (0xd0339c, 'strb w8, [x24, #8]', 'mov w9, #0x3d', '6cac53'),
        (0xd033a0, 'ldr w8, [x24, #8]', 'mul w26, w8, w9', '6cac53'),
        (0xd033a4, 'lsl w26, w8, #1', 'lsr w26, w26, #5', '6cac53'),
    ],
}

# raw data words (not code): fn -> [(addr, old u32, new u32, note)]
RAW = {
    0x703176: [(0x11b0fb0, 0x1a4, 0x1b0, '7032fd push 0x1a4->0x1b0: the port reads this x '
                'from a per-language table [en, fr, de, es]; en entry only, sole reader dc534c')],
}
