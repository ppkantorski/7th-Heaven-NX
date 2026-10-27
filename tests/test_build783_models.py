"""BUILD 783: a mod that replaces a whole battle/summon model keeps its own
texture aspect (Ninostyle's Shiva: 1024x1024 squeezed into vanilla's 256x64).

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install()

import build as B  # noqa: E402


def test_783_owner_files():
    assert B._model_owner('magic.lgp', 'cyvadat.t00') == 'cyvadat.d'
    assert B._model_owner('battle.lgp', 'bmac') == 'bmaa'
    assert B._model_owner('battle.lgp', 'bmaa') == 'bmaa'
    assert B._model_owner('world_us.lgp', 'x.tex') is None


def test_783_only_the_same_mod_owns_the_geometry():
    files = {'cyvadat.d': ('/n/d', 'Nino'), 'cyvadat.t00': ('/n/t', 'Nino'),
             'bmaa': ('/x/aa', 'Other'), 'bmac': ('/n/ac', 'Nino'),
             'ifreetda.t00': ('/s/t', 'SYW')}
    assert B._owns_geometry('magic.lgp', 'cyvadat.t00', 'Nino', files)
    assert not B._owns_geometry('battle.lgp', 'bmac', 'Nino', files)      # skeleton from another mod
    assert not B._owns_geometry('magic.lgp', 'ifreetda.t00', 'SYW', files)  # a repaint
    assert not B._owns_geometry('battle.lgp', 'bmaa', 'Other', files)       # the skeleton itself


def test_783_model_cap(monkeypatch):
    monkeypatch.delenv(B.MODEL_TEX_CAP_ENV, raising=False)
    assert B._model_tex_cap() == 512
    monkeypatch.setenv(B.MODEL_TEX_CAP_ENV, '256')
    assert B._model_tex_cap() == 256
    monkeypatch.setenv(B.MODEL_TEX_CAP_ENV, '9999')
    assert B._model_tex_cap() == 1024
