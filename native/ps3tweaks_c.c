/*
 * ps3tweaks_c.c -- BUILD 868. The PS3Tweaks caves too long to write in
 * assembly. Compiled to assembly and appended to ps3tweaks.S by
 * build_ps3tweaks.sh, so everything is one section with no relocations:
 * every function here is static and reached from an entry in ps3tweaks.S,
 * which passes in what it needs (the x86 context, the guest-to-host
 * translator, trampolines to translated game functions).
 *
 * Licensed under GPLv3
 * Copyright (c) 2026 ppkantorski
 */
#include "ps3tweaks.h"

/* ------------------------------------------------------------- AP Share
 * x86 0x9A7200, jumped to in place of 0x5C6F28 `call 0x5C7260` (half EXP
 * for a character outside the party; esp -> its arguments, the first the
 * character record; [ebp-4] the character index). Gives that character's
 * equipped materia the battle's full AP, grown by the weapon (slots 0-7)
 * and armour (8-15) growth rate, mastering it (AP 0xFFFFFF, and a new copy
 * of the materia into the inventory, x86 0x6CC0EA) when it reaches its
 * last level. The original call then runs as before.
 */
PT static void pt_ap_share(u32 *ctx, g2h_t g2h, fn_t add_materia)
{
    u32 rec = U32(ctx[ESP] + 4);
    u32 idx = U32(ctx[EBP] - 4);
    u32 equip = idx * 0x84 + 0xDBFDA9;
    u32 weapon = U8(equip - 1), armour = U8(equip);
    u32 ap = U32(0x99E2C4) << 8;
    u32 by_armour = (U8(0xDBCCF1 + armour * 0x24) & 3) * ap;
    u32 add = (U8(0xDBE736 + weapon * 0x2C) & 3) * ap;
    u32 end = idx * 0x84 + 0xDBFE0C;
    u32 p = rec + 0x40;
    int n;

    for (n = 0; n < 16; n++) {
        u32 m = U32(p);
        if ((m & 0xFFFFFF00u) != 0xFFFFFF00u) {
            u32 v = m + add;
            u32 tbl = 0xDBDF60 + (v & 0xFF) * 0x14;
            u32 last = 0;
            int k;
            for (k = 0; k < 4; k++) {
                u32 w = U16(tbl + 2 * k);
                if (w == 0xFFFF)
                    break;
                last = w;
            }
            if (last) {
                if ((s32)v < (s32)(last * 0x6400)) {
                    U32(p) = v;
                } else {
                    U32(p) = v | 0xFFFFFF00u;
                    call1(ctx, g2h, add_materia, v & 0xFF);
                }
            }
        }
        p += 4;
        if (end - p == 0x20)
            add = by_armour;
        if (end == p)
            break;
    }
}

/* -------------------------------------------------------- True Wait Mode
 * x86 0x7415FC: 0 when an active battle actor (9 of them, 0x44 apart from
 * 0x9A8B12, a nonzero word) has a turn waiting for input (word at +2 is
 * 0xFFFF), else 1. With the run buttons (byte [0x9A85D4] & 0xC) held, the
 * first two actor slots are not looked at, and in the Easy Run version a
 * battle whose flags (dword [0x9A88A6]) lack bit 3 stops the scan there
 * with 1. Leaves ecx at the actor it stopped on, as the x86 does.
 */
PT static u32 pt_wait_check(u32 *ctx, g2h_t g2h, u32 easy)
{
    u32 ecx = 0x9A8B14;

    do {
        if (U16(ecx - 2) != 0) {
            int look = 1;
            if ((U8(0x9A85D4) & 0xC) == 0xC) {
                if (ecx < 0x9A8B9D) {
                    look = 0;
                } else if (easy && (U32(0x9A88A6) & 8) == 0) {
                    ctx[ECX] = ecx;
                    return 1;
                }
            }
            if (look && U16(ecx) == 0xFFFF) {
                ctx[ECX] = ecx;
                return 0;
            }
        }
        ecx += 0x44;
    } while (ecx != 0x9A8D78);
    ctx[ECX] = ecx;
    return 1;
}

/* ----------------------------------------------------------- Turbo Turns
 * x86 0x7B59B5, called in place of 0x6CE9F0 `call 0x4320AC` (one battle
 * update): runs that update, and while 0x4324B6 returns 0, the run buttons
 * are not both held and no actor waits for input (the Easy Run check),
 * runs the next frame's battle work (0x43526A, 0x4351BD(2), 0x434347) and
 * the update again: at most 256 updates a frame. ecx = the count, as the
 * x86 leaves it. `fns`: five consecutive one-word trampolines, to
 * 0x43526A, 0x4351BD, 0x434347, 0x4320AC and 0x4324B6.
 */
