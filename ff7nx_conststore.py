#!/usr/bin/env python3
"""
ff7nx_conststore.py -- locate `[guest_address] = constant` stores in ARM64.

WHY THIS EXISTS
===============
The resolver matches FFNx's patch specs by looking for the stock VALUE as a
rewritable ARM64 immediate inside the containing function. When several sites in
one function materialise the same small number, it refuses -- correctly, but it
leaves the spec unresolved.

`battle_sub_430DD0` is the case that matters. FFNx scales four of its constants;
two resolved and two did not, and the two that did not are why battle victory
still runs fast. For `+0x361` the resolver reported:

    4 ARM64 candidates for 1 x86 site -- counts do not match, cannot assign
        cand +0x09D680 321D03E8 movi        <- actually `push 8`
        cand +0x09D740 321D03F7 movi        <- actually a loop-invariant hoist
        cand +0x09E03C 321D03EA movi

Patching the first would corrupt a call argument. Patching the second would hit
every site sharing the hoisted register. Value alone cannot tell them apart.

But the x86 is not just "a mov of 8" -- it is
`mov dword ptr [0x9AE138], 8`, and `+0x326` right before it is
`mov dword ptr [0x9AE138], 30`. The pair (target address, value) is far more
specific than the value on its own, and the recompiler emits a store to a guest
address as one recognisable shape:

    <materialise the guest address into w0>
    bl   0x10FC3A0                   ; guest -> host
    mov  wV, #imm                    ; the value  (may precede the bl instead)
    str  wV, [x0]                    ; the store

So instead of asking "where is the number 8?", this asks "where does this
function store 8 to guest 0x9AE138?". That question has one answer.

A guest ESP push looks superficially similar but is excluded automatically,
because its address is computed from the guest stack pointer
(`ldr w8,[ctx,#0x10]; sub w0,w8,#4`) rather than from a tracked constant --
`push 8` is exactly what `+0x09D680` turned out to be.
"""
import struct

import ff7nx_dispatch as D

TRANSLATE = D.TRANSLATE


def _addr_of_w0(text, pc, consts, lo=6):
    """
    Work out the guest address in w0 immediately before the `bl` at `pc`.

    Accepted forms, all resolved against tracked constants only:
        add  w0, wB, #imm      -> B + imm
        mov  w0, wB            -> B          (ORR w0, wzr, wB)
        movz w0,#lo ; movk w0,#hi,lsl#16
    Anything else -- notably an address derived from the guest stack pointer --
    returns None, which is what keeps pushes out of the results.
    """
    for k in range(1, lo + 1):
        p = pc - 4 * k
        if p < 0:
            return None
        w, = struct.unpack('<I', text[p:p + 4])
        m = D.dec_add_imm(w)
        if m and m[0] == 0:
            base = consts[p].get(m[1])
            return None if base is None else (base + m[2]) & 0xFFFFFFFF
        if (w & 0xFFE0FFE0) == 0x2A0003E0 and (w & 0x1F) == 0:      # mov w0,wB
            return consts[p].get((w >> 16) & 0x1F)
        mz = D.dec_movz(w)
        if mz and mz[0] == 0:
            v = consts[p + 4].get(0)
            return v
        # A definition of w0 by anything else means we cannot claim to know it.
        if (w & 0x1F) == 0 and D.dec_ldst_imm(w) is None:
            return None
    return None


def _translate_before(text, pc, fn_start, window=10):
    """
    The `bl 0x10FC3A0` whose returned x0 the access at `pc` is using.

    Scans back up to `window` instructions and requires that NOTHING between
    the call and the access redefines x0 -- otherwise the pointer being used is
    not the one that call produced. The window has to be generous: the
    recompiler hoists unrelated constant materialisations in between, and in
    battle_sub_430DD0's `+0x361` site there are three of them, which a
    four-instruction window silently missed.
    """
    for k in range(1, window + 1):
        p = pc - 4 * k
        if p < fn_start:
            return None
        w, = struct.unpack('<I', text[p:p + 4])
        if D.is_bl_to(w, p, TRANSLATE):
            return p
        # x0 redefined in between -> different pointer, give up.
        if (w & 0x1F) == 0 and D.dec_ldst_imm(w) is None and \
                (w & 0xFFFFFC00) not in (0x79000000, 0x39000000):
            if _defines_w0(w):
                return None
    return None


