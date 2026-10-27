/* BUILD 792: x86 0x6465FB in native code -- the field background's
 * per-tile quad builder (ARM +0xA095B0 in the translation).
 *
 * For each of the 42 background layers with tiles (count at
 * [0xD00050] + layer * 0x1804), after 0x66E272 has made room in the layer's
 * vertex buffer, it writes four vertices (0x20 bytes each) per tile at
 * the buffer's cursor (+0x70) and advances it (+0x40); blend mode 2
 * (+0x1A) has its own offsets and no byte list (+0x78/+0x7C).
 *
 * This is the x86 function step by step, as the translation runs it: the
 * same reads and writes of guest memory, the frame (ebp, the locals, the
 * pushed arguments), the two calls made through their translated bodies,
 * the timer tick at the return, eax/ecx/edx and the flag bytes as the
 * last instructions leave them, and the x87 stack slot the last fld..fstp
 * used. Floating point as the translation does x87: each float widened to
 * double, the sums in double, one rounding to float at the fstp. Built
 * with -ffp-contract=off so nothing is fused.
 *
 * Guest memory goes through the module's own page table, read the way the
 * translator (+0x10FC3A0) reads it: a zero entry answers the fallback page,
 * the address 0 answers NULL. The table is re-read after every call.
 * tests/test_bgquads.py runs this and the translated body side by side. */
typedef unsigned int u32;
typedef unsigned short u16;
typedef unsigned char u8;
typedef unsigned long u64;
typedef void (*fn_t)(void);

typedef struct {
    u8 *ctx;
    u8 *const *const *tabp;     /* -> the page table pointer */
    u8 *const *dummyp;          /* -> the fallback page */
    u8 *x87;
    u8 *const *tab;
    u8 *dummy;
} G;

#define C_EAX 0x00
#define C_ECX 0x04
#define C_EDX 0x08
#define C_EBX 0x0C
#define C_ESP 0x10
#define C_EBP 0x14
#define C_ZF  0x23
#define C_SF  0x24
#define C_OF  0x26
#define X87_TOP 0x60

static inline __attribute__((always_inline)) u8 *H(const G *g, u32 a)
{
    u8 *p;
    if (!a)
        return 0;
    p = g->tab[a >> 12];
    return p ? p + (a & 0xFFF) : g->dummy;
}

static inline __attribute__((always_inline)) u32 R32(const G *g, u32 a) { return *(volatile u32 *)H(g, a); }
static inline __attribute__((always_inline)) u16 R16(const G *g, u32 a) { return *(volatile u16 *)H(g, a); }
static inline __attribute__((always_inline)) u8 R8(const G *g, u32 a) { return *(volatile u8 *)H(g, a); }
static inline __attribute__((always_inline)) void W32(const G *g, u32 a, u32 v) { *(volatile u32 *)H(g, a) = v; }
static inline __attribute__((always_inline)) void W8(const G *g, u32 a, u8 v) { *(volatile u8 *)H(g, a) = v; }

static inline __attribute__((always_inline)) u32 *CR(const G *g, u32 off) { return (u32 *)(g->ctx + off); }

static inline __attribute__((always_inline)) double D(u32 bits)
{
    union { u32 u; float f; } v;
    v.u = bits;
    return (double)v.f;
}

/* st(0) op m32 as the translation emits it: fadd/fmul d0, st0, m. The
   operand order decides which NaN comes through when both are NaNs, so it
   is fixed here rather than left to the compiler (+ and * commute). */
static inline __attribute__((always_inline)) double FA(double st, double m)
{
    double r;
    __asm__("fadd %d0, %d1, %d2" : "=w"(r) : "w"(st), "w"(m));
    return r;
}

static inline __attribute__((always_inline)) double FM(double st, double m)
{
    double r;
    __asm__("fmul %d0, %d1, %d2" : "=w"(r) : "w"(st), "w"(m));
    return r;
}

/* fstp dword: the double rounded to float; the slot the fld used keeps it */
static inline __attribute__((always_inline)) u32 FST(const G *g, double d)
{
    union { u32 u; float f; } v;
    u32 top = *(u32 *)(g->x87 + X87_TOP);
    *(volatile double *)(g->x87 + 8 * ((top - 1) & 7)) = d;
    v.f = (float)d;
    return v.u;
}

