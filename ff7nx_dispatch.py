#!/usr/bin/env python3
"""
ff7nx_dispatch.py -- locate and emit the battle effect/camera dispatcher hooks.

WHAT THIS IS FOR
================
Everything still wrong in FF7 Switch battle at 60 FPS is one mechanism, and it
is not a constant. FFNx replaces four functions outright:

    execute_effect10_fn      execute_effect100_fn
    execute_effect60_fn      execute_camera_functions

Each is a per-frame slot dispatcher. Each walks a fixed array of registered
function pointers and calls them. FFNx's replacement adds exactly one thing to
three of the four: on a slot's FIRST frame after registration, it rescales
that slot's timing fields, keyed on WHICH function was registered.

That is not a "replace the function" problem on Switch. It is a "run a few
instructions on the first frame" problem -- which is the branch-out-and-back
cave pattern already proven on hardware by the eight post-call opcode scalers.

  execute_effect10_fn      symptoms 1 and 2: limit-break damage landing before
                           the swing finishes, character movement pacing
  execute_effect100_fn     symptom 3: damage / action text vanishing early
  execute_camera_functions symptom 2's remainder and symptom 4: magic and
                           limit-break camera racing, victory pacing. Also
                           unblocks r-battle_camera, whose two ATB constants
                           are calibrated against a slowed intro camera.

execute_effect60_fn is deliberately NOT implemented here. Its first-frame block
does not only rescale: for every function it does not name it installs an
InterpolationEffectDecorator, which runs the slot function at the original rate
and interpolates rotation matrices, palettes and colours between frames. That
is a behaviour with per-slot C++ state, not an arithmetic rescale, and it
cannot be expressed as a cave over the stock body. See DISPATCH_NOTES.md.

HOW THE HOOK SITES ARE FOUND
============================
Nothing here is a hand-typed offset. Each dispatcher and each add_fn is located
through FFNx's own derivation chain, mapped to ARM64 through the 0x126D3A8
table, and then scanned for one exact instruction signature:

    add   w0, wB, wI, lsl #2        ; wB holds the array_fn guest base
    bl    0x10FC3A0                 ; guest -> host address translation
    ldr   wF, [x0]                  ; wF = array_fn[idx]     (dispatcher)
    cmp   wF, #0                    ; <-- HOOK
    ...
    cbz   wF, <next slot>

    add   w0, wB, wI, lsl #2        ; same, in add_fn_to_*
    bl    0x10FC3A0
    str   wFN, [x0]                 ; <-- HOOK: array_fn[idx] = function

wB is confirmed to hold the array_fn base by tracking the movz/movk pair that
materialised it. A `str wI, [xCTX, #IDXOFF]` or `ldr wI, [xCTX, #IDXOFF]` in
the same window tells us which guest register slot holds idx, which is how the
cave recovers idx without needing a live host register.

If any part of the signature does not match, the site is REFUSED. There is no
fallback and no guess: a wrong hook here writes into live BSS or into another
slot's timing fields, which is exactly the failure that corrupted an earlier
build while every verification gate still passed.

WHY x16/x17 ARE FREE
====================
Every hook sits on the instruction immediately after a `bl 0x10FC3A0`. x16 and
x17 are IP0/IP1 -- caller-saved by the AAPCS, so dead the instant a call
returns. The translated epilogues confirm it: they restore only x19 and up.
This is the same argument the hardware-confirmed opcode scalers rely on.

WHY NOTHING IS SKIPPED
======================
The hard constraint from HANDOFF 5b: a cave may branch out and back, but may
never skip a translated function, because a translated `call` pushes the guest
return address and the callee's body is what pops it. Every cave here replays
its displaced instruction and branches to hook+4. Control flow is unchanged and
no call is elided. `--cam-throttle` violated this and crashed; these do not.
"""
import argparse, struct, sys
import a64 as A

TRANSLATE = 0x10FC3A0          # guest addr in w0 -> host ptr in x0
BL_TRANSLATE_MASK = 0xFC000000
CTX_SLOT_NAMES = {0x00: 'EAX', 0x04: 'ECX', 0x08: 'EDX', 0x0C: 'EBX',
                  0x10: 'ESP', 0x14: 'EBP', 0x18: 'ESI', 0x1C: 'EDI'}


# ----------------------------------------------------------------- decoding

def dec_movz(w):
    """movz Wd, #imm16  ->  (rd, imm16) or None."""
    if (w & 0xFFE00000) == 0x52800000:
        return w & 0x1F, (w >> 5) & 0xFFFF
    return None


def dec_movk_hi(w):
    """movk Wd, #imm16, lsl #16  ->  (rd, imm16) or None."""
    if (w & 0xFFE00000) == 0x72A00000:
        return w & 0x1F, (w >> 5) & 0xFFFF
    return None


def dec_add_lsl2(w):
    """add Wd, Wn, Wm, lsl #2  ->  (rd, rn, rm) or None."""
    if (w & 0xFFE0FC00) == 0x0B000800:
        return w & 0x1F, (w >> 5) & 0x1F, (w >> 16) & 0x1F
    return None


def dec_add_imm(w):
    """add Wd, Wn, #imm12  (no shift)  ->  (rd, rn, imm) or None."""
    if (w & 0xFFC00000) == 0x11000000:
        return w & 0x1F, (w >> 5) & 0x1F, (w >> 10) & 0xFFF
    return None


def dec_orr_imm(w):
    """
    orr Wd, WZR, #bitmask  ->  (rd, value) or None.

    The recompiler materialises a constant with movz when it has to and with
    ORR-immediate when the value happens to be encodable as a logical mask.
    `mov w8, #0x1e` in battle_sub_430DD0 is the ORR form (321F0FE8), which is
    why a movz-only tracker cannot see the victory-outro constants at all.
    """
    if (w & 0xFF800000) != 0x32000000:
        return None
    if ((w >> 5) & 0x1F) != 31:                       # must be from WZR
        return None
    from ff7nx_resolve import decode_bitmask
    v = decode_bitmask(0, (w >> 22) & 1, (w >> 16) & 0x3F, (w >> 10) & 0x3F)
    return None if v is None else (w & 0x1F, v)


def dec_movn(w):
    """movn Wd, #imm16  ->  (rd, value) or None."""
    if (w & 0xFFE00000) == 0x12800000:
        return w & 0x1F, (~((w >> 5) & 0xFFFF)) & 0xFFFFFFFF
    return None


def dec_mov_wzr(w):
    """mov Wd, WZR  (orr Wd, WZR, WZR)  ->  rd or None."""
    if (w & 0xFFFFFFE0) == 0x2A1F03E0:
        return w & 0x1F
    return None


def array_fn_candidates(text, start, end, array_fn_base):
    """
    Yield (idx_reg, w0_pc, sig_pairs) for every place in [start,end) that
    computes  w0 = array_fn_base + 4 * idx.

    Two forms occur, both produced by the same recompiler:

      direct  add w0, wB, wI, lsl #2                     wB == base
      folded  add wD, wB, wI, lsl #2 ; add w0, wD, #imm  wB + imm == base

    The folded form appears where the base register already held a nearby
    address -- execute_effect100_fn reuses the effect100_array_idx pointer and
    adds 0x4A0 to reach effect100_array_fn. Accepting only the direct form
    would silently miss it, so both are matched and the arithmetic is checked
    against the chain-derived base either way.
    """
    consts = base_registers(text, start, end)
    for pc in range(start, end - 4, 4):
        w, = struct.unpack('<I', text[pc:pc + 4])
        m = dec_add_lsl2(w)
        if not m:
            continue
        rd, rn, idx_reg = m
        base = consts[pc].get(rn)
        if base is None:
            continue
        if rd == 0 and base == array_fn_base:
            yield idx_reg, pc, pc, [(pc, w)]
            continue
        w2, = struct.unpack('<I', text[pc + 4:pc + 8])
        m2 = dec_add_imm(w2)
        if m2 and m2[0] == 0 and m2[1] == rd and base + m2[2] == array_fn_base:
            yield idx_reg, pc, pc + 4, [(pc, w), (pc + 4, w2)]


def dec_ldst_imm(w):
    """
    32-bit LDR/STR (immediate, unsigned offset).
    -> ('ldr'|'str', rt, rn, byte_offset) or None.
    """
    if (w & 0xFFC00000) == 0xB9400000:
        op = 'ldr'
    elif (w & 0xFFC00000) == 0xB9000000:
        op = 'str'
    else:
        return None
    return op, w & 0x1F, (w >> 5) & 0x1F, ((w >> 10) & 0xFFF) * 4


def dec_cmp_imm0(w):
    """subs wzr, Wn, #0  ->  rn or None."""
    if (w & 0xFFFFFC1F) == 0x7100001F:
        return (w >> 5) & 0x1F
    return None


def dec_cbz(w):
    """cbz Wt, label  ->  (rt, imm19_signed) or None."""
    if (w & 0xFF000000) == 0x34000000:
        imm = (w >> 5) & 0x7FFFF
        if imm & 0x40000:
            imm -= 0x80000
        return w & 0x1F, imm
    return None


def is_bl_to(w, pc, target):
    if (w & BL_TRANSLATE_MASK) != 0x94000000:
        return False
    imm = w & 0x3FFFFFF
    if imm & 0x2000000:
        imm -= 0x4000000
    return pc + imm * 4 == target


# ------------------------------------------------------- constant tracking

def _branch(w, pc):
    """
    -> (targets, falls_through) for control flow, or None if not a branch.

    `targets` are absolute addresses inside the module; an indirect branch
    reports no targets, which makes every block it could reach look
    unreachable -- conservative in the safe direction, because an unreachable
    block simply contributes no constants.
    """
    if (w & 0xFC000000) == 0x14000000:                    # b
        imm = w & 0x3FFFFFF
        if imm & 0x2000000:
            imm -= 0x4000000
        return [pc + imm * 4], False
    if (w & 0xFF000000) == 0x54000000:                    # b.cond
        imm = (w >> 5) & 0x7FFFF
        if imm & 0x40000:
            imm -= 0x80000
        return [pc + imm * 4], True
    if (w & 0x7E000000) == 0x34000000:                    # cbz / cbnz
        imm = (w >> 5) & 0x7FFFF
        if imm & 0x40000:
            imm -= 0x80000
        return [pc + imm * 4], True
    if (w & 0x7E000000) == 0x36000000:                    # tbz / tbnz
        imm = (w >> 5) & 0x3FFF
        if imm & 0x2000:
            imm -= 0x4000
        return [pc + imm * 4], True
    if (w & 0xFFFFFC1F) == 0xD65F0000:                    # ret
        return [], False
    if (w & 0xFFFFFC1F) == 0xD61F0000:                    # br Xn
        return [], False
    return None


def base_registers(text, start, end, logical=False):
    """
    Forward constant propagation over the basic blocks of one function, giving
    for each program counter the map {reg: 32-bit value} of registers that
    provably hold a materialised guest address at that point.

    Only movz and movk-lsl-16 create values; any other definition kills one.
    At a control-flow merge two predecessors must AGREE on a register's value
    for it to survive, so a value can never be claimed on a path that does not
    actually produce it.

    `logical=True` additionally tracks constants materialised by ORR-immediate,
    MOVN and `mov Wd, WZR`. It is OFF by default so the dispatcher hook search
    -- which is already verified on hardware -- keeps behaving bit-identically;
    the constant-store locator turns it on, because the recompiler uses the ORR
    form for exactly the victory-outro values that need it.

    A plain linear scan is not good enough here, and not in a subtle way:
    add_fn_to_effect100_fn has `mov w19, #1` inside its assertion block, which
    ends in `ret`. Linearly that appears to destroy the effect100_array_idx
    pointer that the success path further down still relies on, and the hook
    search then finds nothing. Basic blocks make the abort path's write
    invisible to the code it cannot reach.
    """
    pcs = list(range(start, end, 4))
    words = {pc: struct.unpack('<I', text[pc:pc + 4])[0] for pc in pcs}

    # ---- block leaders --------------------------------------------------
    leaders = {start}
    succs = {}
    for pc in pcs:
        br = _branch(words[pc], pc)
        if br is None:
            succs[pc] = [pc + 4] if pc + 4 < end else []
            continue
        targets, ft = br
        nxt = []
        for t in targets:
            if start <= t < end:
                leaders.add(t)
                nxt.append(t)
        if ft and pc + 4 < end:
            nxt.append(pc + 4)
        if pc + 4 < end:
            leaders.add(pc + 4)
        succs[pc] = nxt
    leaders = sorted(leaders)

    block_of = {}
    for i, ld in enumerate(leaders):
        stop = leaders[i + 1] if i + 1 < len(leaders) else end
        for pc in range(ld, stop, 4):
            block_of[pc] = ld

    preds = {ld: set() for ld in leaders}
    for pc in pcs:
        for s in succs[pc]:
            if s in preds:
                preds[s].add(block_of[pc])

    def step(regs, w):
        m = dec_movz(w)
        if m:
            regs[m[0]] = m[1]
            return
        m = dec_movk_hi(w)
        if m:
            if m[0] in regs:
                regs[m[0]] = (regs[m[0]] & 0xFFFF) | (m[1] << 16)
            else:
                regs.pop(m[0], None)
            return
        if logical:
            m = dec_orr_imm(w)
            if m:
                regs[m[0]] = m[1]
                return
            m = dec_movn(w)
            if m:
                regs[m[0]] = m[1]
                return
            r = dec_mov_wzr(w)
            if r is not None:
                regs[r] = 0
                return
        for rd in _defs(w):
            regs.pop(rd, None)

    TOP = None                      # not yet reached
    block_in = {ld: (dict() if ld == start else TOP) for ld in leaders}
    block_out = {ld: TOP for ld in leaders}

    for _ in range(len(leaders) + 2):
        changed = False
        for i, ld in enumerate(leaders):
            stop = leaders[i + 1] if i + 1 < len(leaders) else end
            if ld == start:
                inc = dict()
            else:
                inc = TOP
                for p in preds[ld]:
                    po = block_out[p]
                    if po is TOP:
                        continue
                    if inc is TOP:
                        inc = dict(po)
                    else:
                        inc = {k: v for k, v in inc.items()
                               if po.get(k) == v}
            if inc is TOP:
                continue
            if block_in[ld] is not TOP and block_in[ld] == inc:
                pass
            else:
                block_in[ld] = inc
                changed = True
            regs = dict(inc)
            for pc in range(ld, stop, 4):
                step(regs, words[pc])
            if block_out[ld] != regs:
                block_out[ld] = regs
                changed = True
        if not changed:
            break

    out = {}
    for i, ld in enumerate(leaders):
        stop = leaders[i + 1] if i + 1 < len(leaders) else end
        regs = dict(block_in[ld] or {})
        for pc in range(ld, stop, 4):
            out[pc] = dict(regs)
            step(regs, words[pc])
    return out


def _defs(w):
    """
    Conservative set of registers this instruction may define. Erring toward
    "defines more" only ever makes constant tracking give up, never lie.
    """
    top = w >> 24
    # Branches and stores define nothing we care about.
    if (w & 0xFC000000) == 0x14000000:              # b
        return ()
    if (w & 0xFF000000) in (0x54000000,):           # b.cond
        return ()
    if (w & 0xFC000000) == 0x94000000:              # bl -- clobbers x0-x18,x30
        return tuple(range(0, 19)) + (30,)
    ls = dec_ldst_imm(w)
    if ls and ls[0] == 'str':
        return ()
    if (w & 0x3B000000) == 0x39000000 and not (w & 0x00400000):
        return ()                                    # other stores
    return (w & 0x1F,)                               # default: Rd/Rt


# --------------------------------------------------------------- the search

class Refused(Exception):
    pass


def find_dispatcher_hook(text, fn_start, fn_end, array_fn_base, n_slots):
    """
    Locate the `cmp wF, #0` that guards a dispatcher's per-slot body.

    Returns dict(hook, displaced, fn_reg, ctx_reg, idx_off, add_pc).
    Raises Refused with a reason if the signature is not matched exactly.
    """
    consts = base_registers(text, fn_start, fn_end)
    cands = list(array_fn_candidates(text, fn_start, fn_end, array_fn_base))
    if not cands:
        raise Refused('nothing in [0x%X,0x%X) computes w0 = array_fn base '
                      '0x%X + 4*idx' % (fn_start, fn_end, array_fn_base))

    hits = []
    for idx_reg, add_pc, w0_pc, add_sig in cands:
        # Find the `bl translate` within the next 3 instructions, allowing the
        # recompiler to interleave one guest-register spill.
        bl_pc = None
        for k in (1, 2, 3):
            pc = w0_pc + 4 * k
            w, = struct.unpack('<I', text[pc:pc + 4])
            if is_bl_to(w, pc, TRANSLATE):
                bl_pc = pc
                break
        if bl_pc is None:
            continue
        w1, = struct.unpack('<I', text[bl_pc + 4:bl_pc + 8])
        ls = dec_ldst_imm(w1)
        if not ls or ls[0] != 'ldr' or ls[2] != 0:
            continue                                  # want `ldr wF, [x0]`
        fn_reg = ls[1]
        hook = bl_pc + 8
        w2, = struct.unpack('<I', text[hook:hook + 4])
        if dec_cmp_imm0(w2) != fn_reg:
            continue                                  # want `cmp wF, #0`
        # The `cbz wF` two instructions later is what proves this is the
        # per-slot guard and not some unrelated read of the same array.
        cbz_pc = None
        for k in (1, 2, 3, 4):
            pc = hook + 4 * k
            w3, = struct.unpack('<I', text[pc:pc + 4])
            c = dec_cbz(w3)
            if c and c[0] == fn_reg:
                cbz_pc = pc
                break
        if cbz_pc is None:
            continue
        # idx must be recoverable from a guest context slot. Look for the
        # ldr/str of idx_reg against a base register that is NOT a tracked
        # constant (i.e. the guest context pointer).
        ctx = _find_ctx_slot(text, add_pc, idx_reg, consts)
        if ctx is None:
            continue
        w_bl, = struct.unpack('<I', text[bl_pc:bl_pc + 4])
        w_cbz, = struct.unpack('<I', text[cbz_pc:cbz_pc + 4])
        hits.append(dict(hook=hook, displaced=w2, fn_reg=fn_reg,
                         ctx_reg=ctx[0], idx_off=ctx[1], add_pc=add_pc,
                         n_slots=n_slots,
                         sig=_sig(hook, add_sig + [(bl_pc, w_bl),
                                                   (bl_pc + 4, w1), (hook, w2),
                                                   (cbz_pc, w_cbz),
                                                   (ctx[2], ctx[3])])))
    if len(hits) != 1:
        raise Refused('%d candidate dispatcher hooks matched the full '
                      'signature, need exactly 1' % len(hits))
    return hits[0]


def _sig(hook, pairs):
    """
    Turn absolute (pc, word) pairs into hook-relative ones, deduplicated and
    sorted. The generator re-checks every entry against the stock NSO, so a
    build cannot proceed if any instruction the location argument depends on
    has moved or changed.
    """
    return sorted({(pc - hook, w) for pc, w in pairs})


