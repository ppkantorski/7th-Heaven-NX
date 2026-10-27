/* BUILD 675: FFNx's per-vertex game lighting for field (and every other)
 * model, in place of the PC version's CPU lighting.
 *
 * THE PC FORMULA (x86 0x68E92D -> 0x68DD1E, ARM native +0x10F23C0; and
 * 0x68DE75 / 0x68DBF4 through 0x66CFE8, ARM +0x7E40), per channel:
 *     I = max(amb, amb + M . n)      M = (C x D) x bone^T  (light +0xF8)
 * C (light +0x78): row = channel (R,G,B), column = light 1..3 colour;
 * D (light +0x38): row i = light i's direction ("point"); amb = M's 4th
 * column (light +0x24, the global light). The dot products are SIGNED and
 * summed before anything is clamped, so a light behind a surface cancels a
 * light in front of it: in del3 (lights 1 and 2 nearly opposite) half of a
 * model's vertices sit on the flat `amb` floor, with a hard crease where the
 * sum crosses it -- the crease sweeps over a walking model (the "shimmer").
 *
 * FFNX (FFNx.vert, game_lighting = 1, its default, "closely match the PSX"):
 *     I = amb + sum_i C[ch][i] * saturate(n . L_i)^2
 * each light clamped on its own, squared, added to the ambient.
 *
 * 675b: the field lights a model's parts in its own routines (0x64CA65,
 * 0x64C382, through 0x685028), all parts in a row, long after each part's
 * 0x69C3D7 -- 675 kept only the LAST part set up, so every other part fell
 * back to stock (hardware: "looks about the same"). Each light now carries
 * its own record, in its ramp (+0x1A4), which only a ramp light (+0x17C ==
 * 2, flags mode 2/3) reads, and which 0x69C8DA/0x69C91A rebuild whole
 * whenever a light becomes one; a ramp light is never written.
 *
 * 675c: hardware probe (675p): del2's models and del3's NPCs took the new
 * path, del3's CLOUD did not -- his light has a ramp sub-mode (+0x17C 2/3,
 * set by the field script), so 675b would not write its record into the
 * ramp. The records now live in our own table (BSS, 128 slots keyed by the
 * light's +0xF8 address): no light is refused, nothing in a light is
 * written. Only mode-1 lights (flags & 3 == 1) are lit here (675d: every
 * light is set up -- the field's scripted light becomes mode 1 after it).
 *
 * Here: lt_setup runs as each body part's light is set up (x86 0x69C3D7,
 * from 0x69C69F; its bone matrix is on the guest stack) and keeps
 * DM = D x bone^T (the three light directions in the part's space, exactly
 * as M is built) and C. lt_vtx replaces the native per-vertex routine;
 * lt_mvfix corrects 0x66CFE8's result for the two scripted-colour
 * variants. A matrix that is not the one set up last (another light mode,
 * a non-model caller) takes the stock path unchanged. */
/* (679: one section, .text.lt -- have() and intens() are shared, not
 * inlined into each entry; the build reads the entries from the symbols.
 * The verts/fixes/fallbacks counters are no longer kept; 783: nor setups,
 * for the room the battle test needs.)
 *
 * BUILD 679: EACH PART LIT WITH ITS OWN BONE IN THE FIELD'S RELIGHT.
 *
 * del3's per-step pulse (hardware: shadows off -- stays; light flat -- gone;
 * "cyclical, the same pattern with each leg"): KAWAI sub-op 9 (x86
 * 0x64A541), every frame, draws the model with its light out of mode 1
 * (draw_3d_model 0x6840DA: each part's light set up from ITS bone,
 * 0x69C69F -> 0x69C3D7, then 0x68E92D, which lights nothing in mode 0),
 * then makes the light mode 1 and relights every part through 0x685028 ->
 * 0x64CA65 / 0x64C382. Those take the light's +0xF8 matrix as it stands --
 * built from the LAST part set up -- so every part (face, hair) was lit as
 * if it were turned like the last limb drawn, and its shading swung with
 * that leg. PC FFNx never shows it: its shader lights each part at draw
 * time with that part's own matrices and ignores the CPU colours.
 *
 * Fix: at 0x68E92D's entry (polygon set, light), the light's record from
 * the setup just before it (marked fresh by lt_setup) is bound to that
 * polygon set (Part table, 1024 slots, 4 probed, keyed by the set). At 0x64CA65's and
 * 0x64C382's entries the set's own record becomes the cache, with the
 * light's current colours, so lt_vtx / lt_mvfix light each part with its
 * own bone -- FFNx's intensity, as 675d. A set with no record keeps the
 * stock path. */
