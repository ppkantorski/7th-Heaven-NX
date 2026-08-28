#!/usr/bin/env python3
"""A/B the world-map block neighbourhood in a built sdout, no rebuild.

    python3 ab_world_blocks.py            # show which one is live
    python3 ab_world_blocks.py --wedge    # stock: 3x3 core + a yaw wedge
    python3 ab_world_blocks.py --full     # ours:  the whole 5x5, always

WHY THIS EXISTS
---------------
`world_compute_block_visibility_751AC4` decides which blocks the streamer
asks for. Stock marks the 3x3 core unconditionally and admits each block of
the outer ring only if its CENTRE falls inside a +/-78.75 degree wedge about
the camera heading. We changed the unconditional core from |d| <= 1 to
|d| <= 2, so the whole 5x5 is always requested and the set stops depending
on which way you are facing.

That took the resident working set from roughly 17 blocks to 25.

WHAT THAT DOES AND DOES NOT PRESSURE (verified in ff7.exe, not assumed)
----------------------------------------------------------------------
Streaming requests are keyed by SECTOR GROUP, not by block -- 0x75184B
computes (by>>2)*9 + (bx>>2) -- and a 5-wide span covers at most two
4-aligned group columns, exactly as a 3-wide span does. So both the stock
neighbourhood and ours touch at most 2x2 = 4 groups, and both dedupe against
the pending list (0x751962 over 0xE28CB0) and the loaded list (0x7518F6 over
0xE28C28). The free list built at 0x750549 holds 20 requests. Our change
CANNOT starve it. That matters, because that path is the one with no guard:

    007518A8  cmp   [0xE28B7C], 0        ; request free list
    007518AF  jne   0x7518BB
    007518B1  push  0xF
    007518B3  call  0x74C9A0             ; world_assert -- a NO-OP STUB:
    007518B8  add   esp, 4               ;   push ebp; mov ebp,esp; pop ebp; ret
    007518BB  mov   ecx, [0xE28B7C]
    007518C7  mov   eax, [edx]           ; <- NULL deref

Block DESCRIPTORS are the pool our change does pressure: 32 of them, 0x18
bytes each, built at 0x750645 (i < 0x1F linked, 0x1F terminated) at
0xE04610. 25 of 32 resident where stock sits at 17.

CORRECTED, AND THE CORRECTION MATTERS
-------------------------------------
An earlier version of this note said the allocator "hands back a descriptor
something else still owns", i.e. silent corruption. That was wrong, and
disassembling the whole cycle rather than just the fallback is what showed
it. `world_alloc_block_751E43` DOES clean up before reusing the descriptor:

    00751E50  cmp   [0xE28CCC], 0        ; free head; empty ->
    00751E6E  cmp   [0xE045F8], 0        ; ...take the oldest resident
    00751E88  ...                        ; walk to the last entry
    00751EA9  mov   [edx], 0             ; unlink it
    00751ECB  call  0x761644             ; <- and NULL every reference to
                                         ;    (bx,by) held in the 0xE39A00
                                         ;    list, 8-byte records at +0x60
                                         ;    ..+0x90, before handing it on

So this is ORDINARY LRU EVICTION, not a use-after-free. What our change
actually did is narrow the margin that eviction has to work in, and that is
provable arithmetic rather than a hunch:

  * A descriptor NEVER returns to the free list from the resident state.
    The only refill is `0x75199C`, which ages each PENDING request and
    frees it after 0x96 = 150 frames without a completion. A block that
    loads successfully goes pending -> resident (0x751513..0x751524) and
    stays there until it is evicted.
  * So the free list settles at (32 - working set) and every allocation
    after that evicts the resident TAIL -- the oldest-loaded block, chosen
    with no reference to whether it is still on screen.
  * `world_stream_blocks_75164A` admits with NO eviction pass of its own:
    it clears the visibility table for blocks already resident or pending,
    then allocates one descriptor per remaining wanted cell.
  * A diagonal block-boundary crossing makes 9 new cells wanted at once
    (a new row of 5 plus a new column of 5, less the shared corner).

    stock, 3x3 core + wedge:  ~17 resident, margin 15  >  9   never evicts
                                                            a block that is
                                                            still wanted
    ours,  the whole 5x5:      25 resident, margin  7  <  9   at least two
                                                            evictions per
                                                            diagonal step
                                                            hit blocks that
                                                            ARE still in the
                                                            visible window

Those two are re-requested immediately, which evicts two more, and so on:
a self-sustaining churn while flying diagonally, plus whatever the renderer
does with a block whose references were nulled mid-frame. It is not proof
of the freeze, but it IS a real regression our change introduced and the
only one on the world map with a number behind it.

Flip to --wedge and fly the same route. If the fault follows the setting,
the fix is to keep the working set inside (32 - 9) rather than to guess at
memory.

Flipping back and forth costs one command and no rebuild. Play the same route
on each and see whether the fault follows the setting.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import hashlib
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import types


def ensure_lz4():
    try:
        import lz4.block  # noqa: F401
        return
    except ImportError:
        pass
    library = ctypes.util.find_library('lz4')
    if not library:
        raise SystemExit('need python-lz4 or a system liblz4 installation')
    lib = ctypes.CDLL(library)
    lib.LZ4_decompress_safe.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                                        ctypes.c_int, ctypes.c_int]
    lib.LZ4_decompress_safe.restype = ctypes.c_int
    lib.LZ4_compressBound.argtypes = [ctypes.c_int]
    lib.LZ4_compressBound.restype = ctypes.c_int
    lib.LZ4_compress_default.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                                         ctypes.c_int, ctypes.c_int]
    lib.LZ4_compress_default.restype = ctypes.c_int
    block = types.ModuleType('lz4.block')

    def decompress(data, uncompressed_size):
        out = ctypes.create_string_buffer(uncompressed_size)
        n = lib.LZ4_decompress_safe(data, out, len(data), uncompressed_size)
        if n < 0:
            raise RuntimeError('LZ4 decompression failed (%d)' % n)
        return out.raw[:n]

    def compress(data, store_size=False, **_kw):
        if store_size:
            raise RuntimeError('the NSO fallback requires raw LZ4 blocks')
        cap = lib.LZ4_compressBound(len(data))
        out = ctypes.create_string_buffer(cap)
        n = lib.LZ4_compress_default(data, out, len(data), cap)
        if n <= 0:
            raise RuntimeError('LZ4 compression failed (%d)' % n)
        return out.raw[:n]

    block.decompress = decompress
    block.compress = compress
    pkg = types.ModuleType('lz4')
    pkg.block = block
    sys.modules['lz4'] = pkg
    sys.modules['lz4.block'] = block


ensure_lz4()

import nso_patcher                                             # noqa: E402


HERE = Path(__file__).resolve().parent
TITLE_ID = '0100A5B00BDC6000'
DEFAULT_MAIN = (HERE / 'sdout' / 'atmosphere' / 'contents' / TITLE_ID /
                'exefs' / 'main')

# subs w8, w9, #1  ->  the stock 3x3 core     (|d| <= 1)
# subs w8, w9, #2  ->  the whole 5x5          (|d| <= 2)
SITES = ((0x00F525BC, 'dx'), (0x00F52670, 'dy'))
WEDGE = bytes.fromhex('28050071')     # subs w8, w9, #1
FULL = bytes.fromhex('28090071')      # subs w8, w9, #2


def read(nso, va, n=4):
    seg, off = nso_patcher.segment_for_va(nso, va, n)
    return bytes(seg.data[off:off + n])


def state(nso):
    have = [read(nso, va) for va, _ in SITES]
    if all(w == FULL for w in have):
        return 'full'
    if all(w == WEDGE for w in have):
        return 'wedge'
    return 'mixed'


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--main', type=Path, default=DEFAULT_MAIN)
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--wedge', action='store_true',
                   help='stock: 3x3 core plus the yaw wedge (~17 blocks)')
    g.add_argument('--full', action='store_true',
                   help='ours: the whole 5x5, always (25 blocks)')
    args = ap.parse_args(argv)

    target = args.main.resolve()
    if not target.is_file():
        raise SystemExit('no sdout module: %s' % target)
    nso = nso_patcher.read_nso(target)
    now = state(nso)
    label = {'full': 'FULL 5x5  (25 blocks -- ours)',
             'wedge': 'WEDGE     (~17 blocks -- stock)',
             'mixed': 'MIXED -- the two words disagree, refusing to guess'}[now]
    print('live: %s' % label)
    for va, ax in SITES:
        print('   +0x%07X %s  %s' % (va, ax, read(nso, va).hex(' ')))
    if now == 'mixed':
        return 1
    if not (args.wedge or args.full):
        print('\nnothing changed. Pass --wedge or --full to switch.')
        return 0

    want = 'wedge' if args.wedge else 'full'
    if want == now:
        print('\nalready %s; nothing to do.' % want)
        return 0

    new = WEDGE if want == 'wedge' else FULL
    old = FULL if want == 'wedge' else WEDGE
    try:
        nso_patcher.apply_spec(nso, {
            'name': 'world block neighbourhood -> %s' % want,
            'patches': [{'name': 'world block neighbourhood %s' % ax,
                         'va': va,
                         'expect': old.hex(),
                         'set': new.hex()} for va, ax in SITES],
        })
        rebuilt = nso_patcher.rebuild(nso)
    except Exception as exc:                                   # noqa: BLE001
        raise SystemExit('refused: %s' % exc)

    backup = target.with_name(target.name + '.pre-world-block-ab')
    if not backup.exists():
        shutil.copy2(target, backup)
        print('\nbackup: %s' % backup)

    fd, tmp = tempfile.mkstemp(prefix='.wblk-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(rebuilt)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    check = nso_patcher.read_nso(target)
    if state(check) != want:
        raise SystemExit('post-write check failed; restore %s' % backup)
    print('now:  %s' % want.upper())
    print('sha256: %s' % hashlib.sha256(target.read_bytes()).hexdigest())
    print('\nCopy exefs/main to the card and play the same route as before.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
