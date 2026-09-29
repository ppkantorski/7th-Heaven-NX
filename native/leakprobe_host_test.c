/* Host test for leakprobe.c v3: fake texture manager, heap, GL errors and
   the Highwind around a trip into fship. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define LP_HOST
typedef unsigned long long u64_;
static u64_ fake_ticks;
static u64_ lp_host_ticks(void) { return fake_ticks; }
#include "leakprobe.c"
/* v7b: write the counters where leakprobe.py's caves do (state + STATE_BYTES - NG*32 + 32*k),
   not through st->gacc, so a layout mismatch fails here */
#define PY_STATE_BYTES 9984
#define CAVE(st, k) ((u64 *)(void *)((char *)(st) + PY_STATE_BYTES - NG * 32 + 32 * (k)))
_Static_assert(sizeof(State) == PY_STATE_BYTES, "leakprobe.py STATE_BYTES");

static u8 *mem;
static u8 *g2h(u32 g) { return mem + g; }
static void w32(u32 g, u32 v) { memcpy(mem + g, &v, 4); }
static u64 mgr[0x2090 / 8];
static u64 *rendp = mgr;
static u32 mode_word;
static u32 *mode_host = &mode_word;
static int errors;
static int gl_queue[64], gl_n;
static u32 fake_glerr(void) { return gl_n ? (u32)gl_queue[--gl_n] : 0; }
static u32 hwblk[8] = { 1, 50000, 20000, 70000, 0, 0, 1, 3 };
#define ENT 0x02F00000u
#define CHECK(c, w) do { if (!(c)) { printf("  FAIL: %s\n", w); errors++; } } while (0)

static void make_heap(void)
{
    w32(HEAP_DESC + 0, 0x04000000); w32(HEAP_DESC + 4, 0x01000000);
    u32 h = 0x02001000u;
    w32(h + BLK_SIZE, 0x100000); w32(h + BLK_USED, 0); w32(h + BLK_NEXT, 0);
    w32(HEAP_DESC + 8, h + BLK_HDR);
}
static PF pf;
#define TK(us) ((u64)(us) * 96u / 5u)          /* us -> 19.2 MHz ticks */
/* one frame: the game's logic `pre` us, the window `win`, the far field
   `far`, the rest `post`, then the flip */
static void frame_t(State *st, u32 mode, u32 field, u32 pre, u32 win, u32 far, u32 post)
{
    mode_word = mode;
    mem[G_FIELD_ID] = (u8)field; mem[G_FIELD_ID + 1] = (u8)(field >> 8);
    if (mode == 4) {
        pf.bend0 = fake_ticks + TK(pre);
        pf.draw0 = pf.bend0 + TK(win);
        pf.draw1 = pf.draw0 + TK(far);
        pf.frame++; pf.tris = 12000 + far; pf.draws = 150; pf.zoom = 20000.0f;
        pf.t_proj += TK(win / 3); pf.t_sub += TK(win / 2);   /* inside the window */
        pf.t_terr += TK(post / 2);                            /* inside the rest */
        pf.own = win < 2000;                                  /* the next frame's */
        fake_ticks = pf.draw1 + TK(post);
    } else {
        fake_ticks += TK(16667);
    }
    lp_frame(st, g2h, 0, (u32 *)(void *)&mode_host, (u64 **)(void *)&rendp, fake_glerr, hwblk, &pf);
}
static void frame(State *st, u32 mode, u32 field)
{
    frame_t(st, mode, field, 4000, 3000, 2000, 7667);
}
static void ship(u32 y, u32 ctl, u32 view)
{
    w32(G_PLAYER_ENT, ENT);
    w32(ENT + 0xC, 50000); w32(ENT + 0x10, y); w32(ENT + 0x14, 70000);
    w32(G_CONTROL, ctl); w32(G_VIEW_TYPE, view); w32(G_HW_TAKEOFF, 4000);
}

