/*
 * texguard.c -- the texture loader may only trust a GPU texture id it
 * uploaded itself, into that exact slot, and that nobody has deleted or
 * re-created since.
 *
 * WHY (PROBE-599, texprobe v3 on hardware)
 * The port's native gfx_drv_load_texture (+0x10D6BC0) skips the upload when
 * texture_set->texturehandle[palette_index] is non-zero (+0x10D6E48:
 * `ldr w8, [x0, x21, lsl #2]; cbnz w8, ret`). On the second fship_2 entry
 * from the world map the game hands it five texture sets whose slots hold
 * ids 0x47..0x4B -- ids that belonged to world-map textures and had been
 * deleted. Every input to the upload is identical to the clean visit (image
 * and palette hashes match); the loader simply binds dead ids, and the
 * manager hands those ids out round-robin to the next textures created, so
 * the crew and Cloud are drawn with whatever lands on them: the speckles.
 *
 * The texture manager (renderer object *(module+0x12CF4F0)): a table of 1024
 * object pointers at +0, ids handed out round-robin from a cursor at
 * +0x2000. create +0x44E0 (the id is loaded at +0x4604), delete +0x42D0
 * (x0 manager, w1 id). The loader stores an id into a set's slot only at
 * +0x10D7C20 (`str w22, [x20, x21, lsl #2]`, inside +0x10D7B40).
 *
 * owner[id] = host address of the slot the loader stored it in. Cleared on
 * every create and delete of that id. At the shortcut the slot's id is kept
 * only if it is < 1024, live in the table, and owner[id] is that slot;
 * otherwise the slot is zeroed (NOT deleted -- the id is not ours) and the
 * loader uploads as for a new texture.
 *
 * Built like worldfar.c: position independent, no data/bss sections, the
 * state block is passed in x0 by the stubs.
 */
#include <stdint.h>
typedef uint8_t u8; typedef uint32_t u32; typedef uint64_t u64;

#define NID 1024u

typedef struct {
    u64 owner[NID];
    u32 checks, kept, dropped, stores, forgets, pad[3];
} TG;

static int live(u64 **rvar, u32 id)
{
    u64 *table = rvar ? *rvar : 0;
    return table && table[id] != 0;
}

/* at the shortcut: the id to use for this slot (0 = upload it) */
__attribute__((section(".text.tg_check")))
u32 tg_check(TG *st, u32 *arr, u64 palidx, u64 **rvar)
{
    u32 *slot = arr + palidx;
    u32 id = *slot;
    st->checks++;
    if (!id) return 0;
    if (id < NID && live(rvar, id) && st->owner[id] == (u64)(uintptr_t)slot) {
        st->kept++;
        return id;
    }
    st->dropped++;
    *slot = 0;
    return 0;
}

/* the loader's own store of a new id */
__attribute__((section(".text.tg_store")))
void tg_store(TG *st, u32 *arr, u64 palidx, u32 id)
{
    u32 *slot = arr + palidx;
    *slot = id;
    st->stores++;
    if (id < NID) st->owner[id] = (u64)(uintptr_t)slot;
}

/* the manager created or deleted this id */
__attribute__((section(".text.tg_forget")))
void tg_forget(TG *st, u64 *mgr, u32 id, u64 **rvar)
{
    if (!rvar || mgr != *rvar || id >= NID) return;
    st->owner[id] = 0;
    st->forgets++;
}
