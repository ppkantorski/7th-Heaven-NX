/*
 * leakprobe.c -- what grows across fship <-> world-map trips. DIAGNOSTIC.
 *
 * The fship speckles appear only after SEVERAL trips in and out (and the
 * graphics-pool notes of build 191 saw the same: corruption "after enough
 * churn", sooner with a bigger pool). texprobe proved every texture's data,
 * id and object correct at load, so this probe measures accumulation instead.
 *
 * One hook: native gfx_drv_flip (+0x10DA880), every frame.
 *   * visits: a run of frames with the same key = (driver mode << 16) |
 *     field id. For each visit it keeps, over the visit:
 *       - live texture objects in the texture manager (table of 1024 at
 *         *(module+0x12CF4F0)): at the start, at the end, and the peak
 *       - the manager's parked (recycled) objects, +0x2080
 *       - the guest heap (HeapCreate's pool at guest 0x02000000): free
 *         bytes (minimum and at the end), the largest free block, and the
 *         number of free blocks, walked off the free list every 32 frames
 *   * R3 click outside the world map: the last 16 visits go into the crash
 *     report (stack dump 0..7, TLS dump 8..15) and svcBreak.
 *
 * Built like texprobe.c: position independent, no data/bss sections.
 */
#include <stdint.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32;
typedef uint64_t u64; typedef int32_t s32; typedef int16_t s16;
typedef u8 *(*g2h_fn)(u32 guest);
typedef u32 (*glerr_fn)(void);

#define G_FIELD_ID  0xCFF468u
#define G_PLAYER_ENT 0xE3A7D0u
#define G_VIEW_TYPE 0xDFC4B4u
#define G_CONTROL   0xDE6B5Cu
#define G_HW_TAKEOFF 0xDF5420u
#define HEAP_DESC   0x02000000u   /* +0 total, +4 remains, +8 free list */
#define BLK_HDR     0x34u         /* list points at payload; header before */
#define BLK_SIZE    0x14u
#define BLK_NEXT    0x1Cu
#define BLK_USED    0x20u
#define MGR_NTEX    1024u
#define MGR_PARKED  0x2080u
#define DRIVER_WORLD 4u
#define BTN_R3      ((1ull << 5) | (1ull << 62))
#define MAGIC       0x374B454Cu   /* 'LEK7' */
#define NG          160           /* v7: GL device vtable slots timed (leakprobe.py GFX_SLOTS) */
#define GL_OOM      0x0505u
#define NV          8            /* v3: 8 visits (stack dump); the TLS dump is the Highwind */
#define SAMPLE      32u

typedef struct {                  /* 32 bytes, also the dump format */
    u32 key;                      /* mode << 16 | field */
    u32 frames;
    u16 tex_start, tex_end, tex_peak, parked;
    u16 nerr;                     /* GL errors read this visit (v2, saturating) */
    u16 noom;                     /* ... of them GL_OUT_OF_MEMORY */
    u16 first, last;              /* the first and last error code */
    u32 free_end;                 /* guest heap bytes free at the end */
    u16 seq;                      /* visit number */
    u16 errframes;                /* frames with any GL error */
} V;
_Static_assert(sizeof(V) == 32, "V is the 32-byte dump record");

typedef struct {                  /* v3: the Highwind over one world visit, 128 bytes */
    s16 y_first[16];              /* y on frames 1..16 */
    s16 y_sparse[8];              /* y on frames 24, 32, 48, 64, 96, 128, 192, 256 */
    s16 y_last[16];               /* ring: the last 16 frames (ordered at dump) */
    u16 ctl_first, ctl_last;      /* bit i: control on, frame i of those 16 */
    u16 hw_first, hw_last;        /* bit i: Highwind view (3) */
    s32 x_first, z_first, x_last, z_last;
    s32 y_max, y_last_ctl;        /* highest y; y on the last frame with control in view 3 */
    u32 frames, ring;             /* frames seen; next ring slot */
    s16 takeoff, pad;
    u32 pad2;
} W;
_Static_assert(sizeof(W) == 128, "W is 128 bytes");

