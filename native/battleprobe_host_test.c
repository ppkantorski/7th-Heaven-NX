/* host test for battleprobe.c: a fake guest memory, one stalled battle */
#define BP_HOST 1
#include "battleprobe.c"
#include <setjmp.h>
#include <stdio.h>
#include <string.h>

static u8 mem[0x1000000];
static jmp_buf jb;
static int errors;
void bp_host_break(void) { longjmp(jb, 1); }
static u8 *g2h(u32 g) { return g < sizeof(mem) ? &mem[g] : 0; }
static void w32(u32 g, u32 v) { memcpy(&mem[g], &v, 4); }
static void w16(u32 g, u16 v) { memcpy(&mem[g], &v, 2); }
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); errors++; } } while (0)

int main(void)
{
    static u8 pad[0x100]; u8 *pp = pad;            /* input object chain */
    static u64 chain[4][32];
    u8 *got_target = (u8 *)chain[0];
    *(u64 *)(void *)chain[0] = (u64)chain[1];
    chain[1][1] = (u64)chain[2];
    chain[2][0] = (u64)chain[3];
    *(u64 *)(void *)((u8 *)chain[3] + 0x88) = (u64)pp;
    u8 *got = got_target;
    u32 mode = 3; u32 *modep = &mode;

    w32(E100_FN + 4 * 37, 0x46BA0B); w16(E100_DATA + 0x20 * 37, 0x1234);
    w16(E100_DATA + 0x20 * 37 + 4, 77);
    w32(E10_FN + 4 * 2, 0x426941);  w16(E10_DATA + 0x20 * 2 + 4, 5);
    w32(E60_FN + 4 * 59, 0x45CF2A);
    w32(CAM_FN + 4 * 15, 0x5D0000);
    mem[G_PAUSED] = 1; mem[G_SCRIPT_WAIT] = 9;
    w32(G_ANIM_QUEUE, 0xAABBCC03);
    w32(G_ACTORS + 0x68 * 4, 0x11); w32(G_ACTORS + 0x68 * 4 + 4, 0x22);

    /* R3 up, or not battle: nothing happens */
    bp_frame(g2h, &got, (u32 *)(void *)&modep);
    *(u64 *)(void *)(pp + 0x20) = BTN_L3;
    mode = 2; bp_frame(g2h, &got, (u32 *)(void *)&modep);
    mode = 3;
    if (!setjmp(jb)) { bp_frame(g2h, &got, (u32 *)(void *)&modep); CHECK(!"no break"); }
    u32 *t = (u32 *)(void *)bp_host_tls;
    CHECK(t[0] == MAGIC);
    CHECK(t[1] == (1u | (1u << 8) | (1u << 16) | (1u << 24)));
    CHECK(t[2] == (1u | (9u << 8) | (3u << 16)));
    CHECK(*(u32 *)(void *)(bp_host_tls + 16) == 0xAABBCC03u);
    CHECK(t[16 + 8] == 0x11 && t[17 + 8] == 0x22);
    CHECK(t[36] == (0x426941u | (2u << 24)) && t[37] == (5u << 16));
    CHECK(t[56] == (0x5D0000u | (15u << 24)));
    CHECK(bp_host_out[0] == (0x45CF2Au | (59u << 24)));
    CHECK(bp_host_out[24] == (0x46BA0Bu | (37u << 24)));
    CHECK(bp_host_out[25] == (0x1234u | (77u << 16)));
    CHECK(bp_host_regs[0] == MAGIC && bp_host_regs[1] == t[1]);
    printf("%s (%d errors)\n", errors ? "FAILED" : "ok", errors);
    return errors != 0;
}
