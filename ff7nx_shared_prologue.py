"""
ff7nx_shared_prologue.py -- one shared dispatcher prologue, called from a
short per-site stub, instead of each of effect10/effect100/camera paying for
its own ~19-word copy of the same sequence.

WHY THIS IS SAFE IN A WAY THAT PICKING NEW SCRATCH REGISTERS WOULD NOT BE
--------------------------------------------------------------------------
The hook is "an unconditional B into .text" spliced into code the recompiler
generated -- not a real function call. Whatever the surrounding translated
block happens to be holding in any given register at that exact PC is not
something static analysis or emulation against our own algorithm can see;
it is a property of code we don't have. The only registers with any real
evidence of being safe to clobber at these three specific hook points are
the ones the ALREADY-SHIPPED, hardware-verified cave already clobbers there:
x16/x17 (IP0/IP1 -- reserved intra-procedure-call scratch by the AAPCS64,
never allocated to a live variable by any compliant compiler), x0 (written
before read in the existing cave, so provably dead-in at this PC on real
hardware -- but ONLY on the path that writes it; see the correction note
below), x8/F (deliberately saved/restored, so already known to survive
correctly), x30/LR (the reference's own `bl TRANSLATE` clobbers it -- per
AAPCS, any real call is free to -- with no save/restore, on every single
frame where the flag is set; that has shipped and works, which is exactly
the same kind of hardware evidence x16/x17's exemption rests on), and sp
(already used transiently the same way).

So rather than reach for x9-x15/x18-x20 to hold table pointers, a site id,
etc. -- which would need FRESH hardware verification at these three PCs
before anyone could trust it -- this stays inside exactly that register set
and uses the stack for the two extra values of state that need to survive
across the call (the masked index, and x0 itself -- see the correction
note below).

A CORRECTION THE DIFFERENTIAL TEST FOUND, RECORDED HONESTLY
-------------------------------------------------------------
The first version of this file assumed x0 was free to use as scratch on the
not-set path, on the reasoning that the reference cave writes x0 before
reading it, so nothing downstream should depend on x0's incoming value.
That reasoning does not hold: "the reference writes x0 before reading it"
is true only on the path where the flag WAS set. On the not-set path the
reference does not touch x0 at all -- it comes out exactly as it went in --
and test_shared_prologue.py caught the first version returning a clobbered
x0 (0, from the flag-byte load) on that path instead. Whether real
translated code actually depends on x0 surviving an untaken hook is not
something this project can determine by inspection; the reference is the
only evidence available, and it preserves x0, so this does too now. x0 is
saved to the stack before it is used as scratch and restored before the
not-set return. Status (proceed vs not-set) is now signalled through an
explicit w16 flag instead of "x0 == 0", since x0 can no longer double as
that sentinel once it is a preserved value rather than a scratch one.

THE SPLIT
---------
Per site (the only part that has to differ, because ARM64 cannot
parameterise *which* register `ctx` is):

    ldr  w16, [ctx, #idx_off]      ; idx           (ctx_reg differs per site)
    and  w16, w16, #mask_bits      ; masked idx     (mask_bits differs)
    adrp x17, page(entry_va)       ; entry_va = this site's 12-byte record in
    add  x17, x17, #lo12(entry_va) ;   the .rodata tail-gap table -- differs
    sub  sp,  sp,  #0x10
    str  w16, [sp, #0]             ; the ONLY extra storage this design uses:
                                    ; the stack, not a new register
    bl   SHARED
    add  sp,  sp,  #0x10
    cbz  w16, OUT                  ; SHARED's status flag -- 0 = not set
    b    SKIP_SHARED               ; proceed -> jump over SHARED's body to
                                    ;   whatever legitimately follows it (the
                                    ;   dispatch cases, or OUT directly if
                                    ;   there are none) -- see the SECOND
                                    ;   correction note below for why this
                                    ;   branch has to exist at all

Shared, emitted once, reached by `bl` from every site (position-independent:
no absolute address is baked into it, only what x17/x8/sp/lr already hold on
entry, so it does not matter where in the cave it physically sits):

    str  x0,  [sp, #8]             ; save x0 -- must come back unclobbered
                                    ;   on the not-set path (see correction
                                    ;   above); [sp,#8] is free -- the stub's
                                    ;   own slot only uses [sp,#0]
    ldr  w0,  [x17, #0]            ; delta_flag = flag_addr - entry_va,
                                    ;   always positive here: the flag block
                                    ;   is in .data/.bss, far above .rodata
    ldr  w16, [sp, #0]             ; masked idx (1st use -- see the SECOND
                                    ;   correction note: the flag block is
                                    ;   per-slot, so entry_va+delta_flag ALONE
                                    ;   is only slot 0's address)
    add  w0,  w0,  w16              ; w0 = delta_flag + idx
    add  x16, x0,  x17             ; x16 = entry_va + (delta_flag+idx)
                                    ;   = &flag[idx], correctly rebased off
                                    ;   x17 the same way adrp/add already
                                    ;   rebases it today
    ldrb w0,  [x16, #0]            ; flag byte
    cbz  w0,  SH_OUT                ; not set
    strb wzr, [x16, #0]            ; consume the flag
    ldr  w16, [sp, #0]             ; masked idx, restored a 2nd time (x16 was
                                    ;   needed above for the flag byte, same
                                    ;   reason the original inline prologue
                                    ;   re-reads ctx a second time instead of
                                    ;   keeping idx around)
    ldr  w0,  [x17, #8]            ; stride
    mul  w16, w16, w0               ; idx * stride
    ldr  w0,  [x17, #4]            ; data_base (a GUEST address -- no
                                    ;   rebasing needed, unlike flag_addr)
    add  w0,  w0,  w16              ; guest &array_data[idx]
    stp  x8,  x30, [sp, #-0x10]!   ; F must survive, same as the inline
                                    ;   version; LR must ALSO survive --
                                    ;   SHARED is a real subroutine and must
                                    ;   `ret` back to its caller, and
                                    ;   TRANSLATE is free to clobber x30 like
                                    ;   any real call (its model does)
    bl   TRANSLATE
    ldp  x8,  x30, [sp], #0x10
    movz w16, #1                    ; status = proceed (idx*stride, left in
                                    ;   w16 above, can legitimately be 0, so
                                    ;   it cannot double as this flag)
    ret                             ; x0 = host &array_data[idx], w16 = 1
SH_OUT:
    ldr  x0,  [sp, #8]              ; restore x0, byte-for-byte
    movz w16, #0                    ; status = not-set
    ret

A SECOND CORRECTION THE DIFFERENTIAL TEST FOUND, ALSO RECORDED HONESTLY
------------------------------------------------------------------------
Two more real bugs, both caught by test_shared_prologue.py rather than
found by inspection:

1. The first version computed x16 as entry_va + delta_flag and stopped --
   that is the flag BLOCK's base address, not this slot's byte. Every real
   site has more than one slot (effect10 has 10, effect100 has 100, camera
   has 16), each with its own flag byte at flag_addr + idx. Testing and
   consuming byte 0 of the block regardless of idx meant idx=0 happened to
   work by coincidence (its offset really is 0) while every other idx tested
   and consumed the WRONG slot's flag entirely. Fixed by folding the masked
   idx into the 32-bit sum before it gets rebased onto x17, so x16 comes out
   as &flag[idx] directly.

2. The stub's cbz is only TAKEN on the not-set path; falling through (the
   proceed path) landed on the very next word in every real layout -- which
   is SHARED's own first instruction, because every call site places SHARED
   immediately after its stub. That re-enters SHARED a second time on every
   single proceed call. The replay harmlessly reads its own just-cleared
   flag byte as not-set, but its SH_OUT `ret` still targets the address the
   stub's ORIGINAL `bl` set in x30 -- so control lands back on the stub's
   `add sp, sp, #0x10` a second time, which executes again with no matching
   second `sub`, leaving sp permanently +0x10 off for the rest of the cave.
   Fixed with one extra instruction: an unconditional `b` right after the
   cbz that jumps over SHARED's entire body on the proceed path.

Both were invisible to a read-through of the assembly -- the first looked
plausible because idx=0 masked the bug in casual testing, the second
because "control-flow falls through to the next instruction" reads as
correct unless you already know that instruction is a different
subroutine's entry point. Both were caught immediately once
test_shared_prologue.py compared real per-slot idx values and full register
state against the hardware-verified reference, which is the whole reason
that test exists rather than trusting this file by inspection.

Every instruction here is one that build_cave/build_cave_reference already
emit somewhere, in the same registers -- this rearranges which of the three
call sites owns which copy, plus the save/restore/status/offset bookkeeping
the corrections above needed. Verified against the hardware-proven reference
by test_shared_prologue.py: prologue-only AND as a full cave with each
site's real dispatch cases appended, register-for-register except x16/x17
(declared scratch, same convention test_dispatch_shrink.py already uses).
"""
import a64 as A

