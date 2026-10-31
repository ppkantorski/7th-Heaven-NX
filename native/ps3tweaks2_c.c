/*
 * ps3tweaks2_c.c -- BUILD 868. The C half of the second PS3Tweaks blob, as
 * ps3tweaks_c.c is of the first.
 *
 * Licensed under GPLv3
 * Copyright (c) 2026 ppkantorski
 */
#include "ps3tweaks.h"

/* ------------------------------------------------------------ More Pairs
 * MorePairs, "Call from regular all", x86 0x5CCD66 (edx = the materia id
 * x 0x14): command materia type 6 now pairs with All too. Its cave, x86
 * 0x5CCFEE, adds [ebp+0xC] to the count of the pair entry (x86 0x5CCE7B)
 * for the materia's first and, when it has one, second command. Returns
 * where the stock code goes on: 1 type 9's case, 2 type 0xA's, 0 the end.
 */
PT static u32 pt_mp_all(u32 *ctx, g2h_t g2h, fn_t pair, u32 edx)
{
    u32 ebp = ctx[EBP], esp = ctx[ESP], base, eax, ecx = ctx[ECX];

    ctx[2] = edx;
    eax = U32(edx + 0xDBDF6D) & 0xF;
    if (eax != 6) {
        ctx[0] = eax;
        return eax == 9 ? 1 : eax == 10 ? 2 : 0;
    }
    base = esp - 4;
    U32(base) = 0x5CCD78;                       /* call 0x5CCFEE */
    eax = U8(ebp - 0xC) * 0x14;
    U32(base - 4) = eax;                        /* push eax */
    edx = (edx & 0xFFFFFF00u) | U8(eax + 0xDBDF6E);
    U32(base - 8) = edx;                        /* push edx */
    ctx[0] = eax;
    ctx[2] = edx;
    ctx[ESP] = base - 8;
    eax = call0(ctx, pair) * 3;
    edx = U32(base - 4);                        /* pop edx, pop edx */
    ecx = U32(eax + 0xC06921) + U32(ebp + 0xC);
    U32(eax + 0xC06921) = ecx;
    eax = (eax & 0xFFFFFF00u) | U8(edx + 0xDBDF6F);
    if ((eax & 0xFF) != 0xFF) {
        U32(base - 4) = eax;                    /* push eax */
        ctx[0] = eax;
        ctx[ECX] = ecx;
        ctx[2] = edx;
        ctx[ESP] = base - 4;
        eax = call0(ctx, pair) * 3;
        edx = U32(base - 4);                    /* pop edx */
        ecx = U32(eax + 0xC06921) + U32(ebp + 0xC);
        U32(eax + 0xC06921) = ecx;
    }
    ctx[ESP] = esp;
    ctx[0] = 0;                                 /* xor eax, eax */
    ctx[ECX] = ecx;
    ctx[2] = edx;
    return 0;
}

/* MorePairs, "Make room in mega all", x86 0x5CCFAD: each of the weapon's
 * command entries (from [0xC068B0] + 0x4C, 6 bytes each, up to the first
 * 0xFF) of type 5, 6, 9, 0xA, 0xB or 0xE gets [ebp-4] more uses. */
PT static void pt_mp_mega(u32 *ctx, g2h_t g2h)
{
    u32 ebp = ctx[EBP], esp = ctx[ESP];
    u32 edx = (ctx[2] & 0xFFFFFF00u) | U8(ebp - 4), dl = edx & 0xFF;
    u32 ecx = U32(0xC068B0) + 0x4C, eax;

    U32(esp - 4) = 0x5CCFB5;                    /* call 0x5CCFBA */
    for (;;) {
        u32 a;
        eax = U32(ecx);
        if ((eax & 0xFF) == 0xFF)
            break;
        U32(esp - 8) = 0x5CCFCE;                /* call 0x5CCFD4 */
        a = (eax - 5) & 0xFF;
        if (a < 2 || ((a - 4) & 0xFF) < 3 || ((a - 4) & 0xFF) == 5) {
            eax = U32(ecx + 4);
            U32(ecx + 4) = (eax & 0xFFFFFF00u) | ((eax + dl) & 0xFF);
        }
        ecx += 6;
    }
    ctx[0] = eax;
    ctx[ECX] = ecx;
    ctx[2] = edx;
}

