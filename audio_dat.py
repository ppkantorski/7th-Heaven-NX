#!/usr/bin/env python3
"""
audio_dat.py -- FF7's `audio.fmt` / `audio.dat` sound-effect archive.

WHY THIS EXISTS
===============
The Switch port has NO external sound-effect path. Every string in
`exefs/main` that reads an `.ogg` reads it from `data/music_ogg`, and nothing
else; there is no FFNx loader, no `use_external_sfx`, no ambient layer and no
voice layer. A sound mod built for FFNx -- Cosmo Memory, Echo-S -- therefore
cannot be dropped onto the SD card and expected to work.

What the port DOES read is the same pair of files the PC game has always
used: `audio.fmt` (750 fixed-size records) and `audio.dat` (their sample
data, concatenated). So a sound mod can be applied by REBUILDING that pair
with the mod's audio in place of the originals, which is what this module is
for.

THE FORMAT
==========
`audio.fmt` is 750 records in slot order, 1-based, matching the sound IDs the
game uses. Each record is:

    struct FfWav_header {          # 24 bytes
        uint32 len;                # bytes of sample data in audio.dat
        uint32 offset;             # where they start in audio.dat
        uint32 loop;               # non-zero if `start`/`end` are meaningful
        uint32 count;
        uint32 start, end;         # loop points, in DECOMPRESSED bytes
    }

followed by the waveform format. That part is VARIABLE LENGTH and the header
is what tells you which:

    len != 0  ->  ADPCMWAVEFORMAT, 50 bytes
                  (WAVEFORMATEX 18 + wSamplesPerBlock 2 + wNumCoef 2
                   + ADPCMCOEFSET aCoef[7] 28)
    len == 0  ->  18 bytes of filler, and the slot is empty

Getting that wrong desynchronises every record after the first empty slot,
which is why it is spelled out here. ficedula's Cosmo tool and the sfxEdit
0.3 fork of it both do exactly this; this module is a Python port of that
behaviour, verified against the same structures.

The audio is 4-bit **MS ADPCM** (`wFormatTag` 2), mono. Not IMA.

WHAT THIS MODULE WILL NOT DO
============================
Rebuild a pair from nothing. Slots the caller does not replace are carried
over from the original byte for byte -- their format block, their loop points
and their `count` field are all preserved rather than regenerated. A sound
archive is not something to rewrite from first principles when 750 slots'
worth of the game depends on it.
"""
import io
import os
import struct
import subprocess

NUM_SLOTS = 750
HEADER = struct.Struct('<6I')          # len, offset, loop, count, start, end
HEADER_LEN = HEADER.size               # 24
FMT_FULL = 50                          # ADPCMWAVEFORMAT
FMT_EMPTY = 18                         # WAVEFORMATEX only
EMPTY_FILL = b'\xCD' * FMT_EMPTY       # what the original packer writes

WAVE_FORMAT_ADPCM = 2


class BadArchive(Exception):
    pass


class Entry:
    """One slot. `data` is None for an empty slot."""

    __slots__ = ('fmt', 'data', 'loop', 'count', 'start', 'end')

    def __init__(self, fmt=None, data=None, loop=0, count=0, start=0, end=0):
        self.fmt = fmt          # 50-byte ADPCMWAVEFORMAT, or None
        self.data = data        # sample bytes, or None
        self.loop = loop
        self.count = count
        self.start = start
        self.end = end

    @property
    def empty(self):
        return not self.data

    def _wfx(self):
        if not self.fmt:
            return None
        return struct.unpack('<HHIIHH', self.fmt[:16] + self.fmt[16:18])

    @property
    def sample_rate(self):
        return struct.unpack('<I', self.fmt[4:8])[0] if self.fmt else None

    @property
    def block_align(self):
        return struct.unpack('<H', self.fmt[12:14])[0] if self.fmt else None

    @property
    def channels(self):
        return struct.unpack('<H', self.fmt[2:4])[0] if self.fmt else None

    def __repr__(self):
        if self.empty:
            return '<Entry empty>'
        return ('<Entry %d bytes, %d Hz, %d ch, block %d%s>'
                % (len(self.data), self.sample_rate or 0, self.channels or 0,
                   self.block_align or 0, ', looping' if self.loop else ''))


