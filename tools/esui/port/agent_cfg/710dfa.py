"""Magic menu (710DFA): one case per [0x921100] (0..4); [0xDC130C] != 1; [0xDD1690] = window pointer

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
FNS.setdefault(0x710dfa, {})
_c = FNS[0x710dfa]
def _win(v):      # window array at 0xdd0000: halfwords x/y/w/h for 6 windows, varied per poke
    return {0xdd0000 + 4 * k: ((v + 7 * k) & 0xff) | (((v * 3 + 5 * k) & 0xff) << 16) for k in range(12)}
_c['pokes'] = [{0xdc130c: 0, 0x921100: m, 0xdd1690: 0xdd0000, **_win(v),
                0xdd169c: r, 0xdd17e8: 0, 0xdd1694: fl,
                0xdd1708: cx, 0xdd170c: cy, 0xdd171c: sc, 0xdd172c: sc2, 0xdd1738: fl,
                0xdd1740: cx2, 0xdd1744: cy, 0xdd1754: sc, 0xdd1764: sc2, 0xdd1770: fl,
                0xdd1778: cx, 0xdd177c: cy, 0xdd178c: sc, 0xdd179c: sc2, 0xdd17a8: fl}
               for m in range(5)
               for v, r, fl, cx, cy, sc, sc2, cx2 in ((0x20, 0, 0, 0, 0, 0, 0, 0), (0x31, 2, 1, 2, 3, 1, 2, 1),
                                                       (0x45, 1, 1, 1, 5, 4, 1, 0), (0x13, 3, 0, 2, 1, 2, 3, 1))]
# the align report maps several of these onto ebp-offset / unrelated words: every edit is by hand
_c['no_align'] = True
_CINC20 = 0x1A800400 | (20 << 16) | (20 << 5) | 20          # cinc w20, w20, ne
# list windows + visible rows. ARM block A = e.skill (x86 712014..), B = summon (711cd9..), C = magic (711641..)
_c['struct_x86'] = XI(0x712014, 0x712034, 0x712046, 0x712050, 0x7120ae, 0x7120b7,
                      0x711ce2, 0x711cf9, 0x711d0c, 0x711d15, 0x711d7d,
                      0x711641, 0x711661, 0x711674, 0x71167d, 0x7116dc, 0x7116e5)
_c['replace'] = [
    # A: w25 = 7 is also a later push: [DD1680] = 0xa from w8; rows 8/7 -> 0xb/0xa; w26 = 0x26a stays live
    (0xe02c98, [A.movz(25, 7), A.movz(8, 0xa)]),
    (0xe02e68, [A.movz(20, 0xa), _CINC20]),
    (0xe02cd4, [A.movz(26, 0x26a), A.movz(8, 0x26f)]),
    # B: w26 = 8 is a later push: [DD1682] = 7 from w8
    (0xe06568, [A.movz(26, 8), A.movz(8, 7)]),
]
_c['arm_struct'] = {
    0xe02c9c: A.strh(8, 0, 0), 0xe02cd8: A.strh(8, 0, 0),
    0xe02cfc: A.add_imm(20, 8, 8), 0xe02d14: A.movz(8, 0xc),
    0xe0656c: A.strh(8, 0, 0), 0xe06594: A.movz(8, 0x26f), 0xe065bc: A.add_imm(20, 8, 7), 0xe065d4: A.movz(8, 0xc),
    0xe06728: A.movz(20, 8),                                   # rows 8/7 -> 8
    0xe0a2d0: A.movz(20, 0xa),                                 # C: [DD1680] and rows (cinc) 7 -> 0xa / 0xb
    0xe0a30c: A.movz(8, 0x26f), 0xe0a334: A.add_imm(21, 8, 8), 0xe0a34c: A.movz(8, 0xc),
}
# BUILD 629: list row pitch 0x24 -> 0x18 (x86 imul 0x24 -> 0x18); the port computes
# x*36 as (x + x<<3) << 2 -> (x + x<<1) << 3
_X36 = [A.add_reg_lsl(8, 8, 8, 1), A.lsl(8, 8, 3)]
import os as _os
_X36_SKIP = {int(x, 16) for x in _os.environ.get('X36_SKIP', '').split(',') if x}
_c['replace'] = _c['replace'] + [(a, _X36) for a in (0xe02558, 0xe03128, 0xe047b8, 0xe05850, 0xe069e4, 0xe09264, 0xe0a8e8) if a not in _X36_SKIP]
_X36_XSKIP = {int(x, 16) for x in _os.environ.get('X36_XSKIP', '').split(',') if x}
_c['struct_x86'] = _c['struct_x86'] + [(a, 3) for a in (0x711180, 0x7111ba, 0x7117d5, 0x71183a, 0x711e1b, 0x711e84, 0x712155) if a not in _X36_XSKIP]