TRANSLATE = 0x10FC3A0

ENTRY_SIZE = 12   # delta_flag u32, data_base u32, stride u32


def pack_table_entry(flag_addr, entry_va, data_base, stride):
    """
    Build one 12-byte .rodata record for a site. Raises if the delta would
    not fit the always-positive assumption the shared body relies on (true
    for every real site: flag blocks live in .data/.bss, well above the
    .rodata tail gap the table itself lives in).
    """
    delta = flag_addr - entry_va
    if not (0 <= delta <= 0xFFFFFFFF):
        raise ValueError(
            'delta_flag %d out of range for entry_va 0x%X, flag_addr 0x%X -- '
            'the shared body assumes flag_addr > entry_va (flag block above '
            'the .rodata table), which does not hold here' % (delta, entry_va,
                                                               flag_addr))
    if not (0 <= data_base <= 0xFFFFFFFF):
        raise ValueError('data_base 0x%X does not fit 32 bits' % data_base)
    if not (0 <= stride <= 0xFFFFFFFF):
        raise ValueError('stride 0x%X does not fit 32 bits' % stride)
    import struct
    return struct.pack('<III', delta & 0xFFFFFFFF, data_base, stride)


def build_shared_prologue(shared_va):
    """
    Emitted once. See module docstring for the general shape -- with one
    correction that docstring predates: x0 must come out of the NOT-SET
    path exactly as it went in, because that is what the hardware-verified
    reference does (it never touches x0 until AFTER the flag test passes),
    and a hook spliced into live translated code cannot assume x0 is dead
    across a path that does nothing. So x0 is saved before it is used as
    scratch for the delta/address computation, and restored on the not-set
    exit -- using the SAME 16-byte slot the stub already reserved for the
    masked idx (idx lives at [sp,#0]; x0 is saved at [sp,#8], which the stub
    leaves unused), so this costs two extra words and zero extra stack
    traffic, not a second sub/add pair.

        str  x0,  [sp, #8]          ; save x0 -- must return unclobbered on
                                     ;   the not-set path
        ldr  w0,  [x17, #0]         ; delta_flag
        ldr  w16, [sp, #0]          ; masked idx (1st use) -- the flag block
                                     ;   is per-slot, so entry_va+delta_flag
                                     ;   ALONE is only slot 0's address; this
                                     ;   is the fix for a real bug the
                                     ;   differential test caught (see module
                                     ;   docstring's second correction note)
        add  w0,  w0,  w16          ; w0 = delta_flag + idx
        add  x16, x0,  x17          ; x16 = &flag[idx] (rebased off x17)
        ldrb w0,  [x16, #0]         ; flag byte
        cbz  w0,  SH_OUT
        strb wzr, [x16, #0]         ; consume the flag
        ldr  w16, [sp, #0]          ; masked idx, restored a 2nd time
        ldr  w0,  [x17, #8]         ; stride
        mul  w16, w16, w0
        ldr  w0,  [x17, #4]         ; data_base
        add  w0,  w0,  w16          ; guest &array_data[idx]
        stp  x8,  x30, [sp, #-0x10]!  ; F and LR both preserved across the
                                     ;   real call (LR because SHARED must
                                     ;   `ret` back to its caller afterward,
                                     ;   and TRANSLATE's model clobbers x30
                                     ;   exactly as a real AAPCS call may)
        bl   TRANSLATE
        ldp  x8,  x30, [sp], #0x10
        movz w16, #1                ; status = proceed (w16 above this point
                                     ;   holds idx*stride, which can
                                     ;   legitimately be 0 -- it cannot double
                                     ;   as the status flag the stub branches
                                     ;   on, so it is set fresh here)
        ret                          ; x0 = host pointer, w16 = 1
    SH_OUT:
        ldr  x0,  [sp, #8]           ; restore x0, byte-for-byte
        movz w16, #0                 ; status = not-set
        ret
    """
    w = []

    def pc(i=None):
        return shared_va + 4 * (len(w) if i is None else i)

    w.append(A.str64(0, A.SP, 8))         # save x0 -- must survive not-set
    w.append(A.ldr(0, 17, 0))             # w0 = delta_flag
    w.append(A.ldr(16, A.SP, 0))          # w16 = masked idx (1st use: the
                                           #   flag block is per-slot, so the
                                           #   BASE address alone is wrong --
                                           #   this is the fix; the first
                                           #   version tested byte 0 of the
                                           #   flag block for every idx)
    w.append(A.add_reg(0, 0, 16))         # w0 = delta_flag + idx
    w.append(A.add_reg64(16, 0, 17))      # x16 = flag_addr + idx = &flag[idx]
    w.append(A.ldrb(0, 16, 0))            # w0 = flag byte
    cbz_i = len(w)
    w.append(0)                           # cbz w0, SH_OUT -- patched below
    w.append(A.strb(A.WZR, 16, 0))        # consume the flag
    w.append(A.ldr(16, A.SP, 0))          # w16 = masked idx, restored
    w.append(A.ldr(0, 17, 8))             # w0 = stride
    w.append(A.mul(16, 16, 0))            # w16 = idx * stride
    w.append(A.ldr(0, 17, 4))             # w0 = data_base
    w.append(A.add_reg(0, 0, 16))         # w0 = guest &array_data[idx]
    # F and LR must both survive this call. F because the inline reference
    # does the same. LR because SHARED, unlike the inline reference, is a
    # real subroutine that must `ret` back to its caller afterward -- and
    # TRANSLATE's model clobbers x30 exactly as a real AAPCS call is free to
    # (caller-saved), so without this SHARED's own `ret` below would read a
    # clobbered LR and jump nowhere on every proceed-path call. Caught by
    # the differential test before it reached hardware.
    w.append(A.stp64_pre(8, 30, A.SP, -0x10))
    w.append(A.bl(pc(), TRANSLATE))
    w.append(A.ldp64_post(8, 30, A.SP, 0x10))
    w.append(A.movz(16, 1))               # status = proceed (w16 is scratch;
                                           #   idx*stride, left over here from
                                           #   above, can legitimately be 0,
                                           #   so it cannot double as this
                                           #   flag -- it must be set fresh)
    w.append(A.ret())                     # proceed path: x0 = host pointer

    sh_out = pc()
    w[cbz_i] = A.cbz(0, shared_va + 4 * cbz_i, sh_out)
    w.append(A.ldr64(0, A.SP, 8))         # SH_OUT: restore x0, byte-for-byte
    w.append(A.movz(16, 0))               # status = not-set
    w.append(A.ret())
    return w