def find_addfn_hook(text, fn_start, fn_end, array_fn_base):
    """
    Locate `str wFN, [x0]` -- the `array_fn[idx] = function` store on the
    success path of add_fn_to_*.

    Returns dict(hook, displaced, ctx_reg, idx_off).
    """
    consts = base_registers(text, fn_start, fn_end)
    hits = []
    for idx_reg, add_pc, w0_pc, add_sig in array_fn_candidates(
            text, fn_start, fn_end, array_fn_base):
        bl_pc = None
        for k in (1, 2, 3):
            p = w0_pc + 4 * k
            w1, = struct.unpack('<I', text[p:p + 4])
            if is_bl_to(w1, p, TRANSLATE):
                bl_pc = p
                break
        if bl_pc is None:
            continue
        hook = bl_pc + 4
        w2, = struct.unpack('<I', text[hook:hook + 4])
        ls = dec_ldst_imm(w2)
        if not ls or ls[0] != 'str' or ls[2] != 0:
            continue                                  # want `str wFN, [x0]`
        # The stored value must not be the index itself -- that would be the
        # loop's own bookkeeping, not `array_fn[idx] = function`.
        if ls[1] == idx_reg:
            continue
        ctx = _find_ctx_slot(text, add_pc, idx_reg, consts)
        if ctx is None:
            continue
        w_bl, = struct.unpack('<I', text[bl_pc:bl_pc + 4])
        hits.append(dict(hook=hook, displaced=w2, fn_val_reg=ls[1],
                         ctx_reg=ctx[0], idx_off=ctx[1], add_pc=add_pc,
                         sig=_sig(hook, add_sig + [(bl_pc, w_bl), (hook, w2),
                                                   (ctx[2], ctx[3])])))
    if len(hits) != 1:
        raise Refused('%d candidate add_fn hooks matched the full signature, '
                      'need exactly 1' % len(hits))
    return hits[0]


def dec_sub_imm(w):
    """sub Wd, Wn, #imm12  (no shift)  ->  (rd, rn, imm) or None."""
    if (w & 0xFFC00000) == 0x51000000:
        return w & 0x1F, (w >> 5) & 0x1F, (w >> 10) & 0xFFF
    return None


def dec_cmp_reg(w):
    """subs wzr, Wn, Wm  ->  (rn, rm) or None."""
    if (w & 0xFFE0FC1F) == 0x6B00001F:
        return (w >> 5) & 0x1F, (w >> 16) & 0x1F
    return None


def dec_bl(w, pc):
    """bl label  ->  absolute target or None."""
    if (w & BL_TRANSLATE_MASK) != 0x94000000:
        return None
    imm = w & 0x3FFFFFF
    if imm & 0x2000000:
        imm -= 0x4000000
    return pc + imm * 4


def find_throttle_sites(text, fn_start, fn_end, array_fn_base, undecorated_fn,
                        n_undecorated):
    """
    Locate the dispatcher's indirect call to a slot function, and with it the
    pre-call and post-call hook points for the pause-throttle.

    The signature is the whole guest-call sequence, anchored on the indirect
    call itself:

        bl  0x10FC3A0             ; translate(&array_fn[idx]) -- P
        ldr wE, [xCTX, #0x10]     ; guest ESP
        ldr w0, [x0]              ; w0 = array_fn[idx]
        sub wE, wE, #4            ; ESP -= 4
        str wE, [xCTX, #0x10]     ; the push        <- PRE HOOK
        bl  <thunk>               ; the indirect call
        <anything>                                  <- POST HOOK

    and the address computation feeding P must be the `array_fn_base + 4*idx`
    the chain derived, exactly as the first-frame search requires. Nothing about
    the thunk's own address is assumed; it is simply the one `bl` in this
    window that does not go to the translator.

    DISAMBIGUATION. execute_effect100_fn has TWO of these sequences. The second
    is the `else if (fn == display_battle_action_text_42782A)` path, which FFNx
    calls undecorated -- it must be found, and then left alone. It is told apart
    structurally, not by address or by which comes first: it is the one reached
    through a `cmp wF, wX` where wX provably holds the guest address of
    display_battle_action_text. `n_undecorated` says how many such sites this
    dispatcher is expected to have, and a disagreement aborts, so a future build
    cannot quietly grow or lose one.

    Returns dict(pre, post, thunk, undecorated, ...). Raises Refused otherwise.
    """
    consts = base_registers(text, fn_start, fn_end)
    want_bl = set()
    for _idx_reg, _add_pc, w0_pc, _add_sig in array_fn_candidates(
            text, fn_start, fn_end, array_fn_base):
        for k in (1, 2, 3):
            pc = w0_pc + 4 * k
            w, = struct.unpack('<I', text[pc:pc + 4])
            if is_bl_to(w, pc, TRANSLATE):
                want_bl.add(pc)
                break

    hits, skipped = [], []
    for pc in range(fn_start, fn_end - 4, 4):
        w, = struct.unpack('<I', text[pc:pc + 4])
        thunk = dec_bl(w, pc)
        if thunk is None or thunk == TRANSLATE:
            continue
        p = pc - 20
        if p < fn_start:
            continue
        if p not in want_bl:
            continue
        w1, w2, w3, w4 = struct.unpack('<4I', text[p + 4:p + 20])
        ls1 = dec_ldst_imm(w1)
        ls2 = dec_ldst_imm(w2)
        sb = dec_sub_imm(w3)
        ls4 = dec_ldst_imm(w4)
        if not (ls1 and ls1[0] == 'ldr' and ls1[3] == 0x10):
            continue
        if not (ls2 and ls2[0] == 'ldr' and ls2[1] == 0 and ls2[2] == 0):
            continue                                  # want `ldr w0, [x0]`
        if not (sb and sb[2] == 4 and sb[0] == sb[1] == ls1[1]):
            continue                                  # want `sub wE, wE, #4`
        if not (ls4 and ls4[0] == 'str' and ls4[1] == ls1[1]
                and ls4[2] == ls1[2] and ls4[3] == 0x10):
            continue                                  # want `str wE,[xCTX,#0x10]`
        ctx_reg, store_reg = ls1[2], ls1[1]
        if ctx_reg in consts[p + 4]:
            continue                        # a tracked constant is not the ctx
        # x16 / x17 must still be dead at the pre hook. They are IP0/IP1 and the
        # nearest `bl` is at p, but assert it rather than assume it: a future
        # build could schedule something into this window.
        for q in (p + 4, p + 8, p + 12):
            wq, = struct.unpack('<I', text[q:q + 4])
            if 16 in _defs(wq) or 17 in _defs(wq):
                raise Refused('x16/x17 are not dead at the pre-call hook '
                              '+0x%X (defined at +0x%X)' % (p + 16, q))
        site = dict(pre=p + 16, pre_displaced=w4, post=pc + 4,
                    thunk=thunk, ctx_reg=ctx_reg, store_reg=store_reg,
                    translate_pc=p, call_pc=pc)
        if _reaches_via_cmp(text, consts, fn_start, pc, undecorated_fn):
            skipped.append(site)
        else:
            hits.append(site)

    if len(skipped) != n_undecorated:
        raise Refused('%d undecorated call site(s) found, expected %d'
                      % (len(skipped), n_undecorated))
    if len(hits) != 1:
        raise Refused('%d decorated indirect call site(s) matched the full '
                      'signature, need exactly 1' % len(hits))
    h = hits[0]
    w_post, = struct.unpack('<I', text[h['post']:h['post'] + 4])
    h['post_displaced'] = w_post
    w_call, = struct.unpack('<I', text[h['call_pc']:h['call_pc'] + 4])
    win = [(q, struct.unpack('<I', text[q:q + 4])[0])
           for q in range(h['translate_pc'], h['post'] + 4, 4)]
    h['pre_sig'] = _sig(h['pre'], win)
    h['post_sig'] = _sig(h['post'], win)
    h['undecorated'] = sorted(s['call_pc'] for s in skipped)
    h['undecorated_words'] = [struct.unpack('<I', text[c:c + 4])[0]
                              for c in h['undecorated']]
    return h


# ==========================================================================
# The camera script wait -- opcode 0xF5
# ==========================================================================
#
# WHY THE CAMERAS WERE STILL WRONG AFTER THE THROTTLE
# ---------------------------------------------------
# The pause-throttle fixed limit-break damage timing, which confirms it works.
# It did NOT fix the limit-break and magic CAMERAS, and the reason is that those
# cameras are not paced by a slot function at all. They are paced by a script.
#
# FF7 drives every battle camera from a bytecode script -- the camdat archives
# for attacks, magic, limits and summons, and two tables inside ff7_en for the
# battle intro. `set_camera_position_scripts` and
# `set_camera_focal_position_scripts` step one of those scripts once per frame.
# Opcode 0xF5 is "wait N frames": it stores its operand into
# `bcamera_position.frames_to_wait`, and opcode 0xF4 decrements that counter one
# per frame until it reaches zero. At 60 FPS the interpreter is stepped four
# times as often, so every wait expires four times too early and the whole
# camera script races. No amount of work on the effect dispatchers can reach it.
#
# FFNx fixes this in camera.cpp by wrapping both interpreters
# (`run_camera_position_script`, `run_camera_focal_position_script`): it
# re-simulates the script from the saved position to work out what the stock
# code is about to do, lets the stock code run, and then overwrites
# `frames_to_wait` with the operand multiplied by battle_frame_multiplier:
#
#     case 0xF5:
#         if (scriptPtr[currentPosition] == 0xFF) {
#             framesToWait = -1;  currentPosition++;
#         } else {
#             executedOpCodeF5 = true;
#             framesToWait = scriptPtr[currentPosition++] * battle_frame_multiplier;
#         }
#
# FFNx re-simulates because it is a DLL reaching in from outside. We are editing
# the binary, so we can do the same thing at the source: hook the one store that
# opcode 0xF5's handler makes, and scale the value on its way in. Same result,
# no simulation, no second interpreter to keep in step with the first, and it
# fires exactly when the real interpreter executes 0xF5 rather than when a
# parallel model thinks it did.
#
# THE 0xFF SENTINEL, AND WHY THE MASK IS NOT COSMETIC
# ---------------------------------------------------
# The stock handler reads the operand with a SIGNED byte load
# (`movsx ax, byte ptr [edx+eax]`), so operand 0xFF arrives as -1 and means
# "wait forever". FFNx reads the same byte through a `byte*` -- UNSIGNED -- and
# special-cases only 0xFF. So for operands 0x80..0xFE the two disagree: stock
# treats them as negative (also effectively "wait forever", since 0xF4
# decrements away from zero), FFNx turns them into a finite 512..1016 frame
# wait.
#
# This cave reproduces FFNx exactly, including that quirk, because FFNx is the
# implementation with a working track record and inventing a third behaviour
# here would be guessing. That is what the mask is for: the value in the
# register has been sign-extended to 16 bits by the guest, so `& 0xFF` recovers
# the byte FFNx would have read, and `== 0xFFFF` is the only operand FFNx leaves
# alone. ff7nx_locate.py reports how many operands in the exe's own camera
# scripts are actually in the 0x80..0xFE range, so the question is answerable
# rather than theoretical.


def dec_ldst_h(w):
    """
    16-bit LDRH/STRH (immediate, unsigned offset).
    -> ('ldrh'|'strh', rt, rn, byte_offset) or None.

    `dec_ldst_imm` only knows the 32-bit forms, and reusing it here silently
    matches nothing -- which is exactly how the first version of this locator
    reported "0 sites" on a binary that has two.
    """
    if (w & 0xFFC00000) == 0x79400000:
        op = 'ldrh'
    elif (w & 0xFFC00000) == 0x79000000:
        op = 'strh'
    else:
        return None
    return op, w & 0x1F, (w >> 5) & 0x1F, ((w >> 10) & 0xFFF) * 2


def dec_ldrsb(w):
    """LDRSB Wt, [Xn, #imm] (32-bit dest) -> (rt, rn, offset) or None."""
    if (w & 0xFFC00000) != 0x39C00000:
        return None
    return w & 0x1F, (w >> 5) & 0x1F, (w >> 10) & 0xFFF


def find_camera_wait_site(text, fn_start, fn_end, struct_base, tag):
    """
    Locate opcode 0xF5's `frames_to_wait = operand` store in one camera script
    interpreter.

    Signature, all of it required:

        ...
        ldrsb wB, [x0]          ; movsx ax, byte ptr [scriptPtr + current_position]
        strh  wB, [xCTX]        ; the operand lands in guest AX
        ...
        ldr   wI, [xCTX, #0x14] ; guest EBP
        add   w0, wI, #8        ; &[ebp+8]  -- variationIndex
        bl    0x10FC3A0
        ldrsb wI, [x0]
        mul   wI, wI, wS        ; * sizeof(bcamera_position)
        ldrh  wV, [xCTX]        ; wV = guest AX = the operand
        add   wJ, wI, wBASE     ; wBASE = &array[0].current_position
        add   w0, wJ, #2        ; -> &array[idx].frames_to_wait
        bl    0x10FC3A0
        strh  wV, [x0]          ; <-- HOOK

    The address the store goes through must resolve, through chain-derived
    constants, to `struct_base + 0xA`. That alone is not enough: opcode 0xF4
    writes the same field to decrement it, and an initialiser writes a zero
    there. What makes this unique is the `ldrsb`/`strh` pair that puts a freshly
    fetched SCRIPT BYTE into guest AX, followed by that same AX being stored to
    `frames_to_wait` -- which is opcode 0xF5 and nothing else.

    Refuses unless exactly one site matches.
    """
    consts = base_registers(text, fn_start, fn_end)
    cur_pos = struct_base + 0x8
    frames = struct_base + 0xA
    hits = []
    for pc in range(fn_start + 4, fn_end, 4):
        w, = struct.unpack('<I', text[pc:pc + 4])
        st = dec_ldst_h(w)
        if not (st and st[0] == 'strh' and st[3] == 0):
            continue
        val_reg, base_reg = st[1], st[2]
        if base_reg != 0 or val_reg == 31:
            continue                                     # want `strh wV,[x0]`
        wb, = struct.unpack('<I', text[pc - 4:pc])
        if not is_bl_to(wb, pc - 4, TRANSLATE):
            continue
        if _guest_addr_for(text, consts, pc - 4) != frames:
            continue
        ctx = _camera_ctx(text, consts, pc, val_reg)
        if ctx is None:
            continue
        opnd = _camera_operand_fetch(text, pc, ctx)
        if opnd is None:
            continue
        win = [(q, struct.unpack('<I', text[q:q + 4])[0])
               for q in list(range(opnd, opnd + 8, 4)) +
               list(range(pc - 24, pc + 4, 4))]
        hits.append(dict(hook=pc, displaced=w, val_reg=val_reg, ctx_reg=ctx,
                         operand_pc=opnd, frames_to_wait=frames,
                         current_position=cur_pos,
                         sig=_sig(pc, win)))
    if len(hits) != 1:
        raise Refused('%d camera-wait store(s) matched the full signature in '
                      '%s, need exactly 1' % (len(hits), tag))
    return hits[0]


def _guest_addr_for(text, consts, bl_pc):
    """
    The guest address handed to a `bl 0x10FC3A0`, when it is provable.

    Two shapes occur, both emitted by the same recompiler:
        add w0, wBase, wIdx                       -> base
        add wD, wBase, wIdx ; add w0, wD, #imm    -> base + imm
    Anything else returns None, which makes the caller skip the site rather than
    guess at it.
    """
    for k in (1, 2, 3, 4):
        q = bl_pc - 4 * k
        w, = struct.unpack('<I', text[q:q + 4])
        m = dec_add_imm(w)
        if m and m[0] == 0:
            _rd, rn, imm = m
            base = consts[q].get(rn)
            if base is None:
                for j in (1, 2, 3):
                    q2 = q - 4 * j
                    w2, = struct.unpack('<I', text[q2:q2 + 4])
                    if (w2 & 0xFFE0FC00) == 0x0B000000 and (w2 & 0x1F) == rn:
                        a = consts[q2].get((w2 >> 5) & 0x1F)
                        b = consts[q2].get((w2 >> 16) & 0x1F)
                        base = a if a is not None else b
                        break
            return None if base is None else base + imm
        if (w & 0xFFE0FC00) == 0x0B000000 and (w & 0x1F) == 0:
            a = consts[q].get((w >> 5) & 0x1F)
            b = consts[q].get((w >> 16) & 0x1F)
            return a if a is not None else b
    return None


def _camera_ctx(text, consts, hook, val_reg, back=8):
    """
    The guest context register, proved by finding `ldrh wV, [xCTX]` -- the read
    of guest AX that produced the value about to be stored.

    This is what lets the cave treat `wV` as a zero-extended 16-bit quantity: it
    is not assumed, it is read off the instruction that defined it. The base
    register must not be a tracked constant, which is what distinguishes the
    guest context pointer from a materialised guest data address.
    """
    for k in range(1, back + 1):
        q = hook - 4 * k
        w, = struct.unpack('<I', text[q:q + 4])
        ls = dec_ldst_h(w)
        if not ls or ls[0] != 'ldrh' or ls[1] != val_reg or ls[3] != 0:
            continue
        rn = ls[2]
        if rn == 0 or rn == 31 or rn in consts[q]:
            continue
        return rn
    return None


def _camera_operand_fetch(text, hook, ctx, back=24):
    """
    Find `ldrsb wB, [x0]` immediately followed by `strh wB, [xCTX]` -- the guest
    `movsx ax, byte ptr [scriptPtr + current_position]`.

    This is the discriminator. Opcode 0xF4 also writes `frames_to_wait`, and so
    does an initialiser; neither of them fetches a script byte into AX first.
    Returns the pc of the `ldrsb`.
    """
    for k in range(2, back + 1):
        q = hook - 4 * k
        w, = struct.unpack('<I', text[q:q + 4])
        sb = dec_ldrsb(w)
        if not sb:
            continue
        rt, rn, off = sb
        if rn != 0 or off != 0:                          # want `ldrsb wB,[x0]`
            continue
        w2, = struct.unpack('<I', text[q + 4:q + 8])
        st = dec_ldst_h(w2)
        if not st or st[0] != 'strh':
            continue
        if st[1] != rt or st[2] != ctx or st[3] != 0:    # guest AX is offset 0
            continue
        return q
    return None


