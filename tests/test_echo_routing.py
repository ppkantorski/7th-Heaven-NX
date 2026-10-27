"""Regression checks for Echo-S's explicit Switch field-script route.

The ordinary PC-field importer intentionally accepts only safe visual
sections.  Echo-S is the narrow exception: its validated importer owns
section 1, and it must be selected only for the real Echo-S manifest rather
than for every mod with a directory named ``flevel.lgp``.

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


ECHO_XML = '''<?xml version="1.0"?>
<ModInfo>
<ID>09E81530-3F09-46B9-831B-DF431B8F319C</ID><Name>Echo-S</Name>
<ModFolder Folder="Echo-S"/>
<Conditional Folder="UI/ESUI" ActiveWhen="ui = 1"/>
<ConfigOption><Type>Bool</Type><Default>1</Default><ID>ui</ID><Name>UI</Name>
<Option Value="0" Name="Off"/><Option Value="1" Name="On"/></ConfigOption>
</ModInfo>\n'''

OTHER_XML = ECHO_XML.replace('09E81530-3F09-46B9-831B-DF431B8F319C',
                             '00000000-0000-0000-0000-000000000000')


class EchoRouting(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='echo-route-')
        self.cache = os.path.join(self.tmp, 'cache')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def mod(self, xml, paths):
        root = os.path.join(self.cache, 'Echo')
        for rel in paths:
            path = os.path.join(root, *rel.split('/'))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb') as handle:
                handle.write(b'field')
        with open(os.path.join(root, 'mod.xml'), 'w') as handle:
            handle.write(xml)
        iro_path = os.path.join(self.tmp, 'Echo.iro')
        with open(iro_path, 'wb'):
            pass
        mod = build.Mod(iro_path, self.cache)
        mod._load_manifest()
        return mod

    @staticmethod
    def plan(mod):
        return build.build_plan(
            [mod], {mod.filename: mod.manifest.defaults()},
            {'flevel.lgp': {'md1_1'}}, lambda *_args: None)

    def test_real_echo_fields_take_the_dedicated_route(self):
        mod = self.mod(ECHO_XML, ('Echo-S/flevel.lgp/md1_1',
                                  'UI/ESUI/flevel.lgp/md1_1'))
        plan = self.plan(mod)
        self.assertEqual(set(plan.echo_fields), {'md1_1'})
        # A verified live conditional is higher precedence in 7th Heaven.
        self.assertIn(os.path.join('UI', 'ESUI', 'flevel.lgp', 'md1_1'),
                      plan.echo_fields['md1_1'][0])
        self.assertNotIn('flevel.lgp', plan.archive_files)

    def test_another_mod_cannot_gain_script_section_one(self):
        mod = self.mod(OTHER_XML, ('Echo-S/flevel.lgp/md1_1',))
        plan = self.plan(mod)
        self.assertEqual(plan.echo_fields, {})
        self.assertIn('md1_1', plan.archive_files['flevel.lgp'])

    def test_fast_reuse_refuses_a_different_flevel_input_fingerprint(self):
        root = os.path.join(self.tmp, 'sdout')
        dest = os.path.join(root, 'atmosphere', 'contents', build.TITLE_ID,
                            build.ROMFS, build.ARCHIVES['flevel.lgp'])
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, 'wb') as handle:
            handle.write(b'known flevel')
        old_cache = build.ARCHIVE_FP_CACHE
        try:
            build.ARCHIVE_FP_CACHE = os.path.join(self.tmp, 'fp')
            os.makedirs(build.ARCHIVE_FP_CACHE)
            st = os.stat(dest)
            with open(os.path.join(build.ARCHIVE_FP_CACHE, 'flevel.lgp.fp'),
                      'w') as handle:
                handle.write('old-inputs\n%d\n%d\n123456\n'
                             % (st.st_size, st.st_mtime_ns))
            with self.assertRaisesRegex(RuntimeError, 'inputs or build code'):
                build._reuse_existing_flevel(root, 'new-inputs')
        finally:
            build.ARCHIVE_FP_CACHE = old_cache


if __name__ == '__main__':
    unittest.main(verbosity=2)


# ------------------------------------------------- the 38 skipped, BUILD 502
def test_the_skipped_voice_files_are_explained_not_just_counted():
    """
    "38 file(s) skipped as unrecognised" is a question every time it appears
    in a build log. Sorting the same 38 into WHY is an answer, and each of
    these four shapes was checked file by file against the real Echo-S
    release (FINDINGS-502). None is a dialogue line we fail to ship.
    """
    import build

    class Plan:
        pass
    plan = Plan()
    # the clean names that ARE staged, which is what makes a near-miss a
    # duplicate rather than a loss
    plan.voice = [('Voice/%s' % r, '/x/' + r, 'm') for r in (
        'ship_1/40_0.ogg', 'zcoal_3/22.ogg', 'jet/3.ogg',
        'jundoc1a/38a.ogg', 'uutai1/3.ogg', 'gon_wa1/3.ogg')]
    notes = ['skip (unknown field): /x/Voice/nvl_cl/1.ogg',
             'skip (unknown field): /x/Voice/gon_wa/3.ogg',
             'skip (unrecognised): /x/Voice/jundoc1a/_12.ogg',
             'skip (unrecognised): /x/Voice/junin1a/_44a.ogg',
             "skip (unrecognised): /x/Voice/zcoal_3/22'.ogg",
             'skip (unrecognised): /x/Voice/ship_1/40_0 .ogg',
             'skip (unrecognised): /x/Voice/jet/3-2.ogg',
             'skip (unrecognised): /x/Voice/bigwheel/36 1.ogg',
             'skip (unrecognised): /x/Voice/coloin1/yeah thanks.ogg']
    by_reason = {}
    for line in build._explain_voice_skips(notes, plan):
        count, rest = line.strip().split('  ', 1)
        by_reason[rest.split('  (')[0]] = int(count)

    assert by_reason['the folder names no field map'] == 2
    assert by_reason['switched off by the authors (leading underscore)'] == 2
    # a trailing space, an apostrophe and a take number, all of whose clean
    # names are staged -- nothing is lost to any of the three
    assert by_reason['a duplicate of a clip that IS staged'] == 3
    # and only the genuinely unattachable ones are left
    assert by_reason['the name matches no dialogue line'] == 2
    assert sum(by_reason.values()) == len(notes)


def test_a_near_miss_is_only_a_duplicate_when_the_clean_name_is_staged():
    """The whole point of the bucket: `40_0 ` is harmless because `40_0` ships.
    If it did not, that file would be a real loss and must not be filed as a
    duplicate."""
    import build

    class Plan:
        pass
    plan = Plan()
    plan.voice = []                       # nothing staged at all
    line = build._explain_voice_skips(
        ['skip (unrecognised): /x/Voice/ship_1/40_0 .ogg'], plan)[0]
    assert 'matches no dialogue line' in line, line
