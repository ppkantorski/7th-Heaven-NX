#!/usr/bin/env python3
"""
ff7nx_tables.py -- the module's contiguous read-only data budget, measured and
allocated in one place.

WHY A SHARED ALLOCATOR, WHEN CAVES DO NOT NEED ONE
==================================================
Code caves need no coordination: `ff7nx_cave`'s pool re-checks that a padding
hole is still zero in the module being patched, so a hole another pass already
claimed is simply skipped. There are 25,648 usable bytes of it and the audio
bridges together want a few hundred, so code is not the constraint.

DATA is. A lookup table has to be CONTIGUOUS to be indexed, and the biggest
padding hole is 12 bytes. There are exactly two places in this module where
contiguous bytes can be claimed, both of them page-alignment slack that cannot
be enlarged, and unlike holes they give no signal when someone else has
already taken them -- a second pass appending to a tail it has not measured
simply overwrites, or silently runs past the segment into the next one.

So: one measurement, one allocation order, one budget line in the build log.

THE TWO REGIONS, MEASURED ON THE STOCK 1.0.3 MODULE
====================================================
    .text   ends 0x1152660 -> .rodata 0x1153000     2,464 bytes
    .rodata ends 0x126CC38 -> .data   0x126D000       968 bytes

and after the 60 FPS pass with the shipping preset (60 FPS + analog-360 +
no-cheats + no-autorun + movie-fps + movie-poll), measured by running it:

    .text tail    984 free   (the 60 FPS caves and the shared-prologue body)
    .rodata tail  851 free   (36 B shared-prologue descriptors, 81 B analog)
    ------------------------------------------------------------------
    TOTAL       1,835 bytes available to everything that comes after

That is the real number every audio bridge is spending from, and it is small.
`ff7nx_60fps` is the only other consumer of either region -- every later module
pass in this project uses the padding pool -- so the figure above is what the
audio passes actually see, not an optimistic ceiling.

A REGION THAT LOOKS FREE AND IS NOT
===================================
There is a third gap, 808 bytes between .data's end (0x12CECD8) and the page
boundary where BSS begins (0x12CF000), and it is tempting because .data's
declared size could simply be grown to cover it without moving BSS.

Do not use it. Seven R_AARCH64_RELATIVE relocations in this module resolve to
0x12CECD8/0x12CECD9 -- the shared empty-object sentinel that several live
pointers in .data name. Writing a table there turns all seven into garbage.
This was checked before the region was rejected, and it is recorded here so it
does not look like an oversight the next time someone counts bytes.

ALLOCATION ORDER AND FAILING CLOSED
===================================
Tables are requested by name and placed .rodata-first (read-only data belongs
in read-only data, and it is the precedent `ff7nx_60fps` set for the analog
lookup tables), falling back to the .text tail.

A request that does not fit raises, and every caller treats that as "this
feature is skipped and reported", not "this build is broken". That matters
because these features are independent: running out of table space for the
ambient map must leave the footstep and shuffle bridges working, and the build
log has to say which one did not fit and by how much.
"""
import struct


class NoSpace(Exception):
    """A table does not fit in what is left. The caller skips that feature."""


