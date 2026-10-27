/*
 * fxinterp.c -- BUILD 747. FFNx's InterpolationEffectDecorator for battle
 * effects (src/ff7/battle/effect.cpp + animations.cpp
 * battle_animate_material_texture / battle_animate_texture_spt).
 *
 * The 60 FPS throttle runs a decorated effect slot's function once per
 * battle tick for real (phase 1) and three times paused (phases 2, 3, 0).
 * At Full 30 FPS (FREQ 2) it is once real (phase 1) and once paused
 * (phase 0), and the paused draw moves 1/2 of the way.
 * The game draws BEFORE it advances, so the real call draws state S(k) and
 * leaves S(k+1); the paused calls draw S(k+1). FFNx saves what each texture
 * draw used on the real call and, on the paused calls, moves the draw 1/4,
 * 2/4, 3/4 of the way from that toward what it is about to draw:
 *
 *   the model matrix (the engine's global rotation_matrix), and
 *     material: its transparency and UV scroll (field_8), the engine's
 *               secondary colour, and seven palette_extra fields;
 *     sprite  : its colour -- and only when the slot's last real call made
 *               exactly one texture draw (FFNx's textureNumCalls == 1).
 *
 * A draw is identified by (slot, guest return address, index of the draw
 * within this call), FFNx's Cantor hash of the last two inside a per-slot
 * map that a real call clears. Here the map is one table with a per-slot
 * generation; a full probe run simply skips the draw (stock behaviour).
 *
 * The throttle caves publish the running slot in Cur (ff7nx_dispatch,
 * `interp_cur`) and clear it after the call; Cur.slot == 0 means "not in a
 * decorated call" and every entry returns at once.
 *
 * Guest memory is reached through the page table (GOT +0x12CE8C0), byte by
 * byte where a value could straddle a page.
 *
 * BUILD 749 -- three guards FFNx does not have. Its blind lerp breaks any
 * effect whose per-tick values JUMP or are not integers, and Knights of the
 * Round's curtain (x86 0x480DF9 -> 0x481096) does all three: a two-copy
 * cylinder conveyor whose stack position wraps back 2462 units every
 * 2462/speed ticks (with the copies' mirror parity flipping on the same
 * tick), whose palette_extra carries FLOAT bits (z_offset, v_offset,
 * z_offset_2) and mode numbers (scroll_v 1/2, field_24). Lerping those for
 * three frames after a wrap slid the stack off its own ends (black panels),
 * mixed mirror modes (flat bands) and broke the copy joint.
 *   - matrix: a tick whose position moves > POS_JUMP or whose rotation
 *     moves > ROT_JUMP on any element is drawn live (a cut, not a slide);
 *   - field_8: its two bytes are separate wrapping UV scrolls -- each moves
 *     the short way round mod 256, or is drawn live if it jumped;
 *   - palette_extra: x/y/z offsets only when they move <= POS_JUMP as
 *     integers (a changing float never does); field_24, z_offset_2,
 *     scroll_v, v_offset are modes/floats and are always drawn live.
 *
 * BUILD 782 -- the camera moves on every frame, the effect does not. An
 * effect's model matrix has the battle view (0xBFCB30) multiplied in
 * (661e85(0xBFCB30, local, out) and the engine's own composition helpers),
 * and since BUILD 384 the camera is interpolated: the view is different on
 * each of the three paused frames. FFNx's blind lerp from the saved matrix
 * (old view x old state) toward the live one (new view x new state) lets
 * only step/4 of the camera's motion SINCE THE TICK through -- 1/4, 2/4, 3/4
 * of a growing offset: the camera arrives at 1/16, 4/16, 9/16 and then jumps
 * the rest on the tick, a 15 Hz judder on every effect while the camera
 * moves (and models, drawn with the live view, slide against them).
 * Hardware 10-22 (bahamut_zero.mp4).
 *
 *   The real call also saves the view it drew with. A paused draw whose view
 *   has changed first carries the saved matrix to the new view --
 *   V_now . inv(V_saved) . M_saved, i.e. the old STATE seen by the new
 *   camera -- and lerps from that, so only the effect's own motion is
 *   interpolated and the camera is exact on every frame.
 *
 *   The engine composes the struct as D3D row vectors or as column vectors
 *   (its flag at +0x48, the branch in 661e85), and the view is always the
 *   left operand. The carried matrix is used only if it is nearer the live
 *   one than the saved matrix is: a draw that does not carry the view
 *   (screen space) keeps the saved matrix -- exactly the 749 behaviour. A
 *   view with no inverse, or one that did not change, changes nothing.
 * BUILD 811 -- a live translation equal to the saved one is never carried (see
 *   rebase): KOTR's finale glow.
 */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long long u64;
