"""
voice_ogg.py -- encode Echo-S's dialogue clips into the one Ogg shape the
Switch's native player is safe with, once, and keep them.

WHY THE CLIPS CANNOT JUST BE COPIED
===================================
The port plays a loose .ogg through `MusicStream` -> vgmstream. That path was
built for background music, and the author established on hardware -- not by
reading, by running it -- that it only behaves for one specific stream shape:

    48 kHz, 2 channels, Vorbis, with a one-sample final loop

and that selecting that route costs roughly the first 100 ms of the clip,
which the decoder discards on overlap. The fix for that is a 100 ms of silence
at the front of every clip. It is an ENCODE-TIME fix on purpose: the
alternative is a timer or a fudge factor in the ARM64 hook, which would have
to be right for every clip length and every frame rate. Silence is right for
all of them.

Echo-S ships 15,505 usable clips as 2.2 GB of arbitrary PC-side Vorbis. None
of it is in that shape, so all of it is re-encoded.

WHY THE CACHE IS THE POINT OF THIS FILE
=======================================
15,505 FFmpeg invocations is not a thing you do on every build. The fork this
came from re-encoded the lot every run and offered `--resume` for when you got
bored. Here the result is content-addressed the same way `_encode_field_cached`
addresses compressed fields: the key is a hash of the SOURCE BYTES plus the
exact recipe, so the cache survives the .iro being re-extracted, the mod being
toggled off and back on, two different lines that happen to ship identical
audio, and -- importantly -- it invalidates itself the moment anybody changes
the recipe. Change `RECIPE` and every clip re-encodes; leave it and none do.

Staging then hard-links out of the cache, so a second copy of 2.5 GB never
exists on disk. The link falls back to a copy across filesystems.

WHAT IS VERIFIED, AND WHEN
==========================
Every encode is checked against the decoder invariants before it is allowed
into the cache, so a cache hit is a file that was already proven good. The
check is not cosmetic: a build that timed out mid-encode used to be able to
leave a file that starts with a valid Vorbis header and changes its logical
stream serial halfway through, which the Switch refuses silently -- i.e. one
line goes quiet and nothing anywhere says why. FFmpeg writes to a temporary
beside the target and the result is published with one atomic replace, so a
half-written clip cannot be observed at all.
"""
import hashlib
import os
import shutil
import struct
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import movies
import voicemod


# The hardware-proven stream shape. LEAD_IN_MS is the decoder's own discard,
# measured; the sample rate and channel count are what the native player
# accepts and are NOT settings -- get either wrong and the line is silent.
LEAD_IN_MS = 100
TARGET_SAMPLE_RATE = 48000
TARGET_CHANNELS = 2


def _bitrate():
    """
    The one genuine knob, because it is the one thing here that is a taste
    judgement rather than a decoder requirement.

    500k is what the author shipped and it is the default, but it is worth
    knowing what it costs: Echo-S's 15,505 clips arrive as 2.2 GB of PC Vorbis
    and come out at roughly 2.8 GB, so the "normalisation" step makes the mod
    BIGGER. Nothing about 500k was proven on hardware -- the invariants that
    were are 48 kHz, stereo and a valid loop-tagged decoder route. For speech,
    128k is transparent
    and lands the whole set near 700 MB.

    It participates in the cache key, so changing it re-encodes everything
    exactly once and changing it back costs nothing.
    """
    return os.environ.get('SEVENTH_NX_VOICE_BITRATE', '500k').strip() or '500k'


TARGET_BITRATE = _bitrate()

# Bump the version and every clip re-encodes. It is deliberately a string that
# spells out what it encodes, so a diff of this file makes the invalidation
# obvious and a changed bitrate invalidates only what it should.
# V3 adds the ENCODER IDENTITY to the key. V2 let a distribution without
# libvorbis fall through to FFmpeg's own experimental Vorbis encoder without
# saying so and without changing the key, so a cache built by the weaker
# encoder was indistinguishable from one built by libvorbis and survived
# installing a better FFmpeg. It is not a cosmetic difference: measured over
# 400 of Echo-S's own clips, the native encoder aborts on roughly one in two
# hundred with `vorbisenc.c: Assertion l != csub failed`, and at equal
# bitrates it is audibly worse. See `_encoder`.
LEGACY_RECIPE = ('ECHO-VOICE-V3 adelay=%d ar=%d ac=%d vorbis %s LOOPSTART=0'
                 % (LEAD_IN_MS, TARGET_SAMPLE_RATE, TARGET_CHANNELS,
                    TARGET_BITRATE))
