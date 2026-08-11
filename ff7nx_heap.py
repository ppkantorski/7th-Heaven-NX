#!/usr/bin/env python3
"""
ff7nx_heap.py -- raise FF7's guest heap above the 64 MB the port hardcoded.

WHAT THE HEAP ACTUALLY IS -- ALL MEASURED FROM `exefs/main`
==========================================================
FF7 on Switch is the Win32 x86 binary (`ff7_en`) run under a recompiler
inside `MaterialSX.nss` (= `exefs/main`). The port does NOT recompile the
Win32 API; it re-implements it natively and binds it by NAME through a
203-entry shim table at module offset **0x1196B98**, records 0x30 bytes,
name at `+0x00`, ARM64 entry point at `+0x18`:

    HeapCreate    +0x010EE7B0        VirtualAlloc  +0x010EE560
    HeapAlloc     +0x010EE860        VirtualFree   +0x010EE5F0
    HeapFree      +0x010EE9C0        LocalFree     +0x010EE470

There is no `HeapReAlloc`, no `GetProcessHeap`, and **`ff7_en` calls
`HeapCreate` exactly once**, at x86 `0x40E019`, as

    push 0 ; push 0x1000 ; push 0        ->  HeapCreate(0, 0x1000, 0)

i.e. "growable heap, 4 KB initial, no maximum". The shim **ignores all three
arguments** and hands back a fixed 64 MB pool. The 64 MB is a port decision.
Nothing in FF7 asks for it and nothing in FF7 depends on it.

`HeapCreate` at +0x010EE7B0, annotated
--------------------------------------
    +10EE7BC  mov  w20, #0xf820             \\  w20 = 0x03FFF820
    +10EE7C0  movk w20, #0x3ff, lsl #16     /   = first free block's size
    +10EE7C4  add  w2,  w20, #0xfc0             region size = 0x040007E0
    +10EE7C8  adrp x0, .. add x0, #0x10         "<heap>"
    +10EE7D0  mov  w1,  #0x2000000              guest base 0x02000000
    +10EE7D4  bl   #0x10FB440                   map_region(name, base, size)
    +10EE7D8  mov  w0,  #0x2000000
    +10EE7DC  bl   #0x10FC3A0                   guest -> host
    +10EE7E0  mov  x8, #0x0400000004000000      desc.total = desc.remains
    +10EE7E8  str  x8, [x0]                       = 0x04000000  (64 MB)
    +10EE7EC  mov  w0, #0x7ac ; movk #0x200,16   first block header
    +10EE7F4  add  w8, w0, #0x34                 = guest 0x020007AC
    +10EE7F8  str  w8, [x19, #8]                 desc.freelist = 0x020007E0
    +10EE838  str  w20, [x0, #0x14]              block.size  = 0x03FFF820
    +10EE848  str  wzr, [x0, #0x20]              block.used  = 0
    +10EE850  mov  w0, #0x2000000                returns the handle
                                                 = the descriptor address

Descriptor: `+0x00` total, `+0x04` remains, `+0x08` free-list head.
Block:      `+0x14` size, `+0x18` prev, `+0x1c` next, `+0x20` used, header 0x34.
Both read straight off the heap-dump format strings at 0x11A93D8 /
0x11AA4AC, which print `[desc+0]`/`[desc+4]` as `total`/`remains` and
`[blk+0x14]`/`[blk+0x18]`/`[blk+0x20]` as `size`/`prev`/`used`.

`HeapAlloc` at +0x010EE860 is **first fit** over that list, and on failure
calls the dump at +0x010EE660, which `fopen`s `"Documents/heap_dump.txt"` --
a path that cannot exist on Switch -- and nnSdk aborts. That abort is the
Men's Hall crash. The `fopen` is the death rattle, not the bug.

`mov x8, #0x0400000004000000` is an **ORR (immediate) logical immediate**,
which is exactly why scanning for `movz`/`movk` pairs found nothing twice.

THE GUEST ADDRESS SPACE -- MEASURED
==================================
`map_region` (+0x10FB440) carves every guest region out of ONE host
`malloc`. Records are 0x18 bytes: `char name[8]; u32 base; u32 end;
void *host;`. The page table it fills is at `[0x12CE8C0] -> 0x12EACE8`,
**8,388,608 bytes = 2^20 entries x 8** -- it covers the whole 32-bit guest
space, so guest addresses are not a constraint. Four callers, and only four:

    +0x0009B34   stack        0x00170000 .. 0x00190000     0x00020000
    +0x010FCD5C  ff7_en PE    0x00400000 .. 0x00F6E000     0x00B6E000
    +0x010EE7D4  "<heap>"     0x02000000 .. 0x06001000     0x04001000
    +0x010EE5A8  "<virt>"     0x06000000 .. (VirtualAlloc MEM_RESERVE only)

    total committed                                        0x04B8F000
    host arena  (mov w21, #0x5000000 at +0x10FB4C0)        0x05000000
    spare                                                  0x00471000  (4.44 MB)

**That is the real ceiling.** The heap cannot grow in place: `<virt>` sits
immediately above it, and the arena has 4.44 MB left. Raising the heap
without raising the arena makes `map_region` take its failure branch at
+0x10FB580 and abort. Raising it without moving `<virt>` overlaps two
regions in the page table, which corrupts silently instead of failing.

So the patch is three things, not one: **heap size, `<virt>` base, arena
size** -- and they have to move together.

The lazy arena init is INLINED at FIVE call sites
-------------------------------------------------
+0x10FB4C0, +0x10FCC68, +0x10FCE34, +0x10FCF90, +0x10FD358. All five write
`total` and `remains` of the SAME arena struct (`[0x12CE8B8] -> 0x12E9348`)
with the same shape:

    mov w?, #0x5000000 ; mov x0, w? ; str w?, [xB, #0x10]
    bl  #0x1150C10     ; str w?, [xB, #0x14] ; stp x0, x0, [xB]

Whichever runs first wins, so patching four of five leaves a build whose
arena size depends on start-up order. All five are in `SITES`.

WHAT IS **NOT** PATCHED, AND WHY
================================
The guest base stays **0x02000000**. The port's own graphics driver calls
`HeapAlloc`/`HeapFree` with that handle as a literal in twelve places --
verified by disassembling each one to its `bl`, not by matching the
immediate, because 0x2000000 is also a common single-bit mask:

    HeapAlloc  +0x10D36C4 +0x10D3748 +0x10D5E6C +0x10D603C +0x10D6C9C
               +0x10D6D8C +0x10D6F14 +0x10D70C4 +0x10E25E0
    HeapFree   +0x10D6474 +0x10D6AE0 +0x10D7BD0

Keeping the base means none of them move.

`<virt>` keeps the stock relationship `base + HEAP_BYTES`, so the region
tail still overlaps `<virt>`'s first page by 0x1000 exactly as stock does.
That overlap is harmless -- the free block ends at `base + HEAP_BYTES`, so
the 0x7E0 tail past it is never handed out -- and reproducing it keeps this
patch to "the same layout, larger", with no new variable to be wrong about.

SIZES
=====
`desc.total` and `desc.remains` are written by ONE 64-bit ORR-immediate, so
`(size << 32) | size` must be a legal AArch64 logical immediate. That admits
every size whose bit pattern is a contiguous run of ones -- 96, 128, 192,
224, 256, 384, 448, 512 MB -- and rejects 160 and 320. `encodable()` is the
authority; the encoder is built by brute-forcing the ARM decode pseudocode
and is checked against every stock word this module touches before it will
write anything (`selftest()`).

    python3 ff7nx_heap.py <main> --show
    python3 ff7nx_heap.py <main> --mb 256 --out <main.patched>
    python3 ff7nx_heap.py <main> --stock --out <main.patched>
    python3 ff7nx_heap.py --selftest
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
from pathlib import Path

# ONLY this directory. The idiom copied from `ff7nx_fieldbuf` also inserted
# the PARENT, which is a live hazard: if anything named `build.py`,
# `lgp.py`... sits beside the project folder it shadows the real module and
# the build silently reads someone else's constants. That happened here --
# a stale `build.py` one level up made `build.FIELD_BG_RAW_CAP` read
# 1,677,721 when the real file says 13,421,772.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

MB = 1 << 20

# ---------------------------------------------------------------- constants
STOCK_MB = 64                       # what the port ships
STOCK_HEAP = STOCK_MB * MB          # 0x04000000
STOCK_ARENA = 0x05000000            # the one host malloc every region shares
GUEST_BASE = 0x02000000             # heap base; NOT patched, see the header
DESC_GAP = 0x7E0                    # bytes of the region below the first block
BLOCK_HDR = 0x34

# The size to build. A CODE CONSTANT, not an environment variable.
#
# 256 MB is four times stock. The measurement it is sized against: Men's
# Hall died with under 192 KB of headroom while holding 1.38 MB of field
# background, so FF7 is running with essentially all 64 MB in use. A 512px
# truecolor page is 4x the pixels of a 256px one (1 MB of surface, not
# 0.25 MB), the heaviest field holds 15 pages, and `HeapAlloc` is FIRST FIT
# -- so the number that has to fit is not the total but the largest single
# contiguous request, and fragmentation across a play session eats the
# difference. 256 MB leaves ~192 MB of headroom for a worst case near 20 MB.
HEAP_MB = 256

# Delete the call to FF7's heap dump on allocation failure. ONE WORD, and it
# is a bug fix rather than a workaround.
#
# MEASURED. When `HeapAlloc` cannot satisfy a request it calls the dump at
# +0x10EE660, which opens "Documents/heap_dump.txt" -- a Windows path with no
# Switch mount behind it. The port's author EXPECTED that to fail and handled
# it: the very next instruction after the `fopen` is
#
#     +10EE6A0  bl  #0x11511a0      ; fopen
#     +10EE6A4  cbz x0, #0x10ee74c  ; NULL FILE* -> epilogue, return quietly
#
# So a NULL return was designed for. nnSdk does not give one -- its `fopen`
# ABORTS internally (fopen+0x40 -> open+0xa0 -> __nnmusl_Is_Dir+0xd0 ->
# nn::diag::detail::Abort, Result 0x2F6202) when the path cannot resolve. The
# crash is the diagnostic, not the condition it is diagnosing.
#
# With this NOP, `HeapAlloc` returns NULL exactly as Win32 `HeapAlloc` does on
# failure -- which is a documented return value FF7 has to cope with, and
# which `field_load_textures` (x86 0x640292) demonstrably does cope with: it
# stops the load loop and leaves the remaining pages on handle 0.
#
# WHY THIS IS THE TEST THAT MATTERS. Raising the heap to 256 MB did NOT stop
# Men's Hall crashing, and a single 0x0FFFF820 free block cannot fail a
# request that fitted in 64 MB. Removing the abort separates the two things
# that measurement conflated:
#
#   * room loads (perhaps with pages missing) -> an allocation really is
#     failing, we can now SEE how much, and the size of the heap was never
#     the variable.
#   * room loads perfectly -> nothing was failing that FF7 minded; the
#     ENTIRE crash was the port's debug path, and it was never a memory
#     problem at all.
#   * still crashes -> it is a different crash, and the crash report says so.
#
# There is no case in which keeping an abort that can only ever abort is
# better, so this defaults ON and is independent of HEAP_MB.
NO_HEAP_DUMP = True

# Arena headroom above the heap + exe + stack. Stock leaves exactly this
# much, and preserving it rather than picking a new number keeps `<virt>`
# and everything else behaving as it does today.
ARENA_SPARE = 0x00471000

EXE_SPAN = 0x00B6E000               # ff7_en's five PE sections, page-rounded
STACK_SPAN = 0x00020000             # guest stack, +0x10FD310 passes 0x20000


# ------------------------------------------------------- AArch64 immediates
def _logical_immediates():
    """{value: (N, immr, imms)} for every legal 64-bit logical immediate.

    Built by running the ARM decode pseudocode forwards over all (N, immr,
    imms), not by deriving the encoding -- the derivation is the part that
    is easy to get subtly wrong, and `selftest()` checks the result against
    the module's own stock words.
    """
    out = {}
    for n in (0, 1):
        for imms in range(64):
            x = (n << 6) | ((~imms) & 0x3F)
            length = x.bit_length() - 1
            if length < 1:
                continue
            size = 1 << length
            s = imms & (size - 1)
            if s == size - 1:                     # all-ones is not encodable
                continue
            for immr in range(64):
                r = immr & (size - 1)
                mask = (1 << size) - 1
                pat = (1 << (s + 1)) - 1
                rot = (((pat >> r) | (pat << (size - r))) & mask) if r else pat
                val = 0
                for i in range(64 // size):
                    val |= rot << (i * size)
                out.setdefault(val, (n, immr, imms))
    return out


_LOGIMM = _logical_immediates()


def orr_xzr64(rd: int, value: int) -> int | None:
    """`mov Xd, #value` as ORR Xd, XZR, #imm. None if not encodable."""
    enc = _LOGIMM.get(value & 0xFFFFFFFFFFFFFFFF)
    if enc is None:
        return None
    n, immr, imms = enc
    return 0xB2000000 | (n << 22) | (immr << 16) | (imms << 10) | (31 << 5) | rd