#define FN(i) ((fn_t)(fns + 4 * (i)))
PT static void pt_turbo(u32 *ctx, g2h_t g2h, char *fns)
{
    u32 count = 0;
    u32 eax;

    for (;;) {
        call0(ctx, FN(3));
        eax = call0(ctx, FN(4));
        if (eax)
            break;
        count++;
        if (count & 0xFF00)
            break;
        eax = (eax & ~0xFFu) | (U8(0x9A85D4) & 0xC);
        if ((eax & 0xFF) == 0xC)
            break;
        eax = pt_wait_check(ctx, g2h, 1);
        if (!eax)
            break;
        call0(ctx, FN(0));
        call1(ctx, g2h, FN(1), 2);
        call0(ctx, FN(2));
    }
    ctx[0] = eax;
    ctx[ECX] = count;
    ctx[ESP] += 4;              /* this call's own return slot */
}

/* --------------------------------------------------------- Show Weakness
 * x86 0x7B5700, called from 0x6D0D57 in the battle target cursor's draw
 * (x = (s16)[ebp+0xc], y = (s16)[ebp+0x10]): finds what the cursor is on
 * the way the menu does (from the command at [0xDC3C70]: a party member or
 * enemy, an item, a summon, an enemy skill...), asks 0x433580 for its
 * elemental and status flags (16 dwords, on the stack), and draws "Status
 * Immune" under the cursor when the target ignores statuses, and "Weak",
 * "Absorbs", "Immune" or "Resists" to its right, with the game's text draw
 * 0x6F5B03(x, y, text, 7). Leaves ebx = the flag mask, as the x86 does.
 * With `sensed`, only for a target Sense was used on (byte [0x9A8B39 +
 * 0x44 * [0xDC3C9C]] bit 6).
 *
 * `text` is the mod's own FF7-encoded text block (0x2A bytes, laid out as
 * the mod's: "Status " runs on into "Immune"). It is copied onto the guest
 * stack to be drawn: the x86 .text the mod kept it in is not guest data
 * here. fns: the trampolines to 0x433580 and 0x6F5B03.
 */
#define SW_TEXT     0x2A
#define SW_STATUS   0x00                /* "Status Immune" */
#define SW_RESISTS  0x0E
#define SW_ABSORBS  0x16
#define SW_IMMUNE   0x1E
#define SW_WEAK     0x25
#define SW_NONE     0x29                /* "" */

PT static void pt_show_weakness(u32 *ctx, g2h_t g2h, char *fns,
                                const u8 *text, u32 sensed)
{
    u32 sp0 = ctx[ESP], ebp = ctx[EBP];
    u32 x = (u32)(s32)(short)U16(ebp + 0xC);
    u32 y = (u32)(s32)(short)U16(ebp + 0x10);
    u32 cmd, eax = 0, ecx = 0, edx = 0, ebx = 0, at, buf, i, sp;
    int calc = 1;

    if (sensed && !(U32(0x9A8B39 + U32(0xDC3C9C) * 0x44) & 0x40))
        return;
    cmd = U32(0xDC3C70);
    if (cmd == 1 || cmd == 9 || cmd == 0xA || cmd == 0x11 || cmd == 0x18 ||
        cmd == 0x19 || cmd == 0x1B) {
        u32 e = 0x9A8E62 + U32(0xDC3C7C) * 0x18;
        ecx = U32(e + 6);
        edx = U16(e);
        calc = 0;
    } else if (cmd == 2 || cmd == 0x15) {
        eax = ((U32(0xDC3C78)) * 0x1C) + 0xDB96A8;
    } else if (cmd == 3 || cmd == 0x16) {
        eax = ((0x38 + U32(0xDC3C78)) * 0x1C) + 0xDB96A8;
    } else if (cmd == 0x1E) {
        eax = U32(0xDBA544 + U8(0xDC3C7C) * 0x440) * 0x1C + 0x91F6EC;
    } else if (cmd == 0x14) {
        eax = U32(0xDC3C78) * 0x1C + 0x91F6EC;
    } else if (cmd == 0xD) {
        eax = ((0x48 + U32(0xDC3C78)) * 0x1C) + 0xDB96A8;
    } else {
        ecx = 0;
        edx = 0;
        calc = 0;
    }
    if (calc) {
        ecx = U32(eax - 4);
        edx = U16(eax);
    }

    /* 0x433580(edx, ecx, [0xDC3C9C], buf, 1, 0) */
    buf = sp0 - 0x40;
    sp = buf;
    U32(sp - 4) = 0;
    U32(sp - 8) = 1;
    U32(sp - 12) = buf;
    U32(sp - 16) = U32(0xDC3C9C);
    U32(sp - 20) = ecx;
    U32(sp - 24) = edx;
    ctx[ESP] = sp - 28;
    ((fn_t)fns)();
    for (i = 0; i < 8; i++)
        if (U32(buf + 4 * i))
            ebx |= 1u << i;
    eax = U32(buf + 0x34);

    /* the texts: on the stack below where the x86 kept its buffer */
    sp = (sp0 - 0x40) & ~3u;
    for (i = 0; i < SW_TEXT; i++)
        U8(sp + i) = text[i];
    if (eax) {
        U32(sp - 4) = 7;
        U32(sp - 8) = sp + SW_STATUS;
        U32(sp - 12) = y + 0x10;
        U32(sp - 16) = x;
        ctx[ESP] = sp - 20;
        ((fn_t)(fns + 4))();
    }
    if (ebx & 1)
        at = SW_WEAK;
    else if (ebx & 0x80)
        at = SW_ABSORBS;
    else if (ebx & 0x20)
        at = SW_IMMUNE;
    else if (ebx & 0x40)
        at = SW_ABSORBS;
    else if ((ebx & 0x14) == 0 || (ebx & 0x14) == 0x14)
        at = SW_NONE;
    else if ((ebx & 0x14) == 4)
        at = SW_WEAK;
    else
        at = SW_RESISTS;
    U32(sp - 4) = 7;
    U32(sp - 8) = sp + at;
    U32(sp - 12) = y;
    U32(sp - 16) = x + 0x30;
    ctx[ESP] = sp - 20;
    ((fn_t)(fns + 4))();
    ctx[ESP] = sp0;
    ctx[3] = ebx;
}