static inline __attribute__((always_inline)) void reload(G *g)
{
    g->tab = *g->tabp;
    g->dummy = *g->dummyp;
}

#define SPRITES  0xD00050u
#define LAYERS   0xCFFC70u
#define BASE     0xD00000u
#define COLOUR   0x906A70u
#define ONE      0x3F800000u
#define ALPHA    0xFF000000u

#define OBJ   R32(g, R32(g, LAYERS + L * 4) + 8)
#define COUNT ((int)R32(g, R32(g, SPRITES) + L * 0x1804u))

/* n words at guest p: one translation when they share a page */
static __attribute__((noinline, section(".text.bg"))) void store(const G *g, u32 p, const u32 *q, u32 n)
{
    u32 k;
    if ((p & 0xFFF) + 4 * n <= 0x1000 && g->tab[p >> 12]) {
        volatile u32 *h = (volatile u32 *)H(g, p);
        for (k = 0; k < n; k++)
            h[k] = q[k];
        return;
    }
    for (k = 0; k < n; k++)
        W32(g, p + 4 * k, q[k]);
}

/* the layer's constants: e4/e8 (ebp-4/-8) and the 16 offsets at
 * 0xD00010.. as doubles, the vertex colour */
typedef struct {
    double e4, e8, gl[16];
    u32 colour;
} K;

/* one tile's four vertices (x86 0x646856.. / 0x646CDE..); b = blend mode 2.
 * The sprite record's six words, then the 32 vertex words in the order
 * the x86 writes them; the last x87 result stays in the slot. */
static __attribute__((noinline, section(".text.bg"))) void tile(const G *g, u32 ebp, u32 L, u32 i,
                                                               const K *k, int b)
{
    u32 s = R32(g, SPRITES) + L * 0x1804u + i * 0x18u;
    u32 o = OBJ, p, q[32], v, j;
    u32 sx = R32(g, s + 4), sy = R32(g, s + 8), sz = R32(g, s + 0xC);
    double su = D(R32(g, s + 0x10)), sv = D(R32(g, s + 0x14));
    double dx = D(sx), dy = D(sy), d;
    /* 0xD00010.. index: (offset - 0x10) / 4 */
#define G4(off) k->gl[((off) - 0x10) >> 2]
    if (!b) {
        W8(g, R32(g, o + 0x78), R8(g, s + 0x18));
        W32(g, o + 0x7C, R32(g, s + 0x18));
    }
    p = R32(g, o + 0x70);
    for (j = 0; j < 4; j++) {
        q[8 * j + 2] = sz;
        q[8 * j + 3] = ONE;
        q[8 * j + 4] = k->colour;
        q[8 * j + 5] = ALPHA;
    }
    q[0] = sx;
    q[1] = sy;
    q[6] = FST(g, FA(su, b ? G4(0x20) : G4(0x10)));
    q[7] = FST(g, FA(sv, b ? G4(0x40) : G4(0x34)));
    q[8] = sx;
    q[9] = FST(g, FA(dy, k->e8));
    q[14] = FST(g, FA(su, b ? G4(0x24) : G4(0x14)));
    q[15] = FST(g, b ? FA(FA(sv, k->e4), G4(0x48)) : FA(FA(sv, G4(0x30)), k->e4));
    q[16] = FST(g, FA(dx, k->e8));
    q[17] = sy;
    q[22] = FST(g, FA(FA(su, k->e4), b ? G4(0x28) : G4(0x18)));
    q[23] = FST(g, FA(sv, b ? G4(0x44) : G4(0x3C)));
    q[24] = FST(g, FA(dx, k->e8));
    q[25] = FST(g, FA(dy, k->e8));
    q[30] = FST(g, b ? FA(FA(su, k->e4), G4(0x2C)) : FA(FA(su, G4(0x1C)), k->e4));
    d = b ? FA(FA(sv, k->e4), G4(0x4C)) : FA(FA(sv, G4(0x38)), k->e4);
    q[31] = FST(g, d);
    store(g, p, q, 32);
    W32(g, ebp - (b ? 0x28 : 0x18), p);
    W32(g, ebp - (b ? 0x2C : 0x1C), p + 0x20);
    W32(g, ebp - (b ? 0x30 : 0x20), p + 0x40);
    W32(g, ebp - (b ? 0x34 : 0x24), p + 0x60);
    v = R32(g, o + 0x70) + R32(g, o + 0x40);
    W32(g, o + 0x70, v);
    if (!b)
        W32(g, o + 0x78, R32(g, o + 0x78) + 1);
#undef G4
}

