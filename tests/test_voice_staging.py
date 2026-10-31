#!/usr/bin/env python3
"""
Echo-S's dialogue clips: what they are called, what shape they are encoded
in, and why the cache is allowed to be trusted.

THE THREE THINGS THAT CAN GO WRONG HERE, ALL SILENTLY
=====================================================
1. A clip gets the wrong NAME. There is no lookup table anywhere -- the
   runtime rebuilds `<field>/<dialog><page>.ogg` from live game state and asks
   the native player for it. A name that is off by one page letter is a line
   that never plays, and nothing reports it.

2. A clip gets the wrong SHAPE. The native player is only safe at 48 kHz
   stereo with loop metadata, and it discards roughly the first 100 ms of that
   decoder route -- hence the silent lead-in. The standards-correct range is
   a final-sample loop, although Switch hardware treats it as a loop boolean;
   MAPJUMP retirement lives in ff7nx_voice. Get the shape wrong and the clip
   is silent or truncated on hardware and fine in desktop players.

3. A clip gets HALF WRITTEN. Two builds encoding into the same target produce
   a file with a valid Vorbis header whose logical stream serial changes
   partway through. The Switch refuses it without a word.

Every one of those failure modes is invisible until you are holding the
console, so each gets an assertion here, and the three `verify` tests build
genuinely broken files rather than asserting against a mock.

These run against the REAL Echo-S release in this project's cache and the
REAL ffmpeg the build would use. If either is missing the tests skip rather
than pretend.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import voicemod as V                                           # noqa: E402
import voice_ogg as VO                                         # noqa: E402

import contextlib                                              # noqa: E402
import importlib                                               # noqa: E402


@contextlib.contextmanager
def _env(name, value):
    """Set one encoder setting and re-read the module under it.

    `LOOP_TAG_MODE`, `LIMITER_CEILING_DB` and `RECIPE` are all computed at
    import, so setting the variable alone would leave the module answering
    with the old shape and the old cache key -- which is the thing under test.
    The reload is in place, so every `VO.` in the tests below sees it.

    `sys.modules[...] = VO` is not redundant and must not become a
    `setdefault`: under pytest's import mode this file can be imported such
    that `sys.modules['voice_ogg']` holds a DIFFERENT module object than the
    `VO` the tests hold. `reload` compares by identity, so it then raises
    "module voice_ogg not in sys.modules" and a setdefault, seeing the key
    already present, would not fix it.
    """
    key = VO.__spec__.name if VO.__spec__ else VO.__name__
    previous = sys.modules.get(key)
    before = os.environ.get(name)
    os.environ[name] = value
    sys.modules[key] = VO
    importlib.reload(VO)
    try:
        yield VO
    finally:
        if before is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = before
        importlib.reload(VO)
        if previous is not None and previous is not VO:
            sys.modules[key] = previous


def _decoded_pcm(case, data):
    """Raw samples, so a metadata-only rewrite can be proved to be one."""
    ffmpeg, _encoder, _codec_args, _oggenc = VO._chosen()
    result = subprocess.run(
        [ffmpeg, '-hide_banner', '-nostdin', '-loglevel', 'error',
         '-i', 'pipe:0', '-f', 's16le', '-'],
        input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    case.assertEqual(result.returncode, 0, result.stderr)
    return result.stdout

ECHO = os.path.join(ROOT, 'cache', 'Echo-s')
VOICE = os.path.join(ECHO, 'VO', 'Voice')
NOCLOUD = os.path.join(ECHO, 'NoCloud')

# Measured against the release in this cache. If a future Echo-S changes
# these, that is worth noticing deliberately rather than discovering as a
# quieter game.
EXPECTED = {'field_line': 13816, 'field_choice': 1419,
            'world_line': 34, 'world_choice': 6,
            'nocloud_tifa_choice': 115, 'nocloud_cid_choice': 115}
EXPECTED_TOTAL = sum(EXPECTED.values())
EXPECTED_SKIPPED = 43


def have_echo():
    return os.path.isdir(VOICE)


def ingest_by_walking():
    notes = []
    entries = list(V.ingest_field_vo(VOICE, log=notes.append))
    entries += list(V.ingest_world_vo(os.path.join(VOICE, '_world'),
                                      log=notes.append))
    entries += list(V.ingest_nocloud_vo(NOCLOUD, log=notes.append))
    return entries, notes


class Naming(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not have_echo():
            raise unittest.SkipTest('Echo-S not extracted')
        cls.entries, cls.notes = ingest_by_walking()

    def test_every_clip_gets_a_distinct_name(self):
        """
        The whole design rests on this. 15,505 clips share one directory tree
        and are addressed by name alone, so a single collision is one line
        permanently playing over another.
        """
        names = [V.folder_voice_key_to_filename(e.key) for e in self.entries]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(names), EXPECTED_TOTAL)

    def test_the_namespace_split_is_what_was_measured(self):
        counts = {}
        for entry in self.entries:
            key = V._NAMES.get((entry.key >> 56) & 0xFF)
            counts[key] = counts.get(key, 0) + 1
        self.assertEqual(counts, EXPECTED)

    def test_unrecognised_files_are_reported_not_absorbed(self):
        """
        43 files have human-readable authoring names that match no dialogue
        line. Skipping them is right; doing it quietly is not, because a
        genuinely mis-ingested file looks exactly the same from outside.
        """
        self.assertEqual(len(self.notes), EXPECTED_SKIPPED)

    def test_layout_follows_ffnx(self):
        names = {V.folder_voice_key_to_filename(e.key) for e in self.entries}
        self.assertIn('cargoin/13a.ogg', names)
        self.assertTrue(any(n.startswith('_world/') for n in names))
        self.assertTrue(any(n.startswith('nocloud/tifa/') for n in names))
        self.assertTrue(any(n.startswith('nocloud/cid/') for n in names))
        # Page letters, not page numbers: FFNx asks for 1a, 1b, 1c...
        self.assertIn('cargoin/1a.ogg', names)

    def test_classify_routes_all_four_shapes(self):
        self.assertEqual(V.classify_voice_path('VO/Voice/cargoin/13a.ogg'),
                         ('field', 'cargoin', '13a.ogg'))
        self.assertEqual(V.classify_voice_path('VO/Voice/_world/39a.ogg')[0],
                         'world')
        self.assertEqual(
            V.classify_voice_path('NoCloud/tifa/Voice/zz2/30_0.ogg'),
            ('nocloud', 'tifa', 'zz2', '30_0.ogg'))
        self.assertEqual(
            V.classify_voice_path('Battle VO/Voice/_battle/char_00/bolt_1.ogg'),
            ('battle', 0, 'bolt_1.ogg'))
        self.assertIsNone(V.classify_voice_path('Sound/sfx/1024.ogg'))
        self.assertIsNone(V.classify_voice_path('Ambient/wind.ogg'))

    def test_the_build_path_and_the_walk_path_agree(self):
        """
        `build.py` uses the `*_from_files` variants over its own file list,
        never the directory walk. Two ingestion paths that disagree would mean
        every test above is testing something the build does not do.
        """
        files = []
        for root in (os.path.join(ECHO, 'VO'), NOCLOUD):
            for dirpath, _dirs, names in os.walk(root):
                for name in names:
                    full = os.path.join(dirpath, name)
                    files.append((os.path.relpath(full, ECHO)
                                  .replace(os.sep, '/'), full))
        from_files = list(V.ingest_field_vo_from_files(files))
        from_files += list(V.ingest_world_vo_from_files(files))
        from_files += list(V.ingest_nocloud_vo_from_files(files))
        self.assertEqual(
            sorted(V.folder_voice_key_to_filename(e.key) for e in from_files),
            sorted(V.folder_voice_key_to_filename(e.key)
                   for e in self.entries))


class Encoding(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not have_echo():
            raise unittest.SkipTest('Echo-S not extracted')
        if not VO.have_ffmpeg():
            raise unittest.SkipTest('ffmpeg not available')
        entries = list(V.ingest_field_vo(VOICE, log=lambda *_a: None))
        cls.entry = next(e for e in entries
                         if V.folder_voice_key_to_filename(e.key)
                         == 'cargoin/13a.ogg')

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='voice-encode-')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_output_is_the_shape_the_player_accepts(self):
        out = os.path.join(self.tmp, 'clip.ogg')
        VO.encode(self.entry.source_path, out)
        with open(out, 'rb') as handle:
            data = handle.read()
        rate, channels = VO._identification(data)
        self.assertEqual(rate, VO.TARGET_SAMPLE_RATE)
        self.assertEqual(channels, VO.TARGET_CHANNELS)
        comments = VO._vorbis_comments(data)
        # The loop tag points at zero, which is what every build up to log 333
        # shipped and what the build log called the only shape the native
        # player is safe with. Build 458 moved it to the final sample and was
        # WITHDRAWN; only its runtime half was reverted, and the tag it left
        # behind is what this asserts is gone.
        self.assertIn(b'LOOPSTART=0', comments)
        final = VO._last_granule(data)
        self.assertNotIn(b'LOOPSTART=' + str(final - 1).encode('ascii'),
                         comments)
        self.assertEqual([value for value in comments
                          if value.upper().startswith(b'LOOPLENGTH=')], [])

    def test_the_withdrawn_tail_loop_is_still_reachable_for_a_comparison(self):
        """`=tail` rebuilds build 458's shape, and it is a distinct cache key."""
        zero_key = VO.cache_path(self.entry.source_path)
        with _env(VO.LOOP_TAG_ENV, 'tail'):
            out = os.path.join(self.tmp, 'tail.ogg')
            VO.encode(self.entry.source_path, out)
            with open(out, 'rb') as handle:
                data = handle.read()
            comments = VO._vorbis_comments(data)
            final = VO._last_granule(data)
            self.assertIn(b'LOOPSTART=' + str(final - 1).encode('ascii'),
                          comments)
            self.assertIn(b'LOOPLENGTH=1', comments)
            self.assertNotEqual(VO.cache_path(self.entry.source_path),
                                zero_key)
            VO.verify(out)
        # ... and back at zero that same file is no longer acceptable, so a
        # tail-tagged cache entry can never be shipped by a zero-tag build.
        with self.assertRaises(VO.VoiceEncodeError):
            VO.verify(out)

    def test_a_tail_tagged_clip_retags_to_zero_without_touching_the_audio(self):
        source = os.path.join(self.tmp, 'tail.ogg')
        with _env(VO.LOOP_TAG_ENV, 'tail'):
            VO.encode(self.entry.source_path, source)
        with open(source, 'rb') as handle:
            tail = handle.read()
        zero = VO._with_final_sample_loop(tail, mode=VO.LOOP_TAG_ZERO)
        self.assertIn(b'LOOPSTART=0', VO._vorbis_comments(zero))
        self.assertEqual(_decoded_pcm(self, tail), _decoded_pcm(self, zero))

    def test_final_sample_retag_changes_no_decoded_audio(self):
        """The fix is metadata-only: every decoded PCM sample stays exact."""
        ffmpeg, _encoder, codec_args, oggenc = VO._chosen()
        if oggenc:
            self.skipTest('this check needs an FFmpeg Vorbis encoder')
        legacy = os.path.join(self.tmp, 'legacy.ogg')
        converted = os.path.join(self.tmp, 'converted.ogg')
        result = subprocess.run(
            [ffmpeg, '-hide_banner', '-nostdin', '-loglevel', 'error', '-y',
             '-i', self.entry.source_path, '-af',
             'adelay=%d:all=1' % VO.LEAD_IN_MS,
             '-ar', str(VO.TARGET_SAMPLE_RATE), '-ac',
             str(VO.TARGET_CHANNELS)] + codec_args +
            ['-b:a', VO.TARGET_BITRATE, '-map_metadata', '-1',
             '-metadata', 'LOOPSTART=0', legacy],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(legacy, 'rb') as handle:
            retagged = VO._with_final_sample_loop(handle.read())
        with open(converted, 'wb') as handle:
            handle.write(retagged)
        decoded = []
        for path in (legacy, converted):
            result = subprocess.run(
                [ffmpeg, '-hide_banner', '-nostdin', '-loglevel', 'error',
                 '-i', path, '-f', 's16le', '-acodec', 'pcm_s16le', '-'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(result.returncode, 0, result.stderr)
            decoded.append(result.stdout)
        self.assertEqual(decoded[0], decoded[1])

    def test_the_lead_in_is_exactly_the_decoder_discard(self):
        """
        100 ms, not 'about 100 ms'. The lead-in exists to absorb what the
        native decoder throws away on this route; too little clips the first
        word, and the only way to know is to measure the file.
        """
        out = os.path.join(self.tmp, 'clip.ogg')
        VO.encode(self.entry.source_path, out)
        with open(self.entry.source_path, 'rb') as handle:
            before = V._ogg_duration_ms(handle.read())
        with open(out, 'rb') as handle:
            after = V._ogg_duration_ms(handle.read())
        self.assertEqual(after - before, VO.LEAD_IN_MS)

    def test_verify_rejects_a_truncated_clip(self):
        out = os.path.join(self.tmp, 'clip.ogg')
        VO.encode(self.entry.source_path, out)
        with open(out, 'rb') as handle:
            data = handle.read()
        with open(out, 'wb') as handle:
            handle.write(data[:len(data) // 2])
        with self.assertRaises(VO.VoiceEncodeError):
            VO.verify(out)

    def test_verify_rejects_a_clip_two_writers_produced(self):
        """
        The failure this check exists for: two builds encoding into the same
        path leave a file with a valid header whose logical stream serial
        changes partway through. Desktop players shrug; the Switch refuses it
        silently. Built here for real by splicing two encodes.
        """
        first = os.path.join(self.tmp, 'a.ogg')
        second = os.path.join(self.tmp, 'b.ogg')
        VO.encode(self.entry.source_path, first)
        # A second encode gets its own random stream serial.
        VO.encode(self.entry.source_path, second)
        with open(first, 'rb') as handle:
            head = handle.read()
        with open(second, 'rb') as handle:
            tail = handle.read()
        if struct.unpack_from('<I', head, 14)[0] == \
                struct.unpack_from('<I', tail, 14)[0]:
            self.skipTest('this ffmpeg uses a deterministic stream serial')
        spliced = os.path.join(self.tmp, 'spliced.ogg')
        with open(spliced, 'wb') as handle:
            handle.write(head[:len(head) // 2])
            handle.write(tail[tail.index(b'OggS', len(tail) // 2):])
        with self.assertRaises(VO.VoiceEncodeError):
            VO.verify(spliced)

    def test_verify_rejects_the_wrong_sample_rate(self):
        ffmpeg, _encoder, codec_args, oggenc = VO._chosen()
        if oggenc:
            self.skipTest('this check needs an FFmpeg Vorbis encoder')
        out = os.path.join(self.tmp, 'wrong.ogg')
        subprocess.run([ffmpeg, '-hide_banner', '-nostdin', '-loglevel',
                        'error', '-y', '-i', self.entry.source_path,
                        '-ar', '44100', '-ac', '2'] + codec_args + [
                        '-metadata', 'LOOPSTART=0', out],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       check=True)
        with self.assertRaises(VO.VoiceEncodeError) as caught:
            VO.verify(out)
        self.assertIn('44100', str(caught.exception))

    def test_verify_rejects_a_missing_loopstart(self):
        ffmpeg, _encoder, codec_args, oggenc = VO._chosen()
        if oggenc:
            self.skipTest('this check needs an FFmpeg Vorbis encoder')
        out = os.path.join(self.tmp, 'noloop.ogg')
        subprocess.run([ffmpeg, '-hide_banner', '-nostdin', '-loglevel',
                        'error', '-y', '-i', self.entry.source_path,
                        '-ar', '48000', '-ac', '2'] + codec_args + [
                        '-map_metadata', '-1', out],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       check=True)
        with self.assertRaises(VO.VoiceEncodeError) as caught:
            VO.verify(out)
        self.assertIn('LOOPSTART', str(caught.exception))


class Cache(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not have_echo():
            raise unittest.SkipTest('Echo-S not extracted')
        if not VO.have_ffmpeg():
            raise unittest.SkipTest('ffmpeg not available')
        entries = list(V.ingest_field_vo(VOICE, log=lambda *_a: None))
        names = {V.folder_voice_key_to_filename(e.key): e for e in entries}
        cls.sample = [names[n] for n in sorted(names)
                      if n.startswith('cargoin/')][:6]

    def setUp(self):
        self.out = tempfile.mkdtemp(prefix='voice-stage-', dir=ROOT)

    def tearDown(self):
        # The staged clips are hard links into the cache; unlinking them here
        # must not disturb the cache entries themselves.
        shutil.rmtree(self.out, ignore_errors=True)

    def test_second_run_encodes_nothing(self):
        first = VO.stage(self.sample, self.out)
        second = VO.stage(self.sample, self.out)
        self.assertEqual(second['encoded'], 0)
        self.assertEqual(second['cached'], len(self.sample))
        self.assertEqual(first['bytes'], second['bytes'])

    def test_legacy_cache_is_retagged_without_reencoding(self):
        """V3 LOOPSTART=0 caches migrate by metadata, not FFmpeg.

        Only while this build's audio is the legacy entry's audio. The
        shortcut rewrites tags and keeps the packets, so it is right for a tag
        change and silently wrong for anything that touches the samples --
        which is how build 482 shipped a loudness line over 15,505 clips it
        had not altered. `LEGACY_IS_AUDIO_IDENTICAL` is that gate, and the
        companion test below holds it shut.
        """
        ffmpeg, _encoder, codec_args, oggenc = VO._chosen()
        if oggenc:
            self.skipTest('this check needs an FFmpeg Vorbis encoder')
        if not VO.LEGACY_IS_AUDIO_IDENTICAL:
            self.skipTest('normalisation is on, so the shortcut is closed')
        old_cache = VO.CACHE
        VO.CACHE = os.path.join(self.out, 'cache')
        try:
            current, legacy = VO._cache_paths(self.sample[0].source_path)
            os.makedirs(os.path.dirname(legacy), exist_ok=True)
            result = subprocess.run(
                [ffmpeg, '-hide_banner', '-nostdin', '-loglevel', 'error',
                 '-y', '-i', self.sample[0].source_path, '-af',
                 'adelay=%d:all=1' % VO.LEAD_IN_MS,
                 '-ar', str(VO.TARGET_SAMPLE_RATE), '-ac',
                 str(VO.TARGET_CHANNELS)] + codec_args +
                ['-b:a', VO.TARGET_BITRATE, '-map_metadata', '-1',
                 '-metadata', 'LOOPSTART=0', legacy],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(result.returncode, 0, result.stderr)

            report = VO.stage(self.sample[:1],
                              os.path.join(self.out, 'staged'))
            self.assertEqual(report['encoded'], 0)
            self.assertEqual(report['cached'], 1)
            self.assertTrue(os.path.isfile(current))
            VO.verify(current)
        finally:
            VO.CACHE = old_cache

    def test_a_recipe_that_changes_the_audio_refuses_the_legacy_shortcut(self):
        """The build-482 defect: a gain that never ran, reported as if it had."""
        with _env(VO.NORMALIZE_ENV, 'loudness:-15.3') as module:
            self.assertFalse(module.LEGACY_IS_AUDIO_IDENTICAL)
        with _env(VO.NORMALIZE_ENV, 'off') as module:
            self.assertTrue(module.LEGACY_IS_AUDIO_IDENTICAL)

    def test_a_restage_into_the_same_place_does_no_work_at_all(self):
        VO.stage(self.sample, self.out)
        again = VO.stage(self.sample, self.out)
        self.assertEqual(again['kept'], len(self.sample))
        self.assertEqual(again['linked'], 0)

    def test_staged_clips_are_copies_not_links(self):
        """BUILD 869: sdout holds real files, never links into cache/."""
        report = VO.stage(self.sample, self.out)
        for path in report['produced']:
            self.assertEqual(os.stat(path).st_nlink, 1, path)

    def test_the_bitrate_is_part_of_the_key(self):
        """
        Changing it must re-encode, and changing it back must not. Getting
        this wrong either ships the old bitrate forever or re-encodes 15,505
        clips on a build that changed nothing.
        """
        source = self.sample[0].source_path
        before = VO.cache_path(source)
        old = VO.TARGET_BITRATE
        try:
            VO.TARGET_BITRATE = '128k'
            VO.RECIPE = VO.RECIPE.replace(old, '128k')
            self.assertNotEqual(VO.cache_path(source), before)
        finally:
            VO.RECIPE = VO.RECIPE.replace('128k', old)
            VO.TARGET_BITRATE = old
        self.assertEqual(VO.cache_path(source), before)

    def test_the_encoder_is_part_of_the_key(self):
        """
        A cache built by FFmpeg's own experimental Vorbis encoder must not be
        reused once libvorbis is installed. Without the encoder in the key
        that machine keeps the weaker audio forever, and nothing says so.
        """
        source = self.sample[0].source_path
        before = VO.cache_path(source)
        real = VO.encoder_id
        try:
            VO.encoder_id = lambda: 'some-other-encoder'
            self.assertNotEqual(VO.cache_path(source), before)
        finally:
            VO.encoder_id = real
        self.assertEqual(VO.cache_path(source), before)

    def test_one_unencodable_clip_does_not_lose_the_other_fifteen_thousand(self):
        """
        THE "IT FAILS WHEN I ASK FOR MORE FIELDS" BUG.

        `stage` used to be a bare `pool.map`, so the first clip that would not
        encode ended the pass and the build reported that nothing was staged.
        FFmpeg's native Vorbis encoder aborts on about one Echo-S clip in two
        hundred, so a build over two fields succeeded and a build over all of
        them failed every time -- and the message blamed the size of the job.

        A clip that cannot be encoded is one silent line. It must never be
        fifteen thousand of them.
        """
        broken = os.path.join(self.out, 'not-audio.ogg')
        os.makedirs(self.out, exist_ok=True)
        with open(broken, 'wb') as handle:
            handle.write(b'this is not an ogg stream at all')
        doomed = V.Entry(self.sample[0].key, duration_ms=0,
                         source_path=broken)
        report = VO.stage(list(self.sample[1:]) + [doomed], self.out)
        self.assertEqual(report['staged'], len(self.sample) - 1)
        self.assertEqual(len(report['failed']), 1)
        self.assertEqual(report['failed'][0][1], broken)
        self.assertTrue(report['produced'])

    def test_a_pass_that_stages_nothing_at_all_still_fails(self):
        """One bad clip is a warning; zero good ones is a broken build."""
        broken = os.path.join(self.out, 'none-of-it-works.ogg')
        os.makedirs(self.out, exist_ok=True)
        with open(broken, 'wb') as handle:
            handle.write(b'not audio')
        doomed = V.Entry(self.sample[0].key, duration_ms=0,
                         source_path=broken)
        with self.assertRaises(VO.VoiceEncodeError):
            VO.stage([doomed], self.out)

    def test_duplicate_keys_stop_the_build(self):
        """
        Never last-wins. Ingestion already collapses FFNx's one legitimate
        duplicate, so anything still colliding is a packaging mistake, and
        picking a winner silently means one line playing over another forever.
        """
        clash = list(self.sample) + [self.sample[0]]
        with self.assertRaises(V.BadArchive):
            VO.stage(clash, self.out)


class Routing(unittest.TestCase):
    """`build.py` must claim voice/ and keep skipping everything else."""

    @classmethod
    def setUpClass(cls):
        if not have_echo():
            raise unittest.SkipTest('Echo-S not extracted')
        import build                                           # noqa: E402
        cls.build = build

    def _plan(self, subdirs):
        root = ECHO

        class _Manifest:
            mod_id = self.build.ECHO_S_MOD_ID
            folders = []
            hext = []
            folder_conditions = {}
            options = []

        class _Mod:
            path = cache = root
            filename = 'Echo-s.iro'
            stem = 'Echo-s'
            display_name = 'Echo-S'
            error = None
            skipped_images = 0

            def __init__(self):
                self.manifest = _Manifest()

            def files_for(self, _s, read=None, log=None):
                picked = []
                for sub in subdirs:
                    base = os.path.join(root, *sub.split('/'))
                    for dirpath, _d, names in os.walk(base):
                        for name in names:
                            full = os.path.join(dirpath, name)
                            picked.append((os.path.relpath(full, root)
                                           .replace(os.sep, '/'), full, None))
                return sorted(picked)

        return self.build.build_plan([_Mod()], {}, {'flevel.lgp': set()},
                                     log=lambda *_a: None)

    def test_voice_is_claimed_and_sfx_is_not(self):
        plan = self._plan(['VO/Voice/cargoin', 'Sound/sfx'])
        self.assertTrue(plan.voice)
        self.assertTrue(all('/voice/' in rel.lower()
                            for rel, _f, _m in plan.voice))
        # sfx keeps going to Cosmo Memory's own collector, untouched.
        self.assertTrue(plan.sfx)
        self.assertNotIn('voice', plan.skipped_ffnx_audio)

    def test_an_unrecognised_shape_under_voice_still_skips(self):
        """
        The claim is narrow on purpose: only paths `classify_voice_path`
        recognises. An unrelated FFNx voice mod with a different folder shape
        must keep skipping rather than being half-ingested.
        """
        plan = self._plan(['VO/Voice/cargoin'])
        claimed = {rel for rel, _f, _m in plan.voice}
        self.assertTrue(claimed)
        for rel in claimed:
            self.assertIsNotNone(V.classify_voice_path(rel))


if __name__ == '__main__':
    unittest.main()
