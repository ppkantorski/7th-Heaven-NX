/*
 * sfxpre.c -- BUILD 665: a field's sound effects are decoded while the field
 * is still black, not the first time a script plays one. See
 * ff7nx_sfxpreload.py for the whole design; this is its native half.
 *
 * Measured (PROBE-664): the one bad frame in mds7plr1 (27.4 ms) spent 16.5 ms
 * inside sfx_load (x86 0x745CF3) -- the first play of a sound reading it out
 * of audio.dat and converting it.
 *
 * sp_plan   first field-loop pass of a new field: walk the field's script
 *           (section 1; the code is contiguous from the entity table to the
 *           string table, so a linear walk is exact), collect every LITERAL
 *           sound id it can play --
 *              SE    0xF1  bank byte 0, id u16 at +2
 *              AKAO  0xF2  bank byte 0, command 0x20..0x2F at +4, id u16 at +6
 *              AKAO2 0xDA  bank byte 0, command 0x20..0x2F at +4, id u16 at +7
 *           -- and list the work: release the decoded buffers kept for the
 *           previous field that this one does not use, and load each new id
 *           once per variant (a rotating Cosmo route has several; the
 *           sequential-SFX bridge picks the next one on every sfx_load).
 *           BUILD 665e: plus the field's FOOTSTEPS. ff7nx_fieldsteps plays them
 *           through reserved row 744, rewriting its descriptor to one of
 *           Cosmo's appended variants and clearing its cache word on every
 *           step -- so every step was a disk read, and the FIRST read of each
 *           variant took 3..17 ms (665d, measured: those were the stutters).
 *           The field's CMFS trailer lists the variants its walkmesh uses;
 *           each one is loaded once, keyed (744, offset) like any other, and
 *           the stock row 159 (what a field without a route steps on) too.
 * sp_prep   before load i: a footstep variant sets row 744's descriptor to
 *           (stride, offset) and clears its cache word, as fieldsteps does.
 * sp_take   after each of those sfx_load(id, 0) calls: move the buffer out of
 *           the game's cache word into our table, keyed by (row, audio.dat
 *           offset of the variant that was loaded), and clear the cache word
 *           again -- the game's cache semantics are never changed. Returns a
 *           buffer the stub must release (a variant already held), or 0.
 * sp_find   inside sfx_load, on its cache-miss path (after the sequential
 *           bridge chose the variant): the buffer kept for (row, offset), or 0.
 * sp_store  ...and when there was one, the stub has duplicated it (the stock
 *           DuplicateSoundBuffer wrapper, x86 0x74A5B7, the same call
 *           play_sfx_on_channel makes for a cached row); store the copy where
 *           sfx_load would have stored a fresh load and set its result. Only
 *           called with a non-zero duplicate.
 *
 * Position independent, no data sections; each entry is its own section and
 * carries its own guest readers (a branch inside one section needs no
 * relocation). The opcode sizes and the per-id variant counts come in
 * through `tab` (built by the Python side).
 */
#include <stdint.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32;

#define FIELD_ID    0xCFF468u   /* guest u16: maplist index of the field */
#define LEVEL_PTR   0xCFF594u   /* guest u32: the decompressed field file */
#define META        0xDE0E84u   /* guest: 750 x 0x1C, +0 length, +4 offset */
#define CACHE       0xDE0128u   /* guest: 750 x u32 cached buffers */
#define N_SLOTS     750
#define MAX_ENT     48

/* tab layout */
#define T_OP        0           /* 256 x u8: instruction size, 0 = unknown */
#define T_SPECIAL   256         /* 16 x u8: SPECIAL (0x0F) sizes, sub-op 0xF0.. */
#define T_VAR       272         /* 376 x u8: a nibble per id: variants, 0 = never */
#define T_CAP       648         /* u32: largest payload loaded (bytes) */
#define T_BUDGET    652         /* u32: payload bytes per field, all variants */
#define T_FOOT_PB   656         /* u32: fieldsteps' appended payload base, 0 = none */
#define T_FOOT_STR  660         /* u32: ... and its stride (= descriptor length) */
#define FOOT_SLOT   743         /* row 744 */
#define STOCK_STEP  159
#define CMFS_MAGIC  0x53464D43u
#define FINA_MAGIC  0x414E4946u