/* the tiles of layer L; returns the index the loop stopped at */
static __attribute__((noinline, section(".text.bg"))) u32 quads(const G *g, u32 ebp, u32 L, int b)
{
    K k;
    u32 i, j;
    k.e4 = D(R32(g, ebp - 4));
    k.e8 = D(R32(g, ebp - 8));
    for (j = 0; j < 16; j++)
        k.gl[j] = D(R32(g, BASE + 0x10 + 4 * j));
    k.colour = R32(g, COLOUR);
    for (i = 0;; i++) {
        W32(g, ebp - 0x14, i);
        if ((int)i >= COUNT)
            return i;
        tile(g, ebp, L, i, &k, b);
    }
}

/* a call through a translated body: the return slot below esp (not
 * written, as the translation does), the callee pops it */
static inline __attribute__((always_inline)) void call(G *g, fn_t f, u32 esp)
{
    *CR(g, C_ESP) = esp - 4;
    f();
    reload(g);
}

__attribute__((section(".text.bg")))
void bg_6465fb(u8 *ctx, u8 *const *const *tabp, u8 *const *dummyp, u8 *x87,
               fn_t get_state, fn_t alloc, fn_t tick)
{
    G gg, *g = &gg;
    u32 esp, ebp, st, v, L, o, cnt, zf;
    u32 eax, ecx, edx;
    g->ctx = ctx;
    g->tabp = tabp;
    g->dummyp = dummyp;
    g->x87 = x87;
    reload(g);
    /* push ebp; mov ebp, esp; sub esp, 0x34 */
    esp = *CR(g, C_ESP) - 4;
    W32(g, esp, *CR(g, C_EBP));
    ebp = esp;
    esp -= 0x34;
    *CR(g, C_EBP) = ebp;
    call(g, get_state, esp);                    /* 0x676578 */
    st = *CR(g, C_EAX);
    W32(g, ebp - 0xC, st);
    v = (R32(g, R32(g, ebp - 0xC) + 0xA90) != 0
         && R32(g, R32(g, ebp - 0xC) + 0xA98) == 0) ? 0xBAE00000u : 0;
    W32(g, BASE + 0x10, v); W32(g, BASE + 0x14, v);
    W32(g, BASE + 0x18, 0); W32(g, BASE + 0x1C, 0);
    W32(g, BASE + 0x34, v); W32(g, BASE + 0x30, v);
    W32(g, BASE + 0x3C, 0); W32(g, BASE + 0x38, 0);
    W32(g, BASE + 0x20, v); W32(g, BASE + 0x24, v);
    W32(g, BASE + 0x28, 0); W32(g, BASE + 0x2C, 0);
    W32(g, BASE + 0x40, v); W32(g, BASE + 0x48, v);
    W32(g, BASE + 0x44, 0); W32(g, BASE + 0x4C, 0);
    eax = *CR(g, C_EAX);
    ecx = *CR(g, C_ECX);
    edx = *CR(g, C_EDX);
    zf = 0;
    for (L = 0; L < 0x2A; L++) {
        W32(g, ebp - 0x10, L);
        eax = L * 0x1804u;
        ecx = R32(g, SPRITES);
        cnt = R32(g, ecx + eax);
        zf = cnt == 0;
        if ((int)cnt <= 0)
            goto next;
        o = R32(g, LAYERS + L * 4);
        if (R16(g, o + 0x18) == 0) {
            W32(g, ebp - 4, 0x3D800000u);
            W32(g, ebp - 8, R32(g, 0xCFF21Cu));
        } else {
            W32(g, ebp - 4, 0x3E000000u);
            W32(g, ebp - 8, FST(g, FM(D(R32(g, 0xCFF21Cu)), D(R32(g, 0x7B7918u)))));
        }
        /* push [layer + 8]; push count; call 0x66E272; add esp, 8 */
        W32(g, esp - 4, OBJ);
        cnt = R32(g, R32(g, SPRITES) + L * 0x1804u);
        W32(g, esp - 8, cnt);
        *CR(g, C_EAX) = L * 0x1804u;
        *CR(g, C_ECX) = R32(g, SPRITES);
        *CR(g, C_EDX) = cnt;
        call(g, alloc, esp - 8);
        *CR(g, C_ESP) = esp;
        eax = *CR(g, C_EAX);
        ecx = *CR(g, C_ECX);
        zf = eax == 0;
        if (eax) {
            o = R32(g, LAYERS + L * 4);
            zf = R16(g, o + 0x1A) == 2;
            if (zf) {
                ecx = quads(g, ebp, L, 1);
            } else {
                quads(g, ebp, L, 0);
                ecx = L * 0x1804u;
            }
        }
        /* 0x6470BA */
        eax = R32(g, LAYERS + L * 4);
        W32(g, eax + 0x14, 1);
    next:
        edx = L + 1;
    }
    (void)edx;
    W32(g, ebp - 0x10, 0x2A);   /* 0x646777: inc of the counter */
    /* cmp [ebp-0x10], 0x2A (equal); mov esp, ebp; pop ebp; ret */
    *CR(g, C_EAX) = eax;
    *CR(g, C_ECX) = ecx;
    *CR(g, C_EDX) = 0x2A;
    ctx[C_ZF] = zf;   /* the last ZF the translation stores: the final cmp's is dead */
    ctx[C_SF] = 0;
    ctx[C_OF] = 0;
    *CR(g, C_EBP) = R32(g, ebp);
    *CR(g, C_ESP) = ebp + 4;
    tick();
    *CR(g, C_ESP) = ebp + 8;
}


