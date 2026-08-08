#!/usr/bin/env python3
"""
verify_patches.py -- independent check on every patch word, by disassembly.

The generator's own verify-before-write only proves the STOCK bytes were what
we expected. It says nothing about whether the replacement word is a sane
instruction. This checks that separately, with a different tool (capstone)
than the one that produced the encodings:

  * both words decode to a real AArch64 instruction;
  * the mnemonic class is preserved (a `cmp` stays a comparison, a shift stays
    a shift), or the substitution is an accepted equivalence
    (ORR Rd,WZR,#imm -> MOVZ Rd,#imm, and MOVZ <-> MOVN for sign changes);
  * the destination and source registers are unchanged;
  * the immediate really moved from the stock value to the intended value.

An encoding that looks plausible in hex and decodes to the wrong register is
exactly the class of error that produces a structurally perfect NSO which
corrupts the game.

    python3 verify_patches.py --nso main
"""
import argparse, re, struct, sys

try:
    import lz4.block
except ImportError:
    sys.exit('need lz4:  pip install lz4 --break-system-packages')
try:
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
except ImportError:
    sys.exit('need capstone:  pip install capstone --break-system-packages')

import ff7nx_60fps as gen
try:
    from ff7nx_patchgroups import INTENT
except ImportError:
    INTENT = {}

# Decoders for the immediate the patched word actually carries. Kept separate
# from the resolver's copy on purpose: this file must be able to disagree.
def _bitmask(sf, n, immr, imms):
    width = 64 if sf else 32
    if n and not sf:
        return None
    bits = (n << 6) | ((~imms) & 0x3F)
    ln = bits.bit_length() - 1
    if ln < 1:
        return None
    esize = 1 << ln
    if esize > width:
        return None
    levels = esize - 1
    s, r = imms & levels, immr & levels
    if s == levels:
        return None
    e = (1 << (s + 1)) - 1
    if r:
        e = ((e >> r) | (e << (esize - r))) & ((1 << esize) - 1)
    v = 0
    for i in range(0, width, esize):
        v |= e << i
    return v & ((1 << width) - 1)


def carried(word, tag):
    """The immediate `word` carries under instruction class `tag`, or None."""
    op = word
    sf = (op >> 31) & 1
    mask = 0xFFFFFFFFFFFFFFFF if sf else 0xFFFFFFFF
    if (op >> 23) & 0x3F == 0x25:
        opc, hw, imm16 = (op >> 29) & 3, (op >> 21) & 3, (op >> 5) & 0xFFFF
        if opc == 2 and tag in ('movz', 'movi', 'movn'):
            return imm16 << (16 * hw)
        if opc == 0 and tag in ('movz', 'movi', 'movn'):
            return (~(imm16 << (16 * hw))) & mask
    if (op >> 24) & 0x1F == 0x11 and tag in ('add', 'adds', 'sub', 'subs'):
        sh, imm12 = (op >> 22) & 1, (op >> 10) & 0xFFF
        return imm12 << 12 if sh else imm12
    if (op >> 23) & 0x3F == 0x24 and tag in ('and', 'ands', 'orr', 'eor',
                                             'movz', 'movi'):
        return _bitmask(sf, (op >> 22) & 1, (op >> 16) & 0x3F, (op >> 10) & 0x3F)
    if (op >> 23) & 0x3F == 0x26 and tag in ('asr', 'lsr', 'lsl'):
        immr, imms = (op >> 16) & 0x3F, (op >> 10) & 0x3F
        top = 63 if sf else 31
        if imms == top:
            return immr
        return top - imms
    return None

# Instruction families that count as the same kind of operation.
FAMILY = {
    'mov': 'const', 'movz': 'const', 'movn': 'const', 'movk': 'const',
    'orr': 'const',
    'add': 'addsub', 'adds': 'addsub', 'sub': 'addsub', 'subs': 'addsub',
    'cmp': 'addsub', 'cmn': 'addsub', 'neg': 'addsub',
    'and': 'logic', 'ands': 'logic', 'eor': 'logic', 'tst': 'logic',
    'asr': 'shift', 'lsr': 'shift', 'lsl': 'shift', 'ubfx': 'shift',
    'sbfx': 'shift', 'ubfiz': 'shift', 'sbfiz': 'shift', 'sbfm': 'shift',
    'ubfm': 'shift', 'lsrv': 'shift',
    # Bitfield INSERT is its own family -- it keeps the destination's other
    # bits, which no shift does. The recompiler emits it where the x86 did
    # `and` + `add` of disjoint bit ranges, e.g. the field blink reload
    # `(jitter & 0x1F) + 0x40` becomes `mov w9,#0x40` + `bfxil w9,w8,#0,#5`.
    # Classified separately so a bfxil can only ever be replaced by a bfxil.
    'bfxil': 'bitfield', 'bfi': 'bitfield', 'bfm': 'bitfield',
    'bfc': 'bitfield',
}
RE_IMM = re.compile(r'#(-?0x[0-9a-fA-F]+|-?\d+)')


