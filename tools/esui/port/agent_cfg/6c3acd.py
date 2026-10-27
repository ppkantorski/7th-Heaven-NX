"""Config sliders/cursor (6c3acd). The automatic align maps 6c3f50 onto an address add (ca8798)
and misses 6c3ef3, so every edit is given by hand. The cursor x is c8 * 0x66 + win.x + 0xd8
(two varying inputs: not fittable) -> c8 * 0x5c + win.x + 0xe4.

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
FNS.setdefault(0x6c3acd, {})
FNS[0x6c3acd].update(
    no_align=True,
    struct_x86=ALLCH(0x6c3acd),
    arm_struct={
        0xca76c0: A.movz(9, 0x5c), 0xca76f0: A.add_imm(20, 8, 0xe4),     # 6c3b5e / 6c3b6a
        0xca83bc: A.movz(9, 0x5c), 0xca83ec: A.add_imm(20, 8, 0xe4),     # 6c3bac / 6c3bb8
        0xca7750: A.add_imm(21, 8, 0x111),                               # 6c3bd1
        0xca7974: A.add_imm(21, 8, 0x34),                                # 6c3c3a
        0xca7c84: A.add_imm(28, 8, 0x18), 0xca7eb4: A.add_imm(28, 8, 0x18), 0xca8100: A.add_imm(28, 8, 0x18),
        0xca7cfc: A.add_imm(21, 8, 0x158), 0xca7f2c: A.add_imm(21, 8, 0x158), 0xca8178: A.add_imm(21, 8, 0x158),
        0xca79f4: A.movz(8, 0x158),                                      # 6c3e4b
        0xca8880: A.add_imm(21, 8, 0x4a),                                # 6c3e9e
        0xca88ac: A.movz(8, 0xe7),                                       # 6c3ea3
        0xca88ec: A.movz(8, 0x16a),                                      # 6c3eb2
        0xca8948: A.add_imm(21, 8, 0x106),                               # 6c3ece
        0xca8a00: A.movz(8, 0x2a),                                       # 6c3ef3
        0xca8b7c: A.add_imm(21, 8, 0xa),                                 # 6c3f50
    },
    argsets=[[0, 0, 0, 0], [2, 0, 0, 0]],
)
_TBL = {0xdc10d8 + 4 * k: 0x00010203 * (k + 1) & 0x0f0f0f0f for k in range(9)}
FNS[0x6c3acd]['pokes'] = [
    {**_TBL, 0xdc130c: 0, 0xdc10fc: 1, 0xdc10cc: cc, 0xdc10c8: c8, 0xdc10d4: d4, 0xdc10ec: (c8 + d4 + i) % 4,
     0xdc10c0: 0xdd0000, 0xdd0008: xy}
    for cc in (0, 1) for c8 in (0, 1, 2) for d4 in (0, 2) for i, xy in enumerate((0x00300010, 0x00720045))]
