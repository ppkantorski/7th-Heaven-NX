/*
 * shadows.c -- model shadows in battle (BUILD 645) and on the field (646).
 * See ff7nx_shadows.py.
 *
 * Current state (646l): one flattened second draw per model part, onto the
 * floor, along FFNx's default light (60/60, fixed in the world); battle
 * keeps 0.50 of the floor (blend mode 5), the field 0.40 (mode 6), each
 * pixel darkened once through the framebuffer alpha (645o); fast path 645q.
 * Battle floor = the round blob's matrix; field floor = the walkmesh under
 * the model (field_floor). The notes below are 645's battle design.
 *
 * The game's round battle shadow is a flat disc mesh it draws under every
 * actor, through the same native draw as everything else (gfx_drv_field_78
 * -> the draw helper +0x10D9D70). Its model matrix is therefore exactly the
 * floor under that actor, in the space the renderer multiplies the view and
 * projection onto (state +0x28 . +0x68 . +0xA8, row vectors).
 *
 * So, per actor:
 *   blob draw  (mode BLOB)   the helper call is skipped; its matrix is kept.
 *   model draw (mode ACTOR)  every part is drawn as usual, then once more
 *                            with its matrix followed by a projection onto
 *                            that floor plane, lifted a hair, in blend mode 5
 *                            (lmain_vv: black at SHADOW_ALPHA), alpha blended,
 *                            depth LESS + write so overlapping parts darken a
 *                            floor pixel once, culling off (the flattening
 *                            can turn faces over).
 * Every renderer field changed for that second draw is saved and restored
 * around it, so the port's own state cache sees exactly what it set.
 *
 * Built like texguard.c: position independent, no data sections; the state
 * block (Ctx) lives in BSS and is passed in by the stubs.
 */
#include <stdint.h>
typedef uint8_t u8; typedef uint32_t u32; typedef int32_t s32;
typedef uint64_t u64; typedef int64_t s64;

#define MODE_NONE   0u
#define MODE_BLOB   1u
#define MODE_ACTOR  2u

#define ACT_DRAW    0u      /* draw as usual */
#define ACT_SKIP    1u      /* the blob: keep its matrix, draw nothing */
#define ACT_SHADOW  2u      /* draw, then draw the shadow */
#define MIN_SHADOW_INDICES 12u  /* BUILD 645p: 4 triangles */

/* renderer state *(module+0x12CE668) */
#define RS_BLEND    0x0C    /* s32 blend mode -> lmain_vv blendMode.x */
#define RS_MODEL    0x28    /* float[16] model matrix */
#define RS_VIEW     0x68    /* float[16] */
#define RS_PROJ     0xA8    /* float[16]  (clip = p . model . view . proj) */
/* render-state block *(module+0x12CE548) */
#define RB_LO       0x740
#define RB_BYTES    0x38    /* 0x740 .. 0x777 */
#define RB_DWRITE   0x744
#define RB_DFUNC    0x748   /* index into {NEVER, LESS, EQUAL, LEQUAL, ...} */
#define RB_STENCIL  0x74C   /* stencil test on/off */
#define RB_SFUNC    0x750   /* index into {NEVER, LESS, EQUAL, ...}; ref 0, mask 0xFF */
#define RB_CMASK    0x740   /* low 4 bits: colour mask R, G, B, A */
/* blend factor / equation indices (GL device tables) */
#define BF_ZERO 0u
#define BF_ONE  1u
#define BF_1MDA 7u          /* ONE_MINUS_DST_ALPHA */
#define BF_DC   8u          /* DST_COLOR */
#define BE_ADD  0u
#define RB_CULL     0x764
#define RB_BSRC     0x76C   /* index into {ZERO, ONE, SRC_COLOR, 1-SRC_COLOR, SRC_ALPHA, 1-SRC_ALPHA, ...} */
#define RB_BEQ      0x770   /* index into {ADD, ...} */
#define RB_BDST     0x774

/* a draw_deferred entry (field_78 fills it, draw_deferred draws it) */
#define DE_HASMAT   0x10    /* u32: 1 -> the model matrix below is used */
#define DE_MATRIX   0x14    /* float[16] */
#define DE_SKIP     0x5ADu  /* our mark in DE_HASMAT: draw_deferred skips it */

#define SHADOW_BLEND 5
#define WS_SPHERE_MAGIC 0x575253  /* lmain_vv blendMode.w: world-map depth (591) */
#define MARK_BIT    0x100000u  /* BUILD 667d: lmain_vv blendMode.x: a marked field model part */
#define FIELD_BLEND  6      /* BUILD 646k: lmain_vv keeps FFNx's 0.30 of the floor */
#define TILT         K(0x3E99999Au)  /* 0.30: light slant along the floor's x axis */

/* a float constant without a literal pool (the blob has no data) */
static inline float K(u32 bits)
{
    float f;
    __asm__("fmov %s0, %w1" : "=w"(f) : "r"(bits));
    return f;
}

#define X86_EBP        0x14         /* the x86 context's ebp */
#define DENT_MAX    6       /* BUILD 667 (fits STATE_BYTES) */
/* BUILD 670: ff7nx_subcam's state (S_FLAG, S_T, S_TSET, S_DEF) */
#define CAM_FLAG    0x00
#define CAM_T       0x24
#define CAM_TSET    0x2C
#define CAM_DEF     0x3C
#define FIELD_ROWS  16      /* BUILD 646j: field models remembered */
#define WORLD_ROWS  12      /* BUILD 647: world-map models remembered */
#define FIELD_WORLD 2u      /* Ctx.field: a world-map model (647) */

typedef struct {
    u32 mode, have_blob, draws, shadows;
    float blob[16];
    float model[16];
    s32 blend;
    float bcos;             /* BUILD 646l: cos of the blob actor's facing */
    u32 rb[RB_BYTES / 4];
    u8 *entry;              /* BUILD 645f: the blob's draw_deferred entry */
    float bias;             /* BUILD 645g: depth bias, x the blob radius (stub) */
    u32 layer;              /* BUILD 645h: shadow draws so far for this actor */
    u32 pend;               /* BUILD 645q: this model draw wants its shadow */
    u32 fastdone;           /* BUILD 645q: ... and fast_stub drew it */
    u32 need_r;             /* BUILD 646: field floor still to build */
    u32 field;              /* BUILD 646c: the floor is a field model's */
    u32 s110;               /* BUILD 646j: the field model's struc_110 (guest) */
    u32 slot, next;         /* BUILD 646j: this model's row, the next free */
    float bsin;             /* BUILD 646l: ... and its sin */
    float light[3];         /* BUILD 646j: the field light, in camera space */
    u32 bok;                /* BUILD 646l: bcos/bsin are this blob's */
    struct { u32 key; s32 tri; } row[FIELD_ROWS];   /* last walkmesh triangle */
    u8 **pages;             /* BUILD 646i: the guest page table (u8 *[]) */
    float wlxz[3], wly[3];  /* BUILD 647: FFNx's world-map light, camera space:
                             * light = wlxz + s . wly (s: which way is up) */
    u32 wnext;              /* BUILD 647: next world row to replace */
    struct { u32 key; float n[3]; } wrow[WORLD_ROWS];  /* model up, camera space */
    u32 sonly;              /* BUILD 647e: shadow-only pass (world map) */
    struct { s32 tri; u32 v; } vrow[FIELD_ROWS];    /* 651: staged verdict */
    s32 mrow[FIELD_ROWS];   /* BUILD 655: the row's field model index (-1 unknown) */
    u32 fade;               /* BUILD 655: 0..100, FFNx's height fade (field) */
    float gap;              /* BUILD 657: model origin above its floor (field) */
    u32 ndef, pad667;       /* BUILD 667: deferred entries queued in a shadow-only pass */
    u64 dent[DENT_MAX];     /* ... their addresses (marked DE_SKIP after it) */
    u32 marked;             /* BUILD 667d: rs blend mode carries the model mark */
    s32 morig;              /* ... the blend mode it replaced */
    u8 *cam;                /* BUILD 670: ff7nx_subcam's state (fast_stub), or 0 */
    float clip[4];          /* BUILD 861: world x of a camera point = clip . p + clip[3] */
    u32 clipon;             /* ... in use for this field model (games' stairs) */
} Ctx;

/* BUILD 646i: guest (x86) memory, through the port's own page table (what
 * module +0x10FC3A0 does: 4 KB pages, an unmapped page is null). */
static inline __attribute__((always_inline)) u8 *g2h(Ctx *c, u32 g)
{
    u8 *p = c->pages[g >> 12];
    return p ? p + (g & 0xFFFu) : 0;
}

static inline __attribute__((always_inline)) int rdg16(Ctx *c, u32 g, s32 *v)
{
    u8 *a = g2h(c, g), *b = g2h(c, g + 1);
    if (!a || !b)
        return 0;
    *v = (s32)(short)(*a | (*b << 8));
    return 1;
}

static inline __attribute__((always_inline)) int rdg32(Ctx *c, u32 g, u32 *v)
{
    s32 lo, hi;
    if (!rdg16(c, g, &lo) || !rdg16(c, g + 2, &hi))
        return 0;
    *v = ((u32)lo & 0xFFFFu) | ((u32)hi << 16);
    return 1;
}

/* BUILD 646j: a guest float */
static inline __attribute__((always_inline)) int rdgf(Ctx *c, u32 g, float *f)
{
    union { u32 u; float f; } v;
    if (!rdg32(c, g, &v.u))
        return 0;
    *f = v.f;
    return 1;
}

#define FIELD_FILE_BUFFER 0xCFF594u /* x86: the field file (FFNx field_level_data_pointer) */
#define FIELD_CAMERA      0xCFF980u /* x86: the field camera, float[16] (see field_floor) */
#define WALK_MAX_TRIS     4096
#define WALK_OFFSET       K(0x41300000u)   /* 11: FFNx walkMeshPosOffset */

/* BUILD 646j: one walkmesh triangle, 3 x {s16 x, y, z, pad}. Straight from
 * host memory when its 24 bytes sit in one page (all but a few), else a
 * guest read per value. */
static inline __attribute__((always_inline)) int rdtri(Ctx *c, u32 g, s32 *v)
{
    u8 *p = g2h(c, g), *q = g2h(c, g + 23);
    int j;
    if (p && q == p + 23) {
        for (j = 0; j < 9; j++) {
            const u8 *a = p + 8 * (j / 3) + 2 * (j % 3);
            v[j] = (s32)(short)(a[0] | (a[1] << 8));
        }
        return 1;
    }
    for (j = 0; j < 9; j++)
        if (!rdg16(c, g + 8 * (j / 3) + 2 * (j % 3), &v[j]))
            return 0;
    return 1;
}

