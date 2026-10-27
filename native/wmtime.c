/*
 * wmtime.c -- BUILD 649 (719: no savemap switch): the world map's own night and sunset looks, driven
 * by the day/night clock. See ff7nx_wmtime.py.
 *
 * The world map keeps 16 colour zones at guest 0xE28CE0 (0x24 bytes each),
 * filled by world-script opcodes 0x310..0x316. Every frame x86 0x7550A7
 * walks them: for each ACTIVE zone (+0x13) whose outer radius reaches the
 * player, it blends the zone's colours with the defaults by distance --
 *   +0x10 rgb  light   (x7, into light 0's colour, light mode 2)
 *   +0x14 rgb  sky top     (default   0,   0, 255)
 *   +0x18 rgb  sky horizon (default 250, 255, 255)
 * -- the last zone in range winning; with none in range it restores the
 * defaults (light mode 3). wm0.ev's zone 0 (Midgar) is the dark night with
 * the bright meteor, zone 2 (Cosmo Canyon) the red sunset.
 *
 * wt_pre runs right before that call. On the overworld it mutes every scripted zone (their flags are saved) and fills
 * zone 15 (saved whole) with the clock's mix of default, night and sunset,
 * at a radius that covers the planet; wt_post puts every byte back. The
 * game's own blend, light setup and sky code do the rest, so the looks are
 * exactly the game's, only on the clock instead of the map.
 *
 * Position independent, no data sections (constants built in code).
 */
#include <stdint.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32; typedef int32_t s32;

#define ZONES       0xE28CE0u   /* guest: 16 x 0x24 */
#define ZONE_BYTES  0x24
#define N_ZONES     16
#define OUR_ZONE    15
#define MAP_TYPE    0xE045E8u   /* guest u32: 0 overworld, 2 underwater, 3 snow */
/* BUILD 719: the savemap's options byte (bank 1 +0x0A, 0xDC08E6) is NOT
 * read. ff7nx_daynight arms the cycle itself (force_on: bit 0 is ORed in a
 * register, the tint runs regardless) and only the FIELD tick ever writes
 * the byte back. A save loaded on the world map with bit 0 clear -- any save
 * made before the cycle existed, until the first field -- had the tint but
 * none of this: the stock sky, the towns' own looks near Midgar and Cosmo
 * Canyon, until a town or the Highwind wrote the bit. wmtime is installed
 * only with the cycle, so the cycle being in IS the switch. */
#define MINUTES     0xDC08E8u
#define HOURS       0xDC08EBu
/* How much of the towns' LIGHT (of 256) the clock takes; the sky takes all
 * of theirs. The day/night tint already darkens the ground, and the full
 * Midgar light under it is close to black (PREVIEW-649). */
#define LIGHT_NIGHT 140
#define LIGHT_SUN   128

typedef struct {
    u32 active;                 /* wt_pre changed the table this frame */
    u8 flags[N_ZONES];
    u8 saved[ZONE_BYTES];       /* zone 15 as the scripts left it */
    u32 night, sunset;          /* last weights (0..256), for tests */
    u8 **pages;
} State;

_Static_assert(sizeof(State) <= 0x60, "STATE_BYTES");

static inline __attribute__((always_inline)) u8 *g2h(u8 **pages, u32 g)
{
    u8 *p = pages[g >> 12];
    return p ? p + (g & 0xFFFu) : 0;
}

/* 0..256 smoothstep of t over [a, b] (minutes) */
static inline __attribute__((always_inline)) u32 ramp(s32 t, s32 a, s32 b)
{
    s32 x;
    if (t <= a)
        return 0;
    if (t >= b)
        return 256;
    x = ((t - a) << 8) / (b - a);              /* 0..256 */
    return (u32)((x * x * (768 - 2 * x)) >> 16);
}

/*
 * The weights, on the clock FFNx's tint uses (night until 06:00, a reddish
 * morning at 07:00 fading white by 15:00, a red afternoon at 19:00, night
 * from 20:00):
 *   night   1 from 20:00 to 06:00; in 19:00->20:00, out 06:00->07:00
 *   sunset  dawn: in 06:00->07:00 as night leaves, out 07:00->10:00
 *           dusk: in 17:00->19:00, out 19:00->20:00 as night arrives
 * night + sunset <= 1 everywhere: each hand-over is one crossfade.
 */