class TableSpace:
    """
    The contiguous data still free in ONE module image, allocated once.

    Construct it from the module a pass is about to patch -- never from the
    stock module -- because both regions are measured from the live segment
    sizes, which is precisely how this composes with whatever ran earlier.
    """

    def __init__(self, segs, raw):
        self.segs = segs
        # Working copies; `commit` hands back the segment images to repack.
        self.text = bytearray(raw[0])
        self.rodata = bytearray(raw[1])
        self.data = bytes(raw[2])
        # .rodata's tail runs up to .data's VA, which is fixed independently
        # of .rodata's declared size -- that is what makes growing the
        # declared size safe rather than a relocation of everything after it.
        self._ro_limit = segs[2][1] - segs[1][1]
        # .text's tail runs up to .rodata's VA, for the same reason.
        self._text_limit = segs[1][1]
        self.placed = []                 # (name, region, va, length)

    # ----------------------------------------------------------- measurement
    @property
    def rodata_free(self):
        return self._ro_limit - len(self.rodata)

    @property
    def text_free(self):
        return self._text_limit - len(self.text)

    @property
    def total_free(self):
        return self.rodata_free + self.text_free

    # ------------------------------------------------------------ allocation
    def place(self, name, blob, region='auto', align=4):
        """
        Claim `blob` and return the module offset it can be indexed from.

        `region` is 'rodata', 'text', or 'auto' (rodata first). `align` pads
        the start so a u16 or u32 table is not left straddling -- the cave's
        scaled-index load forms require it, and an unaligned table would fault
        or, worse, read one byte off on every lookup.
        """
        if not blob:
            raise ValueError('%s: refusing to place an empty table' % name)
        if region == 'auto':
            # BEST FIT, not rodata-first. The two regions are small and close
            # in size, so a big table dropped in the wrong one strands the
            # other: placing a 918-byte map in the 851-byte .rodata tail is
            # impossible, but placing a 232-byte one there first and then
            # finding the 918 will only go in .text leaves 616 + 66 -- two
            # fragments, neither big enough for the 651 that comes next, out
            # of 682 bytes that would have held it.
            #
            # Preferring the region with the LEAST room that still fits keeps
            # the larger region intact for the larger table still to come,
            # which is the only lever available without knowing the whole
            # request list up front.
            fits = [w for w in ('rodata', 'text')
                    if self._room(w, blob, align) is not None]
            order = tuple(sorted(fits, key=lambda w: self._room(w, blob,
                                                                align)))
        elif region in ('rodata', 'text'):
            order = (region,)
        else:
            raise ValueError('unknown region %r' % region)
        for where in order:
            buf = self.rodata if where == 'rodata' else self.text
            base_va = self.segs[1][1] if where == 'rodata' else 0
            limit = self._ro_limit if where == 'rodata' else self._text_limit
            pad = (-(base_va + len(buf))) % align
            if len(buf) + pad + len(blob) > limit:
                continue
            buf.extend(b'\0' * pad)
            va = base_va + len(buf)
            buf.extend(blob)
            self.placed.append((name, where, va, len(blob)))
            return va
        raise NoSpace(
            '%s needs %d contiguous byte(s); %d free in the .rodata tail and '
            '%d in the .text tail'
            % (name, len(blob), self.rodata_free, self.text_free))

    def _room(self, where, blob, align):
        """Bytes that would be LEFT in `where` after placing `blob`, or None."""
        buf = self.rodata if where == 'rodata' else self.text
        base_va = self.segs[1][1] if where == 'rodata' else 0
        limit = self._ro_limit if where == 'rodata' else self._text_limit
        pad = (-(base_va + len(buf))) % align
        after = limit - (len(buf) + pad + len(blob))
        return after if after >= 0 else None

    def would_fit(self, blob, align=4):
        """True if `place` would succeed, without claiming anything."""
        for buf, base_va, limit in (
                (self.rodata, self.segs[1][1], self._ro_limit),
                (self.text, 0, self._text_limit)):
            pad = (-(base_va + len(buf))) % align
            if len(buf) + pad + len(blob) <= limit:
                return True
        return False

    # --------------------------------------------------------------- output
    def commit(self):
        """The three segment images to hand back to `ff7nx_audio_cave.pack`."""
        return [bytes(self.text), bytes(self.rodata), self.data]

    def report(self):
        """Build-log lines: what was placed, and what is left for later."""
        out = []
        for name, where, va, length in self.placed:
            out.append('  table  %-28s %5d B  -> .%s +0x%X'
                       % (name, length, where, va))
        out.append('  contiguous data left: %d B (.rodata tail %d, .text tail '
                   '%d)' % (self.total_free, self.rodata_free, self.text_free))
        return out


def open_module(blob):
    """
    (segs, raw, TableSpace) for a module about to be patched.

    A thin convenience so a pass does not repeat the decompress/measure pair,
    and so every pass measures the same way.
    """
    import ff7nx_audio_cave as AC
    segs, raw = AC.segments(blob)
    return segs, raw, TableSpace(segs, raw)


def budget(blob):
    """
    (rodata_free, text_free) for an already-built module, without patching it.

    `build.py` calls this before it decides which optional audio features to
    attempt, so the log can say up front what the module can still hold rather
    than discovering it feature by feature.
    """
    import ff7nx_audio_cave as AC
    segs, raw = AC.segments(blob)
    space = TableSpace(segs, raw)
    return space.rodata_free, space.text_free