/* the height of triangle v at (x, y), if it holds the point (1e-3 slack) */
static inline __attribute__((always_inline)) int tri_z(const s32 *v, float x, float y, float *z)
{
    float ax = (float)v[0], ay = (float)v[1], bx = (float)v[3], by = (float)v[4];
    float cx = (float)v[6], cy = (float)v[7], d, u, w;
    d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy);
    if (d == 0.0f)
        return 0;
    u = ((by - cy) * (x - cx) + (cx - bx) * (y - cy)) / d;
    w = ((cy - ay) * (x - cx) + (ax - cx) * (y - cy)) / d;
    if (u < K(0xBA83126Fu) || w < K(0xBA83126Fu) || u + w > K(0x3F80418Au))
        return 0;
    *z = u * (float)v[2] + w * (float)v[5] + (1.0f - u - w) * (float)v[8];
    return 1;
}

/* BUILD 646i/j: the walkmesh height under world (x, y), as FFNx's field
 * shadow receiver uses it: level + [level + 0x16] is the walkmesh, +4 its
 * triangle count, +8 the triangles, 24 bytes each. Of the triangles that
 * hold (x, y), the one nearest `near` in height wins (a bridge over a
 * floor). The model's last triangle (*tri) is tried first: while it still
 * holds the point, that is the whole search. 0 if no level is loaded or no
 * triangle holds the point. */
static int tiny(Ctx *c, u32 acc, u32 n, s32 t);

/* BUILD 662: ladder scaffolding -- a walkmesh island of at most two
 * triangles, each under SCAFFOLD units across in x and y. convil_1's long
 * ladder (580 -> -174) passes three of them (z 337, 153, -30) that no
 * floor art shows: a model on the ladder is off its own triangle, the
 * geometric search found them, and the shadow floated in mid-air. Real
 * small floors (the item shop's 2-triangle pieces) are hundreds across. */
#define SCAFFOLD 128
static inline __attribute__((always_inline)) int scaffold(Ctx *c, const s32 *v, u32 acc, u32 n, s32 t)
{
    s32 lo, hi, k, j;
    for (k = 0; k < 2; k++) {
        lo = hi = v[k];
        for (j = 3; j < 9; j += 3) {
            if (v[j + k] < lo) lo = v[j + k];
            if (v[j + k] > hi) hi = v[j + k];
        }
        if (hi - lo >= SCAFFOLD)
            return 0;
    }
    return tiny(c, acc, n, t);
}

/* BUILD 663: the height of triangle v's PLANE at (x, y); 0 if vertical */
static inline __attribute__((always_inline)) int vplane_z(const s32 *v, float x, float y, float *z)
{
    float ax = (float)v[0], ay = (float)v[1], az = (float)v[2];
    float ux = (float)v[3] - ax, uy = (float)v[4] - ay, uz = (float)v[5] - az;
    float vx = (float)v[6] - ax, vy = (float)v[7] - ay, vz = (float)v[8] - az;
    float nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
    if (nz < K(0x3A83126Fu) && nz > K(0xBA83126Fu))
        return 0;
    *z = az - (nx * (x - ax) + ny * (y - ay)) / nz;
    return 1;
}

/* BUILD 663: squared distance in (x, y) from a point to segment a-b */
static inline __attribute__((always_inline)) float seg_d2(float px, float py, float ax, float ay,
                                                          float bx, float by)
{
    float dx = bx - ax, dy = by - ay, l = dx * dx + dy * dy, t = 0.0f;
    if (l > 0.0f) {
        t = ((px - ax) * dx + (py - ay) * dy) / l;
        t = t < 0.0f ? 0.0f : (t > 1.0f ? 1.0f : t);
    }
    dx = ax + t * dx - px;
    dy = ay + t * dy - py;
    return dx * dx + dy * dy;
}

/* BUILD 663: how close (x, y) must come to a floor's edge for a model off
 * its own floor (a ladder) to be shadowed on that floor's plane: the foot of
 * convil_1's long ladder hangs just past its floor's edge, so neither end of
 * the climb found a floor and the shadow appeared/vanished at once. */
#define EDGE_NEAR   48.0f
#define PROP_SLACK  16.0f   /* BUILD 673: see ride_low */
#define HOVER       64.0f   /* BUILD 766: see ride_low / airborne */
#define ABOVE_SLACK 32.0f   /* 11 (FFNx receiver offset) + SINK_RANGE 20 + 1: a
                             * floor just overhead still fades the shadow in */

/* `off`: 0, or the model's up (+-1) when it is off the floor the game names:
 * then scaffolding never receives, only floors BELOW the model do, and a
 * floor whose edge is within EDGE_NEAR counts with its plane extended.
 * BUILD 766: a capped lift ride (ride_low) passes the low end of its travel
 * as `near`: floors above it are skipped in the search, so the next floor
 * down still receives (673 picked the nearest, then refused it). */
static inline __attribute__((always_inline)) int walk_z(Ctx *c, float x, float y, float near,
                                                        s32 *tri, float *out, float off)
{
    u32 level, wo, base, n, i, all;
    s32 v[9], cnt, bi = -1, ei = -1;
    float best = 0.0f, bd = -1.0f, eb = 0.0f, ed = -1.0f, z, e, d2;
    if (!rdg32(c, FIELD_FILE_BUFFER, &level) || !level)
        return 0;
    if (!rdg32(c, level + 0x16, &wo) || !rdg16(c, level + wo + 4, &cnt))
        return 0;
    n = all = (u32)cnt & 0xFFFFu;
    if (n > WALK_MAX_TRIS)
        n = WALK_MAX_TRIS;
    base = level + wo + 8;
    if (off == 0.0f && *tri >= 0 && (u32)*tri < n && rdtri(c, base + 24 * (u32)*tri, v)
        && tri_z(v, x, y, &z)) {
        *out = z;
        return 1;
    }
    for (i = 0; i < n; i++) {
        if (!rdtri(c, base + 24 * i, v))
            return 0;
        if (tri_z(v, x, y, &z)) {
            d2 = 0.0f;
        } else {
            if (off == 0.0f)
                continue;
            d2 = seg_d2(x, y, (float)v[0], (float)v[1], (float)v[3], (float)v[4]);
            e = seg_d2(x, y, (float)v[3], (float)v[4], (float)v[6], (float)v[7]);
            d2 = e < d2 ? e : d2;
            e = seg_d2(x, y, (float)v[6], (float)v[7], (float)v[0], (float)v[1]);
            d2 = e < d2 ? e : d2;
            if (d2 > EDGE_NEAR * EDGE_NEAR || !vplane_z(v, x, y, &z))
                continue;
        }
        if (off != 0.0f) {
            /* BUILD 662: a model off its own floor never lands on scaffolding */
            if (scaffold(c, v, base + 24 * all, all, (s32)i))
                continue;
            /* 663: ... nor on a floor above it */
            if ((z - near) * off > ABOVE_SLACK)
                continue;
        }
        e = z - near;
        if (e < 0)
            e = -e;
        if (d2 == 0.0f) {
            if (bd < 0 || e < bd) {
                bd = e;
                best = z;
                bi = (s32)i;
            }
        } else if (ed < 0 || e < ed) {
            ed = e;
            eb = z;
            ei = (s32)i;
        }
    }
    if (bd < 0) {
        if (ed < 0)
            return 0;
        bi = ei;
        best = eb;
    }
    *tri = bi;
    *out = best;
    return 1;
}

/* BUILD 657: is this the player's model (modules_global_object 0xCC0D88
 * +0x2A field_model_id)? The 651 ledge rule is for staged NPCs; the player
 * standing on a small raised walkmesh piece (convil_1's item shop) keeps
 * the shadow. */
static inline __attribute__((unused, always_inline)) int is_player(Ctx *c)
{
    s32 id;
    return c->mrow[c->slot] >= 0 && rdg16(c, 0xCC0DB2u, &id)
        && (id & 0xFFFF) == c->mrow[c->slot];
}

/* BUILD 655: the height of walkmesh triangle t's PLANE at (x, y) (the
 * triangle extended: a model on a ladder has left it) */
static inline __attribute__((always_inline)) int tri_plane_z(Ctx *c, s32 t, float x, float y, float *z)
{
    u32 level, wo;
    s32 cnt, v[9];
    float ax, ay, az, ux, uy, uz, vx, vy, vz, nx, ny, nz;
    if (!rdg32(c, FIELD_FILE_BUFFER, &level) || !level)
        return 0;
    if (!rdg32(c, level + 0x16, &wo) || !rdg16(c, level + wo + 4, &cnt))
        return 0;
    if (t < 0 || t >= (cnt & 0xFFFF) || !rdtri(c, level + wo + 8 + 24 * (u32)t, v))
        return 0;
    /* the model must stand over it (a model index that does not match the
     * event table, or a stale id, falls back to the geometric search) */
    if (!tri_z(v, x, y, &ax))
        return 0;
    ax = (float)v[0]; ay = (float)v[1]; az = (float)v[2];
    ux = (float)v[3] - ax; uy = (float)v[4] - ay; uz = (float)v[5] - az;
    vx = (float)v[6] - ax; vy = (float)v[7] - ay; vz = (float)v[8] - az;
    nx = uy * vz - uz * vy; ny = uz * vx - ux * vz; nz = ux * vy - uy * vx;
    if (nz < K(0x3A83126Fu) && nz > K(0xBA83126Fu))
        return 0;                                   /* a vertical triangle */
    *z = az - (nx * (x - ax) + ny * (y - ay)) / nz;
    return 1;
}

/* BUILD 655: the mean height of walkmesh triangle t */
static inline __attribute__((unused, always_inline)) int tri_mid_z(Ctx *c, s32 t, float *z)
{
    u32 level, wo;
    s32 cnt, v[9];
    if (!rdg32(c, FIELD_FILE_BUFFER, &level) || !level)
        return 0;
    if (!rdg32(c, level + 0x16, &wo) || !rdg16(c, level + wo + 4, &cnt))
        return 0;
    if (t < 0 || t >= (cnt & 0xFFFF) || !rdtri(c, level + wo + 8 + 24 * (u32)t, v))
        return 0;
    *z = (float)(v[2] + v[5] + v[8]) * K(0x3EAAAAABu);
    return 1;
}

/* BUILD 651: is walkmesh triangle t on an island of at most two triangles
 * (its access table: 3 s16 neighbours, -1 none, after the n triangles)? */
static __attribute__((unused, noinline, section(".text.sh_pre"))) int tiny(Ctx *c, u32 acc, u32 n, s32 t)
{
    s32 a[3], b, u = -1, k = 0, j, m;
    for (j = 0; j < 3; j++) {
        if (!rdg16(c, acc + 6 * (u32)t + 2 * (u32)j, &a[j]))
            return 0;
        if (a[j] >= 0 && (u32)a[j] < n && a[j] != t && a[j] != u) {
            k++;
            u = a[j];
        }
    }
    if (k == 0)
        return 1;
    if (k > 1)
        return 0;
    for (j = 0, m = 0; j < 3; j++) {
        if (!rdg16(c, acc + 6 * (u32)u + 2 * (u32)j, &b))
            return 0;
        if (b >= 0 && (u32)b < n && b != t && b != u)
            m++;
    }
    return m == 0;
}