int main(void)
{
    mem = calloc(1, 0x3000000);
    State *st = calloc(1, sizeof(State));
    make_heap();
    for (u32 i = 0; i < 100; i++) mgr[i] = 0x1000 + i;
    /* world A: fly at 20000, then the game takes control and brings it to 10000 */
    ship(20000, 1, 3);
    for (int f = 0; f < 300; f++) frame(st, 4, 72);
    for (int k = 0; k < 10; k++) { ship(20000 - 1000 * (k + 1), 0, 3); frame(st, 4, 72); }
    /* fship */
    for (int f = 0; f < 50; f++) frame(st, 2, 72);
    /* world B: back at 10000, control off for 3 frames, then on */
    for (int f = 0; f < 3; f++) { ship(10000, 0, 3); frame(st, 4, 72); }
    for (int f = 0; f < 40; f++) { ship(10000, 1, 3); frame(st, 4, 72); }
    /* fship again, then the collection (R3 there) */
    gl_queue[gl_n++] = 0x505;
    for (int f = 0; f < 20; f++) frame(st, 2, 72);
    V out[NV]; W w[2]; u64 regs[10];
    lp_collect(st, g2h, (u64 **)(void *)&rendp, out, w, regs, hwblk);
    printf("A: frames %u y_last_ctl %d y_max %d last16:", w[0].frames, w[0].y_last_ctl, w[0].y_max);
    for (int i = 0; i < 16; i++) printf(" %d%s", w[0].y_last[i], (w[0].ctl_last >> i) & 1 ? "" : "*");
    printf("\nB: frames %u first16:", w[1].frames);
    for (int i = 0; i < 16; i++) printf(" %d%s", w[1].y_first[i], (w[1].ctl_first >> i) & 1 ? "" : "*");
    printf("\n");
    CHECK((regs[0] >> 32) == MAGIC, "magic");
    CHECK(w[0].frames == 310 && w[0].y_last_ctl == 20000 && w[0].y_max == 20000, "A: the altitude flown");
    CHECK(w[0].y_last[15] == 10000 && !((w[0].ctl_last >> 15) & 1) && ((w[0].ctl_last >> 5) & 1),
          "A: the last 16 frames in order, control off for the last 10");
    CHECK(w[0].y_last[5] == 20000 && w[0].y_last[6] == 19000, "A: the descent starts at the 11th-last frame");
    CHECK(w[1].y_first[0] == 10000 && !(w[1].ctl_first & 7) && ((w[1].ctl_first >> 3) & 1), "B: the return");
    CHECK(w[1].hw_first == 0xFFFF && w[1].takeoff == 4000, "B: view and take-off height");
    CHECK(w[0].y_sparse[0] == 20000, "A: the sparse samples");
    CHECK((u32)regs[5] == 20000 && (regs[5] >> 56) == 1 && ((regs[5] >> 48) & 0xFF) == 1
          && (regs[7] >> 32) == 3, "the module's restore state");
    CHECK(out[0].key != 0, "visits");
    int nv = 0; while (nv < NV && out[nv].key) nv++;
    CHECK(nv == 4 && out[nv - 1].noom == 1, "4 visits, the last with an OOM");
    /* v4: frame timing. The world frames above: 16667 us each, 4000 pre,
       3000 window, 2000 far, 7667 post; the first 30 of each visit skipped */
    P *p = &st->perf;
    u32 n0 = p->frames;
    printf("perf: %u frames, interval avg %llu max %u; pre %llu win %llu far %llu post %llu\n",
           p->frames, p->frames ? (unsigned long long)(p->sum[0] / p->frames) : 0ull, p->max[0],
           p->frames ? (unsigned long long)(p->sum[1] / p->frames) : 0ull,
           p->frames ? (unsigned long long)(p->sum[2] / p->frames) : 0ull,
           p->frames ? (unsigned long long)(p->sum[3] / p->frames) : 0ull,
           p->frames ? (unsigned long long)(p->sum[4] / p->frames) : 0ull);
    CHECK(n0 == (300 + 10 - 30) + (43 - 30), "world frames counted, loads skipped");
    CHECK(p->sum[3] / n0 == 2000 && p->sum[2] / n0 == 3000 && p->sum[1] / n0 == 4000,
          "the far field, the window and the logic, in us");
    CHECK(p->sum[0] / n0 >= 16660 && p->sum[0] / n0 <= 16670 && p->over[0] == 0, "the interval");
    /* a slow frame: the far field takes 12 ms */
    for (int f = 0; f < 40; f++) frame_t(st, 4, 72, 4000, 3000, 2000, 7667);
    frame_t(st, 4, 72, 4000, 3000, 12000, 7667);
    for (int f = 0; f < 5; f++) frame_t(st, 4, 72, 4000, 3000, 2000, 7667);
    CHECK(p->worst[0].interval >= 26660 && p->worst[0].interval <= 26670 && p->worst[0].far >= 11999
          && p->worst[0].far <= 12000 && p->over[2] == 1 && p->over[3] == 1 && p->over[4] == 0,
          "the worst frame and its split");
    CHECK(p->worst[0].zoom == 20000 && p->worst[0].tris == 24000 && p->worst[0].draws == 150, "its context");
    /* v5: the window's projection and submit, and frames with our window */
    CHECK(p->sum[5] / p->frames == 1000 && p->sum[6] / p->frames == 1500, "projection 1/3, submit 1/2 of the window");
    CHECK(p->own == 0, "no frame had our window");
    CHECK(p->sum[7] / p->frames >= 3830 && p->sum[7] / p->frames <= 3840, "the terrain draw, half the rest");
    u32 own0 = p->own, fr0 = p->frames;
    for (int f = 0; f < 20; f++) frame_t(st, 4, 72, 4000, 900, 2000, 9767);
    CHECK(p->own - own0 == 19 && p->frames - fr0 == 20, "ours from the frame after it was decided");
    CHECK((p->worst[0].view >> 8) == 0, "the worst frame was not ours");
    /* v7: the GL methods: slot 5 takes 40 us a call, 3 calls a world frame */
    u32 g0 = st->gframes;
    for (int f = 0; f < 10; f++) {
        CAVE(st,5)[0] += 3 * TK(40); CAVE(st,5)[1] += 3;
        CAVE(st,9)[0] += TK(10); CAVE(st,9)[1] += 1;
        frame_t(st, 4, 72, 4000, 900, 2000, 9767);
    }
    CAVE(st,5)[0] += TK(100000); CAVE(st,5)[1] += 50;      /* a field frame: not counted */
    frame_t(st, 2, 72, 0, 0, 0, 0);
    printf("gframes %u gw_t5 %llu gw_c5 %u\n", st->gframes - g0, (unsigned long long)st->gw_t[5], st->gw_c[5]);
    CHECK(st->gframes - g0 == 10, "world frames for the GL methods");
    CHECK(st->gw_t[5] * 5 / 96 == 1200 && st->gw_c[5] == 30, "slot 5: 30 calls, 1.2 ms (world only)");
    printf("sizeof(State) %zu\n", sizeof(State));
    printf("leakprobe host test: %d error(s)\n", errors);
    return errors ? 1 : 0;
}
