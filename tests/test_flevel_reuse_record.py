#!/usr/bin/env python3
"""
The flevel cache record, and the loop it used to trap you in.

`_archive_fingerprint` hashes every .py in the project, so any packer edit
invalidates flevel even when nothing reaching flevel changed.
`_archive_inputs_fingerprint` is the other half of the key -- mods, their
files, settings, widescreen -- and `_reuse_existing_flevel` compares it so it
can tell "only our code moved" from "an input changed".

That only works if the record HAS the inputs key. It used to be written in
exactly one place: the branch that rebuilds flevel. A build that merely
CONFIRMED the archive was current took the cache-hit branch and left the
record untouched, so a record without the key never gained one -- and the next
packer edit refused with "flevel inputs or build code changed", whose only
exits were a full rebuild or a blanket rebase that also waves through genuine
input changes.

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


class FlevelReuseRecord(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = build.ARCHIVE_FP_CACHE
        build.ARCHIVE_FP_CACHE = os.path.join(self.tmp, 'fp')
        self.sdout = os.path.join(self.tmp, 'sdout')
        self.dest = os.path.join(
            self.sdout, 'atmosphere', 'contents', build.TITLE_ID, 'romfs',
            'ff7', 'workingdir', 'data', 'field', 'flevel.lgp')
        os.makedirs(os.path.dirname(self.dest), exist_ok=True)
        with open(self.dest, 'wb') as handle:
            handle.write(b'x' * 1024)

    def tearDown(self):
        build.ARCHIVE_FP_CACHE = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_record(self, fp, payload, inputs=None):
        os.makedirs(build.ARCHIVE_FP_CACHE, exist_ok=True)
        size, mtime = build._stat_sig(self.dest)
        path = os.path.join(build.ARCHIVE_FP_CACHE, 'flevel.lgp.fp')
        with open(path, 'w') as handle:
            handle.write('%s\n%d\n%d\n%s\n' % (fp, size, mtime, payload))
            if inputs:
                handle.write('%s%s\n' % (build.INPUTS_FP_TAG, inputs))

    def test_a_record_without_the_key_refuses_a_code_only_change(self):
        """The failure as reported: four lines, no `inputs=`."""
        self._write_record('OLDFP', '12345')
        self.assertIsNone(build._archive_stored_inputs_fp('flevel.lgp'))
        with self.assertRaises(RuntimeError) as caught:
            build._reuse_existing_flevel(self.sdout, 'NEWFP', lambda *_: None,
                                         expected_inputs_fp='INPUTS')
        self.assertIn('flevel inputs or build code changed',
                      str(caught.exception))

    def test_a_confirming_build_arms_the_cheap_path(self):
        """
        A cache HIT proves the combined key matched, so the inputs matched
        too. Recording that is stating something already proven -- and it is
        what stops the next packer edit dead.
        """
        self._write_record('OLDFP', '12345')
        build._archive_cache_store('flevel.lgp', self.dest, 'OLDFP',
                                   '12345', 'INPUTS')
        self.assertEqual(build._archive_stored_inputs_fp('flevel.lgp'),
                         'INPUTS')
        kept = build._reuse_existing_flevel(
            self.sdout, 'NEWFP', lambda *_: None, expected_inputs_fp='INPUTS')
        self.assertEqual(os.path.abspath(kept), os.path.abspath(self.dest))

    def test_a_genuine_input_change_still_refuses(self):
        """The whole reason the guard exists: stale Echo-S scripts."""
        self._write_record('OLDFP', '12345', 'INPUTS')
        with self.assertRaises(RuntimeError) as caught:
            build._reuse_existing_flevel(self.sdout, 'NEWFP',
                                         lambda *_: None,
                                         expected_inputs_fp='DIFFERENT')
        self.assertIn('changed', str(caught.exception))

    def test_reuse_refreshes_the_record_so_it_stays_armed(self):
        self._write_record('OLDFP', '12345', 'INPUTS')
        build._reuse_existing_flevel(self.sdout, 'NEWFP', lambda *_: None,
                                     expected_inputs_fp='INPUTS')
        self.assertEqual(build._archive_stored_inputs_fp('flevel.lgp'),
                         'INPUTS')


if __name__ == '__main__':
    unittest.main()


class RestatKeepsTheInputsKey(unittest.TestCase):
    """
    THE OTHER WAY THE KEY GOT LOST, AND IT IS WORSE THAN THE FIRST.

    Build 435 fixed the cache-HIT branch not writing the key. This is the
    rebuild branch, which does write it -- and then a later pass rewrites the
    archive in place, `_archive_restat` re-records its size and mtime, and
    because `_archive_record` does not return the inputs key that rewrite
    DELETED it.

    So a full build genuinely planted the key and the record ended up without
    one anyway. Measured on the real cache after a successful full build:

        e93916d02578f148a24cf21e4213023ac3feab10
        1540570218
        1789859533341804575
        12012047                 <- four lines, no `inputs=`

    and the next `SEVENTH_NX_REUSE_FLEVEL=1` refused with "flevel inputs or
    build code changed" pointing at a full build that had just succeeded.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old = build.ARCHIVE_FP_CACHE
        build.ARCHIVE_FP_CACHE = os.path.join(self.tmp, 'fp')
        self.archive = os.path.join(self.tmp, 'flevel.lgp')
        with open(self.archive, 'wb') as handle:
            handle.write(b'x' * 64)

    def tearDown(self):
        build.ARCHIVE_FP_CACHE = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _record(self):
        with open(os.path.join(build.ARCHIVE_FP_CACHE, 'flevel.lgp.fp')) as f:
            return f.read()

    def test_restat_after_a_later_pass_keeps_the_inputs_key(self):
        build._archive_cache_store('flevel.lgp', self.archive, 'COMBINED',
                                   '12012047', 'INPUTSKEY')
        self.assertIn('inputs=INPUTSKEY', self._record())

        # a later pass rewrites the archive in place, changing size and mtime
        with open(self.archive, 'wb') as handle:
            handle.write(b'y' * 128)
        build._archive_restat('flevel.lgp', self.archive, why='a later pass')

        self.assertEqual(build._archive_stored_inputs_fp('flevel.lgp'),
                         'INPUTSKEY')
        record = self._record().split('\n')
        self.assertEqual(record[0], 'COMBINED')
        self.assertEqual(record[1], '128')          # the NEW size
        self.assertEqual(record[3], '12012047')     # payload kept

    def test_the_fps_restat_keeps_it_too(self):
        """battle.lgp's own re-record had the identical hole."""
        build._archive_cache_store('battle.lgp', self.archive, 'COMBINED',
                                   '', 'INPUTSKEY')
        with open(self.archive, 'wb') as handle:
            handle.write(b'z' * 96)
        build._archive_fps_restat('battle.lgp', self.archive)
        self.assertEqual(build._archive_stored_inputs_fp('battle.lgp'),
                         'INPUTSKEY')

    def test_a_record_that_never_had_one_is_not_given_a_fake(self):
        build._archive_cache_store('flevel.lgp', self.archive, 'COMBINED',
                                   '1')
        build._archive_restat('flevel.lgp', self.archive)
        self.assertIsNone(build._archive_stored_inputs_fp('flevel.lgp'))
        self.assertNotIn('inputs=', self._record())
