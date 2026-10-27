"""PHS screen (700c90): ESUI only moves coordinates. The automatic align maps 7012dd onto
dbc71c (an address add), so every edit is given by hand.

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
FNS.setdefault(0x700c90, {})
FNS[0x700c90].update(
    no_align=True,
    struct_x86=ALLCH(0x700c90),
    arm_struct={
        0xdbe0f0: A.add_imm(21, 9, 0x2d),     # 70124f
        0xdbe138: A.movz(8, 0x52),            # 701263 imul 0x4d -> 0x52
        0xdbe14c: A.add_imm(21, 8, 0x2),      # 701266
        0xdbe1e4: A.add_imm(21, 8, 0x44),     # 70121c
        0xdbe210: A.movz(8, 0),               # 701221 push -5 -> 0
        0xdbe2f4: A.add_imm(21, 8, 0x2e),     # 7012dd
        0xdbe340: A.movz(8, 0x52),            # 7012f1
        0xdbe350: A.add_imm(21, 9, 0x2),      # 7012f4
        0xdbe45c: A.add_imm(22, 8, 0xd),      # 701336
        0xdbe494: A.add_imm(22, 8, 0x8f),     # 701344
        0xdbe5b0: A.add_imm(21, 8, 0xc),      # 701373
        0xdbe5e8: A.add_imm(21, 8, 0x36),     # 701381
        0xdbe7ac: A.add_imm(21, 8, 0xd),      # 7013cc
        0xdbe7e4: A.add_imm(21, 8, 0x8f),     # 7013d9
        0xdbe908: A.add_imm(21, 8, 0xc),      # 701408
        0xdbe940: A.add_imm(21, 8, 0x36),     # 701416
        0xdbeafc: A.movz(28, 0x52),           # 70150e (w28 only feeds that mul)
        0xdbf0d8: A.add_imm(20, 8, 0x18),     # 7015c0
        0xdbedfc: A.movz(27, 0x2d),           # 7015c5 push 0x20 -> 0x2d (w27 only pushed there)
    })
# [DCA160] = 1 with [DCA130] = 3 skips the input handling (which rewrites [91F480]).
# reach the 'selected materia' cursor ([DCA118] on, [DCA180] = 1) and the [91F480] = 2 /
# [91F47C] = 1 branch; window at 0xdd0000, its words and every row/column index varied
_PHSW = {0xdca124: 0xdd0000, 0xdc130c: 0, 0xdca118: 1, 0xdc0230: 0xff020100,
         0xdca148: 0x03020100, 0xdca14c: 0x07060504, 0xdca150: 0x0b0a0908}
FNS[0x700c90]['pokes'] = [
    {**_PHSW, 0xdca180: sel, 0xdca160: hold, 0xdca130: 3, 0x91f480: mode, 0x91f47c: m2,
     0xdca140: r, 0xdca13c: c, 0xdca128: (r + 1) % 3, 0xdca11c: (c + 2) % 3, 0xdca17c: r, 0xdca120: c,
     0xdd0008: xy, 0xdd000c: xy ^ 0x00110022, 0xdd0010: xy + 0x00050003, 0xdd0014: 0, 0xdd0018: xy ^ 0x00300040}
    for sel, hold, mode, m2 in ((1, 0, 2, 1), (0, 0, 1, 0), (1, 1, 2, 1), (0, 1, 2, 1), (1, 1, 2, 0), (0, 1, 0, 1))
    for r, c, xy in ((0, 0, 0x00200010), (1, 2, 0x00480064), (2, 1, 0x00100140))]
