"""
Reading and writing FF7 LGP archives, and FF7 field files.

Layout:
    12  creator "\\0\\0SQUARESOFT"
    4   file count
        TOC: count x 27  (20 name, 4 offset, 1 check, 2 conflict)
    3600 lookup table
    2   conflict table entry count
        data blocks: 20 name, 4 length, payload
    14  terminator "FINAL FANTASY7"

The lookup table holds TOC indices rather than file offsets, so it can be
carried over verbatim when entries are replaced but none are added or
removed. Adding entries would require regenerating it, which this module
deliberately refuses to do -- see Archive.replace().
"""
import os
import struct

CREATOR_LEN = 12
TOC_ENTRY_LEN = 27
LOOKUP_LEN = 3600
TERMINATOR = b'FINAL FANTASY7'
FIELD_HEADER_LEN = 42
N_SECTIONS = 9


class NewEntriesRequired(Exception):
    """Raised when a merge would add filenames not present in the archive."""

    def __init__(self, names):
        self.names = names
        super().__init__(f'{len(names)} new entries required')


# ------------------------------------------------------------------ LZS

def lzs_decompress(data):
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        ctrl = data[i]
        i += 1
        for bit in range(8):
            if i >= n:
                break
            if ctrl & (1 << bit):
                out.append(data[i])
                i += 1
            else:
                if i + 1 >= n:
                    break
                b1, b2 = data[i], data[i + 1]
                i += 2
                offset = b1 | ((b2 & 0xF0) << 4)
                length = (b2 & 0x0F) + 3
                start = len(out) - ((len(out) - 18 - offset) & 0xFFF)
                for k in range(length):
                    p = start + k
                    out.append(out[p] if 0 <= p < len(out) else 0)
    return bytes(out)


def lzs_store(data):
    """Valid LZS with every byte literal. ~12.5% larger, always correct."""
    out = bytearray()
    for i in range(0, len(data), 8):
        chunk = data[i:i + 8]
        out.append((1 << len(chunk)) - 1)
        out += chunk
    return bytes(out)


# Reference encoding, derived from lzs_decompress() above rather than from a
# spec, so the two cannot drift apart:
#
#     start = len(out) - ((len(out) - 18 - offset) & 0xFFF)
#
# With `i` bytes already emitted and a match at absolute position `p`, the
# distance is `d = i - p`, and the equation is satisfied for
#
#     offset = (i - 18 - d) & 0xFFF = (p - 18) & 0xFFF
#
# `& 0xFFF` makes that valid for p < 18 too -- the arithmetic is modular on
# both sides. d must be 1..4095: d == 4096 would encode as 0 and decode to
# start == len(out), which is not a back-reference at all.
MAX_MATCH = 18
MIN_MATCH = 3
WINDOW = 4095


