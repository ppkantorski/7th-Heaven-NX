#!/usr/bin/env python3
"""
ff7nx_audio_cave.py -- the assembler layer and the module addresses that every
Cosmo Memory audio bridge shares.

WHY THIS FILE EXISTS
====================
The five audio bridges (`ff7nx_ambient`, `ff7nx_sfxshuffle`, `ff7nx_sfxbattle`,
`ff7nx_worldsteps`, `ff7nx_fieldsteps`) were developed one probe at a time, and
each grew its own private copy of the same six helpers -- `_bss_ptr`,
`_mov32`, `_stp_off`, `_ldp_off`, the label assembler, the NSO
decompress/recompress pair. Five copies of a register-discipline helper is
five places for them to drift apart, and the whole safety case for these caves
rests on that discipline being identical everywhere. So there is one copy, and
it lives here.

This mirrors what `ff7nx_analog_cave.py` already does for the 360-movement
cave: a small label-resolving assembler plus the encodings a64.py does not
carry, kept next to the caves that use them rather than pushed into the shared
encoder.

THE REGISTER DISCIPLINE, WHICH IS THE SAFETY CASE
=================================================
Every hook here is an unconditional `b` spliced into code the RECOMPILER
generated, not a function call into code we wrote. Whatever the surrounding
translated block happens to hold in a given register at that exact PC is not
something we can see statically. Two rules follow, and every cave in this
family obeys both:

  * anything that must survive a call to the guest address translator
    (`GUEST_TRANSLATE`) lives in x19..x28, and the cave saves and restores
    those itself -- `_save_host`/`_restore_host` below;
  * a cave that displaces a stock instruction replays that instruction and
    restores every register the stock continuation still reads.

Both rules were learned the expensive way, on hardware, and the failures are
recorded in README-35 rather than quietly fixed:

  * the Barret loader probe used `W20` inside `sfx_load` without restoring the
    enclosing `X20`, which the stock loader reads after the hook -- it aborted
    in the native audio path, twice, in either attack order;
  * every field-footstep flag cave from r1 to r5 reused `W8` at the inner
    hook, where the preceding stock `ldp` leaves host `W8`/`W9` live and its
    immediate continuation consumes them to rebuild guest ESP. r6 snapshots
    and restores the pair before replaying the displaced load, and that is the
    version that passed the full field/transition/battle/world regression.

WHAT IS DELIBERATELY NOT HERE
=============================
Table placement. Contiguous read-only data is the scarcest resource in this
module -- see `ff7nx_tables.py` for the measured budget and why -- and it is
allocated in one place so the build can report one honest budget line instead
of each bridge discovering independently that it ran out.
"""
import hashlib
import struct
import sys

try:
    import lz4.block
except ImportError:                                          # pragma: no cover
    sys.exit('need lz4:  pip install lz4 --break-system-packages')

import a64 as A


# ---------------------------------------------------------------- addresses
# Every address below is a module offset into the 1.0.3 `exefs/main` whose
# build ID is 8CAAD5A4E142D2B8EBC1811B5AF05125. Each one is verified against
# the instruction it is expected to displace before anything is written, so a
# different module -- or one where another feature already claimed the site --
# refuses the patch rather than corrupting it.

# The recompiler's guest->host address translator. Modelled, correctly, as
# destroying x0..x18, x30 and the flags.
GUEST_TRANSLATE = 0x10FC3A0

# `sfx_load(sound_id, output)`, immediately after it has read and decremented
# its first argument. Every archive-backed SFX reaches this point before the
# metadata/cache lookup, which is what makes it the shared descriptor hook.
LOAD_HOOK = 0xE6FB28
LOAD_ORIG = 0x51000513                  # sub w19, w8, #1
LOAD_RESUME = LOAD_HOOK + 4

# The instruction after it, which is a second, independent hook site. A bridge
# that needs to compose with one already installed at LOAD_HOOK takes this one
# instead of trying to share the first.
PROXY_LOAD_HOOK = LOAD_HOOK + 4
PROXY_LOAD_ORIG = 0xB9401688            # ldr w8, [x20, #0x14]
PROXY_LOAD_RESUME = PROXY_LOAD_HOOK + 4

