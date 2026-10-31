/*
 * ps3tweaks.S -- BUILD 868. The first PS3Tweaks blob's entries; assembled
 * after ps3tweaks_head.S (see there).
 *
 * Licensed under GPLv3
 * Copyright (c) 2026 ppkantorski
 */
/* ------------------------------------------------------------------ Level Cap
 * x86 0x7B5A00: the cap for the current game moment (u16 at 0xDC08DC) is the
 * byte after the first table entry whose s16 moment is >= it; the table ends
 * with moment 0x7FFF. The hooks then compare the level byte with it, signed.
 *
 * lc_core: w1 = value  ->  w1 = (s8)value - (s8)cap. Nothing else changes
 * but the flags.
 */
lc_core:
        stp     x29, x30, [sp, #-0x40]!
        stp     x0, x2, [sp, #0x10]
        stp     x8, x9, [sp, #0x20]
        str     x10, [sp, #0x30]
        mov     w0, #0x08DC
        movk    w0, #0x00DC, lsl #16
        bl      .Lg2h
        ldrsh   w8, [x0]
        adr     x9, .Llc_table
1:      ldrb    w10, [x9]
        ldrb    w2, [x9, #1]
        orr     w10, w10, w2, lsl #8
        sxth    w10, w10
        cmp     w10, w8
        b.ge    2f
        add     x9, x9, #3
        b       1b
2:      ldrsb   w2, [x9, #2]
        sxtb    w1, w1
        sub     w1, w1, w2
        ldr     x10, [sp, #0x30]
        ldp     x8, x9, [sp, #0x20]
        ldp     x0, x2, [sp, #0x10]
        ldp     x29, x30, [sp], #0x40
        ret

/* x86 0x5C77D0, battle-end level up (ARM +0x7F7744 `sub w9, w8, #0x63`,
 * w8 = the level): w9 = level - cap, so `jge` skips the gain at the cap. */
        .global lc_site_a
lc_site_a:
        stp     x1, x30, [sp, #-0x10]!
        mov     w1, w8
        bl      lc_core
        mov     w9, w1
        ldp     x1, x30, [sp], #0x10
        ret

/* x86 0x5C839D, the level shown on the results screen (ARM +0x7FC720
 * `sub w8, w9, #0x64`, w10 = new level - 1): w8 = w10 - cap. */
        .global lc_site_b
lc_site_b:
        stp     x1, x30, [sp, #-0x10]!
        mov     w1, w10
        bl      lc_core
        mov     w8, w1
        ldp     x1, x30, [sp], #0x10
        ret

/* --------------------------------------------------- Enemy Speed Multiplier
 * x86 0x433B21 `mov [eax+0x9A8B12], cx` (a battle actor's speed, eax = actor
 * * 0x44): for an enemy (eax > 0x88) the cave stores cx * mult instead and
 * leaves the product in ecx. ARM +0xEDF3C `strh w19, [x0]` (w19 = cx, x0 =
 * host address, x20 = the x86 context, eax at +0).
 */
        .global sm_site
sm_site:
        stp     x9, x10, [sp, #-0x10]!
        mov     w10, w19
        ldr     w9, [x20]
        cmp     w9, #0x88
        b.le    1f
        ldr     w9, .Lsm_mult
        mul     w10, w10, w9
        str     w10, [x20, #4]
1:      strh    w10, [x0]
        ldp     x9, x10, [sp], #0x10
        ret
        .p2align 2
        .global sm_mult
sm_mult:
.Lsm_mult:
        .space  4

/* ----------------------------------------------- Steal Modifier: Add Luck
 * x86 0x5CA8F8: eax (the stealer's level + the level modifier) += the
 * stealer's luck, byte [0x9AB0F1 + 0x68 * actor], actor = [[0x99CE0C]].
 * ARM +0x80F980 `add w8, w8, #0x28` (w8 = the level): w8 = level +
 * modifier + luck. The modifier is the option's own `add eax, imm8`.
 */
        .global sl_site
sl_site:
        stp     x29, x30, [sp, #-0x30]!
        stp     x0, x1, [sp, #0x10]
        stp     x9, x10, [sp, #0x20]
        ldr     w1, .Lsl_lvl
        add     w1, w8, w1
        mov     w0, #0xCE0C
        movk    w0, #0x0099, lsl #16
        bl      .Lg2h
        ldr     w0, [x0]
        bl      .Lg2h
        ldr     w0, [x0]
        mov     w9, #0x68
        mul     w0, w0, w9
        mov     w9, #0xB0F1
        movk    w9, #0x009A, lsl #16
        add     w0, w0, w9
        bl      .Lg2h
        ldrb    w0, [x0]
        add     w8, w1, w0
        ldp     x9, x10, [sp, #0x20]
        ldp     x0, x1, [sp, #0x10]
        ldp     x29, x30, [sp], #0x30
        ret
        .p2align 2
        .global sl_lvl
sl_lvl:
.Lsl_lvl:
        .space  4

/* -------------------------------------------------------------- AP Share
 * ARM +0x7F572C `bl` to x86 0x5C7260 (the x86 call 0x5C6F28 replaces with
 * its jump; x20 = the x86 context, esp at the return slot): pt_ap_share
 * (ps3tweaks_c.c), then on into the original call.
 */
        .global ap_site
ap_site:
        stp     x29, x30, [sp, #-0x10]!
        bl      .Lsave
        mov     x0, x20
        adr     x1, .Lg2h
        adr     x2, .Lap_master
        bl      pt_ap_share
        bl      .Lrestore
        ldp     x29, x30, [sp], #0x10
        b       .Lap_orig
        .global ap_orig, ap_master
ap_orig:
.Lap_orig:
        b       .                       /* build: b x86 0x5C7260 */
ap_master:
.Lap_master:
        b       .                       /* build: b x86 0x6CC0EA */

/* ------------------------------------------------- Summon Animation Skip
 * x86 0x5DA117 `mov [edx], cl` (the stored byte starts the animation),
 * skipped by the cave when the command, byte [0x99E314], is 3 (Summon) or
 * 0x16. ARM +0x849F6C `strb w21, [x0]` (w21 = cl, x0 = host [edx]).
 */
        .global sa_site
sa_site:
        stp     x29, x30, [sp, #-0x40]!
        stp     x0, x1, [sp, #0x10]
        stp     x8, x9, [sp, #0x20]
        str     x10, [sp, #0x30]
        mov     x1, x0
        mov     w0, #0xE314
        movk    w0, #0x0099, lsl #16
        bl      .Lg2h
        ldrb    w0, [x0]
        cmp     w0, #3
        b.eq    1f
        cmp     w0, #0x16
        b.eq    1f
        strb    w21, [x1]
1:      ldr     x10, [sp, #0x30]
        ldp     x8, x9, [sp, #0x20]
        ldp     x0, x1, [sp, #0x10]
        ldp     x29, x30, [sp], #0x40
        ret

/* --------------------------------------------------------- Dialogue Mash
 * x86 0x64991F: when the OK input is not down this frame (eax = 0), and
 * every button of the option's mask is held (dword [0x9A85D4]), OK is set
 * to bit 0 of dword [0xDC3CC8] (a toggling frame counter), so it presses
 * on alternate frames. The stock code (OK down -> [0xD011C0] = 1) is kept.
 * ARM +0xA1BE6C `cbz w9, +0x18` over that store (w9 = eax; w8 = esp, the
 * same on both paths): w9 != 0 returns into the store; w9 = 0 does the
 * cave, then returns past it (x30 + 0x14).
 */
        .global dm_site
dm_site:
        cbz     w9, 1f
        ret
1:      stp     x29, x30, [sp, #-0x40]!
        stp     x0, x1, [sp, #0x10]
        stp     x8, x9, [sp, #0x20]
        stp     x10, x2, [sp, #0x30]
        mov     w0, #0x85D4
        movk    w0, #0x009A, lsl #16
        bl      .Lg2h
        ldr     w1, [x0]
        ldr     w2, .Ldm_mask
        and     w1, w1, w2
        cmp     w1, w2
        b.ne    2f
        mov     w0, #0x3CC8
        movk    w0, #0x00DC, lsl #16
        bl      .Lg2h
        ldr     w1, [x0]
        and     w1, w1, #1
        mov     w0, #0x11C0
        movk    w0, #0x00D0, lsl #16
        bl      .Lg2h
        str     w1, [x0]
2:      ldp     x10, x2, [sp, #0x30]
        ldp     x8, x9, [sp, #0x20]
        ldp     x0, x1, [sp, #0x10]
        ldp     x29, x30, [sp], #0x40
        add     x30, x30, #0x14
        ret
        .p2align 2
        .global dm_mask
dm_mask:
.Ldm_mask:
        .space  4

/* ------------------------------------------------------------ Fix Overflow
 * Base damage, x86 0x5DE667: eax = (u64)(ecx * eax) * [[0x99CE0C] + 0x48]
 * >> 13, unsigned, where the stock code multiplied in 32 bits (and so
 * overflowed). The mod folds the high half in with `shl edx, 0x17` (not
 * 0x13); kept as it is. ARM +0x858030 `mul w8, w8, w9` (w9 = ecx * eax,
 * w8 = the multiplier, x20 = context): w21 = the result, edx = the folded
 * high half, and on at +0x858048 (x30 + 0x14), where w21 is stored.
 */
        .global fo_dmg
fo_dmg:
        umull   x21, w9, w8
        lsr     x9, x21, #32
        lsl     w9, w9, #23
        str     w9, [x20, #8]
        lsr     w8, w21, #13
        add     w21, w8, w9
        add     x30, x30, #0x14
        ret

/* Random variance, x86 0x5DE99D: (u64)[ebp+8] * (rand + 0xF01) >> 12,
 * unsigned, + 1 unless it is 0, returned in eax ([ebp+8] is not written).
 * ARM +0x858D0C `mul w8, w8, w9` (w8 = [ebp+8], w9 = eax, x19 = context):
 * w8 = the result, on at +0x858D6C (x30 + 0x5C), where w8 becomes eax.
 */
        .global fo_rng
fo_rng:
        str     w8, [x19, #4]
        umull   x8, w9, w8
        lsr     x9, x8, #32
        lsl     w9, w9, #20
        str     w9, [x19, #8]
        lsr     w8, w8, #12
        add     w8, w8, w9
        cbz     w8, 1f
        add     w8, w8, #1
1:      add     x30, x30, #0x5C
        ret

/* --------------------------------------------------- Party Leader Override
 * x86 0x76736E returns which model leads on the world map: 0 Cloud, 1 Tifa,
 * 2 Cid. The mod fixes it (Cloud, Tifa, Cid) or takes it from the first
 * party slot (byte [0xDC0230]: 2 Tifa, 8 Cid, else Cloud), and always
 * stores it in bits 3-4 of byte [0xDC0C96] (the "ocean teleporting" fix),
 * with ecx = the result << 3. ARM +0xF2FEAC, the first `bl` translator
 * after the prologue (x20 = context): w19 = the result, on at +0xF2FFC4
 * (x30 + 0x114), which stores [ebp-0xc] = w19 and returns it in eax.
 */
        .global pl_site
pl_site:
        stp     x29, x30, [sp, #-0x20]!
        stp     x1, x2, [sp, #0x10]
        ldr     w1, .Lpl_mode
        cmp     w1, #3
        b.ne    2f
        mov     w0, #0x0230
        movk    w0, #0x00DC, lsl #16
        bl      .Lg2h
        ldrb    w2, [x0]
        mov     w1, #1
        cmp     w2, #2
        b.eq    2f
        mov     w1, #2
        cmp     w2, #8
        b.eq    2f
        mov     w1, #0
2:      mov     w0, #0x0C96
        movk    w0, #0x00DC, lsl #16
        bl      .Lg2h
        ldrb    w2, [x0]
        and     w2, w2, #0xFFFFFFE7
        orr     w2, w2, w1, lsl #3
        strb    w2, [x0]
        lsl     w2, w1, #3
        str     w2, [x20, #4]
        mov     w19, w1
        ldp     x1, x2, [sp, #0x10]
        ldp     x29, x30, [sp], #0x20
        add     x30, x30, #0x114
        ret
        .p2align 2
        .global pl_mode
pl_mode:
.Lpl_mode:
        .space  4

/* -------------------------------------------------------- No Battle Music
 * x86 0x4093E3, the field battle start's music call (0x740D80): its sound
 * argument, dword [0xCC0DCC], is pushed as -1 when its low byte is 7, the
 * battle theme (eax = the pushed value, ecx = 0, as the mod leaves them).
 * ARM +0x3ACC0 `str w19, [x0]` (w19 = the value, x23 = context).
 */
        .global nb_push
nb_push:
        stp     x9, x10, [sp, #-0x10]!
        mov     w9, w19
        and     w10, w19, #0xFF
        cmp     w10, #7
        b.ne    1f
        mov     w9, #-1
1:      str     w9, [x0]
        str     w9, [x23]
        str     wzr, [x23, #4]
        ldp     x9, x10, [sp], #0x10
        ret

/* x86 0x40827B, after a battle: the battle flags word [0xDB958A] also gets
 * 0x20 when [0xCC0D84] != 3 and [0xCC0DCC] == 7. ARM +0x35F58
 * `strh w21, [x0]` (w21 = the flags, x22 = context, ax at +0).
 */
        .global nb_flag
nb_flag:
        stp     x29, x30, [sp, #-0x30]!
        stp     x0, x1, [sp, #0x10]
        stp     x8, x9, [sp, #0x20]
        str     x10, [sp, #-0x10]!
        mov     x1, x0
        mov     w0, #0x0D84
        movk    w0, #0x00CC, lsl #16
        bl      .Lg2h
        ldr     w9, [x0]
        cmp     w9, #3
        b.eq    1f
        mov     w0, #0x0DCC
        movk    w0, #0x00CC, lsl #16
        bl      .Lg2h
        ldr     w9, [x0]
        cmp     w9, #7
        b.ne    1f
        orr     w21, w21, #0x20
1:      strh    w21, [x1]
        strh    w21, [x22]
        ldr     x10, [sp], #0x10
        ldp     x8, x9, [sp, #0x20]
        ldp     x0, x1, [sp, #0x10]
        ldp     x29, x30, [sp], #0x30
        ret

/* x86 0x74E1A7 (NoWorldNoField only), after the world map's music setup:
 * call 0x75E5C0(1) (eax = 1, the popped argument). ARM +0xF2ADE8, the
 * `bl` translator that follows (w0 = ebp + 8, x25 = context): the call,
 * then on into the translator as before.
 */
        .global nb_world
nb_world:
        stp     x29, x30, [sp, #-0x10]!
        bl      .Lsave
        ldr     w1, [x25, #0x10]
        sub     w0, w1, #4
        bl      .Lg2h
        mov     w8, #1
        str     w8, [x0]
        ldr     w1, [x25, #0x10]
        sub     w1, w1, #8
        str     w1, [x25, #0x10]
        bl      .Lnb_world_fn
        ldr     w1, [x25, #0x10]
        add     w1, w1, #4
        str     w1, [x25, #0x10]
        mov     w8, #1
        str     w8, [x25]
        bl      .Lrestore
        ldp     x29, x30, [sp], #0x10
        b       .Lg2h
        .global nb_world_fn
nb_world_fn:
.Lnb_world_fn:
        b       .                       /* build: b x86 0x75E5C0 */

/* -------------------------------------------------------- True Wait Mode
 * x86 0x4320DC `jne 0x432391` (eax = 0x4324B6's result): also taken when
 * pt_wait_check (ps3tweaks_c.c) finds an actor waiting for input, which is
 * what stops the battle clock. ARM +0xE79F8 `cbnz w8, +0xE84F4` (w8 = eax,
 * x26 = context): on at +0xE84F4 (x30 + 0xAF8) or +0xE79FC (x30).
 */
        .global wm_site
wm_site:
        cbnz    w8, 2f
        stp     x29, x30, [sp, #-0x10]!
        bl      .Lsave
        mov     x0, x26
        adr     x1, .Lg2h
        ldr     w2, .Lwm_easy
        bl      pt_wait_check
        str     w0, [x26]
        mov     w29, w0
        bl      .Lrestore
        cmp     w29, #1
        ldp     x29, x30, [sp], #0x10
        b.eq    3f
2:      add     x30, x30, #0xAF8
3:      ret
        .p2align 2
        .global wm_easy
wm_easy:
.Lwm_easy:
        .space  4

/* ----------------------------------------------------------- Turbo Turns
 * ARM +0xD140A4 `bl` x86 0x4320AC (x23 = context, its return slot pushed):
 * pt_turbo (ps3tweaks_c.c) instead, which pops that slot itself.
 */
        .global tt_site
tt_site:
        stp     x29, x30, [sp, #-0x10]!
        bl      .Lsave
        mov     x0, x23
        adr     x1, .Lg2h
        adr     x2, .Ltt_fn0
        bl      pt_turbo
        bl      .Lrestore
        ldp     x29, x30, [sp], #0x10
        ret
        .global tt_fn0, tt_fn1, tt_fn2, tt_fn3, tt_fn4
tt_fn0:
.Ltt_fn0:
        b       .                       /* build: b x86 0x43526A */
tt_fn1: b       .                       /* build: b x86 0x4351BD */
tt_fn2: b       .                       /* build: b x86 0x434347 */
tt_fn3: b       .                       /* build: b x86 0x4320AC */
tt_fn4: b       .                       /* build: b x86 0x4324B6 */

/* --------------------------------------------------------- Show Weakness
 * ARM +0xD20C48, the `bl` translator for x86 0x6D0D57's store to [ebp-0x5c]
 * (x23 = context): pt_show_weakness (ps3tweaks_c.c), then on into the
 * translator as before. The mod's cave did its work after that store and
 * the `fild`; neither is read by it.
 */
        .global sw_site
sw_site:
        stp     x29, x30, [sp, #-0x10]!
        bl      .Lsave
        mov     x0, x23
        adr     x1, .Lg2h
        adr     x2, .Lsw_fn0
        adr     x3, .Lsw_text
        ldr     w4, .Lsw_sensed
        bl      pt_show_weakness
        bl      .Lrestore
        ldp     x29, x30, [sp], #0x10
        b       .Lg2h
        .global sw_fn0, sw_fn1, sw_sensed
sw_fn0:
.Lsw_fn0:
        b       .                       /* build: b x86 0x433580 */
sw_fn1: b       .                       /* build: b x86 0x6F5B03 */
sw_sensed:
.Lsw_sensed:
        .space  4
/* the mod's text block, x86 0x7B56D6: "Status Immune", "Resists",
 * "Absorbs", "Immune", "Weak", "" in FF7's encoding */
.Lsw_text:
        .byte   0x33, 0x54, 0x41, 0x54, 0x55, 0x53, 0x00
        .byte   0x29, 0x4D, 0x4D, 0x55, 0x4E, 0x45, 0xFF
        .byte   0x32, 0x45, 0x53, 0x49, 0x53, 0x54, 0x53, 0xFF
        .byte   0x21, 0x42, 0x53, 0x4F, 0x52, 0x42, 0x53, 0xFF
        .byte   0x29, 0x4D, 0x4D, 0x55, 0x4E, 0x45, 0xFF
        .byte   0x37, 0x45, 0x41, 0x4B, 0xFF
        .p2align 2

/* --------------------------------------------- Morph As Well (Enablers)
 * The mod moves materia type 0x60 onto type 0x5D's case in two jump
 * tables. The ARM dispatch reads each table from guest memory, so the
 * case is changed where the entry is loaded (x20 = context, eax = case).
 *
 * x86 0x5CBAD4 `jmp [eax*4+0x5CBC33]`, ARM +0x819008 `ldr w0, [x0]`:
 * case 15 -> 0x5CBB52.
 */
        .global mw_case
mw_case:
        ldr     w0, [x0]
        ldr     w9, [x20]
        cmp     w9, #15
        b.ne    1f
        mov     w0, #0xBB52
        movk    w0, #0x005C, lsl #16
1:      ret

/* x86 0x5CBB52 `mov byte [0xC068CC], 8`, ARM +0x8195E0 `mov w8, #8` (only
 * that case reaches it): the mod's cave stores 8 for case 12, else 4. */
        .global mw_byte
mw_byte:
        ldr     w8, [x20]
        cmp     w8, #12
        mov     w8, #4
        mov     w9, #8
        csel    w8, w9, w8, eq
        ret

/* x86 0x5CD964 `jmp [eax*4+0x5CD9D0]`, ARM +0x822964 `ldr w0, [x0]`:
 * case 12 -> 0x5CD9A1. (AP Plus has case 13, at the next word.) */
        .global mw_vis
mw_vis:
        ldr     w0, [x0]
        ldr     w9, [x20]
        cmp     w9, #12
        b.ne    1f
        mov     w0, #0xD9A1
        movk    w0, #0x005C, lsl #16
1:      ret

/* x86 0x5DF1E3 `test ecx, ecx / je`, ARM +0x84DFD0 `tbz w8, #13, ...`
 * (x20 = context, w8 = the flags ecx was masked from): the morph-pair
 * call is also made when bit 2 of [0x99E3F8] is set, with ecx that bit,
 * as the mod's cave does. Not made: on to ARM +0x84DCAC. */
        .global mw_pair
mw_pair:
        tbnz    w8, #13, 2f
        stp     x29, x30, [sp, #-0x10]!
        mov     w0, #0xE3F8
        movk    w0, #0x0099, lsl #16
        bl      .Lg2h
        ldp     x29, x30, [sp], #0x10
        ldr     w8, [x0]
        and     w8, w8, #4
        str     w8, [x20, #4]
        cmp     w8, #0
        cset    w9, eq
        strb    w9, [x20, #0x23]
        cbnz    w8, 2f
        sub     x30, x30, #0x328
2:      ret

/* ---------------------------------------------------- AP Plus (Enablers)
 * x86 0x5CAE0C, ARM +0x810DC0 (x20 = context), the weapon slot's AP:
 * pt_ap_plus (ps3tweaks_c.c); a skipped slot goes on to ARM +0x810C94
 * (x86 0x5CAF2E), a counted one to +0x810F54 (0x5CAE9C).
 */
        .global ap_weapon
ap_weapon:
        stp     x29, x30, [sp, #-0x10]!
        bl      .Lsave
        mov     x0, x20
        adr     x1, .Lg2h
        mov     w2, #0
        bl      pt_ap_plus
        mov     w29, w0
        bl      .Lrestore
        cmp     w29, #0
        ldp     x29, x30, [sp], #0x10
        b.ne    1f
        sub     x30, x30, #0x130
        ret
1:      add     x30, x30, #0x190
        ret

/* x86 0x5CAFF6, ARM +0x811468 (x20 = context), the armour slot's AP, then
 * on to ARM +0x811598 (x86 0x5CB04C). */
        .global ap_armour
ap_armour:
        stp     x29, x30, [sp, #-0x10]!
        bl      .Lsave
        mov     x0, x20
        adr     x1, .Lg2h
        mov     w2, #1
        bl      pt_ap_plus
        bl      .Lrestore
        ldp     x29, x30, [sp], #0x10
        add     x30, x30, #0x12C
        ret

/* x86 0x5CD964's table, ARM +0x822968 `mov w8, #0x2695`: case 13 ->
 * 0x5CD9A1. */
        .global ap_vis
ap_vis:
        ldr     w9, [x20]
        cmp     w9, #13
        b.ne    1f
        mov     w0, #0xD9A1
        movk    w0, #0x005C, lsl #16
1:      mov     w8, #0x2695
        ret

/* -------------------------------------------- Materia Drops (Enablers)
 * Item ids from 0x140 up are materia (id - 0x140) wherever an item is
 * stolen, morphed, dropped, or its name shown.
 *
 * Steal, x86 0x5CA975 / morph, 0x5CAD0C: `and reg, 0xFFFF`, ARM +0x80FBA4 /
 * +0x810938 `ldrh w22, [x0]` (x20 = context; w8 = esp, kept). A materia
 * goes into the inventory (x86 0x6CC0EA) in place of the item add, which
 * is stepped over: on to the instruction after its call (+0x80FC28 /
 * +0x8109BC) with esp as that call leaves it, edx (steal) or eax (morph)
 * 0x56, as the mod's cave leaves them.
 */
        .global md_steal, md_morph
md_steal:
        mov     w9, #2
        b       1f
md_morph:
        mov     w9, #0
1:      ldrh    w22, [x0]
        cmp     w22, #0x140
        b.lo    2f
        stp     x29, x30, [sp, #-0x10]!
        bl      .Lsave
        mov     w29, w9
        mov     x0, x20
        adr     x1, .Lg2h
        adr     x2, .Lap_master
        sub     w3, w22, #0x140
        bl      pt_md_take
        mov     w8, #0x56
        str     w8, [x20, w29, uxtw #2]
        ldr     w8, [x20, #0x10]
        sub     w8, w8, #0x10
        str     w8, [x20, #0x10]
        bl      .Lrestore
        ldp     x29, x30, [sp], #0x10
        add     x30, x30, #0x80
2:      ret

/* Drop, x86 0x5C8551 (the drop count's load), ARM +0x7FCE08 `ldr w8,
 * [x19, #0x14]` (x19 = context, w27 = the count, edx = the item id):
 * pt_md_drop (ps3tweaks_c.c) for materia, then on to the next drop at
 * ARM +0x7FCCC0 (x86 0x5C867B). */
        .global md_drop
md_drop:
        ldr     w9, [x19, #8]
        cmp     w9, #0x140
        b.ge    1f
        ldr     w8, [x19, #0x14]
        ret
1:      stp     x29, x30, [sp, #-0x10]!
        bl      .Lsave
        mov     x0, x19
        adr     x1, .Lg2h
        adr     x2, .Lap_master
        mov     w3, w27
        bl      pt_md_drop
        bl      .Lrestore
        ldp     x29, x30, [sp], #0x10
        sub     x30, x30, #0x14C
        ret

/* Name, x86 0x419B73, ARM +0x86CE0 `bl` x86 0x41963C (x21 = context;
 * arguments 4, id, 8): a materia's name comes from x86 0x6C6B06 (id -
 * 0x140, 7, 0x3DCED917) instead. */
        .global md_name
md_name:
        stp     x29, x30, [sp, #-0x10]!
        ldr     w0, [x21, #0x10]
        add     w0, w0, #4
        bl      .Lg2h
        ldr     w9, [x0, #4]
        cmp     w9, #0x140
        b.lt    1f
        sub     w9, w9, #0x140
        str     w9, [x0]
        str     w9, [x21]
        mov     w9, #7
        str     w9, [x0, #4]
        mov     w9, #0xD917
        movk    w9, #0x3DCE, lsl #16
        str     w9, [x0, #8]
        ldp     x29, x30, [sp], #0x10
        b       .Lmd_fn1
1:      ldp     x29, x30, [sp], #0x10
        b       .Lmd_fn0

/* Drop window, x86 0x6C716D / 0x6C71C2, ARM +0xCEF1D0 / +0xCEF32C `bl`
 * x86 0x41963C (x23 = context; arguments 4, id, 8): a materia's name is
 * kind 0xD, id - 0x140. ecx is the id (and at the first, eax the row
 * offset ecx held), as the mod's code leaves them. */
        .global md_vis1, md_vis2
md_vis1:
        ldr     w9, [x23, #4]
        str     w9, [x23]
md_vis2:
        stp     x29, x30, [sp, #-0x10]!
        ldr     w0, [x23, #0x10]
        add     w0, w0, #4
        bl      .Lg2h
        ldp     x29, x30, [sp], #0x10
        ldr     w9, [x0, #4]
        cmp     w9, #0x140
        b.lt    1f
        sub     w8, w9, #0x140
        bfxil   w9, w8, #0, #16
        str     w9, [x0, #4]
        mov     w8, #0xD
        str     w8, [x0]
1:      str     w9, [x23, #4]
        b       .Lmd_fn0
        .global md_fn0, md_fn1
md_fn0:
.Lmd_fn0:
        b       .                       /* build: b x86 0x41963C */
md_fn1:
.Lmd_fn1:
        b       .                       /* build: b x86 0x6C6B06 */

/* --------------------------------------------------------- Free Party Fix
 * The out-of-battle party is updated (x86 0x767218) only when
 * pt_fp_legal (ps3tweaks_c.c) says the battle party may be it.
 *
 * After a battle, x86 0x766DA6, ARM +0xF2C2E8 (x21 = context), and after
 * a party change, x86 0x767438, ARM +0xF46F24 (x19 = context): the `bl`
 * itself. Not made: its return slot popped, eax 0, as the mod's code.
 */
        .global fp_call_a, fp_call_b
fp_call_a:
        stp     x29, x30, [sp, #-0x10]!
        mov     x29, x21
        b       1f
fp_call_b:
        stp     x29, x30, [sp, #-0x10]!
        mov     x29, x19
1:      bl      .Lsave
        adr     x0, .Lg2h
        bl      pt_fp_legal
        str     x0, [sp]                /* restored into x0 */
        bl      .Lrestore
        cbz     w0, 2f
        ldp     x29, x30, [sp], #0x10
        b       .Lfp_fn
2:      ldr     w9, [x29, #0x10]
        add     w9, w9, #4
        str     w9, [x29, #0x10]
        str     wzr, [x29]
        ldp     x29, x30, [sp], #0x10
        ret

/* From the field menu, x86 0x61C577 (the routine that does it) after its
 * `push ebp`, ARM +0x982E0C `ldr w8, [x19, #0x10]` (x19 = context): not
 * allowed, the routine returns at once (ARM +0x982F08, x86 0x61C5B6's
 * `mov esp, ebp`) with eax 0. */
        .global fp_menu
fp_menu:
        stp     x29, x30, [sp, #-0x10]!
        bl      .Lsave
        adr     x0, .Lg2h
        bl      pt_fp_legal
        str     x0, [sp]
        bl      .Lrestore
        ldp     x29, x30, [sp], #0x10
        ldr     w8, [x19, #0x10]
        cbz     w0, 1f
        ret
1:      str     w8, [x19, #0x14]
        str     wzr, [x19]
        add     x30, x30, #0xF8
        ret
        .global fp_fn
fp_fn:
.Lfp_fn:
        b       .                       /* build: b x86 0x767218 */

/* the option's table, written by the build: (s16 moment, u8 cap) entries,
 * the last with moment 0x7FFF. 64 entries. */
        .global lc_table
lc_table:
.Llc_table:
        .space  192
        .p2align 2