def decode(md, word):
    b = struct.pack('<I', word)
    for i in md.disasm(b, 0):
        return i.mnemonic, i.op_str
    return None, None


def regs(op_str):
    return re.findall(r'\b([wx](?:\d+|zr))\b', op_str)


def imms(op_str):
    return [int(t, 0) for t in RE_IMM.findall(op_str)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--nso', required=True, help='STOCK exefs/main')
    a = ap.parse_args()

    data = open(a.nso, 'rb').read()
    segs = [struct.unpack('<III', data[b:b + 12]) for b in (0x10, 0x20, 0x30)]
    comp = struct.unpack('<III', data[0x60:0x6C])
    flags = struct.unpack('<I', data[0x0C:0x10])[0]
    fo, mo, ds = segs[0]
    blob = data[fo:fo + comp[0]]
    text = (lz4.block.decompress(blob, uncompressed_size=ds)
            if flags & 1 else blob[:ds])

    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    groups = [('CONFIRMED', gen.NSO_CONFIRMED)]
    groups += [(g, gen.NSO_GATED[g]) for g in sorted(gen.NSO_GATED)]

    nok = nbad = 0
    problems = []
    for gname, patches in groups:
        for label, off, old, new in patches:
            cur, = struct.unpack('<I', text[off:off + 4])
            om, oo = decode(md, old)
            nm, no = decode(md, new)
            errs = []
            if cur != old:
                errs.append('stock word is %08X, table says %08X' % (cur, old))
            if om is None:
                errs.append('stock word does not decode')
            if nm is None:
                errs.append('replacement word does not decode')
            if om and nm:
                fo_, fn_ = FAMILY.get(om), FAMILY.get(nm)
                if fo_ is None or fn_ is None:
                    errs.append('unclassified mnemonic %s -> %s' % (om, nm))
                elif fo_ != fn_:
                    errs.append('family changed: %s (%s) -> %s (%s)'
                                % (om, fo_, nm, fn_))
                ro, rn = regs(oo), regs(no)
                # MOV-immediate forms name one fewer register than ORR,WZR
                if fo_ == 'const':
                    ro = [r for r in ro if r not in ('wzr', 'xzr')]
                    rn = [r for r in rn if r not in ('wzr', 'xzr')]
                    if ro[:1] != rn[:1]:
                        errs.append('destination changed: %s -> %s' % (oo, no))
                elif ro != rn:
                    errs.append('registers changed: %s -> %s' % (oo, no))
                io, iN = imms(oo), imms(no)
                if not io or not iN:
                    errs.append('no immediate found: %r / %r' % (oo, no))
                elif io == iN:
                    errs.append('immediate unchanged (%s)' % io)
            # End-to-end: does the patched word carry the value FFNx asks for?
            if off in INTENT:
                tag, stock, want = INTENT[off]
                m32 = 0xFFFFFFFF
                gs, gw = carried(old, tag), carried(new, tag)
                if gs is None or gw is None:
                    errs.append('cannot read the %s immediate back' % tag)
                else:
                    if gs != (stock & m32):
                        errs.append('stock word carries %d, resolver recorded '
                                    'stock %d' % (gs, stock))
                    if gw != (want & m32):
                        errs.append('patched word carries %d, FFNx wants %d'
                                    % (gw, want))
            if errs:
                nbad += 1
                problems.append((gname, label, off, old, new, om, oo, nm, no,
                                 errs))
            else:
                nok += 1

    for gname, label, off, old, new, om, oo, nm, no, errs in problems:
        print('FAIL [%s] %s' % (gname, label))
        print('     +0x%06X  %08X  %-8s %s' % (off, old, om or '??', oo or ''))
        print('              -> %08X  %-8s %s' % (new, nm or '??', no or ''))
        for e in errs:
            print('     * %s' % e)

    print('\n%d patch word(s) verified by disassembly, %d problem(s)'
          % (nok, nbad))

    # A readable dump of everything, so the encodings can be eyeballed too.
    print('\n%-18s %-9s %-34s %s' % ('group', 'offset', 'stock', 'patched'))
    for gname, patches in groups:
        for label, off, old, new in patches:
            om, oo = decode(md, old)
            nm, no = decode(md, new)
            print('%-18s +0x%06X %-8s %-25s %-8s %s'
                  % (gname, off, om, oo, nm, no))
    return 1 if nbad else 0


if __name__ == '__main__':
    sys.exit(main())