def lzs_compress(data, max_chain=24):
    """
    Real LZS compression in the exact dialect lzs_decompress() reads.

    WHY THIS EXISTS
    ---------------
    Every field written back to flevel.lgp used to go through lzs_store(),
    which is literal-only and therefore ~12.5% LARGER than the raw data.
    Vanilla's own entries are properly compressed at roughly 2.4:1, so
    re-encoding a field cost about 2.8x its stored size EVEN IF THE MOD
    CHANGED NOTHING ABOUT IT. With one or two fields that is invisible; with
    Cosmos Limit Break, which replaces the background section of 683 of the
    741 fields, flevel.lgp went from 131 MB to 333 MB.

    Greedy matching with a hash chain on 3-byte prefixes. `max_chain` bounds
    how many candidate positions are tried per byte -- the whole point is to
    stay fast enough to run over a few hundred MB of field data, so this
    trades a little ratio for a lot of speed. Measured on Cosmos Limit
    Break's fields: ~0.31 of raw (better than vanilla's own encoder) at
    ~1.4 MB/s.

    NOT verified here on purpose: the caller checks the round trip (see
    Archive.encode_field), because "the output decompresses to exactly the
    input" is the only property that matters and it is cheap to assert.
    """
    n = len(data)
    out = bytearray()
    chains = {}
    i = 0
    bit = 8
    ctrl = 0
    ctrl_idx = 0
    while i < n:
        if bit == 8:
            ctrl_idx = len(out)
            out.append(0)
            ctrl = 0
            bit = 0
        best_len = 0
        best_pos = 0
        if i + MIN_MATCH <= n:
            chain = chains.get(data[i:i + MIN_MATCH])
            if chain:
                floor = i - WINDOW
                if floor < 0:
                    floor = 0
                limit = n - i
                if limit > MAX_MATCH:
                    limit = MAX_MATCH
                tried = 0
                for p in reversed(chain):
                    if p < floor:
                        break
                    tried += 1
                    if tried > max_chain:
                        break
                    length = MIN_MATCH
                    while length < limit and data[p + length] == data[i + length]:
                        length += 1
                    if length > best_len:
                        best_len = length
                        best_pos = p
                        if length == limit:
                            break
        if best_len >= MIN_MATCH:
            enc = (best_pos - 18) & 0xFFF
            out.append(enc & 0xFF)
            out.append(((enc >> 4) & 0xF0) | (best_len - MIN_MATCH))
            step = best_len
        else:
            ctrl |= 1 << bit            # set = literal
            out.append(data[i])
            step = 1
        out[ctrl_idx] = ctrl
        end = i + step
        j = i
        while j < end:
            if j + MIN_MATCH <= n:
                chains.setdefault(data[j:j + MIN_MATCH], []).append(j)
            j += 1
        i = end
        bit += 1
    return bytes(out)


# ----------------------------------------------------------- field files

def split_sections(raw):
    blank = struct.unpack('<H', raw[0:2])[0]
    count = struct.unpack('<I', raw[2:6])[0]
    if blank != 0 or count != N_SECTIONS:
        raise ValueError('not a field file')
    pointers = struct.unpack('<9I', raw[6:FIELD_HEADER_LEN])
    sections = []
    for p in pointers:
        length = struct.unpack('<I', raw[p:p + 4])[0]
        sections.append(raw[p + 4:p + 4 + length])
    return sections


def join_sections(sections):
    pointers = []
    offset = FIELD_HEADER_LEN
    for s in sections:
        pointers.append(offset)
        offset += 4 + len(s)
    out = bytearray()
    out += struct.pack('<H', 0)
    out += struct.pack('<I', N_SECTIONS)
    out += struct.pack('<9I', *pointers)
    for s in sections:
        out += struct.pack('<I', len(s))
        out += s
    return bytes(out)


# --------------------------------------------------------------- archive