/* v4 (BUILD 605): world-map frame timing. worldfar stamps the hardware
   counter at the window's first terrain transform and around its own far
   field (its State, 48 bytes before the Highwind block); the flip is here. */
typedef struct {                  /* worldfar's perf block (606b: 64 bytes) */
    u64 bend0, draw0, draw1;
    u64 t_proj, t_sub;            /* running totals: the game's window projection / submit */
    u64 t_terr, t_pad;            /* v6: ... and the world render's terrain draw loops */
    u32 frame, tris, draws, armed;
    float zoom;
    u32 own;                      /* the NEXT frame's window is worldfar's */
} PF;
typedef struct {                  /* one world frame, 48 bytes (us) */
    u32 interval, pre, win, far, post;
    u32 proj, sub;                /* v5: inside `win`, the game's projection and submit */
    u32 terr;                     /* v6: inside `post`, the terrain objects' draw */
    u32 tris;
    u16 draws, zoom;
    s16 alt;                      /* the player's y / 2 */
    u16 view;                     /* view type | (window ours) << 8 */
    u32 pad;
} F;
_Static_assert(sizeof(F) == 48, "F is 48 bytes");
typedef struct {                  /* the TLS dump, 256 bytes */
    u32 frames;                   /* world frames measured */
    u32 own;                      /* ... of them with worldfar's window */
    u32 over[5];                  /* frames longer than 16.9 / 18 / 20 / 25 / 33.4 ms */
    u32 pad0;
    u64 sum[8];                   /* us: interval, pre, window, far, post, proj, sub, terr */
    u32 max[8];
    u32 tris_max, pad2;
    u64 tris_sum;
    F worst[2];                   /* the longest frames, longest first */
    u32 pad1[4];
} P;
_Static_assert(sizeof(P) == 256, "P is the 256-byte TLS dump");

typedef struct {
    u32 magic, key, seq, r3_prev;
    u32 n;                        /* visits kept (ring) */
    u32 head;                     /* next slot */
    u32 total_err, total_oom;
    V cur;
    V ring[NV];
    W wcur, hist[2];              /* hist[1] the last finished world visit */
    P perf;                       /* v4 */
    u64 prev_flip;
    u64 prev_proj, prev_sub;      /* v5: the caves' totals at the last flip */
    u64 prev_terr;
    u32 own_now;                  /* this frame's window was worldfar's */
    u32 pad5;
    u32 pf_seen, wframes;         /* worldfar's frame count last seen; frames this world visit */
    u32 prev_world, pad4;
    /* v7: the GL device layer's vtable methods, timed by leakprobe.py's
       caves (each adds its ticks and a call to gacc[slot]). World frames
       add their share to gw_*; gacc is the LAST member (the caves' address). */
    u64 gsnap_t[NG];
    u32 gsnap_c[NG];
    u64 gw_t[NG];
    u32 gw_c[NG];
    u32 gframes, gpad;
    u64 gacc[NG][4];              /* ticks, calls, the cave's saved x30 and start tick */
} State;
/* v7b: leakprobe.py writes the caves' counters at state + sizeof(State) - NG*32,
   so gacc must really be the last member (v7 had 24 bytes after it). */
_Static_assert(__builtin_offsetof(State, gacc) + sizeof(((State *)0)->gacc) == sizeof(State),
               "gacc must be the last member of State");

static u32 rd32(g2h_fn g2h, u32 g)
{
    if (!g) return 0;
    if ((g & 0xFFFu) <= 0xFFCu) {
        const u8 *p = g2h(g);
        if (!p) return 0;
        return (u32)p[0] | ((u32)p[1] << 8) | ((u32)p[2] << 16) | ((u32)p[3] << 24);
    }
    u32 v = 0;
    for (int i = 0; i < 4; i++) { const u8 *p = g2h(g + i); v |= (u32)(p ? *p : 0) << (8 * i); }
    return v;
}

