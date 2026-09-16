#!/usr/bin/env python3
"""
ff7nx_sfxshuffle.py -- FFNx sequential/shuffle SFX mappings, on the stock
loader, without adding a single `audio.fmt` record.

THE PROBLEM
===========
Cosmo Memory's `config.toml` gives many sounds a rotating set:

    [563]
    sequential = [ 2108, 2109, 2110, 2111, 2112 ]

FFNx alternates through those so a repeated effect does not sound identical
every time. It can do that because it owns its own SFX playback layer. This
port does not: `audio.fmt` is exactly 750 records, every caller indexes it, and
that number is not negotiable.

On the real mod, 720 rows are mapped but only 58 have more than one variant --
258 payloads in total. So the archive keeps the first variant of each route in
its ordinary slot (which is what `sfxmod.rebuild` already wrote), the other 200
payloads are appended AFTER the archive in `audio.dat`, and this bridge picks
one at load time by rewriting that row's descriptor.

WHAT WAS TRIED FIRST AND WHY IT IS NOT THIS
===========================================
Three approaches were built, run on hardware, and retired. They are recorded
because each one looks reasonable until you try it:

  * VIRTUAL IDS ABOVE 750. Three variants, including a native-memory-tail
    version. All crashed on the first Cloud attack, all reporting the same
    caller continuation. Expanding the loader's own bounds is not enough --
    callers consume the id through fixed-size tables too.
  * RAW CACHE INVALIDATION. Rewriting the live descriptor and zeroing the
    cache word froze or crashed. The original PC code shows why: `sfx_unload`
    releases the cached DirectSound object BEFORE clearing `audio_cache[id]`,
    so a raw zero leaks a live renderer-owned object.
  * A FULL DESCRIPTOR TABLE. Storing every `(length, offset)` pair needs 2,296
    bytes of contiguous module data. There are 1,835 bytes for everything
    (see `ff7nx_tables`), so it did not fit alongside the 60 FPS and visual
    caves at all.

WHAT THIS DOES INSTEAD
======================
Hook the shared loader entry, `sfx_load` after it has read and decremented its
first argument -- the point every archive-backed SFX passes through, before
the metadata and cache lookup. For a mapped row, select a variant, and change
ONLY the `(length, offset)` descriptor the stock cache/player path is about to
read. The stock game then creates, owns, pans, pitches, stops and collects the
buffer exactly as it always did.

This is deliberately not a replacement battle audio engine. It is a small
selector in front of the proven stock one.

HARDWARE STATUS
===============
The descriptor-selection mechanism passed on Switch (`sfx-direct-loader-probe`
r2: repeated Cloud attacks rotated cleanly, no stutter, no freeze), and the
full 58-route package (`sfx-shuffle-hardware-r1`) was built and audited from
the real `[Tsunamods]_Cosmo_Memory.iro`.

It is safe for call sites that pass a channel-owned output pointer, which is
what a one-shot effect does. CACHE-OWNING callers are a different category and
are NOT handled here -- `ff7nx_sfxbattle` resolves those at the native player,
before its cache check, because a descriptor swapped at the loader leaves the
channel still labelled with the logical id.

THE ROUTE WORD
==============
Eight bytes per route, two words:

    word 0   slot index (10 bits) | count (3) | padded byte stride (19)
    word 1   absolute `audio.dat` byte offset of this route's payload base

Keeping the stride ROUTE-LOCAL is not a micro-optimisation. An earlier version
used one archive-wide stride, and a single long sequential effect then selected
a 289,792-byte stride and padded all 200 extras to 55.3 MiB. Per-route stride
pads each sequence only to its own longest member: about 7.6 MiB for the same
set.

Element zero of every route is the primary descriptor already sitting in the
normal archive row, so that turn leaves the stock loader completely untouched.
"""
import os
import struct

import a64 as A
import ff7nx_audio_cave as AC
import ff7nx_cave
import ff7nx_tables
import nxmap
from ff7nx_audio_cave import Asm

# Route-word field widths. Checked before packing rather than masked into
# silence: a slot or stride that does not fit is a build bug, and a wrapped
# field would select a real descriptor belonging to an unrelated sound.
SLOT_BITS = 10
COUNT_BITS = 3
STRIDE_BITS = 19
MAX_VARIANTS = (1 << COUNT_BITS) - 1            # 7