/* ---------------------------------------------------------------------
 * BUILD 793: x86 0x64085C and 0x640C49, the per-tile visibility passes of
 * background depths 1 and 2 (ARM +0xA059D0 / +0xA06DE0), and
 * add_page_tile 0x6464BA, which they call for each visible tile, inlined.
 *
 * The x86 step by step: eax/ecx/edx/ebx as each instruction leaves them
 * (16-bit partial writes included), the locals and pushed arguments, the
 * frame 0x6464BA makes and its tick at return. The flag bytes are stored
 * where the translation stores them: after a cmp/test, the ones its jcc
 * reads (jle/jg: ZF SF OF, jge/jl: SF OF, je/jne: ZF). The registers and
 * flags go to the context before each tick and are read back after it.
 *
 * Read once, where the x86 reads again: within one tile, the tile index
 * (the word it just stored at ebp-0x18), the tile table base, the tile's
 * own fields, the view scale and the locals it just stored; within one
 * call, its two arguments and the loop counter it stores; within one
 * add_page_tile, its seven arguments, the page table and the page's count.
 * The stores in between are the frame (the x86 stack), the tile's +0x1040
 * / +0x1044 flag word (no field read after it overlaps it) and a page
 * entry (the slot's own count lies before its entries). They could change
 * a value read here only if the stack, the game's globals and its heap
 * blocks overlapped. Everything is read afresh for every tile.
 * --------------------------------------------------------------------- */
static __attribute__((noinline, section(".text.bg"))) u8 *HN(const G *g, u32 a) { return H(g, a); }
static inline __attribute__((always_inline)) u32 R32n(const G *g, u32 a) { return *(volatile u32 *)HN(g, a); }
static inline __attribute__((always_inline)) u16 R16n(const G *g, u32 a) { return *(volatile u16 *)HN(g, a); }
static inline __attribute__((always_inline)) u8 R8n(const G *g, u32 a) { return *(volatile u8 *)HN(g, a); }
static inline __attribute__((always_inline)) void W32n(const G *g, u32 a, u32 v) { *(volatile u32 *)HN(g, a) = v; }
static inline __attribute__((always_inline)) void W16n(const G *g, u32 a, u32 v) { *(volatile u16 *)HN(g, a) = (u16)v; }
/* the same, inline: the loop head and the edge tests every tile takes */
static inline __attribute__((always_inline)) u32 R32i(const G *g, u32 a) { return *(volatile u32 *)H(g, a); }
static inline __attribute__((always_inline)) u16 R16i(const G *g, u32 a) { return *(volatile u16 *)H(g, a); }
static inline __attribute__((always_inline)) void W32i(const G *g, u32 a, u32 v) { *(volatile u32 *)H(g, a) = v; }
static inline __attribute__((always_inline)) void W16i(const G *g, u32 a, u32 v) { *(volatile u16 *)H(g, a) = (u16)v; }

