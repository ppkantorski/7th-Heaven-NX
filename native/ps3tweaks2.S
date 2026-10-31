/*
 * ps3tweaks2.S -- BUILD 868. The second PS3Tweaks blob's entries; assembled
 * after ps3tweaks_head.S (see there). Its own dead body, as the first blob
 * fills its own.
 *
 * Licensed under GPLv3
 * Copyright (c) 2026 ppkantorski
 */

/* ------------------------------------------------------------ More Pairs
 * Command materia of type 2 (Replacement) pairs as type 6 does, and the
 * All routines take more types.
 *
 * Replacement, x86 0x5CC7B9 `sub edx, 6`, ARM +0x819E54 `sub w20, w8, #6`;
 * Sneak Attack, x86 0x5CBF97, ARM +0x81A5BC `sub w21, w8, #6` (there the mod
 * tests the low byte): 2 counts as 6.
 */
        .global mp_rep, mp_sneak
mp_rep:
        cmp     w8, #2
        b.ne    1f
        mov     w8, #6
1:      sub     w20, w8, #6
        ret
mp_sneak:
        and     w9, w8, #0xFF
        cmp     w9, #2
        b.ne    1f
        orr     w8, w8, #4
1:      sub     w21, w8, #6
        ret

/* Counter, x86 0x5CBE49, ARM +0x81AED4 `cmp w8, #6`: equal for 2 too. */
        .global mp_counter
mp_counter:
        cmp     w8, #2
        ccmp    w8, #6, #4, ne
        ret

/* Primary, x86 0x5CC10D `cmp [ebp-0x10], 7 / ja` and the jump table after
 * it, ARM +0x81B71C `cmp w8, #7` (x21 = context, w8 = the type - 5): 3
 * goes to x86 0x5CC1C9 (ARM +0x81B9B8) as before, every other type to the
 * first case, x86 0x5CC129 (ARM +0x81B7AC), with eax and edx as the
 * table lookup the mod left in place leaves them. CF 0, ZF 1 either way. */
        .global mp_pri
