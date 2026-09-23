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

AND SINCE BUILD 482, THE LEVEL
==============================
Echo-S's lines come from many people, many microphones and many years, and
they arrive at very different levels. `_measure` gives each clip ONE gain,
measured from the clip itself, so the whole set lands where the setting says.
See the block above `NORMALIZE_ENV` for the measurements, why the default is
the SPEECH BAND rather than the whole signal, and where the target
comes from.

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
import re
import shutil
import struct
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import movies
import voicemod


class VoiceEncodeError(Exception):
    pass


# ------------------------------------------------------- loudness, BUILD 482
# Echo-S's clips come from many people, many microphones and many years, and
# they arrive at wildly different levels. Measured over 160 random clips of
# the shipped set:
#
#     peak      min -12.0   median  -2.1   max  -0.0 dBFS    spread 12.0 dB
#     loudness  min -28.4   median -16.1   max  -9.2 LUFS    spread 19.1 dB
#
# and that second row is the one you hear. Equalising the PEAKS -- which is
# the obvious reading of "make them all the same volume" -- leaves 16.4 dB of
# that loudness spread standing, because a line with one sharp consonant
# peaks high while sounding quiet. So the default measures the way ears
# hear, EBU R128 integrated loudness, and `peak` remains available for
# anyone who wants literally equal maxima.
#
# WHY THE TARGET IS -12 AND NOT -15.3.
#
# -15.3 LUFS was the measured loudness of Jessie's "My hero!" in `nmkin_3`
# (dialogue id 6) -- a real line, picked so the level was not invented. It
# was still the wrong number, for a reason the reference clip could not show:
# the set's median source loudness is -16.1 LUFS, so a target of -15.3 sits
# in the MIDDLE of the material. Measured over 60 random clips, **19 of them
# (32%) were being turned DOWN** to reach it.
#
# That is what "I can barely hear this line" was. `chrin_1b/1` -- the church,
# after the fall -- is recorded at -11.0 LUFS, one of the loudest clips in
# the game, and we attenuated it by 4.4 dB. Normalising a set that is mostly
# quiet TO ITS OWN MIDDLE makes the loud half quieter, and the loud half is
# not the problem.
#
# -12 is the highest target at which NOTHING is turned down and almost
# everything still reaches it. Measured on the same 60 clips:
#
#     target   turned down   >0.5 dB short   out peaks (dBFS)   spread
#     -15.3      19 (32%)        0            -6.9 .. -1.2      1.1 dB
#     -12         0 (0%)         4            -4.9 .. -1.5      2.5 dB
#     -11         0 (0%)         6            -4.9 .. -1.5      3.1 dB
#
# -12 also tightens the PEAKS, which is the thing that was audibly
# inconsistent between recordings: a 3.4 dB spread against 5.7 dB.
#
# The spread of the loudness grows slightly because the limiter cannot lift
# every clip that far; that is the honest cost and it is smaller than the
# 4 dB of attenuation it replaces. Above -12 it grows faster for less.
#
# THIS IS ONLY HALF THE LEVEL. The other half is the PLAYBACK gain -- FFNx
# plays every voice clip at 2.75 and we played 1.0. See
# `ff7nx_voice.VOICE_GAIN_DEFAULT`; no encoder target can make up 8.8 dB.
#
# WHAT IS APPLIED IS ONE NUMBER PER CLIP. Not `dynaudnorm`, not `speechnorm`,
# not single-pass `loudnorm` -- all three vary the gain THROUGH the clip and
# audibly pump on speech. This measures the whole clip, computes one scalar
# gain, and applies it with `volume`. A pure gain cannot change timbre and
# cannot pump. What stops it clipping is a true-peak limiter on the few
# samples that would have -- NOT a smaller gain; see `LIMITER_CEILING_DB`
# for why the second version had to stop capping.
NORMALIZE_ENV = 'SEVENTH_NX_VOICE_NORMALIZE'
NORMALIZE_DEFAULT = 'speech:-16.2'
REFERENCE_LINE = 'nmkin_3 dialogue 6, Jessie "My hero!"'
TRUE_PEAK_CEILING = -1.0

