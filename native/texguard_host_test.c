/* Host test for texguard.c: a fake texture manager and the loader's calls. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "texguard.c"

static u64 table[NID];                 /* the manager's object table */
static u64 *renderer = table;          /* *(module+0x12CF4F0) */
static u64 **rvar = &renderer;
static u32 cursor;
static TG st;
static int errors;

static u32 create(void)                /* +0x44E0, round-robin */
{
    do cursor = (cursor + 1) % NID; while (!cursor || table[cursor]);
    table[cursor] = 0x1000 + cursor;
    tg_forget(&st, renderer, cursor, rvar);   /* the +0x4604 hook */
    return cursor;
}
static void delete(u32 id)             /* +0x42D0 */
{
    tg_forget(&st, renderer, id, rvar);
    table[id] = 0;
}
/* gfx_drv_load_texture for one slot: returns 1 if it uploaded */
static int load(u32 *arr, u32 pal)
{
    if (tg_check(&st, arr, pal, rvar)) return 0;      /* the shortcut */
    u32 old = arr[pal];                                /* +0x10D7B40 */
    u32 id = create();
    if (old) delete(old);
    tg_store(&st, arr, pal, id);
    return 1;
}
static void unload(u32 *arr, u32 n)   /* +0x10D6A60 */
{
    for (u32 i = 0; i < n; i++) if (arr[i]) delete(arr[i]);
    memset(arr, 0, 4 * n);
}
#define CHECK(c, what) do { if (!(c)) { printf("  FAIL: %s\n", what); errors++; } } while (0)

int main(void)
{
    u32 a[16] = {0}, b[16] = {0}, w[16] = {0};
    /* normal life: upload once, then every repeat call is the shortcut */
    CHECK(load(a, 10) == 1, "first load uploads");
    u32 ida = a[10];
    for (int i = 0; i < 100; i++) CHECK(load(a, 10) == 0, "repeat call is the shortcut");
    CHECK(a[10] == ida, "id kept");
    CHECK(st.dropped == 0, "nothing dropped in normal life");
    /* two palettes of one set, and another set: independent */
    CHECK(load(a, 11) == 1 && load(b, 10) == 1, "other slots upload");
    CHECK(load(a, 10) == 0 && load(a, 11) == 0 && load(b, 10) == 0, "all kept");
    /* unload then reload: uploads again (slot zeroed by unload) */
    unload(b, 16);
    CHECK(load(b, 10) == 1, "reload after unload uploads");
    /* THE BUG: a slot holding an id that was deleted behind its back */
    for (int k = 0; k < 5; k++) CHECK(load(w, k) == 1, "world textures");
    u32 stale = w[2];
    u32 fship[16] = {0};
    fship[10] = stale;                       /* the carried set's slot */
    unload(w, 16);                           /* world exit deletes it */
    CHECK(load(fship, 10) == 1 && fship[10] != stale, "deleted id is not trusted");
    /* ...and one deleted then handed out again to someone else */
    u32 f2[16] = {0};
    for (int k = 0; k < 3; k++) load(w, k);
    u32 reused = w[1];
    f2[12] = reused;                         /* stale copy of a live id */
    CHECK(load(f2, 12) == 1 && f2[12] != reused, "an id owned by another slot is not trusted");
    CHECK(table[reused] != 0 && w[1] == reused && load(w, 1) == 0, "the real owner keeps its id, not deleted");
    /* garbage */
    u32 g[16] = {0}; g[3] = 0xDEADBEEF;
    CHECK(load(g, 3) == 1 && g[3] < NID, "garbage id is replaced");
    /* the table slot emptied without a delete call (a bulk reset) */
    u32 c[16] = {0};
    load(c, 0);
    u32 cid = c[0];
    table[cid] = 0;
    CHECK(load(c, 0) == 1 && c[0] != cid, "an id no longer live is not trusted");
    /* a different manager object does not clear ours */
    u64 other[4];
    u32 before = st.forgets;
    tg_forget(&st, other, ida, rvar);
    CHECK(st.forgets == before && load(a, 10) == 0, "other manager ignored");
    printf("checks %u kept %u dropped %u stores %u forgets %u\n",
           st.checks, st.kept, st.dropped, st.stores, st.forgets);
    printf("sizeof(TG) %zu\n", sizeof(TG));
    printf("texguard host test: %d error(s)\n", errors);
    return errors ? 1 : 0;
}