def orr_xzr32(rd: int, value: int) -> int | None:
    """`mov Wd, #value` as ORR Wd, WZR, #imm. None if not encodable."""
    v = value & 0xFFFFFFFF
    enc = _LOGIMM.get((v << 32) | v)
    if enc is None or enc[0]:
        return None
    _, immr, imms = enc
    return 0x32000000 | (immr << 16) | (imms << 10) | (31 << 5) | rd


def movz32(rd: int, imm16: int, shift: int = 0) -> int:
    return 0x52800000 | ((shift // 16) << 21) | ((imm16 & 0xFFFF) << 5) | rd


def movk32(rd: int, imm16: int, shift: int = 0) -> int:
    return 0x72800000 | ((shift // 16) << 21) | ((imm16 & 0xFFFF) << 5) | rd


def _hex(word: int) -> str:
    return ' '.join('%02X' % b for b in struct.pack('<I', word))


# --------------------------------------------------------------- the sizes
def heap_bytes(mb: int = None) -> int:
    return (HEAP_MB if mb is None else mb) * MB


def region_span(heap: int) -> int:
    """Page-rounded bytes `map_region` takes out of the arena for the heap."""
    return (heap + DESC_GAP + 0xFFF) & ~0xFFF


def virt_base(heap: int) -> int:
    return GUEST_BASE + heap


def arena_bytes(heap: int) -> int:
    return region_span(heap) + EXE_SPAN + STACK_SPAN + ARENA_SPARE


def encodable(mb: int) -> str | None:
    """None if this size can be written, else why it cannot."""
    heap = mb * MB
    if mb <= 0 or heap % MB:
        return 'not a whole number of MB'
    if heap <= STOCK_HEAP:
        return ('%d MB is not larger than the stock %d MB' % (mb, STOCK_MB))
    if orr_xzr64(8, (heap << 32) | heap) is None:
        return ('0x%08X is not an AArch64 logical immediate -- the descriptor '
                'is written by ONE ORR-immediate, so the size must be a '
                'contiguous run of bits (96/128/192/224/256/384/448/512 MB '
                'are; 160 and 320 are not)' % heap)
    v = virt_base(heap)
    if v & 0xFFFF or (v >> 16) > 0xFFFF:
        return '<virt> base 0x%08X is not a MOVZ-encodable hi16' % v
    a = arena_bytes(heap)
    if a & 0xFFFF or (a >> 16) > 0xFFFF:
        return 'arena 0x%08X is not a MOVZ-encodable hi16' % a
    if a > 0x40000000:
        return 'arena 0x%08X is over 1 GB of host malloc' % a
    return None


def sizes(mb: int = None) -> list[int]:
    """Every size this module can write, largest first."""
    return [m for m in (512, 448, 384, 256, 224, 192, 128, 96)
            if encodable(m) is None]


# ------------------------------------------------------------- the sites
# `words` is the STOCK block read out of the module. Every word not named in
# `fields` must match exactly before anything is written -- these immediates
# are common encodings (0x5000000 as a MOVZ appears twelve times in .text,
# seven of them unrelated masks) and a bare word compare would not prove the
# hook landed on the right instruction. Each signature carries the
# neighbouring store that proves what the value is for.
#
# kinds:
#   'desc64'   ORR Xd, XZR, #(size<<32|size)     descriptor total+remains
#   'blockhi'  MOVK Wd, #imm16, lsl #16          first block size, high half
#   'guest32'  ORR Wd, WZR, #imm  or MOVZ hi16   a guest base address
#   'movzhi'   MOVZ Wd, #imm16, lsl #16          the arena size
SITES = [
    {
        'name': 'HeapCreate: first free block size',
        'va': 0x10EE7BC,
        'words': [
            0x529F0414,   # mov  w20, #0xf820           low half, never moves
            0x72A07FF4,   # movk w20, #0x3ff, lsl #16   <- high half
            0x113F0282,   # add  w2, w20, #0xfc0        region size
            0xD00005C0,   # adrp x0, #0x11a8000         "<heap>"
            0x91004000,   # add  x0, x0, #0x10
            0x320703E1,   # mov  w1, #0x2000000         guest base
            0x9400331B,   # bl   #0x10fb440             map_region
        ],
        'fields': {1: ('blockhi', 20, 'heap')},
    },
    {
        'name': 'HeapCreate: descriptor total and remains',
        'va': 0x10EE7E0,
        'words': [
            0xB20603E8,   # mov  x8, #0x0400000004000000  <- total | remains
            0xAA0003F3,   # mov  x19, x0
            0xF9000008,   # str  x8, [x0]                  desc+0, desc+4
            0x5280F580,   # mov  w0, #0x7ac
            0x72A04000,   # movk w0, #0x200, lsl #16       first block header
            0x1100D008,   # add  w8, w0, #0x34
            0xB9000A68,   # str  w8, [x19, #8]             desc+8 free list
        ],
        'fields': {0: ('desc64', 8, 'heap')},
    },
    {
        'name': 'HeapAlloc failure: do not call the heap dump',
        'va': 0x10EE8CC,
        'words': [
            0x35FFFE53,   # cbnz w19, #0x10ee894    keep walking the free list
            0x2A1403E0,   # mov  w0, w20            the heap descriptor
            0x97FFFF63,   # bl   #0x10ee660         <- THE ABORT
            0x2A1F03F3,   # mov  w19, wzr           return NULL
            0x2A1303E0,   # mov  w0, w19
        ],
        'fields': {2: ('nop', 0, 'nodump')},
    },
    {
        'name': 'VirtualAlloc MEM_RESERVE: <virt> base (w1)',
        'va': 0x10EE594,
        'words': [
            0xD00005E0,   # adrp x0, #0x11ac000            "<virt>"
            0x91020000,   # add  x0, x0, #0x80
            0x320707E1,   # mov  w1, #0x6000000            <- base
            0x2A1403E2,   # mov  w2, w20                   size
            0x320707F3,   # mov  w19, #0x6000000           <- base, returned
            0x940033A6,   # bl   #0x10fb440                map_region
        ],
        'fields': {2: ('guest32', 1, 'virt'), 4: ('guest32', 19, 'virt')},
    },
]

# The five inlined copies of the lazy arena init. Same shape, different
# scratch registers; `rd` and the neighbouring stores are what identify them.
_ARENA = [
    (0x10FB4C0, 21, 26, 0x10),
    (0x10FCC68, 22, 20, 0x10),
    (0x10FCE34, 27, 20, 0x10),
    (0x10FCF90, 20, 22, 0x10),
    (0x10FD358, 21, 22, 0x10),
]

for _i, (_va, _rd, _rb, _off) in enumerate(_ARENA):
    SITES.append({
        'name': 'host arena size (inline copy %d of 5)' % (_i + 1),
        'va': _va,
        'words': [
            movz32(_rd, STOCK_ARENA >> 16, 16),          # mov w?, #0x5000000
            0xAA0003E0 | (_rd << 16),                    # mov x0, x?
            0xB9000000 | ((_off // 4) << 10) | (_rb << 5) | _rd,   # str total
        ],
        'fields': {0: ('movzhi', _rd, 'arena')},
    })


# --------------------------------------------------------------- encoding
def encode(kind: str, rd: int, what: str, heap: int) -> int:
    if kind == 'nop':
        return 0xD503201F if NO_HEAP_DUMP else 0x97FFFF63
    if kind == 'desc64':
        w = orr_xzr64(rd, (heap << 32) | heap)
    elif kind == 'blockhi':
        w = movk32(rd, ((heap - DESC_GAP) >> 16) & 0xFFFF, 16)
    elif kind == 'guest32':
        v = virt_base(heap)
        w = orr_xzr32(rd, v) or movz32(rd, v >> 16, 16)
    elif kind == 'movzhi':
        w = movz32(rd, arena_bytes(heap) >> 16, 16)
    else:
        raise ValueError('unknown kind %r' % kind)
    if w is None:
        raise ValueError('%s (%s) is not encodable at %d MB'
                         % (what, kind, heap // MB))
    return w


def _img(main):
    if isinstance(main, (bytes, bytearray)):
        return main
    import nxmap
    return nxmap.Main(str(main)).img


def verify_sites(main) -> list[str]:
    """Complaints about the module. Empty means it is ours to patch.

    Every word of every signature must be the STOCK word. This module is
    deliberately not idempotent-tolerant: re-running it over an already
    raised heap fails loudly rather than compounding.
    """
    img = _img(main)
    bad = []
    for site in SITES:
        for i, expect in enumerate(site['words']):
            va = site['va'] + 4 * i
            if va + 4 > len(img):
                bad.append('+0x%07X is past the end of the module' % va)
                continue
            have = struct.unpack_from('<I', img, va)[0]
            if have != expect:
                bad.append('+0x%07X holds %08X, expected the stock %08X -- '
                           '%s does not match this module'
                           % (va, have, expect, site['name']))
    return bad


def read_mb(main) -> int | None:
    """The heap size the module is set to, or None if it is not decodable."""
    img = _img(main)
    lo = struct.unpack_from('<I', img, 0x10EE7BC)[0]
    hi = struct.unpack_from('<I', img, 0x10EE7C0)[0]
    if lo != 0x529F0414 or (hi & 0xFFE0001F) != (0x72A00000 | 20):
        return None
    block = (((hi >> 5) & 0xFFFF) << 16) | 0xF820
    heap = block + DESC_GAP
    return heap // MB if heap % MB == 0 else None


def patches(img, mb: int = None) -> list[dict]:
    """The nso_patcher patch list, or [] when there is nothing to do."""
    mb = HEAP_MB if mb is None else mb
    if mb != STOCK_MB:
        why = encodable(mb)
        if why:
            raise ValueError('heap %d MB: %s' % (mb, why))
    heap = mb * MB
    out = []
    for site in SITES:
        for i, (kind, rd, what) in sorted(site['fields'].items()):
            # The heap-dump NOP is independent of the size: an abort that can
            # only ever abort is worth removing at 64 MB too.
            if kind != 'nop' and mb == STOCK_MB:
                continue
            va = site['va'] + 4 * i
            cur = struct.unpack_from('<I', img, va)[0]
            new = encode(kind, rd, what, heap)
            if cur == new:
                continue
            out.append({'name': '%s @ +0x%07X (%s)' % (what, va, site['name']),
                        'va': va, 'expect': _hex(cur), 'set': _hex(new)})
    return out


def spec(img, mb: int = None) -> dict | None:
    ps = patches(img, mb)
    if not ps:
        return None
    mb = HEAP_MB if mb is None else mb
    return {'name': 'FF7 guest heap %d MB (arena %.1f MB)'
                    % (mb, arena_bytes(mb * MB) / float(MB)),
            'patches': ps}


def report(mb: int = None, log=print) -> None:
    mb = HEAP_MB if mb is None else mb
    heap = mb * MB
    log('  guest heap  0x%08X .. 0x%08X   %d MB  (stock %d MB)'
        % (GUEST_BASE, GUEST_BASE + heap, mb, STOCK_MB))
    log('  <virt> base 0x%08X                   (stock 0x06000000)'
        % virt_base(heap))
    log('  host arena  %.1f MB one malloc         (stock %.1f MB)'
        % (arena_bytes(heap) / float(MB), STOCK_ARENA / float(MB)))
    log('  headroom over stock: %d MB more heap for field background pages'
        % ((heap - STOCK_HEAP) // MB))


def apply_to_nso(src, dest, log=lambda *_: None, mb: int = None) -> bool:
    """Patch `main` at `src` -> `dest`. Nothing is written on failure."""
    mb = HEAP_MB if mb is None else mb
    if mb == STOCK_MB and not NO_HEAP_DUMP:
        return False
    try:
        import nso_patcher
    except ImportError as exc:
        log('! heap: cannot import nso_patcher (%s)' % exc)
        return False
    # Already exactly as we want it. Not a failure and not ten errors about
    # words that are "wrong" because they are already right -- this is what
    # a second pass over a module an earlier run patched looks like.
    if not patches(_img(src), mb):
        log('  already at %d MB%s; nothing to write'
            % (mb, ', heap dump already removed' if NO_HEAP_DUMP else ''))
        report(mb, log)
        return False
    bad = verify_sites(src)
    if bad:
        for line in bad:
            log('! heap: ' + line)
        log('  nothing was written; the module is unchanged')
        return False
    try:
        from pathlib import Path as _P
        nso = nso_patcher.read_nso(_P(str(src)))
        s = spec(_img(src), mb)
        if s is None:
            return False
        applied = nso_patcher.apply_spec(nso, s)
        data = nso_patcher.rebuild(nso)
    except Exception as exc:                                   # noqa: BLE001
        log('! heap: %s' % exc)
        log('  nothing was written; the module is unchanged')
        return False
    os.makedirs(os.path.dirname(os.path.abspath(str(dest))), exist_ok=True)
    with open(str(dest), 'wb') as f:
        f.write(data)
    for line in applied:
        log('  ' + line)
    report(mb, log)
    return True


# -------------------------------------------------------------- selftest
def selftest(log=print) -> bool:
    """Re-encode every stock word this module claims to understand.

    If the encoder cannot reproduce the bytes that are already in the
    binary, it does not get to write new ones.
    """
    ok = True
    checks = [
        ('mov x8, #0x0400000004000000', 0xB20603E8,
         orr_xzr64(8, (STOCK_HEAP << 32) | STOCK_HEAP)),
        ('mov w1,  #0x2000000', 0x320703E1, orr_xzr32(1, 0x02000000)),
        ('mov w1,  #0x6000000', 0x320707E1, orr_xzr32(1, 0x06000000)),
        ('mov w19, #0x6000000', 0x320707F3, orr_xzr32(19, 0x06000000)),
        ('mov w20, #0xf820', 0x529F0414, movz32(20, 0xF820)),
        ('movk w20, #0x3ff, lsl #16', 0x72A07FF4,
         movk32(20, (STOCK_HEAP - DESC_GAP) >> 16, 16)),
        ('mov w21, #0x5000000', 0x52A0A015, movz32(21, STOCK_ARENA >> 16, 16)),
    ]
    for label, want, got in checks:
        good = (got == want)
        ok = ok and good
        log('  %-30s want %08X  got %s  %s'
            % (label, want, ('%08X' % got) if got is not None else 'None',
               'ok' if good else 'MISMATCH'))
    # the stock arena accounting must add up to the stock arena constant
    total = region_span(STOCK_HEAP) + EXE_SPAN + STACK_SPAN + ARENA_SPARE
    good = (total == STOCK_ARENA)
    ok = ok and good
    log('  %-30s want %08X  got %08X  %s'
        % ('stock arena accounting', STOCK_ARENA, total,
           'ok' if good else 'MISMATCH'))
    return ok


# ------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('nso', nargs='?', help='exefs/main (stock or patched)')
    ap.add_argument('--out')
    ap.add_argument('--mb', type=int, default=HEAP_MB)
    ap.add_argument('--stock', action='store_true',
                    help='report what the module currently holds and exit')
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args(argv)

    if a.selftest or not a.nso:
        print('== encoder selftest')
        ok = selftest()
        print('== sizes this module can write:',
              ', '.join('%d' % m for m in sizes()), 'MB')
        return 0 if ok else 1

    if not selftest(lambda *_: None):
        print('! encoder selftest FAILED -- refusing to write anything')
        selftest()
        return 1

    have = read_mb(a.nso)
    print('module holds: %s'
          % ('%d MB' % have if have else 'an unrecognised heap size'))
    bad = verify_sites(a.nso)
    for line in bad:
        print('! ' + line)
    if a.show or a.stock:
        img = _img(a.nso)
        for p in (patches(img, a.mb) if not bad else []):
            print('  %-60s %s -> %s' % (p['name'], p['expect'], p['set']))
        report(a.mb)
        return 0 if not bad else 1
    if bad:
        print('  nothing was written')
        return 1
    if not a.out:
        print('  --out is required to write a patched module')
        return 1
    return 0 if apply_to_nso(a.nso, a.out, print, a.mb) else 1


if __name__ == '__main__':
    raise SystemExit(main())
