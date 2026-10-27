"""Config window (6c3197). The port draws this window its own way (rows from the 2nd option on
are 0x28 higher, per-language widths via bl 0x10e2720 / 0x10fad60, English = the 'eq'/index-0
path), so the x86<->ARM call sequences never align and the arg fitter cannot carry ESUI's
changes: every one is an ARM edit on the port's own constant (port value + ESUI's delta).
ESUI caves: 0x91399F (push 0x91A888 ; edx = [ebp-0x10] ; back to 'add edx, 2') and 0x9139B4
(dx = (dx * 0x9e >> 8) + 0x137 in place of add edx, 0x126: the slider knob position).

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
FNS.setdefault(0x6c3197, {})
_V9E = lambda: [A.movz(9, 0x9e), A.mul(19, 8, 9), A.lsr(19, 19, 8), A.add_imm(19, 19, 0x137)]
FNS[0x6c3197].update(
    no_align=True,
    native_extra=[(0x91399f, 0x9139ac), (0x9139b4, 0x9139c2)],
    struct_x86=ALLCH(0x6c3197),
    replace=[(0xcbb094, _V9E()), (0xcbb160, _V9E()), (0xcbb224, _V9E()),
             (0xcba6f4, [A.ldr(24, 0, 0), A.add_imm(24, 24, 2)])],   # 6c325f cave: y = [ebp-0x10] + 2
    arm_struct={
        0xcba560: A.sub_imm(8, 8, 0x26),      # 6c31ff/6c3202 (i-1)*0x28 -> i*0x28 - 0x26
        0xcba61c: A.sub_imm(8, 8, 0x4e),      # 6c3226/6c3229 (the port's -0x50)
        0xcba7fc: A.add_imm(24, 8, 0x2a),     # 6c32a8 +0x28 -> +0x2a
        0xcba690: A.movz(28, 0x58),           # 6c32af/6c32fd/6c334e imul 0xd -> 0xb (x8)
        0xcba694: A.movz(25, 0xb),            #   ... and the eax mirror
        0xcba8fc: A.add_imm(24, 8, 0x2a),     # 6c32f6 +0x50 -> +0x52 (port +0x28)
        0xcbaa10: A.add_imm(24, 8, 0xf2),     # 6c3345 +0x118 -> +0x11a (port +0xf0)
        0xcbab94: A.add_imm(25, 8, 0x52),     # 6c33b3 +0x78 -> +0x7a (port +0x50)
        0xcbad18: A.add_imm(20, 8, 0x52),     # 6c33f7 +0x78 -> +0x7a (port +0x50)
        0xcbad50: A.movz(8, 0x1f8),           # 6c33fb push 0x212 -> 0x1f8 (English)
        0xcbae1c: A.add_imm(19, 8, 0x11a),    # 6c3453 +0x140 -> +0x142 (port +0x118)
        0xcbad84: A.movz(27, 0x48),           # 6c345c imul 0x58 -> 0x48 (English)
        0xcbae60: A.add_imm(9, 9, 0x160),     # 6c345f +0x155 -> +0x160 (English)
        0xcbaf54: A.add_imm(19, 8, 0x11a),    # 6c347f +0x140 -> +0x142 (port +0x118)
        0xcbb02c: A.add_imm(19, 8, 0x11f),    # 6c34b1 +0x144 -> +0x147 (port +0x11c)
        0xcbb058: A.movz(8, 0x125),           # 6c34b8 push 0x130 -> 0x125
        0xcbb364: A.add_imm(19, 8, 2),        # 6c35aa lea +4 -> +2
        0xcbb39c: A.movz(8, 0x108),           # 6c35af push 0xf3 -> 0x108 (language index != 3)
        0xcbb42c: A.add_imm(19, 8, 2),        # 6c35d4 lea +4 -> +2
        0xcbb458: A.movz(8, 0x1eb),           # 6c35d9 push 0x238 -> 0x1eb
        0xcbb484: A.movz(8, 0x136),           # 6c35e6 word [ebp-8] 0x126 -> 0x136
        0xcbb2d4: A.movz(28, 0xab),           # 6c35fe word [ebp-4] 0x10c -> 0xab (w28's only use)
    })
# slider bytes [DC0E10] / [DC0E11] (pokes are dwords: e12 = the option word) and [DC0E24]
FNS[0x6c3197]['pokes'] = [{0xdc0e10: a | (((a * 7) & 0xff) << 8) | (s << 16), 0xdc0e24: 0xff - a}
                          for a, s in ((0, 0), (0x40, 0x0555), (0xff, 0x1aaa), (0x81, 0x2c3f))]
