"""
BUILD 905 -- an Echo-S loader that names another field's model record.

tunnel_3 (the Sector 4 subway dead-end with the two Shinra guards): Echo-S
adds a Barret entity and the record `tunnel_1main_ballet.char` (ACGD.HRC)
at index 1. The build used to refuse that loader ("models are keyed per
field") and keep the Switch's three records, so in CHAR order Barret -- the
party leader -- got a guard's model and the second guard asked for model 3
of 3; the scene froze with a guard running in place.

The Switch's char.lgp is keyed by HRC and animation stem, so the loader is
taken whenever every name it loads is present.
"""

import os as _nx_os, sys as _nx_sys  # find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import os
import unittest

import build                                                   # noqa: E402,F401
import echo_s_flevel as EF                                     # noqa: E402
import lgp                                                     # noqa: E402

ROOT = _nx_root
FIELD_DIR = os.path.join(ROOT, 'dump', 'romfs', 'ff7', 'workingdir', 'data',
                         'field')
ECHO = os.path.join(ROOT, 'cache', 'Echo-s', 'Echo-S')
BORROWERS = ('anfrst_2', 'blin671b', 'farm', 'junin2', 'mds6_2',
             'nivgate2', 'nvmkin22', 'tunnel_3')


class BorrowedLoader(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        flevel = os.path.join(FIELD_DIR, 'flevel.lgp')
        char = os.path.join(FIELD_DIR, 'char.lgp')
        if not (os.path.isfile(flevel) and os.path.isfile(char)
                and os.path.isfile(os.path.join(ECHO, 'flevel.lgp',
                                                'tunnel_3'))):
            raise unittest.SkipTest('needs the dump and extracted Echo-S')
        cls.van = lgp.Archive(flevel)
        cls.assets, _added = build._char_asset_names(
            char, {n.lower(): None for n in
                   os.listdir(os.path.join(ECHO, 'char.lgp'))})

    def setUp(self):
        # module-level report lists; another test's refusal must not leak in
        del EF.UNPORTABLE_MODEL_LOADERS[:]
        del EF.BORROWED_MODEL_LOADERS[:]

    def merge(self, field, assets):
        with open(os.path.join(ECHO, 'flevel.lgp', field), 'rb') as h:
            return EF.merge_field_payload(self.van.index[field]['payload'],
                                          h.read(), field_name=field,
                                          char_assets=assets)

    def test_tunnel_3_barret_gets_barret_and_both_guards_a_model(self):
        raw, imported = self.merge('tunnel_3', self.assets)
        self.assertIn(3, imported)
        names = EF.model_loader_names(lgp.split_sections(raw)[2])
        # CHAR order in Echo-S's init: cloud, Barret, gard1, gard2
        self.assertEqual(names, ['tunnel_3main_n_cloud.char',
                                 'tunnel_1main_ballet.char',
                                 'tunnel_3shinra_guard.char',
                                 'tunnel_3shinra_guard.char'])
        self.assertIn('tunnel_3', EF.BORROWED_MODEL_LOADERS)

    def test_every_former_unportable_field_now_ships_echo_s_loader(self):
        for field in BORROWERS:
            raw, imported = self.merge(field, self.assets)
            self.assertIn(3, imported, field)
        self.assertEqual(
            [f for f in BORROWERS if f in EF.UNPORTABLE_MODEL_LOADERS], [])

    def test_a_missing_asset_still_refuses(self):
        assets = set(self.assets) - {'acgd.hrc'}
        raw, imported = self.merge('tunnel_3', assets)
        self.assertNotIn(3, imported)
        with open(os.path.join(ECHO, 'flevel.lgp', 'tunnel_3'), 'rb') as h:
            e3 = EF._sections_and_trailer(
                EF._wrapped_field_raw(h.read()))[0][2]
        self.assertEqual(EF.loader_missing_assets(e3, assets), ['acgd.hrc'])

    def test_no_names_keeps_the_old_refusal(self):
        raw, imported = self.merge('mds6_2', None)
        self.assertNotIn(3, imported)


if __name__ == '__main__':
    unittest.main()