typedef signed short s16;
typedef signed int s32;

#define ENGINE_PTR   0x90AAF0u     /* ff7_game_engine_data * (packed)     */
#define ENG_MATRIX   0x10u         /* rotation_matrix: s16[9], s32[3]     */
#define ENG_COLOR2   0x50u         /* secondary_color                     */
#define PALETTE_X    0xC06A00u     /* palette_extra                       */
#define VIEW_MTX     0xBFCB30u     /* BUILD 782: the battle view matrix   */
/* BUILD 811: Knights of the Round's first tick is a cut. Its camera
   (run_kotr_camera_476AFB, effect100 slot 99) and main loop register
   together; the main loop's first real call draws the stage at its setup
   placement and the next state places it for the summon. Both are black on
   screen, but the paused frames' 1/4..3/4 blend between them swept a stage
   piece around the camera for three frames -- "a frame inside the enemy
   model", with big enemies, whose setup offsets stay under the POS_JUMP
   guard (hardware 10-26, kotrcamprobe: camera static, both camera records
   at opcode 11). While the throttle's cam_init is 0 -- only KOTR's setup
   tick ever has that (BUILD 783: its registration writes 2, its first
   advance 0, the second 1) -- and slot 99 runs that camera, paused draws
   are live. CUR_CAM_INIT is cam_init's distance from Cur in the throttle
   page; ff7nx_fxinterp.apply_to_nso checks it against the built main. */
#define E100_FN      0xBF2858u
#define KOTR_CAM_FN  0x476AFBu
#define CUR_CAM_INIT 0x668u
#define N_SLOT 160u                /* effect100 (100) + effect60 (60)     */
#define N_ENT  256u
#define PROBE  16u
/* Sub-steps per battle tick: 4 at 60 FPS (fxinterp.bin), 2 at Full 30 FPS
   (fxinterp30.bin, built with -DFREQ=2). A power of two. */
#ifndef FREQ
#define FREQ   4
#endif
#define PMASK  (FREQ - 1u)
#define POS_JUMP 1024           /* BUILD 749 */
#define ROT_JUMP 2048           /* 0.5 in the s16 4.12 rotation       */
#define F8_JUMP  64
/* BUILD 749d: a draw whose palette MODE changed since the real call
   (scroll_v or field_24 -- KOTR's curtain/tube conveyors flip every copy's
   mirror mode on the tick their stack wraps back one copy length) is a cut:
   every field of that draw is drawn live. */

typedef struct {
    u64 slot;      /* host &ctr[idx] of the running slot, 0 = none          */
    u8 phase;      /* 1 = the real step, 2/3/0 = paused                      */
    u8 pad;
    u8 idx;        /* texture draws counted so far in this call             */
    u8 seen;       /* the first draw of this call has been handled          */
    u32 pad2;
} Cur;

typedef struct {
    u32 ret;
    u8 slot, idx;
    u16 gen;
    s32 pos[3];
    s32 pal[7];
    s16 m[9];
    u16 f8;
    s16 tr;
    u8 col[4];
    s32 vel[3];    /* BUILD 749g: the last tick's position step            */
    u32 hv;        /* vel is known                                         */
    s16 vm[9];     /* BUILD 782: the view the real call drew with          */
    u16 vok;
    s32 vt[3];
    u32 lh;        /* 783: fingerprint of the last paused frame's live matrix */
    u32 lstep;     /* 783: its step (1..3), 0 = none                         */
} Ent;

/* BUILD 749c: what this call already wrote. The game often builds ONE value
   (the model matrix, a material, the secondary colour) and draws several
   times with it -- KOTR's curtain draws its two cylinder copies with one
   matrix. FFNx (and 747) interpolated in place, so the second draw read the
   first draw's OUTPUT as its live value and moved again: the copies ended up
   (n-p)*s*(1-s) apart -- 3/16..4/16 of a tick's motion -- an overlap or gap
   at their joint on exactly the three repeated frames of every four. A
   later draw that still sees what we wrote gets the original live value
   back first. */
#define N_UN 12
typedef struct {
    u32 addr;
    u8 n, valid, pad[2];
    u8 out[32];
    u8 live[32];
} Undo;

typedef struct {
    u16 gen[N_SLOT];
    u8 numc[N_SLOT];     /* textureNumCalls as the CURRENT real call sees it */
    u8 pend[N_SLOT];     /* draws counted on the latest real call            */
    u8 lastp[N_SLOT];    /* 1 + cycle position of the slot's last draw call  */
    u8 snumc[N_SLOT];    /* 783: sprites the previous real call drew          */
    u8 spend[N_SLOT];    /* 783: sprites counted on the latest real call      */
    Ent e[N_ENT];
    Undo un[N_UN];
} State;

