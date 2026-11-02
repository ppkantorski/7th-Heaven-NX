/* movieprobe.c -- DIAGNOSTIC (BUILD 876m): does fw_movie_update's wait
 * for the next decoded frame (+0x10F161C..+0x10F1648) spin, or does its
 * present() (+0x10F9080) block on vsync? The wait loop's present call is
 * wrapped: count, ticks inside it, the longest, and how many took under
 * 1 ms (a spin). L3 once arms, L3 again (>= 1 s later) dumps (svcBreak):
 *   x0 'MVP1' | frames<<32   x1 presents in the wait | movie updates<<32
 *   x2 ticks in those presents (19.2 MHz)   x3 elapsed ticks since armed
 *   x4 longest present ticks | presents under 1 ms<<32
 *   x5 main thread CPU ticks since armed (svcGetInfo ThreadTickCount)
 *   x6 idle ticks of the core it was armed on   x7 core armed | core now<<8 | waits<<32 */
typedef unsigned int u32;
typedef unsigned long long u64;
typedef unsigned char u8;
typedef void (*fn0_t)(void);
typedef struct { u64 armed, prev, t0, frames, pres, ticks, maxt, fast, waits, updates, cur, thr0, idle0, core0; } P;
#define BTN_L3 (1ull << 4)
#define S __attribute__((noinline, section(".text.mp")))
static inline __attribute__((always_inline)) u64 now(void)
{ u64 v; __asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(v)); return v; }

static inline __attribute__((always_inline)) u64 info(u64 type, u64 handle, u64 sub)
{
    register u64 x0 __asm__("x0");
    register u64 x1 __asm__("x1") = type;
    register u64 x2 __asm__("x2") = handle;
    register u64 x3 __asm__("x3") = sub;
    __asm__ volatile("svc #0x29" : "=r"(x0), "+r"(x1), "+r"(x2), "+r"(x3) : : "x4", "x5", "x6", "x7", "x8", "x9", "x10", "x11", "x12", "x13", "x14", "x15", "x16", "x17", "memory");
    return (u32)x0 ? ~0ull : x1;
}
static inline __attribute__((always_inline)) u64 core(void)
{
    register u64 x0 __asm__("x0");
    __asm__ volatile("svc #0x10" : "=r"(x0) : : "x1", "x2", "x3", "x4", "x5", "x6", "x7", "x8", "x9", "x10", "x11", "x12", "x13", "x14", "x15", "x16", "x17", "memory");
    return (u32)x0;
}
#define THREAD_TICKS() info(0xF0000002ull, 0xFFFF8000ull, ~0ull)
#define IDLE_TICKS(c) info(10, 0, (c))

S void mp_present(P *p, fn0_t present)
{
    u64 a = now(), d;
    present();
    if (!p->armed) return;
    d = now() - a;
    p->pres++;
    p->ticks += d;
    if (d > p->maxt) p->maxt = d;
    if (d < 19200) p->fast++;
    if (p->cur) { p->waits++; p->cur = 0; }
}

S void mp_update(P *p) { if (p->armed) p->updates++; p->cur = 1; }

static S u8 *input_obj(u8 **got)
{
    u8 *q;
    if (!got || !(q = *got)) return 0;
    if (!(q = *(u8 **)(void *)(q + 0))) return 0;
    if (!(q = *(u8 **)(void *)(q + 8))) return 0;
    if (!(q = *(u8 **)(void *)(q + 0))) return 0;
    return *(u8 **)(void *)(q + 0x88);
}

static S __attribute__((noreturn)) void dump(P *p)
{
    u64 r[10];
    r[0] = 0x3150564Dull | p->frames << 32;
    r[1] = (p->pres & 0xFFFFFFFF) | p->updates << 32;
    r[2] = p->ticks;
    r[3] = now() - p->t0;
    r[4] = (p->maxt & 0xFFFFFFFF) | p->fast << 32;
    {
        u64 c = core(), th = THREAD_TICKS(), id = IDLE_TICKS(p->core0);
        r[5] = th == ~0ull || p->thr0 == ~0ull ? ~0ull : th - p->thr0;
        r[6] = id == ~0ull || p->idle0 == ~0ull ? ~0ull : id - p->idle0;
        r[7] = p->core0 | c << 8 | p->waits << 32;
    }
    r[8] = 0; r[9] = 0;
    __asm__ volatile(
        "mov x16, %0\n"
        "ldp x0, x1, [x16, #0]\n" "ldp x2, x3, [x16, #16]\n"
        "ldp x4, x5, [x16, #32]\n" "ldp x6, x7, [x16, #48]\n"
        "ldp x29, x30, [x16, #64]\n"
        "svc #0x26\n" "udf #0\n" : : "r"(r) : "memory");
    __builtin_unreachable();
}

S void mp_frame(P *p, u8 **got)
{
    u8 *q = input_obj(got);
    u64 down = q && (*(u64 *)(void *)(q + 0x20) & BTN_L3);
    u64 edge = down && !p->prev;
    u32 i;
    p->prev = down;
    if (p->armed) p->frames++;
    if (!edge) return;
    if (p->armed && now() - p->t0 >= 19200000ull) dump(p);
    if (p->armed) return;
    for (i = 0; i < sizeof(P) / 8; i++) ((u64 *)p)[i] = 0;
    p->prev = 1; p->armed = 1;
    p->core0 = core(); p->thr0 = THREAD_TICKS(); p->idle0 = IDLE_TICKS(p->core0);
    p->t0 = now();
}
