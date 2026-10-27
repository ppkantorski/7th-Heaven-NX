#!/usr/bin/env python3
"""
test_bitmask.py -- check the ARM64 immediate codecs against capstone.

Every encoding the resolver can emit is checked against an independent
disassembler, exhaustively rather than by sampling. The first version of
decode_bitmask agreed with capstone on only 514 of 4,032 32-bit encodings and
still produced patch words that looked plausible in hex, so this test is not
optional.

    python3 test_bitmask.py

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
import importlib.util, re, struct, sys

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM

spec = importlib.util.spec_from_file_location('rz', nxpaths.src_file('ff7nx_resolve.py'))
rz = importlib.util.module_from_spec(spec)
sys.argv = ['rz']
try:
    spec.loader.exec_module(rz)
except SystemExit:
    pass

md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
RE_IMM = re.compile(r'#(-?0x[0-9a-fA-F]+|-?\d+)')


def cs_decode(word):
    for i in md.disasm(struct.pack('<I', word), 0):
        m = RE_IMM.search(i.op_str)
        return i.mnemonic, (int(m.group(1), 0) if m else None)
    return None, None


def test_decode_bitmask():
    fails = 0
    total = 0
    for sf in (0, 1):
        for n in (0, 1):
            for immr in range(64):
                for imms in range(64):
                    # AND (immediate), Rn=Rd=8
                    w = (0x12000000 | (sf << 31) | (n << 22) | (immr << 16)
                         | (imms << 10) | (8 << 5) | 8)
                    mn, got = cs_decode(w)
                    mine = rz.decode_bitmask(sf, n, immr, imms)
                    mask = 0xFFFFFFFFFFFFFFFF if sf else 0xFFFFFFFF
                    if mn is None or got is None:
                        if mine is not None:
                            fails += 1
                            print('  decode: sf=%d n=%d immr=%d imms=%d '
                                  'capstone rejects, we return 0x%X'
                                  % (sf, n, immr, imms, mine))
                        continue
                    total += 1
                    if mine is None or (mine & mask) != (got & mask):
                        fails += 1
                        if fails < 10:
                            print('  decode: sf=%d n=%d immr=%d imms=%d '
                                  'capstone=0x%X mine=%s'
                                  % (sf, n, immr, imms, got & mask,
                                     '0x%X' % mine if mine is not None else None))
    print('decode_bitmask: %d legal encodings, %d mismatch' % (total, fails))
    return fails


def test_encode_bitmask():
    """Round-trip: encode(v) must decode back to v, and capstone must agree."""
    fails = checked = 0
    for sf in (0, 1):
        width = 64 if sf else 32
        vals = set()
        for immr in range(64):
            for imms in range(64):
                for n in ((0, 1) if sf else (0,)):
                    v = rz.decode_bitmask(sf, n, immr, imms)
                    if v is not None:
                        vals.add(v)
        for v in sorted(vals):
            enc = rz.encode_bitmask(sf, v)
            if enc is None:
                fails += 1
                print('  encode: sf=%d value 0x%X has no encoding' % (sf, v))
                continue
            n, immr, imms = enc
            back = rz.decode_bitmask(sf, n, immr, imms)
            w = (0x12000000 | (sf << 31) | (n << 22) | (immr << 16)
                 | (imms << 10) | (8 << 5) | 8)
            _mn, got = cs_decode(w)
            mask = (1 << width) - 1
            checked += 1
            if back != v or got is None or (got & mask) != v:
                fails += 1
                if fails < 10:
                    print('  encode: sf=%d v=0x%X -> back=0x%X capstone=%s'
                          % (sf, v, back or 0,
                             '0x%X' % (got & mask) if got is not None else None))
    print('encode_bitmask: %d values round-tripped, %d mismatch'
          % (checked, fails))
    return fails


def test_shifts():
    """ASR/LSR/LSL decode and re-encode must survive capstone."""
    fails = 0
    checked = 0
    for sf in (0, 1):
        top = 63 if sf else 31
        for tag, opc in (('asr', 0), ('lsr', 2)):
            for shift in range(0, top + 1):
                w = (0x13000000 | (sf << 31) | (opc << 29) | (sf << 22)
                     | (shift << 16) | (top << 10) | (8 << 5) | 8)
                tags = dict(rz.arm64_immediates(w))
                checked += 1
                if tags.get(tag) != shift:
                    fails += 1
                    if fails < 10:
                        print('  shift decode: sf=%d %s #%d -> %s'
                              % (sf, tag, shift, tags))
        for shift in range(1, top + 1):
            immr, imms = (top + 1 - shift) % (top + 1), top - shift
            w = (0x13000000 | (sf << 31) | (2 << 29) | (sf << 22)
                 | (immr << 16) | (imms << 10) | (8 << 5) | 8)
            mn, got = cs_decode(w)
            tags = dict(rz.arm64_immediates(w))
            checked += 1
            if mn != 'lsl' or got != shift or tags.get('lsl') != shift:
                fails += 1
                if fails < 10:
                    print('  lsl decode: sf=%d #%d -> capstone %s #%s, ours %s'
                          % (sf, shift, mn, got, tags))
    print('shifts: %d encodings checked, %d mismatch' % (checked, fails))
    return fails


def test_rewrite_roundtrip():
    """
    Whatever rewrite() emits must carry the intended value and keep the
    instruction's family and destination register.
    """
    fails = checked = 0
    cases = [
        (0x52800028, 'movz', 7),          # mov w8, #1 -> #7
        (0x321E03E8, 'movi', 1),          # orr w8, wzr, #4 -> mov w8, #1
        (0x1287CFFC, 'movn', -4000),      # mov w28, #-16000 -> #-4000
        (0x11020108, 'add', 32),          # add w8, w8, #128 -> #32
        (0x51004108, 'sub', 4),           # sub w8, w8, #16 -> #4
        (0x7100413F, 'subs', 64),         # cmp w9, #16 -> #64
        (0x13087D1C, 'asr', 9),           # asr w28, w8, #8 -> #9
        (0x121A7908, 'and', 0x7F),        # and w8, w8, #imm -> #0x7F
    ]
    for orig, tag, want in cases:
        new = rz.rewrite(orig, tag, want)
        checked += 1
        if new is None:
            fails += 1
            print('  rewrite: %08X %s -> %s refused' % (orig, tag, want))
            continue
        om, _ = cs_decode(orig)
        nm, got = cs_decode(new)
        tags = dict(rz.arm64_immediates(new))
        carried = tags.get(tag)
        if tag in ('movz', 'movi', 'movn'):
            ok = (carried == (want & 0xFFFFFFFF)
                  or tags.get('movn') == (want & 0xFFFFFFFF)
                  or tags.get('movz') == (want & 0xFFFFFFFF))
        else:
            ok = carried == (want & 0xFFFFFFFF) or carried == want
        if not ok:
            fails += 1
            print('  rewrite: %08X %s want %s -> %08X (%s %s) carries %s'
                  % (orig, tag, want, new, nm, got, tags))
        if (orig & 0x1F) != (new & 0x1F):
            fails += 1
            print('  rewrite: %08X -> %08X changed Rd' % (orig, new))
    print('rewrite: %d cases, %d mismatch' % (checked, fails))
    return fails


def test_scaler_caves():
    """
    Every instruction the post-call scaler caves emit must disassemble to what
    it is meant to be, and the whole cave must be self-consistent: it ends by
    replaying the displaced instruction and branching back to hook+4.
    """
    import importlib.util as iu
    spec2 = iu.spec_from_file_location('gen', nxpaths.src_file('ff7nx_60fps.py'))
    gen = iu.module_from_spec(spec2)
    try:
        spec2.loader.exec_module(gen)
    except SystemExit:
        pass

    fails = 0
    # individual encoders
    checks = [
        (gen._ldrsh(16, 22), 'ldrsh', 'w16, [x22]'),
        (gen._ldrsh(16, 21), 'ldrsh', 'w16, [x21]'),
        (gen._ldrsh(16, 20), 'ldrsh', 'w16, [x20]'),
        (gen._strh(16, 22), 'strh', 'w16, [x22]'),
        (gen._lsl(16, 16, 1), 'lsl', 'w16, w16, #1'),
        (gen._lsl(16, 16, 2), 'lsl', 'w16, w16, #2'),
        (gen._asr(16, 16, 1), 'asr', 'w16, w16, #1'),
        (gen._add_imm(17, 16, 1), 'add', 'w17, w16, #1'),
        (gen._subs_imm(31, 16, 2), 'cmp', 'w16, #2'),
        (gen._adds_imm(31, 16, 2), 'cmn', 'w16, #2'),
        (gen._csel(16, 17, 16, gen.COND_LT), 'csel', 'w16, w17, w16, lt'),
    ]
    for word, mn, ops in checks:
        gm, go = cs_decode_full(word)
        if gm != mn or go.replace(' ', '') != ops.replace(' ', ''):
            fails += 1
            print('  encoder: %08X -> %s %s, wanted %s %s'
                  % (word, gm, go, mn, ops))

    # whole caves, both operations, against the real resolved sites
    sites = getattr(gen, 'OPCODE_SITES', [])
    if not sites:
        print('  scaler caves: OPCODE_SITES empty, nothing to check')
        return fails
    for site in sites:
        for mult, shift in ((2, 1), (4, 2)):
            cave = 0x900000
            words = gen.opcode_scaler_cave(cave, site, mult, shift)
            txt = [cs_decode_full(w) for w in words]
            # first instruction must load the guest return value
            if txt[0][0] != 'ldrsh' or 'x%d' % site['ctx'] not in txt[0][1]:
                fails += 1
                print('  %s: cave does not start by loading [x%d]: %s %s'
                      % (site['name'], site['ctx'], *txt[0]))
            # must write it back through the same register
            wr = [i for i, t in enumerate(txt) if t[0] == 'strh']
            if len(wr) != 1 or 'x%d' % site['ctx'] not in txt[wr[0]][1]:
                fails += 1
                print('  %s: expected exactly one strh to [x%d]'
                      % (site['name'], site['ctx']))
            # penultimate word replays the displaced instruction verbatim
            if words[-2] != site['displaced']:
                fails += 1
                print('  %s: displaced instruction not replayed (%08X vs %08X)'
                      % (site['name'], words[-2], site['displaced']))
            # last word branches to hook+4
            last = words[-1]
            if (last >> 26) != 0x05:
                fails += 1
                print('  %s: cave does not end in an unconditional B' % site['name'])
            else:
                disp = last & 0x3FFFFFF
                if disp & 0x2000000:
                    disp -= 0x4000000
                tgt = cave + 4 * (len(words) - 1) + disp * 4
                if tgt != site['hook'] + 4:
                    fails += 1
                    print('  %s: returns to 0x%X, wanted hook+4 = 0x%X'
                          % (site['name'], tgt, site['hook'] + 4))
            # no instruction may fail to decode
            for i, (mn, _o) in enumerate(txt):
                if mn is None:
                    fails += 1
                    print('  %s: word %d (%08X) does not decode'
                          % (site['name'], i, words[i]))
            # internal branches must land inside the cave
            for i, w in enumerate(words):
                if (w >> 24) & 0xFF == 0x54:          # B.cond
                    imm19 = (w >> 5) & 0x7FFFF
                    if imm19 & 0x40000:
                        imm19 -= 0x80000
                    t = cave + 4 * i + imm19 * 4
                    if not (cave <= t < cave + 4 * len(words)):
                        fails += 1
                        print('  %s: B.cond at %d leaves the cave' % (site['name'], i))
    print('scaler caves: %d sites x 2 multipliers checked, %d mismatch'
          % (len(sites), fails))
    return fails


def cs_decode_full(word):
    for i in md.disasm(struct.pack('<I', word), 0):
        return i.mnemonic, i.op_str
    return None, None


if __name__ == '__main__':
    bad = (test_decode_bitmask() + test_encode_bitmask() + test_shifts()
           + test_rewrite_roundtrip() + test_scaler_caves())
    print('\n%s' % ('ALL PASS' if bad == 0 else '%d FAILURE(S)' % bad))
    sys.exit(1 if bad else 0)