def _defines_w0(w):
    """Does this instruction write w0/x0? Stores and branches do not."""
    if (w & 0xFC000000) == 0x14000000:                 # b
        return False
    if (w & 0xFF000000) == 0x54000000:                 # b.cond
        return False
    if (w & 0x3B000000) == 0x39000000 and not (w & 0x00400000):
        return False                                    # stores
    return (w & 0x1F) == 0


def find_const_compares(text, fn_start, fn_end):
    """
    Every `cmp <value loaded from guest_addr>, #imm` in one ARM64 body.

    FFNx's `battle_sub_430DD0 + 0x60E` is `cmp dword ptr [0x9AE148], 0x10`, not
    a store -- which is exactly why the resolver reported "value 16 is not
    present as a rewritable immediate": the 16 lives in a `subs` against a
    loaded value, and four unrelated x86 sites in the same function also hold
    16. Anchoring on the loaded address disambiguates it.

    Yields dicts with addr, value, cmp_pc, cmp_word, load_pc, reg.
    """
    consts = D.base_registers(text, fn_start, fn_end, logical=True)
    out = []
    for pc in range(fn_start, fn_end, 4):
        w, = struct.unpack('<I', text[pc:pc + 4])
        # ldr/ldrh/ldrb wV, [x0]  with no displacement
        width = None
        if (w & 0xFFFFFC00) == 0xB9400000:
            width = 32
        elif (w & 0xFFFFFC00) == 0x79400000:
            width = 16
        elif (w & 0xFFFFFC00) == 0x39400000:
            width = 8
        if width is None or ((w >> 5) & 0x1F) != 0:
            continue
        rv = w & 0x1F
        bl_pc = _translate_before(text, pc, fn_start)
        if bl_pc is None:
            continue
        addr = _addr_of_w0(text, bl_pc, consts)
        if addr is None:
            continue
        # Find a subs/cmp against an immediate using that register, before it
        # is redefined.
        for k in range(1, 13):
            p = pc + 4 * k
            if p >= fn_end:
                break
            w2, = struct.unpack('<I', text[p:p + 4])
            if (w2 & 0xFF800000) == 0x71000000 and ((w2 >> 5) & 0x1F) == rv:
                imm = (w2 >> 10) & 0xFFF
                if (w2 >> 22) & 1:
                    imm <<= 12
                out.append(dict(addr=addr, value=imm, width=width, cmp_pc=p,
                                cmp_word=w2, load_pc=pc, reg=rv))
                break
            if (w2 & 0x1F) == rv and D.dec_ldst_imm(w2) is None:
                break                                   # register reused
    return out


def find_const_stores(text, fn_start, fn_end):
    """
    Every `[guest_addr] = imm` store in one ARM64 function body.

    Yields dicts with:
        addr      guest address written
        value     the constant stored
        width     32 / 16 / 8, from the store form
        imm_pc    offset of the instruction carrying the constant
        imm_word  that instruction word
        store_pc  offset of the `str`
        reg       register the constant travelled in
    """
    consts = D.base_registers(text, fn_start, fn_end, logical=True)
    out = []
    for pc in range(fn_start, fn_end, 4):
        w, = struct.unpack('<I', text[pc:pc + 4])
        # str wV, [x0]  in 32/16/8-bit forms
        width = None
        if (w & 0xFFFFFC00) == 0xB9000000:
            width = 32
        elif (w & 0xFFFFFC00) == 0x79000000:
            width = 16
        elif (w & 0xFFFFFC00) == 0x39000000:
            width = 8
        if width is None:
            continue
        if ((w >> 5) & 0x1F) != 0:            # must be [x0], no displacement
            continue
        rv = w & 0x1F
        bl_pc = _translate_before(text, pc, fn_start)
        if bl_pc is None:
            continue
        addr = _addr_of_w0(text, bl_pc, consts)
        if addr is None:
            continue
        val = consts[pc].get(rv)
        if val is None:
            continue
        # Where did that constant come from? Find its defining movz/orr.
        imm_pc = imm_word = None
        for p in range(pc - 4, max(fn_start - 4, pc - 4 * 40), -4):
            wi, = struct.unpack('<I', text[p:p + 4])
            if (wi & 0x1F) != rv:
                continue
            if D.dec_movz(wi) or (wi & 0xFF800000) == 0x32000000 \
                    or (wi & 0xFF800000) == 0x12800000:
                imm_pc, imm_word = p, wi
            break
        out.append(dict(addr=addr, value=val, width=width, imm_pc=imm_pc,
                        imm_word=imm_word, store_pc=pc, reg=rv))
    return out
