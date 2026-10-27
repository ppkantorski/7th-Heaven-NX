#!/usr/bin/env python3
"""
test_tifa_slots.py -- the spinning reel in Tifa's limit break.

    python3 test_tifa_slots.py

WHAT IT IS FOR
==============
FFNx applies exactly three things to Tifa's limit break. Two were already in
this project (the effect100 `field_1A` multiply, and
`tifa_limit_2_1_sub_4E48D4+0x1FE`). The third -- animations.cpp:1325, the
"Tifa slots speed patch" -- was missing, and it is the one that paces the
reel itself. Without it the reel turns at a quarter of the rate the rest of
the limit runs at.

On PC it is two literal byte writes into `display_tifa_slots_handler_6E3135`:

    +0x168   07 -> 03    the `and ecx, 7` immediate
    +0x16B   8A -> CA    the SIB byte of `lea edx,[edx+ecx*4+0x144]`, x4 -> x8

Both are code, so on Switch they have to land in the ARM64 recompilation.

WHY THIS TEST EXISTS
====================
Neither byte can be placed with `x86_to_arm`, which maps function ENTRIES,
not instructions -- so the two ARM64 words were placed by reading the
surrounding instructions. That is the same method that went wrong once
already (see MIS_RESOLVED in ff7nx_60fps.py: two identical `sar reg,5` words
in one function, and the recompiler emitted the arm FFNx does NOT patch
first). So the anchors are re-checked here against the real module every run,
rather than trusted because they looked right once:

  * the x86 side is DERIVED, not assumed: 6D83C8 -> 6D82EA -> 6D797C ->
    6E3135, FFNx's own chain, and the two bytes at +0x168/+0x16B are checked
    to be the operands the FFNx comment says they are
  * the two ARM64 sites are UNIQUE in the function and the surrounding window
    is UNIQUE in the whole module -- if a future dump makes either ambiguous,
    this fails instead of patching the wrong instruction
  * the stock words are what the group expects, and the patched words
    disassemble to `and w8, w8, #3` and `add w8, w9, w8, lsl #3`
  * nothing else in the module moves: only these two words differ

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

import capstone

import nxmap
import ff7nx_60fps as F

MAIN = os.environ.get('FF7NX_MAIN', 'rv/main')
EXE = os.environ.get('FF7_EXE', 'ff7_en')

FUNC_X86 = 0x6E3135
AND_OFF, LEA_OFF = 0xD49F78, 0xD49F80

_fail = [0]


def ok(cond, what):
    print(('  ok  ' if cond else '  FAIL  ') + what)
    if not cond:
        _fail[0] += 1


def dis1(word):
    cs = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
    i = next(cs.disasm(struct.pack('<I', word), 0))
    return i.mnemonic + ' ' + i.op_str


def main():
    print('the x86 side is derived from FFNx\'s own chain, not assumed')
    import exe_patch
    data = open(EXE, 'rb').read()
    pe = exe_patch.parse_pe(data)

    def rd(va, n):
        o = exe_patch.va_to_offset(pe, va)
        return data[o:o + n]

    def grc(base, off):
        b = rd(base + off, 5)
        assert b[0] in (0xE8, 0xE9), b.hex(' ')
        return base + off + 5 + struct.unpack('<i', b[1:5])[0]

    a = grc(0x6D83C8, 0xE0)
    b = grc(a, 0x59)
    c = grc(b, 0x1C2)
    ok((a, b, c) == (0x6D82EA, 0x6D797C, 0x6E3135),
       'battle_sub_6D83C8 -> 6D82EA -> 6D797C -> display_tifa_slots_'
       'handler_6E3135')

    o = exe_patch.va_to_offset(pe, c)
    ok(data[o + 0x166:o + 0x168] == b'\x83\xe1',
       'FFNx +0x168 is the imm8 of `and ecx, imm8` at x86 0x6E329B')
    ok(data[o + 0x168] == 0x07,
       '  and its byte is the 0x07 mask FFNx rewrites to 0x03')
    ok(data[o + 0x169] == 0x8D and data[o + 0x16B] == 0x8A,
       'FFNx +0x16B is the SIB byte 0x8A of the `lea` at x86 0x6E329E')
    ok(((data[o + 0x16B] >> 6) & 3) == 2 and ((0xCA >> 6) & 3) == 3,
       '  0x8A -> 0xCA is the SIB scale field, x4 -> x8, nothing else')
    ok(struct.unpack('<I', data[o + 0x16C:o + 0x170])[0] == 0x144,
       '  and the displacement it carries is +0x144')

    print()
    print('the two ARM64 sites are unambiguous')
    m = nxmap.Main(MAIN)
    lo, hi = m.extent(FUNC_X86)
    cs = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
    ins = list(cs.disasm(m.text[lo:hi], lo))
    ands = [i.address for i in ins
            if i.mnemonic == 'and' and i.op_str.endswith('#7')]
    leas = [ins[k].address for k in range(len(ins) - 1)
            if ins[k].mnemonic == 'add' and 'lsl #2' in ins[k].op_str
            and ins[k + 1].mnemonic == 'add'
            and ins[k + 1].op_str.endswith('#0x144')]
    ok(ands == [AND_OFF],
       'exactly one `and w,w,#7` in the function body, at +0x%X' % AND_OFF)
    ok(leas == [LEA_OFF],
       'exactly one `add ...,lsl #2` + `add ...,#0x144`, at +0x%X' % LEA_OFF)
    win = m.text[AND_OFF - 8:LEA_OFF + 8]
    ok(m.text.count(win) == 1,
       'and the %d-byte window around them is unique in the WHOLE module'
       % len(win))
    ok(lo <= AND_OFF < hi and lo <= LEA_OFF < hi,
       'both lie inside display_tifa_slots_handler_6E3135 (+0x%X..+0x%X)'
       % (lo, hi))

    print()
    print('the group says what it means')
    group = F.NSO_GATED['tifa-slots']
    ok(len(group) == 2, 'tifa-slots carries exactly two word patches')
    by_off = {p[1]: p for p in group}
    ok(set(by_off) == {AND_OFF, LEA_OFF},
       'at the two offsets the anchors just confirmed')
    for off in (AND_OFF, LEA_OFF):
        stock = struct.unpack('<I', m.text[off:off + 4])[0]
        ok(by_off[off][2] == stock,
           '+0x%X expected stock word 0x%08X matches the module' % (off, stock))
    ok(dis1(by_off[AND_OFF][2]) == 'and w8, w8, #7'
       and dis1(by_off[AND_OFF][3]) == 'and w8, w8, #3',
       '+0x%X  `and w8, w8, #7` -> `and w8, w8, #3`' % AND_OFF)
    ok(dis1(by_off[LEA_OFF][2]) == 'add w8, w9, w8, lsl #2'
       and dis1(by_off[LEA_OFF][3]) == 'add w8, w9, w8, lsl #3',
       '+0x%X  `add w8, w9, w8, lsl #2` -> `lsl #3`' % LEA_OFF)
    ok(by_off[AND_OFF][2] ^ by_off[AND_OFF][3] == (2 ^ 1) << 10,
       'the AND patch changes the imms field and nothing else')
    ok(by_off[LEA_OFF][2] ^ by_off[LEA_OFF][3] == (2 ^ 3) << 10,
       'the ADD patch changes the shift-amount field and nothing else')

    print()
    print('it is registered where it has to be')
    ok('tifa-slots' in F.RECOMMENDED_ENABLE,
       'tifa-slots is in the shipping preset')
    ok('tifa-slots' in F.NEW_UNTESTED,
       'and is flagged as not yet hardware-confirmed')
    ok('tifa-slots' not in F.QUARANTINED,
       'and was not quarantined as a non-fps symbol')

    print()
    print('applying it moves only those two words')
    patched = bytearray(m.text)
    for off in (AND_OFF, LEA_OFF):
        patched[off:off + 4] = struct.pack('<I', by_off[off][3])
    diff = [i for i in range(0, len(patched), 4)
            if patched[i:i + 4] != m.text[i:i + 4]]
    ok(diff == [AND_OFF, LEA_OFF],
       'exactly two words in the module differ afterwards')

    print()
    print('and a real build moves exactly those two words')
    HERE = os.path.dirname(os.path.abspath(__file__))
    exe = os.path.join(HERE, 'ff7_en')
    nso = os.path.join(HERE, 'dump', 'exefs', 'main')
    if not (os.path.exists(exe) and os.path.exists(nso)):
        print('  --  skipped: no ff7_en / exefs/main beside this test')
    else:
        import shutil
        import tempfile

        def build(enable):
            out = tempfile.mkdtemp()
            saved = F.RECOMMENDED_ENABLE
            F.RECOMMENDED_ENABLE = enable
            try:
                F.run(F.recommended_argv(out, exe=exe, nso=nso),
                      log=lambda *a: None)
                got = nxmap.Main(os.path.join(
                    out, 'atmosphere', 'contents', '0100A5B00BDC6000',
                    'exefs', 'main')).text
            finally:
                F.RECOMMENDED_ENABLE = saved
                shutil.rmtree(out, ignore_errors=True)
            return got

        full = tuple(F.RECOMMENDED_ENABLE)
        on = build(full)
        off = build(tuple(g for g in full if g != 'tifa-slots'))
        # The output is a COMPRESSED NSO, so the files differ in length for
        # reasons that have nothing to do with the patch. The comparison has
        # to be on the decompressed text.
        ok(len(on) == len(off),
           'the decompressed module is the same size either way')
        moved = [i for i in range(0, min(len(on), len(off)), 4)
                 if on[i:i + 4] != off[i:i + 4]]
        ok(moved == [AND_OFF, LEA_OFF],
           'the whole shipping preset differs by exactly +0x%X and +0x%X, '
           'nothing else' % (AND_OFF, LEA_OFF))

    print()
    if _fail[0]:
        print('%d FAILED' % _fail[0])
        return 1
    print('all good')
    return 0


if __name__ == '__main__':
    sys.exit(main())
