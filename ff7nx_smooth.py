#!/usr/bin/env python3
"""
ff7nx_smooth.py -- smooth scripted field movement, FFNx's way.

WHAT WENT WRONG BEFORE (README-35)
==================================
The first attempt HALVED THE STEP: it hooked the two `asr #8` sites that
produce the per-tick displacement and split the result across the two frames
of the field tick. The pair summed to exactly the stock step, so the model
still landed on stock positions -- and it still froze Cloud's backwards walk
at Shinra HQ.

The reason is that a scripted move does not run for a fixed number of frames.
`field_check_collision_with_target` (x86 0x6366A1) decides every frame whether
the model has arrived, by comparing the SQUARED distance left against a
threshold built from the model's speed field:

    006366EC  imul ecx, [ebp-0x28]      ; dx*dx
    006366F6  imul edx, [ebp-0x34]      ; dy*dy   -> dist_sq
    0063671D  imul ecx, edx             ; (speed + arg)^2
    00636720  add  ecx, 0x1000          ;  -> threshold
    00636742  mov  cx, [eax+0x76]       ; speed
    0063674F  imul ecx, eax             ; speed*speed
    0063675B  cmp  ecx, [ebp-0x2c]      ; dist_sq < that -> arrived

The step and that threshold are two expressions of one quantity, and the code
assumes they agree. Halving the step and not the threshold breaks the
assumption, and where the geometry is tight the model can no longer close the
distance in a direction the walkmesh accepts. It walks in place forever.

WHAT FFNx DOES INSTEAD
======================
It never touches the step. It wraps the whole call
(`src/ff7/field/model.cpp`, `ff7_field_update_single_model_position`):

    if (moveFrameIndex == 0) {
        initialPosition = model_pos;
        ret = field_update_single_model_position(model_id);   // FULL stock step
        updateMovementReturnValue = ret;
        finalPosition = model_pos;
        model_pos = initial + (final - initial) * 1 / 2;
    } else {
        ret = updateMovementReturnValue;                      // replayed, NO call
        model_pos = initial + (final - initial) * 2 / 2;      // == finalPosition
    }

The real update runs ONCE PER PAIR, at full stock speed, always starting from
an exact stock position. The arrival test, the walkmesh probes and the
collision radius therefore see exactly what they see at 30 Hz. The midpoint is
written only AFTER the update has returned, and the second frame puts the
exact final position back before the next update runs. Nothing that reads the
step or the distance ever observes a halved value, which is why this cannot
reproduce the freeze.

FFNx wraps a second call for the same reason
(`ff7_field_check_collision_with_target`): the arrival test runs BEFORE the
movement call in the same iteration, so on the replay frame it would otherwise
run against the midpoint and could declare arrival half a step early. Its
result is cached on the first frame of the pair and replayed on the second.

THE BLOCKER THAT IS NOT ONE
===========================
HANDOFF 5b ruled this shape out: "a skipped translated call leaks the guest
return address". The disassembly says the leak is four bytes and we can pay it
back ourselves. Both call sites:

    009D81E0  sub w8, w8, #4          009D8588  sub w8, w8, #4      reserve ret slot
    009D81E4  str w8, [x25,#0x10]     009D858C  str w8, [x25,#0x10]
    009D81E8  bl  #0x9dc6f0           009D8590  bl  #0x9dc6f0       the call
    009D81EC  ldp w8,w9,[x25,#0x10]   009D8594  ldp w8,w9,[x25,#0x10]
    009D81F0  ldr w27, [x25]                                        guest EAX
    009D81F4  add w8, w8, #4          009D8598  add w8, w8, #4      pops the ARG

The caller reserves the return slot and never writes an address into it; the
callee's `ret` pops those four bytes. The caller's `ESP += 4` afterwards pops
the ARGUMENT. So skipping the call costs one instruction: `ESP += 4`.

THE THREE SITES, AND WHICH ONE IS LEFT ALONE
============================================
`field_update_models_positions` is x86 0x6342C6, which makes FFNx's offsets
line up exactly with what is in this module:

    +0x8BC -> x86 0x634B82, ARM 0x9D81E8   PLAYER    -- NOT TOUCHED
    +0x9AA -> x86 0x634C70, ARM 0x9D84EC   collision -- cached/replayed
    +0x9E8 -> x86 0x634CAE, ARM 0x9D8590   SCRIPTED  -- the interpolating wrapper

The player path is deliberately left alone. FFNx treats it differently too
(`ff7_field_update_player_model_position` divides `movement_speed` instead of
interpolating), and this build's existing player handling is the one thing in
this area confirmed good on hardware. Changing it here would put a working
feature at risk to fix a different one.

WHY THE PHASE CANNOT GO STALE
=============================
FFNx keeps a per-model `moveFrameIndex` and resets it from a separate pass
over every model whose `movement_type != 1`. That pass is a third hook, and
its absence would be silent: a model that stops mid-pair keeps an odd index,
and its next move begins on the REPLAY frame, replaying a cached position from
some earlier movement. That is a freeze, dressed as a stale read.

Rather than port the reset pass, the phase here is derived from the global
field tick counter this build already maintains, and every replay is validated
against the tick that produced its cache:

  * the phase advances at most once per model per tick (whichever hook reaches
    it first does it, the other sees the memo), so the two hooks cannot
    disagree about which half of the pair they are in;
  * a gap in the tick sequence -- the model stopped, the field changed, the
    collision test said "arrived" for a while -- forces phase 0, a full real
    call;
  * the replay path additionally refuses to fire unless the cache was written
    by the IMMEDIATELY PRECEDING tick.

So every discontinuity degrades to stock behaviour. There is no state that can
be stale and still be used, which is a stronger guarantee than the reset pass
gives, and it costs no third hook.
"""
from a64 import (adrp, add_imm, add_imm64, add_reg64_lsl, add_reg_lsr,
                 and_mask, asr, bcond, bl, b, cbnz, csel, eor_imm1, ldr,
                 ldp64_off, ldp64_post, ldrh, lsr, movk_hi, movz, mov_reg,
                 mul, orr_lsl, ret, str_, stp64_off, stp64_pre, sub_reg)

