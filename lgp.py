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
        return set(self.index)

    def is_field(self, entry):
        p = entry['payload']
        return len(p) >= 4 and struct.unpack('<I', p[:4])[0] == len(p) - 4

    def decompressed(self, entry):
        return lzs_decompress(entry['payload'][4:])

    def encode_field(self, raw):
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