_Static_assert(sizeof(Cur) == 16, "Cur layout");
_Static_assert(sizeof(Ent) == 132, "Ent layout");
_Static_assert(sizeof(Undo) == 72, "Undo layout");
_Static_assert(sizeof(State) == 160 * 7 + 256 * 132 + N_UN * 72, "State layout (ff7nx_fxinterp.STATE_BYTES)");

/* palette_extra x, y, z, field_24, z_offset_2, scroll_v, v_offset (bytes,
   packed so the blob carries no data section) */
#define PAL_PACK 0x14100824180C00ull
#define PAL_OFF(i) ((u32)((PAL_PACK >> (8 * (i))) & 0xFF))

static inline __attribute__((always_inline)) u8 *g2h(u8 **pg, u32 g)
{
    u8 *p = pg[g >> 12];
    return p ? p + (g & 0xFFFu) : 0;
}

__attribute__((noinline)) static int rd(u8 **pg, u32 g, u8 *dst, u32 n)
{
    for (u32 i = 0; i < n; i++) {
        u8 *p = g2h(pg, g + i);
        if (!p)
            return 0;
        dst[i] = *p;
    }
    return 1;
}

__attribute__((noinline)) static void wr(u8 **pg, u32 g, const u8 *src, u32 n)
{
    for (u32 i = 0; i < n; i++) {
        u8 *p = g2h(pg, g + i);
        if (p)
            *p = src[i];
    }
}

/* b holds what the guest has now; if it is still exactly what this call
   wrote there, put the original live value back. 1 if it did. */
__attribute__((noinline)) static int same(State *st, u32 k, u32 g, const u8 *b, u32 n)
{
    Undo *u = &st->un[k];
    if (!u->valid || u->addr != g || u->n != n)
        return 0;
    for (u32 i = 0; i < n; i++)
        if (b[i] != u->out[i])
            return 0;
    return 1;
}

__attribute__((noinline)) static int unfix(State *st, u32 k, u32 g, u8 *b, u32 n)
{
    if (!same(st, k, g, b, n))
        return 0;
    for (u32 i = 0; i < n; i++)
        b[i] = st->un[k].live[i];
    return 1;
}

/* write `out` (n bytes) to g, remembering the live value it replaces */
__attribute__((noinline)) static void put(u8 **pg, State *st, u32 k, u32 g,
                                          const u8 *live, const u8 *out, u32 n)
{
    Undo *u = &st->un[k];
    u->addr = g;
    u->n = (u8)n;
    for (u32 i = 0; i < n; i++) {
        u->live[i] = live[i];
        u->out[i] = out[i];
    }
    u->valid = 1;
    wr(pg, g, out, n);
}

static inline __attribute__((always_inline)) u32 rd32(u8 **pg, u32 g)
{
    u8 b[4];
    if (!rd(pg, g, b, 4))
        return 0;
    return (u32)b[0] | (u32)b[1] << 8 | (u32)b[2] << 16 | (u32)b[3] << 24;
}

/* FFNx interpolateValue: previous + ((next - previous) * step) / n, in int
   with C truncation, converted back to the field's own type by the caller */
/* BUILD 811: KOTR's setup tick (see CUR_CAM_INIT). The guest slot is read
   first, so the throttle byte is touched only while KOTR's camera runs. */
__attribute__((noinline)) static int kotr_setup(Cur *cur, u8 **pg)
{
    if (rd32(pg, E100_FN + 4u * 99u) != KOTR_CAM_FN)
        return 0;
    return ((const volatile u8 *)cur)[CUR_CAM_INIT] == 0;
}

static inline __attribute__((always_inline)) s32 lerp(s32 p, s32 n, s32 step)
{
    return p + ((n - p) * step) / FREQ;
}

static inline __attribute__((always_inline)) u64 adiff(s32 a, s32 b) { long long d = (long long)a - b; return (u64)(d < 0 ? -d : d); }

static inline __attribute__((always_inline)) s32 ld16s(const u8 *b) { return (s16)(b[0] | b[1] << 8); }
static inline __attribute__((always_inline)) u32 ld16u(const u8 *b) { return (u16)(b[0] | b[1] << 8); }
static inline __attribute__((always_inline)) s32 ld32(const u8 *b) { return (s32)((u32)b[0] | (u32)b[1] << 8 | (u32)b[2] << 16 | (u32)b[3] << 24); }
static inline __attribute__((always_inline)) void st16(u8 *b, u32 v) { b[0] = (u8)v; b[1] = (u8)(v >> 8); }
static inline __attribute__((always_inline)) void st32(u8 *b, u32 v) { b[0] = (u8)v; b[1] = (u8)(v >> 8); b[2] = (u8)(v >> 16); b[3] = (u8)(v >> 24); }