typedef struct {
    u32 last_ptr, last_id;
    u32 n_rel, n_load;
    u32 n_ent, plans, takes, hits;
    u32 rel_buf[MAX_ENT];
    u32 load_id[MAX_ENT];
    u32 ent_slot[MAX_ENT];
    u32 ent_off[MAX_ENT];
    u32 ent_buf[MAX_ENT];
    u32 load_off[MAX_ENT];          /* 665e: footstep variant offset, 0 = none */
} State;

_Static_assert(sizeof(State) == 0x4A0, "STATE layout (ff7nx_sfxpreload)");

/* guest memory through the port's page table: consecutive guest pages are
 * not consecutive host pages, so every byte is looked up */
#define READERS(sec)                                                         \
__attribute__((noinline, section(".text." #sec)))                            \
static u32 sec##_rd8(u8 **pages, u32 g)                                      \
{                                                                            \
    u8 *p = pages[g >> 12];                                                  \
    return p ? p[g & 0xFFFu] : 0;                                            \
}                                                                            \
__attribute__((noinline, section(".text." #sec)))                            \
static u32 sec##_rd32(u8 **pages, u32 g)                                     \
{                                                                            \
    return sec##_rd8(pages, g) | sec##_rd8(pages, g + 1) << 8 |              \
           sec##_rd8(pages, g + 2) << 16 | sec##_rd8(pages, g + 3) << 24;    \
}

#define WR32(sec, pages, g, v) do {                                          \
    for (u32 _i = 0; _i < 4; _i++) {                                         \
        u8 *_p = (pages)[((g) + _i) >> 12];                                  \
        if (_p) _p[((g) + _i) & 0xFFFu] = (u8)((v) >> (8 * _i));             \
    }                                                                        \
} while (0)

READERS(sp_plan)
READERS(sp_prep)
READERS(sp_take)
READERS(sp_find)
READERS(sp_store)

#define rd16(p, g) (sp_plan_rd8((p), (g)) | sp_plan_rd8((p), (g) + 1) << 8)
#define TAB32(t, o) ((t)[o] | (t)[(o) + 1] << 8 | (t)[(o) + 2] << 16 | (u32)(t)[(o) + 3] << 24)

static inline __attribute__((always_inline)) int has(const u32 *v, u32 n, u32 x)
{
    for (u32 i = 0; i < n; i++)
        if (v[i] == x)
            return 1;
    return 0;
}

__attribute__((section(".text.sp_plan")))
u32 sp_plan(State *st, u8 **pages, const u8 *tab)
{
    u32 lp = sp_plan_rd32(pages, LEVEL_PTR);
    u32 fid = rd16(pages, FIELD_ID);
    if (!lp || (lp == st->last_ptr && fid == st->last_id))
        return 0;
    st->last_ptr = lp;
    st->last_id = fid;
    st->n_rel = st->n_load = 0;
    st->plans++;

    u32 off = sp_plan_rd32(pages, lp + 6);
    u32 len = sp_plan_rd32(pages, lp + off);
    u32 s1 = lp + off + 4;
    u32 ids[MAX_ENT];
    u32 n = 0;
    if (len >= 32 && len < 0x40000) {
        u32 ne = sp_plan_rd8(pages, s1 + 2);
        u32 lim = rd16(pages, s1 + 4);
        u32 na = rd16(pages, s1 + 6);
        u32 o = 32 + 8 * ne + 4 * na + 64 * ne;
        u32 cap = tab[T_CAP] | tab[T_CAP + 1] << 8 | tab[T_CAP + 2] << 16 |
                  (u32)tab[T_CAP + 3] << 24;
        if (lim > len)
            lim = len;
        while (o < lim && n < MAX_ENT) {
            u32 op = sp_plan_rd8(pages, s1 + o);
            u32 sz = tab[T_OP + op];
            if (op == 0x0F) {
                u32 sub = sp_plan_rd8(pages, s1 + o + 1);
                sz = sub >= 0xF0 ? tab[T_SPECIAL + sub - 0xF0] : 0;
            }
            else if (op == 0x28)
                sz = sp_plan_rd8(pages, s1 + o + 1);
            if (!sz)
                break;
            u32 id = 0;
            if (op == 0xF1 && sp_plan_rd8(pages, s1 + o + 1) == 0) {
                id = rd16(pages, s1 + o + 2);
            } else if ((op == 0xF2 || op == 0xDA) &&
                       sp_plan_rd8(pages, s1 + o + 1) == 0) {
                u32 c = sp_plan_rd8(pages, s1 + o + 4);
                if (c >= 0x20 && c <= 0x2F)
                    id = rd16(pages, s1 + o + (op == 0xF2 ? 6 : 7));
            }
            if (id >= 1 && id <= N_SLOTS &&
                ((tab[T_VAR + (id >> 1)] >> (4 * (id & 1))) & 0xF) &&
                !has(ids, n, id)) {
                u32 row = META + (id - 1) * 0x1C;
                u32 plen = sp_plan_rd32(pages, row);
                /* +8: the loop flag. Loops are the ambient beds that start
                 * with the field anyway, and the long ones are what fills
                 * the sound pool (ff7nx_audiopool). */
                if (plen && plen <= cap && !sp_plan_rd8(pages, row + 8))
                    ids[n++] = id;
            }
            o += sz;
        }
    }

    /* the stock footstep row, which any field may step on */
    if (n < MAX_ENT && !has(ids, n, STOCK_STEP) &&
        ((tab[T_VAR + (STOCK_STEP >> 1)] >> (4 * (STOCK_STEP & 1))) & 0xF)) {
        u32 pl = sp_plan_rd32(pages, META + (STOCK_STEP - 1) * 0x1C);
        if (pl && pl <= TAB32(tab, T_CAP))
            ids[n++] = STOCK_STEP;
    }

    /* footstep variants this field's CMFS trailer can select */
    u32 foot[MAX_ENT];
    u32 nf = 0;
    u32 fpb = TAB32(tab, T_FOOT_PB), fstr = TAB32(tab, T_FOOT_STR);
    if (fpb && fstr) {
        u32 o9 = sp_plan_rd32(pages, lp + 6 + 8 * 4);
        u32 h = lp + o9 + 4 + sp_plan_rd32(pages, lp + o9);
        u32 m = sp_plan_rd32(pages, h);
        if (m == FINA_MAGIC) {
            h += 14;
            m = sp_plan_rd32(pages, h);
        }
        if (m == CMFS_MAGIC) {
            u32 span = rd16(pages, h + 4);
            u32 seen_b[16], seen_c[16], ns = 0;
            for (u32 t = 0; t <= span && span < 0x4000; t++) {
                u32 r = t == span ? h + 6 : h + 9 + 3 * t;   /* last: fallback */
                u32 b = sp_plan_rd8(pages, r), c = sp_plan_rd8(pages, r + 1);
                if (b == 0xFF || !c)
                    continue;
                u32 dup = 0;
                for (u32 k = 0; k < ns; k++)
                    if (seen_b[k] == b && seen_c[k] == c)
                        dup = 1;
                if (dup || ns >= 16)
                    continue;
                seen_b[ns] = b;
                seen_c[ns++] = c;
                for (u32 j = 0; j < c && nf < MAX_ENT; j++) {
                    u32 off = fpb + (b + j) * fstr;
                    if (!has(foot, nf, off))
                        foot[nf++] = off;
                }
            }
        }
    }

    u32 keep = 0;
    for (u32 i = 0; i < st->n_ent; i++) {
        u32 sl = st->ent_slot[i];
        int want = sl == FOOT_SLOT ? has(foot, nf, st->ent_off[i])
                                   : has(ids, n, sl + 1);
        if (want) {
            st->ent_slot[keep] = sl;
            st->ent_off[keep] = st->ent_off[i];
            st->ent_buf[keep] = st->ent_buf[i];
            keep++;
        } else {
            st->rel_buf[st->n_rel++] = st->ent_buf[i];
        }
    }
    st->n_ent = keep;
    u32 room = MAX_ENT - keep;
    u32 budget = TAB32(tab, T_BUDGET);
    for (u32 i = 0; i < keep; i++) {
        u32 pl = st->ent_slot[i] == FOOT_SLOT ? fstr
               : sp_plan_rd32(pages, META + st->ent_slot[i] * 0x1C);
        budget = pl < budget ? budget - pl : 0;
    }
    /* footsteps first: they play on every step */
    for (u32 i = 0; i < nf && room; i++) {
        int held = 0;
        for (u32 k = 0; k < keep; k++)
            if (st->ent_slot[k] == FOOT_SLOT && st->ent_off[k] == foot[i])
                held = 1;
        if (held || fstr > budget)
            continue;
        st->load_id[st->n_load] = FOOT_SLOT + 1;
        st->load_off[st->n_load++] = foot[i];
        room--;
        budget -= fstr;
    }
    for (u32 i = 0; i < n; i++) {
        u32 id = ids[i];
        if (has(st->ent_slot, keep, id - 1))
            continue;                           /* kept from the last field */
        if (sp_plan_rd32(pages, CACHE + (id - 1) * 4))
            continue;                           /* the game holds it already */
        u32 k = (tab[T_VAR + (id >> 1)] >> (4 * (id & 1))) & 0xF;
        u32 cost = k * sp_plan_rd32(pages, META + (id - 1) * 0x1C);
        if (k > room || cost > budget)
            continue;
        for (u32 j = 0; j < k; j++) {
            st->load_id[st->n_load] = id;
            st->load_off[st->n_load++] = 0;
        }
        room -= k;
        budget -= cost;
    }
    return st->n_rel + st->n_load;
}

__attribute__((section(".text.sp_prep")))
void sp_prep(State *st, u8 **pages, const u8 *tab, u32 i)
{
    u32 off = st->load_off[i];
    if (!off)
        return;
    u32 row = META + FOOT_SLOT * 0x1C;
    u32 str = TAB32(tab, T_FOOT_STR);
    WR32(sp_prep, pages, row, str);
    WR32(sp_prep, pages, row + 4, off);
    WR32(sp_prep, pages, CACHE + FOOT_SLOT * 4, 0u);
    (void)sp_prep_rd32;
}

__attribute__((section(".text.sp_take")))
u32 sp_take(State *st, u8 **pages, u32 id)
{
    u32 slot = id - 1;
    u32 buf = sp_take_rd32(pages, CACHE + slot * 4);
    if (!buf)
        return 0;
    WR32(sp_take, pages, CACHE + slot * 4, 0u);   /* the cache stays as it was */
    u32 off = sp_take_rd32(pages, META + slot * 0x1C + 4);
    for (u32 i = 0; i < st->n_ent; i++)
        if (st->ent_slot[i] == slot && st->ent_off[i] == off)
            return buf;                     /* same variant twice: release */
    if (st->n_ent >= MAX_ENT)
        return buf;
    st->ent_slot[st->n_ent] = slot;
    st->ent_off[st->n_ent] = off;
    st->ent_buf[st->n_ent] = buf;
    st->n_ent++;
    st->takes++;
    return 0;
}

__attribute__((section(".text.sp_find")))
u32 sp_find(State *st, u8 **pages, const u32 *ctx)
{
    if (!st->n_ent)
        return 0;
    u32 slot = sp_find_rd32(pages, ctx[5] - 4);       /* [ebp-4] */
    if (slot >= N_SLOTS)
        return 0;
    u32 off = sp_find_rd32(pages, META + slot * 0x1C + 4);
    for (u32 i = 0; i < st->n_ent; i++)
        if (st->ent_slot[i] == slot && st->ent_off[i] == off) {
            st->hits++;
            return st->ent_buf[i];
        }
    return 0;
}

__attribute__((section(".text.sp_store")))
void sp_store(State *st, u8 **pages, u32 *ctx)
{
    (void)st;
    u32 d = ctx[0];                                   /* eax: the duplicate */
    u32 ebp = ctx[5];
    u32 slot = sp_store_rd32(pages, ebp - 4);
    u32 out = sp_store_rd32(pages, ebp + 12);
    u32 tgt = out ? out : CACHE + slot * 4;
    WR32(sp_store, pages, tgt, d);
    ctx[0] = 1;
}