# WHY THE GAIN IS NO LONGER CAPPED BY THE PEAK, AND WHAT STOPS IT CLIPPING.
#
# The first version computed `min(wanted, ceiling - true_peak)`: a pure gain
# that could never clip because it refused to be large enough to. That reads
# as a safe trade and it is the wrong one for dialogue. A line whose loudest
# sample is a plosive, a click in the take or a door in the background has no
# headroom, so the SPEECH stays quiet -- the peak is not the voice.
#
# Measured over 120 of Echo-S's own clips: **41 of them, 34%, were held below
# the target by that cap**, median 1.7 dB short and worst 5.2 dB. That is the
# unevenness -- a third of the game's lines sitting audibly under the rest
# while the build reported a tidy median.
#
# So the full gain is applied and a true-peak limiter catches the few samples
# that would have clipped. `alimiter` limits SAMPLE peaks, and an inter-sample
# peak runs higher, so the limiter sits 1 dB below the true-peak ceiling it is
# there to respect. Measured across the worst offenders:
#
#     limiter -1.0 dB   spread 1.1 dB   worst true peak -0.4 dBTP   over
#     limiter -1.5 dB   spread 1.3 dB   worst true peak -0.9 dBTP   over
#     limiter -2.0 dB   spread 1.5 dB   worst true peak -1.4 dBTP   OK
#
# -2.0 it is: the only one that actually keeps the ceiling, and 1.5 dB of
# residual spread against the 5.3 dB the cap was leaving.
#
# It only runs when the gain is POSITIVE. A negative gain lowers every peak on
# its own, so a clip being turned down never meets the limiter at all.
LIMITER_CEILING_DB = -2.0      # dBTP, so a gained clip still cannot clip
SILENCE_FLOOR = -60.0         # below this a clip is silence, not a quiet line
MAX_GAIN_DB = 20.0            # a noise floor lifted 20 dB is loud enough

# WHY THE GAIN IS THEN CHECKED AGAINST THE FILTERED AUDIO.
#
# The limiter does not only touch the samples it holds down: its release keeps
# some gain reduction in place after each one, so a clip with a big peak and a
# quiet voice comes out BELOW the gain it was given. Measured over 120 random
# clips with the limiter in and no correction:
#
#     median 0.10 dB short    p90 0.80 dB    worst 4.60 dB
#     27% short by more than 0.3 dB, 4% by more than 1 dB
#
# Far better than the 34%/1.7 dB the cap was leaving, but the tail is exactly
# the kind of line the report was about: `nvdun31/7`, 8.6 dB of gain asked
# for and 4.6 dB of it eaten. So the chain is PROBED -- `ebur128` on the end
# of the real filter chain, decoding but not encoding -- and the gain is
# corrected until the finished clip measures the target.
#
# The correction is a secant step, not a retry: the first probe plus the
# source measurement give the slope of "loudness out per dB in" for THIS clip
# (0.61 for `fship_25/127a`, because most of its gain is going into a
# transient), so the next guess is a solve rather than a nudge. Two steps put
# every clip measured inside the tolerance.
#
# It is not a second gain stage -- one `volume` is still applied to the audio,
# and the limiter still guarantees the ceiling however large it grows. What
# changes is only which number that one `volume` gets.
NORMALIZE_TOLERANCE_DB = 0.3   # closer than this and nobody can hear it
NORMALIZE_SETTLE_STEPS = 2     # probes after the first; each costs a decode