RECIPE = ('ECHO-VOICE-V4 adelay=%d ar=%d ac=%d vorbis %s '
          'LOOPSTART=final-1 LOOPLENGTH=1'
          % (LEAD_IN_MS, TARGET_SAMPLE_RATE, TARGET_CHANNELS, TARGET_BITRATE))

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'cache', '_voice_ogg')


class VoiceEncodeError(Exception):
    pass


# The runtime is meaningful only if this build successfully staged its clips.
# Keep this state here, next to the staging operation, instead of making the
# caller infer success from a partly-populated music_ogg directory left by an
# interrupted build.  The direct patcher tests never call `have_ffmpeg`, so
# they remain pure module-layout tests; production always does.
_stage_attempted = False
_stage_succeeded = False


def stage_attempted():
    return _stage_attempted


def stage_succeeded():
    return _stage_succeeded


def have_ffmpeg():
    global _stage_attempted, _stage_succeeded
    _stage_attempted = True
    _stage_succeeded = False
    return bool(movies._tool('ffmpeg'))


# Where a Vorbis-capable FFmpeg hides when the one on PATH is not.
#
# Homebrew stripped its `ffmpeg` formula to a minimal build at v8 and moved
# the rest -- libvorbis included -- into `ffmpeg-full`, which installs
# KEG-ONLY: it is not symlinked into the prefix, so `ffmpeg` on PATH is still
# the minimal one and `ffmpeg -encoders` looks exactly as it did before the
# install. That is a confusing failure, and the remedy is a fixed path, so
# look there rather than making every future build carry an env var.
#
# SEVENTH_NX_FFMPEG still wins for everything else in the project; this only
# decides which binary encodes VOICE, and only when the ordinary one cannot.
_ALTERNATE_FFMPEG = (
    '/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg',   # Apple silicon
    '/usr/local/opt/ffmpeg-full/bin/ffmpeg',      # Intel
    '/opt/homebrew/opt/ffmpeg@7/bin/ffmpeg',      # last formula that had it
    '/usr/local/opt/ffmpeg@7/bin/ffmpeg',
)


def _has_libvorbis(ffmpeg):
    try:
        result = subprocess.run(
            [ffmpeg, '-hide_banner', '-encoders'], stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, check=False)
    except OSError:
        return False
    return ' libvorbis ' in (result.stdout or '')


@lru_cache(maxsize=None)
def _encoder(ffmpeg):
    """
    Pick a Vorbis encoder, in descending order of how much it can be trusted.

    THIS IS NOT AN IMPLEMENTATION DETAIL
    ====================================
    There are two independent Vorbis encoders in play and they are not
    interchangeable:

    `libvorbis`     Xiph's own, what every other FF7 voice pack is built with,
                    what the mod's own clips were made with.
    `oggenc`        vorbis-tools. The same library, driven directly. Used when
                    FFmpeg cannot do Vorbis but the encoder is on the machine.
    `vorbis`        FFmpeg's own reimplementation. Marked experimental by
                    FFmpeg itself -- it will not run without `-strict -2`.

    That last one is the reason this function exists in its present form.
    Encoding 400 randomly chosen Echo-S clips with it aborts two of them
    outright (`vorbisenc.c:856: floor_encode: Assertion l != csub failed`),
    which over the full 15,505 is about seventy-five. Before this change one
    such abort ended the whole staging pass, so a build over a handful of
    fields succeeded and a build over many "kept failing to install" -- with
    the failure attributed to the size of the job rather than to a specific
    clip. It is also simply a worse encoder at the same bitrate.

    So: libvorbis from the ordinary FFmpeg, else libvorbis from a keg-only
    one at a known path, else oggenc, and the native encoder only as a last
    resort -- named in the build log and in the cache key so nothing silently
    inherits its output later.

    Returns (ffmpeg to use, id, ffmpeg codec args, oggenc path or None).
    """
    if _has_libvorbis(ffmpeg):
        return ffmpeg, 'libvorbis', ['-c:a', 'libvorbis'], None
    for alternate in _ALTERNATE_FFMPEG:
        if os.path.isfile(alternate) and _has_libvorbis(alternate):
            return alternate, 'libvorbis', ['-c:a', 'libvorbis'], None
    oggenc = shutil.which('oggenc')
    if oggenc:
        return ffmpeg, 'oggenc', None, oggenc
    try:
        result = subprocess.run(
            [ffmpeg, '-hide_banner', '-encoders'], stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, check=False)
    except OSError as exc:
        raise VoiceEncodeError('could not inspect ffmpeg encoders: %s' % exc)
    if ' vorbis ' in (result.stdout or ''):
        return ffmpeg, 'vorbis', ['-c:a', 'vorbis', '-strict', '-2'], None
    raise VoiceEncodeError(
        'this ffmpeg cannot encode Vorbis and oggenc is not on PATH; install '
        'an ffmpeg built with libvorbis, or vorbis-tools, or point '
        'SEVENTH_NX_FFMPEG at one')