def build_dispatch_stub(cave, site, mask_bits, entry_va, shared_va,
                        skip_va=None):
    """
    Per-site. `site` is the disp_hook sub-dict (ctx_reg, idx_off), same shape
    build_cave already takes. Returns (words, cbz_index); the caller patches
    words[cbz_index] once it knows the address of this site's OUT (the
    displaced instruction + branch back -- unchanged from build_cave today).

    `skip_va`, if given, is the address the PROCEED path (cbz not taken)
    falls through to. It defaults to shared_va + 4*shared_word_count() --
    correct ONLY when SHARED is placed immediately after THIS stub, which is
    what every differential test here does (a private per-site copy, for
    isolated testability -- see the module docstring: SHARED is
    position-independent, so testing it that way is faithful to a real
    build that places ONE shared copy and reaches it from several stubs at
    different addresses). A caller doing that -- multiple real stubs
    sharing one SHARED -- must pass its own site's true post-stub address
    (cave + 4*stub_word_count()) explicitly, since shared_va's own formula
    is then only correct for whichever one stub happens to sit right before
    SHARED.
    """
    if skip_va is None:
        skip_va = shared_va + 4 * shared_word_count()
    ctx = site['ctx_reg']
    io = site['idx_off']
    w = []

    def pc(i=None):
        return cave + 4 * (len(w) if i is None else i)

    w.append(A.ldr(16, ctx, io))
    w.append(A.and_mask(16, 16, mask_bits))
    w.append(A.adrp(17, pc(), entry_va & ~0xFFF))
    w.append(A.add_imm64(17, 17, entry_va & 0xFFF))
    w.append(A.sub_imm64(A.SP, A.SP, 0x10))
    w.append(A.str_(16, A.SP, 0))
    w.append(A.bl(pc(), shared_va))
    w.append(A.add_imm64(A.SP, A.SP, 0x10))
    cbz_i = len(w)
    w.append(0)                            # cbz w16, OUT -- patched by caller
                                            # (w16 is SHARED's explicit status
                                            # flag -- 0/1 -- not x0, which is
                                            # now preserved rather than used
                                            # as a sentinel)
    # PROCEED falls through here if the cbz above is not taken. See skip_va's
    # own docstring above for what this must point to and why.
    w.append(A.b(pc(), skip_va))
    return w, cbz_i