def build_camera_wait_cave(cave, site, shift):
    """
    Emit the opcode-0xF5 wait scaler.

        and  w16, wV, #0xFFFF      ; the operand, sign-extended to 16 bits
        movz w17, #0xFFFF
        cmp  w16, w17
        b.eq KEEP                  ; operand byte 0xFF -- "wait forever"
        and  w16, wV, #0xFF        ; the byte FFNx reads through its `byte*`
        lsl  w16, w16, #shift      ; * battle_frame_multiplier
        strh w16, [x0]
        b    BACK
    KEEP:
        strh wV, [x0]              ; the displaced instruction, verbatim
    BACK:
        b    hook + 4

    This is the one cave in this tree that does not simply replay its displaced
    instruction on every path -- on the scaling path it stores a different
    value, which is the entire point. It still stores the same width, through
    the same base register, to the same address, and it still branches back to
    hook+4, so control flow and the guest's view of memory layout are unchanged.
    The KEEP path replays the original instruction exactly, so an operand of
    0xFF is bit-for-bit stock.

    x16 and x17 are free: the hook is the instruction after a `bl 0x10FC3A0`
    returns, so IP0/IP1 are dead by the AAPCS. `wV` is only read, never written,
    so anything downstream that still wants the raw operand gets it. `x0` is the
    translated destination pointer and is left alone.

    `frames_to_wait` is a short, so the widest scaled value -- 254 * 4 = 1016 --
    cannot overflow it. That is why this works here and a static byte patch of
    the camdat files does not: there the operand has to stay in one byte and
    anything above 63 clamps.
    """
    v = site['val_reg']
    if v in (0, 16, 17, 31):
        raise SystemExit('refusing to emit a camera-wait cave: the operand is '
                         'in w%d, which the cave needs as scratch' % v)
    w = []

    def pc(i=None):
        return cave + 4 * (len(w) if i is None else i)

    w.append(A.and_mask(16, v, 16))
    w.append(A.movz(17, 0xFFFF))
    w.append(A.cmp_reg(16, 17))
    eq_i = len(w)
    w.append(0)                                     # b.eq KEEP -- patched
    w.append(A.and_mask(16, v, 8))
    w.append(A.lsl(16, 16, shift))
    w.append(A.strh(16, 0, 0))
    b_i = len(w)
    w.append(0)                                     # b BACK -- patched
    keep = pc()
    w[eq_i] = A.bcond(cave + 4 * eq_i, keep, A.EQ)
    w.append(site['displaced'])
    back = pc()
    w[b_i] = A.b(cave + 4 * b_i, back)
    w.append(A.b(pc(), site['hook'] + 4))
    return w


def build_field_wait_cave(cave, site, shift):
    """
    Emit the FIELD script WAIT scaler -- FFNx's `opcode_script_WAIT`.

        and  w16, wV, #0xFFFF      ; the 16-bit operand the handler assembled
        movz w17, #0x7FFF
        cmp  w16, w17
        b.hi KEEP                  ; would overflow the short -- leave it stock
        lsl  w16, w16, #shift      ; * common_frame_multiplier
        strh w16, [x0]
        b    BACK
    KEEP:
        strh wV, [x0]              ; the displaced instruction, verbatim
    BACK:
        b    hook + 4

    WHY THIS EXISTS
    ---------------
    The field limiter divisor is 30 -> 60, so `field_loop` -- and with it the
    opcode interpreter -- steps twice as often as the scripts were written for.
    Opcode 0x24 (WAIT) latches a frame count into `wait_frames[entity]` and
    counts it down once per interpreter step, so every scripted pause expires
    in half the time. That is what makes scripted set pieces (the train's
    warning lights and their SFX, alarms, timed NPC business) run double speed.

    FFNx fixes exactly this, and only this, in
    `ff7::field::opcode_script_WAIT`:

        wait_frames_ptr[id] = get_field_parameter<WORD>(0);
        if (is_fps_running_more_than_original())
            wait_frames_ptr[id] *= get_frame_multiplier();
        if (!wait_frames_ptr[id]) script_pos += 3;

    The stock handler assembles the operand from two script bytes and stores
    the finished 16-bit value once, at x86 `WAIT + 0xA1`. Scaling at that store
    is arithmetically identical to FFNx's `*=`: it happens after the value is
    complete and before the `if (!v)` test re-reads it, and 0 * n is still 0,
    so the "WAIT 0 falls through" path is untouched.

    WHY THE OVERFLOW GUARD
    ----------------------
    The destination is a `short`. FFNx multiplies in a WORD and lets it wrap;
    a script that waits 40000 frames would come back as 14464 and the pause
    would get SHORTER than stock. Refusing to scale above 0x7FFF makes the
    worst case "unchanged from vanilla" instead of "wrong in a new way". No
    vanilla field script waits anywhere near that long, so in practice this
    arm never runs -- it is here so that a garbage operand cannot make the
    patch worse than not applying it.

    x16/x17 are free: the hook is the instruction after a translated helper
    call returns, so IP0/IP1 are dead by the AAPCS. `wV` is only read, and x0
    (the translated destination pointer) is left alone.
    """
    v = site['val_reg']
    if v in (0, 16, 17, 31):
        raise SystemExit('refusing to emit a field-wait cave: the operand is '
                         'in w%d, which the cave needs as scratch' % v)
    w = []

    def pc(i=None):
        return cave + 4 * (len(w) if i is None else i)

    w.append(A.and_mask(16, v, 16))
    w.append(A.movz(17, 0x7FFF))
    w.append(A.cmp_reg(16, 17))
    hi_i = len(w)
    w.append(0)                                     # b.hi KEEP -- patched
    w.append(A.lsl(16, 16, shift))
    w.append(A.strh(16, 0, 0))
    b_i = len(w)
    w.append(0)                                     # b BACK -- patched
    keep = pc()
    w[hi_i] = A.bcond(cave + 4 * hi_i, keep, A.HI)
    w.append(site['displaced'])
    back = pc()
    w[b_i] = A.b(cave + 4 * b_i, back)
    w.append(A.b(pc(), site['hook'] + 4))
    return w


def build_field_blink_test_cave(cave, site, _shift=None):
    """
    Widen the blink test from `counter == 0` to `counter <= 0`.

        cmp  w20, #0
        b.le TAKE
        b    hook + 4          ; counter > 0 -- eyes open, fall through
    TAKE:
        b    blink_arm         ; counter <= 0 -- eyes shut

    WHY A CAVE AND NOT A REWRITTEN BRANCH
    -------------------------------------
    The stock instruction is `cbz w20, blink_arm`, and the obvious edit is to
    make it `b.le blink_arm`. Two things stop that.

    First, the flags are dead by then. `cmp w20, #0` runs six instructions
    earlier and its result was already materialised into the guest ZF byte;
    between them sits `bl 0x10FC3A0`, which clobbers NZCV. That is exactly why
    the recompiler chose `cbz` -- it needs no flags. A `b.le` in that slot
    would branch on whatever the call left behind.

    Second, `b.le` reaches +-1 MB. The cave lives past the end of .text, about
    8 MB from here, so the conditional branch has to stay local to the cave
    and both exits have to be unconditional `b` (+-128 MB).

    So the cave re-does the compare itself and turns both outcomes into
    unconditional branches. w20 still holds the sign-extended counter -- it is
    stored to the guest EDX slot at hook-8 and not written again -- and NZCV
    is dead in both directions, so recomputing the flags here is free.
    """
    v = site['val_reg']
    if v in (16, 17, 31):
        raise SystemExit('refusing to emit a blink-test cave: the counter is '
                         'in w%d, which the cave needs as scratch' % v)
    w = [A.cmp_imm(v, 0), 0, 0]
    w[1] = A.bcond(cave + 4, cave + 12, A.LE)      # b.le TAKE
    w[2] = A.b(cave + 8, site['hook'] + 4)         # counter > 0 -> eyes open
    w.append(A.b(cave + 12, site['blink_arm']))    # counter <= 0 -> eyes shut
    return w


def build_field_blink_hold_cave(cave, site, _shift=None):
    """
    Make the interval reload conditional, so "eyes shut" lasts two frames.

        ldrsh w16, [x0]        ; the counter, before this store overwrites it
        cbz   w16, HOLD        ; first shut frame -- it is still 0
        strh  wV, [x0]         ; second shut frame -- the displaced store
        b     BACK
    HOLD:
        movz  w16, #0xFFFF     ; -1 as a halfword
        strh  w16, [x0]
    BACK:
        b     hook + 4

    The counter is the state. On the frame it reaches 0 the game takes the
    shut arm and would normally reload immediately; instead we write -1, which
    the widened test in build_field_blink_test_cave also reads as "shut", so
    the next frame takes the shut arm again -- and this time the counter is
    not 0, so the real reload happens.

    Writing -1 rather than any other sentinel matters: the counter is read
    with `ldrsh` and compared as a signed value, and the guest ZF byte the
    translation derives from it (`cset w8, eq`) then holds 0, which is what
    the original `test edx, edx` would have produced for a nonzero value. The
    sentinel is therefore indistinguishable from an ordinary negative counter
    to everything downstream.

    THE ONE PATH THAT MUST STAY BIT-EXACT is the real reload: it replays the
    displaced `strh wV, [x0]` verbatim, so a build with the test cave absent
    would still behave exactly as stock.

    x16 is free -- the hook is the instruction after `bl 0x10FC3A0` returns,
    so IP0/IP1 are dead by the AAPCS. x0 is the translated destination pointer
    and wV (the freshly computed reload value) is only read.
    """
    v = site['val_reg']
    if v in (0, 16, 17, 31):
        raise SystemExit('refusing to emit a blink-hold cave: the reload value '
                         'is in w%d, which the cave needs as scratch' % v)
    w = []

    def pc(i=None):
        return cave + 4 * (len(w) if i is None else i)

    w.append(A.ldrsh(16, 0, 0))
    cbz_i = len(w)
    w.append(0)                                     # cbz w16, HOLD -- patched
    w.append(site['displaced'])                     # the real reload, verbatim
    b_i = len(w)
    w.append(0)                                     # b BACK -- patched
    hold = pc()
    w[cbz_i] = A.cbz(16, cave + 4 * cbz_i, hold)
    w.append(A.movz(16, 0xFFFF))
    w.append(A.strh(16, 0, 0))
    back = pc()
    w[b_i] = A.b(cave + 4 * b_i, back)
    w.append(A.b(pc(), site['hook'] + 4))
    return w


def _reaches_via_cmp(text, consts, fn_start, call_pc, undecorated_fn, back=24):
    """
    True if, within `back` instructions before `call_pc`, there is a
    `cmp wA, wB` with one operand provably holding `undecorated_fn`.

    That is the shape of FFNx's
    `else if (fn == display_battle_action_text_42782A)` arm, and it is the only
    thing that distinguishes the second call site from the first without
    resorting to "the one at the lower address".
    """
    if undecorated_fn is None:
        return False
    lo = max(fn_start, call_pc - 4 * back)
    for pc in range(lo, call_pc, 4):
        w, = struct.unpack('<I', text[pc:pc + 4])
        m = dec_cmp_reg(w)
        if not m:
            continue
        live = consts.get(pc, {})
        if any(live.get(r) == undecorated_fn for r in m):
            return True
    return False


def _find_ctx_slot(text, add_pc, idx_reg, consts, lo=6, hi=3):
    """
    Find the guest-context slot holding idx: a 32-bit ldr/str of idx_reg
    against the guest context pointer, scanning OUTWARD from
    `add w0, wB, wI, lsl #2` -- the recompiler puts it either just before
    (add_fn: `ldr w8, [ctx, #8]`) or just after (dispatcher:
    `str w8, [ctx, #8]`).

    The base register must NOT be a tracked constant. Guest data addresses are
    materialised into a register by movz/movk, whereas the context pointer is
    loaded from a module global in the prologue -- that is what distinguishes
    `str w8, [x21, #8]` (guest EDX in the context) from a store through a
    guest pointer. x0 is excluded because it is the translator's return value.

    Returns (ctx_reg, ctx_off, src_pc, src_word) so the generator can re-verify
    the exact instruction this conclusion rests on.
    """
    order = []
    for k in range(1, max(lo, hi) + 1):
        if k <= hi:
            order.append(k)
        if k <= lo:
            order.append(-k)
    for k in order:
        pc = add_pc + 4 * k
        w, = struct.unpack('<I', text[pc:pc + 4])
        ls = dec_ldst_imm(w)
        if not ls:
            continue
        op, rt, rn, off = ls
        if rt != idx_reg or rn in consts[pc] or rn == 0 or rn == 31:
            continue
        if off not in CTX_SLOT_NAMES:
            continue
        return rn, off, pc, w
    return None


# ------------------------------------------------------------ cave emission

def _mul_u(off, k, width):
    """field *= 2**k, truncating to `width` bits as C does on the narrow type."""
    ld = {8: A.ldrb, 16: A.ldrh, 32: A.ldr}[width]
    st = {8: A.strb, 16: A.strh, 32: A.str_}[width]
    return [ld(17, 0, off), A.lsl(17, 17, k), st(17, 0, off)]


def _div_s(off, k, width):
    """
    field /= 2**k on a SIGNED field, truncating toward zero exactly as C's
    integer division does.

        t = v >> 31          ; 0 or -1
        t = t & (2**k - 1)   ; 0 or (2**k - 1)
        v = (v + t) >> k     ; arithmetic shift

    No flags are touched, so this composes freely inside the cave.
    """
    ld = {16: A.ldrsh, 32: A.ldr}[width]
    st = {16: A.strh, 32: A.str_}[width]
    return [ld(17, 0, off),
            A.asr(16, 17, 31),
            A.and_mask(16, 16, k),
            A.add_reg(17, 17, 16),
            A.asr(17, 17, k),
            st(17, 0, off)]


# Field offsets and C types, transcribed from FFNx src/ff7.h.
#   effect10_data / effect100_data: n_frames short @4
#   bcamera_fn_data:                n_frames WORD  @4, stride 0x28
F10 = {'n_frames': (0x04, 16, 's'), 'field_2': (0x02, 16, 's'),
       'field_6': (0x06, 16, 's'), 'field_A': (0x0A, 16, 's'),
       'field_C': (0x0C, 16, 's'), 'field_E': (0x0E, 16, 's'),
       'field_14': (0x14, 32, 's'), 'field_18': (0x18, 8, 'u'),
       'field_19': (0x19, 8, 'u'), 'field_1A': (0x1A, 8, 'u')}
F100 = dict(F10)
F100['field_1A'] = (0x1A, 16, 's')       # effect100_data widens field_1A
FCAM = {'n_frames': (0x04, 16, 'u'), 'field_6': (0x06, 16, 's'),
        'field_8': (0x08, 16, 's'), 'field_E': (0x0E, 16, 's')}


def _ops(fields, spec, k):
    """spec is a list of ('field', 'mul'|'div') pairs."""
    words = []
    for name, how in spec:
        off, width, sign = fields[name]
        if how == 'mul':
            words += _mul_u(off, k, width)
        else:
            if sign != 's':
                raise SystemExit('refusing to emit a signed divide for the '
                                 'unsigned field %s' % name)
            words += _div_s(off, k, width)
    return words


# --------------------------------------------------------------------------
# The dispatch tables, transcribed one-for-one from FFNx.
#
#   execute_effect10_fn   src/ff7/battle/animations.cpp  execute_effect10_fn
#   execute_effect100_fn  src/ff7/battle/animations.cpp  execute_effect100_fn
#   execute_camera_functions  src/ff7/battle/camera.cpp
#
# Only the arithmetic branches are here. Every FFNx branch that installs an
# effect decorator is absent by construction, and `unhandled` records how many
# of them there are so the report can say what is not covered.
# --------------------------------------------------------------------------
EFFECT10_CASES = [
    ('battle_sub_426DE3', [('n_frames', 'mul'), ('field_18', 'mul'),
                           ('field_C', 'div'), ('field_E', 'div'),
                           ('field_6', 'div')], None),
    ('battle_sub_426941', [('n_frames', 'mul'), ('field_A', 'div'),
                           ('field_C', 'div')], 'n_frames>1'),
    ('battle_sub_426899', [('n_frames', 'mul'), ('field_E', 'div')], None),
    ('battle_sub_4267F1', [('n_frames', 'mul'), ('field_A', 'div')], None),
    ('battle_move_character_sub_426A26',
     [('n_frames', 'mul'), ('field_18', 'mul'),
      ('field_C', 'div'), ('field_E', 'div')], None),
    ('battle_move_character_sub_42739D',
     [('n_frames', 'mul'), ('field_19', 'mul'), ('field_1A', 'mul'),
      ('field_C', 'div'), ('field_E', 'div')], None),
    ('battle_move_character_sub_426F58', [('n_frames', 'mul')], None),
    ('battle_move_character_sub_4270DE',
     [('n_frames', 'mul'), ('field_19', 'mul'), ('field_1A', 'mul'),
      ('field_C', 'div'), ('field_E', 'div'), ('field_14', 'div')], None),
]

EFFECT100_CASES = [
    ('display_battle_action_text_42782A', [('field_6', 'mul')], None),
    ('battle_sub_425D29', [('n_frames', 'mul')], None),
    ('battle_sub_5BDA0F', [('field_2', 'div'), ('n_frames', 'mul')], None),
    # FFNx handles Tifa's two limit breaks in one branch; they scale the same
    # field, so they are two cases here with identical bodies.
    ('tifa_limit_1_2_sub_4E3D51', [('field_1A', 'mul')], None),
    ('tifa_limit_2_1_sub_4E48D4', [('field_1A', 'mul')], None),
]

CAMERA_CASES = [
    ('battle_camera_position_sub_5C5B9C', [('n_frames', 'mul')], None),
    ('battle_camera_focal_sub_5C5F5E', [('n_frames', 'mul')], None),
    ('battle_camera_position_sub_5C557D', [('n_frames', 'mul')], None),
    ('battle_camera_focal_sub_5C5714', [('n_frames', 'mul')], None),
    ('battle_camera_position_sub_5C3D0D',
     [('n_frames', 'mul'), ('field_8', 'div'), ('field_6', 'div'),
      ('field_E', 'div')], None),
]

# --------------------------------------------------------------------------
# Which slots the pause-throttle must NOT touch.
#
# This is the one allow-by-default policy in this tree, and it is deliberate:
# it is what FFNx does. `execute_effect100_fn`'s final `else` gives an
# InterpolationEffectDecorator to every function it has not named, so the
# faithful transcription is "throttle unless excluded". It is also the single
# biggest risk in the job, which is why the throttle lives behind its own group
# and why every name below is cross-checked before a build will use it.
#
# Transcribed from src/ff7/battle/animations.cpp -- `execute_effect100_fn`'s
# arithmetic arms plus the sets built at the bottom of `ff7::battle::init`.
#
# The addresses come from the FFNx symbol-name suffixes rather than through the
# derivation chain, because none of these derive: FFNx reaches them through
# ff7_data.h assignments this tree does not evaluate. That is weaker footing, so
# ff7nx_locate.py cross-checks EVERY one against the 0x126D3A8 recompilation map
# -- an address that is a function entry point there is real, one that is not is
# a transcription error -- and additionally against the chain for the five that
# the chain does derive.
#
# NOT excluded, on purpose:
#
#   one_call_effect100_addresses (7)  FFNx gives these OneCallEffectDecorator,
#       which SKIPS the call rather than pausing it. Most retain the conservative
#       call-preserving pause path. Bahamut ZERO is the hardware-observed
#       exception and uses the explicitly stack-balanced exact skip listed in
#       EFFECT100_BALANCED_ONE_CALL below.
#
#   camera_effect100_addresses (25) and model_effect100_addresses (10)  FFNx
#       gives these Camera/ModelInterpolationEffectDecorator, both of which are
#       PauseEffectDecorator subclasses using the same pause trick. Throttling
#       them is the point of the exercise: 21 of the 25 cameras are summon,
#       limit-break and enemy-attack cameras.
#
#   kotr_excluded_frames (13) use FixCounterExceptionEffectDecorator.  These
#       are handled by the dedicated counter-hold path below, not by the pause
#       decorator: the knight function must execute on every rendered frame so
#       its model animation keeps moving, while its logical effect counter is
#       restored on the three repeated frames.
EFFECT100_NO_THROTTLE = [
    # the five arithmetic arms -- these get FFNx's default NoEffectDecorator
    'display_battle_action_text_42782A',
    'battle_sub_425D29',
    'battle_sub_5BDA0F',
    'tifa_limit_1_2_sub_4E3D51',
    'tifa_limit_2_1_sub_4E48D4',
    # sets a global rather than slot data; also no decorator
    'run_odin_steel_sub_4A9908',
    # fixed_effect100_addresses -- explicit NoEffectDecorator
    'battle_enemy_death_5BBD24',
    'battle_iainuki_death_5BCAAA',
    'battle_boss_death_5BC48C',
    'battle_melting_death_5BC21F',
    'battle_disintegrate_2_death_5BBA82',
    'battle_morph_death_5BC812',
    'run_summon_animations_5C0E4B',
    'run_summon_animations_script_5C1B81',
    'goblin_punch_flash_573291',
    # Ifrit used to be excluded because FFNx replaces the whole function.  In
    # this port the stock function is retained, so excluding it advances its
    # 15-fps phase counter on all four 60-fps frames and makes the model vanish.
    # Pause-throttling is the safe stock-code equivalent (correct duration;
    # interpolation can be added independently later).
    'vincent_limit_fade_effect_sub_5D4240',
    'cloud_limit_2_2_sub_467256',
    'battle_escape_magic_loop_5D602A',
]