/* the slot number, or -1; also turns over the slot's map on the first draw
   of a real call (FFNx: previousFrameDataMap.clear() before the call) */
__attribute__((noinline)) static s32 begin(Cur *cur, State *st)
{
    u8 *slot = (u8 *)cur->slot;
    u8 *e100 = (u8 *)cur - 0xE0;   /* effect100 ctr[0] */
    u8 *e60 = (u8 *)cur - 0x50;    /* effect60 ctr[0]  */
    u32 s;
    if (!slot)
        return -1;
    if (slot >= e100 && slot < e100 + 100)
        s = (u32)(slot - e100);
    else if (slot >= e60 && slot < e60 + 60)
        s = 100u + (u32)(slot - e60);
    else
        return -1;
    if (!cur->seen) {
        /* Position in the cycle: real 0, then paused 1, 2, 3. A call at or
           before the position of this slot's previous drawing call means a
           real call happened in between that drew nothing -- FFNx cleared
           the map then too, so turn it over here as well. */
        u32 o = (cur->phase + PMASK) & PMASK;
        cur->seen = 1;
        for (u32 k = 0; k < N_UN; k++)
            st->un[k].valid = 0;
        if (o == 0 || (st->lastp[s] && o <= (u32)st->lastp[s] - 1u)) {
            st->numc[s] = (o == 0) ? st->pend[s] : 0;
            st->pend[s] = 0;
            st->snumc[s] = (o == 0) ? st->spend[s] : 0;      /* 783 */
            st->spend[s] = 0;
            if (++st->gen[s] == 0)
                st->gen[s] = 1;
        }
        st->lastp[s] = (u8)(o + 1u);
    }
    return (s32)s;
}

static inline __attribute__((always_inline)) u32 hash(u32 ret, u32 s, u32 idx)
{
    u32 h = ret * 2654435761u ^ s * 40503u ^ idx * 0x9E37u;
    return (h ^ (h >> 15)) & (N_ENT - 1);
}

/* the entry this draw saved on the latest real call, or 0 */
__attribute__((noinline)) static Ent *look(State *st, u32 ret, u32 s, u32 idx)
{
    u32 h = hash(ret, s, idx);
    for (u32 k = 0; k < PROBE; k++) {
        Ent *e = &st->e[(h + k) & (N_ENT - 1)];
        if (e->ret == ret && e->slot == s && e->idx == idx)
            return e->gen == st->gen[s] ? e : 0;
    }
    return 0;
}

/* an entry to save into: this draw's own, or a free / stale one, or 0 */
__attribute__((noinline)) static Ent *claim(State *st, u32 ret, u32 s, u32 idx)
{
    u32 h = hash(ret, s, idx);
    for (u32 k = 0; k < PROBE; k++) {
        Ent *e = &st->e[(h + k) & (N_ENT - 1)];
        if (e->ret == ret && e->slot == s && e->idx == idx)
            return e;
    }
    for (u32 k = 0; k < PROBE; k++) {
        Ent *e = &st->e[(h + k) & (N_ENT - 1)];
        if (!e->ret || e->slot >= N_SLOT || e->gen != st->gen[e->slot]) {
            e->ret = ret;
            e->slot = (u8)s;
            e->idx = (u8)idx;
            e->hv = 0;
            e->vok = 0;            /* 783: a new draw has no verdict */
            e->lstep = 0;
            return e;
        }
    }
    return 0;
}

/* ---- BUILD 782: carry a saved matrix to the view drawn now ------------ */
typedef long long s64;
#define VK_CARRY 2              /* 783: vok bits -- the draw carries the view */
#define VK_MOVES 4              /* 783: ... seen moving within a tick        */

/* o = a . b / 4096 (3x3, 4.12) */
__attribute__((noinline)) static void mm(const s64 *a, const s64 *b, s64 *o)
{
    for (u32 i = 0; i < 3; i++)
        for (u32 j = 0; j < 3; j++) {
            s64 acc = 0;
            for (u32 k = 0; k < 3; k++)
                acc += a[3 * i + k] * b[3 * k + j];
            o[3 * i + j] = acc / 4096;
        }
}

/* o = v . a / 4096 (row vector) or a . v / 4096 (column vector) */
__attribute__((noinline)) static void mv(const s64 *a, const s64 *v, s64 *o, int row)
{
    for (u32 j = 0; j < 3; j++) {
        s64 acc = 0;
        for (u32 i = 0; i < 3; i++)
            acc += row ? v[i] * a[3 * i + j] : a[3 * j + i] * v[i];
        o[j] = acc / 4096;
    }
}

