#!/usr/bin/env python3
"""
ff7nx_audiopool.py -- give the engine's 32 MB sound-buffer pool the room the
PC never had to ask for.

THE CRASH (build 536's exit-report run, Chocobo Race)
=====================================================
The race closed the software with no report. With every `exit()` trapped,
the crash report's call chain read, innermost first:

    SoundBufferImpl::Init   (vtable slot +0x18, +0x1126A70)
    SoundBufferImpl factory (+0x1125830 -> blr at +0x11258A0)
    DirectSoundBuffer ctor  (+0x1200 -> +0x1274)
    DirectSound::CreateSoundBuffer (+0xC60 -> +0xCD8)
    fake_dsound             (+0x10D4150)
    ... x86 game code through the dispatcher thunk

`+0x1126A70` asks `g_WaveBufferAllocator` (an nn::mem::StandardAllocator)
for the decoded PCM of the sound it is creating, and on NULL prints

    [ASSERT] g_WaveBufferAllocator alloc failed ! increase size ?

and calls exit(-1) at +0x1126BEC. It is the only exit in that function.

WHY IT RUNS OUT HERE AND NOT ON PC
==================================
On PC a DirectSound buffer is ordinary RAM with no cap. On Switch every one
of them -- each cached SFX (`sfx_load` keeps its buffer in the 750-entry
table at 0xDE0128 until `sfx_unload(id)`), each channel's one-shot, the
music, ambience and voice stream rings -- comes out of ONE fixed pool:

    +0x1127DA0  adrp x20, #0x1feb000        static .bss block
    +0x1127DA8  mov  w2, #0x2000000         32 MB
    +0x1127DB0  bl   StandardAllocator::Initialize(x0, x20, w2)
    +0x1127DBC  mov  w3, #0x2000000
    +0x1127DC8  bl   nn::audio::AcquireMemoryPool(x21, pool, x20, w3)

Cosmo Memory's sound effects are MS ADPCM like vanilla's, but its looping
beds are long recordings where vanilla had a short loop. Decoded (which is
what the pool holds), measured on the shipped audio.dat:

    slot 561   0.28 MB -> 10.23 MB        slot  38   0.33 MB ->  6.63 MB
    slot 381   0.74 MB ->  8.69 MB        slot 568   0.17 MB ->  5.49 MB
    slot  39   1.05 MB ->  7.63 MB        slot 223   0.28 MB ->  5.47 MB
    all 750 slots: 272 MB -> 356 MB

Two or three of those resident at once, plus the stream rings, is the pool.
Removing the minigame archives did not help because it is not a texture
problem -- which is exactly what the tester found.

THE FIX
=======
Allocate the pool from the nnSdk heap instead of the static block, at the
size asked for. The heap is the same one `ff7nx_heap` measured: +0x1150DE0
sizes it to all remaining application memory, and it has already carried a
256 MB guest heap and, on the graphics-pool ladder, a 512 MB graphics block.

`+0x1150BC0` is the port's own aligned allocate over that heap:

    adrp x8, ... ; ldr x8, [x8, #0x790]   heap initialised?
    cbz  x8, -> return NULL
    mov  x2, x1 ; mov x1, x0              (size, alignment)
    b    nn::mem::StandardAllocator::Allocate(heap, size, alignment)

so the pool comes back 4 KB aligned, which nn::audio requires of a memory
pool's address and size. If it returns NULL (heap not up yet, or no room),
the cave falls back to the STOCK static block and the stock 32 MB -- this
patch can never make start-up worse than the game shipped.

    +0x1127DA0  adrp x20, #0x1feb000   ->  bl cave      (x20 = base, w22 = size)
    +0x1127DA4  add  x20, x20, #0         unchanged (adds 0)
    +0x1127DA8  mov  w2, #0x2000000    ->  mov w2, w22
    +0x1127DBC  mov  w3, #0x2000000    ->  mov w3, w22

x22 is callee-saved by this function (stp x22, x21 in its prologue, ldp at
+0x1127E08) and is dead from +0x1127D7C to the return, so the cave may leave
the size in it. x0 (the allocator object loaded at +0x1127D9C) is preserved.
The static block is referenced by exactly one instruction in the module
(+0x1127DA0), so nothing else is left pointing at the old pool.
"""
import os
import struct

