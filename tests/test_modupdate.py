"""BUILD 751: mod updates -- stale manifest, cache identity across a
re-extraction, new mods off by default, .iro <-> folder settings carry.

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
import importlib.util, os, struct, sys, tempfile, time, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import build

spec = importlib.util.spec_from_file_location('nx', os.path.join(ROOT, '7th_heaven_nx.py'))
nx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nx)


def write_iro(path, files):
    """Minimal uncompressed IRO (version 0x10000, no flags field)."""
    body = b''
    offs = []
    hdr = 8 + 8
    for name, data in files:
        offs.append(hdr + len(body))
        body += data
    dir_off = hdr + len(body)
    d = b''
    for (name, data), off in zip(files, offs):
        n = name.encode('utf-16-le')
        rec = struct.pack('<H', len(n)) + n + struct.pack('<Iqi', 0, off, len(data))
        d += struct.pack('<H', len(rec) + 2) + rec
    with open(path, 'wb') as f:
        f.write(b'IROS' + struct.pack('<i', 0x10000) + struct.pack('<ii', dir_off, len(files)))
        f.write(body)
        f.write(b'\0\0\0\0' + d)


XML = '<ModInfo><ID>{0}</ID><Name>T</Name><Category>{1}</Category></ModInfo>'


class ModUpdate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mods = os.path.join(self.tmp, 'mods')
        self.cache = os.path.join(self.tmp, 'cache')
        os.makedirs(self.mods)

    def mod(self, cat, extra=b'same', other=b'other'):
        p = os.path.join(self.mods, 'T.iro')
        write_iro(p, [('mod.xml', XML.format('aaaa', cat).encode()),
                      ('a\\keep.bin', extra), ('a\\edit.bin', other)])
        return p

    def test_manifest_is_reread_after_the_iro_is_replaced(self):
        p = self.mod('Miscellaneous')
        m = build.Mod(p, self.cache)
        m.ensure_extracted()
        self.assertEqual(m.manifest.category, 'Miscellaneous')
        time.sleep(1.1)
        self.mod('Media', other=b'other-v2')
        m2 = build.Mod(p, self.cache)
        m2._load_manifest()                   # before any extraction
        self.assertEqual(m2.manifest.category, 'Media')

    def test_unchanged_files_keep_their_cache_identity(self):
        p = self.mod('Media')
        m = build.Mod(p, self.cache)
        m.ensure_extracted()
        keep = os.path.join(m.cache, 'a', 'keep.bin')
        edit = os.path.join(m.cache, 'a', 'edit.bin')
        k0, e0 = os.stat(keep).st_mtime_ns, os.stat(edit).st_mtime_ns
        time.sleep(1.1)
        self.mod('Media', other=b'changed!')
        logs = []
        build.Mod(p, self.cache).ensure_extracted(logs.append)
        self.assertEqual(os.stat(keep).st_mtime_ns, k0)
        self.assertNotEqual(os.stat(edit).st_mtime_ns, e0)
        self.assertEqual(open(edit, 'rb').read(), b'changed!')
        self.assertTrue(any('identical to the previous version' in l for l in logs))

    def test_new_mods_start_off_and_folder_swap_keeps_settings(self):
        class M:
            def __init__(self, fn):
                self.filename = fn
        saved = {'Enhanced Stock UI-rel2.iro': {'enabled': True, 'options': {'x': 1}},
                 'Gone.iro': {'enabled': True}, '__global__': {}}
        mods = [M('Enhanced Stock UI-rel2'), M('Brand New.iro')]
        nx.migrate_saved_mod_keys(saved, mods)
        self.assertTrue(nx.saved_enabled(saved, mods[0]))
        self.assertEqual(saved['Enhanced Stock UI-rel2']['options'], {'x': 1})
        self.assertFalse(nx.saved_enabled(saved, mods[1]))

    def test_an_installed_twin_never_steals_the_entry(self):
        class M:
            def __init__(self, fn):
                self.filename = fn
        saved = {'X.iro': {'enabled': True, 'options': {'a': 1}}}
        mods = [M('X.iro'), M('X')]
        nx.migrate_saved_mod_keys(saved, mods)
        self.assertNotIn('X', saved)
        self.assertFalse(nx.saved_enabled(saved, mods[1]))


if __name__ == '__main__':
    unittest.main()


class ProfileOrder(unittest.TestCase):
    class M:
        def __init__(self, fn):
            self.filename = fn

    def names(self, mods):
        return [m.filename for m in mods]

    def test_saved_order_survives_and_new_mods_go_last(self):
        auto = [self.M(n) for n in ('Double-input-fix', 'Nitpick.iro', 'Cosmo.iro',
                                    'Brand New.iro', 'Echo-s.iro', 'CosmosFMV30.iro')]
        saved = ['Cosmo.iro', 'Nitpick.iro', 'Echo-s.iro', 'CosmosFMV30.iro',
                 'Double-input-fix', 'Gone.iro']
        out = self.names(nx.apply_profile_order(auto, saved))
        self.assertEqual(out, ['Cosmo.iro', 'Nitpick.iro', 'Echo-s.iro',
                               'CosmosFMV30.iro', 'Double-input-fix', 'Brand New.iro'])

    def test_iro_to_folder_keeps_its_place(self):
        auto = [self.M('A.iro'), self.M('Enhanced Stock UI-rel2'), self.M('B.iro')]
        saved = ['B.iro', 'Enhanced Stock UI-rel2.iro', 'A.iro']
        self.assertEqual(self.names(nx.apply_profile_order(auto, saved)),
                         ['B.iro', 'Enhanced Stock UI-rel2', 'A.iro'])

    def test_no_saved_order_is_the_auto_sort(self):
        auto = [self.M('x'), self.M('y')]
        self.assertEqual(self.names(nx.apply_profile_order(auto, [])), ['x', 'y'])


class ReferenceOrder(unittest.TestCase):
    """BUILD 831: supported mods always take data/mod_sources.json's tested
    order, whatever order they were installed in; others follow."""

    class M:
        def __init__(self, filename, mod_id=''):
            self.filename = filename
            self.manifest = type('Mf', (), {'mod_id': mod_id})()

    SOURCES = [{'id': 'a1', 'file': 'Cosmo.iro'},
               {'id': 'b2', 'file': 'Nitpick.iro'},
               {'id': 'c3', 'file': 'Echo-s.iro'},
               {'id': 'd4', 'file': 'ESUI-rel2'}]

    def order(self, mods):
        return [m.filename for m in
                nx.apply_reference_order(mods, self.SOURCES)]

    def test_installed_last_still_takes_its_place(self):
        mods = [self.M('Nitpick.iro'), self.M('Cosmo.iro'),
                self.M('Echo-s.iro')]
        self.assertEqual(self.order(mods),
                         ['Cosmo.iro', 'Nitpick.iro', 'Echo-s.iro'])

    def test_unlisted_mods_follow_in_their_own_order(self):
        mods = [self.M('Z.iro'), self.M('Echo-s.iro'), self.M('Y.iro'),
                self.M('Cosmo.iro')]
        self.assertEqual(self.order(mods),
                         ['Cosmo.iro', 'Echo-s.iro', 'Z.iro', 'Y.iro'])

    def test_folder_for_iro_and_renamed_by_id(self):
        mods = [self.M('Echo-s'), self.M('ESUI-rel2.iro'),
                self.M('my cosmo copy.iro', mod_id='A1')]
        self.assertEqual(self.order(mods),
                         ['my cosmo copy.iro', 'Echo-s', 'ESUI-rel2.iro'])

    def test_real_sources_file_is_the_default(self):
        import ff7nx_download
        files = [s['file'] for s in ff7nx_download.load_sources()]
        mods = [self.M(f) for f in reversed(files)]
        self.assertEqual([m.filename for m in nx.apply_reference_order(mods)],
                         files)