# `play_sfx_on_channel` (x86 0x745160), at the point it reads the requested id
# -- BEFORE its cache comparison and before it calls sfx_load. This is the
# ownership boundary FFNx resolves `battle_char_*`/`battle_enemy_*` at, and
# the reason loader-time remapping had to be retired: the native player
# compares and records the LOGICAL id before calling the loader, so a physical
# slot chosen in the loader leaves the channel labelled with the logical id and
# the next request for that id reuses the wrong buffer without entering the
# loader at all.
#
# THREE instructions are displaced here, not one -- `add w0, w8, #0xc`, the
# `bl` to the translator, and the `ldr w8, [x0]` that consumes it -- so the
# cave replays the whole guest-argument lookup and resumes at +0xEF2B88.
PLAYER_HOOK = 0xEF2B7C
PLAYER_ORIG = 0x11003100                # add w0, w8, #0xc
PLAYER_RESUME = PLAYER_HOOK + 12        # 0xEF2B88, `mov w19, #0x1c`

# `audio_meta[slot]` in the recompiled guest's address space. A descriptor is
# (payload byte length, audio.dat byte offset); slot numbers are zero based,
# as LOAD_HOOK has already converted them.
META_GUEST = 0xDE0E84
META_STRIDE = 0x1C
CACHE_GUEST = 0xDE0128

# Physical archive rows that are empty in the vanilla 1.0.3 archive and sit at
# its reserved high end, so they are inside the fixed 750 records and no game
# route ever asks for them. Reserving one gives a bridge a permanent cache
# identity without a synthetic id -- the mechanism that finally worked after
# virtual ids above 750 crashed on every variant tried.
FOOTSTEP_SLOT = 744                     # world and field steps share this row
BARRET_SLOTS = (731, 732, 733, 734, 735, 736)


def meta_guest(slot):
    """Guest address of one archive row's (length, offset) descriptor."""
    return META_GUEST + (slot - 1) * META_STRIDE


def cache_guest(slot):
    """Guest address of one archive row's cache word."""
    return CACHE_GUEST + (slot - 1) * 4


# ----------------------------------------------------------------- assembler
class Asm:
    """
    Emit words with symbolic labels, against the REAL scattered cave layout.

    `at` is the address of word 0 and `addr` maps a word INDEX to its address.
    That second argument is the whole point: `ff7nx_cave.emit_laid_out` cuts a
    cave into two- and three-word runs spread over 18 MB of .text, and a
    branch resolved against a pretend contiguous block would land in the
    middle of an unrelated function. a64's branch encoders range-check every
    displacement, so a cave whose window was still too wide fails the build
    instead of encoding a wrapped branch.
    """

    def __init__(self, at, addr):
        self.at = at
        self.addr = addr
        self.words = []
        self.labels = {}
        self.fixups = []

    def pc(self):
        return self.addr(len(self.words))

    def emit(self, word):
        self.words.append(word)

    def label(self, name):
        self.labels[name] = self.pc()

    def b(self, name):
        self.fixups.append((len(self.words), name, 'b'))
        self.words.append(0)

    def cbz64(self, reg, name):
        self.fixups.append((len(self.words), name, 'cbz64', reg))
        self.words.append(0)

    def cbz(self, reg, name):
        self.fixups.append((len(self.words), name, 'cbz', reg))
        self.words.append(0)

    def cbnz(self, reg, name):
        self.fixups.append((len(self.words), name, 'cbnz', reg))
        self.words.append(0)

    def bcond(self, name, cond):
        self.fixups.append((len(self.words), name, 'bcond', cond))
        self.words.append(0)

    def resolve(self):
        for item in self.fixups:
            idx, name, kind = item[:3]
            here, target = self.addr(idx), self.labels[name]
            if kind == 'b':
                self.words[idx] = A.b(here, target)
            elif kind == 'bcond':
                self.words[idx] = A.bcond(here, target, item[3])
            elif kind == 'cbz':
                self.words[idx] = A.cbz(item[3], here, target)
            elif kind == 'cbnz':
                self.words[idx] = A.cbnz(item[3], here, target)
            else:
                self.words[idx] = A.cbz64(item[3], here, target)
        return self.words


