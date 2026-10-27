#!/usr/bin/env python3
"""
test_esui.py -- option gating, folder case, and HEXT selection.

    python3 test_esui.py [--mod DIR]

WHAT IT IS FOR
==============
Enhanced Stock UI is the first mod here built almost entirely out of
ALTERNATIVES: 216 declared folders, 35 config options and 162 HEXT exe-patch
files, of which only a couple of dozen belong in any one build. Three things
were wrong for a mod shaped like that.

1. HEXT files were collected from every enabled mod IGNORING option gating.
   Fine for a mod with one; ESUI ships ten mutually exclusive Battle View
   layouts each with its own `hext/all.txt`, and applying all of them writes
   ten layouts over the same addresses with the last one walked winning.

2. Folder names were matched case-sensitively. 7th Heaven runs on Windows,
   so authors are not careful: ESUI declares 71 folders whose case does not
   match what it ships, and FOUR are active at its own defaults --
   `Avatars/Main` against `Avatars/main`, `Battleview/HelpBoxesNone` against
   `BattleView/HelpBoxesNone`. On a case-sensitive volume those resolved to
   nothing, in silence.

3. Section headings in the option list -- `======Battle Menu Options======`,
   a List with one dummy value -- were drawn as one-choice dropdowns. Seven
   of them.

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

import build
import iro

FAIL = []


def check(cond, msg):
    print(('  ok    ' if cond else '  FAIL  ') + msg)
    if not cond:
        FAIL.append(msg)


XML = '''<?xml version="1.0"?>
<ModInfo>
<ID>t</ID><Name>UI</Name>
<ModFolder Folder="Core"/>
<ModFolder Folder="BattleView/Wide" ActiveWhen="view = 0"/>
<ModFolder Folder="BattleView/Narrow" ActiveWhen="view = 1"/>
<ModFolder Folder="Avatars/Main" ActiveWhen="av = 1"/>
<ConfigOption><Type>List</Type><Default>0</Default><ID>Head</ID>
<Name>======Main Options======</Name><Option Value="0" Name="0"/></ConfigOption>
<ConfigOption><Type>List</Type><Default>0</Default><ID>view</ID>
<Name>Battle View</Name>
<Option Value="0" Name="Wide"/><Option Value="1" Name="Narrow"/></ConfigOption>
<ConfigOption><Type>List</Type><Default>0</Default><ID>empty</ID>
<Name>Icon Order</Name></ConfigOption>
<ConfigOption><Type>Bool</Type><Default>1</Default><ID>av</ID>
<Name>Avatars</Name>
<Option Value="0" Name="OFF"/><Option Value="1" Name="ON"/></ConfigOption>
</ModInfo>
'''

# Note the CASE: the manifest says Avatars/Main, the archive ships avatars/main.
TREE = ['Core/hext/base.txt',
        'BattleView/Wide/hext/all.txt',
        'BattleView/Narrow/hext/all.txt',
        'avatars/main/hext/av.txt',
        'loose.hext']


def make(settings):
    root = tempfile.mkdtemp()
    cache = os.path.join(root, 'cache')
    stem = 'UI'
    for rel in TREE:
        p = os.path.join(cache, stem, *rel.split('/'))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, 'w').write('..\\ff7.exe\n\n900000 = 90\n')
    with open(os.path.join(cache, stem, 'mod.xml'), 'w') as f:
        f.write(XML)
    p = os.path.join(root, stem + '.iro')
    open(p, 'wb').write(b'')
    mod = build.Mod(p, cache)
    mod._load_manifest()
    # Start from the mod's own defaults and override, the way the GUI does.
    # iro synthesises a toggle for each ungated ModFolder so the user can
    # exclude it, and those live in defaults() -- a bare dict would leave
    # them unset and the folder would look switched off.
    full = dict(mod.manifest.defaults())
    full.update(settings)
    settings = full
    plan = build.build_plan([mod], {mod.filename: settings}, {},
                            lambda *a, **k: None)
    hext = sorted(os.path.relpath(f, os.path.join(cache, stem))
                  .replace(os.sep, '/') for f, _m in plan.hext_files)
    picked = sorted(r.replace(os.sep, '/')
                    for r, _f, _o in mod.files_for(settings))
    shutil.rmtree(root, ignore_errors=True)
    return hext, picked


print('HEXT follows the option gate\n')
hext, _ = make({'view': 0, 'av': 1})
check('BattleView/Wide/hext/all.txt' in hext,
      'the chosen Battle View is applied')
check('BattleView/Narrow/hext/all.txt' not in hext,
      'and the one NOT chosen is not -- both would write the same addresses')
hext2, _ = make({'view': 1, 'av': 1})
check('BattleView/Narrow/hext/all.txt' in hext2
      and 'BattleView/Wide/hext/all.txt' not in hext2,
      'switching the option switches which one is applied')

print('\nungated HEXT is still always taken\n')
check('loose.hext' in hext,
      'a .hext outside every declared folder has no gate to judge it by and '
      'is kept -- that is where a compatibility flag lives (the 60 FPS mod '
      'keeps its own in a declared folder, so it is gated normally)')
check('Core/hext/base.txt' in hext,
      'and an ungated ModFolder is applied as before')

print('\nfolder names resolve case-insensitively\n')
check('avatars/main/hext/av.txt' in hext,
      'the manifest says Avatars/Main, the archive ships avatars/main, and '
      'it still resolves -- on Windows this never showed up (%s)' % hext)
_h, picked = make({'view': 0, 'av': 1})
check(any('avatars' in p.lower() for p in picked),
      'and its files are picked, not silently dropped')
h_off, _p = make({'view': 0, 'av': 0})
check('avatars/main/hext/av.txt' not in h_off,
      'turning the option off still turns it off -- the case fix does not '
      'defeat the gate')

print('\nsection headings are not settings\n')
man_dir = tempfile.mkdtemp()
with open(os.path.join(man_dir, 'mod.xml'), 'w') as f:
    f.write(XML)
man = iro.Manifest(os.path.join(man_dir, 'mod.xml'))
heads = [o for o in man.options
         if len(o.values) <= 1 and o.name.strip().startswith('=')]
check(len(heads) == 1,
      'a `======...======` entry with one dummy value is recognisable as a '
      'heading (%d found)' % len(heads))
empty = [o for o in man.options if not o.values]
check(len(empty) == 1,
      'and a List with no <Option> at all is recognisable too (%d found)'
      % len(empty))
gui = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        '7th_heaven_nx.py')).read()
check('A SECTION HEADING, not a setting' in gui,
      'the GUI draws headings as dividers rather than one-choice dropdowns')
check('(no choices declared)' in gui,
      'and says so instead of showing an empty dropdown')
shutil.rmtree(man_dir, ignore_errors=True)

mod_dir = None
for i, a in enumerate(sys.argv):
    if a == '--mod' and i + 1 < len(sys.argv):
        mod_dir = sys.argv[i + 1]
if mod_dir and os.path.isdir(mod_dir):
    print('\nagainst the real mod\n')
    man = iro.Manifest(os.path.join(mod_dir, 'mod.xml'))
    act = iro.active_folders(man, man.defaults())
    check(len(man.folders) > 200,
          '%d declared folders' % len(man.folders))
    check(10 < len(act) < 60,
          '%d active at the mod\'s own defaults -- a small fraction, which '
          'is the point of the gate' % len(act))
    heads = [o for o in man.options
             if len(o.values) <= 1 and o.name.strip().startswith('=')]
    check(len(heads) == 7, '%d section headings in the option list' % len(heads))


# ---------------------------------------------------------------------------
# The 60 FPS abort, and what a .text HEXT patch actually does here.
#
# `identify_exe` hashes .text[0:0x3B4639] to prove the code is the build every
# 60 FPS address came from. ESUI writes 1651 patches into that exact range, so
# the first build with it enabled ended:
#
#   ABORT  this exe's code is not the build these patches were derived from.
#   ! 60 FPS: FAILED -- the rest of the build is still valid, but this tree
#     is 30 FPS
#
# The check is about IDENTITY. It now reads the stock exe from the dump while
# patching goes on targeting the HEXT-baked one -- nothing in the 60 FPS exe
# set writes into .text, so they cannot collide.
# ---------------------------------------------------------------------------
print('\nHEXT in .text must not take the 60 FPS set down with it\n')
import hashlib
import ff7nx_60fps as fps
import exe_patch

STOCK = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ff7_en')
NSO = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'exefs', 'main')
if os.path.exists(STOCK) and os.path.exists(NSO):
    import io as _io

    def build_main(exe, identity):
        out = tempfile.mkdtemp()
        buf = _io.StringIO()
        argv = fps.recommended_argv(out, exe=exe, nso=NSO,
                                    exe_identity=identity)
        ok = fps.run(argv, log=lambda s: buf.write(s + '\n'))
        p = os.path.join(out, 'atmosphere', 'contents', '0100A5B00BDC6000',
                         'exefs', 'main')
        h = hashlib.md5(open(p, 'rb').read()).hexdigest() \
            if os.path.exists(p) else None
        shutil.rmtree(out, ignore_errors=True)
        return ok, h, buf.getvalue()

    tmp = tempfile.mkdtemp()
    raw = bytearray(open(STOCK, 'rb').read())
    pe = exe_patch.parse_pe(bytes(raw))
    for va in (0x4B0000, 0x5C0000):
        off = exe_patch.va_to_offset(pe, va)
        raw[off:off + 4] = b'\x90\x90\x90\x90'
    hexted = os.path.join(tmp, 'ff7_en_hext')
    open(hexted, 'wb').write(bytes(raw))

    ok_a, main_a, _t = build_main(STOCK, None)
    check(ok_a and main_a, 'the stock exe builds (%s)' % main_a)
    ok_b, _m, txt_b = build_main(hexted, None)
    check(not ok_b and 'ABORT' in txt_b,
          'and a .text-patched exe still aborts when nothing tells the '
          'patcher where its identity comes from -- the check is doing its '
          'job')
    ok_c, main_c, txt_c = build_main(hexted, STOCK)
    check(ok_c, 'with --exe-identity pointing at the stock exe it builds')
    check(main_c == main_a,
          'and exefs/main comes out IDENTICAL to the stock-exe build (%s) -- '
          'HEXT in .text changes nothing about the 60 FPS set' % main_c)
    shutil.rmtree(tmp, ignore_errors=True)
else:
    print('   ff7_en / exefs/main not beside this script -- skipped')

print('\nand the log says what a .text patch is worth here\n')
b = open(nxpaths.src_file('build.py')).read()
check('exe_identity' in b,
      'build.py passes the stock exe as the identity when it has built one '
      'of its own')
check('this port does not run' in b and 'hext targets' in b,
      'and reports which sections the HEXT patches landed in')


# ---------------------------------------------------------------------------
# A HEXT FILE IS ALL OR NOTHING.
#
# With gating fixed and the abort out of the way, the first real build came
# back "the scaling of the main menu is screwed up and most of it is missing".
# It was: 328 data patches landed and 1776 code patches did not, so the menu
# geometry had moved and the routine that reads it had not. A stock UI at
# least agrees with itself.
# ---------------------------------------------------------------------------
print('\na file that patches code is skipped WHOLE\n')
if os.path.exists(STOCK):
    import exe_patch as _ep
    tmp = tempfile.mkdtemp()
    pe = _ep.parse_pe(open(STOCK, 'rb').read())

    def hext_file(name, vas):
        path = os.path.join(tmp, name)
        with open(path, 'w') as f:
            f.write('..\\ff7.exe\n\n')
            for va in vas:
                f.write('%X = 90\n' % va)
        return path

    # .data lives above .text's end; 0x914B00 is in the flag block the 60 FPS
    # compatibility patch uses, 0x500000 is code.
    code_and_data = hext_file('mixed.txt', [0x500000, 0x914B00])
    data_only = hext_file('dataonly.txt', [0x914B00, 0x914B04])

    out = tempfile.mkdtemp()
    msgs = []
    build.build_exe(STOCK, [code_and_data, data_only], out, msgs.append)
    applied = [m for m in msgs if m.startswith('  applying')]
    check(len(applied) == 1 and 'dataonly' in applied[0],
          'the data-only file is applied and the mixed one is not (%s)'
          % [a.strip() for a in applied])
    check(any('skipped whole' in m for m in msgs),
          'and the log says so, with the count')
    check(any('half a UI patch set is worse than none' in m for m in msgs),
          'and says why its data half went with it')
    shutil.rmtree(out, ignore_errors=True)

    print('\nthe override still exists for experimenting\n')
    os.environ['SEVENTH_NX_HEXT_TEXT'] = 'apply'
    out = tempfile.mkdtemp()
    msgs = []
    build.build_exe(STOCK, [code_and_data, data_only], out, msgs.append)
    applied = [m for m in msgs if m.startswith('  applying')]
    check(len(applied) == 2,
          'SEVENTH_NX_HEXT_TEXT=apply puts both back (%d applied)'
          % len(applied))
    os.environ.pop('SEVENTH_NX_HEXT_TEXT')
    shutil.rmtree(out, ignore_errors=True)
    shutil.rmtree(tmp, ignore_errors=True)
else:
    print('   ff7_en not beside this script -- skipped')


# ---------------------------------------------------------------------------
# Collisions, handled the way 7th Heaven handles them.
#
# HEXT resolves by LAST WRITE WINS, so the order decides which mod's value
# survives. 7H applies mods in priority order and a mod's folders in manifest
# declaration order. This used to walk the filesystem -- neither of those --
# so with 45 files the winner of any overlap depended on directory order and
# was not reproducible between machines. ESUI overlaps itself on 726 bytes.
# ---------------------------------------------------------------------------
print('\nHEXT order is deterministic and follows the manifest\n')
ORDER_XML = '''<?xml version="1.0"?>
<ModInfo>
<ID>t</ID><Name>UI</Name>
<ModFolder Folder="Zzz"/>
<ModFolder Folder="Aaa"/>
<ConfigOption><Type>Bool</Type><Default>1</Default><ID>x</ID><Name>x</Name>
<Option Value="0" Name="OFF"/><Option Value="1" Name="ON"/></ConfigOption>
</ModInfo>
'''


def order_run():
    root = tempfile.mkdtemp()
    cache = os.path.join(root, 'cache')
    for rel in ('Zzz/hext/z.txt', 'Aaa/hext/a.txt', 'base.hext'):
        p = os.path.join(cache, 'UI', *rel.split('/'))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, 'w').write('..\\ff7.exe\n\n914B00 = 01\n')
    with open(os.path.join(cache, 'UI', 'mod.xml'), 'w') as f:
        f.write(ORDER_XML)
    ip = os.path.join(root, 'UI.iro')
    open(ip, 'wb').write(b'')
    mod = build.Mod(ip, cache)
    mod._load_manifest()
    plan = build.build_plan([mod], {mod.filename: mod.manifest.defaults()},
                            {}, lambda *a, **k: None)
    out = [os.path.relpath(f, os.path.join(cache, 'UI')).replace(os.sep, '/')
           for f, _m in plan.hext_files]
    shutil.rmtree(root, ignore_errors=True)
    return out


a, b = order_run(), order_run()
check(a == b, 'the same inputs give the same order twice (%s)' % a)
check(a and a[0] == 'base.hext',
      'a file outside every declared folder goes FIRST -- it is the mod\'s '
      'baseline and a folder the user chose should be able to override it '
      '(%s)' % a)
check(a[1:] == ['Zzz/hext/z.txt', 'Aaa/hext/a.txt'],
      'and the folders follow MANIFEST declaration order (Zzz then Aaa), not '
      'alphabetical or filesystem order (%s)' % a[1:])

print('\ncolliding writes are reported, not silent\n')
if os.path.exists(STOCK):
    tmp = tempfile.mkdtemp()
    one = os.path.join(tmp, 'first.txt')
    two = os.path.join(tmp, 'second.txt')
    open(one, 'w').write('..\\ff7.exe\n\n914B00 = 01 02\n')
    open(two, 'w').write('..\\ff7.exe\n\n914B00 = 09\n')
    out = tempfile.mkdtemp()
    msgs = []
    build.build_exe(STOCK, [one, two], out, msgs.append)
    check(any('hext collisions' in m for m in msgs),
          'two files writing the same byte differently is reported')
    check(any('first.txt' in m and 'second.txt' in m for m in msgs),
          'and it names which file overrode which (%s)'
          % [m.strip() for m in msgs if 'overridden' in m])
    check(any('last write wins, as in 7th Heaven' in m for m in msgs),
          'and states the rule being followed')
    shutil.rmtree(out, ignore_errors=True)
    shutil.rmtree(tmp, ignore_errors=True)

print('\n%s' % ('FAILED: %d' % len(FAIL) if FAIL else 'all checks passed'))
sys.exit(1 if FAIL else 0)
