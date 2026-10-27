"""BUILD 756/804: user settings vs developer settings and their defaults.

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
import re
import unittest

import nxsettings as S

ROOT = nxpaths.ROOT


class Modes(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.pop(S.DEV_ENV, None)

    def tearDown(self):
        os.environ.pop(S.DEV_ENV, None)
        if self._env is not None:
            os.environ[S.DEV_ENV] = self._env

    def test_dev_mode_only_with_the_variable(self):
        self.assertFalse(S.dev_mode())
        for v in ('1', 'true', 'YES', 'on'):
            os.environ[S.DEV_ENV] = v
            self.assertTrue(S.dev_mode(), v)
        for v in ('0', '', 'no'):
            os.environ[S.DEV_ENV] = v
            self.assertFalse(S.dev_mode(), v)

    def test_saved_values_build_in_both_modes(self):
        """BUILD 804: the toggle hides rows; it never changes the build."""
        saved = {'gfx_pool_mb': 512, 'texture_cache': 'off', 'scaler': 'xbr'}
        for on in (False, True):
            S.set_dev_override(on)
            g = S.effective_global(saved)
            self.assertEqual(g['gfx_pool_mb'], 512)
            self.assertEqual(g['texture_cache'], 'off')
            self.assertEqual(g['scaler'], 'xbr')
        S.set_dev_override(False)

    def test_locked_fills_only_missing_keys(self):
        g = S.effective_global({'gfx_pool_mb': 512})
        for k, v in S.LOCKED.items():
            if k != 'gfx_pool_mb':
                self.assertEqual(g[k], v, k)
        self.assertEqual(S.effective_global(None), S.LOCKED)

    def test_modified_keys(self):
        self.assertEqual(S.modified_keys(dict(S.LOCKED)), [])
        self.assertEqual(S.modified_keys({}), [])
        self.assertEqual(S.modified_keys({'scaler': 'xbr'}), [])  # user key
        g = dict(S.LOCKED, gfx_pool_mb=512, field_tex_cap=1024)
        self.assertEqual(S.modified_keys(g), ['field_tex_cap', 'gfx_pool_mb'])


class Wiring(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, '7th_heaven_nx.py'), encoding='utf-8') as f:
            self.src = f.read()

    def test_every_saved_global_key_is_user_or_locked(self):
        """A key the GUI saves must be either shown to users or locked, or a
        user build silently follows whatever settings.json happens to hold."""
        block = self.src.split("persist['__global__'] = {")[1].split('}\n')[0]
        keys = set(re.findall(r"'([a-z0-9_]+)':", block))
        derived = {'mod_load_order', 'field_bg_preset'}
        loose = keys - S.USER_KEYS - set(S.LOCKED) - derived
        self.assertFalse(loose, 'neither user nor locked: %s' % sorted(loose))

    def test_locked_values_are_offered_choices(self):
        import ast
        tables = {
            'field_tex_cap': 'FIELD_TEX_CAP_CHOICES',
            'world_tex_cap': 'WORLD_TEX_CAP_CHOICES',
            'battle_bg_tex_cap': 'BATTLE_BG_TEX_CAP_CHOICES',
            'spell_tex_cap': 'SPELL_TEX_CAP_CHOICES',
            'minigame_tex_cap': 'MINIGAME_TEX_CAP_CHOICES',
            'spell_fx_cap': 'SPELL_FX_CAP_CHOICES',
            'field_bg_page_px': 'FIELD_BG_PAGE_PX_CHOICES',
            'field_bg_budget_mb': 'FIELD_BG_BUDGET_CHOICES',
            'field_bg_truecolor_pages': 'FIELD_BG_TRUECOLOR_CHOICES',
            'field_bg_max_pages': 'FIELD_BG_MAX_PAGES_CHOICES',
            'field_bg_replace_only': 'FIELD_BG_REPLACE_ONLY_CHOICES',
            'field_bg_d1_px': 'FIELD_BG_D1_PX_CHOICES',
            'margin_art': 'FIELD_BG_MARGIN_ART_CHOICES',
            'limiter_fps': 'LIMITER_FPS_CHOICES',
            'field_buffer': 'FIELD_BUF_CHOICES',
            'gfx_pool_mb': 'GFX_POOL_CHOICES',
            'texture_cache': 'TEX_CACHE_CHOICES',
            'esui_texscale': 'ESUI_TEXSCALE_CHOICES',
            'hd_black_point': 'BLACK_POINT_CHOICES',
        }
        for key, name in tables.items():
            m = re.search(r'^%s = (\[.*?\n\])' % name, self.src, re.S | re.M)
            self.assertTrue(m, name)
            values = [v for v, _l in ast.literal_eval(m.group(1))]
            self.assertIn(S.LOCKED[key], values, key)

    def test_both_entry_paths_use_the_same_values(self):
        self.assertIn("global_saved = nxsettings.effective_global(", self.src)
        self.assertIn("saved['__global__'] = nxsettings.effective_global(", self.src)
        self.assertIn('return nxsettings.effective_global(', self.src)
        self.assertNotIn('persist_global', self.src)

    def test_toggle_shows_default_or_modified(self):
        self.assertIn('nxsettings.modified_keys(', self.src)
        self.assertIn("'Modified (%d)'", self.src)


if __name__ == '__main__':
    unittest.main()