mp_pri:
        strb    wzr, [x21, #0x20]
        mov     w9, #1
        strb    w9, [x21, #0x23]
        cmp     w8, #3
        b.ne    1f
        add     x30, x30, #0x298
        ret
1:      stp     x29, x30, [sp, #-0x10]!
        str     w8, [x21]
        mov     w9, #0xC2A0
        movk    w9, #0x005C, lsl #16
        add     w0, w8, w9
        bl      .Lg2h
        ldrb    w9, [x0]
        str     w9, [x21, #8]
        ldp     x29, x30, [sp], #0x10
        add     x30, x30, #0x8C
        ret

/* Regular All, x86 0x5CCD66, ARM +0x819B88 `mov w9, #0xDF6D` (x19 =
 * context, w8 = edx): pt_mp_all (ps3tweaks2_c.c), then type 9's case
 * (ARM +0x819C00), type 0xA's (+0x819CB8) or the end (+0x819D04). */
        .global mp_all
mp_all:
        stp     x29, x30, [sp, #-0x10]!
        bl      .Lsave
        mov     x0, x19
        adr     x1, .Lg2h
        adr     x2, .Lmp_fn
        mov     w3, w8
        bl      pt_mp_all
        str     x0, [sp]
        bl      .Lrestore
        ldp     x29, x30, [sp], #0x10
        cmp     w0, #0
        cset    w9, ne
        strb    w9, [x19, #0x23]
        cbnz    w0, 1f
        add     x30, x30, #0x178
        ret
1:      cmp     w0, #1
        b.ne    2f
        add     x30, x30, #0x74
        ret
2:      add     x30, x30, #0x12C
        ret

/* Mega All, x86 0x5CCFAD, ARM +0x817784 `mov w25, wzr` (x20 = context):
 * pt_mp_mega, then on to x86 0x5CD03C (ARM +0x817A10). */
        .global mp_mega
mp_mega:
        stp     x29, x30, [sp, #-0x10]!
        bl      .Lsave
        mov     x0, x20
        adr     x1, .Lg2h
        bl      pt_mp_mega
        bl      .Lrestore
        ldp     x29, x30, [sp], #0x10
        add     x30, x30, #0x288
        ret
        .global mp_fn
mp_fn:
.Lmp_fn:
        b       .                       /* build: b x86 0x5CCE7B */

/* ----------------------------------------------------- Fix Equip Display
 * The equip menu's routine, x86 0x707891 (x20 = context; its frame widened
 * to 0x6C by an inline word). Current gear: x86 0x707952, ARM +0xDD86BC
 * `str w8, [x20, #8]` (w8 = edx), on to x86 0x707A4A (ARM +0xDD8A38).
 * The weapon under the cursor, x86 0x707B9B, ARM +0xDD9228, and the
 * armour, x86 0x707BD6, ARM +0xDD90C8 (both `ldr w8, [x20, #0x14]`), on
 * to x86 0x707C1A (ARM +0xDD92A4). pt_fe_* in ps3tweaks2_c.c.
 */
        .global fe_now, fe_weapon, fe_armour
fe_now:
        stp     x29, x30, [sp, #-0x10]!
        bl      .Lsave
        mov     x0, x20
        adr     x1, .Lg2h
        mov     w2, w8
        bl      pt_fe_now
        bl      .Lrestore
        ldp     x29, x30, [sp], #0x10
        add     x30, x30, #0x378
        ret
fe_weapon:
        stp     x29, x30, [sp, #-0x10]!
        bl      .Lsave
        mov     x0, x20
        adr     x1, .Lg2h
        bl      pt_fe_weapon
        bl      .Lrestore
        ldp     x29, x30, [sp], #0x10
        add     x30, x30, #0x78
        ret
fe_armour:
        stp     x29, x30, [sp, #-0x10]!
        bl      .Lsave
        mov     x0, x20
        adr     x1, .Lg2h
        bl      pt_fe_armour
        bl      .Lrestore
        ldp     x29, x30, [sp], #0x10
        add     x30, x30, #0x1D8
        ret

/* -------------------------------------------------- Unlimited Auto Sense
 * The formatter's number field, x86 0x419BA5, ARM +0x86698 `mov w8, #1`
 * (x21 = context): pt_us_case (ps3tweaks2_c.c), then on at x86 0x419C0F
 * (ARM +0x86868), which writes the digits out. us_case_show also draws
 * the steal / morph texts.
 */
        .global us_case, us_case_show
us_case:
        mov     w9, #0
        b       1f
us_case_show:
        mov     w9, #1
1:      stp     x29, x30, [sp, #-0x10]!
        bl      .Lsave
        mov     x0, x21
        adr     x1, .Lg2h
        mov     w2, w9
        adr     x3, .Lus_fns
        bl      pt_us_case
        bl      .Lrestore
        ldp     x29, x30, [sp], #0x10
        add     x30, x30, #0x1CC
        ret

/* The formatter's two calls, x86 0x41996F / 0x4199F0, ARM +0x8557C /
 * +0x85A24 `bl` x86 0x419A8F (x20 = context, its return slot pushed): the
 * slot gets the x86 return address, which the show code tells them apart
 * by, then the call. */
        .global us_call_a, us_call_b
us_call_a:
        mov     w9, #0x9974
        b       1f
us_call_b:
        mov     w9, #0x99F5
1:      movk    w9, #0x41, lsl #16
        stp     x16, x17, [sp, #-0x20]!
        str     x30, [sp, #0x10]
        mov     w17, w9
        ldr     w0, [x20, #0x10]
        bl      .Lg2h
        str     w17, [x0]
        ldr     x30, [sp, #0x10]
        ldp     x16, x17, [sp], #0x20
        b       .Lus_fn3

/* Sense, x86 0x5CA10F `cmp [ecx+0x9AB10C], 0x7530; jb`, ARM +0x80A578
 * `lsr w8, w8, #4` before `cmp w8, #0x753; b.lo`: the limit is 0x7FFFFFFF,
 * so w8 = 0 below it, 0x753 from it on. */
        .global us_limit
us_limit:
        tbnz    w8, #31, 1f
        add     w8, w8, #1
        tbnz    w8, #31, 1f
        mov     w8, wzr
        ret
1:      mov     w8, #0x753
        ret

        .global us_fn0, us_fn1, us_fn2, us_fn3
        .p2align 2
.Lus_fns:
us_fn0: b       .                       /* build: b x86 0x419457 */
us_fn1: b       .                       /* build: b x86 0x6F5B03 */
us_fn2: b       .                       /* build: b x86 0x6C6B06 */
us_fn3:
.Lus_fn3:
        b       .                       /* build: b x86 0x419A8F */

/* ------------------------------------------------ Toggle Animation Skip
 * AnimationSkip/ToggleAnimationSkip: bit 0 of 0x9ABE68 (kept by the mod's
 * shorter clear at x86 0x41CCE0) turns the animation skip on and off.
 *
 * x86 0x5DA117 `mov [edx], cl`, ARM +0x849F6C `strb w21, [x0]`: the byte is
 * only copied while the bit is clear.
 */
        .global ta_store, ta_toggle
ta_store:
        sub     sp, sp, #0x30
        stp     x0, x30, [sp]
        stp     x8, x9, [sp, #0x10]
        str     x10, [sp, #0x20]
        mov     w0, #0xBE68
        movk    w0, #0x9A, lsl #16
        bl      .Lg2h
        ldrb    w8, [x0]
        ldr     x0, [sp]
        tbnz    w8, #0, 1f
        strb    w21, [x0]
1:      ldp     x8, x9, [sp, #0x10]
        ldr     x10, [sp, #0x20]
        ldp     x0, x30, [sp]
        add     sp, sp, #0x30
        ret

/* x86 0x41C0C1 `mov [ebp-4], 0`, ARM +0x91B60 `str wzr, [x0]` (x26 =
 * context), the battle loop's routine before its loop: on the press of
 * the button bit 0 of 0x9A85D4 (al > 1 while it is held) the bit flips
 * (0x9ABE68 = 3 or 4), on its release only the bit is kept. al and ecx as
 * the mod's code leaves them. */
ta_toggle:
        str     wzr, [x0]
        sub     sp, sp, #0x30
        stp     x0, x30, [sp]
        stp     x8, x9, [sp, #0x10]
        stp     x10, x11, [sp, #0x20]
        mov     w0, #0x85D4
        movk    w0, #0x9A, lsl #16
        bl      .Lg2h
        ldrb    w11, [x0]
        and     w11, w11, #1
        str     w11, [x26, #4]
        mov     w0, #0xBE68
        movk    w0, #0x9A, lsl #16
        bl      .Lg2h
        ldrb    w8, [x0]
        cbz     w11, 2f
        sxtb    w9, w8
        cmp     w9, #1
        b.gt    3f
        and     w8, w8, #1
        add     w8, w8, #3
        b       3f
2:      and     w8, w8, #1
3:      strb    w8, [x0]
        strb    w8, [x26]
        ldp     x8, x9, [sp, #0x10]
        ldp     x10, x11, [sp, #0x20]
        ldp     x0, x30, [sp]
        add     sp, sp, #0x30
        ret

/* x86 0x436DE6, the counter at 0x9AEA98 wraps at 0x50 (was 0x80), the
 * mod's freed slots: ARM +0xF90C8 `and w21, w8, #0x7F` (w8 = it + 1). */
        .global ta_count
ta_count:
        cmp     w8, #0x50
        csel    w21, w8, wzr, lt
        ret

/* ---------------------------------------------------- Multi Linked Slots
 * x86 0x5CB921 `and edx, 0xFF ... cmp edx, 7` becomes `and edx, 3 ...
 * cmp edx, 3`: ARM +0x815718 `cmp w8, #7` (w8 = the byte, stored as edx
 * next). The rest of MultiLinkedSlots is inline words.
 */
        .global ml_slot
ml_slot:
        and     w8, w8, #3
        cmp     w8, #3
        ret

/* ------------------------------------------------- Break Damage/HP Limit
 * The damage sprite's value, x86 0x5BB6FD, ARM +0x7C5A90 `ldrh w8, [x0]`
 * (x22 = context, eax = the record): ecx is the word at +0xE with the word
 * at +0x14 >> 15 (bd_push3, the Unlimited files: >> 3) as its high half.
 */
        .global bd_push, bd_push3
bd_push3:
        sub     sp, sp, #0x30
        stp     x0, x30, [sp]
        stp     x9, x10, [sp, #0x10]
        mov     w10, #3
        b       1f
bd_push:
        sub     sp, sp, #0x30
        stp     x0, x30, [sp]
        stp     x9, x10, [sp, #0x10]
        mov     w10, #15
1:      ldrh    w8, [x0]
        stp     w8, w10, [sp, #0x20]            /* g2h takes x8-x10 */
        ldr     w0, [x22]
        add     w0, w0, #0x14
        bl      .Lg2h
        ldrh    w9, [x0]
        ldr     w10, [sp, #0x24]
        lsr     w9, w9, w10
        ldr     w8, [sp, #0x20]
        orr     w9, w8, w9, lsl #16
        str     w9, [x22, #4]
        ldp     x9, x10, [sp, #0x10]
        ldp     x0, x30, [sp]
        add     sp, sp, #0x30
        ret

/* x86 0x5DA58E, ARM +0x84E314 `ldrh w20, [x0]` (x19 = context; ax =
 * [ebp+0x10]): eax = ([0x99E51C] >> 16) << 15 (bd_store3: << 3) with ax
 * += it, as stored. */
        .global bd_store, bd_store3
bd_store3:
        sub     sp, sp, #0x30
        stp     x0, x30, [sp]
        stp     x8, x9, [sp, #0x10]
        mov     w8, #3
        b       1f
bd_store:
        sub     sp, sp, #0x30
        stp     x0, x30, [sp]
        stp     x8, x9, [sp, #0x10]
        mov     w8, #15
1:      str     x10, [sp, #0x20]
        str     w8, [sp, #0x28]                 /* g2h takes x8-x10 */
        ldrh    w20, [x0]
        mov     w0, #0xE51C
        movk    w0, #0x99, lsl #16
        bl      .Lg2h
        ldr     w9, [x0]
        lsr     w9, w9, #16
        ldr     w8, [sp, #0x28]
        lsl     w9, w9, w8
        add     w10, w9, w20
        bfxil   w9, w10, #0, #16
        str     w9, [x19]
        and     w20, w9, #0xFFFF
        ldr     x10, [sp, #0x20]
        ldp     x8, x9, [sp, #0x10]
        ldp     x0, x30, [sp]
        add     sp, sp, #0x30
        ret

/* The digit routine's one call, x86 0x5BB778, ARM +0x7C5D08 `bl` x86
 * 0x5BB9C9 (x23 = context, its return slot pushed): pt_bd_digits. */
        .global bd_digits5, bd_digits9
bd_digits5:
        mov     w9, #5
        b       1f
bd_digits9:
        mov     w9, #9
1:      stp     x29, x30, [sp, #-0x10]!
        bl      .Lsave
        mov     x0, x23
        adr     x1, .Lg2h
        mov     w2, w9
        bl      pt_bd_digits
        bl      .Lrestore
        ldp     x29, x30, [sp], #0x10
        ret

/* The cap, x86 0x5DAA92, ARM +0x84B650 `mov w8, #0x270F` (x21 = context):
 * bd_cap pt_bd_cap's, bd_cap99 99,999. */
        .global bd_cap, bd_cap99
bd_cap:
        stp     x29, x30, [sp, #-0x10]!
        bl      .Lsave
        mov     x0, x21
        adr     x1, .Lg2h
        bl      pt_bd_cap
        str     x0, [sp, #0x40]                 /* restored as x8 */
        bl      .Lrestore
        ldp     x29, x30, [sp], #0x10
        ret
bd_cap99:
        mov     w8, #0x869F
        movk    w8, #1, lsl #16
        ret

/* HP limit 9,999 -> 32,767 where the constant is in a register the clamp
 * also stores: x86 0x5CE816 `cmp eax, 0x270F`, ARM +0x816C48 / +0x816C7C
 * `cmp w8, w21`. */
        .global bd_cmp7fff
bd_cmp7fff:
        sub     sp, sp, #0x10
        str     x9, [sp]
        mov     w9, #0x7FFF
        cmp     w8, w9
        ldr     x9, [sp]
        add     sp, sp, #0x10
        ret

/* x86 0x65362C `mov [eax+0x88], 0x270F` with the mod's bytes at +7:
 * 0x7FFF0F (ARM +0xA49AA0 `mov w8, #0x270F`). */
        .global bd_hp88
bd_hp88:
        mov     w8, #0xFF0F
        movk    w8, #0x7F, lsl #16
        ret

/* Stores of a new constant where the old one is in a register other code
 * shares: `str wN, [x0]` / `strh wN, [x0]`. */
        .global bd_st5, bd_st108, bd_sth7d, bd_sth17c
bd_st5:
        sub     sp, sp, #0x10
        str     x9, [sp]
        mov     w9, #5
        str     w9, [x0]
        b       2f
bd_st108:
        sub     sp, sp, #0x10
        str     x9, [sp]
        mov     w9, #0x108
        str     w9, [x0]
        b       2f
bd_sth7d:
        sub     sp, sp, #0x10
        str     x9, [sp]
        mov     w9, #0x7D
        strh    w9, [x0]
        b       2f
bd_sth17c:
        sub     sp, sp, #0x10
        str     x9, [sp]
        mov     w9, #0x17C
        strh    w9, [x0]
2:      ldr     x9, [sp]
        add     sp, sp, #0x10
        ret

/* The HP texts' shared calls, ARM +0xD40F58 (x21) / +0xD47638 (x20) `bl`
 * x86 0x6F9739, return slot pushed: pt_bd_args, then the call. */
        .global bd_hp_call, bd_heal_call
bd_hp_call:
        mov     w9, #0x118
        mov     x10, x21
        b       1f
bd_heal_call:
        mov     w9, #0x17A
        mov     x10, x20
1:      stp     x29, x30, [sp, #-0x10]!
        bl      .Lsave
        mov     x0, x10
        adr     x1, .Lg2h
        mov     w2, w9
        bl      pt_bd_args
        bl      .Lrestore
        ldp     x29, x30, [sp], #0x10
        b       .Lbd_fn
        .global bd_fn
bd_fn:
.Lbd_fn:
        b       .                       /* build: b x86 0x6F9739 */