static u32 mode_of(u32 *mode_slot)
{
    if (!mode_slot) return 0;
    u32 *m = *(u32 **)(void *)mode_slot;
    return m ? *m : 0;
}

static void textures(u64 **rvar, u32 *live, u32 *parked)
{
    *live = 0; *parked = 0;
    u64 *mgr = rvar ? *rvar : 0;
    if (!mgr) return;
    for (u32 i = 0; i < MGR_NTEX; i++) *live += mgr[i] != 0;
    *parked = (u32)*(u64 *)(void *)((u8 *)mgr + MGR_PARKED);
}

/* free bytes, largest free block, free blocks: the HeapAlloc free list */
static void heap(g2h_fn g2h, u32 *freeb, u32 *largest, u32 *nfree)
{
    *freeb = 0; *largest = 0; *nfree = 0;
    u32 p = rd32(g2h, HEAP_DESC + 8);
    for (u32 guard = 0; p && guard < 200000; guard++) {
        u32 h = p - BLK_HDR;
        u32 size = rd32(g2h, h + BLK_SIZE);
        if (!rd32(g2h, h + BLK_USED)) {
            *freeb += size;
            if (size > *largest) *largest = size;
            (*nfree)++;
        }
        u32 nx = rd32(g2h, h + BLK_NEXT);
        if (nx == p) break;
        p = nx;
    }
}

static void sample(State *st, g2h_fn g2h, u64 **rvar, int end)
{
    u32 live, parked;
    textures(rvar, &live, &parked);
    if (live > st->cur.tex_peak) st->cur.tex_peak = (u16)live;
    st->cur.tex_end = (u16)live;
    st->cur.parked = (u16)parked;
    u32 fb, lg, nf;
    heap(g2h, &fb, &lg, &nf);
    st->cur.free_end = fb;
    (void)lg; (void)nf; (void)end;
}

static void begin(State *st, g2h_fn g2h, u64 **rvar, u32 key)
{
    u32 *z = (u32 *)(void *)&st->cur;
    for (u32 i = 0; i < sizeof(V) / 4; i++) z[i] = 0;
    st->cur.key = key;
    st->cur.seq = (u16)++st->seq;
    if ((key >> 16) == 4) {
        u32 *z2 = (u32 *)(void *)&st->wcur;
        for (u32 i = 0; i < sizeof(W) / 4; i++) z2[i] = 0;
    }
    u32 live, parked;
    textures(rvar, &live, &parked);
    st->cur.tex_start = (u16)live;
    sample(st, g2h, rvar, 0);
}

static void copyv(V *d, const V *s)
{
    u32 *dd = (u32 *)(void *)d;
    const u32 *ss = (const u32 *)(const void *)s;
    for (u32 i = 0; i < sizeof(V) / 4; i++) dd[i] = ss[i];
}

static s16 c16(s32 v) { return (s16)(v > 32767 ? 32767 : v < -32768 ? -32768 : v); }

