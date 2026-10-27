"""tools/esui/port/agent_cfg/705d16.py

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
FNS.setdefault(0x705d16, {})
# equip screen. Every change carried by hand (the port duplicates the [dca6a0] 0/1 draw paths and
# shares constants in registers: w25 = 7 (two jle 8 overflow tests), w22 = 0x24 / 0x12f, w20 = 0xab / 0x11)
_c = FNS[0x705d16]
_c['no_align'] = True
_c['struct_x86'] = ALLCH(0x705d16)
_SUBS_C = 0x71003128                                   # subs w8, w9, #0xc
_c['arm_struct'] = {
    # 705d7f / 705de3 / 7071cd: list rows shown 8 -> 0xc (jle: N from w9-c, V from (b-1 - w9))
    0xdd1e8c: A.movz(25, 0xb), 0xdd1e94: _SUBS_C, 0xdd1fa8: _SUBS_C,
    0xdd1ed0: A.movz(8, 0xc), 0xdd1f18: A.movz(8, 0x26f), 0xdd1f34: A.movz(8, 0xc),
    0xdd1fd8: A.movz(20, 0xc),
    0xdd6d8c: A.movz(12, 0xb), 0xdd6d94: _SUBS_C, 0xdd6dc4: A.movz(20, 0xc),
    # 705eb8 list rows x36 -> x24, +0xc1 -> +0xb1, x 0x1ab -> 0x1bb
    **_R24(0xdd2188), 0xdd21ac: A.add_imm(20, 8, 0xb1), 0xdd20fc: A.movz(23, 0x1bb),
    # 7060be path: cursor y x0x22, x 0xf0; list cursor y x24 + 0xb5, x 0x190
    0xdd24b0: A.movz(9, 0x22), 0xdd24e4: A.movz(8, 0xf0),
    0xdd252c: A.add_imm(20, 8, 0xb5), 0xdd2558: A.movz(8, 0x190),
    # 705f01 path: cursor x 0xf0
    0xdd28c8: A.movz(8, 0xf0),
    # both paths: labels x 0x1b -> 0x35, 70760f (x 0x99 -> 0x96, y +0x15 -> +0x17), centre 0xf3 -> 0xb2
    0xdd2628: A.movz(25, 0x35), 0xdd2994: A.movz(25, 0x35),
    0xdd27f0: A.add_imm(22, 8, 0x17), 0xdd281c: A.movz(8, 0x96),
    0xdd2b7c: A.add_imm(21, 8, 0x17), 0xdd2ba8: A.movz(8, 0x96),
    0xdd3840: A.add_imm(22, 8, 0x17), 0xdd386c: A.movz(8, 0x96),
    0xdd3e08: A.add_imm(23, 8, 0x17), 0xdd3e34: A.movz(8, 0x96),
    0xdd3a4c: A.movz(10, 0xb2), 0xdd401c: A.movz(9, 0xb2),
    # 706286..: stat labels x 0xfa -> 0x11a, 707891 y +0x1a -> +0x1d, 6e6c5b y 9 -> 0xc, equipment x 0x12f -> 0x14b
    0xdd4068: A.movz(25, 0x11a), 0xdd41a8: A.add_imm(20, 8, 0x1d), 0xdd42b0: A.movz(8, 0xc),
    0xdd43bc: A.movz(22, 0x14b),
}
_c['replace'] = [
    (0xdd1f28, [A.movz(8, 0xaa), A.strh(8, 0, 0)]),          # [dca498] 0xab -> 0xaa (w20 is the shared 0xab)
    (0xdd2898, [A.movz(9, 0x22), A.mul(8, 8, 9)]),           # 705f0c cursor y x0x24 -> x0x22 (w22 = 0x24 stays)
    (0xdd2528, [A.add_reg_lsl(8, 8, 8, 1), A.lsl(8, 8, 3)]), # 7060f1 x0x24 -> x24 (w22 = 0x24 stays)
    (0xdd423c, [A.movz(8, 0xd), A.str_(8, 0, 0)]),           # 6c62a2 y 0x11 -> 0xd (w20 = 0x11 is reused)
]
_c['native_both'] = [0x41b099, 0x41ab67]          # button tests run for real so the input paths are reachable
_c['argsets'] = [[0, 0, 0, 0], [2, 0, 0, 0]]
_WIN = {0xdca4a0: 0xdd0000, 0xdd0010: 0x00230017, 0xdd0018: 0x00410031, 0xdd001c: 0x00500060}
_BASE = {**_WIN, 0xdc130c: 0, 0xdc0230: 0xff020100, 0x9a85e4: 0, 0xdc1294: 0}
_c['pokes'] = [{**_BASE, 0xdca6a0: m, 0xdca7ec: n, 0xdca628: f, 0xdca5c4: s, 0xdca61c: sc,
                0xdca60c: sc, 0xdca5fc: r, 0xdca4a4: ch, 0x9a85d4: 0}
               for m in (0, 1, 2) for s in (0, 1, 2)
               for n, f, sc, r, ch in ((3, 0, 0, 0, 0), (8, 1, 1, 2, 1), (10, 0, 2, 1, 2), (12, 1, 0, 3, 0), (14, 0, 3, 5, 1))]
# input: R1/L1-free, 'select' (0x20) pressed -> 7071cd's row count
_c['pokes'] += [{**_BASE, 0xdca6a0: 0, 0xdca7ec: n, 0xdca5c4: s, 0x9a85d4: 0x20, 0xdc0b4b: 0, 0xdca4a4: 0, 0xdc1294: 0}
                for n in (3, 8, 9, 12, 13) for s in (0, 1, 2)]