/* 4.12 inverse of a 4.12 3x3; 0 if it has none */
__attribute__((noinline)) static int inv33(const s64 *m, s64 *o)
{
    s64 c[9];
    c[0] = m[4] * m[8] - m[5] * m[7];
    c[1] = m[5] * m[6] - m[3] * m[8];
    c[2] = m[3] * m[7] - m[4] * m[6];
    c[3] = m[2] * m[7] - m[1] * m[8];
    c[4] = m[0] * m[8] - m[2] * m[6];
    c[5] = m[1] * m[6] - m[0] * m[7];
    c[6] = m[1] * m[5] - m[2] * m[4];
    c[7] = m[2] * m[3] - m[0] * m[5];
    c[8] = m[0] * m[4] - m[1] * m[3];
    s64 det = m[0] * c[0] + m[1] * c[1] + m[2] * c[2];
    /* |det| below (1/8)^3 of a unit basis: degenerate, leave it alone */
    if (det < 0x1000000LL * 8 && det > -0x1000000LL * 8)
        return 0;
    for (u32 i = 0; i < 3; i++)
        for (u32 j = 0; j < 3; j++)    /* inverse = adj / det, adj = cof^T */
            o[3 * i + j] = c[3 * j + i] * (4096LL * 4096LL) / det;
    return 1;
}

static inline __attribute__((always_inline)) s64 sabs(s64 v) { return v < 0 ? -v : v; }

/* The base a paused draw lerps from: the saved matrix (em, ep), or that
   matrix carried to the view drawn now, if that is nearer the live one
   (lm, lp). `row`: the engine composes D3D row vectors (its flag at +0x48
   is 0, 6611fb: t = ta.Rb + tb), else column vectors (661465:
   t = Ra.tb + ta). The view is always the LEFT operand -- census of ff7_en:
   all 525 calls of 661e85 that take 0xBFCB30 take it as the first matrix,
   and the 130 other sites load it as the matrix itself. */