/* ----------------------------------------------------- Fix Equip Display
 * FixEquipDisplay: the equip menu's stats count each equipment's stat
 * bonuses (four stat ids at +0x10 / +0x16, their values 4 bytes on).
 * x86 0x707979: the bonus to stat `cl` of the list at `edx`, or 0.
 */
static inline __attribute__((always_inline)) u32 fe_bonus(g2h_t g2h, u32 edx,
                                                         u32 cl)
{
    u32 i;

    for (i = 0; i < 4; i++)
        if (U8(i + edx) == (cl & 0xFF))
            return U8(i + edx + 4);
    return 0;
}

#define L(o) U32(ebp - (o))

/* x86 0x707952 (ecx = the character record offset, edx = the weapon's
 * x 0x2C, eax 0) to 0x707A4A: the current weapon and armour, into the
 * frame the mod widened to 0x6C. Leaves eax, ecx and edx as it does. */
PT static void pt_fe_now(u32 *ctx, g2h_t g2h, u32 edx)
{
    u32 ebp = ctx[EBP], ecx = ctx[ECX], eax = 0, c;

    edx += 0xDBE734;
    eax = U8(edx);
    L(0x18) = eax;
    L(0x5C) = eax;
    eax = U8(edx + 4);
    L(8) = eax;
    L(0x40) = eax;
    edx += 0x10;
    U32(ctx[ESP] - 4) = ecx;                    /* push ecx */
    c = (ecx & 0xFFFFFF00u) | 2;
    eax = fe_bonus(g2h, edx, c);
    L(0x44) = eax;
    L(0x50) = eax;
    c -= 1;
    eax = fe_bonus(g2h, edx, c);
    L(0xC) = eax;
    L(0x30) = eax;
    L(0x60) = eax;
    c = (c & 0xFFFFFF00u) | 3;
    eax = fe_bonus(g2h, edx, c);
    L(0x34) = eax;
    L(0x1C) = eax;
    L(0x64) = eax;
    edx = ecx;                                  /* pop edx */
    eax = ((eax & 0xFFFFFF00u) | U8(edx + 0xDBFDA9)) * 0x24;
    edx = eax + 0xDBCCE2;
    c = U8(edx);
    L(0xC) += c;
    L(0x30) += c;
    L(0x68) = c;
    c = U8(edx + 2);
    L(0x14) = c;
    L(0x20) = c;
    edx += 0x16;
    c = 2;
    eax = fe_bonus(g2h, edx, c);
    L(0x44) += eax;
    L(0x54) = eax;
    c = 0;
    eax = fe_bonus(g2h, edx, c);
    L(0x18) += eax;
    L(0x58) = eax;
    eax = U8(edx - 0x15);
    L(0x6C) = eax;
    L(0x34) += eax;
    L(0x1C) += eax;
    eax = L(0x18);
    L(0x10) = eax;
    eax = (eax & 0xFFFFFF00u) | U8(edx - 0x13);
    L(0x38) = eax;
    L(0x28) = eax;
    /* 0x707A3D, and 0x707A44 as the mod has it */
    L(0x2C) = 0;
    eax = L(0x44);
    L(0x2C) = eax;
    ctx[0] = eax;
    ctx[ECX] = c;
    ctx[2] = edx;
}

/* x86 0x707B9B (ecx = [ebp-0x3C] x 0x2C) to 0x707C1A: the weapon the
 * cursor is on, against the current armour. */
PT static void pt_fe_weapon(u32 *ctx, g2h_t g2h)
{
    u32 ebp = ctx[EBP], ecx = L(0x3C) * 0x2C, edx, eax = 0;

    edx = ecx + 0xDBE734;
    eax = U8(edx + 4);
    L(0x40) = eax;
    eax = U8(edx) + L(0x58);
    edx += 0x10;
    L(0x10) = eax;                              /* 0x707A12 */
    ecx = (ecx & 0xFFFFFF00u) | 2;
    eax = fe_bonus(g2h, edx, ecx) + L(0x54);
    L(0x2C) = eax;
    ecx = (ecx & 0xFFFFFF00u) | 1;
    eax = fe_bonus(g2h, edx, ecx) + L(0x68);
    L(0x30) = eax;
    ecx = (ecx & 0xFFFFFF00u) | 3;
    eax = fe_bonus(g2h, edx, ecx) + L(0x6C);
    L(0x1C) = eax;
    U32(ctx[ESP] - 4) = 0x707BBC;               /* call 0x707A12 */
    ctx[0] = eax;
    ctx[ECX] = ecx;
    ctx[2] = edx;
}

