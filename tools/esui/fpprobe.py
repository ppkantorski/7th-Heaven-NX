"""tools/esui/fpprobe.py

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
import sys, os, json, importlib.util
sys.path.insert(0, '.')
esui_on = sys.argv[1] == 'on'
spec = importlib.util.spec_from_file_location('g', '7th_heaven_nx.py'); g = importlib.util.module_from_spec(spec)
sys.argv = ['7th_heaven_nx.py', '--cli']
spec.loader.exec_module(g)
import build
SET = '/sessions/' + os.environ['USER'] + '/mnt/7th_heaven_nx/settings.json' if False else os.path.join(os.path.expanduser('~'), 'mnt/7th_heaven_nx/settings.json')
real = build.load_settings
def ls(path):
    d = real(SET)
    d['Enhanced Stock UI-rel2.iro']['enabled'] = esui_on
    return d
build.load_settings = ls
g.SETTINGS = SET
def probe(sdout, expected_fp, log=None, expected_inputs_fp=None):
    print('PROBE', esui_on, 'fp', expected_fp, 'inputs', expected_inputs_fp, flush=True)
    os._exit(0)
build._reuse_existing_flevel = probe
os.environ['SEVENTH_NX_REUSE_FLEVEL'] = '1'
for k in ('SEVENTH_NX_REUSE_BATTLE','SEVENTH_NX_REUSE_MAGIC','SEVENTH_NX_REUSE_WORLD','SEVENTH_NX_REUSE_HIGH','SEVENTH_NX_REUSE_CHAR'):
    os.environ[k] = '1'
g.SDOUT_DIR = os.path.abspath('tools/esui/run/sdout_probe')
g.main()