/* BUILD 651: a STAGED placement -- a model standing on a walkmesh island of
 * at most two triangles (a window sill, a doorway, a ledge: script-placed,
 * not walkable ground) whose height is more than STAGED_GAP above or below
 * the nearest walkable ground (the closest triangle, by centre, that is not
 * on such an island). Its shadow would be cast past the small piece of floor
 * it stands on and land on the street or wall far from it (del2's window,
 * hardware): no shadow. Shopkeepers behind counters stand at their shop's
 * floor height and keep theirs. Decided once per model and triangle. */
#define STAGED_GAP K(0x42800000u)   /* 64 */
/* BUILD 655: FFNx's field shadow fade (FFNx.pcf.sh): a shadow fades out as
 * the vertical gap between the occluder and the walkmesh under it grows --
 * fully drawn up to FADE_START, gone FADE_RANGE later (FFNx.lighting.toml:
 * 100..200 on the fields it tunes, range 100). Here the gap is the model's
 * height above ITS OWN floor triangle. */
#define FADE_START K(0x42C80000u)   /* 100 */
#define FADE_RANGE K(0x42C80000u)   /* 100 */
#define SINK_RANGE K(0x41A00000u)   /* 20: BUILD 662 */
#define HANG_SLACK K(0x41000000u)   /* 8: BUILD 703 */
#define FADE_MAGIC 0x534844u        /* 'SHD' in blendMode.z: .y is the fade */

/* BUILD 655: the walkmesh triangle the GAME says this field model stands on
 * (field_event_data[i].triangle, x86 0xCC0B60 + i*0x88 + 0x78; FFNx ff7.h
 * field_triangle_id), i the model's index in the field model table
 * (0xCFF738 + i*0x190, whose +0x178 object + 0x38 is the struc_110 the draw
 * passes -- field_draw_everything, x86 0x63A743). -1 if unknown. Multi-level
 * fields (Fort Condor, ladders) put several floors under one (x, y); the
 * geometric guess picked the wrong one, the game's own triangle cannot. */
#define FIELD_MODELS  0xCFF738u
#define FIELD_NMODELS 0xCFF73Eu
#define FIELD_EVENTS  0xCC0B60u
#define FIELD_ZOFS    0xCFF8E8u  /* BUILD 667e: per-model z raise (x86 0x6393B7) */
static inline __attribute__((always_inline)) s32 game_tri(Ctx *c)
{
    u32 tab, obj, ev, i;
    s32 n, t;
    if (c->mrow[c->slot] < 0) {
        if (!rdg32(c, FIELD_MODELS, &tab) || !tab || !rdg16(c, FIELD_NMODELS, &n))
            return -1;
        n &= 0xFFFF;
        if (n > 64)
            n = 64;
        for (i = 0; i < (u32)n; i++)
            if (rdg32(c, tab + 0x190 * i + 0x178, &obj) && obj && obj + 0x38 == c->s110)
                break;
        if (i == (u32)n)
            return -1;
        c->mrow[c->slot] = (s32)i;
    }
    if (!rdg32(c, FIELD_EVENTS, &ev) || !ev
        || !rdg16(c, ev + 0x88 * (u32)c->mrow[c->slot] + 0x78, &t))
        return -1;
    return t;
}
/* BUILD 671: a LIFT out of a hidden shaft. mds7pb_1, Barret climbing out of
 * the pinball machine: XYZI puts him on triangle 94 (z -255, a 2-triangle
 * island under the bar) and LADER (x86 0x615EC6: movement type +0x63 = 4/5,
 * the destination triangle at +0x7A) lifts him to triangle 92 (z 25, the
 * machine's top). His named floor stays 94 until he arrives, so the shadow
 * lay on the shaft's floor, then on the bar floor's plane over the hole
 * (clipped by the hole's art), then jumped up onto 92 (hardware 10-14).
 * On a LADER whose named triangle is scaffolding (scaffold(), 662), the
 * island never receives; the DESTINATION does, once it holds the model's
 * (x, y), with the usual sink fade (it fades in over the last SINK_RANGE
 * units of the climb, and is the floor he then stands on: no jump); else
 * nothing. Ladders from a real floor (convil_1, every other LADER) never
 * reach this. (ride_low decides it, BUILD 766.) */
static __attribute__((noinline, section(".text.sh_pre"))) int rd16n(Ctx *c, u32 g, s32 *v)
{
    return rdg16(c, g, v);
}

static __attribute__((noinline, section(".text.sh_pre"))) int rd32n(Ctx *c, u32 g, u32 *v)
{
    s32 lo, hi;
    if (!rd16n(c, g, &lo) || !rd16n(c, g + 2, &hi))
        return 0;
    *v = ((u32)lo & 0xFFFFu) | ((u32)hi << 16);
    return 1;
}

/* BUILD 766: the (x, y) box of walkmesh triangle t (lo/hi: x, y) */
static __attribute__((noinline, section(".text.sh_pre"))) int tbox(Ctx *c, u32 base, s32 t, s32 *lo, s32 *hi)
{
    s32 k, j, a;
    for (k = 0; k < 2; k++) {
        lo[k] = 0x7FFF;
        hi[k] = -0x8000;
        for (j = 0; j < 3; j++) {
            if (!rd16n(c, base + 24 * (u32)t + 8 * (u32)j + 2 * (u32)k, &a))
                return 0;
            lo[k] = a < lo[k] ? a : lo[k];
            hi[k] = a > hi[k] ? a : hi[k];
        }
    }
    return 1;
}

/* BUILD 673: a PROP lift. mds7pb_2, the pinball machine (char 07): LADER
 * (254, -33) z 0 <-> z 340, destination triangle 53 -- whose island (z 260,
 * x 191..235) does not even hold the machine's (x, y): it rides beside the
 * walkmesh, never on it. Off its floor, the 663 edge rule found that island
 * within 48 units and, from z 228 up, shadowed the machine on its plane:
 * in mid-air from the basement camera (hardware 10-14). FFNx's receivers
 * are the walkmesh triangles themselves; ours are planes, so an island next
 * to a prop catches a whole flattened machine.
 * On a LADER whose END is not on its destination triangle (a prop moved by
 * script, not a climber: Barret's lift ends on 92, every convil_1 ladder on
 * its floor; "on" = inside its (x, y) box), the model receives only on
 * floors no higher than the low end of its travel: the lower of its end
 * height and its named floor's height (its first vertex).
 *
 * BUILD 766 (hardware 10-20, mds7pb_2 again):
 * (a) the ARRIVAL frame. The ride's last step (x86 0x6357E2) sets the named
 *     triangle to the destination (53, the island, z 260) and the state
 *     (+0x70) to 2; the LADER opcode (0x615F83) clears the movement type
 *     only on the script's next turn, and the machine is hidden (VISI 0)
 *     after that. For that one drawn frame the "low end" was the island's
 *     own height, so the island received the machine 80 above it: its
 *     shadow flashed at the top of the screen. The ride's START (+0x18, the
 *     position copied when it began, 0x6351FC; FFNx model_initial_pos) is
 *     now part of the low end while the ride runs (state 1 or 2): z 0.
 * (b) a ride that starts in MID-AIR. The mod's script puts Tifa (Cloud on
 *     the way in) at z 360 (340) on triangle 61 -- the same z 260 island,
 *     100 (80) under her feet: she stands on the machine, not on it -- and
 *     lowers her to z 35 on 62. Her named floor stays 61 until she lands,
 *     so her shadow lay on the island's plane, in the air, from 360 down to
 *     240, and then nothing until she landed (popped in). A ride whose
 *     start is more than HOVER above its named floor (it never stood on it;
 *     every census XYZI but these is within 24 of its floor) is capped the
 *     same way: at the lower of its start and end. Her shadow is now the basement
 *     floor's, fading in with FFNx's height fade as she comes down.
 * (c) a model STANDING (movement type 0: not walking 1, jumping 3 or on a
 *     LADER 4/5) whose own position is more than HOVER above the floor it
 *     would shade: placed in mid-air by script -- Tifa at z 360 for the
 *     frame(s) between her XYZI and her ride (REQSW waits a turn), the
 *     machine resting at z 340 beside the island. No shadow. Census (702
 *     fields, 4,806 XYZI on a triangle that holds them): only these place a
 *     model more than 24 above its floor (next: nmkin_2's av_j, 39).
 * One reading of the model's event for 671, 673 and 766 (lift_dest merged
 * in): 0 nothing; -1 a 671 lift with no receiver; 16 + t a 671 lift onto
 * triangle t; 1 a prop, *low the cap (raw walkmesh height); 2 a climber
 * whose ride has begun, *low the cap and *sz its start height -- capped
 * only if that start is HOVER above its named floor (field_floor); 3
 * standing, *sz its height. */
static __attribute__((noinline, section(".text.sh_pre"))) int ride_low(Ctx *c, s32 own, float s, float *low, float *sz)
{
    u32 ev, level, wo, base, all, f[3], g[3];
    s32 mt, d, cnt, a, lo[2], hi[2], k, st, ride = 2;
    float z;
    if (own < 0 || c->mrow[c->slot] < 0 || !rd32n(c, FIELD_EVENTS, &ev) || !ev)
        return 0;
    ev += 0x88u * (u32)c->mrow[c->slot];
    if (!rd16n(c, ev + 0x62, &mt) || !rd16n(c, ev + 0x7A, &d) || !rd16n(c, ev + 0x70, &st)
        || !rd32n(c, ev + 0x14, &g[2]))
        return 0;
    mt = (mt >> 8) & 0xFF;
    *sz = (float)((s32)g[2] >> 12);
    if (mt == 0)
        return 3;           /* 766 (c): standing, *sz its own height (+0x0C) */
    if (mt != 4 && mt != 5)
        return 0;
    for (k = 0; k < 3; k++)
        if (!rd32n(c, ev + 0x7C + 4 * k, &f[k]) || !rd32n(c, ev + 0x18 + 4 * k, &g[k]))
            return 0;
    if (!rd32n(c, FIELD_FILE_BUFFER, &level) || !level
        || !rd32n(c, level + 0x16, &wo) || !rd16n(c, level + wo + 4, &cnt))
        return 0;
    all = (u32)cnt & 0xFFFFu;
    d &= 0xFFFF;
    if (all > WALK_MAX_TRIS || (u32)own >= all)
        return 0;
    base = level + wo + 8;
    /* 671: the named triangle is scaffolding (scaffold(), the extent read
     * one value at a time): a lift out of a hidden shaft */
    if (!tbox(c, base, own, lo, hi))
        return 0;
    if (hi[0] - lo[0] < SCAFFOLD && hi[1] - lo[1] < SCAFFOLD && tiny(c, base + 24 * all, all, own))
        return (u32)d < all && d != own ? 16 + d : -1;
    /* 766: not begun (state 0: the start is not this ride's yet) -- it has
     * not moved: judged as standing (c) */
    if (st != 1 && st != 2)
        return 3;
    if ((u32)d >= all || !tbox(c, base, d, lo, hi))
        return 0;
    /* 673: the end point inside the destination's (x, y) box: a climber */
    if (!((s32)f[0] >> 12 >= lo[0] && (s32)f[0] >> 12 <= hi[0]
          && (s32)f[1] >> 12 >= lo[1] && (s32)f[1] >> 12 <= hi[1]))
        ride = 1;                               /* a prop */
    /* the low end of the travel: the end height, the start's (766 a) and,
     * for a prop, the named floor's */
    z = (float)((s32)f[2] >> 12);
    *sz = (float)((s32)g[2] >> 12);
    if ((*sz - z) * s < 0.0f)
        z = *sz;
    if (ride == 1) {
        if (!rd16n(c, base + 24 * (u32)own + 4, &a))
            return 0;
        if (((float)a - z) * s < 0.0f)
            z = (float)a;
    }
    *low = z;
    return ride;
}