/* x86 0x707BD6 (eax = [ebp-0x3C]) to 0x707C1A: the armour the cursor is
 * on, against the current weapon. */
PT static void pt_fe_armour(u32 *ctx, g2h_t g2h)
{
    u32 ebp = ctx[EBP], eax = ctx[0], ecx, edx;

    ecx = eax * 0x24;
    edx = ecx + 0xDBCCE2;
    eax = ((eax & 0xFFFFFF00u) | U8(edx)) + L(0x60);
    L(0x30) = eax;
    eax = (eax & 0xFFFFFF00u) | U8(edx + 2);
    L(0x20) = eax;
    eax = ((eax & 0xFFFFFF00u) | U8(edx + 1)) + L(0x64);
    L(0x1C) = eax;
    edx += 0x16;
    ecx = (ecx & 0xFFFFFF00u) | 2;
    eax = fe_bonus(g2h, edx, ecx) + L(0x50);
    L(0x2C) = eax;
    eax = (eax & 0xFFFFFF00u) | U8(edx - 0x13);
    L(0x28) = eax;
    ecx = 0;
    eax = fe_bonus(g2h, edx, ecx) + L(0x5C);
    L(0x10) = eax;
    ctx[0] = eax;
    ctx[ECX] = ecx;
    ctx[2] = edx;
}
#undef L

/* ---------------------------------------------------- Unlimited Auto Sense
 * UnlimitedAutoSense, the battle text formatter x86 0x419A8F's number field
 * (x86 0x419BA5..0x419C0E, from the mod's cave at x86 0x916075 on): in a
 * Sense message (target 4 or more; the field's position, [ebp-0xC], 6 for
 * HP or 0xA for MP) the number is the enemy's full 32-bit HP or MP, and the
 * digits are taken from 32 bits, as the mod's `and eax, 0xFFFFFFFF` words
 * do. Any other number is the stock 16-bit one: where the mod reads the
 * upper half of [ebp-0x14] the game never wrote it (stack left-overs).
 * Ends as the x86 does at 0x419C0F: eax = edx = the last quotient, ecx 10.
 *
 * ShowStealAndMorph (show), x86 0x9160D4 and 0x916420: when the command
 * steals or morphs, or the target can be (its attack-flag bytes), also
 * draws "Steal" with the first item there is to steal, or "Already Stolen",
 * and "Morph" with what it morphs into, with the game's text draw. Not from
 * the formatter's call that returns to x86 0x4199F5 (us_call_b writes the
 * return addresses the mod tells the callers apart by). The mod leaves ebx
 * 0 or a pointer; here the caller keeps its own ebx.
 * fns: trampolines to 0x419457 (text by kind), 0x6F5B03 (draw), 0x6C6B06
 * (materia name).
 */
PT static u32 calln(u32 *ctx, g2h_t g2h,
        fn_t fn, u32 n, u32 a0, u32 a1, u32 a2, u32 a3)
{
    u32 sp = ctx[ESP];

    U32(sp - 4 * n) = a0;
    U32(sp - 4 * n + 4) = a1;
    U32(sp - 4 * n + 8) = a2;
    if (n > 3)
        U32(sp - 4 * n + 12) = a3;
    ctx[ESP] = sp - 4 * n - 4;  /* the arguments, and the return slot */
    fn();                       /* its `ret` pops the slot */
    ctx[ESP] = sp;
    return ctx[0];
}

#define US_KIND    ((fn_t)(fns))
#define US_DRAW    ((fn_t)(fns + 4))
#define US_MATERIA ((fn_t)(fns + 8))