# FFNx does not pause these thirteen Knights of the Round script functions.
# It calls them on every rendered frame, but restores effect100.field_2 on the
# three repeated frames (and suppresses nested effect registration).  At one
# counter value per affected knight it temporarily presents counter+1 during
# the call; without that exception the knight's own model animation stalls.
# A value of zero means that knight has no exceptional counter.
KOTR_COUNTER_EXCEPTIONS = [
    ('run_summon_kotr_knight_script_0',  0x47ABB0,  50),
    ('run_summon_kotr_knight_script_1',  0x47C793,  50),
    ('run_summon_kotr_knight_script_2',  0x47CBAE,   0),
    ('run_summon_kotr_knight_script_3',  0x47D976,  52),
    ('run_summon_kotr_knight_script_4',  0x47DD6A,  35),
    ('run_summon_kotr_knight_script_5',  0x47E05E,  14),
    ('run_summon_kotr_knight_script_6',  0x47E367,   0),
    ('run_summon_kotr_knight_script_7',  0x47EB92,  26),
    ('run_summon_kotr_knight_script_8',  0x47EFA0,  71),
    ('run_summon_kotr_knight_script_9',  0x47FB7D,  51),
    ('run_summon_kotr_knight_script_10', 0x47FFC2,  56),
    ('run_summon_kotr_knight_script_11', 0x48034E,  56),
    ('run_summon_kotr_knight_script_12', 0x480776, 112),
]

# FFNx's summon-specific ModelInterpolationEffectDecorator set.  The final
# value is stored as marker bit 3: zero means the normal 1000-unit smoothness
# threshold, eight means Alexander's 3000-unit threshold.  Chocobuckle and
# Barret's limit use related decorators but are intentionally not included in
# this summon-only path: their pause policy and actor selection differ.
# Quarantined after the first hardware run (build 214).  KOTR's separate
# counter-hold path was stable, but every tested entry through this actor-3
# position path (Shiva, Alexander and Bahamut ZERO) aborted the game.  The
# generic PauseEffectDecorator path already preserves the required 15 Hz
# logical pacing while still executing the stock function on every rendered
# frame; use that proven path until the Switch-native actor-state access can be
# established independently.  Keeping this list empty is intentional: the
# functions remain in EFFECT100_MODEL/candidates, so they are throttled rather
# than excluded and can still be selected in a future, separately gated model
# interpolation implementation.
SUMMON_MODEL_INTERPOLATION = []

QUARANTINED_SUMMON_MODEL_INTERPOLATION = [
    ('run_fat_chocobo_movement_509692', 0x509692, 0),
    ('run_bahamut_movement_49ADEC', 0x49ADEC, 0),
    ('run_bahamut_neo_movement_48D7BC', 0x48D7BC, 0),
    ('run_odin_gunge_movement_4A584D', 0x4A584D, 0),
    ('run_odin_steel_movement_4A6CB8', 0x4A6CB8, 0),
    ('run_phoenix_movement_518AFF', 0x518AFF, 0),
    ('run_chocomog_movement_50B1A3', 0x50B1A3, 0),
    ('run_bahamut_zero_movement_48BBFC', 0x48BBFC, 0),
    ('run_shiva_movement_592538', 0x592538, 0),
    ('run_alexander_movement_5078D8', 0x5078D8, 8),
]

# The functions FFNx DOES throttle, by name. They are not needed to build the
# exclusion table -- allow-by-default covers them -- but they are the addresses
# a bisect will want to move across one at a time, so they are carried through
# to the sites file and `--throttle-exclude` accepts any of them.
#
# camera_effect100_addresses: CameraInterpolationEffectDecorator. Twenty-one of
# these twenty-five are summon, limit-break and enemy-attack cameras -- they are
# the reason this whole mechanism exists, and the first place to look if the
# throttle turns out to be wrong somewhere.
EFFECT100_CAMERA = [
    'run_chocomog_camera_509B10',
    'run_fat_chocobo_camera_507CA4',
    'run_ifrit_camera_592A36',
    'run_shiva_camera_58E60D',
    'run_ramuh_camera_597206',
    'run_alexander_camera_501637',
    'run_bahamut_camera_497A37',
    'run_phoenix_camera_515238',
    'run_titan_camera_59B4B0',
    'run_hades_camera_4B65A8',
    'run_leviathan_camera_5B0716',
    'run_odin_gunge_camera_4A0F52',
    'run_odin_steel_camera_4A5D3C',
    'run_bahamut_neo_camera_48C75D',
    'run_kujata_camera_4F9A4D',
    'run_typhoon_camera_4D594C',
    'run_bahamut_zero_camera_483866',
    'run_kotr_camera_476AFB',
    'barret_limit_4_1_camera_4688A2',
    'aerith_limit_4_1_camera_473CC2',
    'enemy_atk_camera_sub_439EE0',
    'enemy_atk_camera_sub_44A7D2',
    'enemy_atk_camera_sub_44EDC0',
    'enemy_atk_camera_sub_4522AD',
    'enemy_atk_camera_sub_457C60',
]

# model_effect100_addresses: ModelInterpolationEffectDecorator, which uses the
# same pause trick with usePauseTrick=true. Plus the two functions FFNx gives a
# model decorator to by name rather than through the set.
EFFECT100_MODEL = [
    'run_fat_chocobo_movement_509692',
    'run_bahamut_movement_49ADEC',
    'run_bahamut_neo_movement_48D7BC',
    'run_odin_gunge_movement_4A584D',
    'run_odin_steel_movement_4A6CB8',
    'run_phoenix_movement_518AFF',
    'run_chocomog_movement_50B1A3',
    'run_bahamut_zero_movement_48BBFC',
    'run_shiva_movement_592538',
    'run_alexander_movement_5078D8',
    'run_chocobuckle_main_loop_560C32',
    'barret_limit_4_1_model_movement_4698EF',
]

EFFECT100_ONE_CALL = [
    'run_bahamut_zero_main_loop_484A16',
    'death_sentence_main_loop_5661A0',
    'roulette_skill_main_loop_566287',
    'bomb_blast_black_bg_effect_537427',
    'run_confu_main_loop_5600BE',
    'death_kill_sub_loop_5624A5',
    'death_kill_sub_loop_562C60',
]

# OneCallEffectDecorator truly skips the callback on repeated render frames;
# it is not a PauseEffectDecorator. Bahamut ZERO's main loop is the observed
# case where calling it with g_is_battle_paused set is visibly not equivalent:
# it still changes presentation state and makes the summon flicker.
#
# The dedicated pre-cave path cancels the translated guest `ESP -= 4`, writes
# the restored ESP, and then joins at the post-call hook. That is stack-balanced
# unlike the old experiment which skipped only the BL and leaked four guest
# bytes per held frame. Keep the allow-list narrow until another callback has a
# hardware-observed need for true call skipping.
EFFECT100_BALANCED_ONE_CALL = [
    ('run_bahamut_zero_main_loop_484A16', 0x484A16),
]

# execute_effect60_fn, same source file. The first seven are FFNx's arithmetic
# arms; this tree does not implement effect60 arithmetic scaling, so throttling
# them instead would change their duration by a factor this build cannot
# predict. They are excluded, which leaves them exactly as they are today.
# The rest are FFNx's explicit NoEffectDecorator list.
# battle_smoke_move_handler_5BE4E2 is OneCall and is deliberately absent.
EFFECT60_NO_THROTTLE = [
    'battle_sub_4276B6',
    'battle_sub_4255B7',
    'battle_sub_427737',
    'battle_sub_425AAD',
    'battle_sub_427AF1',
    'battle_sub_4277B1',
    'battle_sub_5BD96D',
    'battle_sub_425E5F',
    'battle_sub_5C1C8F',
    'battle_sub_5BCF9D',
    'handle_aura_effects_425520',
    'battle_boss_death_sub_5BC5EC',
    'battle_sub_5BCD42',
    'display_battle_damage_5BB410',
    'limit_break_aura_effects_5C0572',
    'enemy_skill_aura_effects_5C06BF',
    'summon_aura_effects_5C0953',
    'battle_sub_5C18BC',
]


DISPATCHERS = {
    'effect10': dict(fn='execute_effect10_fn', add='add_fn_to_effect10_fn',
                     arr='effect10_array_fn', data='effect10_array_data',
                     slots=10, stride=0x20, fields=F10, cases=EFFECT10_CASES,
                     unhandled=0,
                     what='character movement and resting positions -- '
                          'limit-break hit timing, attacker->target movement'),
    'effect100': dict(fn='execute_effect100_fn', add='add_fn_to_effect100_fn',
                      arr='effect100_array_fn', data='effect100_array_data',
                      slots=100, stride=0x20, fields=F100,
                      cases=EFFECT100_CASES, unhandled=7,
                      what='action / damage text hold duration'),
    'camera': dict(fn='execute_camera_functions', add='add_fn_to_camera_fn_array',
                   arr='camera_fn_array', data='camera_fn_data',
                   slots=16, stride=0x28, fields=FCAM, cases=CAMERA_CASES,
                   unhandled=0,
                   what='battle camera move durations -- magic, limit break, '
                        'battle intro, victory'),
    # effect60 has no arithmetic group here -- `cases` is empty, so it gets a
    # registration hook and the throttle caves and nothing else. Its seven
    # arithmetic arms are in EFFECT60_NO_THROTTLE, so they behave exactly as
    # they do today.
    'effect60': dict(fn='execute_effect60_fn', add='add_fn_to_effect60_fn',
                     arr='effect60_array_fn', data='effect60_array_data',
                     slots=60, stride=0x20, fields=F100, cases=[],
                     unhandled=0,
                     what='per-frame battle effects -- auras, smoke, damage '
                          'display, Vincent limit camera'),
}

# Which dispatchers the pause-throttle can be built for, and how many
# undecorated indirect call sites each is expected to have.
#
#   effect100  two call sites. The second is the
#              `else if (fn == display_battle_action_text_42782A)` arm, which
#              FFNx calls with no decorator at all. It is located so that it can
#              be asserted still present, and then left untouched.
#   effect60   one call site.
#
# effect10 and camera are absent on purpose: FFNx gives neither dispatcher any
# decorator, so there is nothing to throttle.
# The two camera script interpreters, and the array each one's wait state lives
# in. Both are stepped once per frame by `handle_camera_functions`, and both
# need opcode 0xF5's wait scaling -- FFNx wraps both, for the same reason.
# The two effect60 slots the AURA system needs throttled, and only these.
#
# `run_aura_effects_5C0230` registers 0x5C0AFF into effect60. That function is
# not an aura -- it is a SPAWNER:
#
#     if (g_is_battle_paused) return;
#     if ((data.field_2 & 3) == 0)
#         add_fn_to_effect60_fn(data.field_1C);   // spawn an aura instance
#     data.field_2++;
#     if (old field_2 == 0xC) data.field_0 = 0xFFFF;
#
# so it spawns three instances over twelve ticks and retires. `data.field_1C` is
# the per-type handler chosen by a jump table: 0x5C0300 magic, 0x5C0572 limit
# break, 0x5C06BF enemy skill. (Summon is different -- 0x5C0953 is pushed
# straight into effect60 by handle_summon_aura_5C0850.)
#
# FFNx names 0x5C0572 and 0x5C06BF in its effect60 NoEffectDecorator list, so
# those two run at full rate with scaled constants -- `limit-aura` and
# `aura-eskill`. It does NOT name the spawner or the magic handler, so both get
# an InterpolationEffectDecorator: the pause-throttle.
#
# Getting the spawner wrong is what produced BOTH reported symptoms at once.
# Unthrottled it spawns all three instances in twelve frames instead of
# forty-eight, so the aura "happens super fast"; and once `aura-eskill` made
# each instance last four times longer, three of them stacked inside those
# twelve frames and the last one outlived the attack, so the aura on the target
# "happens super slow". One unthrottled spawner, two opposite-looking symptoms.
#
# Both entries are verified as function entry points in the 0x126D3A8 map, the
# same as every other address in this file. Neither has an FFNx symbol name --
# FFNx reaches them by not naming them -- so the names here are descriptive, and
# the address in the suffix is what is checked.
# Every effect60 slot function, recovered by scanning ff7_en for
# `push imm32 ; call add_fn_to_effect60_fn`. 91 of them. Twenty are named by
# FFNx (full rate, constants scaled); the other seventy-one it throttles.
# `aura-throttle` ships two. This list exists so --throttle-only can validate an
# address instead of taking it on faith, and so --list-effect60 can show what is
# still running at full rate.
EFFECT60_SLOTS = [
    0x42517B, 0x425520, 0x4255B7, 0x425AAD, 0x425E5F, 0x42675F, 0x4276B6,
    0x427737, 0x4277B1, 0x427AF1, 0x43870E, 0x4389B6, 0x438D93, 0x4391BC,
    0x43937F, 0x439592, 0x439774, 0x443EF8, 0x455695, 0x45CF2A, 0x45E3CE,
    0x45E79C, 0x4615D5, 0x46E946, 0x46E9F9, 0x46EEA6, 0x46F50E, 0x46F63D,
    0x46F8C9, 0x46F95D, 0x46FA89, 0x46FD49, 0x4BEB0B, 0x4BF111, 0x4BF647,
    0x4C02E5, 0x4C06D8, 0x4C14FD, 0x4C5A8A, 0x4C61E9, 0x4DBD38, 0x4DD68E,
    0x4DEBA4, 0x4E00D1, 0x50C61D, 0x50DB2E, 0x50F830, 0x511861, 0x511C99,
    0x51BE08, 0x51F4ED, 0x524AEE, 0x52644C, 0x5273D4, 0x527EB5, 0x52B813,
    0x536139, 0x536C9E, 0x537E84, 0x538118, 0x538A12, 0x539482, 0x53ADB8,
    0x53AEB9, 0x53B7A2, 0x53E1A1, 0x53E3A3, 0x544C22, 0x54596F, 0x547AC3,
    0x549906, 0x54A262, 0x54A333, 0x550B14, 0x55133E, 0x5BB410, 0x5BC5EC,
    0x5BCD42, 0x5BCF9D, 0x5BD96D, 0x5BE4E2, 0x5BE5A9, 0x5BE760, 0x5BE8A4,
    0x5BF41F, 0x5BF7F6, 0x5C0850, 0x5C0953, 0x5C0AFF, 0x5C18BC, 0x5C1C8F,
]

EFFECT60_AURA_THROTTLE = [
    'aura_spawner_5C0AFF',
    'magic_aura_effects_5C0300',
]

CAMERA_WAIT = {
    'camera-position': dict(fn='set_camera_position_scripts',
                            arr='battle_camera_position',
                            what='camera POSITION script waits -- magic, limit '
                                 'break, summon, enemy attack, battle intro'),
    'camera-focal': dict(fn='set_camera_focal_position_scripts',
                         arr='battle_camera_focal_point',
                         what='camera FOCAL POINT script waits -- the same '
                              'scripts, aiming rather than placing'),
}

THROTTLE = {
    'effect100': dict(exclude=EFFECT100_NO_THROTTLE,
                      one_call=EFFECT100_ONE_CALL,
                      throttled=EFFECT100_CAMERA + EFFECT100_MODEL,
                      undecorated='display_battle_action_text_42782A',
                      n_undecorated=1),
    'effect60': dict(exclude=EFFECT60_NO_THROTTLE,
                     one_call=['battle_smoke_move_handler_5BE4E2'],
                     throttled=['vincent_limit_satan_slam_camera_45CF2A'],
                     undecorated=None,
                     n_undecorated=0),
}




def add_lsl(rd, rn, rm, sh):
    """add Wd, Wn, Wm, lsl #sh"""
    return 0x0B000000 | (sh << 10) | (rm << 16) | (rn << 5) | rd


def idx_scale_words(stride):
    if stride == 0x20:
        return [A.lsl(16, 16, 5)]
    if stride == 0x28:
        return [add_lsl(16, 16, 16, 2), A.lsl(16, 16, 3)]
    raise SystemExit('unsupported stride 0x%X' % stride)


