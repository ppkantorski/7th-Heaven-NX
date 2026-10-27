"""tools/esui/port/fns.py

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
NOP = 0xD503201F
import a64 as A
FNS = {
  0x6dd041: dict(
    struct_x86=[(0x6dd170, 2), (0x6dd182, 2), (0x6dd194, 2), (0x6dd1a6, 2),
                (0x6dd24c, 2), (0x6dd71e, 6), (0x6dd80e, 6),
                (0x6dd2a4, 5),            # status cycle mod 0x1b -> 0x20
                (0x6dd31b, 7),            # 7777 name start x 0xe -> 0x14
                (0x6dd418, 3)],           # name flash bit 3 -> bit 4
    arm_struct={0xd3f1a8: NOP, 0xd3f244: NOP, 0xd3f2e0: NOP, 0xd3f37c: NOP,
                0xd3f7fc: A.b(0xd3f7fc, 0xd3f718), 0xd408ac: NOP, 0xd40d38: NOP,
                0xd3f570: 0x5280041C,     # mov w28, #0x20 (the divisor, only use)
                0xd3f884: 0x1ADC0D09,     # smulh (x/27 magic) -> sdiv w9, w8, w28
                0xd3f888: NOP,            # its sign fix-up
                0xd3fa34: 0x52800288,     # mov w8, #0x14
                0xd3f5a4: 0x53041108},    # ubfx w8, w8, #4, #1
    skip_x86=[(0x6dd048, 7)],
    native=[0x6d0ae8],          # the port inlines it
    poke_extra=[0xdc2068 + k for k in (1, 4, 5, 6, 7, 0x18, 0x1a, 0x1b)],
    pokes=[{0xdc2069: 2, 0xdc3c7c: 0, 0xdc2080: 0},
           {0xdc2069: 2, 0xdc3c7c: 0, 0xdc2080: 0, 0xdba4b0: 0xffff},
           {0xdc2069: 2, 0xdc3c7c: 1, 0xdc2080: 0, 0xdba4b0: 0xffff, 0xdbaaf0: 0x1234},
           {0xdba4b0: 0xffff, 0xdba8f0: 0x8000, 0xdbad30: 0xffff},
           {0xdba4b0: 0x1234, 0xdc2069: 0}],   # HelpBoxes status toggle: writes 0x6e1864, carried in 0x6e1308
  ),
}
FNS[0x6e1308] = dict(
    struct_x86=[(0x6e1412, 6), (0x6e18ac, 6),   # sub-menu switch: always the status case
                (0x6e17fb, 5),                  # status cycle mod 0x1b -> 0x20
                (0x6e1790, 6)],                 # rotation mask 0xf -> 0x1f
    arm_struct={0xd47158: A.b(0xd47158, 0xd47440),
                0xd48050: A.b(0xd48050, 0xd48264), 0xd48284: NOP,
                0xd46df0: 0x52800413,           # mov w19, #0x20
                0xd46ebc: 0x1AD30D09,           # sdiv w9, w8, w19
                0xd46ec0: NOP,
                0xd474a0: 0x7200111F,           # tst w8, #0x1f
                0xd474a4: 0x12001109},          # and w9, w8, #0x1f
    native=[0x6d0ae8],
    poke_extra=[0xdc2088, 0x9ad1d0, 0xdc1f44] + [0xdc2068 + k for k in range(0x20)],
    pokes=[{0xdc2088: 0}, {0xdc2088: 1}, {0xdc2088: 2}, {0xdc2088: 3}],
    x86_dyn=[(0x9ad1d0, 1, 0x6e1864, 2, 1)],     # what cave 0x914B3F writes
    post={0xd47070: [(1, 0x9ad1d0, 1, 0x100)]},   # HelpBoxes status: y +0x100 (hidden) when [9AD1D0]==1
)
# ESUI caves: 'remove main boxes and help box when the reels show'
REEL_NATIVE = [(0x913fe0, 0x913ff8), (0x91444e, 0x91446c)]
FNS[0x6e3135] = dict(            # Tifa reels
    struct_x86=[(0x6e3162, 6)],
    hooks=[(0xd49a88, [('st8', 0x91c343, 2)])],    # [0x914B3D] is never 1 in this exe
    native=[0x6d0ade, 0x6d0ae3] + REEL_NATIVE,
)
FNS[0x6e2170] = dict(            # Cait Sith reels
    struct_x86=[(0x6e2447, 7)],
    hooks=[(0xd48784, [('st8', 0x91c343, 2)])],
    native=[0x6d0ade, 0x6d0ae3] + REEL_NATIVE,
)
FNS[0x6d9ad5] = dict(store_const=[(0xd64ea0, 4)], struct_x86=[(0x6d9b2b, 7)])   # magic rows 3->4 (shared w21)
FNS[0x6da23c] = dict(store_const=[(0xd6705c, 4)], struct_x86=[(0x6da2cc, 10)])  # e.skill rows 3->4 (shared w23)
FNS[0x6d0af9] = dict(struct_x86=[(0x6d0b2d, 4), (0x6d0b45, 5)],
                     arm_struct={0xd20468: NOP, 0xd204b8: NOP})                   # keep defend/change box position
FNS[0x6dac2d] = dict(struct_x86=[(0x6daed2, 3)],
                     arm_struct={0xd6b654: 0x51002108})                            # viewing area y +4: sub w8, w8, #8 before the >>1
# sub-menu visible rows: ESUI 4/3 -> 5/4 (the port: mov wB,#3 ; cinc wD,wB,ne)
ROWS = lambda r: [0x52800080 | r, 0x1A800400 | (r << 16) | (r << 5) | r]   # mov wR,#4 ; cinc wR,wR,ne
FNS[0x6debfe] = dict(struct_x86=[(0x6ded7d, 7), (0x6dec95, 6), (0x6dec9d, 6)],
                     skip_calls=[0xd42e94],                 # Icons/base: old item icons not drawn
                     replace=[(0xd42b5c, ROWS(20))])
FNS[0x6df9ba] = dict(struct_x86=[(0x6dfa73, 6), (0x6dfa7b, 6)], replace=[(0xd43784, ROWS(19))])
FNS[0x6e06c9] = dict(struct_x86=[(0x6e0762, 6), (0x6e076a, 6)], replace=[(0xd44958, ROWS(19))])
FNS[0x6e09bb] = dict(struct_x86=[(0x6e0a55, 6), (0x6e0a5d, 6)], replace=[(0xd44230, ROWS(19))])
import struct as _st, nxmap as _nx
_M = _nx.Main('dump/exefs/main')
def ROW24(fn):
    """every 'lsl wD, wS, #5' in fn's ARM body -> wD = wS*24 (ESUI's
    shl 5 -> imul 0x18 row spacing), as 2-word replace caves"""
    a0, a1 = _M.extent(fn); out = []
    for a in range(a0, a1, 4):
        w = _st.unpack_from('<I', _M.img, a)[0]
        if (w & 0xFFFFFC00) == 0x531B6800:          # UBFM wD, wS, #27, #26 == lsl #5
            d, s = w & 31, (w >> 5) & 31
            out.append((a, [A.add_reg_lsl(d, s, s, 1), A.lsl(d, d, 3)]))
    return out
for _f, _x in ((0x6debfe, [0x6ded72, 0x6ded9d, 0x6dedd8, 0x6deec5]),):
    pass
FNS[0x6debfe]['replace'] = FNS[0x6debfe]['replace'] + ROW24(0x6debfe)
FNS[0x6df9ba]['replace'] = FNS[0x6df9ba]['replace'] + ROW24(0x6df9ba)
FNS[0x6e06c9]['replace'] = FNS[0x6e06c9]['replace'] + ROW24(0x6e06c9)
FNS[0x6e09bb]['replace'] = FNS[0x6e09bb]['replace'] + ROW24(0x6e09bb)
import re as _re, os as _os
def SHL5(fn):
    t = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'align_%x.txt' % fn)).read()
    return [(int(m, 16), 3) for m in _re.findall(r'^([0-9a-f]+): shl \w+, 5 -> imul', t, _re.M)]
for _f in (0x6debfe, 0x6df9ba, 0x6e06c9, 0x6e09bb):
    FNS[_f]['struct_x86'] = FNS[_f]['struct_x86'] + SHL5(_f)
FNS[0x6de3db] = dict(struct_x86=[(0x6de4bd, 3), (0x6de675, 3), (0x6de6bc, 3), (0x6de724, 3), (0x6de7c4, 3),
                                 (0x6de5dd, 3), (0x6de669, 3), (0x6de6af, 3), (0x6de717, 3), (0x6de7b7, 3)],
                     arm_struct={0xd419f0: 0x52800919,      # w25 = 0x48 (column spacing; its only uses are the five muls)
                                 0xd419fc: 0x528002DC})     # w28 = 0x16 (row spacing; ditto)
# 02-Load: 0x7217C5's jump into 00-Spacing's cave only rewrites the width
# table every frame (window.bin carries it here); keep the stock code, the
# lea -0xe -> -0xa after it is an ordinary arg change
FNS[0x7212fb] = dict(skip_x86=[(0x7217c5, 9)])   # jmp + 4 nops over mov edx,[dd6f24] ; imul 0x1a
FNS[0x720775] = dict(struct_x86=[(0x7208a5, 5)],                       # hours: 6F9C44 (zero-padded) -> 6F9739
                     arm_struct={0xe40058: A.bl(0xe40058, 0xce3e20)})
# the hour value comes back from 6CA1EF, which 00-Main already ports (999-hour cap): run it for real on both sides
import ff7nx_esui as _E
FNS[0x720775]['native_both'] = [0x6ca1ef]
FNS[0x720775]['arm_struct'].update({va: new for va, old, new, fn, n in _E.CODE_MAIN if fn == 0x6CA1EF})
# load/save select cursor spacing: the port picks 0x5b (English) / 0x8c (JP) where the x86 has imul 0x5c; ESUI 0x60
FNS[0x7212fb]['struct_x86'] = FNS[0x7212fb].get('struct_x86', []) + [(0x7213a3, 3)]
FNS[0x7212fb]['arm_struct'] = {0xe44734: A.movz(9, 0x60)}
FNS[0x7212fb]['pokes'] = [{0xdd7704: k, 0xdd74e0: 0, 0xdc130c: 0} for k in range(8)] + \
                         [{0xdd7704: k, 0xdd74e0: 1, 0xdc130c: 0, 0xdd6f24: j} for k in range(8) for j in (0, 1, 2)]
FNS[0x7212fb]['pokes'] += [{0xdd7704: k, 0xdd74e0: 0, 0xdc130c: 0, 0xdd6d9c: 0, 0xdd6d98: 0, 0xdd75e8: 0x01010101,
                            0xdd6dd4: j, 0xdd6e00: e} for k in range(8) for j in (0, 1) for e in (0, 1)]
FNS[0x6fedb0] = dict(pokes=[{0xdca028: 7, 0xdc6b1c: j} for j in (0, 1, 2)])
# 02-Shop item list: align maps 71e750 (loop add) onto the 71e793 push word e36008,
# whose shared tail call is fitted -27 anyway -> double shift. Fit everything as args.
FNS[0x71e415] = dict(no_align=True)
_SHOPP = {0xdd4714: 0xdd0000, 0xdd0000: 0x00000302, 0xdd0004: 0x1234, 0xdd0018: 0x05040000, 0xdd001c: 0x0201ff00}
FNS[0x71e415]['pokes'] = [{**_SHOPP, 0xc068b4: k} for k in range(1, 21)]
FNS[0x71e415]['argsets'] = [[0x10, 0, 0, 0], [0xffffff05, 0, 0, 0], [0x1234530, 0, 0, 0]]
FNS[0x71e415]['argsets'] = [[0, 0, 0, 0], [1, 2, 3, 0], [2, 0, 1, 1], [0x10, 0, 0, 0], [0xffffff05, 0, 0, 0], [0x1234530, 0, 0, 0], [0x30, 0, 0, 0], [0x80, 0, 0, 0]]
FNS[0x71e415]['argsets'] += [[3, 0, 0, 0], [4, 0, 0, 0], [0xc, 0, 0, 0], [7, 0, 0, 0]]
FNS[0x71fa7d] = dict(pokes=[{0xdd0100: 0x07030501, 0xdd0104: 0x00060302}],
                     argsets=[[0x20, 0x30, 0xdd0100, 0], [0, 0, 0, 0], [0x40, 0x10, 0xdd0100, 1]])
# 02-Shop main window. ESUI's caves 913a68..913a91 only rewrite pushed args ([ebp-0x30] +4/-1): ordinary arg changes
FNS[0x71aaa3] = dict(struct_x86=[(0x71c16c, 5)], skip_calls=[0xe2de34])
FNS[0x71aaa3]['pokes'] = [{0x92565c: s, 0xdc130c: 0, 0xdd4734: 1, 0xdd4724: k, 0xdd6bb0: b, 0xdd6b94: 0, 0xdd6ba4: 0}
                          for s in range(7) for k in (0, 3) for b in (0, 1)]
FNS[0x71aaa3]['pokes'] = [{0x92565c: s, 0xdc130c: 0, 0xdd4734: 1, 0xdd4724: k, 0xdd6bb0: b, 0xdd6b94: sc, 0xdd6ba4: 0}
                          for s in range(7) for k in (0, 6, 14, 2, 15) for b in (0, 1) for sc in ((0, 3) if k == 15 else (0,))]
# visible item rows 5 -> 7 (the port's jle: cmp #5 ; OF via (4 - a) & a, so 4 -> 6)
_C7, _M6 = A.cmp_imm(8, 7), A.movz(10, 6)
FNS[0x71aaa3]['struct_x86'] += [(0x71b7ea, 3), (0x71b9b5, 3), (0x71b9cd, 3), (0x71b9d2, 7), (0x71b9f4, 9),
                                (0x71d9ad, 3), (0x71d9b2, 10)]
FNS[0x71aaa3]['arm_struct'] = {0xe31160: A.add_imm(21, 8, 7),
                               0xe319b0: _C7, 0xe319bc: _M6, 0xe319e4: _C7,
                               0xe31a10: _C7, 0xe31a1c: _M6, 0xe31a48: _C7, 0xe31a34: A.movz(19, 7),
                               0xe33994: _C7, 0xe339a0: _M6, 0xe339c8: _C7, 0xe339d0: A.movz(20, 7)}
FNS[0x71aaa3]['native'] = [(0x913a68, 0x913a96)]
# sell / materia lists: rows 10 -> 11 (71ae69) and 10 -> 15 (71bf8f)
FNS[0x71aaa3]['struct_x86'] += [(0x71ae69, 3), (0x71bf8f, 3)]
FNS[0x71aaa3]['arm_struct'].update({0xe27264: A.add_imm(23, 8, 0xb), 0xe2d620: A.add_imm(20, 8, 0xf)})
FNS[0x71aaa3]['pokes'] = [{0x92565c: s, 0xdc130c: 0, 0xdd4734: 1, 0xdd4724: k, 0xdd6bb0: b, 0xdd6be8: b, 0xdd6c20: b,
                           0xdd6b94: sc, 0xdd6ba4: 0}
                          for s in range(7) for k in (0, 6, 14, 2, 15) for b in (0, 1) for sc in ((0, 3) if k == 15 else (0,))]
import capstone as _cs
_X86 = open('dump/romfs/ff7/resources/ff7_1.02/ff7_en', 'rb').read()
import exe_patch as _ep
_PE = _ep.parse_pe(_X86)
def XI(*vas):
    md = _cs.Cs(_cs.CS_ARCH_X86, _cs.CS_MODE_32)
    return [(a, next(md.disasm(_X86[_ep.va_to_offset(_PE, a):_ep.va_to_offset(_PE, a) + 16], a)).size) for a in vas]
# materia list rows: y = i*0x24 + scroll*9 + k -> i*0x18 + scroll*9 + k' (the scroll term defeats an arg fit)
_R24 = lambda a: {a: A.add_reg_lsl(8, 8, 8, 1), a + 4: A.lsl(8, 8, 3)}
FNS[0x71aaa3]['struct_x86'] += XI(0x71c058, 0x71c064, 0x71c185, 0x71c191, 0x71c1c2, 0x71c1ce)
for _a, _k in ((0xe2d9cc, 0xe2d9ec), (0xe2dea4, 0xe2dec4), (0xe2dfb8, 0xe2dfd8)):
    FNS[0x71aaa3]['arm_struct'].update(_R24(_a))
FNS[0x71aaa3]['arm_struct'].update({0xe2d9ec: A.add_imm(22, 9, 0x69), 0xe2dec4: A.add_imm(22, 9, 0x70), 0xe2dfd8: A.add_imm(22, 9, 0x6f)})
# sell lists: row pitch imul 0x1a -> 0x18 (the port: mul w8, w8, w19 with w19 = 0x1a)
_X24 = [A.add_reg_lsl(8, 8, 8, 1), A.lsl(8, 8, 3)]
FNS[0x71aaa3]['struct_x86'] += XI(0x71ad70, 0x71aec6, 0x71afa7, 0x71aed1, 0x71afb3)
FNS[0x71aaa3]['replace'] = [(0xe26e58, _X24), (0xe2740c, _X24), (0xe29280, _X24)]
FNS[0x71aaa3]['arm_struct'].update({0xe2742c: A.add_imm(27, 9, 0xca), 0xe292a0: A.add_imm(27, 9, 0xcb)})
# scroll offsets and both window tables vary too (a fit on one value would scale them)
FNS[0x71aaa3]['pokes'] = [{0x92565c: s, 0xdc130c: 0, 0xdd4734: 1, 0xdd4724: k, 0xdd6bb0: b, 0xdd6be8: b, 0xdd6c20: b,
                           0xdd6b94: sc, 0xdd6ba4: sc2, 0xdd6bdc: sc2, 0xdd6c14: sc2, 0xdd6bcc: sc, 0xdd4710: win}
                          for s in range(7) for k in (0, 6, 15) for b in (0, 1) for sc, sc2 in ((0, 0), (3, 2), (1, 5))
                          for win in (0x925660, 0x9256d8)]
# buy list rows: y = win.y + scroll*9 + i*0x24 + 0x11 -> i*0x18 + 0x13
FNS[0x71aaa3]['struct_x86'] += XI(0x71b846, 0x71b849)
FNS[0x71aaa3]['arm_struct'].update(_R24(0xe312f0))
FNS[0x71aaa3]['arm_struct'][0xe312fc] = A.add_imm(20, 9, 0x13)
FNS[0x71aaa3]['pokes'] = [{0x92565c: s, 0xdc130c: 0, 0xdd4734: 1, 0xdd4724: k, 0xdd6bb0: b, 0xdd6be8: b, 0xdd6c20: b,
                           0xdd6b94: sc, 0xdd6ba4: sc2, 0xdd6bdc: sc2, 0xdd6c14: sc2, 0xdd6bcc: sc, 0xdd4710: win}
                          for s in range(7) for k in (0, 2, 6, 14, 15) for b in (0, 1) for sc, sc2 in ((0, 0), (3, 2), (1, 5))
                          for win in (0x925660, 0x9256d8)]
# ---------------------------------------------------------------- battle
# damage / recovery digits (03-Battle): step 9 -> 7 per digit, first offset 8 -> 6 (x [9ad1a8])
FNS[0x5bb756] = dict(struct_x86=XI(0x5bb991, 0x5bb9b8),
                     replace=[(0x7c65b0, [A.add_reg_lsl(8, 8, 8, 1), A.lsl(8, 8, 1)]),      # x6
                              (0x7c6650, [A.lsl(9, 8, 3), 0x4B080128])])                      # x7: sub w8, w9, w8
FNS[0x5bb756]['argsets'] = [[0, 0, a, 0, f, 0] for a in (-3, -2, -1, 5, 123, 9999) for f in (0, 4)]
FNS[0x5bcd42] = dict(pokes=[{0xbf2df4: 0}, {0xbf2df4: 3}])
# battle text rise: ESUI reads the y step from [ecx+0xBFF3A8] instead of +0xBFC3A8 (0x3000 further)
FNS[0x5bcd42].update(struct_x86=XI(0x5bce17), replace=[(0x7cb538, [0x11400D00, A.add_imm(0, 0, 4)])])  # add w0, w8, #3, lsl #12 ; +4
# battle text boxes (BattleViewOptimised): spacing x [9ad1a8] 0x32->0x64, 0x21->0x42/0x12/0, esp quirk
FNS[0x5bd050] = dict(struct_x86=XI(0x5bd094, 0x5bd0d5, 0x5bd0f3, 0x5bd10e, 0x5bd12a, 0x5bd14e),
                     arm_struct={0x7cbcc4: A.movz(9, 0x64), 0x7cbe5c: A.movz(9, 0x64),
                                 0x7cbedc: A.add_imm(8, 8, 0x18),
                                 0x7cbfc4: 0x2A1F03F4},                          # mov w20, wzr
                     replace=[(0x7cbde4, [A.add_reg_lsl(20, 8, 8, 5), A.lsl(20, 20, 1)]),   # x66
                              (0x7cbf28, [A.add_reg_lsl(8, 8, 8, 3), A.lsl(8, 8, 1)])],     # x18
                     pokes=[{0x7c0b24: 1}, {0x7c0b24: 1, 0x9aad68: 5}, {0x7c0b24: 1, 0x9aad68: 0}])
# battle text start (Transparency2): cave 0x9144B3 stores 0 not 0x15, sets [91C343]=1 and [DC3CD8]=1
FNS[0x5bcf9d] = dict(struct_x86=XI(0x5bcfd0), native=[(0x9144b3, 0x9144ce)],
                     arm_struct={0x7cba94: A.movz(8, 0)},
                     hooks=[(0x7cba98, [('st8', 0x91c343, 1), ('st32', 0xdc3cd8, 1)])],
                     argsets=[[0, 0, 0, 0], [1, 0, 0, 0], [2, 0, 0, 0]],
                     pokes=[{0xbf2df4: 0}, {0xbf2df4: 2}])
# battle viewport, mode 2 (BattleViewOptimised): full height, no top offset. The emulator faults in
# this function before the block on both builds; the two words are the block's only constants.
FNS[0x41b300] = dict(struct_x86=XI(0x41b4e2, 0x41b514), arm_struct={0x8d3cc: A.movz(26, 0x1e0), 0x8d3dc: A.movz(20, 0)},
                     unverifiable='emulator faults at 0 before the mode switch (both stock and edited)')
# Action/help text (03-Battle): a void function; eax at return is whatever the stubs left
FNS[0x6e4b3c] = dict(ignore_mem=[-1])
# limit gauge / time bar owner (BattleViewOptimised): cave 0x91446D also sets [91C343] = 1
FNS[0x6daef0] = dict(struct_x86=XI(0x6daf0c), native=[(0x91446d, 0x91447a)],
                     hooks=[(0xd6b79c, [('st8', 0x91c343, 1)])])
# battle cursor (03-Battle / BattleView*): one case per menu, [0x91EF9C] picks it
FNS[0x6cf5c5] = dict(pokes=[{0x91ef9c: c, 0xdc3c7c: p, 0xdc3630: 0xdd0000} for c in range(0x1d) for p in (0, 2)])
_MENUS2 = {a: 0x02020202 for a in range(0xdc2068, 0xdc2090, 4)}
FNS[0x6cf5c5]['pokes'] = [{**_MENUS2, 0x91ef9c: c, 0xdc3c7c: p, 0xdc3630: 0xdd0000, 0xdc3868: z}
                          for c in range(0x1d) for p in (0, 2) for z in (0, 1)]
FNS[0x6cf5c5]['native'] = [(0x9144d7, 0x9144f0), (0x914529, 0x914565)]
def ALLCH(fn, skip=()):
    """every x86 instruction ESUI changes in fn (the port edits them all by hand)"""
    md = _cs.Cs(_cs.CS_ARCH_X86, _cs.CS_MODE_32)
    import esui_port as _P
    a, b = _P.M.containing(fn)
    out = []
    for i in md.disasm(_X86[_ep.va_to_offset(_PE, a):_ep.va_to_offset(_PE, b)], a):
        o = _ep.va_to_offset(_PE, i.address)
        if _P.NEW_FULL[o:o + i.size] != _X86[o:o + i.size] and not any(s <= i.address < e for s, e in skip):
            out.append((i.address, i.size))
    return out
_SDIV = lambda d, n, m: 0x1AC00C00 | (m << 16) | (n << 5) | d
_X24_8 = [A.add_reg_lsl(8, 8, 8, 1), A.lsl(8, 8, 3)]
FNS[0x6cf5c5].update(
    struct_x86=ALLCH(0x6cf5c5),
    arm_struct={0xd1b034: A.sub_imm(22, 8, 0x1a), 0xd1b094: A.movz(9, 0x18), 0xd1b0a8: A.add_imm(20, 8, 0x19),
                0xd1b140: A.sub_imm(22, 8, 0x1e), 0xd1b23c: A.movz(9, 0x60), 0xd1b320: A.sub_imm(22, 8, 0x19),
                0xd1b400: A.movz(9, 0x48), 0xd1b45c: A.sub_imm(24, 8, 0x14),
                0xd1b4c4: NOP, 0xd1b4d0: A.add_imm(20, 8, 0xc),
                0xd1b51c: A.movz(8, 0xfffa), 0xd1b55c: A.add_imm(20, 8, 0x17e),
                0xd1b5ec: NOP, 0xd1b630: A.add_imm(20, 8, 0x17e),
                0xd1b680: A.sub_imm(23, 8, 0x1a), 0xd1b6c8: A.add_imm(20, 8, 9),
                0xd1b714: A.movz(8, 0xfff8), 0xd1b754: A.add_imm(20, 8, 0x17e),
                0xd1d2b8: A.add_imm(21, 8, 5), 0xd1d2ec: A.add_imm(20, 8, 0x3c)},
    replace=[(0xd1b360, _X24_8), (0xd1b558, _X24_8), (0xd1b62c, _X24_8), (0xd1b750, _X24_8),
             (0xd1b4c0, [A.lsl(10, 8, 4), A.add_reg_lsl(10, 10, 8, 2), A.add_reg_lsl(8, 10, 8, 1)]),   # x22
             (0xd1b5dc, [A.movz(9, 0x90), A.mul(23, 8, 9), A.cmp_imm(8, 0)]),                        # x = v * 0x90
             (0xd1d2b4, [A.movz(10, 0x90), _SDIV(8, 8, 10), A.movz(10, 0x74), A.mul(8, 8, 10), A.add_reg(8, 8, 9)]),
             # the [0xBE2] case shares case 0x98's y tail on the port; ESUI changes them differently
             (0xd1b198, [('T',), A.ldr(8, 0, 0), A.ldr(9, 25, 0), A.add_reg_lsl(8, 8, 8, 1), A.lsl(8, 8, 3),
                         A.str_(8, 25, 8), A.add_reg(8, 9, 8), A.add_imm(20, 8, 0x25), A.str_(20, 25, 0),
                         ('b', 0xd1b88c)])],
    store_const=[(0xd1aa08, 0x43f00000), (0xd1ac28, 0x43f00000)],
    hooks=[(0xd1ac2c, [('st8', 0x91c343, 2), ('st32', 0xdc3cd8, 0)]),     # cave 0x9144D7's flags
           (0xd1ae44, [('cursor_tables',)])])                            # cave 0x914529
# case 6cf987: x = v * 0x90 - 6 ; case 6cfa8d: the port picks the multiplier per language (English: x0x90) -> x0x74
FNS[0x6cf5c5]['replace'] = [r for r in FNS[0x6cf5c5]['replace'] if r[0] not in (0xd1b5dc, 0xd1d2b4)] + [
    (0xd1b5dc, [A.movz(9, 0x90), A.mul(23, 8, 9), A.sub_imm(23, 23, 6), A.cmp_imm(8, 0)]),
    (0xd1af6c, [A.movz(9, 0x74), A.mul(8, 8, 9)])]
FNS[0x6cf5c5]['arm_struct'][0xd1af70] = NOP
# ESUI cave ranges run natively on top of the callees the port inlines
for _f in (0x6cf5c5, 0x5bcf9d, 0x6daef0, 0x71aaa3):
    FNS[_f]['native_extra'] = FNS[_f].pop('native')
# Help box (HelpBoxesNone*): ESUI rewrites 6D21A2..6D21D7 -- width +0x1a, x 13, y 9 unless the
# message is 0x17 (then width as is, x 0, y -168); writes the width to [91D21C] and the centred x
# ((0x280 - w) >> 1) to [ebp-0x174]. On the port it is one block, d28fac..d2904c; w11-w15 are free.
def _helpbox():
    T = ('T',)
    ebp = lambda off, sub=True: [A.ldr(8, 25, 0x14), (A.sub_imm(0, 8, off) if sub else A.add_imm(0, 8, off)), T]
    w = []
    w += ebp(0xc, sub=False) + [A.ldrb(11, 0, 0)]
    w += ebp(0x158) + [A.ldrh(12, 0, 0)]
    w += [A.movz(13, 0), A.movz(14, 0xff58), A.cmp_imm(11, 0x17)]
    w += [None]                                                     # b.eq skip (patched below)
    k = len(w) - 1
    w += [A.add_imm(12, 12, 0x1a), A.movz(13, 0xd), A.movz(14, 9)]
    skip = len(w)
    w += [A.movz(0, 0xd21c), A.movk_hi(0, 0x91), T, A.strh(12, 0, 0)]
    w += ebp(0x158) + [A.strh(13, 0, 0)]
    w += ebp(0x168) + [A.strh(14, 0, 0)]
    w += [A.movz(15, 0x280), A.sub_reg(15, 15, 12), A.and_mask(15, 15, 16), A.lsr(15, 15, 1)]
    w += ebp(0x174) + [A.strh(15, 0, 0), ('b', 0xd29050)]
    w[k] = ('bcond', skip - k, 0)                                   # relative, eq
    return w
FNS[0x6d1cc0] = dict(struct_x86=ALLCH(0x6d1cc0), native_extra=[(0x9139cb, 0x9139dd)],
                     replace=[(0xd28fac, _helpbox())])
# statuses like Resist shown (03-Battle): the port loads the status word as two halves masked to
# 28 bits (bfi #16, #12); ESUI keeps all 32 at 41C4C0 (the plain copy loop) and 41C5B9 (the
# one after it); 41C28F stays masked (port 0x92178)
FNS[0x41c0bb] = dict(struct_x86=XI(0x41c4c0, 0x41c5b9),
                     arm_struct={0x92dbc: A.bfi(8, 9, 16, 16), 0x931ac: A.bfi(8, 9, 16, 16)},
                     pokes=[{0x9aad64: s} for s in range(0x10)])
# command box text x (BattleViewThree): ESUI never takes the 'sub-menu open -> 0x14' branch
FNS[0x6d7dfa] = dict(struct_x86=ALLCH(0x6d7dfa),
                     arm_struct={0xd5cd80: NOP, 0xd5cda8: NOP, 0xd5cdd0: NOP, 0xd5cdf8: A.b(0xd5cdf8, 0xd5ce04)},
                     pokes=[{0xdc206c: v} for v in (0x02020202, 0x02000000, 0x00020000, 0x00000200, 0x00000002, 0)])
FNS[0x6d7dfa]['native_both'] = [0x41ab67]     # the 'is a key down' test runs for real, so the flag path is reachable
FNS[0x6d7dfa]['argsets'] = [[0, 0, 0, 0], [5, 0, 0, 0]]
# sub-menu dispatcher (03-Battle / Icons): case args; ESUI nops the 6E1308 call (status help)
FNS[0x6d797c] = dict(struct_x86=XI(0x6d7b29), skip_calls=[0xd3e7f0],
                     argsets=[[c, i, 0, 0] for c in range(0x1d) for i in (0, 1)])
# limit / skill grid: a character with two rows of entries, one of type 0x14 (the reels' text)
_LB = 0xdba498
FNS[0x6de3db]['pokes'] = [{0xdc3c7c: 0, _LB + 0x20: 0x00000200, _LB + 0x4c: t0 | 0x02000000, _LB + 0x50: (t1 << 16),
                           _LB + 0x58: 0x00000014, 0xdc1f44: s} for t0 in (0x14, 0x03) for t1 in (0x14, 0x05) for s in (0, 6)]
FNS[0x6de3db]['pokes'] = [{0xdc3c7c: 0, _LB + 0x20: 0x00000200, _LB + 0x4c: t0 | (fl << 24), _LB + 0x50: (t1 << 16),
                           _LB + 0x54: 0x12000000 | 7, _LB + 0x58: 0x00000014, 0xdc1f44: s}
                          for t0 in (0x14, 0x07, 0x03) for t1 in (0x07, 0x05) for s in (0, 0x40, 0x46) for fl in (0x12, 0)]
FNS[0x6d797c]['argsets'] = [[i, c, 0, 0] for c in range(0x1d) for i in (0, 1)]
# slots / tifa reels / summon screens: one case per [0xDC209C]
FNS[0x6e384f] = dict(pokes=[{0xdc209c: c, 0xdc3860: k, 0xdc1f44: s} for c in range(6) for k in (0, 2) for s in (0, 5)])
# reel title (case 3): ESUI's 0x9139DD centres it, x = (0x280 - width(name)) >> 1, as case 5 already is;
# on the port both cases share one call, so the centring goes on case 3's own x push
FNS[0x6e384f].update(native_extra=[(0x9139dd, 0x9139f5)], store_tr={0xd4b174: ('centre', 0x280, 2, 0x6f54a2)})
# case 3's runs differ in call sequence (ESUI's centring call), so its pushes are given, not fitted
FNS[0x6e384f]['store_tr'].update({0xd4b018: ('add', -0xf), 0xd4b000: ('add', 0x10), 0xd4b15c: ('add', 0x10)})
FNS[0x6e384f]['native_both'] = [0x6f54a2]
# with the measure native the case-3 runs sample like the rest: the fits carry those pushes
for _k in (0xd4b018, 0xd4b000, 0xd4b15c):
    FNS[0x6e384f]['store_tr'].pop(_k)
# W-item/summon/enemy-skill row (x, y, mode): every mode, entries both set and empty
FNS.setdefault(0x6dfc65, {})
FNS[0x6dfc65]['argsets'] = [[x, y, m, 0] for m in (0, 1, 2) for x, y in ((16, 300), (40, 340))]
FNS[0x6dfc65]['pokes'] = [{0xdc3c7c: 0, 0xdc2180: a, 0xdc2184: 0, 0xdc2194: 0,
                           0xdba7e0: e0, 0xdba7e4: e1} for a in (0, 1)
                          for e0, e1 in ((0x00050003, 0x00000001), (0x00ff0003, 0x00000000), (0x00000007, 0x00000000))]
_IDX0 = {a: 0 for a in (0xdc2110, 0xdc2114, 0xdc2124, 0xdc2180, 0xdc2184, 0xdc2194, 0xdc2148, 0xdc214c, 0xdc215c)}
FNS[0x6dfc65]['pokes'] = [{0xdc3c7c: 0, **_IDX0, **{b: e0 for b in range(0xdba5a0, 0xdba900, 8)},
                           **{b + 4: e1 for b in range(0xdba5a0, 0xdba900, 8)}}
                          for e0 in (0x00050003, 0x00000003, 0x000000ff) for e1 in (0, 1)]
FNS[0x6d797c]['argsets'] = [[i, c, 0, 0] for c in (0, 3, 9, 11, 20, 1) for i in (0, 1)]
# the port shares one text call between cases 2 and 3: edit each case's own adds
FNS[0x6d797c].update(no_align=True, struct_x86=ALLCH(0x6d797c),
                     arm_struct={0xd3e704: A.add_imm(22, 8, 6), 0xd3e9b8: A.add_imm(22, 8, 6), 0xd3ea54: A.add_imm(19, 8, 0xe)})
FNS[0x6d797c]['argsets'] = [[i, c, 0, 0] for c in (0, 2, 3, 9, 11, 20) for i in (0, 1)]
# limit / barrier gauge quads (BattleViewOptimised): slow to emulate; the party pokes are enough
FNS.setdefault(0x6df40d, {}).update(no_random=True, argsets=[[0, 0, 0, 0], [1, 2, 0, 0]])
FNS[0x71aaa3]['pokes'] = FNS[0x71aaa3]['pokes'] + [{0x92565c: s, 0xdc130c: 0, 0xdd4734: 1, 0xdd4724: 6, 0xdd4710: 0x925660, 0xdd6b48: k}
                                                   for s in (0, 3) for k in (0, 1, 2)]
# Buy/Sell/Exit cursor: x = win.x + sel * 0x2b - 0x1a (stock 0x32 / -0x20); the port picks the
# stride per language (English: e2e12c); the -0x20 is shared
FNS[0x71aaa3]['struct_x86'] += XI(0x71abe9, 0x71abec)
FNS[0x71aaa3]['arm_struct'].update({0xe2e12c: A.movz(9, 0x2b), 0xe32134: A.sub_imm(20, 8, 0x1a)})
# BUILD 627 tweak (Patrick's photos): the save-file grid's text advances by width('Save N') + 0x14,
# 94 units with ESUI's widths; the cursor's step follows it (ESUI's 0x60 drifted 2 units a column)
FNS[0x7212fb]['arm_struct'] = {0xe44734: A.movz(9, 0x5e)}
FNS[0x6d797c]['argsets'] = [[i, c, 0, 0] for c in (0, 2, 3, 9, 11, 20, 21) for i in (0, 1)]
# (the save-grid cursor step tweak lives in ff7nx_esui.TWEAKS['load']: an arm_struct here is re-fitted back to ESUI)
FNS[0x7212fb]['arm_struct'] = {0xe44734: A.movz(9, 0x60)}
# HP/MP bar rows (03-Battle): y = row * 0x18 + 0x19d (stock row << 5); the y goes into a struct on
# the stack, so only the stack-tracking verify sees it
FNS.setdefault(0x6dc976, {}).update(struct_x86=XI(0x6dc98d, 0x6dca11),
                                    replace=[(0xd727b4, [A.add_reg_lsl(8, 8, 8, 1), A.lsl(8, 8, 3)])])
FNS[0x6dc976]['replace'].append((0xd72990, [A.add_reg_lsl(8, 8, 8, 1), A.lsl(8, 8, 3)]))
# BUILD 628 (Patrick's photos): column steps the arg fits never saw vary
FNS[0x6df9ba].setdefault('arm_struct', {})[0xd437cc] = A.movz(19, 0x60)     # magic columns 0x9c -> 0x60
FNS[0x6e09bb].setdefault('arm_struct', {})[0xd44280] = A.movz(25, 0x24)     # e.skill columns base 0x30 -> 0x24
FNS[0x6e09bb]['arm_struct'].pop(0xd44280)      # the fit carries the base once text calls are compared
FNS.setdefault(0x6e1f64, {})
FNS[0x6e1f64].update(struct_x86=FNS[0x6e1f64].get('struct_x86', []) + XI(0x6e200e),
                     replace=FNS[0x6e1f64].get('replace', []) + [(0xd48608, [A.add_reg_lsl(8, 8, 8, 1), A.lsl(8, 8, 3)])])
# per-function configs written by parallel workers (one file per function)
import glob as _glob
for _p in sorted(_glob.glob(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'agent_cfg', '*.py'))):
    try:
        exec(open(_p).read())
    except Exception as _e:                      # one worker's broken file must not stop the others
        import sys as _sys
        print('fns: skipped %s: %r' % (_p, _e), file=_sys.stderr)

# ---------------------------------------------------------------- BUILD 629 option variants
FNS[0x6dd041]['gate'] = {0xd408ac: (0x6dd71e, 6), 0xd40d38: (0x6dd80e, 6)}      # Transparency1/2 nops
FNS[0x6e1308]['gate'] = {0xd47158: (0x6e1412, 6), 0xd48050: (0x6e18ac, 6), 0xd48284: (0x6e18ac, 6)}   # icon sets: status case
# 6E1308's non-status rows (reached without an icon set, ESUI's default): every
# row is shl 5 -> imul 0x18 in EnhancedStock itself (all variants)
FNS[0x6e1308]['replace'] = FNS[0x6e1308].get('replace', []) + ROW24(0x6e1308)
FNS[0x6e1308]['struct_x86'] = FNS[0x6e1308]['struct_x86'] + [(a, 3) for a in (
    0x6e139c, 0x6e1487, 0x6e14bc, 0x6e14ed, 0x6e1530, 0x6e1551, 0x6e1626, 0x6e165b,
    0x6e168c, 0x6e16cf, 0x6e16f0, 0x6e185e)]
# the help box rewrite belongs to HelpBoxesNone*; with Stock help boxes (hbb=0) 6D1CC0 is stock
FNS[0x6d1cc0].setdefault('gate', {})[0xd28fac] = (0x6d21a2, 6)
# BUILD 634: the buy-list cursor ([DD6B84] row): ESUI imul 0x24 -> 0x18, +0x18 -> +0x16
# (x86 71BAA4 / 71BAD6); the port computes both states in e31cbc / e31d18. The pokes
# never moved the row before, so it was never compared.
FNS[0x71aaa3]['struct_x86'] = FNS[0x71aaa3]['struct_x86'] + XI(0x71baa4, 0x71baa7, 0x71bad6, 0x71bad9)
# x36 is two words here (add lsl #3 ; lsl #2): both are rewritten in place (x24)
FNS[0x71aaa3]['arm_struct'].update({0xe31cbc: _X24[0], 0xe31cc0: _X24[1], 0xe31d18: _X24[0], 0xe31d1c: _X24[1],
                                    0xe31cc8: A.add_imm(19, 9, 0x16), 0xe31d24: A.add_imm(19, 9, 0x16)})
FNS[0x71aaa3]['pokes'] = FNS[0x71aaa3]['pokes'] + [
    {0x92565c: s, 0xdc130c: 0, 0xdd4734: 1, 0xdd4724: 0, 0xdd6bb0: 0, 0xdd6b94: 0, 0xdd6ba4: 0,
     0xdd6b84: r, 0xdd6bbc: r, 0xdd6bf4: r, 0xdd6b48: r % 3, 0xdd6c98: r % 4, 0xdd4710: 0x925660}
    for s in (0, 1, 2, 3, 6) for r in (0, 2, 4, 6)]
