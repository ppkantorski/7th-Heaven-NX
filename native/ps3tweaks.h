/*
 * ps3tweaks.h -- BUILD 868. What the C halves of the PS3Tweaks blobs
 * (ps3tweaks_c.c, ps3tweaks2_c.c) share: types, guest memory through the
 * translator, and calls into translated game functions.
 *
 * Licensed under GPLv3
 * Copyright (c) 2026 ppkantorski
 */
typedef unsigned int u32;
typedef int s32;
typedef short s16;
typedef signed char s8;
typedef unsigned short u16;
typedef unsigned char u8;
typedef unsigned long long u64;
typedef void *(*g2h_t)(u32);
typedef void (*fn_t)(void);

#define PT __attribute__((section(".text.pt"), noinline, used))
#define U8(a)  (*(volatile u8 *)g2h(a))
#define U16(a) (*(volatile u16 *)g2h(a))
#define U32(a) (*(volatile u32 *)g2h(a))

/* x86 context: eax +0, esp +0x10, ebp +0x14 */
#define ESP 4
#define EBP 5
#define ECX 1

/* call a translated cdecl game function with one argument */
static inline __attribute__((always_inline)) void call1(u32 *ctx, g2h_t g2h, fn_t fn, u32 arg)
{
    u32 sp = ctx[ESP];
    U32(sp - 4) = arg;
    ctx[ESP] = sp - 8;          /* the argument, and the return slot */
    fn();                       /* its `ret` pops the slot */
    ctx[ESP] = sp;              /* the caller's `pop` */
}

/* call a translated cdecl game function, or a native routine that takes
 * its arguments as one does, with five arguments */
static inline __attribute__((always_inline)) void call5(u32 *ctx, g2h_t g2h, fn_t fn, u32 a, u32 b, u32 c, u32 d, u32 e)
{
    u32 sp = ctx[ESP];
    U32(sp - 20) = a;
    U32(sp - 16) = b;
    U32(sp - 12) = c;
    U32(sp - 8) = d;
    U32(sp - 4) = e;
    ctx[ESP] = sp - 24;         /* the arguments, and the return slot */
    fn();
    ctx[ESP] = sp;
}

/* call a translated cdecl game function with no argument; its eax */
static inline __attribute__((always_inline)) u32 call0(u32 *ctx, fn_t fn)
{
    ctx[ESP] -= 4;              /* the return slot, popped by its `ret` */
    fn();
    return ctx[0];
}
