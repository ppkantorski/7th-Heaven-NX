/*
 * autosize.c -- BUILD 860: FFNx's "ff7_field_autosize_text_box", the field
 * and world dialogue window sized to its text. See ff7nx_autosize.py.
 *
 * FFNx (src/ff7/misc.cpp autosize_text_box_window, japanese_text.cpp
 * auto_resize_text_box, the native-font English path) runs it at the entry
 * of the window opening step -- field x86 0x6317A9 (from the MESSAGE and
 * ASK opcodes, 0x630D50 / 0x6310A1), world 0x769A66 (0x769050 / 0x7693A1)
 * -- on the window's FIRST opening frame only: the create routine (0x631586)
 * has just set the current size to max(size / 4, 8) and the opening step
 * grows it from there, so that equality is never true again for the same
 * box. Then:
 *
 *   width   the widest line of every page, measured in the game's own
 *           glyph metrics (the byte table the field text draw reads,
 *           [0xDB958C]: advance 2 * (pad + width) half-pixels, 26 in the
 *           FE E9 fixed-width mode), + 32, halved;
 *   height  the most lines on any page, 32 half-pixels a line, + 50,
 *           halved -- and never taller than the script made it;
 *   then    width <= 320, height <= 224, and the box moved inside the
 *           320x224 screen if the new size pushed it out.
 *
 * As FFNx: E0..E4 are expanded ({CHOICE} = 10 spaces, the 4-space tab,
 * ", ", ".\"", "...\""), EA..F5 the character names (savemap, through the
 * game's own table at 0x919928, as 0x6CB9B8), FE DE / FE E1 (numbers)
 * count 50, FE E2 (a fixed string) 60, other FE D2..FF codes nothing,
 * E7 a new line, E8 / E9 a new page.
 *
 * Kept as the script wrote it (nothing is changed): any byte this does not
 * measure exactly (FA..FD font pages, FE below D2), a window with a special
 * type (WSPCL: the countdown timer and the counters draw inside the box at
 * their own offsets), a window id above 3, no text, no glyph table.
 *
 * Position independent, no data sections. Every guest byte goes through the
 * recompiler's translator (+0x10FC3A0), one call per byte, so a string that
 * crosses a page is read correctly under the paged model.
 */
typedef unsigned int u32;
typedef int s32;
typedef unsigned short u16;
typedef short s16;
typedef unsigned char u8;
typedef void *(*g2h_t)(u32);

#define WIN_ARRAY      0xCFF5B8u   /* text_box_window_data, 0x30 a window */
#define WIN_STRIDE     0x30
#define W_POS_X        0x04
#define W_POS_Y        0x06
#define W_W            0x08
#define W_H            0x0A
#define W_CUR_W        0x0C
#define W_CUR_H        0x0E
#define W_SPECIAL      0x1B        /* WSPCL type (x86 0x61FDA2) */
#define FIELD_STRINGS  0xCBF578u   /* current_dialog_string_pointer[4] */
#define WORLD_STRINGS  0xE3B210u   /* world_current_dialog_string_pointer[4] */
#define SPACING_PTR    0xDB958Cu   /* -> the 256-byte glyph metric table */
#define NAME_INDEX     0x919928u   /* u32[12]: 0x6CB9B8's character index */
#define NAMES          0xDBFD9Cu   /* savemap character records, 0x84 each */
#define NAME_STRIDE    0x84
#define MAX_TEXT       1024
#define MAX_W          320
#define MAX_H          224
#define LINE_STEP      32          /* half-pixels */

#define S __attribute__((noinline, section(".text.as")))

static inline __attribute__((always_inline)) u8 rd8(g2h_t g2h, u32 a)
{
    return *(volatile u8 *)g2h(a);
}

static inline __attribute__((always_inline)) u16 rd16(g2h_t g2h, u32 a)
{
    return (u16)(rd8(g2h, a) | (rd8(g2h, a + 1) << 8));
}

static inline __attribute__((always_inline)) u32 rd32(g2h_t g2h, u32 a)
{
    return (u32)rd16(g2h, a) | ((u32)rd16(g2h, a + 2) << 16);
}

static inline __attribute__((always_inline)) void wr16(g2h_t g2h, u32 a, s32 v)
{
    *(volatile u8 *)g2h(a) = (u8)v;
    *(volatile u8 *)g2h(a + 1) = (u8)(v >> 8);
}

/* the half-pixel advance of glyph c */
static S s32 adv(g2h_t g2h, u32 table, u32 c, s32 fixed)
{
    u32 m;
    if (fixed)
        return 26;
    m = rd8(g2h, table + c);
    return 2 * (s32)((m >> 5) + (m & 0x1F));
}

/* E0..E4's expansion, as FFNx's expandedCharacters */
static S s32 expansion(g2h_t g2h, u32 table, u32 c, s32 fixed)
{
    switch (c) {
    case 0xE0: return 10 * adv(g2h, table, 0x00, fixed);
    case 0xE1: return 4 * adv(g2h, table, 0x00, fixed);
    case 0xE2: return adv(g2h, table, 0x0C, fixed) + adv(g2h, table, 0x00, fixed);
    case 0xE3: return adv(g2h, table, 0x0E, fixed) + adv(g2h, table, 0x02, fixed);
    default:   return adv(g2h, table, 0xA9, fixed) + adv(g2h, table, 0x02, fixed);
    }
}

