/* Host harness for worldfar.c (BUILD 588): a fake guest, a fake GTE and
 * transform, a fake native window and a recorder in place of the shape
 * allocator. Checks: the world-fixed planet on the native window against
 * the formula, and the far tiles against the window (the seam); the depth
 * squeeze; the sink; the matrix calibration; placement, single coverage and
 * no holes; the window fill and the full-detail cache (no file access after
 * load); the horizon and the sky slices; 360-degree movement, camera follow,
 * right-stick yaw; ZL/ZR zoom, tilt, the ZL+ZR reset click.
 *
 *   worldfar_host_test <wm0.far>
 */
#define HOST_TEST 1
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <math.h>
#include "worldfar.c"
#include <time.h>
static double draw_ms_last;
static double now_ms_(void) { struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts); return ts.tv_sec * 1e3 + ts.tv_nsec / 1e6; }

#define GMEM (48u << 20)
static u8 *mem;
static FILE *farf;
static u32 world_base = 0x01000000u;
static u32 gte_state = 0x00100000u;
static int errors;
#define SHAPE_BASE 0x01800000u
#define SHAPE_END  0x02400000u
static int alloc_mode;
static int sky_quad_calls;
static int max_alloc;
static int test_model;
static u32 shape_cur = SHAPE_BASE;
static int nshapes, nallocs, nreads;
static double bend_err, bend_n;
static double cam_h = 500.0;            /* BUILD 605: the focus height (Highwind altitude) */
static double FOC = 500.0;
static const double CXP = 320.0, CYP = 240.0, ZN = 50.0, ZF = 60000.0;
static const double RR = 250000.0;

static u8 *g2h(u32 g)
{
    if (g >= GMEM) { fprintf(stderr, "guest OOB %08x\n", g); exit(2); }
    return mem + g;
}
static s32 gi(u32 g) { s32 x; memcpy(&x, mem + g, 4); return x; }
static void si(u32 g, s32 x) { memcpy(mem + g, &x, 4); }
static s16 gs(u32 g) { s16 x; memcpy(&x, mem + g, 2); return x; }
static void sf(u32 g, float x) { memcpy(mem + g, &x, 4); }

static double Rf[3][3];
static s32 T[3];
static State st;
static u32 callx(u32 fn, u32 n, ...);

static int seen_l1[N1], seen_l2[N2], seen_lvl[N1];
static int native_cell[N1];
static double cur_px, cur_pz;        /* the fake window's mesh offset */

static int wrap_copies;
static int own_now;                  /* BUILD 606: this frame's window is ours */
static long own_cells, own_cells_s;
static int own_still;                /* the focus has not jumped since the last frame */
static s32 last_fx = -1, last_fz = -1;
static long l2_wanted, l2_drawn; static int l2_short_frames;
static void host_l2(int drawn, int wanted)
{
    l2_wanted += wanted; l2_drawn += drawn;
    if (drawn < wanted) l2_short_frames++;
}
static void host_tile(int level, int id, int px, int pz)
{
    (void)px; (void)pz;
    if (level == 4) {                    /* BUILD 594: a wrapped copy of a sector */
        wrap_copies++;
        /* only ever past the nearest copy: beyond half the world on one axis */
        if (abs(px) < WRAP_X / 2 - 2 * MESH && abs(pz) < WRAP_Z / 2 - 2 * MESH) {
            fprintf(stderr, "L2 copy %d at (%d, %d) inside the nearest copy\n", id, px, pz); errors++;
        }
        return;
    }
    if (level == 3) {
        if (id < 0 || id >= N2 || seen_l2[id]++) { fprintf(stderr, "L2 %d twice\n", id); errors++; }
    } else {
        if (id < 0 || id >= N1 || seen_l1[id]++) { fprintf(stderr, "cell %d twice\n", id); errors++; }
        if (id >= 0 && id < N1) seen_lvl[id] = level;
        if (id >= 0 && id < N1 && native_cell[id]) {
            if (!own_now) { fprintf(stderr, "cell %d drawn over the native window\n", id); errors++; }
            else { own_cells++; if (level != 2 && own_still) own_cells_s++; }
        }
    }
}

static void combined(const s32 t[3], double m[16])
{
    double R[3][3];
    for (int i = 0; i < 3; i++) for (int j = 0; j < 3; j++)
        R[i][j] = gs(gte_state + 0x10 + 2 * (3 * i + j)) / 4096.0;
    double A = ZF / (ZF - ZN), B = -ZN * ZF / (ZF - ZN);
    for (int r = 0; r < 4; r++) {
        double vx = r < 3 ? R[0][r] : t[0];
        double vy = r < 3 ? R[1][r] : t[1];
        double vz = r < 3 ? R[2][r] : t[2];
        m[r * 4 + 0] = FOC * vx + CXP * vz;
        m[r * 4 + 1] = FOC * vy + CYP * vz;
        m[r * 4 + 2] = A * vz + (r == 3 ? B : 0.0);
        m[r * 4 + 3] = vz;
    }
}

/* the port's 0x10F28B0 + the hooked bend, checked against the planet */
static double height_worst; static int height_checks, height_bad;
static void transform_emu(u32 verts)
{
    s32 t[3] = { gi(gte_state + 0x22), gi(gte_state + 0x26), gi(gte_state + 0x2a) };
    double m[16];
    combined(t, m);
    for (int i = 0; i < 16; i++) sf(G_COMBINED + 4 * i, (float)m[i]);
    double pv[122][3];
    for (int i = 0; i < 122; i++) {
        float p[3] = { gs(verts + 8 * i), gs(verts + 8 * i + 2), gs(verts + 8 * i + 4) };
        memcpy(mem + G_VERTS_F + 12 * i, p, 12);
        for (int k = 0; k < 3; k++) pv[i][k] = p[k];
    }
    int busy = st.busy;
    worldfar_bend(&st, g2h, callx);                  /* the hook */
    /* BUILD 593: the game's ground height (0x76085F) from the bent vertices,
       then worldfar_height: every vertex's height is its flat one again */
    for (u32 i = 0; i < 122; i += 7) {
        float b[3]; memcpy(b, mem + G_VERTS_F + 12 * i, 12);
        u8 cx[0x40] = { 0 };
        u32 esp = 0x00381000, pos = 0x00381100, out = 0x00381200;
        for (int k = 0; k < 3; k++) { s16 w = gs(verts + 8 * i + 2 * k); memcpy(mem + pos + 2 * k, &w, 2); }
        si(esp, 0); si(esp + 4, pos); si(esp + 8, out);
        si(out, (s32)lrint(b[1]));
        st32(cx, 1); st32(cx + 0x10, esp);
        worldfar_height(&st, g2h, callx, cx);
        double want = busy ? lrint(b[1]) : pv[i][1];
        double e = fabs((double)gi(out) - want);
        if (e > height_worst) height_worst = e;
        height_checks++;
        st32(cx, 0); si(out, 12345);                 /* eax 0: *out untouched */
        worldfar_height(&st, g2h, callx, cx);
        if (gi(out) != 12345) height_bad++;
    }
    if (busy) return;
    for (u32 i = 0; i < 122; i++) {
        double ax = cur_px + pv[i][0], az = cur_pz + pv[i][2];
        double q[3] = { pv[i][0], pv[i][1] - (ax * ax + az * az) / (2.0 * RR), pv[i][2] };
        double c[4];
        for (int cc = 0; cc < 4; cc++) c[cc] = q[0] * m[cc] + q[1] * m[4 + cc] + q[2] * m[8 + cc] + m[12 + cc];
        if (c[3] < 100) continue;
        double sx0 = c[0] / c[3], sy0 = c[1] / c[3];
        if (fabs(sx0 - CXP) > 1200 || fabs(sy0 - CYP) > 1200) continue;
        float b[3]; memcpy(b, mem + G_VERTS_F + 12 * i, 12);
        double d[4];
        for (int cc = 0; cc < 4; cc++) d[cc] = b[0] * m[cc] + b[1] * m[4 + cc] + b[2] * m[8 + cc] + m[12 + cc];
        double err = fmax(fabs(d[0] / d[3] - sx0), fabs(d[1] / d[3] - sy0));
        if (err > bend_err) { bend_err = err; if (err > 0.2) fprintf(stderr, "bend err %.3f at %d\n", err, i); }
        bend_n++;
        if (fabs(d[2] / d[3] - c[2] / c[3]) > 1e-4 * (1.0 + fabs(c[2] / c[3]))) { fprintf(stderr, "depth moved\n"); errors++; }
    }
}

static void reset_gte(void)
{
    memcpy(mem + gte_state + 0x10, mem + G_CAM_ROT, 18);
    memcpy(mem + gte_state + 0x22, mem + 0xDE6A20 + 0x12, 12);
}

static u32 callx(u32 fn, u32 n, ...)
{
    va_list ap; va_start(ap, n);
    u32 a[8] = {0};
    for (u32 i = 0; i < n; i++) a[i] = va_arg(ap, u32);
    va_end(ap);
    switch (fn) {
    case F_RESET_GTE: reset_gte(); return 0;
    case F_APPLY: {
        double v[3] = { gs(a[0]), gs(a[0] + 2), gs(a[0] + 4) };
        for (int i = 0; i < 3; i++) {
            double r = 0;
            for (int j = 0; j < 3; j++) r += gs(gte_state + 0x10 + 2 * (3 * i + j)) / 4096.0 * v[j];
            si(a[1] + 4 * i, (s32)r + gi(gte_state + 0x22 + 4 * i));
        }
        si(a[2], 0);
        return 0;
    }
    case F_NAME: strcpy((char *)mem + 0xDE6A48, "rom:/ff7/workingdir/data/wm/WM0.MAP"); return 0xDE6A48;
    case F_READ: {
        const char *nm = (const char *)mem + a[0];
        nreads++;
        if (!strstr(nm, "WM0.FAR")) { fprintf(stderr, "bad name %s\n", nm); errors++; return 1; }
        if (a[2] > FAR_IO_BYTES) { fprintf(stderr, "read of %u bytes\n", a[2]); errors++; return 1; }
        fseek(farf, a[1], SEEK_SET);
        if (fread(mem + a[3], 1, a[2], farf) != a[2]) return 1;
        return 0;
    }
    case F_TRANSFORM: transform_emu(a[0]); return 0;
    case F_DRAW_ENABLED: return 1;
    case F_SKY_QUAD: sky_quad_calls++; if (a[0] != 0x00231000u) errors++; return 0;
    case F_PLAYER_MODEL: return (u32)test_model;
    case F_ALLOC_SHAPES: {
        u32 obj = a[1];
        if (a[0] == 0 || a[0] > 512) { fprintf(stderr, "alloc of %u shapes\n", a[0]); errors++; }
        max_alloc = a[0] > (u32)max_alloc ? (int)a[0] : max_alloc;
        u32 stride = gi(obj + 0x40) ? (u32)gi(obj + 0x40) : 0x60;
        u32 rem = gi(obj + 0x50);
        if (rem < a[0]) rem = 32 > a[0] ? 32 : a[0];
        if (shape_cur + a[0] * stride + 0x2000 > SHAPE_END) return 0;
        if (alloc_mode == 2)             /* a new chunk, elsewhere */
            shape_cur += 0x1000;
        alloc_mode = 0;
        si(obj + 0x70, shape_cur);
        si(obj + 0x40, stride);
        si(obj + 0x50, rem - a[0]);
        shape_cur += a[0] * stride;
        nshapes += a[0];
        nallocs++;
        return 1;
    }
    }
    fprintf(stderr, "unexpected call %x\n", fn); errors++;
    return 0;
}

/* a game-like camera: model +y up, view y down the screen, looking down by
   `pitch` along heading `yaw`, `dist` from the focus */