typedef unsigned int u32;
typedef unsigned char u8;
typedef void *(*g2h_t)(u32);
typedef u32 (*orig5_t)(u32, u32, u32, u32, u32);

typedef struct {
    u32 m;          /* cache: guest address of the light's +0xF8 matrix, 0 = none */
    u32 light;
    float dm[9];    /* light directions in the part's space, row = light */
    float c[9];     /* colour, row = channel R G B, column = light */
    u32 setups, verts, fixes, fallbacks;
} Lt;

/* one record per light, in our own table (675c) */
#define LT_SLOTS 128
typedef struct {
    u32 m;          /* key: the light's +0xF8 address (guest), 0 = empty */
    u32 fresh;      /* 679: set up since its last 0x68E92D */
    float dm[9];
    float c[9];
} Rec;

#define LT_PARTS 1024
typedef struct {
    u32 ps;         /* key: the polygon set (guest), 0 = empty */
    u32 light;
    float dm[9];
} Part;

typedef struct {
    Lt hdr;
    Rec rec[LT_SLOTS];
    Part part[LT_PARTS];
} LtAll;

/* the set's slot among 4 probed from its hash (10 bits: 1024 slots): its
 * own, else (bind) an empty one, else the first; NULL on lookup if none */
static inline __attribute__((always_inline)) Part *pslot(Lt *s, u32 ps, int bind)
{
    u32 h = ((ps >> 2) * 2654435761u) >> 22;
    Part *base = ((LtAll *)s)->part, *empty = 0;
    int i;
    for (i = 0; i < 4; i++) {
        Part *p = &base[(h + i) & (LT_PARTS - 1)];
        if (p->ps == ps)
            return p;
        if (!p->ps && !empty)
            empty = p;
    }
    if (!bind)
        return 0;
    return empty ? empty : &base[h];
}

static inline __attribute__((always_inline)) Rec *slot(Lt *s, u32 m)
{
    u32 h = (m >> 3) * 2654435761u;
    return &((LtAll *)s)->rec[h >> 25];             /* 7 bits: 128 slots */
}

#define LT_FLAGS 0x00
#define LT_D     0x38
#define LT_C     0x78
#define LT_B8    0xB8
#define LT_F8    0xF8

/* a float constant without a literal pool (the blob has no data) -- as shadows.c */
static inline __attribute__((always_inline)) float K(u32 bits)
{
    float f;
    __asm__("fmov %s0, %w1" : "=w"(f) : "r"(bits));
    return f;
}

static inline __attribute__((always_inline)) float sat2(float d) { return d > 0.0f ? d * d : 0.0f; }

static __attribute__((noinline, section(".text.lt"))) void intens(const Lt *s, const float *M, const float *n, float *I)
{
    float t[3];
    int i, ch;
    for (i = 0; i < 3; i++)
        t[i] = sat2(s->dm[3 * i] * n[0] + s->dm[3 * i + 1] * n[1] + s->dm[3 * i + 2] * n[2]);
    for (ch = 0; ch < 3; ch++)
        I[ch] = M[4 * ch + 3] + s->c[3 * ch] * t[0] + s->c[3 * ch + 1] * t[1]
              + s->c[3 * ch + 2] * t[2];
}


/* BUILD 783: battle keeps the game's own lighting. FF7's _mode word
 * (guest 0xCBF9DC, FFNx's common_externals._mode) is 2 in battle. The FFNx
 * intensity was built and hardware-checked for field models (del3, 675-679);
 * in battle it left whole parts of a summon out of its light (Shiva's
 * reveal, hardware 10-22: arm, hand, hair strands dark while the body is
 * white, and which parts changes with the effect's 15 Hz tick). In battle
 * have() answers 0, so both lighting calls (lt_vtx, lt_mvfix) take the stock
 * path; setup and the part table only fill our own tables. */