@lru_cache(maxsize=1)
def _chosen():
    ffmpeg = movies._tool('ffmpeg')
    if not ffmpeg:
        return None
    try:
        return _encoder(ffmpeg)
    except VoiceEncodeError:
        return None


def encoder_detail():
    """
    A log line naming the encoder, and the binary when it is not the ordinary
    one. Voice is allowed to reach past PATH for a Vorbis-capable build, so
    which binary produced the clips has to be visible -- otherwise `ffmpeg
    -encoders` in the terminal contradicts the build log and there is no way
    to tell which one is lying.
    """
    chosen = _chosen()
    if not chosen:
        return 'unknown'
    if chosen[0] != movies._tool('ffmpeg'):
        return ('%s   (from %s -- the ffmpeg on PATH cannot encode Vorbis)'
                % (chosen[1], chosen[0]))
    return chosen[1]


@lru_cache(maxsize=1)
def encoder_id():
    """The encoder this machine will use, or None when ffmpeg is missing.

    Memoised because `cache_path` folds it into every key and that runs once
    per clip: without this it is a PATH scan and an `ffmpeg -encoders` probe
    fifteen thousand times.
    """
    chosen = _chosen()
    return chosen[1] if chosen else None


def encoder_warning():
    """
    One line for the build log when the chosen encoder is the weak one, or
    None. Stated where the person running the build will see it, because the
    remedy is a one-line install and the cost of not knowing is every clip on
    the SD card being worse than it needed to be.
    """
    if encoder_id() != 'vorbis':
        return None
    return ('this ffmpeg has no libvorbis and oggenc is not on PATH, so '
            'FFmpeg\'s own experimental Vorbis encoder is being used, and it '
            'costs real quality two ways. It IGNORES the requested bitrate: '
            'measured over 12 Echo-S clips averaging 380 kbps at source, '
            '-b:a 500k gives 397 kbps through libvorbis and 180 kbps through '
            'this one, so every line is running at roughly half the rate it '
            'was authored at. And it aborts outright on about one clip in '
            'two to four hundred (those are skipped and named below). '
            'On macOS this is not a broken install: from v8 Homebrew stripped '
            'the `ffmpeg` formula to a minimal build and moved the rest to '
            '`ffmpeg-full`, and libvorbis went with it. `brew install '
            'ffmpeg-full` is enough -- it installs KEG-ONLY, so `ffmpeg'
            ' -encoders` on your terminal will still show the minimal build, '
            'but this pass looks for it at its own path and will use it by '
            'itself on the next build. `brew install vorbis-tools` also '
            'works; oggenc is preferred over this encoder automatically. '
            'Either way the clips re-encode once, by themselves, because the '
            'encoder is part of the cache key.')