/* BUILD 861: gldgate (field 497), the Gold Saucer's tubes (hardware
 * 10-29: "your model shadow is still showing up in locations on the field
 * as you are jumping into the hole, or returning from inside the hole").
 * Triangles 38..51 are the tubes' slopes (z 55 at the rim to -124 inside);
 * JUMP (x86 0x615CA3) goes in onto 38/41/43/45/47/49/50 and, after an XYZI
 * there, out onto the platform (7, 10..12, 14..16). During a jump the game
 * names its START triangle until the last step (0x634F9F), so the shadow
 * lay on the tube's slope going out, and on the platform's plane over the
 * rim going in. Now: rising (state +0x70 = 1, vertical speed +0x2C still
 * above gravity x step, 16000 x +0x32 -- 0x634F46/0x63510C) the shadow
 * belongs to the start triangle, falling to the destination (+0x7A); in a
 * tube, none. So going in it shows until the top of the jump, coming out
 * from the top of the jump on. Returns the triangle, or -2: no shadow.
 * The event is read straight from host memory (one page; the array is
 * static, x86 0x634D4D addresses it as 0xCC1670 + 0x88 i), else as is. */
#define FID_GLDGATE 497
/* BUILD 861: games (Wonder Square's lobby, field 505), the stairs up to the
 * TV station (hardware 10-29: "my character's shadow is peaking through the
 * wall when i walk up the staircase"). The stair and landing walkmesh
 * (triangles 0..9) ends at x -36; left of it stands the cylinder whose
 * lower half is 1997 layer-2 art at ID 4081 (no depth). A part's shadow
 * landing past the edge lay on the stair plane's extension, in front of
 * that art -- the sword's, a thin dark bar on the cylinder (measured on
 * the screenshot: camera (-42, 89.5), Cloud at (-9, -59, 121) on triangle
 * 4, the bar at world x < -56). A depth fix cannot do it: that extension is
 * as near as anyone standing on the floor in front of the cylinder. So on
 * those triangles a part whose shadow origin lands left of STAIR_EDGE (12
 * past the walkmesh) casts none; FFNx's receivers are the walkmesh itself
 * and show nothing there either. */
#define FID_GAMES   505
#define STAIR_EDGE  48.0f
#define FIELD_EVENT0 0xCC1670u   /* the event array itself (*FIELD_EVENTS) */
static __attribute__((noinline, section(".text.sh_pre"))) s32 tube_jump(Ctx *c, s32 gt)
{
    s32 i = c->mrow[c->slot];
    u32 g = FIELD_EVENT0 + 0x88u * (u32)i;
    u8 *e = i >= 0 && (g & 0xFFFu) <= 0x1000u - 0x88u ? g2h(c, g) : 0;
    if (e && e[0x63] == 3
        && *(short *)(e + 0x70) == 1
        && *(s32 *)(e + 0x2C) <= 16000 * (s32)*(short *)(e + 0x32))
        gt = *(short *)(e + 0x7A);
    return gt >= 38 && gt <= 51 ? -2 : gt;
}

static __attribute__((unused, noinline, section(".text.sh_pre"))) int staged(Ctx *c, s32 tri, float x, float y, float tz)
{
    u32 level, wo, base, acc, n, i;
    s32 cnt, v[9];
    float bd = -1.0f, bz = 0.0f, cx, cy, d;
    if (c->vrow[c->slot].tri == tri && c->vrow[c->slot].v)
        return c->vrow[c->slot].v == 2;
    c->vrow[c->slot].tri = tri;
    c->vrow[c->slot].v = 1;
    if (!rdg32(c, FIELD_FILE_BUFFER, &level) || !level)
        return 0;
    if (!rdg32(c, level + 0x16, &wo) || !rdg16(c, level + wo + 4, &cnt))
        return 0;
    n = (u32)cnt & 0xFFFFu;
    if (n > WALK_MAX_TRIS || tri < 0 || (u32)tri >= n)
        return 0;
    base = level + wo + 8;
    acc = base + 24 * n;
    if (!tiny(c, acc, n, tri))
        return 0;
    for (i = 0; i < n; i++) {
        if (tiny(c, acc, n, (s32)i) || !rdtri(c, base + 24 * i, v))
            continue;
        cx = (float)(v[0] + v[3] + v[6]) * K(0x3EAAAAABu) - x;
        cy = (float)(v[1] + v[4] + v[7]) * K(0x3EAAAAABu) - y;
        d = cx * cx + cy * cy;
        if (bd < 0 || d < bd) {
            bd = d;
            bz = (float)(v[2] + v[5] + v[8]) * K(0x3EAAAAABu);
        }
    }
    if (bd < 0)
        return 0;
    d = tz - bz;
    if (d < 0)
        d = -d;
    if (d > STAGED_GAP)
        c->vrow[c->slot].v = 2;
    return c->vrow[c->slot].v == 2;
}

/* FFNx's default field light (light_rotation_vertical = horizontal = 60):
 * the direction towards the light, in field world space (Z up) */
#define FLIGHT_X K(0x3EDDB3D7u)     /*  0.4330 */
#define FLIGHT_Y K(0xBE800000u)     /* -0.25   */
#define FLIGHT_Z K(0x3F5DB3D7u)     /*  0.8660 */

/* BUILD 646j: the floor of a field model, in the space its parts are drawn
 * in -- which on the field is the CAMERA's, not the world's (x86, checked):
 *   0x639157 builds the camera C = 0xCFF980 (float, column vectors:
 *     v = A w + T, A = C[0..2|4..6|8..10], T = C[3|7|11]) from the field
 *     camera 0xCFF3D8, and sets the game's view matrix (+0x2FC) to identity;
 *   0x6392BB, per model: struc_110.position = C . (entity position), and
 *     struc_110.matrix = (C . Rz(direction) . Y/Z swap)^T;
 *   draw_3d_model (0x6840DA; field flags 0x407B, so 0x4000 on, 0x8000 off):
 *     root = anim root . struc_110.matrix + position, part = bone . root.
 * So every part matrix (rs +0x28) maps model space straight to camera space,
 * and the view (+0x68) is identity. That is why 646-646i, which treated it
 * as the world, put the floor wherever the camera axes happened to point.
 * Here, with A^-1 from C:
 *   the model's world position  w = A^-1 (struc_110.position - T),
 *   the floor height            the walkmesh under (w.x, w.y) (FFNx's
 *                               receiver), or w.z off the walkmesh,
 *   up                          world +-Z, whichever has the first part
 *                               (the hips) above the floor,
 *   the light                   FFNx's default, fixed in the world,
 * all taken to camera space for the plane the parts are flattened onto
 * (b, battle-blob style: row 0 radius, row 1 normal, row 3 a point) and
 * c->light. 0: no camera or no struc_110 (no shadow). */
