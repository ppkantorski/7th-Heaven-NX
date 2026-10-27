"""
Headless check that the 7th Heaven NX GUI remembers its toggles.

tkinter is stubbed (tkstub/), so this exercises the REAL launch_ui() --
widget construction, trace wiring, the close handler -- without a display.
The three things that were broken are each asserted separately:

  1. toggling a mod writes settings.json immediately
  2. closing the window writes settings.json
  3. a fresh launch reads those values back into the checkbox vars

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
import importlib.util as iu
import json, os, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tkstub'))
sys.path.insert(0, os.getcwd())
import tkinter

MODS = tempfile.mkdtemp()
for name in ('AAA Test Mod.iro', 'BBB Second Mod.iro'):
    open(os.path.join(MODS, name), 'wb').write(b'\x00' * 32)

spec = iu.spec_from_file_location('hnx', os.path.join(os.getcwd(),
                                                     '7th_heaven_nx.py'))
hnx = iu.module_from_spec(spec)
spec.loader.exec_module(hnx)

TMP = tempfile.mkdtemp()
hnx.SETTINGS = os.path.join(TMP, 'settings.json')
hnx.MODS_DIR = MODS

FAIL = []


def check(cond, msg):
    print(('  ok    ' if cond else '  FAIL  ') + msg)
    if not cond:
        FAIL.append(msg)


def launch():
    tkinter.CAPTURED['protocol'].clear()
    tkinter.CAPTURED['checks'] = []
    hnx.launch_ui()
    return tkinter.CAPTURED['protocol'].get('WM_DELETE_WINDOW')


def widgets():
    """(fps_var, {mod filename: enable var}) as the UI actually bound them.

    The 60 FPS box is the only Checkbutton with text; the untexted ones are
    the mod rows, created in discover_mods() order."""
    checks = tkinter.CAPTURED['checks']
    fps = [v for t, v in checks if t == '60/30 FPS Patches'][0]
    rows = [v for t, v in checks if not t]
    order = [m.filename for m in hnx.discover_mods()]
    return fps, dict(zip(order, rows))


def read():
    return json.load(open(hnx.SETTINGS)) if os.path.exists(hnx.SETTINGS) else None


print('session 1 -- toggle things, then close the window\n')
close = launch()
check(close is not None, 'WM_DELETE_WINDOW handler installed')
check(read() is None, 'nothing written while the UI was being constructed')

mods = hnx.discover_mods()
names = [m.filename for m in mods]
check(len(names) == 2, 'both test mods discovered: %s' % names)

# the vars the UI actually bound -- reach them the way a click would
fps, enabled = widgets()

# BUILD 751: mods settings.json does not mention start OFF
check(all(v.get() is False for v in enabled.values()),
      'new mods start unticked')
enabled[names[1]].set(True)                  # tick the second mod
enabled[names[0]].set(False)                 # untick the first mod
saved = read()
check(saved is not None, 'untick wrote settings.json without pressing Build')
check(saved and saved[names[0]]['enabled'] is False,
      'the unticked mod was recorded as disabled')
check(saved and saved[names[1]]['enabled'] is True,
      'the other mod was left enabled')

fps.set(True)
saved = read()
check(saved and saved['__global__']['fps_60'] is True,
      '60 FPS checkbox persisted on toggle')

os.remove(hnx.SETTINGS)                      # prove the close path alone works
close()
saved = read()
check(saved is not None, 'closing the window wrote settings.json')
check(saved and saved[names[0]]['enabled'] is False
      and saved['__global__']['fps_60'] is True,
      'close-path state matches what was on screen')

print('\nsession 2 -- relaunch and see what the checkboxes come up as\n')
launch()
fps2, enabled2 = widgets()
check(enabled2[names[0]].get() is False,
      '%s came back UNTICKED' % names[0])
check(enabled2[names[1]].get() is True,
      '%s came back ticked' % names[1])
if hnx.DUMP is not None and hnx.DUMP.nso:
    check(fps2.get() is True, '60 FPS came back ticked')
else:
    # With no exefs/main present the UI force-clears the box at startup, so
    # there is nothing to come back to. The saved value is still on disk and
    # is NOT clobbered -- the force-clear happens before the trace is armed.
    check(read()['__global__']['fps_60'] is True,
          '60 FPS value kept on disk (box force-cleared: no exefs/main here)')

print('\n%s' % ('FAILED: %d' % len(FAIL) if FAIL else 'all checks passed'))
sys.exit(1 if FAIL else 0)