# THE MEASUREMENT BAND, AND WHY IT IS NOT THE WHOLE SIGNAL.
#
# Everything above levels the set by BROADBAND loudness, and by build 348 it
# had succeeded completely: 400 of the 15,787 clips that actually shipped
# measure
#
#     min -16.8  p25 -15.5  median -15.4  p75 -15.3  max -14.6 LUFS
#     spread 2.2 dB, and NOT ONE clip outside +-1.5 dB of the median
#
# and the report was still "some lines are too loud, others far too faint".
# Both of those are true at once, because broadband loudness is not what you
# hear a VOICE at. Measured across those same shipped, already-levelled files:
#
#     broadband RMS                  spread   5.1 dB
#     peak                           spread   7.3 dB
#     speech band 300 Hz - 4 kHz     spread  13.4 dB   <- carries the words
#     energy below 300 Hz            spread  28.0 dB
#     spectral tilt                  spread  38.1 dB
#
# Energy under 300 Hz counts fully towards the loudness number and does
# almost nothing for whether a line is intelligible. The gap between a clip's
# loudness and its speech-band level is fixed by the recording, and a flat
# gain -- which is all this applies -- cannot change it:
#
#     nmkin_3/6   Jessie, "My hero!"      -3.2   <- the level that sounds right
#     chrin_1b/15 same conversation       -2.0
#     chrin_1b/1  same conversation      -11.3
#     chrin_1b/10 same conversation      -13.8
#     chrin_1b/11 same conversation      -14.1
#
# Two lines of ONE conversation, written to the card at the same measured
# loudness, 12 dB apart in the band you hear speech in. Close-miced actors
# (Aerith, Tifa) read loud; a distant or filtered take reads faint. That is
# the unevenness, and no target and no playback gain can touch it, because
# both of those are constants applied to every clip alike.
#
# So the gain is MEASURED THROUGH THE SPEECH BAND. Same 60 clips, encoded for
# real, gain measured through different weightings, then the speech band of
# the RESULT measured:
#
#     measured through                speech band of the result      spread
#     broadband K-weighting (today)   min -21.7 med -17.6 max -15.5  6.2 dB
#     high-pass 150 Hz                min -21.1 med -17.2 max -15.5  5.7 dB
#     high-pass 200 Hz                min -20.7 med -17.0 max -15.6  5.1 dB
#     high-pass 300 Hz                min -20.2 med -16.8 max -15.4  4.8 dB
#     speech band 300 Hz - 4 kHz      min -18.6 med -16.5 max -15.8  2.8 dB
#
# Monotonic, and the last row is the default.
#
# NOTHING IS FILTERED ON THE WAY OUT. The clip is still encoded whole and
# still gets ONE flat gain -- no EQ, no compression, no change of timbre. The
# band exists only to decide what that one number should be. `loudness`
# remains available for the broadband behaviour every build up to 348 had.
SPEECH_BAND_LOW = 300
SPEECH_BAND_HIGH = 4000
SPEECH_FILTER = 'highpass=f=%d,lowpass=f=%d' % (SPEECH_BAND_LOW,
                                                SPEECH_BAND_HIGH)


def _normalization():
    """`(mode, target_db)` -- 'off', 'speech', 'loudness' or 'peak'."""
    raw = os.environ.get(NORMALIZE_ENV, NORMALIZE_DEFAULT).strip()
    if not raw or raw.lower() in ('off', '0', 'none', 'false'):
        return 'off', 0.0
    mode, _, target = raw.partition(':')
    mode = mode.strip().lower()
    if mode not in ('speech', 'loudness', 'peak'):
        raise VoiceEncodeError(
            '%s=%r: the mode must be off, speech, loudness or peak'
            % (NORMALIZE_ENV, raw))
    try:
        value = float(target)
    except ValueError:
        raise VoiceEncodeError('%s=%r: %r is not a level in dB'
                               % (NORMALIZE_ENV, raw, target))
    limit = 0.0 if mode == 'peak' else -1.0
    if not -40.0 <= value <= limit:
        raise VoiceEncodeError('%s=%r: %g is outside the sane range'
                               % (NORMALIZE_ENV, raw, value))
    return mode, value


NORMALIZE_MODE, NORMALIZE_TARGET = _normalization()


def normalization_detail():
    """One line for the build log."""
    if NORMALIZE_MODE == 'off':
        return 'off -- every clip keeps the level the mod shipped it at'
    if NORMALIZE_MODE == 'peak':
        return ('peak, %g dBFS -- every clip\'s loudest sample is made equal'
                % NORMALIZE_TARGET)
    if NORMALIZE_MODE == 'speech':
        return ('SPEECH BAND (%d-%d Hz, EBU R128), %g LUFS -- the level of '
                '%s, which is the line the set is matched to. Broadband '
                'loudness was already flat to 2.2 dB and the speech band '
                'was still 13.4 dB apart; that is the spread you hear'
                % (SPEECH_BAND_LOW, SPEECH_BAND_HIGH, NORMALIZE_TARGET,
                   REFERENCE_LINE))
    return ('broadband loudness (EBU R128), %g LUFS -- the pre-349 behaviour; '
            'levels the whole signal, including energy below 300 Hz that '
            'does not carry speech' % NORMALIZE_TARGET)