static int field_floor(Ctx *c, const float *m, float *b)
{
    float C[12], p[3], hp[3], ai[9], w[3], hw[3], det, fz, s, h, k, g, sink, fu, low = 0.0f, sz = 0.0f;
    int i, cap = 0, on;
    s32 gt, own, lift;
    if (!c->pages)
        return 0;
    for (i = 0; i < 12; i++)
        if (!rdgf(c, FIELD_CAMERA + 4 * i, &C[i]))
            return 0;
    for (i = 0; i < 3; i++)
        if (!rdgf(c, c->s110 + 4 + 4 * i, &p[i]))
            return 0;
    ai[0] = C[5] * C[10] - C[6] * C[9];
    ai[1] = C[2] * C[9] - C[1] * C[10];
    ai[2] = C[1] * C[6] - C[2] * C[5];
    ai[3] = C[6] * C[8] - C[4] * C[10];
    ai[4] = C[0] * C[10] - C[2] * C[8];
    ai[5] = C[2] * C[4] - C[0] * C[6];
    ai[6] = C[4] * C[9] - C[5] * C[8];
    ai[7] = C[1] * C[8] - C[0] * C[9];
    ai[8] = C[0] * C[5] - C[1] * C[4];
    det = C[0] * ai[0] + C[1] * ai[3] + C[2] * ai[6];
    if (!(det > K(0x358637BDu) || det < K(0xB58637BDu)))     /* |det| > 1e-6 */
        return 0;
    det = 1.0f / det;
    for (i = 0; i < 9; i++)
        ai[i] *= det;
    p[0] -= C[3]; p[1] -= C[7]; p[2] -= C[11];
    hp[0] = m[12] - C[3]; hp[1] = m[13] - C[7]; hp[2] = m[14] - C[11];
    for (i = 0; i < 3; i++) {
        w[i] = ai[3 * i] * p[0] + ai[3 * i + 1] * p[1] + ai[3 * i + 2] * p[2];
        hw[i] = ai[3 * i] * hp[0] + ai[3 * i + 1] * hp[1] + ai[3 * i + 2] * hp[2];
    }
    gt = game_tri(c);
    /* BUILD 659: the 651 ledge rule also hid the player on small walkmesh
     * floors (convil_1's item shop is a 2-triangle floor). With the game's
     * own triangle and FFNx's fade it is gone; the one staged NPC that
     * should cast nothing is named: del2 (field 443), the window lady on
     * triangle 120. */
    c->clipon = 0;
    {
        s32 fid;
        if (rdg16(c, 0xCFF468u, &fid)) {
            fid &= 0xFFFF;
            if (gt == 120 && fid == 443)
                return 0;
            /* BUILD 669: the Lifestream (life 723, life2 724). Cloud and Tifa
             * float in a void; the walkmesh under them is invisible, so a
             * shadow there lies on nothing (hardware 10-14) -- none. */
            if (fid == 723 || fid == 724)
                return 0;
            /* BUILD 861: gldgate's tubes */
            if (fid == FID_GLDGATE && (gt = tube_jump(c, gt)) < -1)
                return 0;
            /* BUILD 861: games' staircase (triangles 0..9, x -36..52): no
             * part's shadow past STAIR_EDGE (sh_pre) */
            c->clipon = fid == FID_GAMES && (u32)gt <= 9;
        }
    }
    own = gt;
    s = hw[2] < w[2] ? -1.0f : 1.0f;
    /* BUILD 671: a lift out of a hidden shaft receives only on its top;
     * 673/766: a prop lift, or a ride that began in mid-air: no floor above
     * the low end of its travel receives (ride_low) */
    cap = ride_low(c, gt, s, &low, &sz);
    if (cap < 0)
        return 0;
    lift = cap >= 16 ? cap - 16 : -2;
    if (lift >= 0)
        gt = lift;
    on = gt >= 0 && tri_plane_z(c, gt, w[0], w[1], &fz);
    /* 766 (b): a climber is capped only if its ride began HOVER above it */
    if (cap == 2 && !(on && (sz - fz) * s > HOVER))
        cap = 0;
    if (on && !((cap == 1 || cap == 2) && (fz - low) * s > PROP_SLACK))
        c->row[c->slot].tri = gt;
    else if (lift >= 0)
        return 0;           /* the top does not hold him yet */
    else
        gt = -1;
    if (gt >= 0 || walk_z(c, w[0], w[1], cap == 1 || cap == 2 ? low : w[2],
                          &c->row[c->slot].tri, &fz, own >= 0 ? s : 0.0f)) {
        /* BUILD 766 (c): placed in mid-air, standing HOVER above this floor */
        if (cap == 3 && (sz - fz) * s > HOVER)
            return 0;
        /* BUILD 646k: FFNx draws its walkmesh receiver 11 below the
         * walkmesh (walkMeshPosOffset -11, "just under the character's
         * feet"): 0x6392BB places a model 10 below its walkmesh point */
        /* BUILD 660: up is where the hips are relative to the model's OWN
         * origin, not to the floor -- a model climbing down below its floor
         * (convil_1's item-shop ladder) flipped the old test and cast its
         * shadow UP onto the floor above it */
        s = hw[2] < w[2] ? -1.0f : 1.0f;
        fz -= WALK_OFFSET * s;
        /* BUILD 667e: the game RAISES some models in some fields -- a table
         * per (field, model) built at x86 0x60EFD0, copied at field load to
         * 0xCFF8E8 + 4i and added to the model's z in 0x6392BB. mds7pb_1:
         * Cloud +9, model 1 +7 (also mds7pb_2, mktpb, nivl_e1/2, nvmkin1/22,
         * tin_3, mkt_s2, delinn, blin67_2, mtcrl_8). The floor under the
         * model went 9 below his soles, and FFNx's 60/60 slant put the
         * shadow's contact edge in front of his shoes. The floor goes up
         * with the model, as the model goes up with it. */
        if (c->mrow[c->slot] >= 0) {
            u32 zo;
            if (rdg32(c, FIELD_ZOFS + 4u * (u32)c->mrow[c->slot], &zo)
                && (s32)zo > -64 && (s32)zo < 64)
                fz += (float)(s32)zo;
        }
        fu = fz + (s < 0 ? -2.0f * WALK_OFFSET : 0.0f);
    } else if (own >= 0) {
        /* BUILD 662: the game knows this model's floor, it is off it and
         * nothing is under it (mid-ladder over a void): no shadow. The old
         * fallback put the floor at the model's own feet -- a shadow
         * floating in the air just under it. */
        return 0;
    } else {
        fz = w[2];
        fu = fz;
    }
    s = hw[2] < w[2] ? -1.0f : 1.0f;
    /* BUILD 703: a body HANGING below the floor it holds on to. southmk2,
     * after the Air Buster: Cloud dangles from the broken bridge, his
     * origin at the walkway, his hips far below it. The walkmesh still
     * spans the gap (one flat bridge, z 491, art blown away), and with the
     * hips below the origin `s` flipped up to -Z, so his flattened copy lay
     * on that invisible walkmesh, in the void. The field's up is +Z (FFNx:
     * the receiver is 11 BELOW the walkmesh, its fade is occluder z -
     * receiver z; the game's lifts climb +Z), and FFNx's shadow map cannot
     * shade a receiver from a body under it: nothing. Only hips below the
     * receiver (fu: FFNx's, 11 under the walkmesh, +Z up) by HANG_SLACK do
     * this; anyone standing, climbing or lying keeps his shadow. */
    if (hw[2] < fu - HANG_SLACK)
        return 0;
    h = (hw[2] - fz) * s;
    /* BUILD 660/662: sinking below its own floor (into a ladder hole) an
     * occluder stops shading the surface above it (FFNx): the shadow fades
     * out over the first SINK_RANGE units below standing height instead of
     * vanishing at once (660 cut it at 16 below, at full strength -- the
     * feet showed under it for those 16). */
    g = (w[2] - fz) * s;
    if (g <= -SINK_RANGE)
        return 0;
    sink = g < 0 ? -g / SINK_RANGE : 0.0f;
    /* BUILD 655: FFNx's fade (ladders, jumps, a model off its floor) */
    c->gap = g > 1.0f ? g - 1.0f : 0.0f;   /* above standing (g = 1: origin 10, floor 11 below the walkmesh) */
    if (g < 0)
        g = -g;
    g = (g - FADE_START) / FADE_RANGE;
    if (g >= 1.0f)
        return 0;
    if (g < sink)
        g = sink;
    c->fade = g > 0 ? (u32)(g * 100.0f) : 0;
    for (i = 0; i < 16; i++)
        b[i] = 0.0f;
    /* the radius (only the depth bias uses it): a third of the hips' height */
    h *= K(0x3EAAAAABu);
    b[0] = h > K(0x3A83126Fu) ? h : K(0x3A83126Fu);                /* >= 0.001 */
    /* normal: A^-T (0, 0, s) = s . row 2 of A^-1 */
    b[4] = s * ai[6]; b[5] = s * ai[7]; b[6] = s * ai[8];
    /* a point: the floor under the model, C . (w.x, w.y, fz) */
    k = fz - w[2];
    b[12] = p[0] + C[3] + k * C[2];
    b[13] = p[1] + C[7] + k * C[6];
    b[14] = p[2] + C[11] + k * C[10];
    b[15] = 1.0f;
    /* BUILD 667b: FFNx's angle, as before (667 tried straight overhead;
     * the look must stay FFNx's). Checked against FFNx's lighting.cpp:
     * forward (0,0,-1) . RotateXYZ(60,60,0) = (0.433,-0.866,-0.25), field
     * swap (x, z, -y) = FLIGHT, eye = centre + dir. */
    for (i = 0; i < 3; i++)
        c->light[i] = C[4 * i] * FLIGHT_X + C[4 * i + 1] * FLIGHT_Y + C[4 * i + 2] * FLIGHT_Z * s;
    /* BUILD 861: a part origin p (camera space) lands on this floor along
     * the light at q = p - ((p - b12) . n / (l . n)) l; its world x is
     * ai0 . (q - T), linear in p: clip . p + clip[3] (+ STAIR_EDGE) */
    if (c->clipon) {
        float cl = 0.0f, ln = 0.0f, dd = 0.0f;
        for (i = 0; i < 3; i++) {
            cl += ai[i] * c->light[i];
            ln += c->light[i] * b[4 + i];
            dd += b[12 + i] * b[4 + i];
        }
        k = cl / ln;            /* ln: FFNx's light is never along the floor */
        for (i = 0; i < 3; i++)
            c->clip[i] = ai[i] - k * b[4 + i];
        c->clip[3] = STAIR_EDGE + k * dd - ai[0] * C[3] - ai[1] * C[7] - ai[2] * C[11];
    }
    return 1;
}

/* BUILD 647: the floor of a world-map model. Like the field, world models
 * are drawn in camera space (x86 0x75E0BA: 0x74D50E builds the model's
 * camera C = camera rotation 0xDFC448 . the model's terrain tilt, plus its
 * position; the game's view is identity; struc_110.position = C . 0 = the
 * model's origin in camera space). sh_wcam kept C's world-up column (the
 * terrain's up under the model) per model; here:
 *   point  struc_110.position (the feet: world models stand on the ground),
 *   normal that up, signed so the hips are above the floor,
 *   light  FFNx's world-map light, fixed in the world (sh_wcam). */
static int world_floor(Ctx *c, const float *m, float *b)
{
    float p[3], *n = c->wrow[c->slot].n, s, h;
    int i;
    for (i = 0; i < 3; i++)
        if (!rdgf(c, c->s110 + 4 + 4 * i, &p[i]))
            return 0;
    h = (m[12] - p[0]) * n[0] + (m[13] - p[1]) * n[1] + (m[14] - p[2]) * n[2];
    s = h < 0 ? -1.0f : 1.0f;
    h *= s * K(0x3EAAAAABu);
    for (i = 0; i < 16; i++)
        b[i] = 0.0f;
    b[0] = h > K(0x3A83126Fu) ? h : K(0x3A83126Fu);
    b[4] = s * n[0]; b[5] = s * n[1]; b[6] = s * n[2];
    b[12] = p[0]; b[13] = p[1]; b[14] = p[2]; b[15] = 1.0f;
    for (i = 0; i < 3; i++)
        c->light[i] = c->wlxz[i] + s * c->wly[i];
    return 1;
}

/* the offsets ff7nx_shadows.py's stubs write (CTX_*), checked at build time */
#define OFF(f) __builtin_offsetof(Ctx, f)
_Static_assert(OFF(entry) == 0xD0, "CTX_ENTRY");
_Static_assert(OFF(bias) == 0xD8, "CTX_BIAS");
_Static_assert(OFF(pend) == 0xE0 && OFF(fastdone) == 0xE4, "CTX_PEND/FASTDONE");
_Static_assert(OFF(field) == 0xEC, "CTX_FIELD");
_Static_assert(OFF(wrow) == 0x1B4, "world rows (tests)");
_Static_assert(OFF(sonly) == 0x274, "CTX_SONLY");
_Static_assert(OFF(fade) == 0x338, "CTX_FADE");
_Static_assert(OFF(ndef) == 0x340, "CTX_NDEF");
_Static_assert(OFF(dent) == 0x348, "CTX_DENT");
_Static_assert(OFF(marked) == 0x378, "CTX_MARKED");
_Static_assert(OFF(morig) == 0x37C, "CTX_MORIG");
_Static_assert(OFF(cam) == 0x380, "CTX_CAM");
_Static_assert(sizeof(Ctx) <= 0x3A0, "STATE_BYTES");
#undef OFF