static void set_camera(double yaw, double pitch, double dist, s32 fx, s32 fz)
{
    double f[3] = { sin(yaw) * cos(pitch), -sin(pitch), cos(yaw) * cos(pitch) };
    double r[3] = { cos(yaw), 0, -sin(yaw) };
    double d[3] = { r[1] * f[2] - r[2] * f[1], r[2] * f[0] - r[0] * f[2], r[0] * f[1] - r[1] * f[0] };
    double *rows[3] = { r, d, f };
    for (int i = 0; i < 3; i++) for (int j = 0; j < 3; j++) {
        s16 q = (s16)lrint(rows[i][j] * 4096.0);
        memcpy(mem + G_CAM_ROT + 2 * (3 * i + j), &q, 2);
        Rf[i][j] = q / 4096.0;
    }
    T[0] = 0; T[1] = 0; T[2] = (s32)dist;
    for (int i = 0; i < 3; i++) si(0xDE6A20 + 0x12 + 4 * i, T[i]);
    si(G_CAM_DELTA_Y, (s32)cam_h);
    si(G_PLAYER_POS, fx); si(G_PLAYER_POS + 4, 0); si(G_PLAYER_POS + 8, fz);
}

static void gte_for(double px, double py, double pz)
{
    double pos[3] = { px, py, pz };
    for (int i = 0; i < 3; i++) {
        double r = 0;
        for (int j = 0; j < 3; j++) r += Rf[i][j] * pos[j];
        si(gte_state + 0x22 + 4 * i, (s32)r + T[i]);
    }
    cur_px = px; cur_pz = pz;
}

#define FAKE_VERTS 0x00300000u
static void fake_window(s32 fx, s32 fz, int skip_mask)
{
    memset(native_cell, 0, sizeof native_cell);
    reset_gte();
    int cx = fx >> 13, cz = fz >> 13, lx = fx & 0x1FFF, lz = fz & 0x1FFF;
    int k = 0;
    for (int dz = -2; dz <= 2; dz++) for (int dx = -2; dx <= 2; dx++, k++) {
        if (skip_mask & (1 << k)) continue;
        gte_for(dx * MESH - lx, -cam_h, dz * MESH - lz);
        transform_emu(FAKE_VERTS);
        int mx = ((cx + dx) % MX + MX) % MX, mz = ((cz + dz) % MZ + MZ) % MZ;
        native_cell[mz * MX + mx] = 1;
    }
    reset_gte();
}

static int run(double yaw, double pitch, double dist, s32 fx, s32 fz, int skip, int verbose)
{
    memset(seen_l1, 0, sizeof seen_l1);
    memset(seen_l2, 0, sizeof seen_l2);
    memset(seen_lvl, 0, sizeof seen_lvl);
    int e0 = errors;
    set_camera(yaw, pitch, dist, fx, fz);
    fake_window(fx, fz, skip);
    shape_cur = SHAPE_BASE; nshapes = 0; nallocs = 0;
    for (int k = 0; k < 512; k++) si(0x00220000 + 0x100 * k + 0x50, 0);
    own_now = (int)st.own_window;
    own_still = fx == last_fx && fz == last_fz;
    last_fx = fx; last_fz = fz;
    { double _t = now_ms_(); worldfar_draw(&st, g2h, callx, 1.0f); draw_ms_last = now_ms_() - _t; }
    if (st.nnat) { fprintf(stderr, "native records not cleared\n"); errors++; }
    if ((u32)nshapes != st.last_tris + st.sky_quads) { fprintf(stderr, "shapes %d tris %u sky %u\n", nshapes, st.last_tris, st.sky_quads); errors++; }
    for (u32 q = SHAPE_BASE; q < shape_cur; q += 0x20) {
        float f[8]; memcpy(f, mem + q, 32);
        if (!(f[3] > 0) || !(f[2] < 1.0f) || f[0] != f[0] || f[1] != f[1]) { fprintf(stderr, "bad TL vertex %g %g %g %g\n", f[0], f[1], f[2], f[3]); errors++; break; }
    }
    int l1 = 0, l2 = 0, nfine = 0;
    for (int k = 0; k < N1; k++) if (seen_l1[k]) { l1++; if (seen_lvl[k] == 2) nfine++; }
    for (int k = 0; k < N2; k++) if (seen_l2[k]) l2++;
    int cx = fx >> 13, cz = fz >> 13;
    for (int s = 0; s < N2; s++) if (seen_l2[s]) {
        int sx = s % SX, sz = s / SX;
        for (int k = 0; k < 16; k++) {
            int mx = sx * 4 + (k & 3), mz = sz * 4 + (k >> 2);
            if (native_cell[mz * MX + mx] || seen_l1[mz * MX + mx]) { fprintf(stderr, "L2 %d overlaps mesh %d,%d\n", s, mx, mz); errors++; }
        }
    }
    for (int mz = 0; mz < MZ; mz++) for (int mx = 0; mx < MX; mx++) {
        double cxw = wrapi(mx * MESH + MESH / 2 - fx, WRAP_X);
        double czw = wrapi(mz * MESH + MESH / 2 - fz, WRAP_Z);
        if (sqrt(cxw * cxw + czw * czw) > 45000) continue;
        double vz = T[2] + Rf[2][0] * cxw + Rf[2][1] * -cam_h + Rf[2][2] * czw;
        double vx = T[0] + Rf[0][0] * cxw + Rf[0][1] * -cam_h + Rf[0][2] * czw;
        if (vz < 12000 || fabs(vx) > vz) continue;
        int s = (mz >> 2) * SX + (mx >> 2);
        if (native_cell[mz * MX + mx] || seen_l1[mz * MX + mx] || seen_l2[s]) continue;
        fprintf(stderr, "hole at mesh %d,%d (d %d,%d)\n", mx, mz, wrapi(mx - cx, MX), wrapi(mz - cz, MZ));
        errors++;
    }
    if (verbose)
        printf("yaw %.2f pitch %.2f dist %.0f focus %d,%d skip %x: cells %d (full %d) L2 %d tris %u allocs %d errors %d\n",
               yaw, pitch, dist, fx, fz, skip, l1, nfine, l2, st.last_tris, nallocs, errors - e0);
    return errors - e0;
}

/* the far tiles' projection against the window's (bend) for the same
   points: the seam between the game's cells and ours */
static void test_seam(void)
{
    double worst = 0;
    srand(11);
    for (int it = 0; it < 300; it++) {
        double yaw = (rand() % 6283) / 1000.0, pitch = 0.2 + (rand() % 1100) / 1000.0;
        double dist = 3000 + rand() % 60000;
        set_camera(yaw, pitch, dist, 100000, 100000);
        reset_gte();
        Ctx x;
        Cam cam;
        Env env = { g2h, callx, &st };
        camera(&env, world_base + FAR_OFF + FAR_SCRATCH, &cam);
        x.v = &env; x.c = &cam; x.gte = gte_state;
        if (!calibrate(&env, &x, world_base + FAR_OFF + FAR_IO, 5000)) { fprintf(stderr, "calibration failed\n"); errors++; continue; }
        s32 px = (rand() % 13 - 6) * MESH - 1234, pz = (rand() % 13 - 6) * MESH - 777;
        double mf[16];
        tile_matrix(&x, px, pz, mf);
        reset_gte();
        gte_for(px, -500.0, pz);
        double m[16];
        s32 t[3] = { gi(gte_state + 0x22), gi(gte_state + 0x26), gi(gte_state + 0x2a) };
        transform_emu(FAKE_VERTS);
        st.nnat = 0;
        combined(t, m);
        for (int i = 0; i < 122; i++) {
            float b[3]; memcpy(b, mem + G_VERTS_F + 12 * i, 12);
            double d[4];
            for (int cc = 0; cc < 4; cc++) d[cc] = b[0] * m[cc] + b[1] * m[4 + cc] + b[2] * m[8 + cc] + m[12 + cc];
            if (d[3] < 200) continue;
            double sxA = d[0] / d[3], syA = d[1] / d[3];
            if (fabs(sxA - CXP) > 1500 || fabs(syA - CYP) > 1500) continue;
            float p0 = gs(FAKE_VERTS + 8 * i), p1 = gs(FAKE_VERTS + 8 * i + 2), p2 = gs(FAKE_VERTS + 8 * i + 4);
            float ax = px + p0, az = pz + p2;
            p1 -= (ax * ax + az * az) * (0.5f / SPHERE_R);
            float sxB, syB;
            if (!proj(mf, p0, p1, p2, &sxB, &syB)) continue;
            double e = fmax(fabs(sxA - sxB), fabs(syA - syB));
            if (e > worst) worst = e;
        }
    }
    reset_gte();
    printf("seam: far tiles within %.3f px of the window\n", worst);
    if (worst > 0.75) errors++;          /* one unit of GTE translation rounding at 1000 units */
}

static void test_sink(void)
{
    u8 ctx[0x40] = { 0 };
    u32 esp = 0x00380000;
    si(esp, 0xDEAD); si(esp + 4, 30000); si(esp + 8, -40000);
    st32(ctx + 0x10, esp);
    worldfar_sink(&st, g2h, callx, ctx, 0xF93C8C);
    s32 eax = (s32)ld32(ctx);
    s32 want = (s32)lrint((30000.0 * 30000 + 40000.0 * 40000) / (2 * RR));
    printf("sink: (30000, -40000) drops %d (want %d)\n", eax, want);
    st32(ctx + 0x10, esp);
    worldfar_sink(&st, g2h, callx, ctx, 0xF65BC0);
    if ((s32)ld32(ctx) != (s32)lrint(want / 4.0)) { fprintf(stderr, "track sink %d\n", (s32)ld32(ctx)); errors++; }
    if (eax != want || ld32(ctx + 0x10) != esp + 4) errors++;
}

/* BUILD 591: the sky drawn by sky() on the planet's horizon */
#define SKY_OBJ 0x00232000u
#define MET_OBJ 0x00233000u
static void sky_setup(void)
{
    memset(mem + SKY_OBJ, 0, 0x100); memset(mem + MET_OBJ, 0, 0x100);
    si(SKY_OBJ + 0x40, 0x80); si(MET_OBJ + 0x40, 0x80);
    sf(SKY_OBJ + 0x24, 1.0f / 256); sf(SKY_OBJ + 0x28, 1.0f / 64);
    sf(MET_OBJ + 0x24, 1.0f / 256); sf(MET_OBJ + 0x28, 1.0f / 128);
    si(G_SKY_DESC + 0x28, SKY_OBJ);
    si(G_SKY_OBJ, SKY_OBJ); si(G_METEOR_OBJ, MET_OBJ);   /* the live ones */
    mem[G_SKY_DESC + 0x0D] = 0; mem[G_SKY_DESC + 0x1D] = 0x3F;
    si(G_SKY_DESC + 0x2C, (s32)0x807F7F7F);
    si(G_METEOR_DESC + 0x28, MET_OBJ);
    mem[G_METEOR_DESC + 0x0C] = 0; mem[G_METEOR_DESC + 0x14] = 0x8F;
    mem[G_METEOR_DESC + 0x0D] = 0; mem[G_METEOR_DESC + 0x1D] = 0x3F;
    si(G_METEOR_DESC + 0x2C, (s32)0x80FFFFFF);
    s16 two = 2; memcpy(mem + G_SKY_SCALE, &two, 2);
}

/* the true horizon on screen at screen column sx: scan the sea (y = -r^2/2R
   about the player) along that column for the highest point */