#define FF7_MODE   0xCBF9DCu
#define MODE_BATTLE 2

/* make s's cache hold the record of the light whose +0xF8 matrix is m:
 * 1 if it has one (set up as mode 1), 0 if not (stock path) */
static __attribute__((noinline, section(".text.lt"))) int have(Lt *s, u32 m, g2h_t g2h)
{
    const Rec *r;
    int i;
    if (!m || *(const unsigned short *)g2h(FF7_MODE) == MODE_BATTLE)
        return 0;
    if (m == s->m)
        return 1;
    r = slot(s, m);
    if (r->m != m || (*(const u32 *)g2h(m - LT_F8 + LT_FLAGS) & 3) != 1)
        return 0;
    for (i = 0; i < 9; i++) {
        s->dm[i] = r->dm[i];
        s->c[i] = r->c[i];
    }
    s->light = m - LT_F8;
    s->m = m;
    return 1;
}

/* before 0x69C3D7(light+0xB8, bone, light+0x24, light+0xF8): esp = the guest
 * stack pointer at the call (the return slot), arguments at esp+4.. */
__attribute__((section(".text.lt")))
void lt_setup(Lt *s, u32 esp, g2h_t g2h)
{
    u32 a1, bone, a4, light, flags;
    Rec *r;
    const float *D, *B, *C;
    int i, c;
    s->m = 0;
    a1 = *(u32 *)g2h(esp + 4);
    bone = *(u32 *)g2h(esp + 8);
    a4 = *(u32 *)g2h(esp + 16);
    if (!a1 || !bone || !a4)
        return;
    light = a1 - LT_B8;
    if (a4 != light + LT_F8)
        return;
    /* 675d: no mode test here. The field's scripted light (x86 0x64A1E9..)
     * sets a model's light up through 0x69C69F BEFORE it makes it mode 1
     * (0x64A281: flags |= 1) and lights the model right after (0x685028 ->
     * 0x64CA65): del3's Cloud. The record is kept for any light (it is only
     * our table); the mode is tested where it is used (have()). */
    flags = *(u32 *)g2h(light + LT_FLAGS);
    D = (const float *)g2h(light + LT_D);
    C = (const float *)g2h(light + LT_C);
    B = (const float *)g2h(bone);
    for (i = 0; i < 3; i++)             /* as 0x67A5AF: out[r][c] = row r . bone row c */
        for (c = 0; c < 3; c++)
            s->dm[3 * i + c] = D[4 * i] * B[4 * c] + D[4 * i + 1] * B[4 * c + 1]
                             + D[4 * i + 2] * B[4 * c + 2];
    for (i = 0; i < 3; i++)
        for (c = 0; c < 3; c++)
            s->c[3 * i + c] = C[4 * i + c];
    s->light = light;
    s->m = (flags & 3) == 1 ? a4 : 0;   /* the cache only holds a mode-1 light */
    r = slot(s, a4);
    for (i = 0; i < 9; i++) {
        r->dm[i] = s->dm[i];
        r->c[i] = s->c[i];
    }
    r->m = a4;
    r->fresh = 1;
}

/* in place of the native +0x10F23C0(M, normal, vcolour floats, alpha src,
 * out bytes) -- same arguments, same result, FFNx's intensity */
__attribute__((section(".text.lt")))
u32 lt_vtx(u32 m, u32 n, u32 vc, u32 al, u32 out, Lt *s, g2h_t g2h, orig5_t orig)
{
    float I[3], p;
    const float *M, *N, *V;
    u8 *O;
    int ch;
    if (!n || !vc || !al || !out || !have(s, m, g2h)) {
            return orig(m, n, vc, al, out);
    }
    M = (const float *)g2h(m);
    N = (const float *)g2h(n);
    V = (const float *)g2h(vc);
    intens(s, M, N, I);
    O = (u8 *)g2h(out);
    for (ch = 0; ch < 3; ch++) {        /* row 0 (R) -> byte 2 and vcolour +8, ... */
        p = V[2 - ch] * I[ch];
        if (!(p < K(0x437F0000u)))          /* 255.0 */
            O[2 - ch] = 0xFF;
        else
            O[2 - ch] = (u8)(int)p;
    }
    O[3] = *((u8 *)g2h(al) + 3);
    return out;
}