def read(fmt_path, dat_path, slots=NUM_SLOTS):
    """Parse a pair into a list of `slots` Entries."""
    with open(fmt_path, 'rb') as f:
        fmt = f.read()
    with open(dat_path, 'rb') as f:
        dat = f.read()
    return loads(fmt, dat, slots)


def loads(fmt, dat, slots=NUM_SLOTS):
    out = []
    pos = 0
    for i in range(slots):
        if pos + HEADER_LEN > len(fmt):
            raise BadArchive('audio.fmt ends inside record %d of %d '
                             '(%d bytes)' % (i + 1, slots, len(fmt)))
        ln, off, loop, count, start, end = HEADER.unpack_from(fmt, pos)
        pos += HEADER_LEN
        if ln:
            block = fmt[pos:pos + FMT_FULL]
            if len(block) < FMT_FULL:
                raise BadArchive('audio.fmt ends inside the format block of '
                                 'record %d' % (i + 1))
            pos += FMT_FULL
            if off + ln > len(dat):
                raise BadArchive('record %d wants bytes %d..%d of audio.dat, '
                                 'which is only %d long'
                                 % (i + 1, off, off + ln, len(dat)))
            out.append(Entry(block, dat[off:off + ln], loop, count,
                             start, end))
        else:
            pos += FMT_EMPTY
            out.append(Entry())
    return out


def dumps(entries):
    """Serialise back to (fmt bytes, dat bytes). Offsets are recomputed."""
    fmt = io.BytesIO()
    dat = io.BytesIO()
    for i, e in enumerate(entries):
        if e.empty:
            # The original packer records the CURRENT dat position even for an
            # empty slot and pads the format block with 0xCD. Both are
            # reproduced so a pair that is read and written unchanged comes
            # back byte for byte.
            fmt.write(HEADER.pack(0, dat.tell(), 0, 0, 0, 0))
            fmt.write(EMPTY_FILL)
            continue
        if not e.fmt or len(e.fmt) != FMT_FULL:
            raise BadArchive('slot %d has %d bytes of format block, expected '
                             '%d' % (i + 1, len(e.fmt or b''), FMT_FULL))
        fmt.write(HEADER.pack(len(e.data), dat.tell(), e.loop, e.count,
                              e.start, e.end))
        fmt.write(e.fmt)
        dat.write(e.data)
    return fmt.getvalue(), dat.getvalue()


def write(entries, fmt_path, dat_path):
    fmt, dat = dumps(entries)
    for path, blob in ((fmt_path, fmt), (dat_path, dat)):
        d = os.path.dirname(os.path.abspath(path))
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, 'wb') as f:
            f.write(blob)
    return len(fmt), len(dat)


# ---------------------------------------------------------------- RIFF

def parse_wav(blob):
    """
    (format block, sample data, loop_start, loop_end) from an MS ADPCM RIFF.

    Chunks other than `fmt `, `data` and the tool's own `fflp` are skipped --
    ffmpeg writes `fact` and `LIST`, and neither belongs in audio.fmt.
    """
    if blob[:4] != b'RIFF' or blob[8:12] != b'WAVE':
        raise BadArchive('not a RIFF/WAVE file')
    fmt = data = None
    start = end = 0
    pos = 12
    while pos + 8 <= len(blob):
        cid = blob[pos:pos + 4]
        size = struct.unpack_from('<I', blob, pos + 4)[0]
        body = blob[pos + 8:pos + 8 + size]
        if cid == b'fmt ':
            if size < FMT_FULL:
                raise BadArchive('fmt chunk is %d bytes; MS ADPCM needs %d '
                                 '(is this really 4-bit ADPCM?)'
                                 % (size, FMT_FULL))
            fmt = body[:FMT_FULL]
        elif cid == b'data':
            data = body
        elif cid == b'fflp' and size >= 8:
            start, end = struct.unpack_from('<2I', body, 0)
        pos += 8 + size + (size & 1)          # RIFF chunks are word-aligned
    if fmt is None or data is None:
        raise BadArchive('WAV has no %s chunk'
                         % ('fmt ' if fmt is None else 'data'))
    tag = struct.unpack_from('<H', fmt, 0)[0]
    if tag != WAVE_FORMAT_ADPCM:
        raise BadArchive('wFormatTag is %d, expected %d (MS ADPCM). The game '
                         'decodes nothing else from audio.dat.'
                         % (tag, WAVE_FORMAT_ADPCM))
    return fmt, data, start, end