_MAX_VOLUME = re.compile(r'max_volume:\s*(-?[0-9.]+) dB')
_INPUT_I = re.compile(r'"input_i"\s*:\s*"(-?[0-9.a-zA-Z]+)"')
_INPUT_TP = re.compile(r'"input_tp"\s*:\s*"(-?[0-9.a-zA-Z]+)"')


def _float_or_none(match):
    """
    The number, or None when there is not one.

    `-inf` IS a number here and must survive: `loudnorm` prints it for pure
    digital silence, and Echo-S ships silent placeholders. Rejecting it would
    file every one of them as "could not measure", which is a different thing
    from "there is nothing to measure" and would put a misleading count in
    the build log. The caller's silence floor catches it, because -inf is
    below every floor.
    """
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    if value != value:                        # NaN
        return None
    return value


def _measure_level(ffmpeg, source):
    """
    What this clip measures at: LUFS in loudness mode, dBFS peak in peak mode.

    Returns `(level, reason)` with the same `reason` contract as `_measure`,
    and `level` None whenever `reason` is set. Split out from `_measure` so
    the settle loop below can work in the units it measures in rather than
    in gains relative to a target.
    """
    if NORMALIZE_MODE == 'off':
        return None, None
    if NORMALIZE_MODE == 'peak':
        chain = 'volumedetect'
    elif NORMALIZE_MODE == 'speech':
        # The band decides the NUMBER. Nothing here is written out, so the
        # clip is not filtered -- see SPEECH_FILTER.
        chain = SPEECH_FILTER + ',loudnorm=print_format=json'
    else:
        chain = 'loudnorm=print_format=json'
    probe = subprocess.run(
        [ffmpeg, '-hide_banner', '-nostdin', '-i', source,
         '-af', chain, '-f', 'null', '-'],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if probe.returncode:
        return None, 'ffmpeg could not measure it'
    text = probe.stderr
    if NORMALIZE_MODE == 'peak':
        peak = _float_or_none(_MAX_VOLUME.search(text))
        if peak is None:
            return None, 'no max_volume in the probe'
        if peak <= SILENCE_FLOOR:
            return None, 'silence'
        return peak, None
    loudness = _float_or_none(_INPUT_I.search(text))
    true_peak = _float_or_none(_INPUT_TP.search(text))
    if loudness is None:
        return None, 'no integrated loudness in the probe'
    if loudness <= SILENCE_FLOOR:
        return None, 'silence'
    # THE PEAK NO LONGER HOLDS THE VOICE DOWN. See LIMITER_CEILING_DB.
    # `true_peak` is still measured because it is what decides whether the
    # limiter will have anything to do, and the build reports it.
    del true_peak
    return loudness, None


def _clamped(gain):
    return max(-MAX_GAIN_DB, min(MAX_GAIN_DB, gain))


def _measure(ffmpeg, source):
    """
    The gain this clip needs, in dB, measured rather than assumed.

    Returns `(gain, reason)`; `reason` is None when the measurement was used
    and a short string when it was not, so the build can say how many clips
    it could not measure instead of silently shipping them at a level nobody
    chose. A clip that cannot be measured is shipped UNCHANGED -- a line at
    the wrong volume is better than a line that is missing.
    """
    level, why = _measure_level(ffmpeg, source)
    if level is None:
        return 0.0, why
    return _clamped(NORMALIZE_TARGET - level), None


_unmeasured = 0
# (clips settled, clips that needed a correction, clips still outside the
# tolerance when the step budget ran out). The third number is the one worth
# reading: it is how many lines this build knows are not at the target.
_settled = [0, 0, 0]


def settle_counts():
    """`(probed, corrected, still off target)` for the build log."""
    return tuple(_settled)


def unmeasured():
    """How many clips this build could not measure."""
    return _unmeasured


# The hardware-proven stream shape. LEAD_IN_MS is the decoder's own discard,
# measured; the sample rate and channel count are what the native player
# accepts and are NOT settings -- get either wrong and the line is silent.
LEAD_IN_MS = 100
TARGET_SAMPLE_RATE = 48000
TARGET_CHANNELS = 2

# WHERE THE LOOP POINT GOES, AND WHY IT IS BACK AT ZERO.
# ======================================================
# The native player needs loop metadata at all -- without a LOOPSTART comment
# it does not take the decoder route these clips are staged for. Every build up
# to log 333 shipped `LOOPSTART=0` and the log called it, in those words, "the
# only shape the native player is safe with".
#
# Build 458 moved it to the final sample (`LOOPSTART=<granule-1>`,
# `LOOPLENGTH=1`) so that a player left alive across a field transition would
# loop one inaudible sample instead of replaying the sentence. **That build is
# marked WITHDRAWN in its own document.** On hardware the line still restarted,
# the conclusion drawn was that NativeOggPlayer treats the tag as a loop
# boolean, and build 459 solved the transition properly in `ff7nx_voice` by
# latching the player at MAPJUMP.
#
# What build 459 did not do was put the tag back. It changed only
# `ff7nx_voice.py` and `build.py` and said "no voice re-encoding is required",
# so the withdrawn tag stayed in this file and has been on every clip since log
# 334. That is the whole of the difference between the voice set that worked
# and the voice set that does not.
#
# The tail tag is not inert. A loop point at the LAST decoded sample asks the
# player to seek into the final Ogg page, and whether that lands inside a
# packet or past the end of the stream depends on where that clip's pages
# happen to fall -- which is why the failure is per-clip rather than total:
# some lines play, some replay from the beginning, some wedge the stream
# thread. Zero is the one loop target that is a valid seek in every stream.
#
# `SEVENTH_NX_VOICE_LOOPTAG=tail` restores the withdrawn shape for A/B use. It
# is part of the cache key, so switching costs one re-encode and switching back
# costs none.
LOOP_TAG_ENV = 'SEVENTH_NX_VOICE_LOOPTAG'
LOOP_TAG_ZERO = 'zero'
LOOP_TAG_TAIL = 'tail'


def _loop_tag_mode():
    raw = (os.environ.get(LOOP_TAG_ENV, '') or '').strip().lower()
    if not raw:
        return LOOP_TAG_ZERO
    if raw not in (LOOP_TAG_ZERO, LOOP_TAG_TAIL):
        raise VoiceEncodeError('%s=%r is not %r or %r'
                               % (LOOP_TAG_ENV, raw, LOOP_TAG_ZERO,
                                  LOOP_TAG_TAIL))
    return raw


LOOP_TAG_MODE = _loop_tag_mode()


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
# V5 adds the NORMALISATION to the key. The gain itself is per clip and
# measured from the source, and the source bytes are already in the key, so
# the mode and the target are all that need to be here -- change either and
# every clip re-encodes exactly once; change it back and none do.
# V6 puts the LOOP TAG in the key. V4 and V5 both shipped the withdrawn
# build-458 tail loop, and a cache holding those must not answer a build that
# wants the proven `LOOPSTART=0` back -- which is exactly how the withdrawn
# shape survived build 459 in the first place.
# V7 replaces the peak CAP with a peak LIMITER, and settles the gain against
# the limited audio instead of trusting it. V4..V6 all shipped the cap, which
# left a third of the set below the target, so a build that wants the limiter
# must not be handed one of their entries. The tolerance is in the key
# because it is what "at the target" means.
RECIPE = ('ECHO-VOICE-V7 adelay=%d ar=%d ac=%d vorbis %s loop=%s norm=%s'
          % (LEAD_IN_MS, TARGET_SAMPLE_RATE, TARGET_CHANNELS, TARGET_BITRATE,
             LOOP_TAG_MODE,
             'off' if NORMALIZE_MODE == 'off'
             else '%s%s:%g:lim%g:settle%g/%d'
             % (NORMALIZE_MODE,
                '%d-%d' % (SPEECH_BAND_LOW, SPEECH_BAND_HIGH)
                if NORMALIZE_MODE == 'speech' else '',
                NORMALIZE_TARGET, LIMITER_CEILING_DB,
                NORMALIZE_TOLERANCE_DB, NORMALIZE_SETTLE_STEPS)))


# THE LEGACY MIGRATION IS A RETAG, SO IT IS ONLY VALID FOR A TAG CHANGE.
#
# `LEGACY_RECIPE` is V3. V3 -> V4 changed the loop COMMENTS and nothing else,
# so lifting a V3 entry into the new path with `_publish_final_sample_loop`
# -- rewriting its tags, keeping its audio -- was exactly right, and it saved
# 15,505 re-encodes.
#
# V5 changes the AUDIO: it applies a measured per-clip gain. Taking the same
# shortcut then publishes V3 audio under a V5 key, and the feature silently
# does nothing. That is precisely what build 482 shipped:
#
#     0 encoded, 15505 taken from the cache; 15505 linked into sdout
#     loudness: loudness (EBU R128), -15.3 LUFS ...
#
# -- a loudness line over a set of clips that had not been touched. Measured
# afterwards: every V5 cache entry was byte-for-byte its V4 entry.
#
# So the shortcut is allowed only while the current recipe is audio-identical
# to the legacy one, which is exactly when normalisation is off. `RECIPE`
# carries `norm=off` then, so the two differ in tags alone and the retag is
# sound again.
LEGACY_IS_AUDIO_IDENTICAL = (NORMALIZE_MODE == 'off')

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'cache', '_voice_ogg')


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