static double true_horizon(double sx)
{
    /* eye in world, relative to the player (focus at y = 500, set_camera) */
    double e[3];
    for (int j = 0; j < 3; j++) e[j] = -(Rf[0][j] * T[0] + Rf[1][j] * T[1] + Rf[2][j] * T[2]);
    e[1] += 500.0;
    double best = 1e30;
    /* the viewing ray for (sx, sy): d = R^T ((sx-CXP)/FOC, (sy-CYP)/FOC, 1) */
    double lo = -4000, hi = 4000;
    for (int it = 0; it < 60; it++) {          /* bisect on sy: hits the sea? */
        double sy = 0.5 * (lo + hi);
        double v[3] = { (sx - CXP) / FOC, (sy - CYP) / FOC, 1.0 }, d[3];
        for (int j = 0; j < 3; j++) d[j] = Rf[0][j] * v[0] + Rf[1][j] * v[1] + Rf[2][j] * v[2];
        /* y(t) = e1 + t d1 vs sea -(x^2+z^2)/2R: a quadratic in t */
        double A = (d[0] * d[0] + d[2] * d[2]) / (2 * RR);
        double B = d[1] + (e[0] * d[0] + e[2] * d[2]) / RR;
        double C = e[1] + (e[0] * e[0] + e[2] * e[2]) / (2 * RR);
        double disc = B * B - 4 * A * C;
        int hit = disc >= 0 && (-B + sqrt(disc)) > 0;
        if (hit) hi = sy; else lo = sy;
        best = hi;
    }
    return best;
}

/* BUILD 598: what 0x75A921 leaves behind when the world map exits (into
   fship_2 and every other field): the live sky objects and every texture
   object below the count are freed and ZEROED, but the cloud and meteor
   records keep their copies. Nothing may be allocated on, or written
   through, any of those. */
static void frame_only(void)
{
    memset(seen_l1, 0, sizeof seen_l1); memset(seen_l2, 0, sizeof seen_l2);
    memset(seen_lvl, 0, sizeof seen_lvl);
    set_camera(0.5, 0.2, 30000, 150000, 120000);
    fake_window(150000, 120000, 0);
    shape_cur = SHAPE_BASE;
    worldfar_draw(&st, g2h, callx, 1.0f);
}

static void test_after_exit(void)
{
    sky_setup();
    si(G_METEOR_ON, 1);
    frame_only();
    int live_tris = (int)st.last_tris, live_sky = (int)st.sky_quads;
    static u32 obj_save[0x11A];
    for (int k = 0; k < 0x11A; k++) { obj_save[k] = (u32)gi(G_TEX_OBJS + 4 * k); si(G_TEX_OBJS + 4 * k, 0); }
    si(G_SKY_OBJ, 0); si(G_METEOR_OBJ, 0);
    u8 sky_before[0x100], met_before[0x100];
    memcpy(sky_before, mem + SKY_OBJ, 0x100); memcpy(met_before, mem + MET_OBJ, 0x100);
    int a0 = nallocs;
    frame_only();
    int b1 = (nallocs != a0), b2 = memcmp(sky_before, mem + SKY_OBJ, 0x100) || memcmp(met_before, mem + MET_OBJ, 0x100);
    int b3 = st.sky_quads != 0, b4 = st.last_tris != 0;
    /* the next load, before 0x7543D3 refreshes the records: the copies are
       not the live objects, so the sky waits */
    for (int k = 0; k < 0x11A; k++) si(G_TEX_OBJS + 4 * k, obj_save[k]);
    si(G_SKY_OBJ, SKY_OBJ + 0x40000); si(G_METEOR_OBJ, MET_OBJ + 0x40000);
    frame_only();
    int b5 = st.sky_quads != 0 || memcmp(sky_before, mem + SKY_OBJ, 0x100);
    int b6 = st.last_tris == 0;
    /* no texture count, no texture objects looked up */
    si(G_TEX_COUNT, 0);
    frame_only();
    int b7 = st.last_tris != 0;
    si(G_TEX_COUNT, 0x11A);
    sky_setup();
    frame_only();
    int b8 = !st.sky_quads || !st.last_tris;
    int bad = b1 | b2 | b3 | b4 | b5 | b6 | b7 | b8;
    printf("after the world exit: nothing allocated on or written through freed objects "
           "(live frame %d tris %d sky quads): %s\n", live_tris, live_sky, bad ? "NO" : "yes");
    if (bad) { fprintf(stderr, "after exit: %d%d%d%d%d%d%d%d\n", b1, b2, b3, b4, b5, b6, b7, b8); errors++; }
}

static void test_sky(void)
{
    sky_setup();
    si(G_METEOR_ON, 1);
    int worst_i = 0;
    double worst = 0;
    int cases = 0, bad_u = 0, quads = 0;
    double worst_perp = 0, max_slope = 0;
    for (int c = 0; c < 6; c++) {
        double yaw = c == 0 ? M_PI : 0.4 + c, pitch = 0.05 + 0.1 * c, dist = 4000 + 7000 * c;
        run(yaw, pitch, dist, 150000, 120000, 0, 0);
        if (st.sky_frame != st.frames || !st.sky_quads) { fprintf(stderr, "no sky (case %d)\n", c); errors++; continue; }
        quads += st.sky_quads;
        u32 vp = gi(SKY_OBJ + 0x70);
        (void)vp;
        /* every cloud quad: foot 8*S below the true horizon, width one column of u */
        for (u32 q = SHAPE_BASE; q < shape_cur; q += 0x20) {
            float f[8]; memcpy(f, mem + q, 32);
            if (f[3] != SKY_RHW) continue;
            /* a quad: 4 vertices from here */
            float a[4][8];
            for (int k = 0; k < 4; k++) memcpy(a[k], mem + q + 0x20 * k, 32);
            q += 0x60;
            if (a[0][7] == 0.0f && a[2][7] > 0.9f && fabsf(a[0][6] - a[1][6]) < 0.2f) {   /* cloud */
                float du = fabsf(a[1][6] - a[0][6]) * 256.0f;
                if (fabsf(du - 256.0f / (SKY_GRID / SKY_TILES)) > 0.01f) bad_u++;
                /* BUILD 775: each edge stands along the horizon's normal, so
                   its foot is 16 px below the curve PERPENDICULARLY: on a
                   slope m that is 16 sqrt(1 + m^2) straight down, and the
                   edge (top minus foot) is square to the tangent (1, m). */
                for (int k = 2; k < 4; k++) {
                    if (a[k][0] < 0 || a[k][0] > 640) continue;
                    double m = 0.5 * (true_horizon(a[k][0] + 1.0) - true_horizon(a[k][0] - 1.0));
                    double want = true_horizon(a[k][0]) + 16.0 * sqrt(1.0 + m * m);
                    double e = fabs(a[k][1] - want);
                    if (e > worst) { worst = e; worst_i = c; }
                    double ex = a[k - 2][0] - a[k][0], ey = a[k - 2][1] - a[k][1];
                    double cosang = fabs(ex + m * ey) / (sqrt(ex * ex + ey * ey) * sqrt(1.0 + m * m));
                    if (cosang > worst_perp) worst_perp = cosang;
                    if (fabs(m) > max_slope) max_slope = fabs(m);
                }
            } else if (a[0][6] == 0.0f && fabsf(a[1][6] - 0x8F / 256.0f) < 1e-5f) {   /* meteor */
                /* BUILD 775: the meteor stands on the horizon along its
                   normal: its foot (the bottom edge's middle) is 4 px below
                   the curve perpendicularly, its sides square to it */
                float xm = 0.5f * (a[2][0] + a[3][0]), ym = 0.5f * (a[2][1] + a[3][1]);
                double m = 0.5 * (true_horizon(xm + 1.0) - true_horizon(xm - 1.0));
                double want = true_horizon(xm) + 4.0 * sqrt(1.0 + m * m);
                double wdt = sqrt((a[1][0] - a[0][0]) * (a[1][0] - a[0][0]) + (a[1][1] - a[0][1]) * (a[1][1] - a[0][1]));
                double sx = a[0][0] - a[2][0], sy = a[0][1] - a[2][1];
                double cosang = fabs(sx + m * sy) / (sqrt(sx * sx + sy * sy) * sqrt(1.0 + m * m));
                printf("meteor: case %d foot x %.1f (width %.0f), foot %.1f vs %.1f, slope %.3f, side vs tangent cos %.4f\n",
                       c, xm, wdt, ym, want, m, cosang);
                if (c == 0 && fabsf(xm - (float)CXP) > 1.0f) errors++;
                if (fabs(ym - want) > 1.5 || cosang > 0.02 || fabs(wdt - 0x90 * 2) > 0.5) errors++;
            }
        }
        cases++;
    }
    printf("sky: %d views, %d quads, cloud foot within %.2f px of the true horizon (case %d), %d bad columns\n",
           cases, quads, worst, worst_i, bad_u);
    printf("sky: column edges square to the horizon within cos %.4f (steepest slope %.3f)\n",
           worst_perp, max_slope);
    if (worst > 1.5 || bad_u || worst_perp > 0.03) errors++;
    /* the band is fixed to the world: turn a little, the same column keeps its u */
    si(G_METEOR_ON, 0);
    /* the stock quad routine is replaced: it pops its return address only */
    u8 ctx[0x40] = { 0 };
    u32 esp = 0x00380000;
    st32(ctx + 0x10, esp);
    int n0 = nshapes;
    worldfar_sky(&st, g2h, callx, ctx);
    if (ld32(ctx + 0x10) != esp + 4 || nshapes != n0) { fprintf(stderr, "stock sky stub\n"); errors++; }
    /* on the other world maps the stock routine still runs */
    si(esp + 4, 0x00231000); st32(ctx + 0x10, esp);
    si(G_MAP_TYPE, 3);
    worldfar_sky(&st, g2h, callx, ctx);
    si(G_MAP_TYPE, 0);
    printf("sky stub: overworld draws nothing, snowstorm calls the stock quad %d time(s)\n", sky_quad_calls);
    if (sky_quad_calls != 1 || ld32(ctx + 0x10) != esp + 4) errors++;
}

/* BUILD 591: the story variants */
static void test_variants(void)
{
    int bad = 0;
    /* progress 0: every cell is its own; the crater sector (50) after */
    for (int p = 0; p < 6; p++)
        for (int s = 0; s < N2B; s++) {
            int a = alt_of(s, p);
            int want = -1;
            if (s == 50 && p > 0) want = 0;
            if (s == 41 && p > 1) want = 1;
            if (s == 42 && p > 1) want = 2;
            if (s == 60 && p > 2) want = 3;
            if (s == 47 && p > 3) want = 4;
            if (s == 48 && p > 3) want = 5;
            if (a != want) bad++;
        }
    /* the variant tiles are in the file and differ from the base ones */
    int differ = 0;
    for (int a = 0; a < NALT; a++) {
        int bs = a == 0 ? 50 : a == 1 ? 41 : a == 2 ? 42 : a == 3 ? 60 : a == 4 ? 47 : 48;
        int mx = (bs % SX) * 4, mz = (bs / SX) * 4;
        if (l1_index(mx, mz, 0) != mz * MX + mx || l1_index(mx, mz, 9) != N1B + 16 * a) bad++;
        if (l2_index(bs, 9) != N1 + N2B + a || l2_index(bs, 0) != N1 + bs) bad++;
        const u8 *r0 = st.data + st.refs + 8u * (u32)(mz * MX + mx);
        const u8 *r1 = st.data + st.refs + 8u * (u32)(N1B + 16 * a);
        if (ld32(r0) != ld32(r1)) differ++;
    }
    printf("variants: %d mapping errors, %d of %d replacement sectors carried\n", bad, differ, NALT);
    if (bad || differ != NALT) errors++;
    /* drawing with every variant on */
    si(G_PROGRESS, 9);
    int e0 = errors;
    for (int i = 0; i < 20; i++) run(0.3 * i, 0.4, 20000, (i * 37000) % WRAP_X, (i * 23000) % WRAP_Z, 0, 0);
    si(G_PROGRESS, 0);
    printf("variants: 20 views with every replacement on, %d errors\n", errors - e0);
}


/* ---------------------------------------------------------------- bench
 * BUILD 605: `worldfar_host_test <wm0.far> bench` -- for a set of views,
 * zoom out step by step (as ZL does) and report, per distance, the
 * triangles, the full-detail (N) and coarse (S) cells, the host time of
 * worldfar_draw, and how many cells drawn at two consecutive zoom steps
 * changed level (N <-> S): the "detail pops in and out as I zoom". */