/* BUILD 811: the world map's terrain keeps the PC light. The game's 5x5
 * window lights each terrain triangle in 0x75F68C with three calls of
 * 0x68DAE1 on the world's light, [0xE360F4]+0xF8 -- the light the world
 * map's models are set up with, so have() found its record and gave the
 * terrain FFNx's intensity. worldfar (BUILD 606) draws the same window
 * itself, with the PC formula (worldfar.c light()), on every frame whose
 * ring-3 cells are all cached, and hands it back to the game on any frame
 * they are not: the two intensities differ by ~1.2x (wmdupprobe: R/G/B
 * 0xFB/DC/C6 against 0xCF/B8/A7 on the same ground), and the square around
 * the player brightened for that frame.
 * 0x68DAE1's frame at its 0x66CFE8 call: its saved ebp -- the caller's --
 * is at esp+0x38 (ebp = esp+0x38: 0x1C locals, ebx/esi/edi, three
 * arguments, the return slot). The translated `call` does NOT store a
 * return address (it only moves esp), so the caller is told by the normal
 * it passes, a local of 0x75F68C's frame at ebp-0x40 / -0x60 / -0x7C (one
 * per triangle vertex), and that n is the normal 0x66CFE8 got. */
static inline __attribute__((always_inline)) int wm_terrain(u32 esp, u32 n, g2h_t g2h)
{
    u32 d = *(u32 *)g2h(esp + 0x38) - n;
    return d == 0x40u || d == 0x60u || d == 0x7Cu;
}

/* after +0x7E40 (0x66CFE8(M, n, out): out = M . n + M's 4th column) from
 * the two scripted-colour variants: put FFNx's intensity in `out` instead.
 * esp = the guest stack pointer at the call (the return slot, which the
 * callee popped); the arguments are still at esp+4.. */
__attribute__((section(".text.lt")))
void lt_mvfix(Lt *s, u32 esp, g2h_t g2h)
{
    float I[3];
    float *O;
    u32 m, n, out;
    m = *(u32 *)g2h(esp + 4);
    n = *(u32 *)g2h(esp + 8);
    out = *(u32 *)g2h(esp + 12);
    if (!n || !out || wm_terrain(esp, n, g2h) || !have(s, m, g2h))
        return;
    intens(s, (const float *)g2h(m), (const float *)g2h(n), I);
    O = (float *)g2h(out);
    O[0] = I[0];
    O[1] = I[1];
    O[2] = I[2];
}

/* 679: at the entry of 0x68E92D (kind 0) or of 0x64CA65 / 0x64C382 (kind
 * 1): esp = the guest stack pointer there (the return slot), the polygon
 * set at esp+4, the light at esp+8. */
__attribute__((section(".text.lt")))
void lt_entry(Lt *s, u32 esp, g2h_t g2h, u32 kind)
{
    u32 ps, light, key;
    Rec *r;
    Part *p;
    const float *C;
    int i, c;
    ps = *(u32 *)g2h(esp + 4);
    light = *(u32 *)g2h(esp + 8);
    if (!ps || !light)
        return;
    key = light + LT_F8;
    p = pslot(s, ps, kind == 0);
    if (kind == 0) {
        r = slot(s, key);
        if (r->m != key || !r->fresh)
            return;
        r->fresh = 0;
        p->ps = ps;
        p->light = light;
        for (i = 0; i < 9; i++)
            p->dm[i] = r->dm[i];
        return;
    }
    if (!p || p->light != light)
        return;
    C = (const float *)g2h(light + LT_C);
    for (i = 0; i < 9; i++)
        s->dm[i] = p->dm[i];
    for (i = 0; i < 3; i++)
        for (c = 0; c < 3; c++)
            s->c[3 * i + c] = C[4 * i + c];
    s->light = light;
    s->m = (*(const u32 *)g2h(light + LT_FLAGS) & 3) == 1 ? key : 0;
}