class Archive:
    def __init__(self, path):
        self.path = path
        with open(path, 'rb') as f:
            self.creator = f.read(CREATOR_LEN)
            if not self.creator.endswith(b'SQUARESOFT'):
                raise ValueError(f'{path} is not an LGP archive')
            count = struct.unpack('<i', f.read(4))[0]
            toc = []
            for _ in range(count):
                e = f.read(TOC_ENTRY_LEN)
                name = e[:20].split(b'\0')[0].decode('ascii', 'replace')
                offset, check, conflict = struct.unpack('<IBH', e[20:27])
                toc.append((name, offset, check, conflict))
            # Everything between the TOC and the first data block -- the
            # 3600-byte lookup table AND the variable-length conflict table.
            # Reading it as one verbatim blob is format-agnostic: the
            # conflict table's length varies with how many filenames collide,
            # and truncating it (e.g. reading only the 2-byte count) corrupts
            # the archive for any game LGP that has conflicts. flevel.lgp has
            # none, which masked this; battle.lgp and char.lgp do have them.
            mid_start = f.tell()
            first_data = min(offset for _, offset, _, _ in toc)
            self.middle = f.read(first_data - mid_start)
            self.entries = []
            for name, offset, check, conflict in toc:
                f.seek(offset)
                block = f.read(24)
                size = struct.unpack('<I', block[20:24])[0]
                self.entries.append({
                    'name': name,
                    'raw_name': block[:20],
                    'check': check,
                    'conflict': conflict,
                    'payload': f.read(size),
                })
        self.index = {e['name'].lower(): e for e in self.entries}

    def names(self):
        """
        Entry names, SORTED. Never a set.

        THIS RETURNED A SET, AND IT MADE THE WHOLE BUILD NON-REPRODUCIBLE.

        Python randomises string hashing per process (PYTHONHASHSEED), so a
        set of names iterates in a different order in every run. Eleven passes
        walk `names()` directly -- `ff7nx_marginart`, `ff7nx_marginpage`,
        `ff7nx_palkey`, `ff7nx_ws`, `ff7nx_bgkey`, `ff7nx_vclip` and more --
        and at least `ff7nx_palkey` carries state across fields, so the order
        changed its decisions.

        MEASURED across four builds, the palette key's own counter:

            build 33  3,796 palette(s) LEFT ALONE
            build 34  3,847
            build 35  3,861
            build 36  3,883        <- and build 36 changed ONE LOG LINE

        Build 36 altered no archive logic whatsoever and still moved it by 22.
        Two builds of identical settings did not produce identical archives,
        which means part of every log diff this project has ever done was
        noise, and a different set of ~20 palettes got de-fringed each time.

        It also explains the note in `field_bg_pagecap.clamp_palettes`: "a
        second reading of the same archive with the same call reported 0 tiles
        repointed where the first reported 14, which means my own measurement
        of this is not stable". It was not the measurement. It was this.

        Sorted is the fix. No caller does set algebra on the result -- every
        one of them either iterates it or immediately sorts it -- so a list is
        a straight superset of what they used.
        """
        return sorted(self.index)

    def is_field(self, entry):
        p = entry['payload']
        return len(p) >= 4 and struct.unpack('<I', p[:4])[0] == len(p) - 4

    def decompressed(self, entry):
        return lzs_decompress(entry['payload'][4:])

    def encode_field(self, raw, compress=True):
        """
        Wrap raw field bytes as a stored LGP payload.

        Compressed by default, and VERIFIED: the compressor's output is
        decompressed with the same routine the game's format demands and
        compared against the input. Anything short of an exact match falls
        back to lzs_store(), which is literal-only and cannot be wrong. So
        the worst case of a compressor bug is the old file size, never a
        corrupt field.
        """
        enc = None
        if compress:
            try:
                cand = lzs_compress(raw)
                if lzs_decompress(cand) == raw:
                    enc = cand
            except Exception:                                  # noqa: BLE001
                enc = None
        if enc is None:
            enc = lzs_store(raw)
        return struct.pack('<I', len(enc)) + enc

    def replace(self, payloads):
        """
        Stage replacement payloads keyed by lowercase name. Raises
        NewEntriesRequired if any name is absent, since regenerating the
        lookup table is not supported.
        """
        missing = sorted(n for n in payloads if n not in self.index)
        if missing:
            raise NewEntriesRequired(missing)
        for name, data in payloads.items():
            self.index[name]['payload'] = data

    def write(self, dest):
        count = len(self.entries)
        offset = (CREATOR_LEN + 4 + count * TOC_ENTRY_LEN
                  + len(self.middle))
        toc = bytearray()
        for e in self.entries:
            toc += struct.pack('<20sIBH', e['raw_name'], offset,
                               e['check'], e['conflict'])
            offset += 24 + len(e['payload'])
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        with open(dest, 'wb') as f:
            f.write(self.creator)
            f.write(struct.pack('<i', count))
            f.write(toc)
            f.write(self.middle)
            for e in self.entries:
                f.write(e['raw_name'])
                f.write(struct.pack('<I', len(e['payload'])))
                f.write(e['payload'])
            f.write(TERMINATOR)


def verify_roundtrip(path):
    """True if reading and rewriting an archive reproduces it byte for byte."""
    a = Archive(path)
    tmp = path + '.roundtrip'
    try:
        a.write(tmp)
        with open(path, 'rb') as f1, open(tmp, 'rb') as f2:
            return f1.read() == f2.read()
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