def route_word(slot_index, count, stride):
    """Pack one route, with every field range-checked first."""
    if not 0 <= slot_index < (1 << SLOT_BITS):
        raise ValueError('SFX slot index %d does not fit the route word'
                         % slot_index)
    if not 2 <= count <= MAX_VARIANTS:
        raise ValueError('SFX route count %d is outside 2..%d'
                         % (count, MAX_VARIANTS))
    if not 0 < stride < (1 << STRIDE_BITS):
        raise ValueError('SFX route stride %d does not fit the route word'
                         % stride)
    return slot_index | (count << SLOT_BITS) | (stride << (SLOT_BITS +
                                                           COUNT_BITS))


def normalise(plan):
    """
    Validate `build.py`'s route plan into [(slot_index, count, base, stride)].

    `plan` is {'compact': True, 'routes': {sound_id: {count, payload_base,
    stride}}} -- the shape `_emplace_sfx` records after it has appended the
    extra payloads and therefore knows their final offsets.
    """
    if not isinstance(plan, dict) or not plan.get('compact'):
        raise ValueError('sequential SFX route plan is not in compact form')
    try:
        source = plan['routes']
    except (KeyError, TypeError) as exc:
        raise ValueError('malformed sequential SFX route plan') from exc
    out = []
    for sound_id, item in sorted(source.items()):
        try:
            sound_id = int(sound_id)
            count = int(item['count'])
            payload_base = int(item['payload_base'])
            stride = int(item['stride'])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('malformed sequential SFX route %r'
                             % (sound_id,)) from exc
        if not 1 <= sound_id <= 750:
            raise ValueError('SFX ID %r is outside the 750-slot archive'
                             % (sound_id,))
        if not 0 <= payload_base <= 0xFFFFFFFF:
            raise ValueError('SFX ID %d has an invalid payload base'
                             % sound_id)
        route_word(sound_id - 1, count, stride)      # range check
        out.append((sound_id - 1, count, payload_base, stride))
    return out


def table_bytes(routes):
    """The route table exactly as the cave will read it."""
    return b''.join(struct.pack('<II', route_word(slot, count, stride), base)
                    for slot, count, base, stride in routes)


def build_cave(cave, address, scratch, route_va, route_count):
    """
    The selector. Linear scan of a sparse route table, then the stock loader.

    REGISTER DISCIPLINE. x20..x25 are saved and restored around everything,
    because the guest address translator called near the end destroys
    x0..x18/x30 and the stock loader continues to read its own x20 after the
    hook. An earlier revision used W20 without preserving the enclosing X20
    and aborted in the native audio path on hardware, in either attack order.
    """
    a = Asm(cave, address)
    a.emit(AC.LOAD_ORIG)                # replay: w19 = requested SFX ID - 1
    a.emit(A.stp64_pre(20, 21, 31, -0x30))
    a.emit(AC.stp_off(22, 23, 0x10))
    a.emit(AC.stp_off(24, 25, 0x20))

    AC.bss_ptr(a, 20, scratch)
    a.emit(A.add_imm64(25, 20, 0))      # x25 walks one cursor per route
    a.emit(A.adrp(21, a.pc(), route_va & ~0xFFF))
    a.emit(A.add_imm64(21, 21, route_va & 0xFFF))
    a.emit(A.movz(24, route_count))

    a.label('scan')
    a.emit(A.ldr(23, 21, 0))
    a.emit(A.and_mask(8, 23, SLOT_BITS))
    a.emit(A.cmp_reg(8, 19))
    a.bcond('found', A.EQ)
    a.emit(A.add_imm64(21, 21, 8))
    a.emit(A.add_imm64(25, 25, 4))
    a.emit(A.sub_imm(24, 24, 1))
    a.emit(A.cmp_imm(24, 0))
    a.bcond('out', A.EQ)
    a.b('scan')

    a.label('found')
    a.emit(A.lsr(8, 23, SLOT_BITS))
    a.emit(A.and_mask(8, 8, COUNT_BITS))
    a.emit(A.ldr(24, 25, 0))
    a.emit(A.cmp_reg(24, 8))
    a.bcond('counter_ok', A.LT)
    a.emit(A.movz(24, 0))
    a.label('counter_ok')
    a.emit(A.mov_reg(9, 24))            # the element this turn selects
    a.emit(A.add_imm(24, 24, 1))
    a.emit(A.cmp_reg(24, 8))
    a.bcond('save_counter', A.LT)
    a.emit(A.movz(24, 0))
    a.label('save_counter')
    a.emit(A.str_(24, 25, 0))
    # Element zero is the primary descriptor already installed in its normal
    # archive row: leave the stock loader entirely alone for that turn.
    a.emit(A.cmp_imm(9, 0))
    a.bcond('out', A.EQ)
    a.emit(A.sub_imm(9, 9, 1))
    a.emit(A.lsr(22, 23, SLOT_BITS + COUNT_BITS))    # this route's stride
    a.emit(A.mul(9, 9, 22))
    a.emit(A.ldr(23, 21, 4))            # this route's absolute payload base
    a.emit(A.add_reg(23, 23, 9))

    # Replace only the descriptor the stock cache/player path is about to
    # read. `audio_meta + slot * 0x1C`, computed as (slot << 5) - (slot << 2).
    a.emit(A.lsl(8, 19, 5))
    a.emit(A.lsl(9, 19, 2))
    a.emit(A.sub_reg(8, 8, 9))
    a.emit(A.movz(0, AC.META_GUEST & 0xFFFF))
    a.emit(A.movk_hi(0, (AC.META_GUEST >> 16) & 0xFFFF))
    a.emit(A.add_reg(0, 0, 8))
    a.emit(A.bl(a.pc(), AC.GUEST_TRANSLATE))
    a.emit(A.str_(22, 0, 0))            # length
    a.emit(A.str_(23, 0, 4))            # offset

    a.label('out')
    a.emit(AC.ldp_off(24, 25, 0x20))
    a.emit(AC.ldp_off(22, 23, 0x10))
    a.emit(A.ldp64_post(20, 21, 31, 0x30))
    a.emit(A.b(a.pc(), AC.LOAD_RESUME))
    return a.resolve()