#include <time.h>
static double now_ms(void)
{
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1e3 + ts.tv_nsec / 1e6;
}
static int bench(void)
{
    static const double dists[] = { 4000, 7000, 10000, 14000, 18000, 23000, 29000, 35000 };
    static const double pitches[] = { 0.30, 0.55, 0.85, 1.20 };
    enum { ND = sizeof dists / sizeof dists[0], NP = sizeof pitches / sizeof pitches[0] };
    long flips = 0, both = 0; int nmax = 0;
    l2_drawn = l2_wanted = 0; l2_short_frames = 0;
    double tmax = 0, tsum = 0; long nt = 0;
    u32 trmax = 0; double trsum = 0;
    double dt_d[ND] = {0}, tr_d[ND] = {0}, n_d[ND] = {0}, s_d[ND] = {0}; int c_d[ND] = {0};
    u32 trmax_d[ND] = {0}; double tmax_d[ND] = {0};
    static int prev_lvl[N1], prev_seen[N1];
    srand(7);
    static const double alts[] = { 500, 12000, 25000 };
    for (int v = 0; v < 24; v++) {
        s32 fx = rand() % WRAP_X, fz = rand() % WRAP_Z;
        cam_h = alts[v % 3];
        double yaw = (rand() % 6283) / 1000.0;
        double pitch = pitches[v % NP];
        memset(prev_seen, 0, sizeof prev_seen);
        for (int di = 0; di < ND; di++) {
            for (int f = 0; f < 60; f++) run(yaw, pitch, dists[di], fx, fz, 0, 0);   /* cache settles */
            double dt = 1e9;
            for (int f = 0; f < 10; f++) { run(yaw, pitch, dists[di], fx, fz, 0, 0); if (draw_ms_last < dt) dt = draw_ms_last; }
            int nn = 0, ns = 0;
            for (int k = 0; k < N1; k++) if (seen_l1[k]) { if (seen_lvl[k] == 2) nn++; else ns++; }
            if (nn > nmax) nmax = nn;
            for (int k = 0; k < N1; k++) if (seen_l1[k] && prev_seen[k]) {
                both++;
                if (prev_lvl[k] != seen_lvl[k]) flips++;
            }
            for (int k = 0; k < N1; k++) { prev_seen[k] = seen_l1[k]; prev_lvl[k] = seen_lvl[k]; }
            if (dt > tmax) tmax = dt;
            tsum += dt; nt++;
            if (st.last_tris > trmax) trmax = st.last_tris;
            trsum += st.last_tris;
            dt_d[di] += dt; tr_d[di] += st.last_tris; n_d[di] += nn; s_d[di] += ns; c_d[di]++;
            if (st.last_tris > trmax_d[di]) trmax_d[di] = st.last_tris;
            if (dt > tmax_d[di]) tmax_d[di] = dt;
        }
    }
    cam_h = 500.0;
    printf("bench: LOD_RULE %d MAX_TRIS_FRAME %d NSLOT %d\n", LOD_RULE, MAX_TRIS_FRAME, NSLOT);
    printf("  %6s %8s %8s %8s %8s %8s %8s\n", "dist", "tris", "trismax", "N", "S", "ms", "msmax");
    for (int di = 0; di < ND; di++)
        printf("  %6.0f %8.0f %8u %8.1f %8.1f %8.3f %8.3f\n", dists[di], tr_d[di] / c_d[di], trmax_d[di],
               n_d[di] / c_d[di], s_d[di] / c_d[di], dt_d[di] / c_d[di], tmax_d[di]);
    printf("  all: tris avg %.0f max %u; ms avg %.3f max %.3f; level flips between zoom steps %ld of %ld (%.1f%%)\n",
           trsum / nt, trmax, tsum / nt, tmax, flips, both, both ? 100.0 * flips / both : 0.0);
    printf("  lens FOC %.0f; N max %d; horizon tiles %ld of %ld drawn, %d frames short\n", FOC, nmax, l2_drawn, l2_wanted, l2_short_frames);
    printf("  loads %u fail %u errors %d\n", st.loads, st.load_fail, errors);
    return errors ? 1 : 0;
}