static void world_frame(State *st, g2h_fn g2h)
{
    u32 ent = rd32(g2h, G_PLAYER_ENT);
    if (!ent) return;
    s32 x = (s32)rd32(g2h, ent + 0xC), y = (s32)rd32(g2h, ent + 0x10);
    s32 z = (s32)rd32(g2h, ent + 0x14);
    u32 view = rd32(g2h, G_VIEW_TYPE), ctl = rd32(g2h, G_CONTROL) != 0;
    W *w = &st->wcur;
    u32 f = w->frames++;                 /* 0-based */
    if (f == 0) {
        w->x_first = x; w->z_first = z; w->y_max = y;
        w->takeoff = c16((s32)rd32(g2h, G_HW_TAKEOFF));
    }
    if (f < 16) {
        w->y_first[f] = c16(y);
        if (ctl) w->ctl_first |= (u16)(1u << f);
        if (view == 3) w->hw_first |= (u16)(1u << f);
    }
    int si = -1;                         /* frames 24, 32, 48, 64, 96, 128, 192, 256 */
    switch (f) {
    case 23: si = 0; break;  case 31: si = 1; break;  case 47: si = 2; break;
    case 63: si = 3; break;  case 95: si = 4; break;  case 127: si = 5; break;
    case 191: si = 6; break; case 255: si = 7; break; default: break;
    }
    if (si >= 0) w->y_sparse[si] = c16(y);
    u32 r = w->ring & 15;
    w->y_last[r] = c16(y);
    if (ctl) w->ctl_last |= (u16)(1u << r); else w->ctl_last &= (u16)~(1u << r);
    if (view == 3) w->hw_last |= (u16)(1u << r); else w->hw_last &= (u16)~(1u << r);
    w->ring++;
    if (y > w->y_max) w->y_max = y;
    if (ctl && view == 3) w->y_last_ctl = y;
    w->x_last = x; w->z_last = z;
}

/* the ring in time order, oldest first (the bits too) */
static void order_ring(W *w)
{
    s16 y[16]; u16 c = 0, h = 0;
    u32 n = w->ring < 16 ? w->ring : 16, start = w->ring - n;
    for (u32 i = 0; i < n; i++) {
        u32 r = (start + i) & 15;
        y[i] = w->y_last[r];
        c |= (u16)(((w->ctl_last >> r) & 1u) << i);
        h |= (u16)(((w->hw_last >> r) & 1u) << i);
    }
    for (u32 i = n; i < 16; i++) y[i] = 0;
    for (u32 i = 0; i < 16; i++) w->y_last[i] = y[i];
    w->ctl_last = c; w->hw_last = h; w->ring = n;
}

static void finish(State *st)
{
    if ((st->cur.key >> 16) == 4) {
        order_ring(&st->wcur);
        u32 *d0 = (u32 *)(void *)&st->hist[0], *d1 = (u32 *)(void *)&st->hist[1];
        const u32 *s = (const u32 *)(const void *)&st->wcur;
        for (u32 i = 0; i < sizeof(W) / 4; i++) { d0[i] = d1[i]; d1[i] = s[i]; }
    }
    copyv(&st->ring[st->head], &st->cur);
    st->head = (st->head + 1) % NV;
    if (st->n < NV) st->n++;
}

static u8 *input_obj(u8 **got)
{
    if (!got) return 0;
    u8 *p = *got;
    if (!p) return 0;
    p = *(u8 **)(void *)(p + 0);
    if (!p) return 0;
    p = *(u8 **)(void *)(p + 8);
    if (!p) return 0;
    p = *(u8 **)(void *)(p + 0);
    if (!p) return 0;
    return *(u8 **)(void *)(p + 0x88);
}

/* the last 8 visits, oldest first, the current (partial) one last; and the
   two last world visits' Highwind traces (the one before, then the last) */
