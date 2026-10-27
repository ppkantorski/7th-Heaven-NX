/* BUILD 797: the gfx driver's texture conversion (+0x10D77D0), paletted
 * path, in native code.
 *
 * load_texture (+0x10D6BC0) converts a texture set's pixels to RGBA with
 * +0x10D77D0 before it creates the texture. For a paletted format
 * (fmt+0x2C == 1) each source byte is a palette index, and for EVERY pixel
 * the stock loop reloads the palette's guest address (fmt+0x24) and
 * translates it (+0x10FC3A0), then reads the entry at that one host base
 * plus (index + offset) * 4. Here the base is translated once; everything
 * else is the stock loop:
 *
 *   rows = w2, cols = w3 (row r's pixels at r * cols .. + cols, in and out)
 *   fmt+0x10 == 0, rows == 0 or cols == 0: nothing written
 *   index > fmt+0x1C (the palette count, reread per pixel in stock, constant
 *   here): stop there, the rest of the buffer as it was
 *   colour key (w6 bit 0) and index 0: 0
 *   else p = pal[index + w7]; alpha byte 0xFE -> p & 0xFFFFFF | stack arg
 *   out = p with bytes 0 and 2 swapped
 *
 * Any other format goes to the stock function with the same arguments.
 * tests/test_palconv.py runs both under the Unicorn emulator on the dumped
 * module and compares the output buffers. */
typedef unsigned int u32;
typedef unsigned char u8;
typedef unsigned long u64;

typedef u8 *(*g2h_t)(u32);
typedef void (*conv_t)(const u8 *, u32 *, u32, u32, const u8 *, u32, u32,
                       u32, u32);

#define F_ROWS_OK  0x10
#define F_COUNT    0x1C
#define F_PALETTE  0x24
#define F_PALETTED 0x2C

static inline __attribute__((always_inline)) u32 rd(const u8 *p, u32 off)
{
    return *(const u32 *)(p + off);
}

/* a[0..7] = x0..x7 of the call, a[8] = its stack argument (the alpha) */
__attribute__((section(".text.pc")))
void pc_convert(const u64 *a, g2h_t g2h, conv_t stock)
{
    const u8 *src = (const u8 *)a[0];
    u32 *out = (u32 *)a[1];
    u32 rows = (u32)a[2], cols = (u32)a[3];
    const u8 *fmt = (const u8 *)a[4];
    u32 key = (u32)a[6] & 1, off = (u32)a[7], alpha = (u32)a[8];
    const u32 *pal;
    u32 n, r, c, k;

    if (rd(fmt, F_PALETTED) != 1 || !rd(fmt, F_PALETTE)) {
        stock(src, out, rows, cols, fmt, (u32)a[5], (u32)a[6], off, alpha);
        return;
    }
    if (!rd(fmt, F_ROWS_OK) || !rows || !cols)
        return;
    pal = (const u32 *)g2h(rd(fmt, F_PALETTE));
    n = rd(fmt, F_COUNT);
    for (r = 0, k = 0; r < rows; r++) {
        for (c = 0; c < cols; c++, k++) {
            u32 i = src[k], p;
            if (n < i)
                return;
            if (key && !i) {
                p = 0;
            } else {
                p = pal[i + off];
                if ((p >> 24) == 0xFE)
                    p = (p & 0xFFFFFF) | alpha;
            }
            out[k] = (p & 0xFF00FF00) | ((p >> 16) & 0xFF) | ((p & 0xFF) << 16);
        }
    }
}