def loop_comments(data, mode=None):
    """The LOOP* comments this build ships, for a finished stream."""
    mode = LOOP_TAG_MODE if mode is None else mode
    if mode == LOOP_TAG_TAIL:
        return [b'LOOPSTART=' + str(_last_granule(data) - 1).encode('ascii'),
                b'LOOPLENGTH=1']
    return [b'LOOPSTART=0']


def _with_final_sample_loop(data, mode=None):
    """Put the required Ogg loop tag where this build wants it.

    The Switch decoder needs loop metadata to take the route these clips are
    staged for; WHERE the loop points is the question, and `LOOP_TAG_ENV`
    above is the record of why it points at zero. Only the Vorbis comment
    packet and its Ogg CRC change; audio packets are byte-for-byte preserved,
    so this converts a finished clip either way without re-encoding.
    """
    data = bytearray(data)
    wanted = loop_comments(bytes(data), mode)
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

    comments.extend(wanted)
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
        wanted = loop_comments(data)
        missing = [value for value in wanted if value not in comments]
        if missing:
            raise VoiceEncodeError(
                '%s does not carry this build\'s loop tag (%s missing %r)'
                % (path, LOOP_TAG_MODE, missing))
        if LOOP_TAG_MODE == LOOP_TAG_ZERO:
            # A stale tail tag left behind by a build 458-era cache entry is
            # the exact thing this shape exists to remove, so a leftover
            # LOOPLENGTH is a failure, not something to tolerate.
            for value in comments:
                if value.upper().startswith(b'LOOPLENGTH='):
                    raise VoiceEncodeError(
                        '%s still carries %r from the withdrawn tail-loop '
                        'shape' % (path, value))
    return len(data), voicemod._ogg_duration_ms(data)


