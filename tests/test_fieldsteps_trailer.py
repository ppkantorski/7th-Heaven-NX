#!/usr/bin/env python3
"""
The CMFS trailer pass, and the two ways it interacts badly with a fast-reuse
build if nobody thinks about it.

BOTH OF THESE REACHED A REAL BUILD.

1. THE STALE CACHE RECORD. The archive build records flevel.lgp's (size,
   mtime) the moment it writes it. This pass then rewrites the same file.
   `_reuse_existing_flevel` compares the file against that record and refuses
   when they differ -- correctly -- so every later `SEVENTH_NX_REUSE_FLEVEL=1`
   build rejects the archive this build produced itself:

       ERROR: SEVENTH_NX_REUSE_FLEVEL=1 refused: sdout flevel.lgp no longer
       matches the exact file recorded by the build cache

   The project already solved this shape of problem for battle.lgp after the
   60 FPS pass rewrites it (`_archive_fps_restat`). `_archive_restat` is the
   same move for any pass that rewrites an archive later.

2. THE STACKED TRAILER. A flevel KEPT by fast reuse already carries a trailer
   from the build that made it, and it IS in `produced` despite never having
   been written this run -- so "is it in produced" is exactly the wrong test
   for "is it fresh". Appending a second trailer makes `read_trailer`'s length
   check fail. That is caught by `apply_to_flevel`'s own validation rather
   than shipping, but it disables field footsteps for that build.

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
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import build                                                    # noqa: E402
import ff7nx_fieldsteps_data as FD                              # noqa: E402
import lgp                                                      # noqa: E402

FLEVEL = os.path.join(ROOT, 'dump', 'romfs', 'ff7', 'workingdir', 'data',
                      'field', 'flevel.lgp')


class Trailer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(FLEVEL):
            raise unittest.SkipTest('dump flevel.lgp not available')
        archive = lgp.Archive(FLEVEL)
        cls.names = [e['name'].lower() for e in archive.entries
                     if archive.is_field(e)][:3]

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='cmfs-')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def plan(self):
        return {'fields': {n: {None: (3, 4), 0: (0, 0), 5: (10, 2)}
                           for n in self.names},
                'routes': 3, 'triangles': 2, 'fallbacks': 1}

    def path(self, name):
        return os.path.join(self.tmp, name)

    def trailers(self, archive_path):
        archive = lgp.Archive(archive_path)
        return [FD.read_trailer(archive.decompressed(archive.index[n]))
                for n in self.names]

    def test_a_fresh_flevel_takes_the_trailer(self):
        out = self.path('a.lgp')
        FD.apply_to_flevel(FLEVEL, out, self.plan())
        got = self.trailers(out)
        want = FD._trailer(self.plan()['fields'][self.names[0]])
        self.assertTrue(all(t is not None for t in got))
        self.assertTrue(all(t == want for t in got))

    def test_appending_onto_an_existing_trailer_is_refused(self):
        first = self.path('a.lgp')
        FD.apply_to_flevel(FLEVEL, first, self.plan())
        # This is what the old `built_here` test caused on a reuse build.
        self.assertRaises(ValueError, FD.apply_to_flevel,
                          first, self.path('b.lgp'), self.plan(),
                          replace_existing=False)

    def test_replacing_an_existing_trailer_works_and_is_idempotent(self):
        first = self.path('a.lgp')
        FD.apply_to_flevel(FLEVEL, first, self.plan())
        second = self.path('b.lgp')
        FD.apply_to_flevel(first, second, self.plan(), replace_existing=True)
        want = FD._trailer(self.plan()['fields'][self.names[0]])
        self.assertTrue(all(t == want for t in self.trailers(second)))
        # A third pass must not grow the archive again.
        third = self.path('c.lgp')
        FD.apply_to_flevel(second, third, self.plan(), replace_existing=True)
        self.assertEqual(os.path.getsize(third), os.path.getsize(second))
        self.assertTrue(all(t == want for t in self.trailers(third)))

    def test_existing_trailers_are_verified_before_a_fast_reuse_rewrites(self):
        """A cache-less, already-correct archive must not be recompressed."""
        first = self.path('a.lgp')
        plan = self.plan()
        FD.apply_to_flevel(FLEVEL, first, plan)
        self.assertTrue(build._fieldsteps_archive_matches(first, plan))

        changed = dict(plan)
        changed['fields'] = dict(plan['fields'])
        changed['fields'][self.names[0]] = {None: (3, 5), 0: (0, 0),
                                            5: (10, 2)}
        self.assertFalse(build._fieldsteps_archive_matches(first, changed))


class ProvenanceLogic(unittest.TestCase):
    """`_wrote_flevel` must say NO for an archive the build merely kept."""

    def setUp(self):
        build.KEPT_ARCHIVES.clear()
        self.tmp = tempfile.mkdtemp(prefix='prov-')
        self.dest = os.path.join(self.tmp, 'flevel.lgp')
        with open(self.dest, 'wb') as f:
            f.write(b'x')

    def tearDown(self):
        build.KEPT_ARCHIVES.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_freshly_written_counts_as_written(self):
        self.assertTrue(build._wrote_flevel([self.dest], self.dest))

    def test_kept_by_fast_reuse_does_NOT_count_as_written(self):
        # The exact trap: a reused flevel IS in `produced`.
        build.KEPT_ARCHIVES.add('flevel.lgp')
        self.assertFalse(build._wrote_flevel([self.dest], self.dest))

    def test_absent_from_produced_counts_as_not_written(self):
        self.assertFalse(build._wrote_flevel([], self.dest))


class CacheRestat(unittest.TestCase):
    """`_archive_restat` keeps the fingerprint and payload, moves the sig."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='restat-')
        self.old_cache = build.ARCHIVE_FP_CACHE
        build.ARCHIVE_FP_CACHE = os.path.join(self.tmp, 'fp')
        os.makedirs(build.ARCHIVE_FP_CACHE, exist_ok=True)
        self.dest = os.path.join(self.tmp, 'flevel.lgp')

    def tearDown(self):
        build.ARCHIVE_FP_CACHE = self.old_cache
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, blob):
        with open(self.dest, 'wb') as f:
            f.write(blob)

    def test_the_record_follows_the_rewritten_file(self):
        self.write(b'original')
        build._archive_cache_store('flevel.lgp', self.dest, 'FP123',
                                   payload='12011456')
        before = build._archive_record('flevel.lgp')

        # a later pass rewrites the archive -- as the CMFS trailer pass does
        self.write(b'original plus a trailer')
        stale = build._archive_record('flevel.lgp')
        self.assertNotEqual(int(stale[1]), os.path.getsize(self.dest),
                            'the record should be stale before the restat')

        build._archive_restat('flevel.lgp', self.dest)
        after = build._archive_record('flevel.lgp')
        self.assertEqual(after[0], before[0], 'fingerprint must be kept')
        self.assertEqual(after[3], before[3], 'payload must be kept')
        self.assertEqual(int(after[1]), os.path.getsize(self.dest))

    def test_it_preserves_a_60_fps_signature_if_one_is_present(self):
        self.write(b'original')
        build._archive_cache_store('battle.lgp', self.dest, 'FP', payload='')
        build._archive_fps_restat('battle.lgp', self.dest)
        fps_sig = build._archive_record('battle.lgp')[4]
        self.assertIsNotNone(fps_sig)
        self.write(b'rewritten again')
        build._archive_restat('battle.lgp', self.dest)
        self.assertEqual(build._archive_record('battle.lgp')[4], fps_sig)

    def test_a_missing_record_is_a_no_op_rather_than_a_crash(self):
        self.write(b'x')
        build._archive_restat('never-built.lgp', self.dest)   # must not raise