def _last_granule(data):
    """Return the exact decoded sample count from the final Ogg page."""
    offset, last = 0, None
    while offset < len(data):
        if data[offset:offset + 4] != b'OggS' or offset + 27 > len(data):
            raise VoiceEncodeError('invalid Ogg page at %d' % offset)
        segments = data[offset + 26]
        lacing_end = offset + 27 + segments
        if lacing_end > len(data):
            raise VoiceEncodeError('truncated Ogg lacing table')
        page_end = lacing_end + sum(data[offset + 27:lacing_end])
        if page_end > len(data):
            raise VoiceEncodeError('truncated Ogg page body')
        granule, = struct.unpack_from('<q', data, offset + 6)
        if granule >= 0:
            last = granule
        offset = page_end
    if last is None or last < 2:
        raise VoiceEncodeError('Ogg stream has no usable final granule')
    return last


def _vorbis_comments(data):
    """Return the complete user-comment byte strings from a Vorbis stream."""
    marker = data.find(b'\x03vorbis')
    if marker < 0 or marker + 11 > len(data):
        raise VoiceEncodeError('no Vorbis comment header')
    vendor_len, = struct.unpack_from('<I', data, marker + 7)
    count_at = marker + 11 + vendor_len
    if count_at + 4 > len(data):
        raise VoiceEncodeError('truncated Vorbis comment header')
    count, = struct.unpack_from('<I', data, count_at)
    cursor = count_at + 4
    comments = []
    for _ in range(count):
        if cursor + 4 > len(data):
            raise VoiceEncodeError('truncated Vorbis comment length')
        length, = struct.unpack_from('<I', data, cursor)
        cursor += 4
        if cursor + length > len(data):
            raise VoiceEncodeError('truncated Vorbis comment body')
        comments.append(bytes(data[cursor:cursor + length]))
        cursor += length
    return comments


def _with_final_sample_loop(data):
    """Move the required Ogg loop to one inaudible sample at the file tail.

    The Switch decoder needs loop metadata. Hardware later established that
    its NativeOggPlayer treats the tag as a loop boolean and still rewinds the
    sentence, so this standards-correct range is not the transition fix;
    ff7nx_voice owns that at MAPJUMP. Keeping the narrow range remains useful
    for players which honor the comments. Only the Vorbis comment packet and
    its Ogg CRC change; audio packets are byte-for-byte preserved.
    """
    data = bytearray(data)
    last = _last_granule(data)
    marker = data.find(b'\x03vorbis')
    page = data.rfind(b'OggS', 0, marker + 1)
    if page < 0 or page + 27 > len(data):
        raise VoiceEncodeError('Vorbis comment is not in an Ogg page')
    segments = data[page + 26]
    lacing = page + 27
    body = lacing + segments
    page_end = body + sum(data[lacing:body])
    if not body <= marker < page_end:
        raise VoiceEncodeError('Vorbis comment crosses an Ogg page')

    vendor_len, = struct.unpack_from('<I', data, marker + 7)
    count_at = marker + 11 + vendor_len
    count, = struct.unpack_from('<I', data, count_at)
    cursor = count_at + 4
    comments = []
    loopstart_count = 0
    for _ in range(count):
        if cursor + 4 > page_end:
            raise VoiceEncodeError('truncated Vorbis comment length')
        length, = struct.unpack_from('<I', data, cursor)
        cursor += 4
        value = bytes(data[cursor:cursor + length])
        cursor += length
        if cursor > page_end:
            raise VoiceEncodeError('truncated Vorbis comment body')
        upper = value.upper()
        if upper.startswith(b'LOOPSTART='):
            loopstart_count += 1
            continue
        if upper.startswith(b'LOOPLENGTH='):
            continue
        comments.append(value)
    if loopstart_count != 1:
        raise VoiceEncodeError('expected exactly one LOOPSTART comment, got %d'
                               % loopstart_count)

    comments.extend((b'LOOPSTART=' + str(last - 1).encode('ascii'),
                     b'LOOPLENGTH=1'))
    replacement = (struct.pack('<I', len(comments)) +
                   b''.join(struct.pack('<I', len(value)) + value
                            for value in comments))
    old_start, old_end = count_at, cursor
    delta = len(replacement) - (old_end - old_start)

    # `cursor` points at the Vorbis comment framing bit. Find the lacing entry
    # which terminates that packet; growing it moves the following setup packet
    # without changing any audio packet or page boundary after this one.
    needed = cursor + 1 - body
    walked = 0
    packet_end_segment = None
    for index in range(segments):
        walked += data[lacing + index]
        if walked >= needed:
            for final in range(index, segments):
                if data[lacing + final] < 255:
                    packet_end_segment = final
                    break
            break
    if (packet_end_segment is None or
            not 0 <= data[lacing + packet_end_segment] + delta <= 255):
        raise VoiceEncodeError('final loop tags need an Ogg lacing rewrite')
    data[lacing + packet_end_segment] += delta
    data[old_start:old_end] = replacement
    page_end += delta

    crc = 0
    for where in range(page, page_end):
        byte = 0 if page + 22 <= where < page + 26 else data[where]
        crc ^= byte << 24
        for _ in range(8):
            crc = ((crc << 1) ^
                   (0x04C11DB7 if crc & 0x80000000 else 0)) & 0xFFFFFFFF
    struct.pack_into('<I', data, page + 22, crc)
    return bytes(data)