typedef struct {
    u32 eax, ecx, edx, ebx, ebp;
    u8 zf, sf, of;
} S;

static inline __attribute__((always_inline)) u32 SX(u32 w) { return (u32)(int)(short)(u16)w; }

/* cmp a, b */
static inline __attribute__((always_inline)) void CMP3(S *s, u32 a, u32 b)
{
    u32 r = a - b;
    s->zf = r == 0;
    s->sf = r >> 31;
    s->of = ((a ^ b) & (a ^ r)) >> 31;
}

static inline __attribute__((always_inline)) void CMP2(S *s, u32 a, u32 b)
{
    u32 r = a - b;
    s->sf = r >> 31;
    s->of = ((a ^ b) & (a ^ r)) >> 31;
}

#define JLE(s) ((s)->zf || (s)->sf != (s)->of)
#define JGE(s) ((s)->sf == (s)->of)

static inline __attribute__((always_inline)) void sync(G *g, const S *s)
{
    *CR(g, C_EAX) = s->eax;
    *CR(g, C_ECX) = s->ecx;
    *CR(g, C_EDX) = s->edx;
    *CR(g, C_EBX) = s->ebx;
    *CR(g, C_EBP) = s->ebp;
    g->ctx[C_ZF] = s->zf;
    g->ctx[C_SF] = s->sf;
    g->ctx[C_OF] = s->of;
}

static inline __attribute__((always_inline)) void resync(G *g, S *s)
{
    reload(g);
    s->eax = *CR(g, C_EAX);
    s->ecx = *CR(g, C_ECX);
    s->edx = *CR(g, C_EDX);
    s->ebx = *CR(g, C_EBX);
    s->ebp = *CR(g, C_EBP);
    s->zf = g->ctx[C_ZF];
    s->sf = g->ctx[C_SF];
    s->of = g->ctx[C_OF];
}

/* fild dword [a]: the int as a double */
static inline __attribute__((always_inline)) double FILD(const G *g, u32 a) { return (double)(int)R32i(g, a); }

#define PAGES 0xD00050u

/* add_page_tile 0x6464BA(x, y, z, u, v, palette, page), its frame at e
 * (the caller's 7 pushed words at e + 8; the return slot e + 4 unwritten) */
static __attribute__((noinline, section(".text.bg"))) void add_tile(G *g, S *s, u32 e, fn_t tick)
{
    u32 page = R32i(g, e + 0x20) * 0x1804u;
    u32 pages = R32i(g, PAGES);
    u32 slot = pages + page;
    u32 n = R32i(g, slot);
    u32 at = slot + n * 0x18u;
    W32i(g, e, s->ebp);
    W32i(g, at + 4, FST(g, FA(FILD(g, 0xCFF204u), D(R32i(g, e + 8)))));
    W32i(g, at + 8, FST(g, FA(FILD(g, 0xCFF208u), D(R32i(g, e + 0xC)))));
    W32i(g, at + 0xC, R32i(g, e + 0x10));
    W32i(g, at + 0x10, R32i(g, e + 0x14));
    W32i(g, at + 0x14, R32i(g, e + 0x18));
    W32i(g, at + 0x18, R32i(g, e + 0x1C));
    W32i(g, slot, n + 1);
    /* pop ebp; ret (the tick between) */
    s->eax = n + 1;
    s->ecx = page;
    s->edx = pages;
    s->ebp = R32i(g, e);
    *CR(g, C_ESP) = e + 4;
    sync(g, s);
    tick();
    resync(g, s);
    *CR(g, C_ESP) = e + 8;
}

/* push ebp; mov ebp, esp: the frame at ctx esp - 4 */
static inline __attribute__((always_inline)) u32 enter(G *g, S *s)
{
    u32 ebp = *CR(g, C_ESP) - 4;
    s->eax = *CR(g, C_EAX);
    s->ecx = *CR(g, C_ECX);
    s->edx = *CR(g, C_EDX);
    s->ebx = *CR(g, C_EBX);
    s->zf = g->ctx[C_ZF];
    s->sf = g->ctx[C_SF];
    s->of = g->ctx[C_OF];
    W32n(g, ebp, *CR(g, C_EBP));
    s->ebp = ebp;
    return ebp;
}

