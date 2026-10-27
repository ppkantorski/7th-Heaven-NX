#!/usr/bin/env python3
"""
test_conditional.py -- <Conditional> RuntimeVar gates and loose-file routing.

    python3 test_conditional.py

WHAT IT IS FOR
==============
[Tsunamods] Wizard Staff ("Modern Spell Names") ships SIX variants of
`kernel/kernel2.bin` -- MSN, "MSN DE", Icon, NT15, NT20, NTIcon -- every one
of them gated `ActiveWhen="Spells = 1"` and told apart only by

    <Conditional Folder="MSN" ActiveWhen="Spells = 1">
      <And>
        <RuntimeVar Var="Byte:0x914B1D" Values="00"/>
        <RuntimeVar Var="Byte:0x719C60" Values="137"/>
      </And>
    </Conditional>

7th Heaven answers those by reading the running game's memory. The variables
are bytes in the ff7 exe -- the flag block mods write to advertise themselves
-- so reading the exe we are about to ship gives the same answer.

Two bugs this covers, both of which made the mod do nothing or the wrong
thing:
  * `kernel2.bin` was routed nowhere. It is not an LGP entry, not a movie and
    not an .ogg, so it fell into `unrecognised`. ff7_en asks for
    `kernel/kernel2.bin` by path.
  * with every conditional folder gated off, `files_for` fell back to "the
    whole mod root is the payload" and emplaced all six variants at once.

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
<ID>t</ID><Name>Wizard Staff</Name>
<Conditional Folder="NTIcon" ActiveWhen="Spells = 1">
  <And><RuntimeVar Var="Byte:0x914B1A" Values="02"/>
       <RuntimeVar Var="Byte:0x913FAE" Values="01"/></And>
</Conditional>
<Conditional Folder="NTIcon" ActiveWhen="Spells = 1">
  <And><RuntimeVar Var="Byte:0x914B1A" Values="01"/>
       <RuntimeVar Var="Byte:0x913FAE" Values="01"/></And>
</Conditional>
<Conditional Folder="NT15" ActiveWhen="Spells = 1">
  <RuntimeVar Var="Byte:0x92229B" Values="10"/>
</Conditional>
<Conditional Folder="NT20" ActiveWhen="Spells = 1">
  <RuntimeVar Var="Byte:0x914B1A" Values="01"/>
</Conditional>
<Conditional Folder="MSN" ActiveWhen="Spells = 1">
  <And><RuntimeVar Var="Byte:0x914B1D" Values="00"/>
       <RuntimeVar Var="Byte:0x719C60" Values="137"/></And>
</Conditional>
<ConfigOption><Type>Bool</Type><Default>1</Default><ID>Spells</ID>
<Name>Spell Names</Name>
<Option Value="0" Name="OFF"/><Option Value="1" Name="ON"/></ConfigOption>
</ModInfo>
'''

VANILLA = {'Byte:0x914B1A': 0, 'Byte:0x913FAE': 0, 'Byte:0x92229B': 14,
           'Byte:0x914B1D': 0, 'Byte:0x719C60': 137}
# New Threat patches the exe, so its flag goes up AND the byte MSN keys on
# stops matching. Flipping only the first would leave both gates true, which
# is legal -- 7th Heaven lets declaration order decide -- but it would not be
# testing what it claims to.
NEW_THREAT_20 = dict(VANILLA, **{'Byte:0x914B1A': 1, 'Byte:0x719C60': 0})


def reader(table):
    return lambda spec: table.get(spec.strip())


def plan_for(settings, read, folders=('MSN', 'NT15', 'NT20', 'NTIcon')):
    root = tempfile.mkdtemp()
    cache = os.path.join(root, 'cache')
    stem = 'WizardStaff'
    for f in folders:
        d = os.path.join(cache, stem, f, 'kernel')
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, 'kernel2.bin'), 'wb').write(f.encode().ljust(64, b'\0'))
        open(os.path.join(d, 'KERNEL.bonez'), 'wb').write(b'\0' * 32)
    with open(os.path.join(cache, stem, 'mod.xml'), 'w') as fh:
        fh.write(XML)
    p = os.path.join(root, stem + '.iro')
    open(p, 'wb').write(b'')
    mod = build.Mod(p, cache)
    mod._load_manifest()
    plan = build.build_plan([mod], {mod.filename: settings}, {},
                            lambda *a, **k: None, runtime_read=read)
    # Read the payloads before the tree goes away, so callers can assert on
    # WHICH variant won without holding the directory open.
    plan.loose_text = {k[1]: open(v[0], 'rb').read().strip(b'\0').decode()
                       for k, v in plan.loose.items()}
    shutil.rmtree(root, ignore_errors=True)
    return plan


print('the gate is parsed, not just its variable names\n')
man_dir = tempfile.mkdtemp()
with open(os.path.join(man_dir, 'mod.xml'), 'w') as fh:
    fh.write(XML)
man = iro.Manifest(os.path.join(man_dir, 'mod.xml'))
check(set(man.folder_conditions) == {'MSN', 'NT15', 'NT20', 'NTIcon'},
      'every Conditional carries a parsed gate (%s)'
      % sorted(man.folder_conditions))
check(man.folder_conditions['NTIcon'][0] == 'or',
      'NTIcon is declared twice and the two gates are OR-ed, as 7th Heaven '
      'treats repeats')
check(iro.evaluate_runtime(man.folder_conditions['MSN'],
                           reader(VANILLA)) is True,
      'MSN matches a vanilla English exe')
check(iro.evaluate_runtime(man.folder_conditions['NT20'],
                           reader(VANILLA)) is False,
      'NT20 does not')
check(iro.evaluate_runtime(man.folder_conditions['NT20'],
                           reader(NEW_THREAT_20)) is True,
      'and NT20 does match when the New Threat flag is set')
check(iro.evaluate_runtime(man.folder_conditions['MSN'],
                           reader(NEW_THREAT_20)) is False,
      'while MSN stops matching, so exactly one variant is left')
check(iro.evaluate_runtime(man.folder_conditions['MSN'],
                           lambda _s: None) is None,
      'an unanswerable variable gives None, not a silent False')
shutil.rmtree(man_dir, ignore_errors=True)

print('\nValues parsing follows 7th Heaven\'s own rules\n')
check(iro._parse_values('137') == ('set', {137}),
      'bare digits are DECIMAL -- 137, not 0x137')
check(iro._parse_values('0x10') == ('set', {16}), '0x-prefixed is hex')
check(iro._parse_values('1,2,3') == ('set', {1, 2, 3}), 'a,b,c is a set')
check(iro._parse_values('2..5') == ('range', (2, 5)), 'a..b is a range')

print('\nrouting: one variant, to the path ff7_en asks for\n')
plan = plan_for({'Spells': 1}, reader(VANILLA))
check(list(plan.loose) == [('kernel', 'kernel2.bin')],
      'exactly one kernel2.bin, held for path resolution (%s)'
      % sorted(plan.loose))
check(plan.loose_text['kernel2.bin'] == 'MSN',
      'and it is the MSN one, not whichever folder happened to be last')
check(not any(f[1].endswith('.bonez') for f in plan.loose),
      'KERNEL.bonez is not routed -- the game never asks for it, and neither '
      'does 7th Heaven serve it')

plan = plan_for({'Spells': 1}, reader(NEW_THREAT_20))
check(plan.loose_text['kernel2.bin'] == 'NT20',
      'with the New Threat flag set the NT20 variant wins instead')

print('\nthe off switch must apply LESS, not more\n')
plan = plan_for({'Spells': 0}, reader(VANILLA))
check(plan.loose == {},
      'Spells=OFF emplaces nothing (%s)' % sorted(plan.loose))
check(plan.total_portable() == 0,
      'and nothing else either -- the "no folders means the whole mod root" '
      'fallback must not fire for a mod whose folders are all gated off')

print('\nwithout an exe, nothing is thrown away\n')
plan = plan_for({'Spells': 1}, None)
check(('kernel', 'kernel2.bin') in plan.loose,
      'a build with no exe still emplaces a variant rather than none')


# ---------------------------------------------------------------------------
# WHERE the file goes. The port does not keep loose data where the PC game
# does: exefs/main carries `%s/data/lang-%s/movies/%s.avi` and a bare
# `lang-en/`, and the operator's dump has battle, field, menu, movies,
# music_ogg, wm and five lang-XX folders under data/ -- and no `kernel`
# folder at all. The first build wrote data/kernel/kernel2.bin, which the
# game never opens, and the mod did nothing for a second round.
# ---------------------------------------------------------------------------
class FakeDump:
    def __init__(self, workingdir):
        self.workingdir = workingdir


def dump_with(paths):
    root = tempfile.mkdtemp()
    wd = os.path.join(root, 'workingdir')
    for p in paths:
        full = os.path.join(wd, *p.split('/'))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        open(full, 'wb').write(b'\0')
    return FakeDump(wd), root


print('\nthe destination is found in the dump, not assumed\n')
d, root = dump_with(['data/lang-en/kernel/kernel2.bin',
                     'data/battle/battle.lgp'])
rel, found = build._loose_destination('kernel', 'kernel2.bin', d, print)
check(rel == 'data/lang-en/kernel/kernel2.bin',
      'a dump that keeps it under lang-en/ gets it back there (%s)' % rel)
check(found is not None, 'and it is reported as found, not assumed')
shutil.rmtree(root, ignore_errors=True)

d, root = dump_with(['data/kernel/kernel2.bin'])
rel, _f = build._loose_destination('kernel', 'kernel2.bin', d, print)
check(rel == 'data/kernel/kernel2.bin',
      'a PC-style layout still works (%s)' % rel)
shutil.rmtree(root, ignore_errors=True)

d, root = dump_with(['data/lang-en/kernel/kernel2.bin',
                     'data/lang-fr/kernel/kernel2.bin',
                     'data/lang-ja/kernel/kernel2.bin'])
rel, _f = build._loose_destination('kernel', 'kernel2.bin', d, print)
check(rel == 'data/lang-en/kernel/kernel2.bin',
      'five languages ship the same filename -- the English one wins, so an '
      'English mod does not overwrite the Japanese text (%s)' % rel)
shutil.rmtree(root, ignore_errors=True)

d, root = dump_with(['data/battle/battle.lgp'])
msgs = []
rel, found = build._loose_destination('kernel', 'kernel2.bin', d, msgs.append)
check(rel == 'data/kernel/kernel2.bin' and found is None,
      'no vanilla copy at all falls back to the PC path (%s)' % rel)
check(any('first thing to check' in m for m in msgs),
      'and says so, loudly, instead of writing a dead file in silence')
shutil.rmtree(root, ignore_errors=True)

rel, found = build._loose_destination('kernel', 'kernel2.bin', None, print)
check(rel == 'data/kernel/kernel2.bin' and found is None,
      'no dump at all is the same fallback')

print('\n%s' % ('FAILED: %d' % len(FAIL) if FAIL else 'all checks passed'))
sys.exit(1 if FAIL else 0)
