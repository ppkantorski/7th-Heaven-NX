"""BUILD 752: ESUI 2.968 pinned to the verified 2.966 layout.

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
import hashlib, json, os, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import build, iro
import ff7nx_esui as E

OLD = os.path.join(ROOT, 'mods', 'Enhanced Stock UI-rel2.iro')
NEW = os.environ.get('ESUI_NEW', os.path.join(os.path.dirname(ROOT), 'UPDATED_MODS',
                                              'Enhanced Stock UI-rel2'))
HAVE = os.path.exists(OLD) and os.path.isdir(NEW)


def state(path, opts):
    m = build.Mod(path, tempfile.mkdtemp())
    m._load_manifest()
    o = dict(m.manifest.defaults())
    o.update(opts)
    E.CURRENT = None
    for c in (E._HEXT_MEM, E._HEXT_SRC, E._SCREEN_PORTS, E._COMPAT):
        c.clear()
    logs = []
    E.CURRENT = E.plan_for(m, o, log=logs.append)
    ports = {s: E.screen_ports(s) for s in E.SCREENS}
    return dict(folders=[f.lower() for f in E.CURRENT['folders']],
                mem=E.hext_bytes(), data=E.esui_data(), widths=E.widths(),
                unc=E.uncovered(),
                ports=repr(sorted((k, sorted(v.items()) if v else v)
                                  for k, v in ports.items())),
                tex={n: {p: f.lower() for p, (f, _r) in v.items()}
                     for n, v in E.texture_sources().items()}), logs


@unittest.skipUnless(HAVE, 'needs ESUI 2.966 in mods/ and 2.968 beside the repo')
class EsuiCompat(unittest.TestCase):
    OPTS = {'enhanced': 1, 'icons': 2, 'menuwidth': 1, 'forcenoaddon': 0,
            'controller': 1, 'controllerorder': 1, 'controllerxinput': 0}

    def check(self, opts):
        a, _ = state(OLD, opts)
        b, logs = state(NEW, opts)
        for k in a:
            self.assertEqual(a[k], b[k], k)
        self.assertTrue(any('pinned to the verified 2.966' in l for l in logs))

    def test_users_layout_is_identical(self):
        self.check(self.OPTS)

    def test_defaults_are_identical(self):
        self.check({})

    def test_the_profile_is_keyed_on_the_new_mod_xml(self):
        import ff7nx_esui_compat as C
        h = hashlib.sha1(iro.read_one(NEW, 'mod.xml')).hexdigest()
        self.assertIn(h, C.PROFILES)
        self.assertEqual(C.PROFILES[h]['pinned_to'], E.VERIFIED_RELEASE)

    def test_the_old_release_is_untouched(self):
        m = build.Mod(OLD, tempfile.mkdtemp())
        m._load_manifest()
        E._COMPAT.clear()
        self.assertIsNone(E.compat(m))


if __name__ == '__main__':
    unittest.main()