def apply_to_nso(src, dest, plan, space=None):
    """
    Install the bridge, patching `src` into `dest`.

    `space` is an optional `ff7nx_tables.TableSpace` already opened on the same
    module, so a caller installing several bridges in one pass shares one
    budget. When it is None one is opened here and committed on the way out.
    """
    routes = normalise(plan)
    if not routes:
        return None
    with open(src, 'rb') as handle:
        blob = handle.read()
    segs, raw, own_space = ff7nx_tables.open_module(blob)
    space = own_space if space is None else space
    text = space.text
    AC.expect_word(text, AC.LOAD_HOOK, AC.LOAD_ORIG, 'sequential SFX loader')

    table = table_bytes(routes)
    route_va = space.place('sequential SFX routes', table, align=4)
    scratch = AC.scratch_base(blob, segs)
    bss_bytes = len(routes) * 4                     # one cursor per route

    pool = ff7nx_cave.HolePool(text, starts=set(nxmap.Main(src).arm_starts))
    entry, placed = ff7nx_cave.emit_laid_out(
        pool, lambda cave, address: build_cave(cave, address, scratch,
                                               route_va, len(routes)))
    placed[AC.LOAD_HOOK] = A.b(AC.LOAD_HOOK, entry)
    for where, word in placed.items():
        struct.pack_into('<I', text, where, word)

    out = AC.pack(blob, space.commit(), bss_bytes)

    # Re-parse the finished file exactly as the loader would, rather than
    # trusting the bytes we just assembled: decompress, re-verify every
    # segment hash, and read the hook back out of the recompressed image.
    check_segs, check_raw = AC.segments(out)
    if struct.unpack_from('<I', check_raw[0], AC.LOAD_HOOK)[0] != \
            A.b(AC.LOAD_HOOK, entry):
        raise ValueError('loader hook did not survive the repack')
    # Read the table back through the segment that really holds it: it lands
    # in the .rodata tail or the .text tail depending on what was free, and a
    # check hardcoded to .text would pass vacuously in the other case.
    first = routes[0]
    if AC.read_word(check_segs, check_raw, route_va) != \
            route_word(first[0], first[1], first[3]):
        raise ValueError('route table did not survive the repack')
    if AC.read_word(check_segs, check_raw, route_va + 4) != first[2]:
        raise ValueError('route payload base did not survive the repack')
    old_bss, = struct.unpack_from('<I', blob, 0x3C)
    if struct.unpack_from('<I', out, 0x3C)[0] != old_bss + bss_bytes:
        raise ValueError('BSS did not grow by the cursor table')

    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    with open(dest, 'wb') as handle:
        handle.write(out)
    return {'main': dest,
            'routes': len(routes),
            'variants': sum(count for _s, count, _b, _st in routes),
            'table_bytes': len(table),
            'table_va': route_va,
            'bss_bytes': bss_bytes,
            'cave_entry': entry,
            'cave_words': len(placed) - 1}