def _verify(path, legacy=False):
    """
    Check the decoder invariants on a finished file. Returns (bytes, ms).

    Raises rather than returning False: a clip that fails this must never
    reach the SD card, because the way it fails on hardware is silence.
    """
    with open(path, 'rb') as handle:
        data = handle.read()
    if data[:4] != b'OggS':
        raise VoiceEncodeError('%s is not an Ogg stream' % path)

    # Walk the page chain. Two things are being caught here. One is ordinary
    # truncation. The other is the nasty one: two FFmpeg processes writing
    # alternate page ranges into the same target, which produces a file with a
    # valid header whose logical stream serial changes partway through. The
    # Switch refuses that silently.
    offset, serial, sequence, final_flags = 0, None, 0, 0
    while offset < len(data):
        if data[offset:offset + 4] != b'OggS' or offset + 27 > len(data):
            raise VoiceEncodeError('%s: invalid Ogg page at %d'
                                   % (path, offset))
        if data[offset + 4] != 0:
            raise VoiceEncodeError('%s: unsupported Ogg version' % path)
        segments = data[offset + 26]
        lacing_end = offset + 27 + segments
        if lacing_end > len(data):
            raise VoiceEncodeError('%s: truncated Ogg lacing table' % path)
        page_end = lacing_end + sum(data[offset + 27:lacing_end])
        if page_end > len(data):
            raise VoiceEncodeError('%s: truncated Ogg page body' % path)
        this_serial, this_sequence = struct.unpack_from('<II', data,
                                                        offset + 14)
        if serial is None:
            serial = this_serial
            if not data[offset + 5] & 0x02:
                raise VoiceEncodeError('%s: first page has no BOS flag' % path)
        elif this_serial != serial:
            raise VoiceEncodeError('%s: Ogg stream serial changes at page %d '
                                   '-- the file was written twice'
                                   % (path, sequence))
        if this_sequence != sequence:
            raise VoiceEncodeError('%s: Ogg page sequence is %d, expected %d'
                                   % (path, this_sequence, sequence))
        final_flags = data[offset + 5]
        offset = page_end
        sequence += 1
    if not final_flags & 0x04:
        raise VoiceEncodeError('%s: last Ogg page has no EOS flag' % path)

    rate, channels = _identification(data)
    if rate != TARGET_SAMPLE_RATE or channels != TARGET_CHANNELS:
        raise VoiceEncodeError('%s is %d Hz / %d channel(s); the native player '
                               'is only safe at %d / %d'
                               % (path, rate, channels, TARGET_SAMPLE_RATE,
                                  TARGET_CHANNELS))
    comments = _vorbis_comments(data)
    if legacy:
        if b'LOOPSTART=0' not in comments:
            raise VoiceEncodeError('%s has no legacy LOOPSTART=0 comment'
                                   % path)
    else:
        last = _last_granule(data)
        expected = b'LOOPSTART=' + str(last - 1).encode('ascii')
        if expected not in comments or b'LOOPLENGTH=1' not in comments:
            raise VoiceEncodeError(
                '%s does not loop only its final sample (expected %r and '
                'LOOPLENGTH=1)' % (path, expected))
    return len(data), voicemod._ogg_duration_ms(data)