# ---------------------------------------------------------------- addresses
TRANSLATE   = 0x10FC3A0      # guest VA in w0 -> host pointer in x0
MOVE_TGT    = 0x9DC6F0       # translated field_update_single_model_position
COLL_TGT    = 0x9DF010       # translated field_check_collision_with_target

MOVE_HOOK   = 0x9D8590       # the SCRIPTED call site   (bl MOVE_TGT)
COLL_HOOK   = 0x9D84EC       # the collision call site  (bl COLL_TGT)
PLAYER_CALL = 0x9D81E8       # the PLAYER call site -- deliberately untouched

MOVE_ORIG   = 0x94001058       # bl #0x9dc6f0 encoded at MOVE_HOOK (measured)
COLL_ORIG   = 0x94001AC9       # bl #0x9df010 encoded at COLL_HOOK (measured)

EV_BASE     = 0xCC1670       # field_event_data[0]
EV_STRIDE   = 0x88
POS_OFF     = 0x0C           # model_pos.x, .y, .z at +0x0C/+0x10/+0x14
MAX_MODELS  = 32             # FFNx MAX_FIELD_MODELS

GUEST_EAX   = 0x00           # [x25 + 0x00]
GUEST_ESP   = 0x10           # [x25 + 0x10]
GUEST_REGS  = 25             # x25

# id = (ptr - EV_BASE) / 0x88, exact for every multiple of 0x88 below
# 32*0x88: 0x88*k*482 >> 16 == k for k < 4096.
RECIP_482   = 482

REC_SHIFT   = 6              # 64 bytes per model
REC_BYTES   = 1 << REC_SHIFT
R_INIT      = 0x00           # initial x, y, z
R_FINAL     = 0x0C           # final x, y, z
R_EAX_MOVE  = 0x18
R_EAX_COLL  = 0x1C
R_STATE     = 0x20           # tick | phase << 30
R_TICK_MOVE = 0x24           # tick whose phase-0 wrote R_FINAL / R_EAX_MOVE
# (R_TICK_COLL is gone: the arrival hook shares the movement hook's
#  R_TICK_MOVE stamp, which is what makes the two provably agree.)

SCRATCH_BYTES = MAX_MODELS * REC_BYTES      # 2048

TICK_MASK_BITS = 30          # phase lives in bit 30

EQ, NE, HS = 0, 1, 2


def _mov32(rd, val):
    """Two words that put a full 32-bit constant in Wd."""
    return [movz(rd, val & 0xFFFF), movk_hi(rd, (val >> 16) & 0xFFFF)]