import a64 as A
import ff7nx_audio_cave as AC
import ff7nx_cave
import nxmap

STOCK_MB = 32
DEFAULT_MB = 96
MB_ENV = 'SEVENTH_NX_AUDIO_POOL_MB'
CHOICES = (32, 48, 64, 96, 128)

BASE_SITE = 0x1127DA0
BASE_ORIG = 0x90007634            # adrp x20, #0x1feb000
ADD_SITE = 0x1127DA4
ADD_ORIG = 0x91000294             # add  x20, x20, #0
SIZE_SITE_1 = 0x1127DA8
SIZE_SITE_2 = 0x1127DBC
SIZE_ORIG_1 = 0x320703E2          # mov  w2, #0x2000000
SIZE_ORIG_2 = 0x320703E3          # mov  w3, #0x2000000
INIT_CALL = (0x1127DB0, 0x9400A748)     # bl StandardAllocator::Initialize
POOL_CALL = (0x1127DC8, 0x9400A76E)     # bl nn::audio::AcquireMemoryPool
STATIC_POOL = 0x1FEB000
STOCK_BYTES = STOCK_MB << 20

ALIGNED_ALLOC = 0x1150BC0         # (size, alignment) -> ptr or NULL
ALIGNED_ALLOC_BODY = (0xF00174C8, 0xF943C908, 0xB40000C8, 0xAA0103E2,
                      0xAA0003E1, 0xF00174C0, 0x911E6000, 0x1400036D,
                      0xAA1F03E0, 0xD65F03C0)
POOL_ALIGN = 0x1000


def pool_mb(env=None):
    """The requested pool size in MB; STOCK_MB means 'leave the game alone'."""
    raw = (os.environ if env is None else env).get(MB_ENV, '').strip()
    if not raw:
        return DEFAULT_MB
    mb = int(raw)
    if mb not in CHOICES:
        raise ValueError('%s must be one of %s' % (MB_ENV, CHOICES))
    return mb


def _csel64(rd, rn, rm, cond):
    """CSEL Xd, Xn, Xm, cond"""
    return 0x9A800000 | (rm << 16) | (cond << 12) | (rn << 5) | rd


def _csel32(rd, rn, rm, cond):
    """CSEL Wd, Wn, Wm, cond"""
    return 0x1A800000 | (rm << 16) | (cond << 12) | (rn << 5) | rd


def _cmp64_zero(rn):
    """CMP Xn, #0 (SUBS XZR, Xn, #0)"""
    return 0xF100001F | (rn << 5)


NE = 1


def _movz_hi(rd, value):
    """MOVZ Wd, #(value >> 16), LSL #16 -- every pool size is a whole MB."""
    if value & 0xFFFF or value >> 32:
        raise ValueError('pool size %#x is not a whole number of 64 KB units'
                         % value)
    return 0x52A00000 | ((value >> 16) << 5) | rd


def build_cave(size_bytes):
    """The cave builder (emit_laid_out contract). 15 words.

    BRANCH-FREE on purpose: the choice between the heap block and the stock
    static pool is two CSELs, so the cave holds nothing but `bl`/`adrp`,
    both of which reach far beyond the whole module. That lets it take any
    free padding hole (ff7nx_cave.ANY_SPAN) -- by the time this pass runs,
    the other module passes have spent every 960 KB window a `cbz` would
    need, and very little padding is left at all, so it is also as short as
    it can be:

      * no stack frame. LR waits in x22 across the call (callee-saved, dead
        in this function until the cave itself writes the size there);
      * the allocator object (x0, loaded at +0x1127D9C) waits in x20, which
        the cave overwrites with the pool base anyway;
      * each size is one MOVZ ..., LSL #16.

    Flags are not live here: the next consumer is a `tbz` after two further
    calls.
    """
    def build(cave, addr):
        a = AC.Asm(cave, addr)
        a.emit(A.mov_reg64(22, 30))               # LR -> x22
        a.emit(A.mov_reg64(20, 0))                # allocator object -> x20
        a.emit(_movz_hi(0, size_bytes))           # x0 = size
        a.emit(A.movz(1, POOL_ALIGN))             # x1 = 4 KB alignment
        a.emit(A.bl(a.pc(), ALIGNED_ALLOC))
        a.emit(A.mov_reg64(30, 22))               # LR back
        a.emit(_cmp64_zero(0))
        a.emit(A.adrp(9, a.pc(), STATIC_POOL))    # x9 = the game's own pool
        a.emit(_csel64(9, 0, 9, NE))              # x9 = block ? block : stock
        a.emit(A.mov_reg64(0, 20))                # allocator object back
        a.emit(A.mov_reg64(20, 9))                # x20 = pool base
        a.emit(_movz_hi(10, size_bytes))
        a.emit(_movz_hi(11, STOCK_BYTES))
        a.emit(_csel32(22, 10, 11, NE))           # w22 = matching size
        a.emit(A.ret())
        return a.resolve()
    return build