/* a character name (EA..F5), -1 if it holds a byte this cannot measure */
static S s32 name_width(g2h_t g2h, u32 table, u32 c, s32 fixed)
{
    u32 idx = rd32(g2h, NAME_INDEX + 4 * (c - 0xEA));
    u32 at, j, b;
    s32 w = 0;
    if (idx > 15)
        return -1;
    at = NAMES + idx * NAME_STRIDE;
    for (j = 0; j < 9; j++) {
        b = rd8(g2h, at + j);
        if (b == 0xFF)
            break;
        if (b >= 0xFA)
            return -1;
        w += adv(g2h, table, b, fixed);
    }
    return w;
}

/*
 * The text's size in half-pixels: *ow = widest line, *oh = most lines' height
 * (LINE_STEP each, the first line counted as 0). 0 if it cannot be measured.
 */
static S s32 measure(g2h_t g2h, u32 text, u32 table, s32 *ow, s32 *oh)
{
    s32 w = 0, h = 0, mw = 0, mh = 0, fixed = 0, i, n;
    u32 c, d;
    for (i = 0; i < MAX_TEXT; i++) {
        c = rd8(g2h, text + i);
        if (c == 0xFF)
            break;
        if (c >= 0xFA && c <= 0xFD)
            return 0;                       /* a font page: not English */
        if (c == 0xFE) {
            d = rd8(g2h, text + i + 1);
            if (d < 0xD2)
                return 0;
            if (d == 0xE9)
                fixed = !fixed;
            else if (d == 0xDE || d == 0xE1)
                w += 50;
            else if (d == 0xE2) {
                w += 60;
                i += 4;
            }
            i += 1;
            continue;
        }
        if (c >= 0xE0 && c <= 0xE4) {
            w += expansion(g2h, table, c, fixed);
            continue;
        }
        if (c >= 0xEA && c <= 0xF5) {
            n = name_width(g2h, table, c, fixed);
            if (n < 0)
                return 0;
            w += n;
            continue;
        }
        if (c == 0xE7) {
            if (w > mw) mw = w;
            w = 0;
            h += LINE_STEP;
            continue;
        }
        if (c == 0xE8 || c == 0xE9) {
            if (w > mw) mw = w;
            if (h > mh) mh = h;
            w = 0;
            h = 0;
            continue;
        }
        w += adv(g2h, table, c, fixed);
    }
    if (w > mw) mw = w;
    if (h > mh) mh = h;
    *ow = mw;
    *oh = mh;
    return 1;
}

static inline __attribute__((always_inline)) s32 quarter(s32 v)
{
    s32 q = v / 4;                          /* C truncation, as FFNx's int16 */
    return q < 8 ? 8 : q;
}

/*
 * At the opening step's entry. esp: the guest stack (the return slot, the
 * window id above it). world: 0 field, 1 world. Returns 1 if it resized.
 */
S s32 as_open(u32 esp, g2h_t g2h, u32 world)
{
    s32 win, ww, wh, tw, th, nw, nh, x, y;
    u32 wa, text, table;
    win = (s16)rd16(g2h, esp + 4);
    if (win < 0 || win > 3)
        return 0;
    wa = WIN_ARRAY + (u32)win * WIN_STRIDE;
    ww = (s16)rd16(g2h, wa + W_W);
    wh = (s16)rd16(g2h, wa + W_H);
    if ((s16)rd16(g2h, wa + W_CUR_W) != quarter(ww)
        || (s16)rd16(g2h, wa + W_CUR_H) != quarter(wh))
        return 0;                           /* not the first opening frame */
    if (rd8(g2h, wa + W_SPECIAL) != 0)
        return 0;
    text = rd32(g2h, (world ? WORLD_STRINGS : FIELD_STRINGS) + 4 * (u32)win);
    table = rd32(g2h, SPACING_PTR);
    if (!text || !table)
        return 0;
    if (!measure(g2h, text, table, &tw, &th))
        return 0;
    nw = (tw + 32) / 2;
    nh = (th + 50) / 2;
    if (nh > wh)
        nh = wh;                            /* never taller than authored */
    if (nw > MAX_W) nw = MAX_W;
    if (nw < 0) nw = 0;
    if (nh > MAX_H) nh = MAX_H;
    if (nh < 0) nh = 0;
    x = (s16)rd16(g2h, wa + W_POS_X);
    y = (s16)rd16(g2h, wa + W_POS_Y);
    if (x > MAX_W - nw) x = MAX_W - nw;
    if (x < 0) x = 0;
    if (y > MAX_H - nh) y = MAX_H - nh;
    if (y < 0) y = 0;
    wr16(g2h, wa + W_W, nw);
    wr16(g2h, wa + W_H, nh);
    wr16(g2h, wa + W_POS_X, x);
    wr16(g2h, wa + W_POS_Y, y);
    return 1;
}