__attribute__((noinline)) static void rebase(u8 **pg, Ent *e, int row, s32 step,
                                             const s16 *lm, const s32 *lp,
                                             s32 *bm, s32 *bp)
{
    u8 b[30];
    s64 nm[9], km[9], ri[9], q[9], q2[9], rm[9], rp[9], r1[9];
    s64 tn[3], tk[3], tm[3], d[3], dm[3], u[3], c[1][3];
    s64 old = 0, err;
    int changed = 0, same = 1, pick = -1;
    for (u32 i = 0; i < 9; i++)
        bm[i] = e->m[i];
    for (u32 i = 0; i < 3; i++)
        bp[i] = e->pos[i];
    if (!(e->vok & 1) || !rd(pg, VIEW_MTX, b, 30))
        return;
    for (u32 i = 0; i < 9; i++) {
        km[i] = ld16s(b + 2 * i);
        nm[i] = e->vm[i];
        rm[i] = e->m[i];
        changed |= km[i] != nm[i];
        same &= rm[i] == lm[i];
    }
    for (u32 i = 0; i < 3; i++) {
        tk[i] = ld32(b + 18 + 4 * i);
        tn[i] = e->vt[i];
        tm[i] = e->pos[i];
        changed |= tk[i] != tn[i];
        dm[i] = tm[i] - tn[i];
    }
    {
        /* BUILD 783: the effect's own state is frozen over a tick's paused
           frames, so a draw whose live matrix still changes from one paused
           frame to the next while the view moves is carrying the view. */
        u32 h = 2166136261u;
        for (u32 i = 0; i < 9; i++)
            h = (h ^ (u32)(u16)lm[i]) * 16777619u;
        for (u32 i = 0; i < 3; i++)
            h = (h ^ (u32)lp[i]) * 16777619u;
        if (changed && step >= 2 && e->lstep == (u32)(step - 1) && h != e->lh)
            e->vok |= VK_CARRY | VK_MOVES;
        e->lh = h;
        e->lstep = (u32)step;
    }
    if (!changed || !inv33(nm, ri))
        return;
    mm(km, ri, q);                /* V_now . inv(V_saved)                */
    mm(ri, km, q2);               /* inv(V_saved) . V_now                */
    mm(q, rm, r1);                /* the rotation, either convention     */
    /* c[0]: the whole matrix carried, view on the left (582 census) */
    if (row) {                    /* t' = t_m + (t_now - t_saved) . R_P  */
        mm(ri, rm, rp);
        for (u32 i = 0; i < 3; i++)
            d[i] = tk[i] - tn[i];
        mv(rp, d, u, 1);
        for (u32 i = 0; i < 3; i++)
            c[0][i] = tm[i] + u[i];
    } else {                      /* t' = Q . (t_m - t_saved) + t_now    */
        mv(q, dm, u, 0);
        for (u32 i = 0; i < 3; i++)
            c[0][i] = u[i] + tk[i];
    }
    /* BUILD 783: a billboard (Phoenix's drop, x86 0x516C64: a uniform
       0x400 scale, the camera-space point as translation) never carries the
       view in its rotation, only in its point: carry the point, in the
       engine's convention (0x662ECC branches on the same flag as 661e85),
       and keep the rotation. */
    {
        int bill = rm[1] == 0 && rm[2] == 0 && rm[3] == 0 && rm[5] == 0
                   && rm[6] == 0 && rm[7] == 0 && rm[0] == rm[4]
                   && rm[0] == rm[8] && rm[0] != 0 && same;
        const s64 *r = bill ? rm : r1;
        int ok = 1;
        if (bill) {
            if (row)
                mv(q2, dm, u, 1);     /* (x - t_saved) . inv(R_s) . R_now + t_now */
            else
                mv(q, dm, u, 0);      /* R_now . inv(R_s) . (x - t_saved) + t_now */
            for (u32 i = 0; i < 3; i++)
                c[0][i] = u[i] + tk[i];
        }
        s64 ot = 0;
        for (u32 i = 0; i < 9; i++)
            old += sabs(rm[i] - lm[i]);
        for (u32 i = 0; i < 3; i++)
            ot += sabs(tm[i] - lp[i]);
        old += ot;
        /* BUILD 811: the view changed since the real call (else we left
           above), so a matrix with the view in it cannot keep its saved
           TRANSLATION (the view's motion is in it): a draw whose live
           translation is exactly the saved one does not carry the view,
           whatever the sticky verdict says. Its rotation may still change
           -- the paused calls draw the next state. KOTR's finale (x86
           0x481E7C) draws its rays and then its glow from one matrix block;
           the glow is local (0,0,512) with a scale that grows each tick,
           but was marked carrying-and-moving (fxentprobe: vok 7) and was
           re-projected through the swinging camera on the three repeated
           frames -- an orb off to the side. (811b: 811a required the whole
           matrix to match, which the growing scale never does.) */
        if (!ot)
            return;
        err = 0;
        for (u32 i = 0; i < 9; i++) {
            ok &= r[i] >= -32768 && r[i] <= 32767;
            err += sabs(r[i] - lm[i]);
        }
        for (u32 i = 0; i < 3; i++) {
            ok &= c[0][i] >= -0x7FFFFFFFLL && c[0][i] <= 0x7FFFFFFFLL;
            err += sabs(c[0][i] - lp[i]);
        }
        if (!ok)
            return;
        /* Nearer than the saved matrix: the draw carries the view (782).
           The verdict sticks (783): one frame into a tick the camera has
           barely moved and the effect's own step can outweigh it (the drop
           falls 156 units a tick), so a draw seen MOVING between two paused
           frames of a tick (its own state is frozen there, only the view
           moves) keeps the carried matrix; a draw clearly nearer as saved
           (twice) is screen space. */
        if (2 * err < old)
            e->vok |= VK_CARRY;
        else if (2 * old < err && !(e->vok & VK_MOVES))
            e->vok &= (u16)~VK_CARRY;
        pick = (err < old || (e->vok & (VK_CARRY | VK_MOVES))) ? 0 : -1;
        if (pick == 0)
            for (u32 i = 0; i < 9; i++)
                r1[i] = r[i];
    }
    if (pick < 0)
        return;                   /* the draw does not carry the view    */
    for (u32 i = 0; i < 9; i++)
        bm[i] = (s32)r1[i];
    for (u32 i = 0; i < 3; i++)
        bp[i] = (s32)c[0][i];
}