/* BUILD 645q: the fast path. The GL device object is **dev; its vtable: */
#define DEV_DEPTH   0x108   /* (obj, depth write, func, stencil on, stencil func) */
#define DEV_BLEND   0x118   /* (obj, 0, src, eq, dst) */
#define DEV_UBO     0xD0    /* (obj, 0, 1, buffer, 0, 0, size, 0): bind BlockVertex */
#define DEV_DRAW    0x150   /* (obj, count, 0, index offset, primitive, index buffer) */
#define DEV_CULL    0x128   /* (obj, mode (0 = off), front face) -- +0x10D9FC8 */
#define RB_FRONT    0x768
typedef void (*fn5)(void *, u64, u64, u64, u64);
typedef void (*fn6)(void *, u64, u64, u64, u64, u64);
typedef void (*fn8)(void *, u64, u64, void *, u64, u64, u64, u64);
typedef u64 (*fn_size)(void *);
typedef void (*fn_write)(void *, void *, u64, u64);
#define VCALL(obj, off) (*(void **)(*(u8 **)(obj) + (off)))


static float rd(const u8 *p, int o) { return *(const float *)(p + o); }

static float isqrt(float v)
{
    return 1.0f / __builtin_sqrtf(v);
}

/* at every helper call: what to do with this draw */
__attribute__((section(".text.sh_pre")))
u32 sh_pre(Ctx *c, u8 *rs, u32 nidx)
{
    int i;
    c->draws++;
    /* BUILD 667d: the last part's mark comes off before anything else */
    if (c->marked) {
        *(s32 *)(rs + RS_BLEND) = c->morig;
        c->marked = 0;
    }
    if (c->mode == MODE_BLOB) {
        for (i = 0; i < 16; i++)
            c->blob[i] = rd(rs, RS_MODEL + 4 * i);
        c->have_blob = 1;
        c->layer = 0;
        c->field = 0;
        return ACT_SKIP;
    }
    /* BUILD 645p: a part of a few triangles (an eye, a mouth, a fingertip)
     * adds nothing a reader could see to its actor's shadow, which the
     * bigger parts around it already cover, but its shadow costs a whole
     * draw like any other. */
    /* BUILD 647e: the world map draws a model twice, its shadows first
     * (only the shadows: every other draw skipped), then the model itself
     * with none, so a shadow lifted over bumpy ground can never darken
     * the model it belongs to. */
    if (c->mode == MODE_ACTOR && c->sonly)
        return c->have_blob && nidx >= MIN_SHADOW_INDICES ? ACT_SHADOW : ACT_SKIP;
    /* BUILD 667d: a field model's floor, at its first shadowed part (as
     * sh_begin did), then the MODEL MARK on every opaque part: lmain_vv
     * writes the part's framebuffer alpha as the model's own shadow grey s.
     * The shadow blend is rgb * (s + 1 - dst alpha) (645o), so no shadow of
     * this model can darken it -- drawn after a part, a later part's shadow
     * landed on the boots (hardware 667c; two passes cost 3-8 fps). */
    if (c->mode == MODE_ACTOR && c->field == 1 && c->have_blob) {
        if (c->need_r && nidx >= MIN_SHADOW_INDICES) {
            float m[16];
            for (i = 0; i < 16; i++)
                m[i] = rd(rs, RS_MODEL + 4 * i);
            c->need_r = 0;
            if (!field_floor(c, m, c->blob)) {
                c->have_blob = 0;           /* the rest of this model: none */
                return ACT_DRAW;
            }
        }
        if (!c->need_r && *(s32 *)(rs + RS_BLEND) == 4) {
            c->morig = 4;
            c->marked = 1;
            *(s32 *)(rs + RS_BLEND) = (s32)(MARK_BIT | (c->fade << 8) | 4u);
        }
    }
    if (c->mode == MODE_ACTOR && c->have_blob && nidx >= MIN_SHADOW_INDICES) {
        /* BUILD 861: games' stairs -- where this part's origin lands on
         * the floor plane along the light, in world x */
        if (c->field == 1 && c->clipon) {
            float x = c->clip[3];
            for (i = 0; i < 3; i++)
                x += c->clip[i] * rd(rs, RS_MODEL + 48 + 4 * i);
            if (x < 0.0f)
                return ACT_DRAW;
        }
        return ACT_SHADOW;
    }
    return ACT_DRAW;
}

/* BUILD 646: a field model is about to be drawn (draw_3d_model from
 * field_draw_everything); there is no blob. s110 is its struc_110 (guest
 * address), the key of its row (646j: the walkmesh triangle it was last
 * on). The floor itself is built at its first shadowed part (field_floor). */
__attribute__((section(".text.sh_field")))
void sh_field(Ctx *c, u32 s110, u8 **pages)
{
    u32 i;
    for (i = 0; i < FIELD_ROWS; i++)
        if (c->row[i].key == s110)
            break;
    if (i == FIELD_ROWS) {
        i = c->next++ % FIELD_ROWS;
        c->row[i].key = s110;
        c->row[i].tri = -1;
        c->vrow[i].tri = -1;
        c->vrow[i].v = 0;
        c->mrow[i] = -1;
    }
    c->slot = i;
    c->s110 = s110;
    c->pages = pages;
    c->field = 1;
    c->have_blob = s110 != 0;
    c->layer = 0;
    c->need_r = 1;
    c->fade = 0;
    c->gap = 0.0f;
}

/* BUILD 645f: after the blob's draw call. The blob is semi-transparent, so
 * field_78 queued it (entry recorded by the stub at its allocation) instead
 * of drawing it: keep the entry's matrix and mark the entry so draw_deferred
 * skips it. An entry with no matrix of its own is left alone (blob drawn,
 * no shadow) -- its matrix would be whatever is current at draw time. */
/* BUILD 647: a world-map model is about to be drawn (draw_3d_model from
 * 0x74C6B0). It casts a shadow only if sh_wcam kept a row for it this
 * frame (a ground model). */
__attribute__((section(".text.sh_world")))
void sh_world(Ctx *c, u32 s110, u8 **pages)
{
    u32 i;
    c->have_blob = 0;
    c->field = FIELD_WORLD;
    for (i = 0; i < WORLD_ROWS; i++)
        if (c->wrow[i].key == s110 && s110)
            break;
    if (i == WORLD_ROWS)
        return;
    c->slot = i;
    c->s110 = s110;
    c->pages = pages;
    c->have_blob = 1;
    c->layer = 0;
    c->need_r = 1;
}

#define WORLD_CAMERA 0xDFC448u      /* x86: the world camera rotation, s16 3x3 / 4096 */
/* FFNx's world-map light: its 60/60 direction with y and z negated
 * (lighting.cpp updateLightMatrices, MODE_WORLDMAP) */
#define WLIGHT_X K(0x3EDDB3D7u)     /* 0.4330 */
#define WLIGHT_Y K(0x3F5DB3D7u)     /* 0.8660, the up component */
#define WLIGHT_Z K(0x3E800000u)     /* 0.25 */

/* model ids (world object +0x50) that stand on the ground and cast a shadow:
 * Cloud, Tifa, Cid, wild chocobo, Tiny Bronco, buggy, ridden chocobo. The
 * Highwind, the WEAPONs and the rest keep the game's own shadow or none. */
static int ground_model(u32 id)
{
    return id <= 2 || id == 4 || id == 5 || id == 6 || id == 19;
}

/* BUILD 647b: before 0x763B74 calls 0x75DEAA(obj) -- the game's round
 * shadow sprite under a world object: 1 = skip it, the object casts its own
 * shadow (FFNx removes the round shadow altogether; the Highwind and the
 * WEAPONs keep theirs here). obj is the call's argument, guest [esp + 4]. */
__attribute__((section(".text.sh_wblob")))
u32 sh_wblob(Ctx *c, u8 *x86, u8 **pages)
{
    u32 esp, obj;
    s32 v;
    if (!x86 || !pages)
        return 0;
    c->pages = pages;
    esp = *(u32 *)(x86 + 0x10);
    if (!rdg32(c, esp + 4, &obj) || !obj || !rdg16(c, obj + 0x50, &v))
        return 0;
    return ground_model((u32)v & 0xFF);
}

/* BUILD 647: in 0x75E0BA, right after 0x661465 turned the model's camera C
 * (s16, built by 0x74D50E) into floats at [ebp - 0xF8]: keep C's world-up
 * column for the model (key: its struc_110, [ebp + 8] + 0x44), if it is a
 * ground model ([ebp + 0xC] + 0x50), and FFNx's light through the camera. */
__attribute__((section(".text.sh_wcam")))
void sh_wcam(Ctx *c, u8 *x86, u8 **pages)
{
    u32 ebp, entry, obj, key, i, free = WORLD_ROWS;
    s32 id = 0xFF, v;
    float n[3], k, m[9];
    if (!x86 || !pages)
        return;
    c->pages = pages;
    ebp = *(u32 *)(x86 + X86_EBP);
    if (!rdg32(c, ebp + 8, &entry) || !entry)
        return;
    key = entry + 0x44;
    if (rdg32(c, ebp + 0xC, &obj) && obj && rdg16(c, obj + 0x50, &v))
        id = v & 0xFF;
    for (i = 0; i < WORLD_ROWS; i++) {
        if (c->wrow[i].key == key)
            break;
        if (!c->wrow[i].key && free == WORLD_ROWS)
            free = i;
    }
    if (!ground_model((u32)id)) {
        if (i < WORLD_ROWS)
            c->wrow[i].key = 0;
        return;
    }
    for (i = 0; i < 3; i++)
        if (!rdgf(c, ebp - 0xF8 + 4 + 16 * i, &n[i]))
            return;
    k = n[0] * n[0] + n[1] * n[1] + n[2] * n[2];
    if (!(k > K(0x2B8CBCCCu)))
        return;
    k = isqrt(k);
    for (i = 0; i < WORLD_ROWS; i++)
        if (c->wrow[i].key == key)
            break;
    if (i == WORLD_ROWS)
        i = free < WORLD_ROWS ? free : c->wnext++ % WORLD_ROWS;
    c->wrow[i].key = key;
    c->wrow[i].n[0] = n[0] * k; c->wrow[i].n[1] = n[1] * k; c->wrow[i].n[2] = n[2] * k;
    for (i = 0; i < 9; i++) {
        if (!rdg16(c, WORLD_CAMERA + 2 * i, &v))
            return;
        m[i] = (float)v * K(0x39800000u);           /* / 4096 */
    }
    for (i = 0; i < 3; i++) {
        c->wlxz[i] = m[3 * i] * WLIGHT_X + m[3 * i + 2] * WLIGHT_Z;
        c->wly[i] = m[3 * i + 1] * WLIGHT_Y;
    }
}