def _word(text, va):
    return struct.unpack_from('<I', text, va)[0]


def state(text):
    """'stock', 'patched', or a reason string."""
    if (_word(text, BASE_SITE) == BASE_ORIG
            and _word(text, SIZE_SITE_1) == SIZE_ORIG_1
            and _word(text, SIZE_SITE_2) == SIZE_ORIG_2):
        return 'stock'
    if ((_word(text, BASE_SITE) & 0xFC000000) == 0x94000000
            and _word(text, SIZE_SITE_1) == A.mov_reg(2, 22)
            and _word(text, SIZE_SITE_2) == A.mov_reg(3, 22)):
        return 'patched'
    return 'unknown'


def verify(text):
    """Every word the patch relies on, checked against the measured module."""
    bad = []
    for va, want, what in ((ADD_SITE, ADD_ORIG, 'add x20, x20, #0'),
                           (INIT_CALL[0], INIT_CALL[1], 'Initialize call'),
                           (POOL_CALL[0], POOL_CALL[1],
                            'AcquireMemoryPool call')):
        if _word(text, va) != want:
            bad.append('+%#x (%s) holds %08X, expected %08X'
                       % (va, what, _word(text, va), want))
    for i, want in enumerate(ALIGNED_ALLOC_BODY):
        if _word(text, ALIGNED_ALLOC + 4 * i) != want:
            bad.append('+%#x: the aligned allocator is not the measured one'
                       % (ALIGNED_ALLOC + 4 * i))
            break
    return bad


def apply_to_nso(src, dest, mb=None, log=lambda *_: None):
    """Install the pool cave, `src` -> `dest`. Returns a report or None."""
    mb = pool_mb() if mb is None else mb
    with open(src, 'rb') as handle:
        blob = handle.read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    st = state(text)
    if st != 'stock':
        raise ValueError('audio pool site is %s, not the stock init' % st)
    bad = verify(text)
    if bad:
        raise ValueError('; '.join(bad))
    if mb == STOCK_MB:
        return None
    size = mb << 20
    pool = ff7nx_cave.HolePool(text, starts=set(nxmap.Main(src).arm_starts))
    entry, placed = ff7nx_cave.emit_laid_out(pool, build_cave(size),
                                             span=ff7nx_cave.ANY_SPAN)
    placed[BASE_SITE] = A.bl(BASE_SITE, entry)
    placed[SIZE_SITE_1] = A.mov_reg(2, 22)
    placed[SIZE_SITE_2] = A.mov_reg(3, 22)
    for where, word in placed.items():
        struct.pack_into('<I', text, where, word)
    raw[0] = bytes(text)
    out = AC.pack(blob, raw, 0)

    check_segs, check_raw = AC.segments(out)
    assert check_segs[0][2] == segs[0][2]
    assert state(check_raw[0]) == 'patched'
    assert struct.unpack_from('<I', out, 0x3C)[0] == \
        struct.unpack_from('<I', blob, 0x3C)[0]

    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, 'wb') as handle:
        handle.write(out)
    return {'mb': mb, 'cave_entry': entry, 'cave_words': len(placed) - 3,
            'code': {k: v for k, v in placed.items()}}