def build_cave_reference(cave, site, flag_addr, mask_bits, data_base, stride,
                         fields, cases, sym, k):
    """
    The ORIGINAL one-body-per-case dispatcher cave, kept verbatim.

    This is not dead code and must not be deleted: it is the reference that
    test_dispatch_shrink.py diffs the compact `build_cave` against, by
    executing both in arm64emu.py and comparing full machine state. It is the
    shape that has been on hardware, so "the compact cave is correct" means
    exactly "it does what this does".

    Nothing in a build calls it.

        ldr   w16, [ctx, #idx_off]     ; idx, from the guest register slot
        and   w16, w16, #mask          ; provably inside the flag block
        adrp  x17, page(flags)
        add   x17, x17, #lo12(flags)
        add   x17, x17, x16            ; &flag[idx]
        ldrb  w16, [x17]
        cbz   w16, OUT                 ; not the first frame -- do nothing
        strb  wzr, [x17]               ; consume the flag
        ldr   w16, [ctx, #idx_off]     ; idx again (translate preserves guest state)
        and   w16, w16, #mask
        <idx * stride>
        movz  w17, #lo16(data_base)
        movk  w17, #hi16(data_base), lsl #16
        add   w0,  w17, w16            ; guest &array_data[idx]
        sub   sp,  sp,  #0x10
        str   xF,  [sp]                ; the fn pointer must survive the call
        bl    0x10FC3A0                ; x0 = host &array_data[idx]
        ldr   xF,  [sp]
        add   sp,  sp,  #0x10
        <dispatch on wF, scaling fields of [x0]>
    OUT:
        <displaced instruction, replayed verbatim>
        b     hook + 4
    """
    F = site['fn_reg']
    ctx = site['ctx_reg']
    io = site['idx_off']
    w = []

    def pc(i=None):
        return cave + 4 * (len(w) if i is None else i)

    w.append(A.ldr(16, ctx, io))
    w.append(A.and_mask(16, 16, mask_bits))
    w.append(A.adrp(17, pc(), flag_addr & ~0xFFF))
    w.append(A.add_imm64(17, 17, flag_addr & 0xFFF))
    w.append(A.add_reg64(17, 17, 16))
    w.append(A.ldrb(16, 17, 0))
    cbz_i = len(w)
    w.append(0)                                     # cbz w16, OUT -- patched
    w.append(A.strb(A.WZR, 17, 0))
    w.append(A.ldr(16, ctx, io))
    w.append(A.and_mask(16, 16, mask_bits))
    w += idx_scale_words(stride)
    w.append(A.movz(17, data_base & 0xFFFF))
    w.append(A.movk_hi(17, (data_base >> 16) & 0xFFFF))
    w.append(A.add_reg(0, 17, 16))
    w.append(A.sub_imm64(A.SP, A.SP, 0x10))
    w.append(A.str64(F, A.SP, 0))
    w.append(A.bl(pc(), TRANSLATE))
    w.append(A.ldr64(F, A.SP, 0))
    w.append(A.add_imm64(A.SP, A.SP, 0x10))

    # ---- dispatch -------------------------------------------------------
    out_jumps = []
    for name, spec, guard in cases:
        target = sym[name]
        w.append(A.movz(17, target & 0xFFFF))
        w.append(A.movk_hi(17, (target >> 16) & 0xFFFF))
        w.append(A.cmp_reg(F, 17))
        bne_i = len(w)
        w.append(0)                                 # b.ne next -- patched
        body_start = len(w)
        guard_i = None
        if guard == 'n_frames>1':
            off, width, _s = fields['n_frames']
            w.append(A.ldrsh(17, 0, off))
            w.append(A.cmp_imm(17, 1))
            guard_i = len(w)
            w.append(0)                             # b.le skip -- patched
        elif guard is not None:
            raise SystemExit('unknown guard %r' % guard)
        w += _ops(fields, spec, k)
        if guard_i is not None:
            w[guard_i] = A.bcond(cave + 4 * guard_i, pc(), A.LE)
        out_jumps.append(len(w))
        w.append(0)                                 # b OUT -- patched
        w[bne_i] = A.bcond(cave + 4 * bne_i, pc(), A.NE)
        assert len(w) > body_start

    out = pc()
    w[cbz_i] = A.cbz(16, cave + 4 * cbz_i, out)
    for i in out_jumps:
        w[i] = A.b(cave + 4 * i, out)
    w.append(site['displaced'])
    w.append(A.b(pc(), site['hook'] + 4))
    return w


