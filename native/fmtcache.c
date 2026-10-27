/*
 * fmtcache.c (BUILD 813b) -- audio.fmt, and the world-footstep bank of
 * audio.dat, served from memory.
 *
 * PROBE-812/813 (wmfpsprobe v1..v6, the world map after a save load): about
 * every 20 frames (the 0.30 s step cadence of ff7nx_worldsteps, row 744,
 * never cached by design) one frame took 40..70 ms, inside one main-thread
 * storage read:
 *   812: 24 bytes of rom://ff7/workingdir/data/sound/audio.fmt   ~55 ms
 *   813: with audio.fmt cached, 17 KB of .../sound/audio.dat at offset
 *        0x64C4A4D -- a padded footstep variant in the bank the build
 *        appends (build.py, plan.world_footsteps['payload_base'])  ~50 ms
 * Each variant gets fast once storage has read it, which is why it "fixes
 * itself" (and why a battle, which reads around it, warms it).
 *
 * The port's stream read (+0x1119350, read(stream, buf, size): path at +8
 * (char[256]), position at +280, file size at +288 -- the open at
 * +0x1118DB0 stores nn::fs::GetFileSize there) is entered through a cave that
 * calls fc_read(state, stream, buf, size, stock, range):
 *   - a path ending in "sound/audio.fmt": the whole file (<= 64 KB) is read
 *     once, then every read is a copy;
 *   - a path ending in "sound/audio.dat", when the build passed a range
 *     (range[0] = offset, range[1] = length, both 0 = none): on the first
 *     read of audio.dat at all (boot), that range is read once into the
 *     state; afterwards a read lying entirely inside it is a copy.
 * Copies give the same bytes, position update and return value the stock
 * read gives. Anything else -- another file, a read outside the range, a
 * failed fill -- returns -1: the stock read runs unchanged. A failed fill is
 * never retried. Neither file is written while the game runs.
 */
typedef unsigned char u8;
typedef unsigned int u32;
typedef long long s64;
typedef unsigned long long u64;
typedef s64 (*read_fn)(u8 *stream, u8 *buf, u64 size);

#define CAP      65536u
#define PATH_OFF 8u
#define PATH_MAX 256u
#define POS_OFF  280u
#define SIZE_OFF 288u

typedef struct {
    u32 n;          /* audio.fmt bytes held; 0 = not filled yet */
    u32 bad;        /* audio.fmt fill failed */
    u32 hits;       /* audio.fmt reads served */
    u32 dstate;     /* audio.dat range: 0 not yet, 1 held, 2 failed */
    u32 dhits;      /* audio.dat reads served */
    u32 pad[3];
    u8 buf[CAP];
    u8 dbuf[];      /* the range: range[1] bytes (the build sizes the BSS) */
} State;

/* 1 = audio.fmt, 2 = audio.dat, 0 = neither */
static int which(const u8 *p)
{
    static const char want[] = "sound/audio.fm";      /* + 't' or 'dat' */
    u32 len = 0;
    while (len < PATH_MAX && p[len]) len++;
    if (len == PATH_MAX || len < sizeof want) return 0;
    const u8 *t = p + len - sizeof want;
    for (u32 i = 0; i < sizeof want - 3; i++) {     /* "sound/audio." */
        u8 c = t[i];
        if (c >= 'A' && c <= 'Z') c = (u8)(c + 32);
        if (c == '\\') c = '/';
        if (c != (u8)want[i]) return 0;
    }
    u8 e[3];
    for (u32 i = 0; i < 3; i++) {
        u8 c = t[sizeof want - 3 + i];
        e[i] = (c >= 'A' && c <= 'Z') ? (u8)(c + 32) : c;
    }
    if (e[0] == 'f' && e[1] == 'm' && e[2] == 't') return 1;
    if (e[0] == 'd' && e[1] == 'a' && e[2] == 't') return 2;
    return 0;
}

static s64 serve(u8 *stream, u8 *buf, u64 size, const u8 *src, u64 lo, u64 n)
{
    u64 *pos = (u64 *)(void *)(stream + POS_OFF);
    u64 at = *pos;
    if (at < lo) return -1;
    u64 k = at - lo >= n ? 0 : n - (at - lo);
    if (k > size) k = size;
    const u8 *s = src + (at - lo);
    for (u64 i = 0; i < k; i++) buf[i] = s[i];
    *pos = at + k;
    return (s64)k;
}

static int fill(u8 *stream, read_fn stock, u8 *dst, u64 from, u64 n)
{
    u64 *pos = (u64 *)(void *)(stream + POS_OFF);
    u64 keep = *pos;
    *pos = from;
    s64 got = stock(stream, dst, n);
    *pos = keep;
    return got > 0 && (u64)got == n;
}

__attribute__((section(".text.fc_read")))
s64 fc_read(State *st, u8 *stream, u8 *buf, u64 size, read_fn stock,
            const u64 *range)
{
    if (!st || !stream) return -1;
    int w = which(stream + PATH_OFF);
    u64 fsize = *(u64 *)(void *)(stream + SIZE_OFF);
    if (w == 1) {
        if (st->bad) return -1;
        if (!st->n) {
            if (!fsize || fsize > CAP || !fill(stream, stock, st->buf, 0, fsize)) {
                st->bad = 1;
                return -1;
            }
            st->n = (u32)fsize;
        }
        st->hits++;
        return serve(stream, buf, size, st->buf, 0, st->n);
    }
    if (w == 2 && range && range[1]) {
        u64 lo = range[0], n = range[1];
        if (st->dstate == 0)
            st->dstate = (lo + n <= fsize && fill(stream, stock, st->dbuf, lo, n)) ? 1 : 2;
        if (st->dstate != 1) return -1;
        u64 at = *(u64 *)(void *)(stream + POS_OFF);
        if (at < lo || at + size > lo + n) return -1;    /* not wholly inside */
        st->dhits++;
        return serve(stream, buf, size, st->dbuf, lo, n);
    }
    return -1;
}
