"""BUILD 763: the title background 7th Heaven would serve (ff7nx_titleart).

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
import tempfile
import unittest

import iro
import ff7nx_titleart as T

ROOT = nxpaths.ROOT
MODS = os.environ.get('SEVENTH_NX_TEST_MODS') or os.path.join(ROOT, 'mods')
ESUI = os.path.join(MODS, 'Enhanced Stock UI-rel2')
ECHO = os.path.join(MODS, 'Echo-s.iro')
need = unittest.skipUnless(os.path.isdir(ESUI) and os.path.isfile(ECHO),
                           'needs the Enhanced Stock UI folder and Echo-s.iro')


class _Mod:
    def __init__(self, path, xml_dir):
        self.path = path
        self.filename = os.path.basename(path)
        self.display_name = self.filename
        if os.path.isdir(path):
            self.manifest = iro.Manifest(os.path.join(path, 'mod.xml'))
        else:
            p = os.path.join(xml_dir, self.filename + '.mod.xml')
            with open(p, 'wb') as f:
                f.write(iro.read_one(path, 'mod.xml'))
            self.manifest = iro.Manifest(p)


@need
class Choose(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.echo = _Mod(ECHO, cls.tmp)
        cls.esui = _Mod(ESUI, cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        os.environ[T.MODE_ENV] = 'mods'

    def tearDown(self):
        os.environ.pop(T.MODE_ENV, None)

    def pick(self, esui_opts, read, echo_on=True):
        mods = ([self.echo] if echo_on else []) + [self.esui]   # later wins
        st = {self.echo.filename: {'Echo-S': 1},
              self.esui.filename: esui_opts}
        p = T.choose(mods, st, read)
        return p and (p['mod'].filename, p['folder'].replace('\\', '/'))

    def test_echo_s_byte_live_gives_esui_echo_variant(self):
        rd = lambda s: 233 if '719C60' in s.upper() else None   # noqa: E731
        self.assertEqual(self.pick({'enhanced': 1, 'start': 1}, rd),
                         ('Enhanced Stock UI-rel2', 'ECHOS/Texture'))
        self.assertEqual(self.pick({'enhanced': 1, 'start': 0}, rd),
                         ('Enhanced Stock UI-rel2', 'ECHOS/Texture'))

    def test_without_echo_s_the_start_option_decides(self):
        rd = lambda s: 137 if '719C60' in s.upper() else None   # noqa: E731
        self.assertEqual(self.pick({'enhanced': 1, 'start': 1}, rd),
                         ('Enhanced Stock UI-rel2', 'Start'))
        # ESUI Start Screen = Stock: Echo-S's own backdrop shows through
        self.assertEqual(self.pick({'enhanced': 1, 'start': 0}, rd),
                         ('Echo-s.iro', 'Echo-S'))

    def test_stock_when_nobody_ships_one(self):
        rd = lambda s: 137 if '719C60' in s.upper() else None   # noqa: E731
        self.assertIsNone(self.pick({'enhanced': 0, 'start': 0}, rd,
                                    echo_on=False))

    def test_sword_mode_ignores_the_options(self):
        os.environ[T.MODE_ENV] = 'sword'
        art = T.SWORD_ART
        try:
            p = T.plan([self.echo, self.esui], {}, None)
            self.assertEqual((p['folder'], p['compose']),
                             ('assets/title', 'art'))           # BUILD 767
            T.SWORD_ART = os.path.join(self.tmp, 'missing.png')
            p = T.plan([self.echo, self.esui], {}, None)    # 763b fallback
            self.assertEqual((p['folder'], p['compose']),
                             ('Start', 'vanilla_layout'))
        finally:
            T.SWORD_ART = art
            os.environ.pop(T.MODE_ENV, None)
            T.CURRENT = None

    def test_sword_lands_on_the_vanilla_sword(self):
        import numpy as np
        from PIL import Image
        import lgp
        import tex
        pic = T.compose_vanilla_layout(
            iro.read_one(ESUI, 'Start\\Menu\\buster_00.DDS'))
        self.assertEqual(pic.shape, (1080, 1440, 4))
        self.assertEqual(int(pic[760:, 1150:, :3].max()), 0)   # no logo
        dump = os.path.join(ROOT, 'dump', 'romfs', 'ff7', 'workingdir',
                            'data', 'menu', 'menu_us.lgp')
        if not os.path.isfile(dump):
            self.skipTest('no dump menu_us.lgp')
        t = tex.parse(lgp.Archive(dump).index['buster.tex']['payload'])
        pal = np.frombuffer(t['palette'], np.uint8).reshape(-1, 4)[:256]
        idx = np.frombuffer(t['pixels'], np.uint8)[:65536].reshape(256, 256)
        van = Image.fromarray(pal[idx][:, :, [2, 1, 0]]).crop((0, 0, 150, 150))
        canvas = Image.new('L', (640, 480))
        canvas.paste(van.convert('L').resize((300, 321), Image.BICUBIC),
                     (185, 180))
        a = np.asarray(canvas, np.float32)
        b = np.asarray(Image.fromarray(pic).convert('L').resize(
            (640, 480), Image.BILINEAR), np.float32)
        a -= a.mean(); b -= b.mean()
        self.assertGreater(float((a * b).sum() / np.sqrt(
            (a * a).sum() * (b * b).sum())), 0.98)

    def test_env_off(self):
        os.environ[T.ENV] = '0'
        try:
            self.assertIsNone(T.plan([self.echo, self.esui], {}, None))
        finally:
            os.environ.pop(T.ENV, None)
        os.environ[T.MODE_ENV] = 'stock'
        try:
            self.assertIsNone(T.plan([self.echo, self.esui], {}, None))
        finally:
            os.environ.pop(T.MODE_ENV, None)


class SwordArt(unittest.TestCase):
    """BUILD 767: `sword` shows the hand-refined art in assets/title."""

    def tearDown(self):
        os.environ.pop(T.MODE_ENV, None)
        T.CURRENT = None

    def test_the_art_ships_and_is_4_3(self):
        from PIL import Image
        self.assertTrue(os.path.isfile(T.SWORD_ART))
        w, h = Image.open(T.SWORD_ART).size
        self.assertLess(abs(w * 3 - h * 4), max(w, h) * 0.01)

    def test_sword_needs_no_mod(self):
        os.environ[T.MODE_ENV] = 'sword'
        p = T.plan([], {}, None)
        self.assertEqual((p['compose'], p['art'], p['nitpick']),
                         ('art', T.SWORD_ART, False))
        self.assertEqual(p['mod'].path, T.SWORD_ART)

    def test_picture_is_the_title_area(self):
        import numpy as np
        pic = T.sword_art_picture()
        self.assertEqual(pic.shape, (T.COMPOSE_H, T.COMPOSE_W, 4))
        self.assertEqual(int(pic[..., 3].min()), 255)
        # the menu text's corner (NEW GAME / Continue?, left of the sword)
        # stays dark
        self.assertLess(float(pic[300:500, 450:900, :3].mean()), 12.0)
        # and it is the art the user handed over, not the ESUI composite
        self.assertGreater(float(pic[..., :3].max()), 200)
        _ = np

    def test_other_shapes_refused(self):
        from PIL import Image
        d = tempfile.mkdtemp()
        try:
            q = os.path.join(d, 'wide.png')
            Image.new('RGB', (1920, 1080)).save(q)
            with self.assertRaises(ValueError):
                T.sword_art_picture(q)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_built_tex(self):
        import tex
        dump = os.path.join(ROOT, 'dump', 'romfs', 'ff7', 'workingdir',
                            'data', 'menu', 'menu_us.lgp')
        if not os.path.isfile(dump):
            self.skipTest('no dump menu_us.lgp')
        os.environ[T.MODE_ENV] = 'sword'
        T.plan([], {}, None)
        d = tempfile.mkdtemp()
        try:
            p = T.build_texture({T.ARCHIVE: dump}, d)
            t = tex.parse(open(p, 'rb').read())
            self.assertEqual((t['width'], t['height'], t['num_palettes']),
                             (256 * T.SCALE, 256 * T.SCALE, 1))
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