/* mov esp, ebp; pop ebp; ret (the tick between) */
static inline __attribute__((always_inline)) void leave(G *g, S *s, u32 ebp, fn_t tick)
{
    s->ebp = R32n(g, ebp);
    *CR(g, C_ESP) = ebp + 4;
    sync(g, s);
    tick();
    *CR(g, C_ESP) = ebp + 8;
}

/* BUILD 795: the tile window as the build's widescreen passes left the
 * translation (ff7nx_uncrop's origin, ff7nx_wsclamp's four extents),
 * read from the text at install and passed by the stub: 12 bits each,
 * origin y | top << 12 | left << 24 | right << 36 | bottom << 48. Stock
 * 224, 256, 336, 0, 0. right/bottom bias the camera before the compare,
 * and the biased camera is what lands in the register, as in the caves. */
#define WS_OY(w)     ((u32)(w) & 0xFFF)
#define WS_TOP(w)    ((u32)((w) >> 12) & 0xFFF)
#define WS_LEFT(w)   ((u32)((w) >> 24) & 0xFFF)
#define WS_RIGHT(w)  ((u32)((w) >> 36) & 0xFFF)
#define WS_BOTTOM(w) ((u32)((w) >> 48) & 0xFFF)

/* the screen offsets both passes start with: ebp-0xC, ebp-8 (16-bit) */
static __attribute__((noinline, section(".text.bg"))) void origin(G *g, S *s, u32 ebp, u32 lim, u32 oy)
{
    u32 k = R32n(g, 0xCFF1F0u);
    W32n(g, ebp - 0x10, 0x3D800000u);
    W32n(g, ebp - 0x14, 0x3D800000u);
    s->eax = SX(R16n(g, ebp + 8));
    s->ecx = (0x140 - s->eax) * k;
    W16n(g, ebp - 0xC, s->ecx);
    s->edx = SX(R16n(g, ebp + 0xC));
    s->eax = (oy - s->edx) * k;
    W16n(g, ebp - 8, s->eax);
    CMP3(s, R32n(g, 0xCFF24Cu), 0);
    if (JLE(s))
        return;
    s->ecx = s->edx;
    CMP3(s, s->ecx, lim);
    if (!JLE(s))                                /* jg */
        return;
    s->edx = R32n(g, 0xCFF24Cu) * k;
    s->eax = (s->eax & 0xFFFF0000u) | ((s->eax - s->edx) & 0xFFFF);
    W16n(g, ebp - 8, s->eax);
}

/* the loop head both share: ++[ebp-0x20] (16-bit, through cx; *i is the
 * word it stored), the cmp with the tile count (jge). 0 when done. */
static inline __attribute__((always_inline)) int head(G *g, S *s, u32 ebp, u32 count, u32 *i)
{
    s->edx = *i;
    s->eax = R16i(g, count);
    CMP2(s, s->edx, s->eax);
    return !JGE(s);
}

static inline __attribute__((always_inline)) void step(G *g, S *s, u32 ebp, u32 *i)
{
    *i = (*i + 1) & 0xFFFF;
    s->ecx = (s->ecx & 0xFFFF0000u) | *i;
    W16i(g, ebp - 0x20, *i);
}

/* the tile's index from the table at [tab] (stored at ebp-0x18) */
static inline __attribute__((always_inline)) u32 pick(G *g, S *s, u32 ebp, u32 tab, u32 i)
{
    u32 ax;
    s->ecx = i;
    s->edx = R32i(g, tab);
    ax = R16i(g, s->edx + i * 4);
    s->eax = (s->eax & 0xFFFF0000u) | ax;
    W16i(g, ebp - 0x18, ax);
    return ax * 0x105Cu;
}