def verify(path):
    """Verify this build's loop shape."""
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


def _filter_chain(gain):
    """
    The ffmpeg filter chain for one measured gain.

    Order matters twice over:

      * `volume` runs BEFORE `adelay`, so the lead-in silence this pass adds
        can never be part of what was measured;
      * `alimiter` runs AFTER `volume`, so it only ever catches the samples a
        POSITIVE gain pushed too high. A clip being turned down cannot clip,
        so it gets no limiter at all -- which also means the quiet-clip path
        and the loud-clip path are not both paying for the same filter.

    Up to V6 a clip whose peak was already near full scale simply got less
    gain, and 34% of the set landed short of the target because of it (median
    1.7 dB, worst 5.2 dB). That, not the target, is what made the set sound
    uneven. The limiter replaces the cap: every clip now gets the gain its
    LOUDNESS asks for, and the few samples that would have clipped are held
    down individually. See `LIMITER_CEILING_DB`.
    """
    chain = 'adelay=%d:all=1' % LEAD_IN_MS
    if not gain:
        return chain
    if gain > 0:
        return 'volume=%.2fdB,alimiter=limit=%.6f:level=disabled,%s' % (
            gain, 10.0 ** (LIMITER_CEILING_DB / 20.0), chain)
    return 'volume=%.2fdB,%s' % (gain, chain)