def _field_span(fields, name):
    off, width, _sign = fields[name]
    return range(off, off + width // 8)


def _plan_dispatch(fields, cases, sym):
    """
    Decide the shape of the dispatch before emitting anything.

    Returns (groups, shared), where `groups` is a list of
    [targets, spec, guard] and `shared` is one ('field', how) op that has been
    lifted out of every unguarded group and will be emitted once.

    TWO REWRITES HAPPEN HERE, AND BOTH REST ON A CHECKED PRECONDITION.

    1. Cases with an identical body are merged into one group with several
       compares.  Legal because the compares are `wFn == target` against
       DISTINCT targets, so at most one can ever match: which compare found
       the match cannot change what runs, and the order they are tried in
       cannot either.  `targets are distinct` is asserted, not assumed.

    2. One op common to every unguarded group is moved to the end of each of
       those groups so it can be emitted once and fallen into.  Legal only
       because the ops within a case write DISJOINT BYTE RANGES of the slot's
       data block -- each is a load/modify/store of one field -- so they
       commute.  The byte ranges are computed from the field table and the
       overlap check is an assert.

    Guarded groups are excluded from the second rewrite on purpose.  A guard
    is `if (n_frames > 1)`, and the op being lifted is usually the n_frames
    scale itself; letting a guarded case fall into shared code would run the
    scale on the path FFNx leaves alone.
    """
    if not cases:
        raise SystemExit('build_cave: no arithmetic cases for this dispatcher '
                         '-- a scaler cave with nothing to dispatch on would '
                         'spend a translator call to do nothing')
    targets = [sym[name] for name, _spec, _guard in cases]
    if len(set(targets)) != len(targets):
        raise SystemExit('dispatch cases have duplicate target addresses; '
                         'merging bodies would not be safe')

    groups = []
    index = {}
    for name, spec, guard in cases:
        span = set()
        for fname, _how in spec:
            bytes_ = set(_field_span(fields, fname))
            if bytes_ & span:
                raise SystemExit(
                    'case %s writes overlapping fields (%s); the ops do not '
                    'commute and must not be reordered' % (name, fname))
            span |= bytes_
        key = (tuple(spec), guard)
        if key in index:
            groups[index[key]][0].append(sym[name])
        else:
            index[key] = len(groups)
            groups.append([[sym[name]], list(spec), guard])

    shared = None
    unguarded = [g for g in groups if g[2] is None]
    if len(unguarded) > 1:
        common = [op for op in unguarded[0][1]
                  if all(op in g[1] for g in unguarded)]
        if common:
            shared = common[0]
            for g in unguarded:
                g[1].remove(shared)
    return groups, shared


def build_cave(cave, site, flag_addr, mask_bits, data_base, stride,
               fields, cases, sym, k):
    """
    Emit the dispatcher cave. `cave` is the module offset the words land at.

        ldr   w16, [ctx, #idx_off]     ; idx, from the guest register slot
        and   w16, w16, #mask          ; provably inside the flag block
        adrp  x17, page(flags)
        add   x17, x17, #lo12(flags)
        add   x17, x17, x16            ; &flag[idx]
        ldrb  w16, [x17]
        cbz   w16, OUT                 ; not the first frame -- do nothing
        strb  wzr, [x17]               ; consume the flag
        ldr   w16, [ctx, #idx_off]     ; idx again (translate preserves guest state)
        and   w16, w16, #mask
        <idx * stride>
        movz  w17, #lo16(data_base)
        movk  w17, #hi16(data_base), lsl #16
        add   w0,  w17, w16            ; guest &array_data[idx]
        sub   sp,  sp,  #0x10
        str   xF,  [sp]                ; the fn pointer must survive the call
        bl    0x10FC3A0                ; x0 = host &array_data[idx]
        ldr   xF,  [sp]
        add   sp,  sp,  #0x10
        <dispatch on wF, scaling fields of [x0]>
    OUT:
        <displaced instruction, replayed verbatim>
        b     hook + 4

    THE DISPATCH IS LAID OUT TO SHARE CODE
    --------------------------------------
    The cave region is the page-alignment gap between the end of .text and
    .rodata -- 2464 bytes for every cave in the build, and .text has no
    internal padding to borrow from.  So the dispatch is emitted as:

        <groups that do not use the shared op>   ... b OUT
        <groups that do>                         ... b SHARED
        SHARED: <the shared op>                  (falls through)
        OUT:

    with the last group of each run falling through instead of branching.
    `_plan_dispatch` decides the grouping and checks the two preconditions
    that make it legal.  The result is diffed against `build_cave_reference`
    by executing both -- see test_dispatch_shrink.py.
    """
    F = site['fn_reg']
    ctx = site['ctx_reg']
    io = site['idx_off']
    w = []

    def pc(i=None):
        return cave + 4 * (len(w) if i is None else i)

    w.append(A.ldr(16, ctx, io))
    w.append(A.and_mask(16, 16, mask_bits))
    w.append(A.adrp(17, pc(), flag_addr & ~0xFFF))
    w.append(A.add_imm64(17, 17, flag_addr & 0xFFF))
    w.append(A.add_reg64(17, 17, 16))
    w.append(A.ldrb(16, 17, 0))
    cbz_i = len(w)
    w.append(0)                                     # cbz w16, OUT -- patched
    w.append(A.strb(A.WZR, 17, 0))
    w.append(A.ldr(16, ctx, io))
    w.append(A.and_mask(16, 16, mask_bits))
    w += idx_scale_words(stride)
    w.append(A.movz(17, data_base & 0xFFFF))
    w.append(A.movk_hi(17, (data_base >> 16) & 0xFFFF))
    w.append(A.add_reg(0, 17, 16))
    w.append(A.sub_imm64(A.SP, A.SP, 0x10))
    w.append(A.str64(F, A.SP, 0))
    w.append(A.bl(pc(), TRANSLATE))
    w.append(A.ldr64(F, A.SP, 0))
    w.append(A.add_imm64(A.SP, A.SP, 0x10))

    # ---- dispatch -------------------------------------------------------
    groups, shared = _plan_dispatch(fields, cases, sym)
    # Groups that end at OUT first, then the ones that end at SHARED, so the
    # last of each run can fall through instead of branching.
    plain = [g for g in groups if shared is None or g[2] is not None]
    sharing = [g for g in groups if shared is not None and g[2] is None]
    ordered = plain + sharing

    out_jumps = []          # word indices of `b OUT`, patched once OUT is known
    shared_jumps = []       # word indices of `b SHARED`
    guard_jumps = []        # word indices of `b.le OUT`
    next_patches = []       # (word index, group index it must skip to)
    group_start = []

    for gi, (tgts, spec, guard) in enumerate(ordered):
        group_start.append(len(w))
        last_group = (gi == len(ordered) - 1)
        falls_into_shared = (shared is not None and guard is None
                             and gi == len(ordered) - 1)
        eq_jumps = []
        for ti, target in enumerate(tgts):
            w.append(A.movz(17, target & 0xFFFF))
            w.append(A.movk_hi(17, (target >> 16) & 0xFFFF))
            w.append(A.cmp_reg(F, 17))
            if ti < len(tgts) - 1:
                eq_jumps.append(len(w))
                w.append(0)                         # b.eq BODY -- patched
            else:
                next_patches.append((len(w), gi + 1))
                w.append(0)                         # b.ne next -- patched
        body = len(w)
        for i in eq_jumps:
            w[i] = A.bcond(cave + 4 * i, cave + 4 * body, A.EQ)

        if guard == 'n_frames>1':
            off, _width, _s = fields['n_frames']
            w.append(A.ldrsh(17, 0, off))
            w.append(A.cmp_imm(17, 1))
            guard_jumps.append(len(w))
            w.append(0)                             # b.le OUT -- patched
        elif guard is not None:
            raise SystemExit('unknown guard %r' % guard)

        w += _ops(fields, spec, k)

        if shared is not None and guard is None:
            if not falls_into_shared:
                shared_jumps.append(len(w))
                w.append(0)                         # b SHARED -- patched
        elif not last_group:
            out_jumps.append(len(w))
            w.append(0)                             # b OUT -- patched
        # a last group with no shared block falls straight through to OUT

    shared_at = len(w)
    if shared is not None:
        w += _ops(fields, [shared], k)

    out = pc()
    w[cbz_i] = A.cbz(16, cave + 4 * cbz_i, out)
    for i in out_jumps:
        w[i] = A.b(cave + 4 * i, out)
    for i in guard_jumps:
        w[i] = A.bcond(cave + 4 * i, out, A.LE)
    for i in shared_jumps:
        w[i] = A.b(cave + 4 * i, cave + 4 * shared_at)
    for i, gi in next_patches:
        # The LAST group's failing compare means nothing matched at all, so it
        # goes to OUT -- never to SHARED, which would apply the shared scale to
        # a function FFNx does not name.
        nxt = cave + 4 * group_start[gi] if gi < len(ordered) else out
        w[i] = A.bcond(cave + 4 * i, nxt, A.NE)

    w.append(site['displaced'])
    w.append(A.b(pc(), site['hook'] + 4))
    return w


def build_addfn_cave(cave, site, flag_addr, mask_bits, throttle=None):
    """
    Emit the registration cave: flag[idx] = 1, then replay the store.

        ldr   w16, [ctx, #idx_off]
        and   w16, w16, #mask
        adrp  x17, page(flags)
        add   x17, x17, #lo12(flags)
        add   x17, x17, x16
        movz  w16, #1
        strb  w16, [x17]
        <displaced: str wFN, [x0]>
        b     hook + 4

    No call, so x0 -- which the displaced store needs -- is untouched, and no
    stack frame is required.

    With `throttle` given, the same cave ALSO decides, once per registration,
    whether this slot is one the pause-throttle applies to, and seeds the slot's
    throttle byte accordingly. See `_addfn_throttle_words` for why that decision
    belongs here and not in the first-frame cave.

    `flag_addr` may be None: a build that enables the throttle group without the
    first-frame scaler needs the registration hook, but has no flag block to
    write. With `flag_addr=None` and `throttle=None` this function has nothing to
    do and refuses rather than emitting a cave that only replays an instruction.
    """
    if flag_addr is None and throttle is None:
        raise SystemExit('build_addfn_cave: neither a flag block nor a throttle '
                         'block was asked for -- nothing to emit')
    ctx, io = site['ctx_reg'], site['idx_off']
    w = []
    # Knights of the Round calls add_fn from inside each repeated knight
    # frame.  FFNx deliberately suppresses those nested registrations while
    # its logical counter is held.  Branch to the stock function's common
    # return path before either our registration bookkeeping or the displaced
    # array store.  No other dispatcher supplies these keys, so its behaviour
    # remains byte-for-byte unchanged.
    if throttle is not None and throttle.get('kotr'):
        dis = throttle['kotr_disable']
        w += [A.adrp(17, cave + 4 * len(w), dis & ~0xFFF),
              A.add_imm64(17, 17, dis & 0xFFF),
              A.ldrb(16, 17, 0)]
        enabled_i = len(w)
        w.append(0)                                  # cbz -> normal registration
        w.append(A.b(cave + 4 * len(w), throttle['kotr_add_skip']))
        w[enabled_i] = A.cbz(16, cave + 4 * enabled_i,
                             cave + 4 * len(w))
    if flag_addr is not None:
        w += [A.ldr(16, ctx, io),
              A.and_mask(16, 16, mask_bits),
              A.adrp(17, cave + 8, flag_addr & ~0xFFF),
              A.add_imm64(17, 17, flag_addr & 0xFFF),
              A.add_reg64(17, 17, 16),
              A.movz(16, 1),
              A.strb(16, 17, 0)]
    table_at = None
    if throttle is not None:
        w, table_at = _addfn_throttle_words(cave, w, site, mask_bits, throttle,
                                            throttle.get('allow', False))
    w.append(site['displaced'])
    w.append(A.b(cave + 4 * len(w), site['hook'] + 4))
    if table_at is not None:
        # The literal table is emitted immediately after the branch back, so it
        # is inside the cave (adr reach) and can never be executed.
        assert len(w) == table_at, (len(w), table_at)
        w += list(throttle['table']) + [0]
        # Packed as exception:8 | guest_function:24.  Guest addresses in this
        # binary are below 0x01000000, so the format is lossless; zero remains
        # available as the sentinel and exception zero means "no exception".
        w += [((exc & 0xFF) << 24) | (va & 0xFFFFFF)
              for _name, va, exc in throttle.get('kotr', ())] + [0]
        # Packed as marker-bits:8 | guest_function:24.  Bit 5 identifies the
        # model decorator; bit 3 selects Alexander's 3000-unit threshold.
        w += [((marker & 0xFF) << 24) | (va & 0xFFFFFF)
              for _name, va, marker in throttle.get('model', ())] + [0]
        # Exact OneCall callbacks use a plain, sentinel-terminated address
        # table. Marker 0x10 is supplied by the registration cave itself.
        w += [va for _name, va in throttle.get('one_call', ())] + [0]
    return w


# ==========================================================================
# The pause-throttle decorator
# ==========================================================================
#
# WHAT IS STILL WRONG AND WHY THIS IS IT
# --------------------------------------
# The first-frame scalers above reproduce the FIVE arithmetic branches of
# FFNx's execute_effect100_fn. They are not the whole function. Seven other
# branches install an *effect decorator*, and the final `else` catches every
# effect100 function FFNx does not name:
#
#     else aux_effect100_handler[fn_index].setEffectDecorator(
#              std::make_shared<InterpolationEffectDecorator>(
#                  battle_frame_multiplier, ff7_externals.g_is_battle_paused));
#
# Strip the smoothing out of InterpolationEffectDecorator, ModelInterpolation-
# EffectDecorator and CameraInterpolationEffectDecorator and the same skeleton
# is left in all three:
#
#     byte wasPaused = *isBattlePaused;
#     if (frameCounter % frequency == 0) { fn(); }         // a real step
#     else { *isBattlePaused = 1; fn(); *isBattlePaused = wasPaused; }
#     frameCounter++;
#
# Everything else those classes do -- rotation matrices, palettes, colours,
# model and camera positions -- is interpolation, there to hide the fact that
# the effect now steps at 15 Hz on a 60 Hz screen. The PACING is entirely the
# pause trick.
#
# So on a build without this, every unnamed effect100 and effect60 slot function
# runs four times too fast, which predicts exactly the two symptoms left:
#
#   * magic / limit-break / summon cameras race. Those cameras are NOT in
#     execute_camera_functions -- they are effect100 slot functions, all 25 of
#     them in FFNx's camera_effect100_addresses. That is why `camera-scale`
#     fixed the battle-intro camera and nothing else.
#   * limit-break damage lands before the animation finishes. The animation
#     comes from battle.lgp's ?ab scripts and is correctly scaled x4; the
#     effect100 slot function that decides WHEN the damage applies is not, so it
#     finishes early.
#
# WHY THE PAUSE TRICK AND NOT "SKIP THE CALL"
# -------------------------------------------
# OneCallEffectDecorator simply does not call on skipped frames, and that is
# tempting because it is simpler. It is also the exact failure that made
# `--cam-throttle` crash on entering battle. The guest return address is pushed
# BEFORE the call:
#
#     ldr w0, [x0]              ; w0 = the guest function pointer
#     sub w8, w8, #4            ; guest ESP -= 4
#     str w8, [x22, #0x10]      ; the push
#     bl  #0xa1a0               ; indirect call thunk
#
# Skipping only the `bl` leaks four bytes of guest stack per skipped frame. The
# generic pause trick therefore never changes control flow. The one deliberate
# exception is EFFECT100_BALANCED_ONE_CALL: its pre-cave restores those four
# bytes and writes the corrected ESP before branching over the thunk.
#
# INTERPOLATION SCOPE
# -------------------
# Generic effect100/effect60 entries retain correct 15 Hz pacing on the 60 Hz
# render loop.  FFNx's ten summon movement functions are different: their
# ModelInterpolationEffectDecorator advances once, then displays three
# interpolated actor-3 positions.  The effect100 cave below now carries the
# equivalent fixed per-slot state directly in BSS (including Alexander's 3000
# unit teleport threshold and deferred final-frame retirement).  KOTR uses its
# separate counter-hold decorator because its thirteen knight scripts animate
# by a different contract.  This keeps interpolation narrowly matched to FFNx
# instead of guessing that every visual effect owns the same kind of position.
#
# STATE
# -----
# One byte per slot, in the BSS block appended after the first-frame flags:
#
#     0x80  this slot's function is on the exclusion list -- never throttle
#     0x40  this slot uses KOTR's counter-hold decorator
#     0x20  this slot uses model interpolation (currently quarantined/unused)
#     0x10  this slot uses the exact, stack-balanced OneCall decorator
#     0..3  the phase; a real step happens on phase 0
#
# The byte is seeded at REGISTRATION, in the add_fn cave, not on the first
# frame. Two reasons. It keeps `build_cave` -- the only part of this tree with
# hardware history -- byte-for-byte unchanged. And it is where FFNx puts it:
# `aux_effect100_handler[idx] = AuxiliaryEffectHandler()` runs in add_fn, so a
# slot freed and re-registered with the same function in the same frame gets a
# fresh counter, which is the common case for repeated attacks.
#
# The phase counter wraps 0->1->2->3->0 via `and #3`, so it can never reach 0x80
# and turn a throttled slot into an excluded one.


def _addfn_throttle_words(cave, w, site, mask_bits, throttle, allow=False):
    """
    Append to the registration cave:  ctr[idx] = excluded(fn) ? 0x80 : 0.

        sub  sp, sp, #0x10
        str  x0, [sp]              ; x0 is live for the displaced store
        adr  x17, TABLE
        movz w16, #0               ; result: not excluded
      L:
        ldr  w0, [x17], #4
        cbz  w0, DONE              ; sentinel -- ran off the end
        cmp  w0, wFN
        b.ne L
        movz w16, #0x80            ; matched -- excluded
      DONE:
        ldr  w0, [ctx, #idx_off]
        and  w0, w0, #mask
        adrp x17, page(ctr)
        add  x17, x17, #lo12(ctr)
        add  x17, x17, x0
        strb w16, [x17]
        ldr  x0, [sp]
        add  sp, sp, #0x10

    x16 and x17 are free -- the hook sits on the instruction after a
    `bl 0x10FC3A0`, so IP0/IP1 are dead by the AAPCS. A third scratch register
    is needed for the table walk and there is not one, so x0 is borrowed across
    a 16-byte stack frame and put back before the displaced store uses it. No
    call happens in this cave, so nothing else can observe the borrow.

    Returns (words, table_index) where table_index is the word index the literal
    table must start at, i.e. two words on (displaced + branch back).
    """
    ctx, io = site['ctx_reg'], site['idx_off']
    fn = site['fn_val_reg']
    if fn in (0, 16, 17):
        raise SystemExit('refusing to emit a throttle registration cave: the '
                         'function-pointer register w%d is one this cave uses '
                         'as scratch' % fn)
    if ctx in (0, 16, 17):
        raise SystemExit('refusing to emit a throttle registration cave: the '
                         'guest context register x%d is one this cave uses as '
                         'scratch' % ctx)
    ctr = throttle['ctr']

    def pc(i=None):
        return cave + 4 * (len(w) if i is None else i)

    # Deny-by-default (`allow=False`, the original): the table lists functions
    # NOT to throttle, so a miss means "throttle". Allow-by-default is what FFNx
    # does for effect100, where the throttle is nearly universal.
    #
    # Allow-list (`allow=True`): the table lists the ONLY functions to throttle.
    # effect60 needs this. Throttling all 60 of its slots reproduces FFNx's
    # rule, but FFNx pairs it with interpolation that smooths the visual back to
    # 60 Hz, and effect60 IS the per-frame visual layer -- auras, smoke,
    # sparkles, dozens at once. Without the smoothing the whole battle steps at
    # 15 Hz, which is a worse bug than the one being fixed. Naming the two
    # functions that actually need it keeps the fix and drops the blast radius.
    #
    # The two forms differ by exactly which constant is the default and which is
    # the match, so they share every other word of the cave.
    miss_val, hit_val = (0x80, 0) if allow else (0, 0x80)
    w.append(A.sub_imm64(A.SP, A.SP, 0x10))
    w.append(A.str64(0, A.SP, 0))
    adr_i = len(w)
    w.append(0)                                       # adr x17, TABLE -- patched
    w.append(A.movz(16, miss_val))
    loop_i = len(w)
    w.append(A.ldr_post(0, 17, 4))
    cbz_i = len(w)
    w.append(0)                                       # cbz w0, DONE -- patched
    w.append(A.cmp_reg(0, fn))
    w.append(A.bcond(pc(), cave + 4 * loop_i, A.NE))
    w.append(A.movz(16, hit_val))
    done = pc()
    w[cbz_i] = A.cbz(0, cave + 4 * cbz_i, done)
    # Keep the ordinary exclusion result while the optional KOTR table borrows
    # w16 for its masked function comparison.
    special = throttle.get('kotr', ())
    model = throttle.get('model', ())
    one_call = throttle.get('one_call', ())
    if special or model or one_call:
        w.append(A.str_(16, A.SP, 8))
    if special:
        special_adr_i = len(w)
        w.append(0)                                  # adr x17,KOTR_TABLE
        special_loop_i = len(w)
        w.append(A.ldr_post(0, 17, 4))
        special_end_i = len(w)
        w.append(0)                                  # cbz -> ordinary result
        w.append(A.and_mask(16, 0, 24))
        w.append(A.cmp_reg(16, fn))
        w.append(A.bcond(pc(), cave + 4 * special_loop_i, A.NE))
        # A match is the KOTR marker (bit 6), and its high byte is saved in the
        # per-slot exception array.  This includes zero for the two knights
        # without an exceptional frame, clearing stale state on slot reuse.
        w.append(A.lsr(16, 0, 24))
        w.append(A.ldr(0, ctx, io))
        w.append(A.and_mask(0, 0, mask_bits))
        exc = throttle['kotr_exc']
        w.append(A.adrp(17, pc(), exc & ~0xFFF))
        w.append(A.add_imm64(17, 17, exc & 0xFFF))
        w.append(A.add_reg64(17, 17, 0))
        w.append(A.strb(16, 17, 0))
        w.append(A.movz(16, 0x40))
        special_done_jump = len(w)
        w.append(0)                                  # b -> final slot store
        special_miss = pc()
        w[special_end_i] = A.cbz(0, cave + 4 * special_end_i, special_miss)
        # Do not clear a stale exception byte here.  It is unreachable unless
        # this slot's counter marker has bit 6 set, and every KOTR registration
        # overwrites the byte before setting that marker.  Omitting the dead
        # cleanup saves seven words in the tightly bounded dispatcher cave.
    else:
        special_adr_i = special_done_jump = None

    # FFNx gives these ten summon movement routines a model-position
    # decorator rather than the generic pause decorator.  A table match seeds
    # marker bit 5; the optional high-byte bit 3 is Alexander's larger
    # teleport threshold.  Phase and retirement state are reset lazily on the
    # first invocation, after registration has made the marker authoritative.
    if model:
        model_adr_i = len(w)
        w.append(0)                                  # adr x17,MODEL_TABLE
        model_loop_i = len(w)
        w.append(A.ldr_post(0, 17, 4))
        model_end_i = len(w)
        w.append(0)                                  # cbz -> ordinary result
        w.append(A.and_mask(16, 0, 24))
        w.append(A.cmp_reg(16, fn))
        w.append(A.bcond(pc(), cave + 4 * model_loop_i, A.NE))
        w.append(A.lsr(16, 0, 24))
        w.append(A.add_imm(16, 16, 0x20))
        model_done_jump = len(w)
        w.append(0)                                  # b -> final slot store
        model_miss = pc()
        w[model_end_i] = A.cbz(0, cave + 4 * model_end_i, model_miss)
    else:
        model_adr_i = model_done_jump = None

    # Exact OneCall callbacks are stored as plain guest addresses. Marker bit
    # 4 selects the stack-balanced skip path; its low bits remain the phase.
    if one_call:
        one_call_adr_i = len(w)
        w.append(0)                                  # adr x17,ONE_CALL_TABLE
        one_call_loop_i = len(w)
        w.append(A.ldr_post(0, 17, 4))
        one_call_end_i = len(w)
        w.append(0)                                  # cbz -> ordinary result
        w.append(A.cmp_reg(0, fn))
        w.append(A.bcond(pc(), cave + 4 * one_call_loop_i, A.NE))
        w.append(A.movz(16, 0x10))
        one_call_done_jump = len(w)
        w.append(0)                                  # b -> final slot store
        one_call_miss = pc()
        w[one_call_end_i] = A.cbz(0, cave + 4 * one_call_end_i,
                                   one_call_miss)
    else:
        one_call_adr_i = one_call_done_jump = None

    if special or model or one_call:
        w.append(A.ldr(16, A.SP, 8))                 # ordinary exclusion result

    final_store = pc()
    if special_done_jump is not None:
        w[special_done_jump] = A.b(cave + 4 * special_done_jump, final_store)
    if model_done_jump is not None:
        w[model_done_jump] = A.b(cave + 4 * model_done_jump, final_store)
    if one_call_done_jump is not None:
        w[one_call_done_jump] = A.b(cave + 4 * one_call_done_jump, final_store)
    w.append(A.ldr(0, ctx, io))
    w.append(A.and_mask(0, 0, mask_bits))
    w.append(A.adrp(17, pc(), ctr & ~0xFFF))
    w.append(A.add_imm64(17, 17, ctr & 0xFFF))
    w.append(A.add_reg64(17, 17, 0))
    w.append(A.strb(16, 17, 0))
    w.append(A.ldr64(0, A.SP, 0))
    w.append(A.add_imm64(A.SP, A.SP, 0x10))
    table_at = len(w) + 2                             # + displaced + branch back
    w[adr_i] = A.adr(17, cave + 4 * adr_i, cave + 4 * table_at)
    if special_adr_i is not None:
        special_at = table_at + len(throttle['table']) + 1
        w[special_adr_i] = A.adr(17, cave + 4 * special_adr_i,
                                  cave + 4 * special_at)
    if model_adr_i is not None:
        model_at = (table_at + len(throttle['table']) + 1
                    + len(throttle.get('kotr', ())) + 1)
        w[model_adr_i] = A.adr(17, cave + 4 * model_adr_i,
                               cave + 4 * model_at)
    if one_call_adr_i is not None:
        one_call_at = (table_at + len(throttle['table']) + 1
                       + len(throttle.get('kotr', ())) + 1
                       + len(throttle.get('model', ())) + 1)
        w[one_call_adr_i] = A.adr(17, cave + 4 * one_call_adr_i,
                                  cave + 4 * one_call_at)
    return w, table_at


def build_throttle_pre_cave(cave, site, throttle, paused_guest, mask_bits,
                            freq_bits=2, addr=None):
    """
    Emit the pre-call half of the pause-throttle, hooked on the guest-stack push
    that immediately precedes the dispatcher's indirect call.

        adrp x17, page(scratch)
        add  x17, x17, #lo12(scratch)
        strb wzr, [x17, #DID]          ; assume no pause this frame
        ldr  w16, [ctx, #idx_off]
        and  w16, w16, #mask
        add  x17, x17, x16             ; &ctr[idx]  (ctr is at scratch + 0)
        ldrb w16, [x17]
        tbnz w16, #7, OUT              ; excluded slot -- stock behaviour
        add  w16, w16, #1
        and  w16, w16, #(freq-1)
        strb w16, [x17]
        cmp  w16, #1                   ; new phase 1 <=> old phase 0
        b.eq OUT                       ; ... which is the real step
        sub  sp, sp, #0x10
        str  x0, [sp]                  ; the guest function pointer
        str  xE, [sp, #8]              ; the value the displaced store writes
        movz w0, #lo16(g_is_battle_paused)
        movk w0, #hi16(g_is_battle_paused), lsl #16
        bl   0x10FC3A0                 ; x0 = host &g_is_battle_paused
        adrp x17, page(scratch)
        add  x17, x17, #lo12(scratch)
        str  x0,  [x17, #PPTR]         ; hand the pointer to the post cave
        ldrb w16, [x0]
        strb w16, [x17, #SAVED]        ; wasPaused
        movz w16, #1
        strb w16, [x0]                 ; *isBattlePaused = 1
        strb w16, [x17, #DID]
        ldr  x0, [sp]
        ldr  xE, [sp, #8]
        add  sp, sp, #0x10
    OUT:
        <displaced: str wE, [ctx, #0x10]>
        b    hook + 4

    Register argument, and it is the whole safety case for this cave:

      x16, x17   free. The hook is four instructions past a `bl 0x10FC3A0` and
                 nothing between defines them; IP0/IP1 are caller-saved.
      w0         LIVE -- it is the guest function pointer and must reach the
                 `bl 0xa1a0` four bytes later. Spilled across our own call.
      wE         LIVE -- the displaced store writes it. Spilled with it.
      xCTX       callee-saved, so the translator preserves it.
      x30        dead: the very next instruction is a `bl`, which overwrites it.

    The host pointer to g_is_battle_paused is handed to the post cave through
    BSS rather than re-derived there, and it is NOT cached across frames: it is
    obtained from the translator microseconds earlier in the same slot
    iteration, so this assumes nothing about whether the guest-to-host mapping
    is stable over time.
    """
    ctx, io = site['ctx_reg'], site['idx_off']
    e = site['store_reg']
    if e in (0, 16, 17):
        raise SystemExit('refusing to emit a throttle pre cave: the displaced '
                         'store uses w%d, which this cave needs as scratch' % e)
    if ctx in (0, 16, 17):
        raise SystemExit('refusing to emit a throttle pre cave: the guest '
                         'context register is x%d' % ctx)
    base, pptr, saved, did = (throttle['ctr'], throttle['pptr'],
                              throttle['saved'], throttle['did'])
    w = []

    def pc(i=None):
        i = len(w) if i is None else i
        return addr(i) if addr is not None else cave + 4 * i

    w.append(A.adrp(17, pc(), base & ~0xFFF))
    w.append(A.add_imm64(17, 17, base & 0xFFF))
    w.append(A.strb(A.WZR, 17, did - base))
    w.append(A.ldr(16, ctx, io))
    w.append(A.and_mask(16, 16, mask_bits))
    w.append(A.add_reg64(17, 17, 16))
    w.append(A.ldrb(16, 17, 0))
    # Use ordinary conditional branches for the three class tests below.
    # The production padding allocator may scatter decorator blocks by well
    # over TBZ/TBNZ's +/-32 KiB range, while B.cond remains valid to +/-1 MiB.
    w.append(A.cmp_imm(16, 0x80))
    tb_i = len(w)
    w.append(0)                                     # b.hs OUT -- excluded marker
    kotr_i = None
    if throttle.get('kotr'):
        w.append(A.cmp_imm(16, 0x40))
        kotr_i = len(w)
        w.append(0)                                 # b.hs KOTR (excluded split first)
    model_i = None
    if throttle.get('model'):
        w.append(A.cmp_imm(16, 0x20))
        model_i = len(w)
        w.append(0)                                 # b.ge MODEL (KOTR already split)
    one_call_i = None
    if throttle.get('one_call'):
        w.append(A.cmp_imm(16, 0x10))
        one_call_i = len(w)
        w.append(0)                                 # b.hs ONE_CALL
    w.append(A.add_imm(16, 16, 1))
    w.append(A.and_mask(16, 16, freq_bits))
    w.append(A.strb(16, 17, 0))
    w.append(A.cmp_imm(16, 1))
    eq_i = len(w)
    w.append(0)                                     # b.eq OUT -- patched
    w.append(A.sub_imm64(A.SP, A.SP, 0x10))
    w.append(A.str64(0, A.SP, 0))
    w.append(A.str64(e, A.SP, 8))
    w.append(A.movz(0, paused_guest & 0xFFFF))
    w.append(A.movk_hi(0, (paused_guest >> 16) & 0xFFFF))
    w.append(A.bl(pc(), TRANSLATE))
    w.append(A.adrp(17, pc(), base & ~0xFFF))
    w.append(A.add_imm64(17, 17, base & 0xFFF))
    w.append(A.str64(0, 17, pptr - base))
    w.append(A.ldrb(16, 0, 0))
    w.append(A.strb(16, 17, saved - base))
    w.append(A.movz(16, 1))
    w.append(A.strb(16, 0, 0))
    w.append(A.strb(16, 17, did - base))
    w.append(A.ldr64(0, A.SP, 0))
    w.append(A.ldr64(e, A.SP, 8))
    w.append(A.add_imm64(A.SP, A.SP, 0x10))
    normal_out_i = None
    if (throttle.get('kotr') or throttle.get('model')
            or throttle.get('one_call')):
        normal_out_i = len(w)
        w.append(0)                                 # b OUT -- don't fall into decorator paths

    # FFNx OneCallEffectDecorator. On phase 0 the stock call runs normally.
    # On the other phases, cancel the stock `sub wE,wE,#4`, store the restored
    # guest ESP, and join at the post-call hook. Joining there executes that
    # hook's displaced instruction but never pushes a guest return address or
    # enters the indirect thunk.
    one_call_real_i = None
    if throttle.get('one_call'):
        one_call = pc()
        w[one_call_i] = A.bcond(pc(one_call_i), one_call, A.HS)
        w.append(A.add_imm(16, 16, 1))
        # w0 is still the guest callback address consumed by the indirect
        # thunk on the real-call phase.  Keep all phase arithmetic in w16;
        # clobbering w0 here turns 0x00484A16 into phase value 1 and sends the
        # translator to an invalid guest address as soon as Bahamut ZERO runs.
        w.append(A.and_mask(16, 16, freq_bits))
        w.append(A.add_imm(16, 16, 0x10))
        w.append(A.strb(16, 17, 0))
        w.append(A.cmp_imm(16, 0x11))               # phase 1 = real call
        one_call_real_i = len(w)
        w.append(0)                                 # b.eq OUT
        w.append(A.add_imm(e, e, 4))                # cancel guest ESP -= 4
        w.append(site['displaced'])                 # publish restored guest ESP
        w.append(A.b(pc(), throttle['post']))       # skip thunk, run post hook

    # FFNx's FixCounterExceptionEffectDecorator for the thirteen KOTR knights.
    # The function is called every rendered frame (so model animation is not
    # frozen), but field_2 is restored after repeat calls.  Nested add_fn calls
    # are suppressed, field_0 is restored except on the final repeat, and the
    # named exception counter is temporarily presented as counter+1.
    kotr_end_i = None
    if throttle.get('kotr'):
        kotr = pc()
        w[kotr_i] = A.bcond(pc(kotr_i), kotr, A.HS)
        w.append(A.add_imm(16, 16, 1))
        w.append(A.and_mask(16, 16, freq_bits))
        w.append(A.add_imm(16, 16, 0x40))
        w.append(A.strb(16, 17, 0))
        w.append(A.cmp_imm(16, 0x41))               # phase 1 = real step
        kotr_real_i = len(w)
        w.append(0)                                 # b.eq OUT

        w.append(A.sub_imm64(A.SP, A.SP, 0x10))
        w.append(A.str64(0, A.SP, 0))
        w.append(A.str64(e, A.SP, 8))

        # did=3 on the final repeat (phase 0), otherwise did=2.  The post cave
        # uses this to let a retirement at the final repeat persist exactly as
        # FFNx does.
        w.append(A.cmp_imm(16, 0x40))
        w.append(A.movz(16, 2))
        # did=2 when phase != 0x40; increment to 3 when phase == 0x40.
        w.append(A.csinc(16, 16, 16, A.NE))
        w.append(A.adrp(17, pc(), base & ~0xFFF))
        w.append(A.add_imm64(17, 17, base & 0xFFF))
        w.append(A.strb(16, 17, did - base))

        # Translate &effect100_array_data[idx].
        w.append(A.ldr(16, ctx, io))
        w.append(A.and_mask(16, 16, mask_bits))
        data = throttle['data_base']
        w.append(A.movz(0, data & 0xFFFF))
        w.append(A.movk_hi(0, (data >> 16) & 0xFFFF))
        if throttle.get('stride') != 0x20:
            raise SystemExit('KOTR counter hold requires effect100 stride 0x20')
        w.append(A.add_reg_lsl(0, 0, 16, 5))
        w.append(A.bl(pc(), TRANSLATE))

        w.append(A.adrp(17, pc(), base & ~0xFFF))
        w.append(A.add_imm64(17, 17, base & 0xFFF))
        w.append(A.str64(0, 17, throttle['kotr_ptr'] - base))
        w.append(A.ldrh(16, 0, 0))
        w.append(A.strh(16, 17, throttle['kotr_active'] - base))
        w.append(A.ldrh(16, 0, 2))
        w.append(A.strh(16, 17, throttle['kotr_counter'] - base))

        # Fetch this slot's exception and, only on an exact counter match,
        # expose counter+1 during the call.  The post cave always restores it.
        w.append(A.ldr(16, ctx, io))
        w.append(A.and_mask(16, 16, mask_bits))
        w.append(A.add_reg64(17, 17, 16))
        w.append(A.ldrb(16, 17, throttle['kotr_exc'] - base))
        no_exc_i = len(w)
        w.append(0)                                 # cbz NO_EXCEPTION
        w.append(A.ldrh(17, 0, 2))
        w.append(A.cmp_reg(17, 16))
        not_exc_i = len(w)
        w.append(0)                                 # b.ne NO_EXCEPTION
        w.append(A.add_imm(17, 17, 1))
        w.append(A.strh(17, 0, 2))
        no_exception = pc()
        w[no_exc_i] = A.cbz(16, pc(no_exc_i), no_exception)
        w[not_exc_i] = A.bcond(pc(not_exc_i), no_exception, A.NE)

        w.append(A.adrp(17, pc(), base & ~0xFFF))
        w.append(A.add_imm64(17, 17, base & 0xFFF))
        w.append(A.movz(16, 1))
        w.append(A.strb(16, 17, throttle['kotr_disable'] - base))
        w.append(A.ldr64(0, A.SP, 0))
        w.append(A.ldr64(e, A.SP, 8))
        w.append(A.add_imm64(A.SP, A.SP, 0x10))
        if throttle.get('model'):
            kotr_end_i = len(w)
            w.append(0)                             # b OUT -- don't enter MODEL

    # FFNx ModelInterpolationEffectDecorator for all ten summon movement
    # functions.  Marker bit 5 selects this path, bit 4 means the initial
    # position was captured, and bit 3 chooses Alexander's larger teleport
    # threshold.  The phase byte is kept separately so wrapping 3->0 cannot
    # carry into those marker bits.
    model_init_i = model_real_i = model_repeat_done_i = None
    if throttle.get('model'):
        model = pc()
        w[model_i] = A.bcond(pc(model_i), model, A.GE)
        # AND's helper takes a low-bit count, not a literal mask.  Keeping
        # the low five bits distinguishes uninitialized 0x20/0x28 from
        # initialized 0x30/0x38 without losing Alexander's bit 3.
        w.append(A.and_mask(0, 16, 5))
        w.append(A.cmp_imm(0, 0x10))
        model_init_i = len(w)
        w.append(0)                                 # b.lo MODEL_INIT

        # Remember the current slot for the post-call half, then advance its
        # independent 0..3 interpolation phase.
        w.append(A.ldr(0, ctx, io))
        w.append(A.and_mask(0, 0, mask_bits))
        w.append(A.adrp(17, pc(), base & ~0xFFF))
        w.append(A.add_imm64(17, 17, base & 0xFFF))
        w.append(A.strb(0, 17, throttle['model_idx'] - base))
        w.append(A.add_reg64(17, 17, 0))
        w.append(A.ldrb(16, 17, throttle['model_phase'] - base))
        w.append(A.add_imm(0, 16, 1))
        w.append(A.and_mask(0, 0, freq_bits))
        w.append(A.strb(0, 17, throttle['model_phase'] - base))

        # did = 5,6,7 for phases 1,2,3 and 8 for phase 0.  Only phase 1
        # advances the logical summon; the other three calls use the pause
        # trick and are overwritten with an interpolated model position.
        w.append(A.cmp_imm(16, 0))
        w.append(A.add_imm(16, 16, 4))
        w.append(A.movz(0, 8))
        w.append(A.csel(16, 0, 16, A.EQ))
        w.append(A.adrp(17, pc(), base & ~0xFFF))
        w.append(A.add_imm64(17, 17, base & 0xFFF))
        w.append(A.strb(16, 17, did - base))
        w.append(A.cmp_imm(16, 5))
        model_real_i = len(w)
        w.append(0)                                 # b.eq MODEL_REAL

        # Repeated model frame: execute the stock function paused.  This is
        # the same safe call-preserving pause trick as the generic decorator,
        # but did=6/7/8 tells post which interpolation step to display.
        w.append(A.sub_imm64(A.SP, A.SP, 0x10))
        w.append(A.str64(0, A.SP, 0))
        w.append(A.str64(e, A.SP, 8))
        w.append(A.movz(0, paused_guest & 0xFFFF))
        w.append(A.movk_hi(0, (paused_guest >> 16) & 0xFFFF))
        w.append(A.bl(pc(), TRANSLATE))
        w.append(A.adrp(17, pc(), base & ~0xFFF))
        w.append(A.add_imm64(17, 17, base & 0xFFF))
        w.append(A.str64(0, 17, pptr - base))
        w.append(A.ldrb(16, 0, 0))
        w.append(A.strb(16, 17, saved - base))
        w.append(A.movz(16, 1))
        w.append(A.strb(16, 0, 0))
        w.append(A.ldr64(0, A.SP, 0))
        w.append(A.ldr64(e, A.SP, 8))
        w.append(A.add_imm64(A.SP, A.SP, 0x10))
        model_repeat_done_i = len(w)
        w.append(0)                                 # b OUT

        # First invocation: mark initialized, reset per-registration state,
        # and call the stock function normally.  Post captures nextPosition.
        model_init = pc()
        w[model_init_i] = A.bcond(pc(model_init_i), model_init, A.LT)
        w.append(A.add_imm(16, 16, 0x10))
        w.append(A.strb(16, 17, 0))                  # ctr[idx] marker
        w.append(A.ldr(0, ctx, io))
        w.append(A.and_mask(0, 0, mask_bits))
        w.append(A.adrp(17, pc(), base & ~0xFFF))
        w.append(A.add_imm64(17, 17, base & 0xFFF))
        w.append(A.strb(0, 17, throttle['model_idx'] - base))
        w.append(A.add_reg64(17, 17, 0))
        w.append(A.movz(16, 1))
        w.append(A.strb(16, 17, throttle['model_phase'] - base))
        w.append(A.strb(A.WZR, 17, throttle['model_final'] - base))
        w.append(A.adrp(17, pc(), base & ~0xFFF))
        w.append(A.add_imm64(17, 17, base & 0xFFF))
        w.append(A.movz(16, 4))
        w.append(A.strb(16, 17, did - base))
        model_init_done_i = len(w)
        w.append(0)                                 # b OUT

        # Real logical step (phase 1): save effectActive so post can defer a
        # retirement until interpolation step 4, exactly as FFNx does.
        model_real = pc()
        w[model_real_i] = A.bcond(pc(model_real_i), model_real, A.EQ)
        w.append(A.sub_imm64(A.SP, A.SP, 0x10))
        w.append(A.str64(0, A.SP, 0))
        w.append(A.str64(e, A.SP, 8))
        w.append(A.ldr(16, ctx, io))
        w.append(A.and_mask(16, 16, mask_bits))
        data = throttle['data_base']
        w.append(A.movz(0, data & 0xFFFF))
        w.append(A.movk_hi(0, (data >> 16) & 0xFFFF))
        if throttle.get('stride') != 0x20:
            raise SystemExit('model interpolation requires effect100 stride 0x20')
        w.append(A.add_reg_lsl(0, 0, 16, 5))
        w.append(A.bl(pc(), TRANSLATE))
        w.append(A.adrp(17, pc(), base & ~0xFFF))
        w.append(A.add_imm64(17, 17, base & 0xFFF))
        w.append(A.str64(0, 17, throttle['model_ptr'] - base))
        w.append(A.ldrh(16, 0, 0))
        w.append(A.strh(16, 17, throttle['model_active'] - base))
        w.append(A.ldr64(0, A.SP, 0))
        w.append(A.ldr64(e, A.SP, 8))
        w.append(A.add_imm64(A.SP, A.SP, 0x10))
        model_real_done_i = len(w)
        w.append(0)                                 # b OUT
    out = pc()
    w[tb_i] = A.bcond(pc(tb_i), out, A.HS)
    w[eq_i] = A.bcond(pc(eq_i), out, A.EQ)
    if throttle.get('kotr'):
        w[kotr_real_i] = A.bcond(pc(kotr_real_i), out, A.EQ)
    if one_call_real_i is not None:
        w[one_call_real_i] = A.bcond(pc(one_call_real_i), out, A.EQ)
    if normal_out_i is not None:
        w[normal_out_i] = A.b(pc(normal_out_i), out)
    if kotr_end_i is not None:
        w[kotr_end_i] = A.b(pc(kotr_end_i), out)
    if throttle.get('model'):
        w[model_repeat_done_i] = A.b(pc(model_repeat_done_i), out)
        w[model_init_done_i] = A.b(pc(model_init_done_i), out)
        w[model_real_done_i] = A.b(pc(model_real_done_i), out)
    w.append(site['displaced'])
    w.append(A.b(pc(), site['hook'] + 4))
    return w


def build_throttle_post_cave(cave, site, throttle, addr=None):
    """
    Emit the post-call half: undo the pause the pre cave applied.

        adrp x17, page(scratch)
        add  x17, x17, #lo12(scratch)
        ldrb w16, [x17, #DID]
        cbz  w16, OUT
        strb wzr, [x17, #DID]
        ldrb w16, [x17, #SAVED]
        ldr  x17, [x17, #PPTR]
        strb w16, [x17]
    OUT:
        <displaced>
        b    hook + 4

    The hook is the instruction immediately after the indirect call returns, so
    x16 and x17 are dead by the AAPCS and w0 -- which the displaced instruction
    redefines -- is dead too. Nothing here calls anything, so no spill is
    needed.

    The counter was already advanced by the pre cave. FFNx increments after the
    call; the value is not read again until the next invocation, so incrementing
    before is the same program and saves the post cave from having to recover
    `idx`, which it could not do honestly: the guest function has just run and
    is free to have clobbered the guest register slot idx lives in. That is why
    the stock code re-reads effect100_array_idx from guest memory here rather
    than reusing the spill.
    """
    base, pptr, saved, did = (throttle['ctr'], throttle['pptr'],
                              throttle['saved'], throttle['did'])
    w = []

    def pc(i=None):
        i = len(w) if i is None else i
        return addr(i) if addr is not None else cave + 4 * i

    w.append(A.adrp(17, pc(), base & ~0xFFF))
    w.append(A.add_imm64(17, 17, base & 0xFFF))
    w.append(A.ldrb(16, 17, did - base))
    cbz_i = len(w)
    w.append(0)                                     # cbz w16, OUT -- patched

    model_i = None
    if throttle.get('model'):
        w.append(A.cmp_imm(16, 4))
        model_i = len(w)
        w.append(0)                                 # b.ge MODEL -- did 4..8

    special_i = None
    if throttle.get('kotr'):
        w.append(A.cmp_imm(16, 2))
        special_i = len(w)
        w.append(0)                                 # b.ge KOTR -- did 2/3

    # Generic pause decorator (did=1).
    w.append(A.strb(A.WZR, 17, did - base))
    w.append(A.ldrb(16, 17, saved - base))
    w.append(A.ldr64(17, 17, pptr - base))
    w.append(A.strb(16, 17, 0))
    normal_out_i = None
    if throttle.get('kotr') or throttle.get('model'):
        normal_out_i = len(w)
        w.append(0)                                 # b OUT

    # Knights of the Round counter-hold decorator (did=2/3).
    final_i = special_out_i = None
    if throttle.get('kotr'):
        special = pc()
        w[special_i] = A.bcond(pc(special_i), special, A.GE)
        # Preserve the did value's flags while stores/loads restore the held
        # state.  did==3 is the last repeat, where field_0 retirement persists.
        w.append(A.cmp_imm(16, 3))
        w.append(A.strb(A.WZR, 17, did - base))
        w.append(A.strb(A.WZR, 17, throttle['kotr_disable'] - base))
        w.append(A.ldr64(0, 17, throttle['kotr_ptr'] - base))
        w.append(A.ldrh(16, 17, throttle['kotr_counter'] - base))
        w.append(A.strh(16, 0, 2))
        final_i = len(w)
        w.append(0)                                 # b.eq OUT (keep field_0)
        w.append(A.ldrh(16, 17, throttle['kotr_active'] - base))
        w.append(A.strh(16, 0, 0))
        if throttle.get('model'):
            special_out_i = len(w)
            w.append(0)                             # b OUT -- don't enter MODEL

    # Summon model-position interpolation (did=4..8).  Registers x0..x17 are
    # caller-saved and dead at this post-call hook; the displaced instruction
    # defines w0, so this path may use them freely after TRANSLATE returns.
    model_init_out_i = model_final_out_i = None
    teleport_branches = []
    if throttle.get('model'):
        model = pc()
        w[model_i] = A.bcond(pc(model_i), model, A.GE)

        # Repeated frames used the pause trick; restore the original byte
        # before doing any interpolation work.  did=4/5 were real calls.
        w.append(A.cmp_imm(16, 6))
        no_restore_i = len(w)
        w.append(0)                                 # b.lt MODEL_TRANSLATE
        w.append(A.ldrb(0, 17, saved - base))
        w.append(A.ldr64(1, 17, pptr - base))
        w.append(A.strb(0, 1, 0))
        model_translate = pc()
        w[no_restore_i] = A.bcond(pc(no_restore_i), model_translate, A.LT)

        # Translate actor 3's modelPosition.  FFNx recovers battle_model_state
        # at 0xBE1178, sizeof=0x1AEC, modelPosition=0x166, hence 0xBE63A2.
        pos = throttle['model_pos']
        w.append(A.movz(0, pos & 0xFFFF))
        w.append(A.movk_hi(0, (pos >> 16) & 0xFFFF))
        w.append(A.bl(pc(), TRANSLATE))              # x0 = host modelPosition

        # Recover did and idx after the call (TRANSLATE may clobber every
        # caller-saved register), clear did, and form the two 6-byte vectors.
        w.append(A.adrp(1, pc(), base & ~0xFFF))
        w.append(A.add_imm64(1, 1, base & 0xFFF))
        w.append(A.ldrb(2, 1, did - base))
        w.append(A.strb(A.WZR, 1, did - base))
        w.append(A.ldrb(3, 1, throttle['model_idx'] - base))
        prev = throttle['model_prev']
        nxt = throttle['model_next']
        w.append(A.adrp(4, pc(), prev & ~0xFFF))
        w.append(A.add_imm64(4, 4, prev & 0xFFF))
        w.append(A.add_reg64_lsl(4, 4, 3, 1))
        w.append(A.add_reg64_lsl(4, 4, 3, 2))
        w.append(A.adrp(5, pc(), nxt & ~0xFFF))
        w.append(A.add_imm64(5, 5, nxt & 0xFFF))
        w.append(A.add_reg64_lsl(5, 5, 3, 1))
        w.append(A.add_reg64_lsl(5, 5, 3, 2))

        # Frame zero only captures nextPosition.
        w.append(A.cmp_imm(2, 4))
        model_init_i = len(w)
        w.append(0)                                 # b.eq MODEL_INIT

        # Step four displays nextPosition exactly and, if the real step
        # retired the effect, performs that deferred retirement now.
        w.append(A.cmp_imm(2, 8))
        model_final_i = len(w)
        w.append(0)                                 # b.eq MODEL_FINAL

        # Step one shifts old next -> previous and captures the newly produced
        # position as next. Steps two/three retain those endpoints.
        w.append(A.cmp_imm(2, 5))
        no_capture_i = len(w)
        w.append(0)                                 # b.ne MODEL_THRESH
        for off in (0, 2, 4):
            w.append(A.ldrh(6, 5, off))
            w.append(A.strh(6, 4, off))
            w.append(A.ldrh(6, 0, off))
            w.append(A.strh(6, 5, off))

        # Select 1000 (all summons) or 3000 (Alexander), and compare squared
        # distance without allowing a huge signed-short delta to overflow the
        # 32-bit squares.  A component at/over the threshold is already a
        # teleport; otherwise the sum is bounded to <27,000,000.
        model_thresh = pc()
        w[no_capture_i] = A.bcond(pc(no_capture_i), model_thresh, A.NE)
        w.append(A.adrp(1, pc(), base & ~0xFFF))
        w.append(A.add_imm64(1, 1, base & 0xFFF))
        w.append(A.add_reg64(1, 1, 3))
        w.append(A.ldrb(6, 1, 0))                   # ctr[idx] marker
        large_thresh_i = len(w)
        w.append(0)                                 # tbnz bit3,LARGE
        w.append(A.movz(6, 1000))
        w += A.movz_movk(7, 1000 * 1000)
        thresh_ready_i = len(w)
        w.append(0)                                 # b THRESH_READY
        large_thresh = pc()
        w[large_thresh_i] = A.tbnz(6, 3, pc(large_thresh_i), large_thresh)
        w.append(A.movz(6, 3000))
        w += A.movz_movk(7, 3000 * 3000)
        thresh_ready = pc()
        w[thresh_ready_i] = A.b(pc(thresh_ready_i), thresh_ready)
        w.append(A.movz(8, 0))                      # squared-distance sum
        for off in (0, 2, 4):
            w.append(A.ldrsh(9, 5, off))
            w.append(A.ldrsh(10, 4, off))
            w.append(A.sub_reg(11, 9, 10))
            w.append(A.cmp_reg(11, 6))
            ge_i = len(w)
            w.append(0)                             # b.ge TELEPORT
            teleport_branches.append((ge_i, A.GE))
            w.append(A.sub_reg(12, A.WZR, 6))
            w.append(A.cmp_reg(11, 12))
            le_i = len(w)
            w.append(0)                             # b.le TELEPORT
            teleport_branches.append((le_i, A.LE))
            w.append(A.mul(11, 11, 11))
            w.append(A.add_reg(8, 8, 11))
        w.append(A.cmp_reg(8, 7))
        dist_i = len(w)
        w.append(0)                                 # b.ge TELEPORT
        teleport_branches.append((dist_i, A.GE))

        # Smooth path: previous + trunc((next-previous)*step / 4).  The +3
        # bias for negative products gives C++ signed division toward zero.
        w.append(A.sub_imm(12, 2, 4))               # interpolation step 1..3
        for off in (0, 2, 4):
            w.append(A.ldrsh(9, 4, off))
            w.append(A.ldrsh(10, 5, off))
            w.append(A.sub_reg(10, 10, 9))
            w.append(A.mul(10, 10, 12))
            w.append(A.lsr(11, 10, 31))
            w.append(A.add_reg_lsl(10, 10, 11, 1))
            w.append(A.add_reg(10, 10, 11))
            w.append(A.asr(10, 10, 2))
            w.append(A.add_reg(10, 9, 10))
            w.append(A.strh(10, 0, off))
        smooth_done_i = len(w)
        w.append(0)                                 # b MODEL_AFTER_POSITION

        teleport = pc()
        for bi, cond in teleport_branches:
            w[bi] = A.bcond(pc(bi), teleport, cond)
        for off in (0, 2, 4):
            w.append(A.ldrh(9, 4, off))
            w.append(A.strh(9, 0, off))

        # Only a real phase-one call can newly retire the effect. Restore it
        # and remember to retire after step four, matching FFNx's finalFrame.
        after_position = pc()
        w[smooth_done_i] = A.b(pc(smooth_done_i), after_position)
        w.append(A.cmp_imm(2, 5))
        not_real_i = len(w)
        w.append(0)                                 # b.ne OUT
        w.append(A.adrp(1, pc(), base & ~0xFFF))
        w.append(A.add_imm64(1, 1, base & 0xFFF))
        w.append(A.ldr64(4, 1, throttle['model_ptr'] - base))
        w.append(A.ldrh(5, 4, 0))
        w.append(A.movz(6, 0xFFFF))
        w.append(A.cmp_reg(5, 6))
        not_retired_i = len(w)
        w.append(0)                                 # b.ne OUT
        w.append(A.ldrh(5, 1, throttle['model_active'] - base))
        w.append(A.cmp_reg(5, 6))
        already_dead_i = len(w)
        w.append(0)                                 # b.eq OUT
        w.append(A.strh(5, 4, 0))
        w.append(A.add_reg64(1, 1, 3))
        w.append(A.movz(5, 1))
        w.append(A.strb(5, 1, throttle['model_final'] - base))
        model_retire_out_i = len(w)
        w.append(0)                                 # b OUT

        model_init = pc()
        w[model_init_i] = A.bcond(pc(model_init_i), model_init, A.EQ)
        for off in (0, 2, 4):
            w.append(A.ldrh(6, 0, off))
            w.append(A.strh(6, 5, off))
        model_init_out_i = len(w)
        w.append(0)                                 # b OUT

        model_final = pc()
        w[model_final_i] = A.bcond(pc(model_final_i), model_final, A.EQ)
        for off in (0, 2, 4):
            w.append(A.ldrh(6, 5, off))
            w.append(A.strh(6, 0, off))
        w.append(A.adrp(1, pc(), base & ~0xFFF))
        w.append(A.add_imm64(1, 1, base & 0xFFF))
        w.append(A.add_reg64(1, 1, 3))
        w.append(A.ldrb(6, 1, throttle['model_final'] - base))
        no_final_i = len(w)
        w.append(0)                                 # cbz OUT
        w.append(A.strb(A.WZR, 1, throttle['model_final'] - base))
        w.append(A.adrp(1, pc(), base & ~0xFFF))
        w.append(A.add_imm64(1, 1, base & 0xFFF))
        w.append(A.ldr64(4, 1, throttle['model_ptr'] - base))
        w.append(A.movz(6, 0xFFFF))
        w.append(A.strh(6, 4, 0))
        model_final_out_i = len(w)
        w.append(0)                                 # b OUT

    out = pc()
    w[cbz_i] = A.cbz(16, pc(cbz_i), out)
    if normal_out_i is not None:
        w[normal_out_i] = A.b(pc(normal_out_i), out)
    if throttle.get('kotr'):
        w[final_i] = A.bcond(pc(final_i), out, A.EQ)
    if special_out_i is not None:
        w[special_out_i] = A.b(pc(special_out_i), out)
    if throttle.get('model'):
        for bi in (not_real_i, not_retired_i, already_dead_i):
            cond = A.NE if bi in (not_real_i, not_retired_i) else A.EQ
            w[bi] = A.bcond(pc(bi), out, cond)
        w[model_retire_out_i] = A.b(pc(model_retire_out_i), out)
        w[model_init_out_i] = A.b(pc(model_init_out_i), out)
        w[no_final_i] = A.cbz(6, pc(no_final_i), out)
        w[model_final_out_i] = A.b(pc(model_final_out_i), out)
    w.append(site['displaced'])
    w.append(A.b(pc(), site['hook'] + 4))
    return w


# ==========================================================================
# The movie frame divider
# ==========================================================================
#
# WHY
# ---
# FF7 asks the video player "which frame are you on?" through
# `get_movie_frame` (x86 0x418613) and drives scripted cutscene events off
# the answer: the opening's music cue is a hardcoded `cmp edx, 0x6E0` in the
# field loop, and field scripts in flevel.lgp time their model overlays the
# same way. The number is a FRAME INDEX, not a time, and every vanilla movie
# is 15 fps.
#
# Give the game a 30 fps replacement and that counter runs twice as fast.
# Every scripted cue fires at half its intended moment -- the opening hands
# over to the field while a minute of cutscene is still playing.
#
# HOW
# ---
# On Switch `get_movie_frame` is not recompiled code. It is a three-word stub
# that tail-calls the native dispatcher:
#
#   00042290  mov  w0, #5
#   00042294  movk w0, #0xb00b, lsl #16
#   00042298  b    #0xa510            <- replaced with `b cave`
#
# The native implementation returns its value in GUEST EAX, which lives at
# offset 0 of the guest register context (the pointer at module 0x12CE2B0).
# Both of the module's two call sites read it straight back from there:
#
#   00947D18  bl   #0x42290           ; field_loop_sub_63C17F
#   00947D1C  ldrh w23, [x25]         ; x25 = guest ctx
#
#   010914FC  bl   #0x42290           ; the world-map movie handler
#   01091500  ldr  w22, [x21]
#
# So the cave turns the tail call into a real call, then divides guest EAX
# before returning. Both callers -- and therefore the field loop, the world
# map and every flevel script that reads a movie frame -- see a 15 fps
# equivalent index while the picture runs at 30.
#
# WHAT THIS REQUIRES OF THE BUILD
# -------------------------------
# The divider is unconditional, so it is only correct if EVERY movie the game
# can open is at the doubled rate. build.py enforces that: with this group
# enabled it emplaces the whole movie set at 30 fps, frame-doubling any that
# a mod did not already supply at 30. Enabling the cave without that step
# would halve the counter for 15 fps movies and break them in the opposite
# direction, which is why the two are driven by one setting.
MOVIE_FRAME_STUB = 0x42290           # get_movie_frame's ARM64 stub
MOVIE_FRAME_TAILCALL = 0x42298       # the `b dispatcher` word inside it
MOVIE_FRAME_DISPATCH = 0xA510        # where it tail-calls
GUEST_CTX_SLOT = 0x12CE2B0           # module slot holding the guest ctx ptr
GUEST_EAX_OFF = 0x0                  # EAX within that context


def build_movie_frame_cave(cave, shift=1, ctx_slot=GUEST_CTX_SLOT,
                           dispatch=MOVIE_FRAME_DISPATCH):
    """
    Emit the movie-frame divider.

        stp  x29, x30, [sp, #-16]!
        bl   dispatch              ; the native get_movie_frame
        ldp  x29, x30, [sp], #16
        adrp x16, page(ctx_slot)
        ldr  x16, [x16, #lo12]     ; guest register context
        ldr  w17, [x16]            ; EAX -- the decoded frame index
        lsr  w17, w17, #shift
        str  w17, [x16]
        ret

    x16/x17 are IP0/IP1 and dead across a call by the AAPCS, and the guest
    context pointer is reloaded rather than assumed, so nothing the native
    dispatcher does to the register file matters. EAX is read and written as
    a full 32-bit value because the world-map caller reads it that way; the
    field caller takes the low half of the same number.
    """
    if not 1 <= shift <= 3:
        raise SystemExit('movie frame shift %d is not a sane ratio' % shift)
    w = []

    def pc():
        return cave + 4 * len(w)

    w.append(A.stp64_pre(29, 30, A.SP, -16))
    w.append(A.bl(pc(), dispatch))
    w.append(A.ldp64_post(29, 30, A.SP, 16))
    w.append(A.adrp(16, pc(), ctx_slot & ~0xFFF))
    w.append(A.ldr64(16, 16, ctx_slot & 0xFFF))
    w.append(A.ldr(17, 16, GUEST_EAX_OFF))
    w.append(A.lsr(17, 17, shift))
    w.append(A.str_(17, 16, GUEST_EAX_OFF))
    w.append(A.ret())
    return w


# ==========================================================================
# The movie UPDATE rate -- the field's clock during an FMV
# ==========================================================================
#
# WHAT UPDATE_MOVIE_SAMPLE ACTUALLY IS ON SWITCH
# ----------------------------------------------
# x86 `update_movie_sample` (0x417F39) is NOT recompiled. It maps to a
# sixteen-byte native stub:
#
#   0003C660  mov  w0, #4
#   0003C664  movk w0, #0xb00b, lsl #16
#   0003C668  b    #0xa510
#
# id 0xB00B0004 resolves, through the descriptor array in `.data` at
# 0x12C9A70, to the port's own `fw_movie_update` at module 0x10F1590. The
# whole x86 body -- the fcomp, the `cmp eax, 0x19`, the `call [ecx+0x18]`
# into the media object -- is dead code on this build.
#
# WHY THAT MATTERS
# ----------------
# `fw_movie_update` BLOCKS until one new decoded frame is available:
#
#   if (!frame->ready)                       ; ldarb -- a decoder THREAD sets it
#       do {
#           if (movie_is_finished(player)) break;
#           frame->vtable[0x78](frame);
#           present();                       ; 0x10F9080
#       } while (!frame->ready);
#   if (frame->ready) { upload; draw; counter = frame->index + 1; consume; }
#
# One call consumes exactly one decoded frame. `field_loop_sub_63C17F` runs
# the opcode interpreter and then reaches `field_draw` (0x63A60B), which
# calls `update_movie_sample` once, on every iteration. The decoder is
# wall-clock paced -- the video plays at the right speed -- so:
#
#     DURING A MOVIE, THE FIELD TICKS ONCE PER MOVIE FRAME.
#
# Vanilla that is 15 ticks/second. With the movie set emplaced at 30 fps it
# is 30, and EVERY per-tick clock in the field doubles at once: `WAIT`
# countdowns in `wait_frames[]`, the MVIEF poll counter, `MOVE` steps,
# animation. `movie-fps` and `movie-poll` each correct one of those symptoms.
# Neither touches `WAIT`, and `md1stin`'s guards are a WAIT chain followed by
# a MOVE:
#
#   dir  s0   0428  WAIT 30 / REQ av_m ... 042E WAIT 33 / REQ gu1
#                   0434 WAIT 30 / REQ gu0 ...
#   gu0  s3   0748  SOLID / WAIT 32 / ANIME1 / MSPED / WAIT 8 / MOVE / ...
#                   0770 VISI 0            <- slides in, then hides itself
#
# which is exactly what the operator reports arriving too early over the
# opening FMV and then disappearing.
#
# THE FIX
# -------
# Do not patch the symptoms; remove the cause. Call the native
# `fw_movie_update` one extra time per guest call, so the guest still sees
# one update per VANILLA-rate frame. Every clock above is then vanilla by
# construction, and the field behaves during a 30 fps movie exactly as it
# does during one of the port's own 15 fps movies -- the state the operator
# was happy with before the FMV mod went in.
#
# WHY THE EXTRA CALL IS `bl 0x10F1590` AND NOT A SECOND TRIP THROUGH 0xA510
# ------------------------------------------------------------------------
# The trampoline is not re-entrant for this purpose. After dispatching it
# does:
#
#   0000A538  ldr w8, [x19, #0x10]      ; guest ESP
#   0000A53C  add w8, w8, #4            ; pop the return address
#   0000A540  str w8, [x19, #0x10]
#
# Going through it twice would pop the guest stack twice and corrupt the
# caller. The native function is called directly instead, and the stock
# trampoline still runs exactly once, so the guest side is untouched.
#
# `fw_movie_update` ignores its argument -- it never reads w0/x0 before
# overwriting it -- so nothing has to be marshalled for the extra call. It
# does clobber w0 (it returns 1), which is why the id is rebuilt afterwards.
#
# COMPOSITION
# -----------
# WITH `movie-fps`: required. The raw counter at *(0x12CE7D0) still advances
# twice per field tick, because both native calls draw a frame, so
# get_movie_frame must still be halved to report vanilla frame numbers.
#
# WITH `movie-poll`: mutually exclusive. Once the field ticks 15 times a
# second again, the MVIEF poll count is already vanilla; halving it as well
# would make every camera cue arrive twice as late. ff7nx_60fps.py refuses
# the combination.
#
# COST, STATED PLAINLY
# --------------------
# The first of the two frames is uploaded and drawn but overwritten by the
# second before the game presents, so a 30 fps movie displays at 15 fps. For
# the port's own movies that is lossless -- build.py produced them by
# frame-doubling 15 fps sources, so the two frames are identical. For a mod
# that supplies genuinely interpolated 30 fps video, the interpolated frames
# are discarded. Timing is correct either way; smoothness is what is traded.
MOVIE_UPDATE_STUB = 0x3C660          # update_movie_sample's ARM64 stub
MOVIE_UPDATE_TAILCALL = 0x3C668      # the `b dispatcher` word inside it
MOVIE_UPDATE_DISPATCH = 0xA510       # where it tail-calls
MOVIE_UPDATE_NATIVE = 0x10F1590      # fw_movie_update, the port's own C++
MOVIE_UPDATE_ID = 0xB00B0004         # the stub id the trampoline expects


def build_movie_update_cave(cave, extra=1, native=MOVIE_UPDATE_NATIVE,
                            dispatch=MOVIE_UPDATE_DISPATCH,
                            stub_id=MOVIE_UPDATE_ID):
    """
    Emit the movie-update multiplier.

        stp  x29, x30, [sp, #-16]!
        bl   native                ; fw_movie_update  (x `extra`)
        ldp  x29, x30, [sp], #16
        mov  w0, #<id low 16>
        movk w0, #<id high 16>, lsl #16
        b    dispatch              ; the stock trampoline, exactly once

    `extra` is how many ADDITIONAL frames to consume per guest call, i.e.
    one less than the ratio of the emplaced movie rate to vanilla 15 fps.
    At the shipping ratio of 2 that is a single extra call.

    x29/x30 are saved because the stub is entered with x30 holding the
    translated caller's return address and the tail call at the end relies on
    it. No other register is touched: `fw_movie_update` takes no argument it
    reads, and w0 is rebuilt after the call because it returns 1.
    """
    if not 1 <= extra <= 7:
        raise SystemExit('movie update extra %d is not a sane ratio' % extra)
    w = []

    def pc():
        return cave + 4 * len(w)

    w.append(A.stp64_pre(29, 30, A.SP, -16))
    for _ in range(extra):
        w.append(A.bl(pc(), native))
    w.append(A.ldp64_post(29, 30, A.SP, 16))
    w.append(A.movz(0, stub_id & 0xFFFF))
    w.append(A.movk_hi(0, (stub_id >> 16) & 0xFFFF))
    w.append(A.b(pc(), dispatch))
    return w


# ==========================================================================
# The MVIEF poll counter
# ==========================================================================
#
# WHAT MVIEF ACTUALLY RETURNS
# ---------------------------
# Field scripts time their model overlays during a movie with MVIEF, and the
# obvious assumption -- that it reports the movie's frame -- is wrong. The
# whole of it, from the x86:
#
#   MOVIE   0061A339  mov  word [0xCC0B70], 0     ; reset when playback starts
#   MVIEF   0061A446  mov  cx, word [0xCC0B70]    ; hand THIS to the script
#           0061A452  call 0x61031E               ;   -> script bank
#           0061A45A  mov  dx, word [0xCC0B70]
#           0061A461  add  dx, 1                  ; then increment
#           0061A465  mov  word [0xCC0B70], dx
#
# `0xCC0B70` has exactly four references in the executable -- that reset and
# these three. Nothing else touches it. So the number a field script compares
# against is A COUNT OF HOW MANY TIMES THE SCRIPT HAS POLLED since the movie
# started: one per field tick, with no connection to the decoded frame.
#
# WHY IT IS WRONG AT 60 FPS
# -------------------------
# The field ticks twice as often as the 30 fps the scripts were written for,
# so the count reaches every scripted threshold in half the wall-clock time
# and the models composited over a cutscene appear early. This is a 60 FPS
# artifact and happens with the game's own 15 fps movies too -- replacing the
# FMVs only makes it easy to see, because there is suddenly a long cutscene
# to be early against.
#
# FFNx fixes the same thing from the other end, scaling the comparison
# constant in `opcode_IFSW_compare_sub` by `common_frame_multiplier` when the
# limiter is at 60.
#
# THE FIX
# -------
# Halve the value handed to the script. The counter itself keeps incrementing
# untouched -- the increment re-reads the global rather than reusing the
# loaded value, so dividing at the hand-off point cannot disturb it.
#
#   00979868  mov  w0, w20          ; w20 = guest 0xCC0B70
#   0097986C  bl   translate
#   00979870  ldrh w8, [x0]         <- hooked; replayed inside the cave
#   00979874  mov  x22, x21
#   00979878  strh w8, [x22, #4]!   ; w8 goes to the script and nowhere else
MVIEF_POLL_HOOK = 0x979870
MVIEF_POLL_SIG = [(-8, 0x2A1403E0),      # mov  w0, w20
                  (-4, 0x941E0ACD),      # bl   0x10fc3a0
                  (0, 0x79400008),       # ldrh w8, [x0]
                  (4, 0xAA1503F6),       # mov  x22, x21
                  (8, 0x78004EC8)]       # strh w8, [x22, #4]!
MVIEF_POLL_DISPLACED = 0x79400008
MVIEF_POLL_VALUE_REG = 8


def build_movie_poll_cave(cave, shift=1, hook=MVIEF_POLL_HOOK,
                          displaced=MVIEF_POLL_DISPLACED,
                          reg=MVIEF_POLL_VALUE_REG):
    """
    Emit the MVIEF poll divider.

        ldrh w8, [x0]        ; the displaced load, verbatim
        lsr  w8, w8, #shift  ; report the vanilla-rate count
        b    hook + 4

    Three words, no call, no stack, no scratch register: the only thing
    touched is the value already in flight, between the load that produced it
    and the store that hands it to the script.
    """
    if not 1 <= shift <= 3:
        raise SystemExit('movie poll shift %d is not a sane ratio' % shift)
    w = [displaced, A.lsr(reg, reg, shift)]
    w.append(A.b(cave + 4 * len(w), hook + 4))
    return w