static void collect(State *st, g2h_fn g2h, u64 **rvar, V *out, W *wout, u64 *regs, u32 *hwblk)
{
    if ((st->cur.key >> 16) == 4) {
        W tmp;
        u32 *d = (u32 *)(void *)&tmp; const u32 *s = (const u32 *)(const void *)&st->wcur;
        for (u32 i = 0; i < sizeof(W) / 4; i++) d[i] = s[i];
        order_ring(&tmp);
        s = (const u32 *)(const void *)&st->hist[1];
        d = (u32 *)(void *)&wout[0];
        for (u32 i = 0; i < sizeof(W) / 4; i++) d[i] = s[i];
        s = (const u32 *)(const void *)&tmp; d = (u32 *)(void *)&wout[1];
        for (u32 i = 0; i < sizeof(W) / 4; i++) d[i] = s[i];
    } else {
        for (int k = 0; k < 2; k++) {
            const u32 *s = (const u32 *)(const void *)&st->hist[k];
            u32 *d = (u32 *)(void *)&wout[k];
            for (u32 i = 0; i < sizeof(W) / 4; i++) d[i] = s[i];
        }
    }
    sample(st, g2h, rvar, 1);
    u32 k = 0;
    u32 keep = st->n < NV - 1 ? st->n : NV - 1;
    for (u32 i = 0; i < keep; i++) {
        u32 idx = (st->head + NV - keep + i) % NV;
        copyv(&out[k++], &st->ring[idx]);
    }
    copyv(&out[k++], &st->cur);
    for (; k < NV; k++) { u32 *z = (u32 *)(void *)&out[k]; for (u32 i = 0; i < 8; i++) z[i] = 0; }
    u32 live, parked;
    textures(rvar, &live, &parked);
    regs[0] = ((u64)MAGIC << 32) | st->cur.key;
    regs[1] = ((u64)st->seq << 32) | st->n;
    regs[2] = ((u64)live << 32) | parked;
    regs[3] = ((u64)st->total_err << 32) | st->total_oom;
    regs[4] = ((u64)rd32(g2h, HEAP_DESC) << 32) | rd32(g2h, HEAP_DESC + 4);
    /* the module's own altitude-restore state (worldfar State tail, 603):
       hw_ok, hw_x, hw_y, hw_z, hw_pending, hw_off, hw_lost, hw_restores */
    u32 *hw = hwblk;
    regs[5] = hw ? ((u64)hw[0] << 56 | (u64)(hw[6] & 0xFF) << 48 | (u64)(hw[4] & 0xFFFF) << 32 | hw[2]) : 0;
    regs[6] = hw ? ((u64)hw[1] << 32 | hw[3]) : 0;
    regs[7] = hw ? ((u64)hw[7] << 32 | hw[5]) : 0;
    regs[8] = (u64)(u32)(st->cur.key >> 16 == 4) << 32 | st->n;   /* -> FP */
    regs[9] = (u64)(uintptr_t)hw;                                  /* -> LR */
}

__attribute__((noreturn)) static void dump(State *st, g2h_fn g2h, u64 **rvar, u32 *hwblk)
{
    V out[NV] __attribute__((aligned(16)));
    W wout[2] __attribute__((aligned(16)));
    u64 regs[10];
    collect(st, g2h, rvar, out, wout, regs, hwblk);
    {                               /* v7: the stack dump is the 32 costliest GL methods */
        u32 *o = (u32 *)(void *)out;
        u32 taken[NG / 32];
        for (u32 k = 0; k < NG / 32; k++) taken[k] = 0;
        for (u32 k = 0; k < 32; k++) {
            u32 best = NG;
            for (u32 i = 0; i < NG; i++) {
                if (taken[i >> 5] & (1u << (i & 31))) continue;
                if (best == NG || st->gw_t[i] > st->gw_t[best]) best = i;
            }
            taken[best >> 5] |= 1u << (best & 31);
            u64 us = st->gw_t[best] * 5u / 96u;
            u32 cpf = st->gframes ? st->gw_c[best] / st->gframes : 0;
            o[2 * k] = best | ((cpf > 0xFFFF ? 0xFFFF : cpf) << 16);
            o[2 * k + 1] = (u32)(us > 0xFFFFFFFFull ? 0xFFFFFFFFu : us);
        }
        regs[4] = ((u64)st->gframes << 32) | NG;
    }
    u8 *tls;
    __asm__ volatile("mrs %0, tpidrro_el0" : "=r"(tls));
    {                               /* v4: the TLS dump is the frame timing */
        u64 *d = (u64 *)(void *)tls;
        const u64 *s = (const u64 *)(const void *)&st->perf;
        for (int i = 0; i < 32; i++) d[i] = s[i];
    }
    __asm__ volatile(
        "mov x16, %0\n"
        "mov x17, %1\n"
        "ldp x0, x1, [x16, #0]\n"
        "ldp x2, x3, [x16, #16]\n"
        "ldp x4, x5, [x16, #32]\n"
        "ldp x6, x7, [x16, #48]\n"
        "ldp x29, x30, [x16, #64]\n"
        "mov sp, x17\n"
        "svc #0x26\n"
        "udf #0\n"
        : : "r"(regs), "r"(out) : "memory");
    __builtin_unreachable();
}