def build(addr, scratch, tick_addr):
    """
    Emit the wrapper.

    `addr(i)` gives the address the i'th word will live at, so the same
    function serves a contiguous cave and a run scattered across reclaimed
    padding holes. Branches are computed from those addresses, and a64's
    `_rel` raises rather than truncating if any ends up out of range.

    Returns (words, entries) where `entries` maps 'move'/'coll' to the WORD
    INDEX each hook must branch to.

    WHO OWNS THE PHASE, AND WHY IT MATTERS
    ======================================
    An earlier version advanced the phase on first read, from whichever hook
    reached it first. That was wrong twice over, and it made NPCs jitter:

      * the ARRIVAL hook runs every frame for EVERY model, moving or not. So
        a model standing still had its phase flipped every frame, and by the
        time it started walking the alternation had been driven entirely by
        frames in which it was not walking;
      * the movement hook's stale-cache fallback rewrote the phase AFTER the
        arrival hook had already used the old value in the same frame, so the
        two hooks acted on different halves of the pair, alternately.

    FFNx does not do that. `moveFrameIndex` is advanced in ONE place -- the end
    of the movement wrapper -- and the arrival hook only ever reads it
    (`src/ff7/field/model.cpp`: writes at lines 142/164 in the wrapper and the
    reset at 69; the collision wrapper has none). The arrival hook runs first
    and sees the value the movement wrapper left at the end of the previous
    frame, which IS this frame's phase. Both hooks therefore read one value
    that neither of them has touched yet.

    That is reproduced exactly here. `decide` computes the predicate and
    writes nothing; only `move` updates the phase, and only on its way out.

    NO RESET PASS, AND NO NEED FOR ONE
    ==================================
    FFNx additionally resets the index from a third hook, over every model
    whose `movement_type != 1`. Without it a model that stops mid-pair keeps
    an odd index and its next move begins on a REPLAY frame, replaying a
    position from an older movement.

    Instead of that pass, replaying requires the movement wrapper to have made
    a real call on the IMMEDIATELY PRECEDING tick:

        replay  <=>  phase == 1  AND  R_TICK_MOVE + 1 == now

    Both hooks evaluate that identical expression, from state neither mutates
    before the other reads it. A model that stops simply stops refreshing
    R_TICK_MOVE, so the predicate goes false on its own and both hooks fall
    back to real calls. Every discontinuity degrades to stock behaviour, and
    the two hooks cannot disagree.

    Register discipline. TRANSLATE is a real call and the recompiled code
    around it treats x0-x18 as clobbered, so every value that has to survive
    one lives in x19-x23 -- which the caller does rely on, hence the frame.
    x25 (the guest register block) is never written.

        x19  model id          x22  guest address of model_pos.x
        x20  record address    x23  a value being carried across TRANSLATE
        w21  replay?
    """
    w = []
    lbl = {}
    fix = []

    def emit(*words):
        w.extend(words)

    def mark(name):
        lbl[name] = len(w)

    def branch(kind, name, *args):
        fix.append((len(w), kind, name, args))
        emit(0)

    def prologue():
        emit(stp64_pre(29, 30, 31, -0x40),
             stp64_off(19, 20, 31, 0x10),
             stp64_off(21, 22, 31, 0x20),
             stp64_off(23, 31, 31, 0x30))

    def epilogue():
        emit(ldp64_off(23, 31, 31, 0x30),
             ldp64_off(21, 22, 31, 0x20),
             ldp64_off(19, 20, 31, 0x10),
             ldp64_post(29, 30, 31, 0x40),
             ret())

    def load_tick(rd):
        fix.append((len(w), 'adrp', rd, (tick_addr & ~0xFFF,)))
        emit(0, ldr(rd, rd, tick_addr & 0xFFF))

    def rdvec(rec_off):
        """guest model_pos -> the record. One TRANSLATE per word: the three
        words can straddle a page boundary and translate() is only valid
        inside the page it resolved."""
        for k in range(3):
            emit(add_imm(0, 22, 4 * k))
            branch('bl_abs', TRANSLATE)
            emit(ldr(1, 0), str_(1, 20, rec_off + 4 * k))

    def wrvec(rec_off):
        for k in range(3):
            emit(add_imm(0, 22, 4 * k))
            branch('bl_abs', TRANSLATE)
            emit(ldr(1, 20, rec_off + 4 * k), str_(1, 0))

    def midwrite():
        """model_pos = initial + (final - initial) / 2, truncating toward
        zero exactly as FFNx's C division does."""
        for k in range(3):
            emit(ldr(1, 20, R_INIT + 4 * k),
                 ldr(2, 20, R_FINAL + 4 * k),
                 sub_reg(2, 2, 1),
                 add_reg_lsr(2, 2, 2, 31),
                 asr(2, 2, 1),
                 add_imm(23, 1, 0),
                 0x0B000000 | (2 << 16) | (23 << 5) | 23,   # add w23, w23, w2
                 add_imm(0, 22, 4 * k))
            branch('bl_abs', TRANSLATE)
            emit(str_(23, 0))

    # =================================================================
    # decide -- READ ONLY. in: w19 = model id. out: x20 = record,
    # w21 = 1 if this frame is a replay frame for this model.
    # =================================================================
    mark('decide')
    fix.append((len(w), 'adrp', 20, (scratch & ~0xFFF,)))
    emit(0,
         add_imm64(20, 20, scratch & 0xFFF),
         add_reg64_lsl(20, 20, 19, REC_SHIFT))
    load_tick(0)
    emit(ldr(1, 20, R_STATE),
         and_mask(1, 1, 1),
         ldr(2, 20, R_TICK_MOVE),
         add_imm(2, 2, 1),
         0x6B000000 | (0 << 16) | (2 << 5) | 31,        # cmp w2, w0
         csel(21, 1, 31, EQ),                            # replay = phase && contiguous
         ret())

    # =================================================================
    # THE SCRIPTED MOVEMENT WRAPPER  (replaces bl MOVE_TGT at 0x9D8590)
    # =================================================================
    mark('move')
    prologue()
    emit(ldr(0, GUEST_REGS, GUEST_ESP), add_imm(0, 0, 4))
    branch('bl_abs', TRANSLATE)
    emit(ldrh(19, 0), 0x71000000 | (MAX_MODELS << 10) | (19 << 5) | 31)
    branch('bcond', 'move_pass', HS)
    branch('bl', 'decide')
    emit(movz(0, EV_STRIDE), mul(22, 19, 0))
    emit(*_mov32(0, EV_BASE + POS_OFF))
    emit(0x0B000000 | (0 << 16) | (22 << 5) | 22)       # add w22, w22, w0
    branch('cbnz', 'move_replay', 21)

    rdvec(R_INIT)
    branch('bl_abs', MOVE_TGT)
    emit(ldr(0, GUEST_REGS, GUEST_EAX), str_(0, 20, R_EAX_MOVE))
    rdvec(R_FINAL)
    load_tick(0)
    emit(str_(0, 20, R_TICK_MOVE))
    emit(movz(0, 1), str_(0, 20, R_STATE))              # next frame replays
    midwrite()
    branch('b', 'move_done')

    mark('move_replay')
    emit(ldr(0, 20, R_EAX_MOVE), str_(0, GUEST_REGS, GUEST_EAX),
         ldr(0, GUEST_REGS, GUEST_ESP), add_imm(0, 0, 4),
         str_(0, GUEST_REGS, GUEST_ESP),
         str_(31, 20, R_STATE))                          # next frame is real
    wrvec(R_FINAL)
    branch('b', 'move_done')

    mark('move_pass')
    branch('bl_abs', MOVE_TGT)
    mark('move_done')
    epilogue()

    # =================================================================
    # THE ARRIVAL TEST WRAPPER  (replaces bl COLL_TGT at 0x9D84EC)
    # Reads the phase, never writes it.
    # =================================================================
    mark('coll')
    prologue()
    emit(ldr(0, GUEST_REGS, GUEST_ESP), add_imm(0, 0, 4))
    branch('bl_abs', TRANSLATE)
    emit(ldr(19, 0))
    emit(*_mov32(0, EV_BASE))
    emit(sub_reg(19, 19, 0), movz(0, RECIP_482), mul(19, 19, 0),
         lsr(19, 19, 16),
         0x71000000 | (MAX_MODELS << 10) | (19 << 5) | 31)
    branch('bcond', 'coll_pass', HS)
    branch('bl', 'decide')
    branch('cbnz', 'coll_replay', 21)

    branch('bl_abs', COLL_TGT)
    emit(ldr(0, GUEST_REGS, GUEST_EAX), str_(0, 20, R_EAX_COLL))
    branch('b', 'coll_done')

    mark('coll_replay')
    emit(ldr(0, 20, R_EAX_COLL), str_(0, GUEST_REGS, GUEST_EAX),
         ldr(0, GUEST_REGS, GUEST_ESP), add_imm(0, 0, 4),
         str_(0, GUEST_REGS, GUEST_ESP))
    branch('b', 'coll_done')

    mark('coll_pass')
    branch('bl_abs', COLL_TGT)
    mark('coll_done')
    epilogue()

    for idx, kind, name, args in fix:
        here = addr(idx)
        if kind == 'bl_abs':
            w[idx] = bl(here, name)
        elif kind == 'bl':
            w[idx] = bl(here, addr(lbl[name]))
        elif kind == 'b':
            w[idx] = b(here, addr(lbl[name]))
        elif kind == 'bcond':
            w[idx] = bcond(here, addr(lbl[name]), args[0])
        elif kind == 'cbnz':
            w[idx] = cbnz(args[0], here, addr(lbl[name]))
        elif kind == 'adrp':
            w[idx] = adrp(name, here, args[0])
        else:
            raise AssertionError('unknown fixup %r' % kind)

    return w, {'move': lbl['move'], 'coll': lbl['coll']}