class CMFSReuseCache(unittest.TestCase):
    """Fast flevel reuse must not immediately rewrite identical trailers."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='cmfs-cache-')
        self.old_cache = build.FIELDSTEPS_FP_CACHE
        build.FIELDSTEPS_FP_CACHE = os.path.join(self.tmp, 'fp')
        self.dest = os.path.join(self.tmp, 'flevel.lgp')
        with open(self.dest, 'wb') as f:
            f.write(b'known post-CMFS archive')
        self.route = {
            'fields': {'field_a': {None: (3, 4), 5: (7, 2)}},
            'routes': 2, 'triangles': 1, 'fallbacks': 1,
        }

    def tearDown(self):
        build.FIELDSTEPS_FP_CACHE = self.old_cache
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cache_accepts_only_the_exact_archive_and_trailers(self):
        build._fieldsteps_cache_store(self.dest, self.route)
        self.assertTrue(build._fieldsteps_cache_ok(self.dest, self.route))

        changed = dict(self.route)
        changed['fields'] = {'field_a': {None: (3, 5), 5: (7, 2)}}
        self.assertFalse(build._fieldsteps_cache_ok(self.dest, changed))

        with open(self.dest, 'wb') as f:
            f.write(b'a different archive')
        self.assertFalse(build._fieldsteps_cache_ok(self.dest, self.route))


if __name__ == '__main__':
    unittest.main(verbosity=2)
