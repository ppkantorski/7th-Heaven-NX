#!/usr/bin/env python3
"""ff7nx_hextarm.py -- FFNx HEXT constant patches, translated to ARM64.

BUILD 866. A HEXT file that edits x86 CODE does nothing here: exefs/main is
an ahead-of-time ARM64 recompilation of ff7_en's .text (see build_exe). Most
of what such files do cannot be carried over mechanically -- they jump into
new x86 routines in code caves, and this port has no x86 interpreter. But a
large class of them only CHANGE A CONSTANT: a shift count, a mask, an
immediate operand, or NOP out a shift. Those can be translated.

HOW A SITE IS FOUND
===================
The recompiler is literal enough to rely on one property: inside one mapped
function, instructions keep their order, and an x86 `sar r, 0xd` becomes an
ARM64 `asr w, w, #0xd`, `and r, 0x1fff` an `and w, w, #0x1fff`, and so on.
So for an x86 instruction that carries immediate V and is the k-th
instruction of its kind with that immediate in its function (counted from
the recompilation map's function entry), the ARM64 site is the k-th
instruction of the matching kind with immediate V in the translated body.

Status Speed Multiplier's six patches are the case that proves it: two
`sar 0xd` / `and 0x1fff` pairs in x86, the same two pairs in the same order
in ARM64, followed by the 0xc/0xfff and 0xf/0x7fff pairs.

WHAT IS REFUSED
===============
Everything else, and the whole FILE with it (half a mod's patch set is worse
than none -- see build_exe):
  * any patched byte that is not an immediate operand change of the same
    instruction, or a shift NOPed out;
  * a count mismatch: x86 has n candidates and ARM64 does not have exactly n
    (the compiler folded or split something, e.g. `idiv 0x64` became a
    magic multiply) -- the k-th would be a guess;
  * an ARM64 word that does not re-encode to the new value, or decodes to
    anything but the old instruction with the new immediate;
  * a word that is neither stock nor this patch when the module is written.

Only the dump's stock `main` and ff7_en are read; the result is a list of
(module offset, stock word, new word).

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import struct

import exe_patch

try:
    from capstone import (Cs, CS_ARCH_X86, CS_MODE_32, CS_ARCH_ARM64,
                          CS_MODE_ARM)
    from capstone import x86_const as _X, arm64_const as _A
except ImportError:                                          # pragma: no cover
    Cs = None

# x86 mnemonic -> ARM64 mnemonics the recompiler emits for it, with an
# immediate operand.
_KIND = {
    'sar': ('asr',),
    'shr': ('lsr',),
    'shl': ('lsl',),
    'sal': ('lsl',),
    'and': ('and', 'ands', 'tst'),
    'or': ('orr',),
    'xor': ('eor',),
    'add': ('add', 'adds', 'sub', 'subs'),
    'sub': ('sub', 'subs', 'add', 'adds'),
    'cmp': ('cmp', 'cmn'),
    'mov': ('mov', 'movz'),
}
_SHIFTS = ('sar', 'shr', 'shl', 'sal')
_NOPS = (b'\x90', b'\x66\x90', b'\x0f\x1f\x00', b'\x0f\x1f\x40\x00')


class Refused(Exception):
    pass


def available():
    return Cs is not None


def _x86():
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    md.detail = True
    return md


def _arm():
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    md.detail = True
    return md


def _x86_imm(ins):
    imms = [op.imm & 0xFFFFFFFF for op in ins.operands
            if op.type == _X.X86_OP_IMM]
    return imms[-1] if len(imms) == 1 else None


def _arm_imm(ins):
    imms = [op.imm & 0xFFFFFFFFFFFFFFFF for op in ins.operands
            if op.type == _A.ARM64_OP_IMM]
    if len(imms) != 1:
        return None
    v = imms[0]
    # add/sub/cmp with `lsl #12`: the operand capstone reports is unshifted
    for op in ins.operands:
        if op.type == _A.ARM64_OP_IMM and op.shift.type == \
                _A.ARM64_SFT_LSL and op.shift.value:
            v <<= op.shift.value
    return v


def _norm(v, bits=32):
    return v & ((1 << bits) - 1)


def _only_nops(blob):
    i = 0
    while i < len(blob):
        for n in sorted(_NOPS, key=len, reverse=True):
            if blob[i:i + len(n)] == n:
                i += len(n)
                break
        else:
            return False
    return True


def _decode(md, data, start, end, base):
    out = []
    off = start - base
    for ins in md.disasm(data[off:off + (end - start)], start):
        out.append(ins)
    return out


# ------------------------------------------------------------- re-encoding
def _decode_bitmask(n, immr, imms, regsize):
    """ARM ARM DecodeBitMasks (wmask only). None when reserved."""
    combined = (n << 6) | ((~imms) & 0x3F)
    if combined == 0:
        return None
    length = combined.bit_length() - 1
    if length < 1:
        return None
    esize = 1 << length
    if esize > regsize:
        return None
    levels = esize - 1
    s = imms & levels
    r = immr & levels
    if s == levels:
        return None
    welem = (1 << (s + 1)) - 1
    welem = ((welem >> r) | (welem << (esize - r))) & ((1 << esize) - 1)
    out = 0
    for i in range(regsize // esize):
        out |= welem << (i * esize)
    return out


def _encode_logical(value, regsize):
    for n in ((0, 1) if regsize == 64 else (0,)):
        for immr in range(64 if regsize == 64 else 32):
            for imms in range(64):
                if regsize == 32 and imms >= 32:
                    continue
                if _decode_bitmask(n, immr, imms, regsize) == value:
                    return n, immr, imms
    return None


def _reencode(word, ins, new):
    """`word` (an ARM64 insn with one immediate) with that immediate set to
    `new`. Every field but the immediate is kept. Raises Refused."""
    sf = word >> 31
    regsize = 64 if sf else 32
    mn = ins.mnemonic
    if mn in ('asr', 'lsr', 'lsl') and (word >> 23) & 0x3F == 0x26:  # xBFM
        if not 0 <= new < regsize:
            raise Refused('shift %d out of range' % new)
        base = word & ~((0x3F << 16) | (0x3F << 10))
        if mn == 'lsl':
            immr, imms = (-new) % regsize, regsize - 1 - new
        else:
            immr, imms = new, regsize - 1
        return base | (immr << 16) | (imms << 10)
    if (word >> 23) & 0x3F == 0x24:                         # logical imm
        enc = _encode_logical(_norm(new, regsize), regsize)
        if enc is None:
            raise Refused('0x%X is not an ARM64 logical immediate' % new)
        n, immr, imms = enc
        base = word & ~((1 << 22) | (0x3F << 16) | (0x3F << 10))
        return base | (n << 22) | (immr << 16) | (imms << 10)
    if (word >> 23) & 0x3F == 0x22:                         # add/sub imm
        if 0 <= new < 0x1000:
            sh, imm = 0, new
        elif new & 0xFFF == 0 and new < 0x1000000:
            sh, imm = 1, new >> 12
        else:
            raise Refused('0x%X does not fit add/sub imm12' % new)
        base = word & ~((1 << 22) | (0xFFF << 10))
        return base | (sh << 22) | (imm << 10)
    if (word >> 23) & 0x3F == 0x25 and ((word >> 29) & 3) == 2:   # MOVZ
        if not 0 <= new < 0x10000:
            raise Refused('0x%X does not fit movz' % new)
        base = word & ~((3 << 21) | (0xFFFF << 5))
        return base | (new << 5)
    raise Refused('no re-encoder for %s %s' % (ins.mnemonic, ins.op_str))


# ------------------------------------------------------------- analysis
# Hand-verified translations, for files whose patches are not a constant
# change but whose ARM64 equivalent is one word. Keyed on the file's exact
# patch set; each entry is (module offset, stock word, new word, note), and
# the stock word is checked like any other.
#
# PS3Tweaks Fixed Stat Gains: `and eax, 0xff; and eax, 7` after the random
# roll at 0x5C79C2 becomes `xor eax, eax` (+ `mov al, 7`). The ARM64 reads
# the guest eax back from the context, `ldr w8, [x19]`, before its single
# `and w8, w8, #7`; loading the constant instead is the same result.
_HAND = {
    frozenset({(0x5C79C7, bytes.fromhex('31C0909090'))}):
        [(0x7F9A40, 0xB9400268, 0x52800008,
          'x86 0x5C79C7 random stat roll -> 0 (minimum gains)')],
    frozenset({(0x5C79C7, bytes.fromhex('31C0909090B00790'))}):
        [(0x7F9A40, 0xB9400268, 0x528000E8,
          'x86 0x5C79C7 random stat roll -> 7 (maximum gains)')],
}


def plan(exe, module, hext_text):
    """Translate one HEXT file. Returns [(module offset, stock, new, note)].
    Raises Refused (with the reason) when any patch in it cannot be."""
    if Cs is None:
        raise Refused('capstone is not installed')
    pe = exe_patch.parse_pe(exe)
    text = next((s for s in pe['sections'] if s[0] == '.text'), None)
    if text is None:
        raise Refused('ff7_en has no .text')
    t_va, t_vs, t_raw = text[1], text[2], text[3]
    patches = list(exe_patch.parse_hext(hext_text))
    if not patches:
        raise Refused('no patches')
    hand = _HAND.get(frozenset((va, bytes(blob)) for va, blob in patches))
    if hand is not None:
        for off, stock, _new, _n in hand:
            if struct.unpack_from('<I', module.img, off)[0] != stock:
                raise Refused('+0x%X is not the stock word this hand '
                              'translation was made for' % off)
        return list(hand)
    new_exe, _a, _s = exe_patch.apply_hext(exe, hext_text)

    def in_text(va):
        return t_va <= va < t_va + t_vs

    for va, blob in patches:
        if not in_text(va):
            raise Refused('0x%X is data, not code (data patches belong to '
                          'the exe channel)' % va)
    x86 = _x86()
    arm = _arm()
    funcs = {}
    for va, blob in patches:
        span = module.containing(va)
        if span is None:
            raise Refused('0x%X is not inside a mapped function' % va)
        funcs.setdefault(span, []).append((va, len(blob)))

    out = []
    for (fstart, fend), sites in sorted(funcs.items()):
        old = _decode(x86, exe, fstart, fend, t_va - t_raw)
        new = _decode(x86, new_exe, fstart, fend, t_va - t_raw)
        new_at = {i.address: i for i in new}
        touched = set()
        for va, n in sites:
            touched.update(range(va, va + n))
        edits = []
        for ins in old:
            span = set(range(ins.address, ins.address + ins.size))
            if not span & touched:
                continue
            o = bytes(ins.bytes)
            nb = new_exe[ins.address - (t_va - t_raw):
                         ins.address - (t_va - t_raw) + ins.size]
            if o == nb:
                continue
            ov = _x86_imm(ins)
            mn = ins.mnemonic
            if mn not in _KIND or ov is None:
                raise Refused('0x%X %s %s: not a constant this translator '
                              'carries' % (ins.address, mn, ins.op_str))
            # `mov reg, imm` is refused: the constant is consumed by some
            # LATER instruction the compiler may have reshaped (Steal
            # Modifier's `mov ecx, 0x64` feeds an idiv that became a magic
            # multiply, so the ARM64 `mov w10, #0x64` it would match changes
            # nothing). A store of an immediate, or an ALU op on one, uses
            # the constant in the instruction itself.
            if mn == 'mov' and ins.operands[0].type != _X.X86_OP_MEM:
                raise Refused('0x%X mov %s: a register constant is used '
                              'elsewhere, not here' % (ins.address,
                                                       ins.op_str))
            if _only_nops(nb) and mn in _SHIFTS:
                edits.append((ins, mn, ov, 0))
                continue
            ni = new_at.get(ins.address)
            if ni is None or ni.size != ins.size or ni.mnemonic != mn:
                raise Refused('0x%X %s %s -> %s: more than the immediate '
                              'changed' % (ins.address, mn, ins.op_str,
                                           ni.op_str if ni else '?'))
            nv = _x86_imm(ni)
            if nv is None:
                raise Refused('0x%X: patched instruction has no immediate'
                              % ins.address)
            ops_o = [(op.type, op.reg if op.type == _X.X86_OP_REG else 0)
                     for op in ins.operands if op.type != _X.X86_OP_IMM]
            ops_n = [(op.type, op.reg if op.type == _X.X86_OP_REG else 0)
                     for op in ni.operands if op.type != _X.X86_OP_IMM]
            if ops_o != ops_n:
                raise Refused('0x%X: operands changed, not just the constant'
                              % ins.address)
            edits.append((ins, mn, ov, nv))
        if not edits:
            continue
        a0, a1 = module.extent(fstart)
        body = list(arm.disasm(bytes(module.img[a0:a1]), a0))
        for ins, mn, ov, nv in edits:
            same = [i for i in old if i.mnemonic == mn
                    and _x86_imm(i) == ov]
            k = [i.address for i in same].index(ins.address)
            want = _KIND[mn]
            cand = [i for i in body if i.mnemonic in want
                    and _arm_imm(i) is not None
                    and (_arm_imm(i) == _norm(ov)
                         or (mn in ('add', 'sub') and
                             _norm(-_arm_imm(i)) == _norm(ov)))]
            if len(cand) != len(same):
                raise Refused('0x%X %s %s: %d x86 site(s) but %d ARM64 '
                              'candidate(s) in +0x%X -- the compiler '
                              'reshaped it' % (ins.address, mn, ins.op_str,
                                               len(same), len(cand), a0))
            ai = cand[k]
            stock = struct.unpack_from('<I', module.img, ai.address)[0]
            target = nv
            if mn in ('add', 'sub') and ai.mnemonic not in (
                    ('add', 'adds') if mn == 'add' else ('sub', 'subs')):
                target = _norm(-nv)
                if target > 0xFFFFFF:
                    raise Refused('0x%X: sign flip needed' % ins.address)
            word = _reencode(stock, ai, target)
            chk = next(arm.disasm(struct.pack('<I', word), ai.address), None)
            if chk is None or chk.mnemonic not in want and not (
                    target == 0 and chk.mnemonic in ('mov', 'lsl', 'lsr',
                                                     'asr', 'sbfx', 'ubfx')):
                raise Refused('0x%X: re-encoded word %08X decodes as %s'
                              % (ins.address, word,
                                 chk.mnemonic if chk else '?'))
            out.append((ai.address, stock, word,
                        'x86 0x%X %s %s -> 0x%X' % (ins.address, mn,
                                                    ins.op_str, nv)))
    if not out:
        raise Refused('nothing to translate')
    return out