__attribute__((section(".text.bg")))
void bg_64085c(u8 *ctx, u8 *const *const *tabp, u8 *const *dummyp, u8 *x87, fn_t tick, u64 ws)
{
    G gg, *g = &gg;
    S ss, *s = &ss;
    u32 ebp, off, base, t, ty, tx, yv, xv, b34, k, sx, sy;
    const u32 top = WS_TOP(ws), left = WS_LEFT(ws);
    const u32 right = WS_RIGHT(ws), bottom = WS_BOTTOM(ws);
    u32 i;
    g->ctx = ctx;
    g->tabp = tabp;
    g->dummyp = dummyp;
    g->x87 = x87;
    reload(g);
    ebp = enter(g, s);
    W32n(g, ebp - 0x34, s->ebx);                /* sub esp, 0x30; push ebx */
    origin(g, s, ebp, 8, WS_OY(ws));
    W16n(g, ebp - 0x20, 0);
    yv = SX(R16n(g, ebp + 0xC));                /* the arguments: read once */
    xv = SX(R16n(g, ebp + 8));
    for (i = 0; head(g, s, ebp, 0xCFFDFCu, &i); step(g, s, ebp, &i)) {
        off = pick(g, s, ebp, 0xCFF3FCu, i);
        base = R32i(g, 0xCFFE00u);
        t = base + off;
        ty = SX(R16i(g, t + 2));
        s->ecx = yv - top; s->edx = base; s->eax = ty;
        CMP3(s, s->eax, s->ecx);
        if (JLE(s))
            continue;
        s->edx = yv + bottom; s->eax = base; s->ecx = ty;
        CMP2(s, s->ecx, s->edx);
        if (JGE(s))
            continue;
        tx = SX(R16i(g, t));
        s->ecx = base; s->edx = tx; s->eax = xv - left;
        CMP3(s, s->edx, s->eax);
        if (JLE(s))
            continue;
        s->edx = base; s->eax = tx; s->ecx = xv + right;
        CMP2(s, s->eax, s->ecx);
        if (JGE(s))
            continue;
        b34 = R8n(g, t + 0x34);
        s->edx = off; s->eax = base; s->ecx = b34;
        s->zf = b34 == 0;
        if (!s->zf) {
            s->ecx = R8n(g, t + 0x35);
            s->ebx = b34;
            s->edx = R8n(g, b34 + 0xCC0E7Au);
            s->ecx &= s->edx;
            s->zf = s->ecx == 0;
            if (s->zf)
                continue;
        }
        W32n(g, t + 0x1040, 1);
        k = R32n(g, 0xCFF1F0u);
        sx = tx * k + SX(R16n(g, ebp - 0xC));
        W16n(g, ebp - 4, sx);
        sy = ty * k + SX(R16n(g, ebp - 8));
        W16n(g, ebp - 0x1C, sy);
        s->eax = SX(R16n(g, ebp - 8)); s->ecx = base; s->edx = sy;
        s->zf = R32n(g, 0xCFF1D8u) == 0;
        if (!s->zf) {
            s->edx = base;
            s->eax = R16n(g, t + 0x30);
            s->ecx = R16n(g, 0xD00088u) + 1;
            s->zf = s->eax == s->ecx;
            if (s->zf)
                continue;
        }
        s->edx = off; s->eax = base;
        s->zf = R32n(g, t + 0x103C) == 0;
        if (s->zf) {
            s->ecx = off; s->edx = base;
            W32n(g, ebp - 0x38, R16n(g, t + 0x1056));
            W32n(g, ebp - 0x3C, R16n(g, t + 0x30));
            W32n(g, ebp - 0x40, R32n(g, t + 0x14));
            W32n(g, ebp - 0x44, R32n(g, t + 0x10));
            s->eax = R32n(g, t + 4);
            W32n(g, ebp - 0x48, s->eax);
            s->ecx = SX(sy);
            W32n(g, ebp - 0x24, s->ecx);
            W32n(g, ebp - 0x4C, FST(g, (double)(int)s->ecx));
            s->edx = SX(sx);
            W32n(g, ebp - 0x28, s->edx);
            W32n(g, ebp - 0x50, FST(g, (double)(int)s->edx));
        } else {
            /* the port's own version of this path: +0x1054 >= 2 moves the
             * tile 14 pages on, >= 3 another 4 (not in the PC exe) */
            s->eax = off; s->ecx = base;
            s->edx = R16n(g, t + 0x1058);
            CMP2(s, R32n(g, t + 0x1054), 2);
            if (JGE(s)) {
                s->edx += 0xE;
                CMP2(s, R32n(g, t + 0x1054), 3);
                if (JGE(s))
                    s->edx += 4;
            }
            W32n(g, ebp - 0x38, s->edx);
            W32n(g, ebp - 0x3C, R16n(g, t + 0x30));
            W32n(g, ebp - 0x40, R32n(g, t + 0x14));
            W32n(g, ebp - 0x44, R32n(g, t + 0x10));
            s->edx = R32n(g, t + 4);
            W32n(g, ebp - 0x48, s->edx);
            s->eax = SX(sy);
            W32n(g, ebp - 0x2C, s->eax);
            W32n(g, ebp - 0x4C, FST(g, (double)(int)s->eax));
            s->ecx = SX(sx);
            W32n(g, ebp - 0x30, s->ecx);
            W32n(g, ebp - 0x50, FST(g, (double)(int)s->ecx));
        }
        add_tile(g, s, ebp - 0x58, tick);
    }
    s->eax = 1;
    s->ebx = R32n(g, ebp - 0x34);               /* pop ebx */
    leave(g, s, ebp, tick);
}