static u64 counter(void)
{
    u64 t;
#if defined(__aarch64__) && !defined(LP_HOST)
    __asm__ volatile("mrs %0, cntpct_el0" : "=r"(t));
#else
    t = lp_host_ticks();
#endif
    return t;
}

static void copyf(F *d, const F *s)
{
    u32 *dd = (u32 *)(void *)d;
    const u32 *ss = (const u32 *)(const void *)s;
    for (u32 i = 0; i < sizeof(F) / 4; i++) dd[i] = ss[i];
}

static u32 us(u64 a, u64 b) { return b > a ? (u32)((b - a) * 5u / 96u) : 0u; }  /* 19.2 MHz */

static void perf_frame(State *st, g2h_fn g2h, const PF *pf, u64 now, u32 world)
{
    u32 fresh = world && pf && pf->frame != st->pf_seen;
    if (pf) st->pf_seen = pf->frame;
    u64 prev = st->prev_flip;
    u32 was = st->prev_world;
    st->prev_flip = now;
    st->prev_world = fresh;
    u64 dproj = pf ? pf->t_proj - st->prev_proj : 0, dsub = pf ? pf->t_sub - st->prev_sub : 0;
    u64 dterr = pf ? pf->t_terr - st->prev_terr : 0;
    if (pf) { st->prev_proj = pf->t_proj; st->prev_sub = pf->t_sub; st->prev_terr = pf->t_terr; }
    u32 own_this = st->own_now;       /* decided at the end of the last world frame */
    st->own_now = pf ? pf->own : 0;
    if (!fresh) { st->wframes = 0; return; }
    if (++st->wframes <= 30 || !was || !prev) return;   /* the load and fade-in */
    F f;
    f.interval = us(prev, now);
    if (pf->draw0 < prev || pf->draw1 < pf->draw0 || now < pf->draw1) return;
    u64 b0 = (pf->bend0 >= prev && pf->bend0 <= pf->draw0) ? pf->bend0 : pf->draw0;
    f.pre = us(prev, b0);
    f.win = us(b0, pf->draw0);
    f.far = us(pf->draw0, pf->draw1);
    f.post = us(pf->draw1, now);
    f.proj = (u32)(dproj * 5u / 96u);
    f.sub = (u32)(dsub * 5u / 96u);
    f.terr = (u32)(dterr * 5u / 96u);
    f.pad = 0;
    f.tris = pf->tris;
    f.draws = (u16)(pf->draws > 0xFFFF ? 0xFFFF : pf->draws);
    f.zoom = (u16)(pf->zoom > 65535.0f ? 65535.0f : pf->zoom < 0.0f ? 0.0f : pf->zoom);
    u32 ent = rd32(g2h, G_PLAYER_ENT);
    f.alt = ent ? c16((s32)rd32(g2h, ent + 0x10) / 2) : 0;
    f.view = (u16)((rd32(g2h, G_VIEW_TYPE) & 0xFF) | (own_this ? 0x100 : 0));
    P *p = &st->perf;
    p->frames++;
    if (own_this) p->own++;
    if (f.interval > 16900) p->over[0]++;
    if (f.interval > 18000) p->over[1]++;
    if (f.interval > 20000) p->over[2]++;
    if (f.interval > 25000) p->over[3]++;
    if (f.interval > 33400) p->over[4]++;
    u32 v[8];
    v[0] = f.interval; v[1] = f.pre; v[2] = f.win; v[3] = f.far; v[4] = f.post;
    v[5] = f.proj; v[6] = f.sub; v[7] = f.terr;
    for (int i = 0; i < 8; i++) {
        p->sum[i] += v[i];
        if (v[i] > p->max[i]) p->max[i] = v[i];
    }
    p->tris_sum += f.tris;
    if (f.tris > p->tris_max) p->tris_max = f.tris;
    int k = 2;
    while (k > 0 && p->worst[k - 1].interval < f.interval) k--;
    if (k < 2) {
        for (int j = 1; j > k; j--) copyf(&p->worst[j], &p->worst[j - 1]);
        copyf(&p->worst[k], &f);
    }
}