/* -------------------------------------------------------------- AP Plus
 * Enablers/APPlus, x86 0x5CAE0C (weapon slots) and 0x5CAFF6 (armour):
 * the slot's materia gains growth x AP, once more for each AP Plus
 * materia (data 0x6125) in a slot linked to it (slot type 7). For the
 * weapon the mod's cave also makes the skips the stock code made (empty
 * slot, materia 0x2C, mastered); 0 means skip. Leaves eax and ecx as the
 * cave does.
 */
PT static u32 pt_ap_plus(u32 *ctx, g2h_t g2h, u32 armour)
{
    u32 ebp = ctx[EBP], slots, growth, mat, n = 1, m, i;

    if (armour) {
        slots = U32(ebp - 0x24) + 9;
        growth = U8(slots + 8);
    } else {
        if (U8(ebp - 0x14) == 0xFF || U8(ebp - 0x14) == 0x2C
                || U32(ebp - 8) == 0xFFFFFF)
            return 0;
        slots = U32(0xC068B0) + 0x40E;
        growth = U8(slots);
        slots += 0x16;
    }
    m = growth * U16(ebp + 0xC);
    i = slots + U32(ebp - 0x1C);
    mat = U32(ebp - 0x10);
    for (int k = 0; k < 2; k++) {
        if (U8(i + k) == 7) {
            u32 id = U8(k ? mat + 4 : mat - 4);
            if (id != 0xFF && U16(id * 0x14 + 0xDBDF6D) == 0x6125)
                n++;
        }
    }
    U32(ebp - 8) += n * m;
    ctx[0] = n * m;
    ctx[ECX] = m;
    return 1;
}

/* --------------------------------------------------------- Materia Drops
 * Enablers/MateriaDrops, x86 0x916350 / 0x9163A8: one materia into the
 * inventory for a steal or morph (ps3tweaks.S md_steal).
 */
PT static void pt_md_take(u32 *ctx, g2h_t g2h, fn_t add_materia, u32 id)
{
    call1(ctx, g2h, add_materia, id);
}

/*
 * Enablers/MateriaDrops, x86 0x9163EE (from 0x5C8551): a battle drop of
 * item id 0x140 or more is `count` copies of materia id - 0x140 (x86
 * 0x6CC0EA each), then on to the next drop with eax the cave's popped
 * return address, as the mod leaves it.
 */
PT static void pt_md_drop(u32 *ctx, g2h_t g2h, fn_t add_materia, u32 count)
{
    u32 edx = ctx[2];

    edx = (edx & 0xFFFF0000u) | ((edx - 0x140) & 0xFFFF);
    do
        call1(ctx, g2h, add_materia, edx);
    while (--count);
    ctx[0] = 0x5C8556;
    ctx[ECX] = 0;
    ctx[2] = edx;
}

/* -------------------------------------------------------- Free Party Fix
 * Misc/FreePartyFix, x86 0x9A7900: may the out-of-battle party be updated
 * from the battle party? Only from game moment 0x181 on, with three
 * available members (bits of 0xDC0DDE) that between them are every
 * required and available member (0xDC0DDC & 0xDC0DDE & 0x1FF).
 */
PT static u32 pt_fp_legal(g2h_t g2h)
{
    u32 dx, i;

    if ((s32)(short)U16(0xDC08DC) < 0x181)
        return 0;
    dx = U16(0xDC0DDC) & U16(0xDC0DDE) & 0x1FF;
    for (i = 0; i < 3; i++) {
        u32 cl = U8(0xDC0230 + i), bit;
        if (cl == 0xFF)
            return 0;
        bit = (1u << (cl & 31)) & U16(0xDC0DDE);
        if (!bit)
            return 0;
        dx &= ~bit;
    }
    return (dx & 0xFFFF) == 0;
}
