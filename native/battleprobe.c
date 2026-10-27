/*
 * battleprobe.c -- what a stalled battle is waiting for. DIAGNOSTIC ONLY.
 *
 * Hooked on gfx_drv_flip (every presented frame). Does nothing unless the
 * driver mode is battle AND L3 (left stick click) is held; then it snapshots the battle's
 * sequencing state into the crash report and calls svcBreak:
 *
 *   TLS dump (256):  header, anim_event_queue, the AI context head, every
 *                    actor's status/state flags, effect10 and camera slots
 *   Stack dump (256): effect60 and effect100 slots
 *   x0..x7:           magic, list counts, pause / wait bytes
 *
 * A slot is 8 bytes: the x86 function (24 bits) | slot index << 24, then
 * the slot data's u16 at +0 (0xFFFF = finishing) and at +4 (n_frames).
 * Everything is read through the guest translator one value at a time, so
 * page boundaries do not matter. No state, no BSS.
 */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long long u64;
typedef u8 *(*g2h_fn)(u32 guest);

#define BTN_L3   (1ull << 4)                 /* left stick click */
#define MODE_BATTLE 3u
#define MAGIC    0x314C5442u            /* 'BTL1' */

#define G_PAUSED      0xDC0E6Cu
#define G_SCRIPT_WAIT 0xBFD0F0u
#define G_ANIM_QUEUE  0x9AAD70u
#define G_AI_CTX      0x9AB0A0u
#define G_ACTORS      0x9AB0DCu         /* actor_vars[10], stride 0x68 */

#define E10_FN   0xBE10C0u
#define E10_DATA 0xBF2E70u
#define CAM_FN   0xBF2378u
#define CAM_DATA 0xBFCE08u
#define E60_FN   0xBFB1B0u
#define E60_DATA 0xBFC3A0u
#define E100_FN  0xBF2858u
#define E100_DATA 0xBFB718u

__attribute__((noinline)) static u8 rd8(g2h_fn g2h, u32 g) { const u8 *p = g2h(g); return p ? *p : 0; }
__attribute__((noinline)) static u16 rd16(g2h_fn g2h, u32 g) { return (u16)(rd8(g2h, g) | (rd8(g2h, g + 1) << 8)); }
__attribute__((noinline)) static u32 rd32(g2h_fn g2h, u32 g) { return (u32)rd16(g2h, g) | ((u32)rd16(g2h, g + 2) << 16); }

/* the live slots of one list into `o` (at most `cap`); returns how many
   are live in total, so the reader knows if some did not fit */
static u32 slots(g2h_fn g2h, u32 fn, u32 data, u32 n, u32 stride,
                 u32 *o, u32 cap)
{
    u32 live = 0;
    for (u32 i = 0; i < n; i++) {
        u32 f = rd32(g2h, fn + 4 * i);
        if (!f) continue;
        if (live < cap) {
            u32 d = data + stride * i;
            o[2 * live] = (f & 0xFFFFFFu) | (i << 24);
            o[2 * live + 1] = (u32)rd16(g2h, d) | ((u32)rd16(g2h, d + 4) << 16);
        }
        live++;
    }
    return live;
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

#ifdef BP_HOST
u8 bp_host_tls[256];
u32 bp_host_out[64];
u64 bp_host_regs[10];
void bp_host_break(void);
#endif

__attribute__((noreturn)) static void dump(g2h_fn g2h, u32 mode)
{
#ifdef BP_HOST
    u64 *regs = bp_host_regs;
    u32 *out = bp_host_out;
    u8 *tls = bp_host_tls;
#else
    u64 regs[10];
    u32 out[64];                        /* the stack dump */
    u8 *tls;
    __asm__ volatile("mrs %0, tpidrro_el0" : "=r"(tls));
#endif
    u32 *t = (u32 *)(void *)tls;
    for (u32 i = 0; i < 64; i++) { t[i] = 0; out[i] = 0; }

    /* TLS: 0..15 header, 16..47 anim queue, 48..63 AI context head,
       64..143 actors (status, state) x10, 144..223 effect10 (10),
       224..255 camera (4) */
    u8 *tb = tls;
    for (u32 i = 0; i < 32; i++) tb[16 + i] = rd8(g2h, G_ANIM_QUEUE + i);
    for (u32 i = 0; i < 16; i++) tb[48 + i] = rd8(g2h, G_AI_CTX + i);
    for (u32 a = 0; a < 10; a++) {
        t[16 + 2 * a] = rd32(g2h, G_ACTORS + 0x68 * a);
        t[17 + 2 * a] = rd32(g2h, G_ACTORS + 0x68 * a + 4);
    }
    u32 n10 = slots(g2h, E10_FN, E10_DATA, 10, 0x20, &t[36], 10);
    u32 ncam = slots(g2h, CAM_FN, CAM_DATA, 16, 0x28, &t[56], 4);
    /* stack: effect60 (12) then effect100 (20) */
    u32 n60 = slots(g2h, E60_FN, E60_DATA, 60, 0x20, &out[0], 12);
    u32 n100 = slots(g2h, E100_FN, E100_DATA, 100, 0x20, &out[24], 20);

    t[0] = MAGIC;
    t[1] = n10 | (ncam << 8) | (n60 << 16) | (n100 << 24);
    t[2] = (u32)rd8(g2h, G_PAUSED) | ((u32)rd8(g2h, G_SCRIPT_WAIT) << 8)
           | (mode << 16);
    regs[0] = MAGIC;
    regs[1] = t[1];
    regs[2] = t[2];
    regs[3] = rd32(g2h, G_ANIM_QUEUE);
    regs[4] = rd32(g2h, G_AI_CTX);
    regs[5] = 0; regs[6] = 0; regs[7] = 0; regs[8] = 0; regs[9] = 0;
#ifdef BP_HOST
    bp_host_break();
    __builtin_unreachable();
#else
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
#endif
}

__attribute__((section(".text.bp_frame")))
void bp_frame(g2h_fn g2h, u8 **got, u32 *mode_slot)
{
    if (!mode_slot) return;
    u32 *m = *(u32 **)(void *)mode_slot;
    u32 mode = m ? *m : 0;
    if (mode != MODE_BATTLE) return;
    u8 *p = input_obj(got);
    if (!p || !(*(u64 *)(void *)(p + 0x20) & BTN_L3)) return;
    dump(g2h, mode);
}