/* an item word (0xFFFF none) drawn at x, y 0x100, as the mod decodes it */
PT static void us_item(u32 *ctx,
        g2h_t g2h, char *fns, u32 at, u32 x)
{
    u32 ax = U16(at), k = 0, name;

    if (ax == 0xFFFF)
        return;
    if ((short)ax >= 0x80) {
        ax = (ax - 0x80) & 0xFFFF, k = 1;
        if ((short)ax >= 0x80) {
            ax = (ax - 0x80) & 0xFFFF, k = 2;
            if ((short)ax >= 0x20) {
                ax = (ax - 0x20) & 0xFFFF, k = 3;
                if ((short)ax >= 0x20) {
                    ax = (ax - 0x20) & 0xFFFF;
                    name = calln(ctx, g2h, US_MATERIA, 3, ax, 7, 0x3DCED917, 0);
                    goto draw;
                }
            }
        }
    }
    name = calln(ctx, g2h, US_KIND, 3, 0xA, ax, k, 0);
draw:
    calln(ctx, g2h, US_DRAW, 4, x, 0x100, name, 7);
}


static inline __attribute__((always_inline)) void us_show(u32 *ctx,
        g2h_t g2h, char *fns, u32 ebp)
{
    u32 t = U32(0xDC3C9C), en, a, p, ebx, d, i, s, sp0 = ctx[ESP], sp;
    u8 al, bl, dl, b;

    en = U8((t - 4) * 16 + 0x9A8794) * 0xB8 + 0x9A8F24;
    al = U8(0x9A85D4);
    if (U8(ebp + 4) == 0xF5)
        return;
    if (!(al > 0x80))
        al = 0;
    dl = U8(0xDC3C70);
    if (dl == 5 || dl == 0x11)
        al |= 0x40;
    if (dl == 9)
        al |= 0x20;
    a = U32(0xDC3C7C);
    if (dl == 2 || dl == 0x15) {
        p = a * 0x700 + 0xDC2110;
        d = (U32(p + 4) * 3 + U32(p) + U32(p + 0x14) * 3) * 8;
        ebx = a * 0x440 + 0xDBA5A7 + d;
    } else {
        p = a * 0x700 + 0xDC20A0;
        d = U8(p) * 4;
        d = (d & ~0xFFu) | ((d + U8(p + 4)) & 0xFF);    /* add dl, [eax+4] */
        ebx = a * 0x440 + 0xDBA4E9 + d * 6;
    }
    b = U8(ebx);
    if (b & 8)
        al |= 0x40;
    if (b & 4)
        al |= 0x20;
    if (!al)
        return;
    bl = al & 0x60;
    if (bl != 0x20 && bl < 0x40)
        return;

    /* the texts' calls run below a copy of "Already Stolen" */
    sp = (sp0 - 0x20) & ~3u;
    U32(sp) = 0x45524C21;       /* "Already Stolen", FF-terminated */
    U32(sp + 4) = 0x00594441;
    U32(sp + 8) = 0x4C4F5433;
    U32(sp + 12) = 0x00FF4E45;
    ctx[ESP] = sp;
    if (bl >= 0x40) {
        s = calln(ctx, g2h, US_KIND, 3, 8, 5, 0, 0);
        calln(ctx, g2h, US_DRAW, 4, 0x38, 0x100, s, 7);
        if (U8(U32(0xDC3C9C) * 0x44 + 0x9A8B39) == 1) {
            calln(ctx, g2h, US_DRAW, 4, 0x7F, 0x100, sp, 7);
        } else {
            for (i = 0; i < 4; i++)
                if ((U8(en + i) & 0xC0) == 0x80) {
                    us_item(ctx, g2h, fns, en + 4 + 2 * i, 0x7F);
                    break;
                }
        }
    }
    if (bl == 0x20 || bl == 0x60) {
        s = calln(ctx, g2h, US_KIND, 3, 8, 9, 0, 0);
        calln(ctx, g2h, US_DRAW, 4, 0x150, 0x100, s, 7);
        us_item(ctx, g2h, fns, en + 0x18, 0x1A5);
    }
    ctx[ESP] = sp0;
}

PT static void pt_us_case(u32 *ctx, g2h_t g2h, u32 show, char *fns)
{
    u32 ebp = ctx[EBP], v, t, pos, n = 0;
    u8 *f = (u8 *)ctx;

    U32(ebp - 0x58) = 0;
    v = U16(ebp - 0x14);
    t = U32(0xDC3C9C);
    pos = U32(ebp - 0xC);
    if ((s32)t >= 4 && (pos == 0xA || pos == 6)) {
        v = U32((t - 4) * 0x68 + 0x9AB2A8 + (pos == 6 ? 0 : 4));
        U32(ebp - 0x14) = v;
        if (show)
            us_show(ctx, g2h, fns, ebp);
    }
    while (n < 0x10) {
        U32(ebp - 0x54 + 4 * n) = (u32)((s32)v % 10);
        U32(ebp - 0x58) = ++n;
        v = (u32)((s32)v / 10);
        U32(ebp - 0x14) = v;
        if (!v)
            break;
    }
    ctx[0] = v;
    ctx[ECX] = 10;
    ctx[2] = v;
    f[0x20] = 0;                /* cmp / test: CF 0, ZF 1, SF 0, OF 0 */
    f[0x23] = 1;
    f[0x24] = 0;
    f[0x26] = 0;
}