# ------------------------------------------------------------ extra encoders
# Forms a64.py does not carry. Each is round-tripped through capstone by
# tests/test_audio_cave.py, for the same reason a64.py's own forms are: an
# encoding that looks plausible in hex and decodes to the wrong register
# produces a structurally valid module that behaves incorrectly, and only a
# second, independent decoder catches that.
def stp_off(a, b, off):
    """STP Xa, Xb, [SP, #off] -- scaled, unsigned offset form."""
    if off % 8 or not 0 <= off < 512 * 8:
        raise ValueError('stp offset %d invalid' % off)
    return 0xA9000000 | ((off // 8) << 15) | (b << 10) | (31 << 5) | a


def ldp_off(a, b, off):
    """LDP Xa, Xb, [SP, #off] -- scaled, unsigned offset form."""
    if off % 8 or not 0 <= off < 512 * 8:
        raise ValueError('ldp offset %d invalid' % off)
    return 0xA9400000 | ((off // 8) << 15) | (b << 10) | (31 << 5) | a


def mov64(dst, src):
    """MOV Xdst, Xsrc -- never truncate a host pointer to 32 bits."""
    return 0xAA0003E0 | (src << 16) | dst


def ldrb_uxtw(dst, base, index):
    """LDRB Wdst, [Xbase, Windex, UXTW] -- the byte-index table form."""
    return 0x38604800 | (index << 16) | (base << 5) | dst


def ldrh_uxtw_scaled(dst, base, index):
    """LDRH Wdst, [Xbase, Windex, UXTW #1] -- the u16 pool form."""
    return 0x78605800 | (index << 16) | (base << 5) | dst


def ldr_uxtw_scaled(dst, base, index):
    """LDR Wdst,[Xbase,Windex,UXTW #2] -- a u32 pointer-table entry."""
    return 0xB8605800 | (index << 16) | (base << 5) | dst


def add_x_uxtw(dst, base, index):
    """ADD Xdst, Xbase, Windex, UXTW -- the descriptor-index form."""
    return 0x8B204000 | (index << 16) | (base << 5) | dst


def udiv(dst, left, right):
    """UDIV Wdst, Wleft, Wright."""
    return 0x1AC00800 | (right << 16) | (left << 5) | dst


def msub(dst, left, right, addend):
    """MSUB Wdst, Wleft, Wright, Waddend -- Wdst = Waddend - Wleft*Wright."""
    return 0x1B008000 | (right << 16) | (addend << 10) | (left << 5) | dst


# ------------------------------------------------------------ emit sequences
def mov32(a, reg, value):
    """A full 32-bit constant, in the two words movz/movk always take."""
    a.emit(A.movz(reg, value & 0xFFFF))
    a.emit(A.movk_hi(reg, (value >> 16) & 0xFFFF))


def bss_ptr(a, dst, address):
    """Materialise a module BSS address into Xdst (adrp + add)."""
    a.emit(A.adrp(dst, a.pc(), address & ~0xFFF))
    a.emit(A.add_imm64(dst, dst, address & 0xFFF))


def guest_ptr(a, guest):
    """
    Translate guest address `guest` to a host pointer, returned in x0.

    Clobbers x0..x18, x30 and the flags -- that is the translator's contract,
    not a conservative guess. Callers keep everything that must survive in
    x19..x28, which `_save_host` has already preserved for them.
    """
    mov32(a, 0, guest)
    a.emit(A.bl(a.pc(), GUEST_TRANSLATE))


def save_host(a, frame=0x80):
    """
    Save x19..x30 into a new stack frame.

    16 bytes at +0x60 are deliberately left free for the recompiler's adjacent
    condition metadata, which the field playback cave snapshots. Putting that
    snapshot at +0x00/+0x08 instead would overwrite saved X19/X20 before
    `restore_host` reads them -- which is what an earlier revision did.
    """
    if frame < 0x70 or frame % 16:
        raise ValueError('host frame %#x is too small or misaligned' % frame)
    a.emit(A.stp64_pre(19, 20, 31, -frame))
    a.emit(stp_off(21, 22, 0x10))
    a.emit(stp_off(23, 24, 0x20))
    a.emit(stp_off(25, 26, 0x30))
    a.emit(stp_off(27, 28, 0x40))
    a.emit(stp_off(29, 30, 0x50))


def restore_host(a, frame=0x80):
    """The exact inverse of `save_host`, in the reverse order."""
    a.emit(ldp_off(29, 30, 0x50))
    a.emit(ldp_off(27, 28, 0x40))
    a.emit(ldp_off(25, 26, 0x30))
    a.emit(ldp_off(23, 24, 0x20))
    a.emit(ldp_off(21, 22, 0x10))
    a.emit(A.ldp64_post(19, 20, 31, frame))


# ------------------------------------------------------------------- the NSO
def segments(blob):
    """(segment headers, decompressed [text, rodata, data]) from an NSO0."""
    if blob[:4] != b'NSO0':
        raise ValueError('not an NSO0 module')
    segs = [struct.unpack_from('<III', blob, off) for off in (0x10, 0x20, 0x30)]
    comp = struct.unpack_from('<III', blob, 0x60)
    flags, = struct.unpack_from('<I', blob, 0x0C)
    raw = []
    for i, (file_off, _mem_off, size) in enumerate(segs):
        part = blob[file_off:file_off + comp[i]]
        data = (lz4.block.decompress(part, uncompressed_size=size)
                if flags & (1 << i) else part[:size])
        if len(data) != size:
            raise ValueError('segment %d decompressed to %d bytes, header '
                             'says %d' % (i, len(data), size))
        want = blob[0xA0 + 0x20 * i:0xC0 + 0x20 * i]
        if hashlib.sha256(data).digest() != want:
            raise ValueError('segment %d hash does not match the NSO header'
                             % i)
        raw.append(data)
    return segs, raw


def pack(blob, raw, bss_growth):
    """
    Rebuild an NSO0 from `raw`, growing bssSize by `bss_growth` bytes.

    WHY THIS EXISTS ALONGSIDE nso_patcher.rebuild
    ---------------------------------------------
    `nso_patcher.rebuild` deliberately refuses a size change -- it is the
    verified-byte in-place patcher, and "the segment size changed" is exactly
    the corruption it is there to catch. These bridges genuinely do change
    sizes: they append route tables to a segment tail and they grow BSS for
    their per-route cursors. So they use this, which is the same repack
    `ff7nx_60fps` has always used for its own caves.

    BSS GROWTH COMPOSES BY CHAINING, NOT BY COORDINATION
    ----------------------------------------------------
    Each pass reads the bssSize of the module IT IS PATCHING and adds to it,
    and each pass computes its own scratch base as `page_align(data_end) +
    that bssSize`. Because every pass runs on the previous pass's output, the
    Nth pass's scratch necessarily begins after the (N-1)th pass's. No shared
    allocator and no agreed offsets are needed, and a pass that is skipped
    simply does not appear in the chain. The one rule this depends on is the
    one every module pass in this project already follows: whoever edits
    `exefs/main` last has to see what everyone else wrote.
    """
    out = bytearray(blob[:0x100])
    body, file_off = b'', 0x100
    for i, part in enumerate(raw):
        compressed = lz4.block.compress(part, mode='high_compression',
                                        compression=12, store_size=False)
        struct.pack_into('<I', out, 0x10 + 16 * i, file_off)
        struct.pack_into('<I', out, 0x18 + 16 * i, len(part))
        struct.pack_into('<I', out, 0x60 + 4 * i, len(compressed))
        out[0xA0 + 32 * i:0xC0 + 32 * i] = hashlib.sha256(part).digest()
        body += compressed
        file_off += len(compressed)
    old_bss, = struct.unpack_from('<I', out, 0x3C)
    struct.pack_into('<I', out, 0x3C, old_bss + bss_growth)
    return bytes(out) + body


def scratch_base(blob, segs):
    """
    Where this pass's BSS scratch starts in the module it is patching.

    BSS begins at the PAGE-ALIGNED end of .data, not its raw end. Using the
    raw end put the 60 FPS camera counters 0x328 bytes inside live BSS and
    crashed on entering battle -- that is measured, not theoretical, and it is
    why this is a function rather than an expression repeated in six files.
    """
    data_end = (segs[2][1] + segs[2][2] + 0xFFF) & ~0xFFF
    old_bss, = struct.unpack_from('<I', blob, 0x3C)
    return data_end + old_bss


def read_word(segs, raw, va):
    """
    The u32 at module offset `va`, from whichever segment actually holds it.

    Tables are placed in the .rodata tail or the .text tail depending on what
    is free (see `ff7nx_tables`), so a post-repack verification that reads
    them out of `raw[0]` unconditionally is wrong half the time -- and wrong
    in the direction that reports success when the table is not there.
    """
    for (_file_off, mem_off, size), blob in zip(segs, raw):
        if mem_off <= va and va + 4 <= mem_off + len(blob):
            return struct.unpack_from('<I', blob, va - mem_off)[0]
    raise ValueError('module offset %#x is outside every segment' % va)


def expect_word(text, va, want, what):
    """
    Refuse to patch unless `va` still holds the instruction we expect.

    This is the single check that makes these passes composable: if another
    feature already claimed a hook site, or the input is not this exact
    module, the build stops here with the site named rather than writing a
    branch over somebody else's cave.
    """
    current, = struct.unpack_from('<I', text, va)
    if current != want:
        raise ValueError(
            '%s hook +0x%X holds %08X, expected %08X -- either this is not '
            'the 1.0.3 module this bridge was derived against, or another '
            'feature already owns that instruction' % (what, va, current, want))
    return current