__attribute__((section(".text.lp_frame")))
void lp_frame(State *st, g2h_fn g2h, u8 **got, u32 *mode_slot, u64 **rvar,
              glerr_fn glerr, u32 *hwblk, const PF *pfblk)
{
    u64 now = counter();
    u32 mode = mode_of(mode_slot);
    u32 key = (mode << 16) | (rd32(g2h, G_FIELD_ID) & 0xFFFFu);
    if (st->magic != MAGIC) {
        u32 *z = (u32 *)(void *)st;
        for (u32 i = 0; i < sizeof(State) / 4; i++) z[i] = 0;
        st->magic = MAGIC;
        st->key = key;
        begin(st, g2h, rvar, key);
    }
    if (key != st->key) {
        /* no fresh sample here: the next scene may already be loading, and
           its textures must not be charged to the visit that just ended */
        finish(st);
        st->key = key;
        begin(st, g2h, rvar, key);
    }
    st->cur.frames++;
    if (mode == 4) world_frame(st, g2h);
    perf_frame(st, g2h, pfblk, now, mode == 4);
    if (mode == 4) st->gframes++;
    for (u32 i = 0; i < NG; i++) {
        u64 t = st->gacc[i][0];
        u32 c = (u32)st->gacc[i][1];
        if (mode == 4) {
            st->gw_t[i] += t - st->gsnap_t[i];
            st->gw_c[i] += c - st->gsnap_c[i];
        }
        st->gsnap_t[i] = t;
        st->gsnap_c[i] = c;
    }
    /* v2: drain GL's error flags once a frame (nothing else reads them after
       texture or buffer calls). GL_OUT_OF_MEMORY is the graphics pool. */
    if (glerr) {
        u32 any = 0;
        for (int i = 0; i < 8; i++) {
            u32 e = glerr();
            if (!e) break;
            any = 1;
            if (st->cur.nerr != 0xFFFF) st->cur.nerr++;
            st->total_err++;
            if (e == GL_OOM) { if (st->cur.noom != 0xFFFF) st->cur.noom++; st->total_oom++; }
            if (!st->cur.first) st->cur.first = (u16)e;
            st->cur.last = (u16)e;
        }
        if (any && st->cur.errframes != 0xFFFF) st->cur.errframes++;
    }
    /* v4: no heap walk on the world map -- it would land in the timing */
    if ((st->cur.frames & (SAMPLE - 1)) == 0 && mode != 4) sample(st, g2h, rvar, 0);
    else {
        u32 live, parked;
        textures(rvar, &live, &parked);
        if (live > st->cur.tex_peak) st->cur.tex_peak = (u16)live;
        st->cur.tex_end = (u16)live;
        st->cur.parked = (u16)parked;
    }
    u8 *p = input_obj(got);
    u32 r3 = (p && (*(unsigned long long *)(void *)(p + 0x20) & BTN_R3)) ? 1u : 0u;
    u32 click = r3 && !st->r3_prev;
    st->r3_prev = r3;
    if (!click || mode == DRIVER_WORLD) return;
    dump(st, g2h, rvar, hwblk);
}

#ifdef LP_HOST
/* for the host test: the same collection without the svcBreak */
void lp_collect(State *st, g2h_fn g2h, u64 **rvar, V *out, W *wout, u64 *regs, u32 *hwblk)
{
    collect(st, g2h, rvar, out, wout, regs, hwblk);
}
#endif