static inline __attribute__((always_inline)) void weights(s32 m, u32 *night, u32 *sunset)
{
    u32 n, s;
    if (m < 6 * 60 || m >= 20 * 60) {
        n = 256; s = 0;
    } else if (m < 7 * 60) {
        s = ramp(m, 6 * 60, 7 * 60); n = 256 - s;
    } else if (m < 17 * 60) {
        n = 0; s = 256 - ramp(m, 7 * 60, 10 * 60);
    } else if (m < 19 * 60) {
        n = 0; s = ramp(m, 17 * 60, 19 * 60);
    } else {
        n = ramp(m, 19 * 60, 20 * 60); s = 256 - n;
    }
    *night = n; *sunset = s;
}

static inline __attribute__((always_inline)) u8 mix(u32 d, u32 nv, u32 sv, u32 n, u32 s)
{
    return (u8)((d * (256 - n - s) + nv * n + sv * s) >> 8);
}

__attribute__((section(".text.wt_pre")))
void wt_pre(State *st, u8 **pages)
{
    u8 *z, *mi, *hr, *mt;
    u32 n, s, i, m;
    /* light / top / horizon: default, wm0.ev zone 0 (Midgar), zone 2 (Cosmo
     * Canyon). The light default is the game's 1774 / 7 (light 0 = 0x6E). */
    u32 d[9], nt[9], ss[9];
    st->active = 0;
    st->pages = pages;
    if (!pages)
        return;
    mt = g2h(pages, MAP_TYPE);
    mi = g2h(pages, MINUTES);
    hr = g2h(pages, HOURS);
    z = g2h(pages, ZONES);
    if (!mt || !mi || !hr || !z)
        return;
    if (*(u32 *)mt != 0)
        return;
    if (*hr > 23 || *mi > 59)
        return;
    m = (u32)*hr * 60 + *mi;
    weights((s32)m, &n, &s);
    st->night = n; st->sunset = s;

    d[0] = 253; d[1] = 253; d[2] = 253;  d[3] = 0;   d[4] = 0;   d[5] = 255;
    d[6] = 250; d[7] = 255; d[8] = 255;
    /* BUILD 649b: the night SKY is darker than Midgar's (0,0,65 ->
     * 145,255,190 read too bright under the tint on hardware); the light
     * stays Midgar's. Clouds and the meteor are their own sprites. */
    nt[0] = 60; nt[1] = 90; nt[2] = 100; nt[3] = 0;  nt[4] = 0;  nt[5] = 28;
    nt[6] = 60; nt[7] = 100; nt[8] = 95;
    ss[0] = 255; ss[1] = 130; ss[2] = 40; ss[3] = 130; ss[4] = 120; ss[5] = 180;
    ss[6] = 240; ss[7] = 100; ss[8] = 50;

    for (i = 0; i < N_ZONES; i++)
        st->flags[i] = z[ZONE_BYTES * i + 0x13];
    for (i = 0; i < ZONE_BYTES; i++)
        st->saved[i] = z[ZONE_BYTES * OUR_ZONE + i];
    st->active = 1;
    for (i = 0; i < N_ZONES; i++)
        z[ZONE_BYTES * i + 0x13] = 0;                 /* the towns: muted */
    if (n + s == 0)
        return;                                       /* plain day: defaults */
    {
        u8 *q = z + ZONE_BYTES * OUR_ZONE;
        for (i = 0; i < 3; i++) {
            q[0x10 + i] = mix(d[i], nt[i], ss[i], (n * LIGHT_NIGHT) >> 8,
                              (s * LIGHT_SUN) >> 8);
            q[0x14 + i] = mix(d[3 + i], nt[3 + i], ss[3 + i], n, s);
            q[0x18 + i] = mix(d[6 + i], nt[6 + i], ss[6 + i], n, s);
        }
        q[0x13] = 1;
        *(u32 *)(q + 0x1C) = 1;                       /* outer: d = 0 < 1024 */
        *(u32 *)(q + 0x20) = 0x100000;                /* inner << 10: the planet */
    }
}

__attribute__((section(".text.wt_post")))
void wt_post(State *st)
{
    u8 *z;
    u32 i;
    if (!st->active || !st->pages)
        return;
    st->active = 0;
    z = g2h(st->pages, ZONES);
    if (!z)
        return;
    for (i = 0; i < ZONE_BYTES; i++)
        z[ZONE_BYTES * OUR_ZONE + i] = st->saved[i];
    for (i = 0; i < N_ZONES; i++)
        z[ZONE_BYTES * i + 0x13] = st->flags[i];
}
