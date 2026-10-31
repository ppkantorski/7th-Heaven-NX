/*
 * ps3tweaks_head.S -- BUILD 868. The start of each PS3Tweaks blob
 * (ps3tweaks.S, ps3tweaks2.S): ARM64 versions of the code caves in
 * Postscriptthree's Gameplay Tweaks (PS3Tweaks.iro), for the Switch port's
 * recompiled exefs/main. See src/core/ff7nx_ps3tweaks.py, which places each
 * blob in a dead function body of its own, writes the per-option data
 * tables and points each hooked word at its site_* entry with a `bl`.
 *
 * Rules every entry keeps:
 *   - it is reached by a `bl` that replaced ONE word in a translated
 *     function, after that function already made calls (so x30 is free);
 *   - it changes only the register(s) the replaced word wrote, and the
 *     NZCV flags, which no word after any hooked site reads;
 *   - guest memory is reached through pt_g2h, which the build points at
 *     the port's guest-to-host translator (clobbers x0, x8, x9, x10).
 *
 * Licensed under GPLv3
 * Copyright (c) 2026 ppkantorski
 */
        .section .text.pt, "ax"
        .p2align 4
        .global pt_g2h
pt_g2h:
.Lg2h:
        b       .                       /* build: b guest_to_host */

/* .Lsave / .Lrestore: push / pop x0-x17 and the caller-saved vector
 * registers q0-q7, q16-q31 (0x220 bytes, x30 untouched).
 * The recompiled caller may keep values in caller-saved registers across
 * a `bl` it knows does not touch them, so a cave that calls C or a game
 * function from such a site keeps all of them. */
.Lsave:
        sub     sp, sp, #0x220
        stp     q0, q1, [sp, #0xA0]
        stp     q2, q3, [sp, #0xC0]
        stp     q4, q5, [sp, #0xE0]
        stp     q6, q7, [sp, #0x100]
        stp     q16, q17, [sp, #0x120]
        stp     q18, q19, [sp, #0x140]
        stp     q20, q21, [sp, #0x160]
        stp     q22, q23, [sp, #0x180]
        stp     q24, q25, [sp, #0x1A0]
        stp     q26, q27, [sp, #0x1C0]
        stp     q28, q29, [sp, #0x1E0]
        stp     q30, q31, [sp, #0x200]
        stp     x0, x1, [sp]
        stp     x2, x3, [sp, #0x10]
        stp     x4, x5, [sp, #0x20]
        stp     x6, x7, [sp, #0x30]
        stp     x8, x9, [sp, #0x40]
        stp     x10, x11, [sp, #0x50]
        stp     x12, x13, [sp, #0x60]
        stp     x14, x15, [sp, #0x70]
        stp     x16, x17, [sp, #0x80]
        ret
.Lrestore:
        ldp     x0, x1, [sp]
        ldp     x2, x3, [sp, #0x10]
        ldp     x4, x5, [sp, #0x20]
        ldp     x6, x7, [sp, #0x30]
        ldp     x8, x9, [sp, #0x40]
        ldp     x10, x11, [sp, #0x50]
        ldp     x12, x13, [sp, #0x60]
        ldp     x14, x15, [sp, #0x70]
        ldp     x16, x17, [sp, #0x80]
        ldp     q0, q1, [sp, #0xA0]
        ldp     q2, q3, [sp, #0xC0]
        ldp     q4, q5, [sp, #0xE0]
        ldp     q6, q7, [sp, #0x100]
        ldp     q16, q17, [sp, #0x120]
        ldp     q18, q19, [sp, #0x140]
        ldp     q20, q21, [sp, #0x160]
        ldp     q22, q23, [sp, #0x180]
        ldp     q24, q25, [sp, #0x1A0]
        ldp     q26, q27, [sp, #0x1C0]
        ldp     q28, q29, [sp, #0x1E0]
        ldp     q30, q31, [sp, #0x200]
        add     sp, sp, #0x220
        ret