/* the matrix: save or interpolate in place */
__attribute__((noinline)) static void matrix(u8 **pg, State *st, u32 mtx,
                                             Ent *e, int real, s32 step,
                                             int modecut)
{
    u8 b[30], live[30];
    s16 lm[9];
    s32 lp[3], bm[9], bp[3];
    if (!rd(pg, mtx, b, 30))
        return;
    if (real) {
        for (u32 i = 0; i < 9; i++)
            e->m[i] = (s16)ld16s(b + 2 * i);
        for (u32 i = 0; i < 3; i++)
            e->pos[i] = ld32(b + 18 + 4 * i);
        /* BUILD 782: and the view this draw was made with (783: the
           verdict bits survive the tick) */
        e->vok &= (u16)~1;
        if (rd(pg, VIEW_MTX, b, 30)) {
            for (u32 i = 0; i < 9; i++)
                e->vm[i] = (s16)ld16s(b + 2 * i);
            for (u32 i = 0; i < 3; i++)
                e->vt[i] = ld32(b + 18 + 4 * i);
            e->vok |= 1;
        }
        return;
    }
    int fixed = unfix(st, 0, mtx, b, 30);
    for (u32 i = 0; i < 30; i++)
        live[i] = b[i];
    for (u32 i = 0; i < 9; i++)
        lm[i] = (s16)ld16s(b + 2 * i);
    for (u32 i = 0; i < 3; i++)
        lp[i] = ld32(b + 18 + 4 * i);
    /* BUILD 782: the engine's composition convention (661e85's branch) */
    rebase(pg, e, rd32(pg, mtx - ENG_MATRIX + 0x48) == 0, step, lm, lp, bm, bp);
    int rcut = 0, pcut = modecut;
    for (u32 i = 0; i < 9; i++)
        if (adiff(lm[i], bm[i]) > ROT_JUMP)
            rcut = 1;                       /* a cut: draw it live */
    for (u32 i = 0; i < 3; i++)
        if (adiff(lp[i], bp[i]) > POS_JUMP)
            pcut = 1;
    if (rcut)
        pcut = 1;
    if (!rcut)
        for (u32 i = 0; i < 9; i++)
            st16(b + 2 * i, (u32)lerp(bm[i], lm[i], step));
    if (!pcut) {
        for (u32 i = 0; i < 3; i++) {
            e->vel[i] = lp[i] - bp[i];
            st32(b + 18 + 4 * i, (u32)lerp(bp[i], lp[i], step));
        }
        e->hv = 1;
    } else if (modecut && !rcut && e->hv) {
        /* BUILD 749g: a conveyor wrap (KOTR's curtain/tube): the live state
           is the same picture one copy length on, with its mirror parity
           flipped. Arrive at it at the speed it was moving instead of
           holding it for the tick -- which read as a brief pause. */
        for (u32 i = 0; i < 3; i++)
            st32(b + 18 + 4 * i, (u32)lerp(lp[i] - e->vel[i], lp[i], step));
    }
    if (fixed || !rcut || !pcut)
        put(pg, st, 0, mtx, live, b, 30);
}

__attribute__((noinline)) static void colour(u8 **pg, State *st, u32 k, u32 g,
                                             Ent *e, int real, s32 step)
{
    u8 b[4], live[4];
    if (!rd(pg, g, b, 4))
        return;
    if (real) {
        for (u32 i = 0; i < 4; i++)
            e->col[i] = b[i];
        return;
    }
    unfix(st, k, g, b, 4);
    for (u32 i = 0; i < 4; i++) {
        live[i] = b[i];
        b[i] = (u8)lerp(e->col[i], b[i], step);
    }
    put(pg, st, k, g, live, b, 4);
}