/* ------------------------------------------------- Break Damage/HP Limit
 * BreakDamageHPLimit, x86 0x5BB9C9 as the mod has it: the damage number's
 * n digit sprites (n 5, or 9 for Unlimited Damage) from the 32-bit damage
 * at [esp+4] into the u16 array at [esp+8], then the leading 0x98s
 * blanked; eax is n - the first shown digit (al 1 when only the last one
 * is). Called in place of the translated routine, its return slot pushed;
 * the registers, frame and stack words end as the x86 leaves them.
 */
PT static void pt_bd_digits(u32 *ctx, g2h_t g2h, u32 n)
{
    u32 sp0 = ctx[ESP], ebp0 = ctx[EBP], fr = sp0 - 4, arr, eax, ecx = 0, edx;
    s32 i, v;

    U32(fr) = ebp0;                             /* push ebp */
    U32(fr - 4) = ctx[ECX];                     /* push ecx */
    arr = U32(fr + 0xC);
    for (i = 0; i < (s32)n; i++) {
        U32(fr - 4) = i;
        v = (s32)U32(fr + 8);
        U16(arr + (n - 1 - i) * 2) = (u16)((u32)(v % 10) << 3);
        U32(fr + 8) = (u32)(v / 10);
        ecx = (U16(arr + (n - 1 - i) * 2) + 0x98) & 0xFFFF;
        U16(arr + (n - 1 - i) * 2) = (u16)ecx;
    }
    U32(fr - 4) = i;
    eax = n;
    edx = ctx[2];
    for (i = 0; i < (s32)n - 1; i++) {
        U32(fr - 4) = i;
        edx = i;
        eax = arr;
        ecx = (u32)(s32)(short)U16(arr + i * 2);
        if (ecx != 0x98) {
            eax = n - i;
            goto out;
        }
        U16(arr + i * 2) = 0;
        ecx = i + 1;
    }
    U32(fr - 4) = i;
    eax = (eax & ~0xFFu) | 1;
out:
    ctx[0] = eax;
    ctx[ECX] = ecx;
    ctx[2] = edx;
    ctx[EBP] = ebp0;
    ctx[ESP] = sp0 + 4;                         /* its `ret` */
}

/* The battle damage cap, x86 0x9A7000 (from 0x5DAA92): 99,999 when the
 * attacker (0x99E308, a party slot 0-2) is the character in slot 6 of
 * 0xDC0230's list or has learnt a level 4 limit (the word at 0xDBFDAE +
 * 0x84 x char is 0x200 or more), else 9,999. eax and ebx 0, as the mod
 * leaves them (edx 0 after its `mul`). */
PT static u32 pt_bd_cap(u32 *ctx, g2h_t g2h)
{
    u32 al = U8(0x99E308), bl;

    ctx[0] = 0;
    ctx[3] = 0;
    if ((signed char)al > 2)
        return 9999;
    bl = U8(0xDC0230 + al);
    if (bl == 6)
        return 99999;
    ctx[2] = 0;
    if ((short)U16(0xDBFDAE + bl * 0x84) < 0x200)
        return 9999;
    return 99999;
}

/* The HP / max-HP text of the battle and healing menus, x86 0x6DD855 /
 * 0x6E1472 (`push 4` -> 5) and their x position: only in the branch that
 * pushed 6 or 7 (the other pushes 2 there), which the ARM shares the call
 * with. esp is the return slot: x at +4, the digit count +0x10, then the
 * colour. */
PT static void pt_bd_args(u32 *ctx, g2h_t g2h, u32 x)
{
    u32 esp = ctx[ESP];

    if (U32(esp + 0x14) != 2) {
        U32(esp + 0x10) = 5;
        U32(esp + 4) = x;
    }
}