int main(int argc, char **argv)
{
    if (argc < 2) return 3;
    mem = calloc(1, GMEM);
    farf = fopen(argv[1], "rb");
    if (!farf) return 3;
    si(G_WORLD_BASE, world_base);
    si(G_GTE, gte_state);
    si(G_ONSET, 5000);
    si(G_MAP_TYPE, 0);
    si(G_MOVE_MULT, 2);
    si(G_LIGHT, 0x00200000);
    { float L[12] = { 0.2f, -0.6f, 0.1f, 0.3f,  0.1f, -0.7f, 0, 0.3f,  0, -0.8f, 0.1f, 0.3f };
      memcpy(mem + 0x00200000 + 0xF8, L, 48); }
    si(G_TEX_ORIGIN, 0x00210000);
    for (int k = 0; k < 512; k++) si(G_TEX_OBJS + 4 * k, 0x00220000 + 0x100 * k);
    si(G_TEX_COUNT, 0x11A);                  /* wm0's 282 texture objects */
    for (int k = 0; k < 512; k++) { float s = 1.0f / 32; memcpy(mem + 0x00220000 + 0x100 * k + 0x24, &s, 4); memcpy(mem + 0x00220000 + 0x100 * k + 0x28, &s, 4); }
    si(G_VIEW_X, 0); si(G_VIEW_Y, 0); si(G_VIEW_W, 640); si(G_VIEW_H, 480);
    srand(3);
    for (int i = 0; i < 122; i++) {
        s16 v[4] = { (s16)(rand() % 8193), (s16)(rand() % 3000), (s16)(rand() % 8193), 0 };
        memcpy(mem + FAKE_VERTS + 8 * i, v, 8);
    }
    worldfar_draw(&st, g2h, callx, 1.0f);
    if (st.last_tris) { fprintf(stderr, "terrain drawn without data\n"); errors++; }   /* the sky may be */
    worldfar_alloc(&st, g2h, callx);
    if (st.magic != 0x52414657u) { fprintf(stderr, "load failed\n"); return 1; }
    int load_reads = nreads;
    worldfar_alloc(&st, g2h, callx);            /* a second world visit */
    if (nreads != load_reads) { fprintf(stderr, "loaded twice\n"); errors++; }
    printf("load: %d reads of wm0.far, once\n", load_reads);
    st.hw_pad2[0] = 1;                       /* BUILD 606: as the window caves do */
    if (argc > 2 && !strcmp(argv[2], "bench")) { if (argc > 3) FOC = atof(argv[3]); return bench(); }
    test_seam();
    test_sink();
    int bad = 0;
    srand(1);
    for (int i = 0; i < 300; i++) {
        double yaw = (rand() % 6283) / 1000.0;
        double pitch = 0.3 + (rand() % 900) / 1000.0;
        double dist = 3000 + rand() % 32000;
        s32 fx = rand() % WRAP_X, fz = rand() % WRAP_Z;
        int skip = (i % 3 == 0) ? (rand() & 0x1FFFFFF) : 0;
        for (int f = 0; f < 3; f++) bad += run(yaw, pitch, dist, fx, fz, skip, i < 4 && f == 2);
    }
    {
        int fine = 0;
        for (int f = 0; f < 40; f++) run(0.5, 0.9, 9000, 150000, 120000, 0, 0);
        for (int k = 0; k < N1; k++) if (seen_l1[k] && seen_lvl[k] == 2) fine++;
        printf("after 40 frames: %d cells at full detail, %u meshes decoded, %u failed\n", fine, st.loads, st.load_fail);
        /* BUILD 606: a flight across many cells (the Highwind at full speed,
           ~400 a frame, and faster) keeps the window ours, never coarse */
        long c0 = own_cells, s0 = own_cells_s; u32 f0 = st.own_frames;
        for (int f = 0; f < 600; f++) {
            s32 fx = 150000 + f * 400, fz = 120000 + f * 150;
            last_fx = fx; last_fz = fz;          /* continuous: counts as still */
            run(0.5 + f * 0.003, 0.9, 9000, fx % WRAP_X, fz % WRAP_Z, 0, 0);
        }
        printf("flight: %u of 600 frames with our window, %ld window cells, %ld coarse\n",
               st.own_frames - f0, own_cells - c0, own_cells_s - s0);
        if (OWN_WINDOW && (st.own_frames - f0 < 590 || own_cells_s != s0)) { fprintf(stderr, "flight\n"); errors++; }
        /* BUILD 606b: a world load right on a cell that was never read
           (the player's own, a window cell the game always drew) still ends
           with the window ours */
        {
            s32 fx = 61000, fz = 53000;
            int cx = fx >> 13, cz = fz >> 13;
            for (int dz = -3; dz <= 3; dz++) for (int dx = -3; dx <= 3; dx++) {
                int cell = l1_index(((cx + dx) % MX + MX) % MX, ((cz + dz) % MZ + MZ) % MZ, 0);
                Slot *sl = find_slot(&st, cell);
                if (sl) { sl->cell = -1; st.slot_of[cell] = -1; }
            }
            worldfar_alloc(&st, g2h, callx);
            u32 o1 = st.own_frames;
            last_fx = fx; last_fz = fz;
            for (int f = 0; f < 60; f++) run(0.3, 0.9, 9000, fx, fz, 0, 0);
            printf("spawn on unread cells: our window after %u of 60 frames\n", 60 - (st.own_frames - o1));
            if (OWN_WINDOW && st.own_frames - o1 < 50) { fprintf(stderr, "spawn\n"); errors++; }
        }
        if (st.load_fail) errors++;
        if (fine < 10) { fprintf(stderr, "the cache never filled the ring\n"); errors++; }
    }
    if (nreads != load_reads) { fprintf(stderr, "a file was read while drawing\n"); errors++; }
    printf("bend: %.0f vertices, worst %.4f px from the planet\n", bend_n, bend_err);
    if (bend_err > 0.25) { errors++; bad++; }
    if (st.calib_bad) { fprintf(stderr, "calibration failed %u times\n", st.calib_bad); errors++; }
    test_sky();
    test_after_exit();
    test_variants();

    /* controls */
    {
        static u8 obj[0x100], l3[0x100], l2[0x100], l1[0x100], holder[16];
        u8 *pl1 = l1, *pl2 = l2, *pl3 = l3, *pobj = obj;
        memcpy(holder + 0, &pl1, 8);
        memcpy(l1 + 8, &pl2, 8);
        memcpy(l2 + 0, &pl3, 8);
        memcpy(l3 + 0x88, &pobj, 8);
        u8 *hp = holder;
        u8 **got = &hp;
        float one = 1.0f, zero = 0.0f;
        #define RSTICK(u, d, r, l) do { float _u = u, _d = d, _r = r, _l = l; \
            memcpy(obj + 0x40, &_u, 4); memcpy(obj + 0x44, &_d, 4); \
            memcpy(obj + 0x48, &_r, 4); memcpy(obj + 0x4C, &_l, 4); } while (0)
        #define LSTICK(u, d, r, l) do { float _u = u, _d = d, _r = r, _l = l; \
            memcpy(obj + 0x30, &_u, 4); memcpy(obj + 0x34, &_d, 4); \
            memcpy(obj + 0x38, &_r, 4); memcpy(obj + 0x3C, &_l, 4); } while (0)
        #define BUTTONS(b) do { unsigned long long _b = (b); memcpy(obj + 0x20, &_b, 8); } while (0)
        st.zoom = 0; st.tilt = 0;
        RSTICK(zero, zero, zero, zero); LSTICK(zero, zero, zero, zero);
        BUTTONS(BTN_ZL);
        for (int f = 0; f < 400; f++) { set_camera(0.3, 0.9, 4000, 100000, 100000); worldfar_camera(&st, g2h, callx, got); }
        s32 z = gi(0xDE6A20 + 0x12 + 8);
        printf("ZL: camera distance %d\n", z);
        if (z != 35000) errors++;
        BUTTONS(BTN_ZR);
        for (int f = 0; f < 400; f++) { set_camera(0.3, 0.9, 4000, 100000, 100000); worldfar_camera(&st, g2h, callx, got); }
        z = gi(0xDE6A20 + 0x12 + 8);
        printf("ZR: camera distance %d (BUILD 715: 35%% closer than the game's 4000)\n", z);
        if (z != 2600) errors++;
        /* BUILD 599: a zoom-out survives a few frames in which the game's
           own distance leaves no room (the frames after leaving fship_*) */
        BUTTONS(BTN_ZL);
        for (int f = 0; f < 400; f++) { set_camera(0.3, 0.9, 4000, 100000, 100000); worldfar_camera(&st, g2h, callx, got); }
        BUTTONS(0);
        for (int f = 0; f < 5; f++) { set_camera(0.3, 0.9, 34000, 100000, 100000); worldfar_camera(&st, g2h, callx, got); }
        z = gi(0xDE6A20 + 0x12 + 8);
        if (z != 35000) { fprintf(stderr, "big game distance: %d\n", z); errors++; }
        set_camera(0.3, 0.9, 4000, 100000, 100000); worldfar_camera(&st, g2h, callx, got);
        z = gi(0xDE6A20 + 0x12 + 8);
        printf("zoom kept through a reload: camera distance %d (want 35000)\n", z);
        if (z != 35000) errors++;
        /* BUILD 603c: the game SETS the ship to one altitude going into
           fship and coming back (hardware), with control on */
        {
            u32 pe = 0x00260000;
            #define HWF(n) for (int _f = 0; _f < (n); _f++) { set_camera(0.3, 0.9, 4000, 100000, 100000); worldfar_camera(&st, g2h, callx, got); }
            const int V = 9950;                            /* the game's altitude */
            BUTTONS(0);
            si(G_PLAYER_ENT, pe); si(G_HW_TAKEOFF, 4000);
            si(pe + 0xC, 50000); si(pe + 0x10, 20000); si(pe + 0x14, 70000);
            si(G_VIEW_TYPE, 3); si(G_CONTROL, 1);
            HWF(3);                                        /* the player flies at 20000 */
            si(pe + 0x10, V); HWF(2);                      /* going in: set, control still on */
            worldfar_alloc(&st, g2h, callx);               /* fship, and back */
            si(pe + 0x10, V); HWF(1);
            printf("Highwind altitude after fship (from 20000): %d\n", gi(pe + 0x10));
            if (gi(pe + 0x10) != 20000) errors++;
            si(pe + 0x10, V); HWF(1);                      /* the game sets it once more */
            if (gi(pe + 0x10) != 20000) { fprintf(stderr, "a second set was not undone\n"); errors++; }
            /* the player descends in control: never pulled back */
            for (int yv = 20000; yv >= 3000; yv -= 100) { si(pe + 0x10, yv); HWF(1); }
            if (gi(pe + 0x10) != 3000) { fprintf(stderr, "a controlled descent was pulled back\n"); errors++; }
            /* from LOW: the game puts it up at V; it comes back down */
            si(pe + 0x10, V); HWF(1);                      /* going in, no allocator hook */
            si(G_CONTROL, 0); HWF(3); si(G_CONTROL, 1);
            HWF(1);
            printf("Highwind altitude after fship (from 3000): %d\n", gi(pe + 0x10));
            if (gi(pe + 0x10) != 3000) errors++;
            /* control taken and given back at the same height: nothing to do */
            si(G_CONTROL, 0); HWF(2); si(G_CONTROL, 1); HWF(1);
            if (gi(pe + 0x10) != 3000) errors++;
            /* landed (30+ frames out of the Highwind view), reload: not restored */
            si(G_VIEW_TYPE, 0); HWF(31);
            worldfar_alloc(&st, g2h, callx);
            si(pe + 0x10, V); si(G_VIEW_TYPE, 3); HWF(1);
            if (gi(pe + 0x10) != V) { fprintf(stderr, "restored after landing\n"); errors++; }
            /* flying, set, back somewhere else: not restored */
            si(pe + 0x10, 22000); HWF(1); si(pe + 0x10, 22100); HWF(1);
            si(pe + 0x10, V); si(pe + 0xC, 90000); HWF(1);
            if (gi(pe + 0x10) != V) { fprintf(stderr, "restored at another spot\n"); errors++; }
            si(G_VIEW_TYPE, 0); si(G_PLAYER_ENT, 0); si(G_CONTROL, 1);
            HWF(1);
            #undef HWF
        }
        /* ...and ZR then works from what is on screen, at once */
        BUTTONS(BTN_ZR);
        for (int f = 0; f < 5; f++) { set_camera(0.3, 0.9, 30000, 100000, 100000); worldfar_camera(&st, g2h, callx, got); }
        z = gi(0xDE6A20 + 0x12 + 8);
        if (z != 30000 + 5000 - 5 * 250)       /* ZOOM_STEP x move mult 2 */ { fprintf(stderr, "ZR after clamp: %d\n", z); errors++; }
        BUTTONS(BTN_ZR);
        for (int f = 0; f < 400; f++) { set_camera(0.3, 0.9, 4000, 100000, 100000); worldfar_camera(&st, g2h, callx, got); }
        BUTTONS(0);
        set_camera(0.3, 1.52, 7000, 100000, 100000);
        u8 before[18]; memcpy(before, mem + G_CAM_ROT, 18);
        worldfar_camera(&st, g2h, callx, got);
        if (memcmp(before, mem + G_CAM_ROT, 18)) { fprintf(stderr, "camera changed with no input\n"); errors++; }
        set_camera(0.3, 0.9, 7000, 100000, 100000);
        double el0 = asin(fabs(gs(G_CAM_ROT + 2 * 7) / 4096.0)) * 57.29578;
        RSTICK(one, zero, zero, zero);
        for (int f = 0; f < 400; f++) { set_camera(0.3, 0.9, 7000, 100000, 100000); worldfar_camera(&st, g2h, callx, got); }
        double el1 = asin(fabs(gs(G_CAM_ROT + 2 * 7) / 4096.0)) * 57.29578;
        RSTICK(zero, one, zero, zero);
        for (int f = 0; f < 400; f++) { set_camera(0.3, 0.9, 7000, 100000, 100000); worldfar_camera(&st, g2h, callx, got); }
        double el2 = asin(fabs(gs(G_CAM_ROT + 2 * 7) / 4096.0)) * 57.29578;
        printf("tilt: game %.1f deg, stick up %.1f, stick down %.1f\n", el0, el1, el2);
        if (fabs(el1 - 5.0) > 0.3 || fabs(el2 - 85.0) > 0.3) errors++;
        /* BUILD 591: holding the reset buttons resets nothing by itself; a
           click (worldfar_input) resets zoom and tilt once. BUILD 803: the
           click is ZL+ZR (it was R3); ZL and ZR held together cancel out. */
        BUTTONS(BTN_ZL | BTN_ZR); RSTICK(zero, zero, zero, zero);
        set_camera(0.3, 0.9, 7000, 100000, 100000);
        float t0 = st.tilt, z0 = st.zoom;
        worldfar_camera(&st, g2h, callx, got);
        if (st.tilt != t0) { fprintf(stderr, "held ZL+ZR reset the camera\n"); errors++; }
        if (st.zoom != z0) { fprintf(stderr, "held ZL+ZR zoomed (%g -> %g)\n", z0, st.zoom); errors++; }
        BUTTONS(0);
        st.reset_click = 1; st.zoom = 5000;
        worldfar_camera(&st, g2h, callx, got);
        if (st.tilt != 0 || st.zoom != 0 || st.reset_click) { fprintf(stderr, "reset click did not reset\n"); errors++; }

        /* BUILD 803: what counts as the click. ZL+ZR together and nothing
           else for the whole hold; it fires once, on the release, and puts
           L2 (the stock view toggle) in the keys for that update. The stick
           directions (16..23) do not spoil it; the d-pad, A, L3, R3 do, and
           R3 alone does nothing. */
        {
            const unsigned long long ZZ = BTN_ZL | BTN_ZR, A_ = 1ull << 0,
                L3_ = 1ull << 4, R3_ = (1ull << 5) | (1ull << 62),
                UP_ = 1ull << 13, LSTK = (1ull << 16) | (1ull << 19),
                RSTK = 1ull << 21, SL_ = 1ull << 24;
            struct { unsigned long long seq[5]; int n, clicks; const char *what; } Z[] = {
                { { BTN_ZL, ZZ, ZZ, 0 }, 4, 1, "ZL then ZR, let go" },
                { { ZZ, BTN_ZR, 0 }, 3, 1, "ZL let go first: one click" },
                { { ZZ, ZZ | A_, ZZ, 0 }, 4, 0, "A during the hold" },
                { { ZZ | UP_, 0 }, 2, 0, "with the d-pad" },
                { { ZZ, A_ }, 2, 0, "A on the release" },
                { { A_, ZZ, 0 }, 3, 1, "A before, let go first" },
                { { ZZ | L3_, ZZ, 0 }, 3, 0, "with L3" },
                { { ZZ | R3_, 0 }, 2, 0, "with R3" },
                { { ZZ | SL_, 0 }, 2, 0, "with SL" },
                { { ZZ | LSTK, ZZ | RSTK, 0 }, 3, 1, "stick directions do not count" },
                { { R3_, 0, R3_, 0 }, 4, 0, "R3 alone does nothing now" },
                { { BTN_ZL, 0, BTN_ZR, 0 }, 4, 0, "ZL and ZR one after the other" },
                { { ZZ, ZZ, ZZ, ZZ, ZZ }, 5, 0, "held: nothing until let go" },
            };
            int zok = 1;
            si(G_MAP_TYPE, 0);
            for (unsigned t = 0; t < sizeof Z / sizeof Z[0]; t++) {
                st.reset_prev = 0; st.reset_click = 0;
                int clicks = 0;
                for (int i = 0; i < Z[t].n; i++) {
                    BUTTONS(Z[t].seq[i]);
                    si(G_KEYS, 0);
                    worldfar_input(&st, g2h, callx, got, 0);
                    u32 k = (u32)gi(G_KEYS);
                    if (st.reset_click) {
                        clicks++;
                        if (!(k & K_L2) || st.keys_added != K_L2) {
                            fprintf(stderr, "%s: no L2 for the view toggle\n", Z[t].what); zok = 0; }
                    } else if (k & K_L2) {
                        fprintf(stderr, "%s: L2 without a click\n", Z[t].what); zok = 0; }
                    st.reset_click = 0;
                    si(G_KEYS, (s32)(k & ~st.keys_added)); st.keys_added = 0;
                }
                if (clicks != Z[t].clicks) {
                    fprintf(stderr, "%s: %d clicks, want %d\n", Z[t].what, clicks, Z[t].clicks); zok = 0; }
            }
            /* leaving the world map mid-hold: no click on the way back */
            st.reset_prev = 0; st.reset_click = 0;
            BUTTONS(ZZ); worldfar_input(&st, g2h, callx, got, 0);
            si(G_MAP_TYPE, 1); worldfar_input(&st, g2h, callx, got, 0);
            si(G_MAP_TYPE, 0); BUTTONS(0); si(G_KEYS, 0);
            worldfar_input(&st, g2h, callx, got, 0);
            if (st.reset_click) { fprintf(stderr, "reset click across a map change\n"); zok = 0; }
            st.reset_click = 0; st.reset_prev = 0; st.keys_added = 0; si(G_KEYS, 0);
            BUTTONS(0);
            printf("ZL+ZR reset click: %u cases: %s\n", (unsigned)(sizeof Z / sizeof Z[0]), zok ? "yes" : "NO");
            if (!zok) errors++;
        }

        /* 360-degree movement: the stock UP+RIGHT move (45, -45) with the
           stick up-right becomes 60 along the camera's up-right */
        si(G_CONTROL, 1); si(G_VIEW_TYPE, 0); si(G_MAP_TYPE, 0);
        set_camera(0.7, 0.9, 7000, 100000, 100000);
        u32 ent = 0x00240000; si(G_ENTITY, ent);
        si(G_CAM_FRONT, 1000); si(G_CAM_ROTY, 1000);
        float c = 0.70710678f;
        LSTICK(c, zero, c, zero);
        worldfar_input(&st, g2h, callx, got, 0);
        u8 ctx[0x40] = { 0 };
        u32 esp = 0x00380000;
        si(esp + 4, 45); si(esp + 8, -45);
        st32(ctx + 0x10, esp);
        worldfar_move(&st, g2h, callx, ctx, 0);
        double wx = (Rf[0][0] + Rf[2][0] / hypot(Rf[2][0], Rf[2][2])), wz = (Rf[0][2] + Rf[2][2] / hypot(Rf[2][0], Rf[2][2]));
        double wl = hypot(wx, wz);
        s32 mx_ = gi(esp + 4), mz_ = gi(esp + 8);
        printf("move: (%d, %d), want (%.0f, %.0f)\n", mx_, mz_, 60 * wx / wl, 60 * wz / wl);
        if (fabs(mx_ - 60 * wx / wl) > 1.5 || fabs(mz_ - 60 * wz / wl) > 1.5) errors++;
        si(G_KEYS, 0x1000 | 0x2000);             /* the stick made UP+RIGHT */
        si(G_CAM_FRONT, 1016);                   /* and the stock turned 16 */
        worldfar_input(&st, g2h, callx, got, 1);
        s32 fr = gi(G_CAM_FRONT);
        s16 face = gs(ent + 0x40);
        double wantf = atan2(wx, wz) * 2048.0 / M_PI;
        s32 wantfr = 1016;
        printf("follow: heading 1000 -> %d (want %d), facing %d (want %.0f)\n", fr, wantfr, face, wantf);
        if (fr != wantfr || fabs(face - wantf) > 3) errors++;
        /* (stick, keys, what the stock did, want) */
        struct { float u, d, r, l; u32 keys; s32 stock, want; int model; const char *what; } T[] = {
            { 0, 0, 1, 0, 0x2000, 1032, 1032, 0, "stick right = holding right" },
            { 0, 0, 0, 1, 0x8000,  968,  968, 0, "stick left = holding left" },
            { 0, 0, 0, .6f, 0x8000, 968, 981, 0, "stick part left turns less" },
            { 1, 0, 0, 0, 0x1000, 1000, 1000, 0, "stick up: no turn" },
            { 0, .70710678f, .70710678f, 0, 0x6000, 984, 1005, 0, "down-right eases, no whip" },
            { 0, 1, 0, 0, 0x4000, 1000, 1000, 0, "stick down: no turn" },
            { 0, 0, 1, 0, 0x2000, 1005, 1005, 0, "an unexplained change is kept" },
            { 0, 0, 1, 0, 0x2000, 1032, 1032, 6, "buggy: stock" },
            { 0, 0, 1, 0, 0x2000, 1032, 1032, 19, "chocobo: follows" },
        };
        for (unsigned t = 0; t < sizeof T / sizeof T[0]; t++) {
            test_model = T[t].model;
            LSTICK(T[t].u, T[t].d, T[t].r, T[t].l);
            si(G_CAM_FRONT, 1000); si(G_CAM_ROTY, 1000);
            worldfar_input(&st, g2h, callx, got, 0);
            si(G_KEYS, (s32)T[t].keys);
            si(G_CAM_FRONT, T[t].stock);
            worldfar_input(&st, g2h, callx, got, 1);
            if (gi(G_CAM_FRONT) != T[t].want) {
                fprintf(stderr, "%s: %d, want %d\n", T[t].what, gi(G_CAM_FRONT), T[t].want); errors++;
            }
        }
        test_model = 0;
        printf("turns: %u stick/key cases checked\n", (unsigned)(sizeof T / sizeof T[0]));
        /* BUILD 694: on foot, L1/R1 never reach the update (no stock spin, the
           player keeps his facing) and turn the camera at the stock rate;
           a new L1+R1 press puts it behind him, held it does nothing more */
        {
            LSTICK(zero, zero, zero, zero); RSTICK(zero, zero, zero, zero); BUTTONS(0);
            u16 f500 = 500; memcpy(mem + ent + 0x40, &f500, 2);
            /* BUILD 697: L1/R1 orbit the camera (st.orbit); the heading the
               game moves by is never touched */
            struct { u32 keys; s32 want; int model; const char *what; } L[] = {
                { 0x0004, 4096 - 24, 0, "L1 orbits the camera left" },
                { 0x0008, 0, 2, "R1 orbits it right (Cid)" },
                { 0x0004, 4096 - 24, 19, "L1 on a chocobo" },
                { 0x000C, 0, 0, "L1+R1 press: orbit back to 0" },
                { 0x000C, 0, 0, "L1+R1 held: nothing more" },
                { 0x0008, 0, 0, "L1 let go first: R1 alone does not nudge" },
                { 0x0008, 0, 0, "... still R1 alone" },
                { 0x0000, 0, 0, "both let go" },
                { 0x0008, 24, 0, "R1 after L1+R1" },
                { 0x0000, 24, 0, "let go: the orbit stays" },
                { 0x0008, 48, 6, "R1 in the buggy" },
                { 0x1008, 72, 0, "R1 walking (up held)" },
            };
            int ok = 1;
            for (int view = 0; view <= 2; view += 2) {   /* BUILD 696: both views */
            si(G_VIEW_TYPE, view);
            memcpy(mem + ent + 0x40, &f500, 2);
            st.lr_prev = 0; st.orbit = 0;
            for (unsigned t = 0; t < sizeof L / sizeof L[0]; t++) {
                test_model = L[t].model;
                si(G_KEYS, (s32)L[t].keys);
                BUTTONS(((L[t].keys & 4) ? BTN_L : 0) | ((L[t].keys & 8) ? BTN_R : 0));
                si(G_CAM_FRONT, 1000); si(G_CAM_ROTY, 1000);
                worldfar_input(&st, g2h, callx, got, 0);
                u32 during = (u32)gi(G_KEYS);
                worldfar_input(&st, g2h, callx, got, 1);   /* the update turned nothing */
                u32 after = (u32)gi(G_KEYS);
                if (during & 0x000C) { fprintf(stderr, "%s: the update saw L1/R1\n", L[t].what); ok = 0; }
                if (after != L[t].keys) { fprintf(stderr, "%s: keys %x after\n", L[t].what, after); ok = 0; }
                if (gi(G_CAM_FRONT) != 1000) {
                    fprintf(stderr, "%s: heading %d, want 1000\n", L[t].what, gi(G_CAM_FRONT)); ok = 0; }
                if (st.orbit != L[t].want) {
                    fprintf(stderr, "%s: orbit %d, want %d\n", L[t].what, st.orbit, L[t].want); ok = 0; }
                if (gs(ent + 0x40) != 500) {
                    fprintf(stderr, "%s (view %d): the player turned (%d)\n", L[t].what, view, gs(ent + 0x40)); ok = 0; }
            }
            }
            si(G_VIEW_TYPE, 0);
            /* BUILD 702: the right stick's L1/R1 (the port makes them on the
               world map) with no L/R button held: stock, the orbit is not
               touched and the update sees them */
            for (int view = 0; view <= 2; view += 2) {
                si(G_VIEW_TYPE, view); BUTTONS(0);
                u16 o0 = st.orbit;
                for (u32 kk = 4; kk <= 8; kk += 4) {
                    si(G_KEYS, (s32)kk); si(G_CAM_FRONT, 1000); si(G_CAM_ROTY, 1000);
                    worldfar_input(&st, g2h, callx, got, 0);
                    if (!(gi(G_KEYS) & kk)) { fprintf(stderr, "right stick (view %d): the update lost key %x\n", view, kk); ok = 0; }
                    worldfar_input(&st, g2h, callx, got, 1);
                    if (st.orbit != o0) { fprintf(stderr, "right stick (view %d): orbit moved to %d\n", view, st.orbit); ok = 0; }
                }
                /* L held and the stick pushed right: L orbits, R1 stays stock */
                BUTTONS(BTN_L); si(G_KEYS, 0x000C); st.lr_prev = 0;
                worldfar_input(&st, g2h, callx, got, 0);
                if ((gi(G_KEYS) & 0xC) != 0x8) { fprintf(stderr, "L + stick: update keys %x\n", gi(G_KEYS)); ok = 0; }
                worldfar_input(&st, g2h, callx, got, 1);
                if (st.orbit != (u16)((o0 - 24) & 4095)) { fprintf(stderr, "L + stick: orbit %d\n", st.orbit); ok = 0; }
                st.orbit = o0; BUTTONS(0);
            }
            printf("right stick: L1/R1 it makes stay stock: %s\n", ok ? "yes" : "NO");
            si(G_VIEW_TYPE, 0);

            /* the Highwind: L1 is the camera's too */
            si(G_VIEW_TYPE, 3);
            BUTTONS(BTN_L);
            si(G_KEYS, 0x0004); si(G_CAM_FRONT, 1000);
            worldfar_input(&st, g2h, callx, got, 0);
            if (gi(G_KEYS) & 0x0004) { fprintf(stderr, "Highwind: the update saw L1\n"); ok = 0; }
            worldfar_input(&st, g2h, callx, got, 1);
            if (st.orbit != 48 || gi(G_CAM_FRONT) != 1000) { fprintf(stderr, "Highwind: orbit %d heading %d\n", st.orbit, gi(G_CAM_FRONT)); ok = 0; }
            BUTTONS(0);
            /* BUILD 722: the Highwind turns by how far the stick points;
               the bank eases towards the stick's share of +-0x80 */
            {
                struct { float u, d, r, l; u32 keys; s32 stock, want, bank; const char *what; } H[] = {
                    { 0, 0, 1, 0, 0x2000, 1032, 1032, -4, "full right = holding right" },
                    { 0, 0, 0, 1, 0x8000,  968,  968,  4, "full left = holding left" },
                    { 0, 0, .6f, 0, 0x2000, 1032, 1016, -2, "part right turns half" },
                    { 0, 0, .3f, 0, 0x0000, 1000, 1004, 0, "a little right, no key: a little" },
                    { .70710678f, 0, .70710678f, 0, 0x3000, 1016, 1013, -3, "up-right: about the stock half" },
                    { 1, 0, 0, 0, 0x1000, 1000, 1000, 0, "up: no turn" },
                    { .9f, 0, .15f, 0, 0x1000, 1000, 1000, 0, "up, leaning a little: no turn, no bank" },
                    { 0, 0, 1, 0, 0x2080, 1032, 1032, 99, "Square (drift): stock" },
                    { 0, 0, 1, 0, 0x2000, 1005, 1005, 99, "an unexplained change is kept" },
                };
                si(G_VIEW_TYPE, 3);
                int hok = 1;
                for (unsigned t = 0; t < sizeof H / sizeof H[0]; t++) {
                    LSTICK(H[t].u, H[t].d, H[t].r, H[t].l);
                    si(G_CAM_FRONT, 1000); si(G_CAM_ROTY, 1000); si(G_HW_BANK, 0);
                    st.orbit = 0;
                    worldfar_input(&st, g2h, callx, got, 0);
                    si(G_KEYS, (s32)H[t].keys);
                    si(G_CAM_FRONT, H[t].stock);
                    si(G_HW_BANK, 77);                  /* what the stock eased to */
                    worldfar_input(&st, g2h, callx, got, 1);
                    s32 bank = gi(G_HW_BANK);
                    if (gi(G_CAM_FRONT) != H[t].want
                        || (H[t].bank != 99 && (bank != H[t].bank
                                                || gs(G_HW_ROT + 4) != -2 * bank))
                        || (H[t].bank == 99 && bank != 77)) {
                        fprintf(stderr, "Highwind %s: heading %d (want %d), bank %d (want %d)\n",
                                H[t].what, gi(G_CAM_FRONT), H[t].want, bank, H[t].bank);
                        hok = 0;
                    }
                }
                printf("Highwind analogue turn: %u cases %s\n", (unsigned)(sizeof H / sizeof H[0]), hok ? "ok" : "FAILED");
                /* BUILD 725: level, it is drawn straight ahead of the camera
                   (swing + 0x3C + 0x3E == 0x800 - (camera heading - orbit)),
                   whatever the easings left; banked past 32 the swing is the
                   game's 4 x bank, and in between the correction fades */
                {
                    u32 pe = 0x00250000;
                    Env env_for_test = { g2h, callx, &st };
                    si(G_PLAYER_ENT, (s32)pe);
                    struct { s32 roty, orbit; s16 f3c, f3e; s32 bank; } C[] = {
                        { 1000, 0, 2048 - 1000 + 45, 0, 0 }, { 1000, 0, 2048 - 1000, 37, 0 },
                        { 1015, 0, 2048 - 1000, 0, 0 }, { 4200, 200, 2048 - 4050 + 4096, -5, 0 },
                        { 30, -60, 2000, 3, 0 }, { 1000, 0, 2048 - 1060, 0, 40 },
                        { 1000, 0, 2048 - 1060, 0, -100 }, { 1000, 0, 2048 - 1064, 0, 16 },
                    };
                    int cok = 1;
                    for (unsigned c = 0; c < sizeof C / sizeof C[0]; c++) {
                        si(G_CAM_ROTY, C[c].roty); st.orbit_added = C[c].orbit;
                        memcpy(mem + pe + 0x3C, &C[c].f3c, 2); memcpy(mem + pe + 0x3E, &C[c].f3e, 2);
                        si(G_HW_BANK, C[c].bank);
                        s16 sw = 333; memcpy(mem + G_HW_ROT, &sw, 2);
                        hw_centre(&env_for_test, &st);
                        s32 off = wrapi(gs(G_HW_ROT) + C[c].f3c + C[c].f3e - (0x800 - (C[c].roty - C[c].orbit)), 4096);
                        s32 ab = C[c].bank < 0 ? -C[c].bank : C[c].bank;
                        s32 want_sw = ab >= 32 ? 4 * C[c].bank : -1000000;
                        int bad = ab == 0 ? off != 0
                                : ab >= 32 ? gs(G_HW_ROT) != want_sw
                                : !(abs(off - 4 * C[c].bank) < 64 && abs(off) > 0);
                        if (bad) { fprintf(stderr, "Highwind centre case %u: swing %d, off centre %d\n", c, gs(G_HW_ROT), off); cok = 0; }
                    }
                    si(G_VIEW_TYPE, 0);
                    s16 sw = 333; memcpy(mem + G_HW_ROT, &sw, 2);
                    hw_centre(&env_for_test, &st);
                    if (gs(G_HW_ROT) != 333) { fprintf(stderr, "Highwind centre: touched off the Highwind\n"); cok = 0; }
                    si(G_VIEW_TYPE, 3); st.orbit_added = 0;
                    /* BUILD 728: level, a bank within 8 of 0 steps one a frame
                       to 0 from its value before the update -- past the 60 fps
                       easing's stall and without the game's jump to 0 at 4 (726
                       zeroed it at once: a snap); a turn's or a bigger one is
                       left to the game */
                    {
                        struct { u32 keys; s32 old, game, want; } B[] = {
                            { 0, 6, 6, 5 }, { 0, -7, -7, -6 }, { 0, 8, 8, 7 }, { 0, 4, 0, 3 },
                            { 0, 1, 0, 0 }, { 0, 12, 11, 11 }, { 0, 0, 0, 0 },
                            { 0x2000, 6, 6, 6 }, { 0x8000, -5, -5, -5 }, { 0x1000, -6, -6, -5 },
                        };
                        LSTICK(zero, zero, zero, zero);
                        for (unsigned k = 0; k < sizeof B / sizeof B[0]; k++) {
                            si(G_CAM_FRONT, 1000); si(G_CAM_ROTY, 1000);
                            si(G_HW_BANK, B[k].old);
                            worldfar_input(&st, g2h, callx, got, 0);
                            si(G_KEYS, (s32)B[k].keys);
                            si(G_HW_BANK, B[k].game);
                            s16 rl = (s16)(-2 * B[k].game); memcpy(mem + G_HW_ROT + 4, &rl, 2);
                            worldfar_input(&st, g2h, callx, got, 1);
                            if (gi(G_HW_BANK) != B[k].want || gs(G_HW_ROT + 4) != -2 * B[k].want) {
                                fprintf(stderr, "Highwind bank case %u: bank %d roll %d, want %d\n", k, gi(G_HW_BANK), gs(G_HW_ROT + 4), B[k].want); cok = 0; }
                        }
                        /* a whole settle after a turn: the stock 60 fps easing
                           ((15 b + 8) >> 4, 0 at |b| <= 4) and this, frame by frame */
                        for (int sgn = -1; sgn <= 1; sgn += 2) {
                            s32 bk = 112 * sgn; int fr = 0, mono = 1, tail = 0;
                            si(G_KEYS, 0);
                            while (bk && fr < 200) {
                                si(G_CAM_FRONT, 1000); si(G_CAM_ROTY, 1000);
                                si(G_HW_BANK, bk);
                                worldfar_input(&st, g2h, callx, got, 0);
                                s32 g = (15 * bk + 8) >> 4;
                                if (g >= -4 && g <= 4) g = 0;
                                si(G_HW_BANK, g);
                                worldfar_input(&st, g2h, callx, got, 1);
                                s32 nb = gi(G_HW_BANK);
                                if ((sgn > 0 && (nb > bk || nb < 0)) || (sgn < 0 && (nb < bk || nb > 0))) mono = 0;
                                if (abs(bk) <= 12 && abs(bk - nb) > tail) tail = abs(bk - nb);
                                bk = nb; fr++;
                            }
                            printf("bank settle from %d: 0 after %d frames, biggest step near 0 %d\n", 112 * sgn, fr, tail);
                            if (bk || !mono || tail > 1) cok = 0;
                        }
                        si(G_KEYS, 0); si(G_HW_BANK, 0);
                    }
                    printf("Highwind rests centred: %s\n", cok ? "yes" : "NO");
                    if (!cok) hok = 0;
                }
                if (!hok) ok = 0;
                LSTICK(zero, zero, zero, zero);
                si(G_KEYS, 0);
            }
            /* BUILD 727: landing (0x74F916 simulated). From above LAND_HIGH
               the ship comes down over ~2 s on a smooth curve instead of the
               stock compounding drop; from the stock 4000 nothing changes. */
            {
                u32 pe = 0x00270000;
                si(G_PLAYER_ENT, (s32)pe); si(G_CONTROL, 0); si(G_KEYS, 0);
                int lok = 1;
                struct { s32 y0, g, m; } L[] = { { 30767, 500, 1 }, { 30767, 500, 2 },
                    { 12000, 1500, 1 }, { 4000, 300, 1 }, { 4000, 300, 2 }, { 4400, 0, 1 } };
                for (unsigned c = 0; c < sizeof L / sizeof L[0]; c++) {
                    for (int run = 0; run < 2; run++) {     /* 0: stock, 1: with hw_land */
                        s32 y = L[c].y0, g = L[c].g, m = L[c].m, p = 256, vel = 0;
                        si(G_MOVE_MULT, m); si(G_HW_LANDING, -1); si(G_HW_PROG, 256);
                        si(G_HW_PVEL, 0); si(pe + 0x10, y); st.hw_pad2[2] = 0;
                        int fr = 0, down = -1, maxstep = 0, mono = 1;
                        static s32 ys[2][400];
                        while (fr < 400) {
                            si(pe + 0x20, gi(pe + 0x10));            /* 0x7631DF */
                            if (run) {
                                worldfar_input(&st, g2h, callx, got, 0);
                                worldfar_input(&st, g2h, callx, got, 1);
                            }
                            vel = gi(G_HW_PVEL); p = gi(G_HW_PROG);
                            vel += p < 128 ? m : -m;
                            si(G_HW_PVEL, vel);
                            fr++;
                            if (vel == 0) { si(G_HW_LANDING, 0); break; }
                            p += vel; if (p < 0) p = 0; if (p > 256) p = 256;
                            si(G_HW_PROG, p);
                            s32 ny = g + (s32)(((long long)(gi(pe + 0x20) - g) * p) >> 8);
                            if (ny > y) mono = 0;
                            if (y - ny > maxstep) maxstep = y - ny;
                            y = ny; si(pe + 0x10, y);
                            ys[run][fr] = y;
                            if (down < 0 && y == g) down = fr;
                        }
                        if (run) {
                            worldfar_input(&st, g2h, callx, got, 0);
                            worldfar_input(&st, g2h, callx, got, 1);
                        }
                        int high = L[c].y0 > LAND_HIGH;
                        int same = 1;
                        if (run && !high)
                            for (int i = 1; i < fr; i++) if (ys[0][i] != ys[1][i]) same = 0;
                        int good = fr < 400 && y == g && (!run || !high || p == 0) && mono && st.hw_pad2[2] == 0
                                   && (!run || high || same)
                                   && (!run || !high || (maxstep <= 600 * m && down * m > 48));
                        if (!good) fprintf(stderr, "  y %d g %d p %d mono %d pad %08x same %d\n", y, g, p, mono, st.hw_pad2[2], same);
                        printf("landing from %d (mult %d) %s: %d frames, down at %d, biggest drop %d a frame%s\n",
                               L[c].y0, m, run ? "now" : "stock", fr, down, maxstep, good ? "" : "  <-- BAD");
                        if (!good) lok = 0;
                    }
                }
                printf("Highwind lands smoothly: %s\n", lok ? "yes" : "NO");
                if (!lok) ok = 0;
                si(G_MOVE_MULT, 2); si(G_HW_LANDING, 0); si(G_PLAYER_ENT, 0); si(G_CONTROL, 1);
            }
            si(G_VIEW_TYPE, 0);
            /* no control (a script): stock */
            si(G_CONTROL, 0); si(G_KEYS, 0x0004);
            worldfar_input(&st, g2h, callx, got, 0);
            if (!(gi(G_KEYS) & 0x0004)) { fprintf(stderr, "script: L1 taken\n"); ok = 0; }
            worldfar_input(&st, g2h, callx, got, 1);
            si(G_CONTROL, 1);
            /* BUILD 698: the orbit goes into the smoothed heading for the
               draw (the camera builds its matrix from it) and comes back
               out before the next update */
            {
                st.orbit = 512; st.reset_click = 0; st.lr_prev = 0; st.orbit_added = 0;
                si(G_KEYS, 0); si(G_CAM_FRONT, 1000); si(G_CAM_ROTY, 1000);
                worldfar_input(&st, g2h, callx, got, 0);
                worldfar_input(&st, g2h, callx, got, 1);
                int roty_draw = gi(G_CAM_ROTY);
                worldfar_input(&st, g2h, callx, got, 0);
                int roty_upd = gi(G_CAM_ROTY);
                worldfar_input(&st, g2h, callx, got, 1);
                printf("orbit: heading drawn %d, the update sees %d\n", roty_draw, roty_upd);
                if (roty_draw != 1000 + 512 || roty_upd != 1000 || gi(G_CAM_ROTY) != 1512 || gi(G_CAM_FRONT) != 1000) ok = 0;
                st.orbit = 3584;                         /* -512 */
                worldfar_input(&st, g2h, callx, got, 0);
                worldfar_input(&st, g2h, callx, got, 1);
                if (gi(G_CAM_ROTY) != 1000 - 512) { fprintf(stderr, "orbit -512: %d\n", gi(G_CAM_ROTY)); ok = 0; }
                worldfar_input(&st, g2h, callx, got, 0);
                if (gi(G_CAM_ROTY) != 1000) { fprintf(stderr, "orbit -512 not taken out: %d\n", gi(G_CAM_ROTY)); ok = 0; }
                worldfar_input(&st, g2h, callx, got, 1);
                st.orbit = 512;
                /* moving: the same (dx, dz) with the orbit as without */
                LSTICK(.70710678f, zero, .70710678f, zero);
                s32 res[2][2];
                for (int k = 0; k < 2; k++) {
                    st.orbit = k ? 512 : 0;
                    set_camera(k ? 0.7 - M_PI / 4 : 0.7, 0.9, 4000, 100000, 100000);
                    u8 cx2[0x40] = { 0 }; u32 esp2 = 0x00380000;
                    si(esp2 + 4, 45); si(esp2 + 8, -45); st32(cx2 + 0x10, esp2);
                    worldfar_input(&st, g2h, callx, got, 0);
                    worldfar_move(&st, g2h, callx, cx2, 0);
                    res[k][0] = gi(esp2 + 4); res[k][1] = gi(esp2 + 8);
                    worldfar_input(&st, g2h, callx, got, 1);
                }
                printf("orbit: move (%d, %d) without, (%d, %d) with\n", res[0][0], res[0][1], res[1][0], res[1][1]);
                if (abs(res[0][0] - res[1][0]) > 1 || abs(res[0][1] - res[1][1]) > 1) ok = 0;
                LSTICK(zero, zero, zero, zero);
                /* the ZL+ZR click takes it back to 0, in the same frame's heading too */
                st.orbit = 512; st.reset_click = 1; st.orbit_added = 0;
                si(G_CAM_FRONT, 1000); si(G_CAM_ROTY, 1000);
                worldfar_input(&st, g2h, callx, got, 0);
                worldfar_input(&st, g2h, callx, got, 1);
                if (st.orbit_added) { fprintf(stderr, "reset click: orbit still drawn (%d)\n", st.orbit_added); ok = 0; }
                worldfar_camera(&st, g2h, callx, got);
                if (st.orbit) { fprintf(stderr, "reset click kept the orbit\n"); ok = 0; }
                st.orbit = 0; st.reset_click = 0;
                worldfar_input(&st, g2h, callx, got, 0);
                worldfar_input(&st, g2h, callx, got, 1);
            }
            test_model = 0; si(G_KEYS, 0);
            printf("L1/R1: camera orbit only, L1+R1 resets: %s\n", ok ? "yes" : "NO");
            if (!ok) errors++;
        }
        /* the buggy moves as stock */
        test_model = 6; LSTICK(zero, zero, one, zero);
        si(esp + 4, 0); si(esp + 8, 45);
        worldfar_move(&st, g2h, callx, ctx, 0);
        if (gi(esp + 4) != 0 || gi(esp + 8) != 45) { fprintf(stderr, "buggy move changed\n"); errors++; }
        test_model = 0;
        si(G_KEYS, 0);
        LSTICK(one, zero, zero, zero);
        si(G_CAM_FRONT, 1000);
        worldfar_input(&st, g2h, callx, got, 0);
        worldfar_input(&st, g2h, callx, got, 1);
        if (gi(G_CAM_FRONT) != 1000) { fprintf(stderr, "followed while running straight\n"); errors++; }
        /* the d-pad alone: the stock turn stands */
        LSTICK(zero, zero, zero, zero);
        si(G_CAM_FRONT, 1000);
        worldfar_input(&st, g2h, callx, got, 0);
        si(G_KEYS, 0x2000); si(G_CAM_FRONT, 1032);
        worldfar_input(&st, g2h, callx, got, 1);
        if (gi(G_CAM_FRONT) != 1032) { fprintf(stderr, "d-pad turn undone\n"); errors++; }
        si(G_KEYS, 0);
        LSTICK(zero, zero, zero, zero);
        si(esp + 4, 60); si(esp + 8, 0);
        worldfar_move(&st, g2h, callx, ctx, 0);
        if (gi(esp + 4) != 60 || gi(esp + 8) != 0) { fprintf(stderr, "d-pad move changed\n"); errors++; }
        si(G_VIEW_TYPE, 3);
        si(G_CAM_FRONT, 1000);
        worldfar_input(&st, g2h, callx, got, 0);
        si(G_CAM_FRONT, 1032);
        worldfar_input(&st, g2h, callx, got, 1);
        if (gi(G_CAM_FRONT) != 1032) { fprintf(stderr, "Highwind turn undone\n"); errors++; }
        /* BUILD 700: after a dive the drawn pitch settles at 0, not -3;
           a dive still pitches it down */
        {
            int p = -10;
            for (int f = 0; f < 40; f++) {                 /* level: target 0 */
                p = (3 * p + 0) >> 2; si(0xE045E0, p);
                worldfar_input(&st, g2h, callx, got, 0);
                worldfar_input(&st, g2h, callx, got, 1);
                p = gi(0xE045E0);
            }
            int q = 0, lo = 0;
            for (int f = 0; f < 40; f++) {                 /* diving: target -10 */
                q = (3 * q - 10) >> 2; si(0xE045E0, q);
                worldfar_input(&st, g2h, callx, got, 0);
                worldfar_input(&st, g2h, callx, got, 1);
                q = gi(0xE045E0); if (q < lo) lo = q;
            }
            printf("Highwind pitch: settles at %d after a dive, dives to %d\n", p, lo);
            if (p != 0 || lo > -5) errors++;
        }

        si(esp + 4, 60); si(esp + 8, 0); LSTICK(one, zero, zero, zero);
        worldfar_move(&st, g2h, callx, ctx, 0);
        if (gi(esp + 4) != 60) { fprintf(stderr, "Highwind move changed\n"); errors++; }
        si(G_VIEW_TYPE, 0); LSTICK(zero, zero, zero, zero);
        RSTICK(zero, zero, one, zero);
        si(G_CAM_FRONT, 1000);
        worldfar_input(&st, g2h, callx, got, 0);
        worldfar_input(&st, g2h, callx, got, 1);
        printf("yaw: right stick turns %d units per step\n", gi(G_CAM_FRONT) - 1000);
        if (gi(G_CAM_FRONT) - 1000 != 22) errors++;
        /* The reset click (BUILD 803: ZL+ZR, was R3) is the stock L2 (view
           toggle) for exactly one update, and resets the camera (behind him
           when the view is the normal one). BUILD 736: the click is the
           RELEASE, and another button (here L3) down at any point of the
           hold cancels it. The second pass holds the stick directions too,
           which do not count. */
        RSTICK(zero, zero, zero, zero);
        static const struct { unsigned long long f[6]; int click_at; const char *what; } R3T[] = {
            { { 1, 1, 1, 1, 0, 0 }, 4, "ZL+ZR held 4 updates, then up" },
            { { 1, 0, 0, 0, 0, 0 }, 1, "ZL+ZR tapped" },
            { { 1, 3, 3, 1, 0, 0 }, -1, "ZL+ZR, L3 joins and leaves, ZL+ZR up" },
            { { 3, 3, 2, 0, 0, 0 }, -1, "ZL+ZR+L3 together, ZL+ZR up first" },
            { { 2, 3, 1, 0, 0, 0 }, -1, "L3 first, ZL+ZR join, L3 up first" },
            { { 3, 0, 1, 0, 0, 0 }, 3, "after the combo, a plain ZL+ZR click works" },
            { { 2, 2, 0, 0, 0, 0 }, -1, "L3 alone" },
        };
        for (int b = 0; b < 2; b++) {
            unsigned long long r3b = BTN_ZL | BTN_ZR | (b ? 0xFF0000ull : 0);
            BUTTONS(0);
            worldfar_input(&st, g2h, callx, got, 0);
            worldfar_input(&st, g2h, callx, got, 1);
            st.reset_click = 0;
            u16 f968 = 968; memcpy(mem + ent + 0x40, &f968, 2);
            int ok = 1;
            for (unsigned t = 0; t < sizeof R3T / sizeof R3T[0]; t++) {
                int tok = 1;
                for (int f = 0; f < 6; f++) {
                    unsigned long long k = R3T[t].f[f];
                    BUTTONS(((k & 1) ? r3b : 0) | ((k & 2) ? (1ull << 4) : 0));
                    si(G_KEYS, 0x0010);
                    si(G_CAM_FRONT, 300);
                    si(G_VIEW_TYPE, 0);
                    worldfar_input(&st, g2h, callx, got, 0);
                    u32 during = (u32)gi(G_KEYS);
                    worldfar_input(&st, g2h, callx, got, 1);
                    u32 after = (u32)gi(G_KEYS);
                    int want = f == R3T[t].click_at;
                    if (((during & 1) != 0) != want || after != 0x0010) tok = 0;
                    if (want && (gi(G_CAM_FRONT) != 1080 || !st.reset_click)) tok = 0;
                    if (!want && (gi(G_CAM_FRONT) != 300 || st.reset_click)) tok = 0;
                    st.reset_click = 0;
                }
                if (!tok) { fprintf(stderr, "reset click%s: %s\n", b ? " (sticks pushed)" : "", R3T[t].what); ok = 0; }
            }
            printf("ZL+ZR%s: one L2 press per click on release, heading 1080 on the click, none with L3: %s\n", b ? " (sticks pushed)" : "", ok ? "yes" : "NO");
            if (!ok) errors++;
        }
        /* a click that enters the fixed view leaves its heading alone */
        BUTTONS(0);
        worldfar_input(&st, g2h, callx, got, 0);
        worldfar_input(&st, g2h, callx, got, 1);
        BUTTONS(BTN_ZL | BTN_ZR);
        worldfar_input(&st, g2h, callx, got, 0);
        worldfar_input(&st, g2h, callx, got, 1);
        BUTTONS(0);
        si(G_CAM_FRONT, 300);
        worldfar_input(&st, g2h, callx, got, 0);
        if (!st.reset_click) { fprintf(stderr, "ZL+ZR release did not click\n"); errors++; }
        si(G_VIEW_TYPE, 2);                         /* the game toggled */
        worldfar_input(&st, g2h, callx, got, 1);
        if (gi(G_CAM_FRONT) != 300) { fprintf(stderr, "the reset click turned the fixed view\n"); errors++; }
        si(G_VIEW_TYPE, 0); st.reset_click = 0;
        BUTTONS(0); si(G_KEYS, 0);
        si(G_CONTROL, 0);
        si(G_CAM_FRONT, 1000);
        worldfar_input(&st, g2h, callx, got, 0);
        si(G_CAM_FRONT, 1032);
        worldfar_input(&st, g2h, callx, got, 1);
        if (gi(G_CAM_FRONT) != 1032) { fprintf(stderr, "turned without control\n"); errors++; }
    }
    printf("height: %d ground queries on bent meshes, worst %.2f from the flat height, %d touched without a hit\n",
           height_checks, height_worst, height_bad);
    if (!height_checks || height_worst > 1.01 || height_bad) errors++;
    printf("horizon: %ld of %ld visible L2 tiles drawn, %d frames short\n", l2_drawn, l2_wanted, l2_short_frames);
    printf("own window: %u frames drew the game's 5x5 themselves, %ld window cells, %ld of them coarse without a teleport\n",
           st.own_frames, own_cells, own_cells_s);
    if (OWN_WINDOW && (st.own_frames < 100 || own_cells_s)) { fprintf(stderr, "own window\n"); errors++; }
    if (l2_drawn != l2_wanted) errors++;
    printf("wrap: %d wrapped sector copies drawn, all past the nearest copy\n", wrap_copies);
    if (!wrap_copies) errors++;
    printf("state %zu zoom %zu tilt %zu\n", sizeof(State), __builtin_offsetof(State, zoom), __builtin_offsetof(State, tilt));
    printf("frames %u, total errors %d\n", st.frames, errors);
    return errors ? 1 : 0;
}