def entry_from_wav(blob, like=None):
    """
    An Entry built from an MS ADPCM WAV.

    `like` is the slot being replaced; its `count` is carried over.

    LOOP POINTS ARE NEVER INVENTED. An earlier version marked a replacement
    as looping over its whole decoded length when the slot it displaced
    looped, on the reasoning that a sound the engine expects to sustain
    should keep sustaining. That was a guess on a field whose units are not
    certain -- sfxEdit's own readme says the markers are "the sample position
    in bytes as decompressed in memory (i.e. 16bit linear PCM (?))", question
    mark and all -- and the consequence of getting it wrong is not a wrong
    noise, it is a sound that never reports finishing, which is indis-
    tinguishable from a hang to anything waiting on it.

    A replacement therefore loops only if the WAV itself carries an `fflp`
    chunk saying so, with the points that chunk gives. Deciding what to do
    about a vanilla slot that looped is the caller's business; see
    sfxmod.rebuild, which by default declines to touch one at all.
    """
    fmt, data, start, end = parse_wav(blob)
    e = Entry(fmt, data, 1 if (start or end) else 0, 0, start, end)
    if like is not None:
        e.count = like.count
    return e


def decoded_bytes(fmt, data_len):
    """
    Length of the decoded sound in bytes, which is the unit audio.fmt's loop
    points are in (16-bit linear PCM, so always even).
    """
    _tag, channels, _rate, _avg, block_align, _bits = \
        struct.unpack_from('<HHIIHH', fmt, 0)
    samples_per_block = struct.unpack_from('<H', fmt, 18)[0]
    if not block_align or not samples_per_block:
        return 0
    whole, tail = divmod(data_len, block_align)
    total = whole * samples_per_block
    if tail >= 7 * channels:
        total += (tail - 7 * channels) * 2 // channels + 2
    return total * channels * 2


# ------------------------------------------------------------- encoding

class MissingFFmpeg(Exception):
    pass


def encode(src, rate=None, block_align=None, log=lambda *_: None):
    """
    Encode any audio file ffmpeg can read into mono 4-bit MS ADPCM.

    `rate` and `block_align` come from the slot being replaced, so the
    rebuilt entry sits in the archive on the same terms as the one it
    displaces. FF7 pitches some effects relative to their stored rate, so
    resampling to the original's rate is not cosmetic.
    """
    import shutil as _sh
    if not _sh.which('ffmpeg'):
        raise MissingFFmpeg('ffmpeg is required to convert sound effects')
    cmd = ['ffmpeg', '-y', '-nostdin', '-loglevel', 'error', '-i', src,
           '-ac', '1', '-acodec', 'adpcm_ms']
    if rate:
        cmd += ['-ar', str(rate)]
    if block_align and 32 <= block_align <= 8192:
        cmd += ['-block_size', str(block_align)]
    cmd += ['-f', 'wav', '-']
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0 or not p.stdout:
        raise BadArchive('ffmpeg failed on %s: %s'
                         % (os.path.basename(src),
                            p.stderr.decode('utf8', 'replace').strip()[-300:]))
    return p.stdout


if __name__ == '__main__':
    import sys
    if len(sys.argv) != 3:
        print('usage: audio_dat.py audio.fmt audio.dat   (report only)')
        raise SystemExit(1)
    ents = read(sys.argv[1], sys.argv[2])
    used = [e for e in ents if not e.empty]
    print('%d slots, %d used, %d empty' % (len(ents), len(used),
                                           len(ents) - len(used)))
    rates = {}
    for e in used:
        rates[e.sample_rate] = rates.get(e.sample_rate, 0) + 1
    print('sample rates:', dict(sorted(rates.items())))
    print('looping:', sum(1 for e in used if e.loop))
    print('data bytes:', sum(len(e.data) for e in used))