def verify(path):
    """Verify the production one-sample-tail loop shape."""
    return _verify(path, legacy=False)


def _identification(data):
    """(sample rate, channels) from the Vorbis identification packet."""
    offset = 0
    while offset < len(data):
        segments = data[offset + 26]
        body = offset + 27 + segments
        length = sum(data[offset + 27:body])
        packet = data[body:body + length]
        if len(packet) >= 16 and packet[0] == 1 and packet[1:7] == b'vorbis':
            return struct.unpack_from('<I', packet, 12)[0], packet[11]
        offset = body + length
    raise VoiceEncodeError('no Vorbis identification header')


def _cache_paths(source):
    """
    Where this exact source, under this exact recipe, is kept.

    Keyed on the source's CONTENT. Keying on its path would miss the case the
    cache exists for: the same clip reached through a re-extracted .iro, or a
    line that ships audio byte-identical to another line's.
    """
    digests = []
    for recipe in (RECIPE, LEGACY_RECIPE):
        digest = hashlib.sha256()
        digest.update(recipe.encode('ascii'))
        digest.update(b'\0')
        digest.update((encoder_id() or 'none').encode('ascii'))
        digest.update(b'\0')
        digests.append(digest)
    # The encoder is part of the recipe, not part of the environment. Without
    # this, a cache built by FFmpeg's native encoder is reused verbatim after
    # libvorbis is installed and the quality problem is permanent.
    with open(source, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            for digest in digests:
                digest.update(block)
    return tuple(os.path.join(CACHE, digest.hexdigest() + '.ogg')
                 for digest in digests)


def cache_path(source):
    return _cache_paths(source)[0]


def _publish_final_sample_loop(source, target):
    """Retag one legacy cache entry atomically, without re-encoding audio."""
    with open(source, 'rb') as handle:
        converted = _with_final_sample_loop(handle.read())
    os.makedirs(os.path.dirname(target) or '.', exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix='.%s.' % os.path.basename(target), suffix='.tmp.ogg',
        dir=os.path.dirname(target) or '.')
    try:
        with os.fdopen(handle, 'wb') as output:
            output.write(converted)
        verify(temporary)
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def encode(source, target):
    """
    Re-encode one clip into the native shape, atomically, and verify it.

    The temporary lives beside the target so the replace is a rename inside
    one directory -- the only way to guarantee that nothing ever observes a
    partial clip, including a later build resuming after this one was killed.
    """
    if not movies._tool('ffmpeg'):
        raise VoiceEncodeError('ffmpeg is required to stage Echo-S voice '
                               '(set SEVENTH_NX_FFMPEG or put it on PATH)')
    chosen = _chosen()
    if not chosen:
        raise VoiceEncodeError('no usable Vorbis encoder')
    ffmpeg, encoder, codec_args, oggenc = chosen
    os.makedirs(os.path.dirname(target) or '.', exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix='.%s.' % os.path.basename(target), suffix='.tmp.ogg',
        dir=os.path.dirname(target) or '.')
    os.close(handle)
    common = [ffmpeg, '-hide_banner', '-nostdin', '-loglevel', 'error', '-y',
              '-i', source,
              '-af', 'adelay=%d:all=1' % LEAD_IN_MS,
              '-ar', str(TARGET_SAMPLE_RATE),
              '-ac', str(TARGET_CHANNELS)]
    try:
        if oggenc:
            # FFmpeg resamples and pads; oggenc does the Vorbis. The WAV in
            # between is uncompressed and lives on a pipe, so nothing lands on
            # disk except the finished clip.
            shaped = subprocess.run(
                common + ['-map_metadata', '-1', '-f', 'wav', '-'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if shaped.returncode:
                raise VoiceEncodeError(
                    'ffmpeg could not normalise %s: %s'
                    % (source, shaped.stderr.decode('utf-8', 'replace').strip()
                       or 'no stderr'))
            result = subprocess.run(
                [oggenc, '-Q', '-b', TARGET_BITRATE.rstrip('k') or '500',
                 '-c', 'LOOPSTART=0', '-o', temporary, '-'],
                input=shaped.stdout, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE)
            if result.returncode:
                raise VoiceEncodeError(
                    'oggenc could not encode %s: %s'
                    % (source, result.stderr.decode('utf-8', 'replace').strip()
                       or 'no stderr'))
        else:
            result = subprocess.run(
                common + codec_args + [
                    '-b:a', TARGET_BITRATE,
                    '-map_metadata', '-1', '-metadata', 'LOOPSTART=0',
                    temporary],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode:
                raise VoiceEncodeError(
                    'ffmpeg could not normalise %s with the %s encoder: %s'
                    % (source, encoder, result.stderr.strip() or 'no stderr'))
        with open(temporary, 'rb') as handle:
            converted = _with_final_sample_loop(handle.read())
        with open(temporary, 'wb') as handle:
            handle.write(converted)
        measured = verify(temporary)
        os.replace(temporary, target)
        temporary = None
        return measured
    finally:
        if temporary and os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _attempt(work, argument):
    """`(value, None)` or `(None, reason)` -- so one bad clip is one bad clip."""
    try:
        return work(argument), None
    except VoiceEncodeError as exc:
        return None, str(exc)


def _link_or_copy(source, target):
    """
    Hard-link the cached clip into place; copy when that is not possible.

    Every step degrades rather than raising, because none of them is the
    point. The point is that the right bytes end up at `target`: a hard link
    is merely how 2.8 GB avoids existing twice. A cache directory on a
    different filesystem from sdout, a mount that refuses `link`, one that
    refuses to unlink an existing link -- all of them end in a plain copy, and
    the build carries on.
    """
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if os.path.exists(target):
        try:
            if os.path.samefile(source, target):
                return False          # already the same inode: nothing to do
        except OSError:
            pass
        try:
            os.unlink(target)
        except OSError:
            # Cannot remove it, so cannot link over it: overwrite in place.
            # `samefile` said no or could not tell; if it could not tell and
            # they ARE the same file, copying would write the cache entry
            # through its own link, so that case is caught and skipped rather
            # than allowed to shred the cache.
            try:
                shutil.copyfile(source, target)
            except shutil.SameFileError:
                return False
            return True
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)
    return True


def stage(entries, output_dir, filename=voicemod.folder_voice_key_to_filename,
          log=lambda *_: None, workers=None, progress=None):
    """
    Put every entry under `output_dir` in native form, and report what it cost.

    Returns a dict: how many clips, how many bytes, how many had to be
    encoded versus taken from the cache, and how many were already in place.

    Duplicate keys stop the build. Ingestion already collapses FFNx's one
    legitimate duplicate (the window-qualified/plain fallback pair), so
    anything still colliding here is a packaging mistake, and picking a winner
    silently would mean one character's line playing over another's forever.
    """
    global _stage_attempted, _stage_succeeded
    _stage_attempted = True
    _stage_succeeded = False
    entries = list(entries)
    seen = {}
    for entry in entries:
        if entry.key in seen:
            raise voicemod.BadArchive(
                'two files claim %s: %r and %r'
                % (filename(entry.key), seen[entry.key].source_path,
                   entry.source_path))
        seen[entry.key] = entry

    os.makedirs(CACHE, exist_ok=True)
    report = {'clips': len(entries), 'bytes': 0, 'encoded': 0, 'cached': 0,
              'linked': 0, 'kept': 0, 'ms': 0, 'bitrate': TARGET_BITRATE,
              'encoder': encoder_id(), 'failed': []}
    if workers is None:
        try:
            workers = int(os.environ.get('SEVENTH_NX_VOICE_WORKERS') or 0)
        except ValueError:
            workers = 0
    workers = workers or min(8, (os.cpu_count() or 2))

    # ONE SOURCE IS ENCODED ONCE, HOWEVER MANY NAMES IT ANSWERS TO.
    # =============================================================
    # Field voice is very nearly one clip per name, so this used to be a
    # per-ENTRY loop and the difference did not show. Battle barks are not:
    # every action key is staged in eight variant slots off a pool of nine, so
    # the same file backs eight names and often several keys. A per-entry loop
    # hashes it eight times, races eight threads onto the same cache path, and
    # runs FFmpeg eight times to produce eight identical results -- correct
    # only because `encode` publishes with an atomic replace, and eight times
    # the work either way.
    #
    # So: resolve and encode the distinct SOURCES first, then link the names.
    # Echo-S's own field set has duplicate audio across lines too, so this is
    # a straight win there as well.
    sources = sorted({e.source_path for e in entries if e.source_path})
    for entry in entries:
        if not entry.source_path:
            raise VoiceEncodeError('voice entry %s has no source file'
                                   % filename(entry.key))

    def prepare(source):
        cached, legacy = _cache_paths(source)
        made = False
        if not os.path.isfile(cached):
            if os.path.isfile(legacy):
                try:
                    _verify(legacy, legacy=True)
                    _publish_final_sample_loop(legacy, cached)
                except VoiceEncodeError:
                    encode(source, cached)
                    made = True
            else:
                encode(source, cached)
                made = True
        try:
            measured = verify(cached)
        except VoiceEncodeError:
            # A cache entry that no longer verifies is one a killed build or a
            # disk error left behind. Replace it rather than shipping it.
            encode(source, cached)
            measured = verify(cached)
            made = True
        return cached, made, measured

    ready, broken = {}, {}
    report['sources'] = len(sources)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for n, (source, result) in enumerate(
                zip(sources, pool.map(
                    lambda s: _attempt(prepare, s), sources))):
            value, failure = result
            if failure is None:
                ready[source] = value
                # Counted per SOURCE, not per name: one encode is one encode
                # however many variant slots it goes on to answer, and the
                # bytes are what lands on the card -- eight hard links to one
                # clip cost one clip, so summing per name would report the
                # battle set at 725 MB when it is 88.
                report['encoded' if value[1] else 'cached'] += 1
                report['bytes'] += value[2][0]
                report['ms'] += value[2][1]
            else:
                broken[source] = failure
            if progress and (n + 1) % 200 == 0:
                progress(n + 1, len(sources))

    def one(entry):
        cached, made, measured = ready[entry.source_path]
        target = os.path.join(output_dir, filename(entry.key))
        wrote = _link_or_copy(cached, target)
        return made, wrote, measured, target

    # ONE BAD CLIP IS ONE BAD CLIP, NOT A FAILED BUILD.
    # =================================================
    # This used to be a bare `pool.map`, so the first clip that would not
    # encode ended the pass and `build.py` reported that nothing at all was
    # staged. On a handful of fields that never happened; on the full set it
    # happened every time, because FFmpeg's native Vorbis encoder aborts on
    # roughly one clip in two hundred (see `_encoder`). The visible symptom
    # was "it fails when I ask for more fields", which points at the size of
    # the job and not at the single file that is actually wrong.
    #
    # A clip that cannot be encoded is one line of dialogue that stays silent.
    # A pass that refuses to stage anything is fifteen thousand of them. So
    # failures are collected and named, and the build carries on. Only the
    # structural errors -- a duplicate key, a missing source -- still stop it,
    # because those mean the INDEX is wrong rather than one file.
    produced = []

    def attempt(entry):
        if entry.source_path in broken:
            return None, (filename(entry.key), entry.source_path,
                          broken[entry.source_path])
        try:
            return one(entry), None
        except VoiceEncodeError as exc:
            return None, (filename(entry.key), entry.source_path, str(exc))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for n, (ok, failure) in enumerate(pool.map(attempt, entries)):
            if failure is not None:
                report['failed'].append(failure)
            else:
                _made, wrote, _measured, target = ok
                report['linked' if wrote else 'kept'] += 1
                produced.append(target)
            if progress and (n + 1) % 500 == 0:
                progress(n + 1, len(entries))
    report['produced'] = produced
    report['staged'] = len(produced)
    if not produced:
        raise VoiceEncodeError(
            'not one of %d clip(s) could be encoded; first failure: %s'
            % (len(entries),
               report['failed'][0][2] if report['failed'] else 'unknown'))
    _stage_succeeded = True
    return report