__attribute__((section(".text.bg")))
void bg_640c49(u8 *ctx, u8 *const *const *tabp, u8 *const *dummyp, u8 *x87, fn_t tick, u64 ws)
{
    G gg, *g = &gg;
    S ss, *s = &ss;
    u32 ebp, off, base, t, ty, tx, yv, xv, k, sx, sy;
    const u32 top = WS_TOP(ws), left = WS_LEFT(ws);
    const u32 right = WS_RIGHT(ws), bottom = WS_BOTTOM(ws);
    u32 i;
    g->ctx = ctx;
    g->tabp = tabp;
    g->dummyp = dummyp;
    g->x87 = x87;
    reload(g);
    ebp = enter(g, s);                          /* sub esp, 0x28 */
    origin(g, s, ebp, 6, WS_OY(ws));
    W16n(g, ebp - 0x20, 0);
    yv = SX(R16n(g, ebp + 0xC));                /* the arguments: read once */
    xv = SX(R16n(g, ebp + 8));
    for (i = 0; head(g, s, ebp, 0xCFFE5Cu, &i); step(g, s, ebp, &i)) {
        off = pick(g, s, ebp, 0xCFF3F8u, i);
        base = R32i(g, 0xCFFE54u);
        t = base + off;
        W32n(g, t + 0x1044, 1);
        ty = SX(R16i(g, t + 2));
        s->ecx = base; s->edx = ty; s->eax = yv - top;
        CMP3(s, s->edx, s->eax);
        if (JLE(s))
            continue;
        s->edx = base; s->eax = ty; s->ecx = yv + bottom;
        CMP2(s, s->eax, s->ecx);
        if (JGE(s))
            continue;
        tx = SX(R16i(g, t));
        s->eax = base; s->ecx = tx; s->edx = xv - left;
        CMP3(s, s->ecx, s->edx);
        if (JLE(s))
            continue;
        s->ecx = base; s->edx = tx; s->eax = xv + right;
        CMP2(s, s->edx, s->eax);
        if (JGE(s))
            continue;
        k = R32n(g, 0xCFF1F0u);
        sx = tx * k + SX(R16n(g, ebp - 0xC));
        W16n(g, ebp - 4, sx);
        sy = ty * k + SX(R16n(g, ebp - 8));
        W16n(g, ebp - 0x1C, sy);
        s->ecx = base;
        W32n(g, ebp - 0x2C, R16n(g, t + 0x1056));
        W32n(g, ebp - 0x30, R16n(g, t + 0x30));
        W32n(g, ebp - 0x34, R32n(g, t + 0x14));
        s->edx = R32n(g, t + 0x10);
        W32n(g, ebp - 0x38, s->edx);
        W32n(g, ebp - 0x3C, 0x3F7FEC57u);
        s->eax = SX(sy);
        W32n(g, ebp - 0x24, s->eax);
        W32n(g, ebp - 0x40, FST(g, (double)(int)s->eax));
        s->ecx = SX(sx);
        W32n(g, ebp - 0x28, s->ecx);
        W32n(g, ebp - 0x44, FST(g, (double)(int)s->ecx));
        add_tile(g, s, ebp - 0x4C, tick);
    }
    s->eax = 1;
    leave(g, s, ebp, tick);
}