/* BUILD 646l: sin and cos of a in [-pi, pi] (Taylor to a^9 / a^8 on
 * [-pi/2, pi/2], under 3e-5 off) */
static void sincos_pi(float a, float *sn, float *cs)
{
    float a2, flip = 1.0f;
    if (a > K(0x3FC90FDBu)) {               /* pi/2 */
        a = K(0x40490FDBu) - a; flip = -1.0f;
    } else if (a < K(0xBFC90FDBu)) {
        a = -K(0x40490FDBu) - a; flip = -1.0f;
    }
    a2 = a * a;
    *sn = a * (1.0f - a2 * K(0x3E2AAAABu) * (1.0f - a2 * K(0x3D4CCCCDu)
               * (1.0f - a2 * K(0x3CC30C31u) * (1.0f - a2 * K(0x3C638E39u)))));
    *cs = flip * (1.0f - a2 * K(0x3F000000u) * (1.0f - a2 * K(0x3DAAAAABu)
                  * (1.0f - a2 * K(0x3D088889u) * (1.0f - a2 * K(0x3C924925u)))));
}

#define BATTLE_FACING  0xBE12D8u    /* x86: s16 facing of actor i, + i * 0x1AEC */
#define BATTLE_STRIDE  0x1AECu

/* BUILD 645f: after the blob's draw call. The blob is semi-transparent, so
 * field_78 queued it (entry recorded by the stub at its allocation) instead
 * of drawing it: keep the entry's matrix and mark the entry so draw_deferred
 * skips it. An entry with no matrix of its own is left alone (blob drawn,
 * no shadow) -- its matrix would be whatever is current at draw time.
 *
 * BUILD 646l: also the actor's facing. 0x42F3E8 builds the blob's matrix as
 * scale . rotY(facing) . C (x86 0x42FE23..0x4300BC; C = the battle camera,
 * actor +0xDFC, which is also the game's view, as FFNx reads it), so the
 * disc's x and z axes turn with the actor. Undoing rotY gives the battle
 * world's axes in the space the parts are drawn in, for FFNx's light. The
 * actor index is 0x42F3E8's first argument, [ebp + 8]. */
__attribute__((section(".text.sh_blob_done")))
void sh_blob_done(Ctx *c, u8 *x86, u8 **pages)
{
    u8 *e = c->entry;
    int i;
    u32 ebp, idx;
    s32 f;
    c->bok = 0;
    if (x86 && pages) {
        c->pages = pages;
        ebp = *(u32 *)(x86 + X86_EBP);
        if (rdg32(c, ebp + 8, &idx) && idx < 16
            && rdg16(c, BATTLE_FACING + idx * BATTLE_STRIDE, &f)) {
            /* angle = facing * 360/4096 degrees (x86 0x42FE5C, 0x67A254) */
            f &= 0xFFF;
            if (f >= 0x800)
                f -= 0x1000;
            sincos_pi((float)f * K(0x3AC90FDBu), &c->bsin, &c->bcos);
            c->bok = 1;
        }
    }
    c->entry = 0;
    c->layer = 0;
    c->field = 0;
    if (!e || *(u32 *)(e + DE_HASMAT) != 1)
        return;
    for (i = 0; i < 16; i++)
        c->blob[i] = rd(e, DE_MATRIX + 4 * i);
    *(u32 *)(e + DE_HASMAT) = DE_SKIP;
    c->have_blob = 1;
}

/* before the shadow's draw: the flattened matrix and the shadow state */
__attribute__((section(".text.sh_begin")))
u32 sh_begin(Ctx *c, u8 *rs, u8 *rb, u8 *dev, u64 *fast)
{
    float *b = c->blob, *m = c->model, f[16], o[16];
    float nx, ny, nz, k, lx, ly, lz, ln, d, r, cm[16], e[4], t[3], z0, z1, w;
    int i, j, nofloor = 0;

    for (i = 0; i < 16; i++)
        m[i] = rd(rs, RS_MODEL + 4 * i);
    for (i = 0; i < RB_BYTES / 4; i++)
        c->rb[i] = *(u32 *)(rb + RB_LO + 4 * i);
    if (c->need_r) {
        /* BUILD 646j: the field floor, at the model's first shadowed part */
        c->need_r = 0;
        /* BUILD 667d: a field model's floor is built in sh_pre (it decides
         * the model mark before the part is drawn); only the world's here */
        if (c->field != FIELD_WORLD || !world_floor(c, m, b)) {
            c->have_blob = 0;              /* the rest of this model: none */
            lx = 0; nofloor = 1;
            if (fast)
                return 1;
            goto plain;
        }
    }
    c->blend = c->marked ? c->morig : *(s32 *)(rs + RS_BLEND);

    /* floor normal: the disc's local y axis; a point on it: its origin */
    nx = b[4]; ny = b[5]; nz = b[6];
    k = nx * nx + ny * ny + nz * nz;
    if (!(k > K(0x2B8CBCCCu))) {
        lx = 0; nofloor = 1;
        if (fast)
            return 1;                      /* no floor: no shadow */
        goto plain;
    }
    k = isqrt(k); nx *= k; ny *= k; nz *= k;
    r = __builtin_sqrtf(b[0] * b[0] + b[1] * b[1] + b[2] * b[2]);

    /* light: along the normal, slanted along the disc's x axis */
    lx = nx + TILT * b[0] / (r > K(0x358637BDu) ? r : 1.0f);
    ly = ny + TILT * b[1] / (r > K(0x358637BDu) ? r : 1.0f);
    lz = nz + TILT * b[2] / (r > K(0x358637BDu) ? r : 1.0f);
    if (!c->field && c->bok) {
        /* BUILD 646l: FFNx's battle light, fixed in the battle world (one
         * direction for every actor, 60 degrees up): towards
         * (0.433, -0.866, -0.25), battle Y pointing down. The disc rows are
         * sx.(c, 0, -s).C, (0, 1, 0).C, sz.(s, 0, c).C (rotY(facing)), so
         * world X = c.x^ + s.z^, Z = -s.x^ + c.z^, Y = the normal. */
        float q = r > K(0x358637BDu) ? 1.0f / r : 0.0f, zx, zy, zz, wx, wy, wz;
        float x0 = b[0] * q, x1 = b[1] * q, x2 = b[2] * q;
        k = b[8] * b[8] + b[9] * b[9] + b[10] * b[10];
        q = k > K(0x2B8CBCCCu) ? isqrt(k) : 0.0f;
        zx = b[8] * q; zy = b[9] * q; zz = b[10] * q;
        wx = c->bcos * x0 + c->bsin * zx;
        wy = c->bcos * x1 + c->bsin * zy;
        wz = c->bcos * x2 + c->bsin * zz;
        zx = -c->bsin * x0 + c->bcos * zx;
        zy = -c->bsin * x1 + c->bcos * zy;
        zz = -c->bsin * x2 + c->bcos * zz;
        lx = FLIGHT_X * wx - FLIGHT_Z * nx - K(0x3E800000u) * zx;
        ly = FLIGHT_X * wy - FLIGHT_Z * ny - K(0x3E800000u) * zy;
        lz = FLIGHT_X * wz - FLIGHT_Z * nz - K(0x3E800000u) * zz;
    }
    if (c->field) {
        /* BUILD 646j: FFNx's light, fixed in the world (field_floor) */
        lx = c->light[0]; ly = c->light[1]; lz = c->light[2];
    }
    ln = lx * nx + ly * ny + lz * nz;
    if (ln < K(0x3E4CCCCDu) && ln > K(0xBE4CCCCDu)) {
        lx = nx; ly = ny; lz = nz; ln = 1.0f;
    }
    d = b[12] * nx + b[13] * ny + b[14] * nz;          /* plane: p.n = d */

    /* BUILD 645g: a depth bias that does not move the picture. Battle floors
     * are not quite flat, and a shadow lying exactly on the blob's plane
     * dipped under every bump (the "noise"). So the whole shadow is moved
     * toward the eye, along the ray through the actor's floor point: on
     * screen it stays where it was, in depth it clears the bumps.
     * The eye is the point clip.x = clip.y = clip.w = 0 of view . proj (the
     * 4D cross product of those three columns); for an orthographic
     * camera that is a direction. Either way the sign is the one that brings
     * the floor point nearer in depth. */
    for (i = 0; i < 4; i++)
        for (j = 0; j < 4; j++)
            cm[4 * i + j] = rd(rs, RS_VIEW + 16 * i + 0) * rd(rs, RS_PROJ + 0 + 4 * j)
                          + rd(rs, RS_VIEW + 16 * i + 4) * rd(rs, RS_PROJ + 16 + 4 * j)
                          + rd(rs, RS_VIEW + 16 * i + 8) * rd(rs, RS_PROJ + 32 + 4 * j)
                          + rd(rs, RS_VIEW + 16 * i + 12) * rd(rs, RS_PROJ + 48 + 4 * j);
#define C3(a, b, c) (cm[4*(a)+0] * (cm[4*(b)+1] * cm[4*(c)+3] - cm[4*(b)+3] * cm[4*(c)+1]) \
                   - cm[4*(a)+1] * (cm[4*(b)+0] * cm[4*(c)+3] - cm[4*(b)+3] * cm[4*(c)+0]) \
                   + cm[4*(a)+3] * (cm[4*(b)+0] * cm[4*(c)+1] - cm[4*(b)+1] * cm[4*(c)+0]))
    e[0] = C3(1, 2, 3); e[1] = -C3(0, 2, 3); e[2] = C3(0, 1, 3); e[3] = -C3(0, 1, 2);