def build_cave_shared(cave, site, mask_bits, entry_va, shared_va,
                      data_base, stride, fields, cases, sym, k):
    """
    Emits the SAME cave BEHAVIOR as ff7nx_dispatch.build_cave -- the real,
    already-shipping, case-consolidated dispatcher cave -- with the ~19-22
    word inline prologue replaced by a 10-word stub that calls into a SHARED
    subroutine placed once, externally, and shared across every site that
    uses this mechanism.

    `entry_va` is this site's 12-byte descriptor-table entry (see
    pack_table_entry), already written wherever it lives (the .rodata tail
    gap, in the real build). `shared_va` is SHARED's real, already-placed
    address; SHARED is NOT re-emitted here.

    Everything from "---- dispatch ----" onward is copied verbatim from
    ff7nx_dispatch.build_cave -- same variable names, same order -- on
    purpose: that logic (_plan_dispatch's case-grouping/shared-body
    consolidation) is itself an already-verified space optimization, and
    duplicating it by hand here would risk it silently drifting out of sync
    with the real one. This function must be differentially tested against
    build_cave directly (not build_cave_reference) before it is trusted:
    see test_shared_prologue.py's build_cave_shared comparison.
    """
    import ff7nx_dispatch as D

    F = site['fn_reg']
    w = []

    stub_len = stub_word_count()
    skip_va = cave + 4 * stub_len
    stub, cbz_i = build_dispatch_stub(cave, site, mask_bits, entry_va,
                                      shared_va, skip_va=skip_va)
    w += stub
    assert len(w) == stub_len

    def pc(i=None):
        return cave + 4 * (len(w) if i is None else i)

    # ---- dispatch --------------------------------------------------------
    groups, shared = D._plan_dispatch(fields, cases, sym)
    plain = [g for g in groups if shared is None or g[2] is not None]
    sharing = [g for g in groups if shared is not None and g[2] is None]
    ordered = plain + sharing

    out_jumps = []          # word indices of `b OUT`, patched once OUT is known
    shared_jumps = []       # word indices of `b SHARED_CASE`
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

        w += D._ops(fields, spec, k)

        if shared is not None and guard is None:
            if not falls_into_shared:
                shared_jumps.append(len(w))
                w.append(0)                         # b SHARED_CASE -- patched
        elif not last_group:
            out_jumps.append(len(w))
            w.append(0)                             # b OUT -- patched
        # a last group with no shared block falls straight through to OUT

    shared_at = len(w)
    if shared is not None:
        w += D._ops(fields, [shared], k)

    out = pc()
    w[cbz_i] = A.cbz(16, cave + 4 * cbz_i, out)
    for i in out_jumps:
        w[i] = A.b(cave + 4 * i, out)
    for i in guard_jumps:
        w[i] = A.bcond(cave + 4 * i, out, A.LE)
    for i in shared_jumps:
        w[i] = A.b(cave + 4 * i, cave + 4 * shared_at)
    for i, gi in next_patches:
        nxt = cave + 4 * group_start[gi] if gi < len(ordered) else out
        w[i] = A.bcond(cave + 4 * i, nxt, A.NE)

    w.append(site['displaced'])
    w.append(A.b(pc(), site['hook'] + 4))
    return w


def stub_word_count():
    """Fixed regardless of site -- only the immediates inside vary."""
    w, _ = build_dispatch_stub(0, {'ctx_reg': 0, 'idx_off': 0}, 0, 0, 0)
    return len(w)


def shared_word_count():
    return len(build_shared_prologue(0))