/* entry of battle_animate_material_texture (x86 0x5D1BA4) */
__attribute__((section(".text.fx_material")))
void fx_material(Cur *cur, State *st, u8 **pg, u8 *gctx, u32 aret)
{
    s32 s;
    u32 esp, ret, arg, eng, idx;
    int real;
    s32 step;
    Ent *e;
    u8 b[4];
    if (!pg || !gctx || (s = begin(cur, st)) < 0)
        return;
    real = (cur->phase & PMASK) == 1;
    step = (s32)((cur->phase + PMASK) & PMASK);
    esp = *(u32 *)(gctx + 0x10);
    ret = aret;                    /* BUILD 749d: the ARM call site */
    arg = rd32(pg, esp + 4);
    idx = cur->idx++;
    if (real)
        st->pend[s] = cur->idx;
    if (!real && kotr_setup(cur, pg))
        return;                    /* BUILD 811: KOTR's setup tick, live */
    eng = rd32(pg, ENGINE_PTR);
    if (!ret || !arg || !eng)
        return;
    e = real ? claim(st, ret, (u32)s, idx) : look(st, ret, (u32)s, idx);
    if (!e)
        return;
    if (real)
        e->gen = st->gen[s];
    int modecut = 0;
    if (!real) {
        u32 sv = rd32(pg, PALETTE_X + 0x10), f24 = rd32(pg, PALETTE_X + 0x24);
        modecut = (s32)sv != e->pal[5] || (s32)f24 != e->pal[3];
    }
    matrix(pg, st, eng + ENG_MATRIX, e, real, step, modecut);
    /* material ctx: field_8 (WORD) at +8, transparency (short) at +0xA */
    if (rd(pg, arg + 8, b, 4)) {
        if (real) {
            e->f8 = (u16)ld16u(b);
            e->tr = (s16)ld16s(b + 2);
        } else {
            /* BUILD 749f: field_8's two bytes and transparency are three
               fields, undone and written one by one. The game rewrites ONE
               of them between draws that share the material (KOTR's curtain
               flips the v byte, xor 0x200, between its two copies); undoing
               the 4 bytes as a unit then failed and the second copy
               interpolated its u byte again from the first copy's output --
               a horizontal step at the copies' joint. */
            /* a one-byte match can be chance; trust it only when another
               field of the same material still holds our output too (the
               game edited the material, it did not rebuild it) */
            u32 nsame = (u32)same(st, 10, arg + 8, b, 1)
                      + (u32)same(st, 11, arg + 9, b + 1, 1)
                      + (u32)same(st, 2, arg + 10, b + 2, 2);
            for (u32 f = 0; f < 3; f++) {
                u32 n = f < 2 ? 1u : 2u, kd = f == 0 ? 10u : f == 1 ? 11u : 2u;
                u8 *p = b + f;
                u8 live[2];
                int fixed = nsame >= 2 && unfix(st, kd, arg + 8 + f, p, n);
                for (u32 k = 0; k < n; k++)
                    live[k] = p[k];
                if (modecut && f == 1) {
                    /* the v byte carries the copy's mirror parity: live */
                    if (fixed)
                        wr(pg, arg + 8 + f, p, n);
                    continue;
                }
                if (f < 2) {
                    /* a wrapping byte scroll (u, v), the short way round */
                    u8 old = (u8)(e->f8 >> (8 * f));
                    s32 d = (s32)(signed char)(u8)(p[0] - old);
                    if (d <= F8_JUMP && d >= -F8_JUMP)
                        p[0] = (u8)(old + lerp(0, d, step));
                } else
                    st16(p, (u32)lerp(e->tr, ld16s(p), step));
                put(pg, st, kd, arg + 8 + f, live, p, n);
            }
        }
    }
    colour(pg, st, 1, eng + ENG_COLOR2, e, real, step);
    for (u32 i = 0; i < 7; i++) {
        u32 g = PALETTE_X + PAL_OFF(i);
        if (!rd(pg, g, b, 4))
            continue;
        if (real) {
            e->pal[i] = ld32(b);
            continue;
        }
        int fixed = unfix(st, 3 + i, g, b, 4);
        u8 live[4];
        for (u32 k = 0; k < 4; k++)
            live[k] = b[k];
        if (i < 3 && !modecut && adiff(ld32(b), e->pal[i]) <= POS_JUMP) {
            st32(b, (u32)lerp(e->pal[i], ld32(b), step));
            put(pg, st, 3 + i, g, live, b, 4);
        } else if (fixed)
            put(pg, st, 3 + i, g, live, b, 4);
    }
}

/* entry of battle_animate_texture_spt (x86 0x5D1FFB) */
__attribute__((section(".text.fx_spt")))
void fx_spt(Cur *cur, State *st, u8 **pg, u8 *gctx, u32 aret)
{
    s32 s;
    u32 esp, ret, arg, eng, idx, n, sn;
    int real;
    s32 step;
    Ent *e;
    if (!pg || !gctx || (s = begin(cur, st)) < 0)
        return;
    real = (cur->phase & PMASK) == 1;
    n = real ? st->numc[s] : st->pend[s];
    /* BUILD 783: FFNx interpolates a sprite only when the slot's previous
       real call made exactly one counted (material) draw, so a slot whose
       ONE draw is a sprite never moves between ticks -- Phoenix's drop (x86
       0x516ACF), whose camera does: it slid with the camera for three
       frames and jumped back on the tick (hardware 10-22). Such a slot is
       interpolated too; FFNx's own rule is unchanged. */
    sn = real ? st->snumc[s] : st->spend[s];
    if (real && st->spend[s] < 255)
        st->spend[s]++;
    /* (a real call saves a sprite while nothing says it is not alone yet:
       the slot's first call, sn 0; its paused frames check spend == 1) */
    if (n != 1 && !(n == 0 && (sn == 1 || (real && sn == 0))))
        return;
    if (!real && kotr_setup(cur, pg))
        return;                    /* BUILD 811: KOTR's setup tick, live */
    step = (s32)((cur->phase + PMASK) & PMASK);
    esp = *(u32 *)(gctx + 0x10);
    ret = aret;                    /* BUILD 749d: the ARM call site */
    arg = rd32(pg, esp + 4);
    if (n == 1) {
        idx = cur->idx++;
        if (real)
            st->pend[s] = cur->idx;
    } else
        idx = cur->idx;            /* 783: keyed, not counted */
    eng = rd32(pg, ENGINE_PTR);
    if (!ret || !arg || !eng)
        return;
    e = real ? claim(st, ret, (u32)s, idx) : look(st, ret, (u32)s, idx);
    if (!e)
        return;
    if (real)
        e->gen = st->gen[s];
    matrix(pg, st, eng + ENG_MATRIX, e, real, step, 0);
    colour(pg, st, 1, arg + 8, e, real, step);  /* texture_ctx->color */
}