_EBUR128_I = re.compile(r'^\s*I:\s*(-?[0-9.]+) LUFS', re.M)


def _probe_chain(ffmpeg, source, chain):
    """
    The integrated loudness `source` would have AFTER `chain`, or None.

    `ebur128` is a pass-through meter, so this is the finished clip's level
    measured on the finished clip's audio -- verified equal to measuring the
    written .ogg afterwards to within 0.1 dB, at a fraction of the cost
    because nothing is encoded. `-f null` throws the audio away.

    It needs `-loglevel info`, which is where `ebur128` prints its summary,
    so the output is noisy; only the last `I:` line is the summary's.
    """
    measured = chain
    if NORMALIZE_MODE == 'speech':
        # THE PROBE MUST MEASURE WHAT THE GAIN WAS MEASURED IN. Probing the
        # broadband level of a speech-band-targeted encode would have the
        # settle loop chase a number nobody asked for, and it would converge
        # -- on the wrong answer.
        measured += ',' + SPEECH_FILTER
    probe = subprocess.run(
        [ffmpeg, '-hide_banner', '-nostdin', '-loglevel', 'info',
         '-i', source, '-af', measured + ',ebur128=peak=true',
         '-f', 'null', '-'],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if probe.returncode:
        return None
    found = _EBUR128_I.findall(probe.stderr)
    if not found:
        return None
    try:
        value = float(found[-1])
    except ValueError:
        return None
    return None if value <= SILENCE_FLOOR else value


def _settled_gain(ffmpeg, source, gain, level):
    """
    `gain`, corrected for whatever the limiter takes back. See
    `NORMALIZE_TOLERANCE_DB`.

    Only loudness mode and only a positive gain: with no gain there is no
    limiter and nothing to correct, and peak mode is measuring the thing it
    is setting. A probe that fails leaves the gain exactly as it was, so this
    can never make a clip worse than the version without it.
    """
    if NORMALIZE_MODE not in ('loudness', 'speech') \
            or gain <= 0.0 or level is None:
        return gain
    _settled[0] += 1
    corrected = False
    # (0, level) is the second point the secant needs and it is free: it is
    # the measurement already taken, the loudness at no gain at all.
    last_gain, last_out = 0.0, level
    for _ in range(1 + NORMALIZE_SETTLE_STEPS):
        out = _probe_chain(ffmpeg, source, _filter_chain(gain))
        if out is None:
            return gain
        short = NORMALIZE_TARGET - out
        if abs(short) <= NORMALIZE_TOLERANCE_DB:
            return gain
        slope = ((out - last_out) / (gain - last_gain)
                 if abs(gain - last_gain) > 1e-6 else 1.0)
        # A slope at or below zero means more gain is not producing more
        # loudness, so there is nothing left to win and pushing harder would
        # only compress it further. Stop on the value that measured best.
        if slope <= 0.05:
            _settled[2] += 1
            return gain
        last_gain, last_out = gain, out
        stepped = _clamped(gain + short / min(1.0, slope))
        if abs(stepped - gain) < 0.05:
            _settled[2] += 1
            return gain
        gain = stepped
        if not corrected:
            corrected = True
            _settled[1] += 1
    _settled[2] += 1
    return gain


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
    # Measure first, then apply ONE gain. The measurement is of the source,
    # before the lead-in silence exists, so the padding cannot drag the
    # loudness down; the filters then run gain-then-delay for the same
    # reason. A clip that cannot be measured is encoded unchanged and
    # counted, never dropped.
    level, unmeasurable = _measure_level(ffmpeg, source)
    if unmeasurable and unmeasurable != 'silence':
        global _unmeasured
        _unmeasured += 1
    gain = 0.0 if level is None else _clamped(NORMALIZE_TARGET - level)
    gain = _settled_gain(ffmpeg, source, gain, level)
    chain = _filter_chain(gain)
    common = [ffmpeg, '-hide_banner', '-nostdin', '-loglevel', 'error', '-y',
              '-i', source,
              '-af', chain,
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
            if LEGACY_IS_AUDIO_IDENTICAL and os.path.isfile(legacy):
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