#undef C3
    /* the minors scale with the matrices (tiny for an orthographic one) */
    k = 0.0f;
    for (i = 0; i < 4; i++) {
        w = e[i] < 0 ? -e[i] : e[i];
        if (w > k) k = w;
    }
    if (!(k > 0.0f)) k = 1.0f;
    k = 1.0f / k;
    for (i = 0; i < 4; i++) e[i] *= k;
    w = e[3] < 0 ? -e[3] : e[3];
    k = __builtin_sqrtf(e[0] * e[0] + e[1] * e[1] + e[2] * e[2]);
    if (w > K(0x3727C5ACu) * k) {           /* 1e-5: a perspective eye */
        w = 1.0f / e[3];
        t[0] = e[0] * w - b[12]; t[1] = e[1] * w - b[13]; t[2] = e[2] * w - b[14];
    } else {
        t[0] = e[0]; t[1] = e[1]; t[2] = e[2];
    }
    k = t[0] * t[0] + t[1] * t[1] + t[2] * t[2];
    /* BUILD 645h: each later draw of this actor's shadow sits a step
     * (1/64 of the bias) farther back, so where it overlaps a part already
     * drawn it fails the depth test outright instead of tying with it --
     * equal depths from different triangles round differently, and every
     * pixel that rounded nearer was darkened twice (the speckle). */
    j = c->layer < 63 ? (int)c->layer : 63;
    c->layer++;
    if (k > K(0x2B8CBCCCu)) {
        k = c->bias * r * (1.0f - (float)j * K(0x3C800000u)) * isqrt(k);
        t[0] *= k; t[1] *= k; t[2] *= k;
        /* nearer in depth? (ndc z of the floor point, before and after) */
        w = b[12] * cm[3] + b[13] * cm[7] + b[14] * cm[11] + cm[15];
        z0 = (b[12] * cm[2] + b[13] * cm[6] + b[14] * cm[10] + cm[14]) / w;
        w = (b[12] + t[0]) * cm[3] + (b[13] + t[1]) * cm[7] + (b[14] + t[2]) * cm[11] + cm[15];
        z1 = ((b[12] + t[0]) * cm[2] + (b[13] + t[1]) * cm[6]
              + (b[14] + t[2]) * cm[10] + cm[14]) / w;
        if (z1 > z0) { t[0] = -t[0]; t[1] = -t[1]; t[2] = -t[2]; }
    } else {
        t[0] = t[1] = t[2] = 0.0f;
    }

    /* F (row vectors): p' = p - L ((p.n - d) / (L.n)) + t */
    ln = 1.0f / ln;
    f[0] = 1.0f - nx * lx * ln; f[1] = -nx * ly * ln; f[2] = -nx * lz * ln;
    f[4] = -ny * lx * ln; f[5] = 1.0f - ny * ly * ln; f[6] = -ny * lz * ln;
    f[8] = -nz * lx * ln; f[9] = -nz * ly * ln; f[10] = 1.0f - nz * lz * ln;
    f[12] = lx * d * ln + t[0];
    f[13] = ly * d * ln + t[1];
    f[14] = lz * d * ln + t[2];
    f[3] = 0.0f; f[7] = 0.0f; f[11] = 0.0f; f[15] = 1.0f;
    /* BUILD 657: a field model off its floor (ladders, jumps) keeps its
     * shadow UNDER it, as FFNx's does: undo the slant's sideways drift for
     * the height the model has left its floor by */
    if (c->field == 1 && c->gap > 0.0f) {
        f[12] += c->gap * (lx * ln - nx);
        f[13] += c->gap * (ly * ln - ny);
        f[14] += c->gap * (lz * ln - nz);
    }
    /* model' = model . F */
    for (i = 0; i < 4; i++)
        for (j = 0; j < 4; j++)
            o[4 * i + j] = m[4 * i + 0] * f[0 + j] + m[4 * i + 1] * f[4 + j]
                         + m[4 * i + 2] * f[8 + j] + m[4 * i + 3] * f[12 + j];
    if (fast) {
        /* BUILD 645q: called from fast_stub, right after the draw helper
         * issued this part's own draw: its vertices, indices, texture,
         * program and attributes are all still bound. Only the uniform block
         * (blend mode 5 and model' . view . proj), the blend and the depth
         * write change, then the same draw call is issued again -- none of
         * the helper's per-draw setup or vertex copies run a second time. */
        u8 *obj = **(u8 ***)dev, *ring, *buf;
        float ubo[64];
        s32 *cnt, fi;
        u64 size;
        for (i = 0; i < 64; i++)
            ubo[i] = 0.0f;
        *(s32 *)&ubo[0] = c->field == 1 ? FIELD_BLEND : SHADOW_BLEND;   /* 647d: world 0.50 */
        if (c->field == 1) {            /* BUILD 655: the fade, for lmain_vv */
            *(s32 *)&ubo[1] = (s32)c->fade;
            *(s32 *)&ubo[2] = (s32)FADE_MAGIC;
        }
        if (c->field == FIELD_WORLD)
            *(s32 *)&ubo[3] = WS_SPHERE_MAGIC;  /* 647: the world-map depth (lmain_vv) */
        for (i = 0; i < 4; i++)
            for (j = 0; j < 4; j++)
                ubo[4 + 4 * i + j] = o[4 * i + 0] * cm[0 + j] + o[4 * i + 1] * cm[4 + j]
                                   + o[4 * i + 2] * cm[8 + j] + o[4 * i + 3] * cm[12 + j];
        /* BUILD 670: this block bypasses the draw join where ff7nx_subcam
         * moves the field scene by the scripted camera's fraction, so the
         * shadow takes the same clip shift as the part it belongs to (the
         * scene, or one of its deferred groups; the shift is set) -- it
         * wobbled against its model while the camera panned (hardware). */
        if (c->field == 1 && c->cam
            && (*(u32 *)(c->cam + CAM_FLAG) | *(u32 *)(c->cam + CAM_DEF))
            && *(u32 *)(c->cam + CAM_TSET)) {
            float tx = *(float *)(c->cam + CAM_T), ty = *(float *)(c->cam + CAM_T + 4);
            for (i = 0; i < 4; i++) {
                ubo[4 + 4 * i + 0] += tx * ubo[4 + 4 * i + 3];
                ubo[4 + 4 * i + 1] += ty * ubo[4 + 4 * i + 3];
            }
        }
        ((fn5)VCALL(obj, DEV_DEPTH))(obj, 0, 1, 0, 0);          /* LESS, no write */
        ((fn5)VCALL(obj, DEV_BLEND))(obj, 0, BF_DC, BE_ADD, BF_1MDA);
        /* the uniform ring, as module +0x570 does it */
        ring = *(u8 **)fast[5];
        fi = *(s32 *)fast[6];
        cnt = (s32 *)(ring + 0x4030 + 4 * (s64)fi);
        if (*cnt >= 0x100)
            *cnt = 0;
        buf = *(u8 **)(ring + ((s64)fi << 11) + 8 * (s64)*cnt + 0x10);
        size = ((fn_size)VCALL(buf, 0x58))(buf);
        if (size > sizeof(ubo))
            size = sizeof(ubo);
        ((fn_write)VCALL(buf, 0x38))(buf, ubo, size, 0);
        (*cnt)++;
        ((fn8)VCALL(obj, DEV_UBO))(obj, 0, 1, buf, 0, 0, 0x50, 0);
        /* BUILD 667: no face culling. The flattened part is one layer on the
         * floor and the alpha mark (645o) already darkens each pixel once;
         * culling only dropped the faces the flattening turned around --
         * Biggs' hair (one-sided strands) left a triangular hole. */
        ((fn5)VCALL(obj, DEV_CULL))(obj, 0, c->rb[(RB_FRONT - RB_LO) / 4], 0, 0);
        ((fn6)VCALL(obj, DEV_DRAW))(obj, fast[0], fast[1], fast[2], fast[3], fast[4]);
        ((fn5)VCALL(obj, DEV_CULL))(obj, c->rb[(RB_CULL - RB_LO) / 4],
                                    c->rb[(RB_FRONT - RB_LO) / 4], 0, 0);
        /* the state back as the block says (the next helper call re-applies
         * it anyway; this keeps any other path honest) */
        ((fn5)VCALL(obj, DEV_DEPTH))(obj, c->rb[(RB_DWRITE - RB_LO) / 4],
                                     c->rb[(RB_DFUNC - RB_LO) / 4],
                                     c->rb[(RB_STENCIL - RB_LO) / 4],
                                     c->rb[(RB_SFUNC - RB_LO) / 4]);
        ((fn5)VCALL(obj, DEV_BLEND))(obj, 0, c->rb[(RB_BSRC - RB_LO) / 4],
                                     c->rb[(RB_BEQ - RB_LO) / 4],
                                     c->rb[(RB_BDST - RB_LO) / 4]);
        c->shadows++;
        return 1;
    }
    for (i = 0; i < 16; i++)
        *(float *)(rs + RS_MODEL + 4 * i) = o[i];
plain:
    if (nofloor) {
        /* no floor (a degenerate blob or field placement): collapse the
         * shadow draw to a point, so it draws nothing rather than a black
         * copy of the model (646b) */
        for (i = 0; i < 16; i++)
            *(float *)(rs + RS_MODEL + 4 * i) = 0.0f;
    }
    *(s32 *)(rs + RS_BLEND) = c->field == 1 ? FIELD_BLEND : SHADOW_BLEND;
    /* BUILD 645o: each floor pixel darkened once, in ONE draw, through the
     * framebuffer's alpha (there is no stencil buffer). Every opaque draw
     * leaves alpha 1 (the game writes all four channels, blending off); the
     * shader gives the shadow colour s = (0.6, 0.6, 0.6, 0.6). Blend ADD,
     * Fs = DST_COLOR, Fd = 1 - dst alpha:
     *   floor (a = 1):        rgb = s.rgb * rgb + 0         = rgb x 0.60
     *                         a   = s.a * 1 + 1 * 0         = 0.60
     *   shadowed (a = 0.60):  rgb = 0.6 rgb + 0.4 rgb       = rgb (unchanged)
     *                         a   = 0.6 * 0.6 + 0.6 * 0.4   = 0.60
     * so the first shadow fragment on a pixel darkens and marks it, and every
     * later one -- a part's other face, an overlapping part, another actor's
     * shadow -- leaves it as it is, in any order. (645n did the same in two
     * draws, which halved the frame rate.) No depth write: the test against
     * the floor (with 645g's bias) is all the shadow needs, and it can then
     * never hide anything drawn after it. */
    *(u32 *)(rb + RB_DWRITE) = 0;
    *(u32 *)(rb + RB_CULL) = 0;            /* 667: no culling (see the fast path) */
    *(u32 *)(rb + RB_DFUNC) = 1;           /* LESS */
    *(u32 *)(rb + RB_STENCIL) = 0;
    *(u32 *)(rb + RB_CMASK) |= 0xFu;       /* all four channels */
    *(u32 *)(rb + RB_BSRC) = BF_DC;
    *(u32 *)(rb + RB_BEQ) = BE_ADD;
    *(u32 *)(rb + RB_BDST) = BF_1MDA;
    c->shadows++;
    (void)lx; (void)dev;
    return 0;
}

/* after the shadow's draw: everything back as the port set it */
__attribute__((section(".text.sh_end")))
void sh_end(Ctx *c, u8 *rs, u8 *rb, u8 *dev)
{
    int i;
    (void)dev;
    for (i = 0; i < 16; i++)
        *(float *)(rs + RS_MODEL + 4 * i) = c->model[i];
    for (i = 0; i < RB_BYTES / 4; i++)
        *(u32 *)(rb + RB_LO + 4 * i) = c->rb[i];
    *(s32 *)(rs + RS_BLEND) = c->blend;
}
