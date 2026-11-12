"""nxdefaults -- BUILD 910: settings left at their default follow the default
when an update changes it.

THE PROBLEM. Every save writes every value, so settings.json cannot tell
"the user chose 500 ms" from "500 ms was the default when they last saved".
When an update changes a default (Auto-Text Advance delay 500 -> 200 ms in
BUILD 907, Echo-S Auto Text Advance off -> on, ...) everyone who had simply
left it alone keeps the old value for good.

THE RECORD. Each save also writes `__defaults__`: for every setting whose
value IS the factory default at that moment -- `__global__` keys, each mod's
on/off and each mod option -- the default it matched:

    "__defaults__": {"version": "1.1.4",
                     "global": {"voice_auto_delay": 200, ...},
                     "mods": {"Echo-s.iro": {"enabled": true,
                                             "options": {"Auto": 1, ...}}}}

Only matches are listed, so it is small (a few KB), and a setting the user
changed is simply absent.

THE REFRESH (on load). A setting is moved to the new default only when all
three hold:
  * the record lists it (it was at the default when last saved),
  * its value is still the recorded one (nobody changed it since -- not the
    GUI, not a hand edit of settings.json),
  * the factory default now differs from the recorded one.
Anything the user picked is never touched.

FILES WITHOUT A RECORD (written by 1.1.4 and older). LEGACY lists the old
defaults known to have changed. A value equal to one of them is treated as
"left at the default" and moved, once; the first save then writes a record.
A user who deliberately picked exactly the old default cannot be told apart
from one who never touched it -- that is the one guess, and it is limited to
the entries below.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

KEY = '__defaults__'
GLOBAL = '__global__'

# Old defaults that later changed, for settings.json files written before the
# record existed. (setting -> the old default values.) Mods: (file name,
# option id) for an option, (file name, None) for the mod's on/off.
# The mod rows are the differences between the 1.0.x releases' data/
# default_mod_settings.json and the current one.
LEGACY_GLOBAL = {
    'voice_auto_delay': (500,),            # BUILD 907: 500 -> 200 ms
    'hd_black_point': (12,),               # BUILD 911: the json still said
                                           # 12 after BUILD 822 made it 11
}
LEGACY_MODS = {
    ('Echo-s.iro', 'Auto'): (0,),          # Auto Text Advance off -> on
    ('Enhanced Stock UI-rel2', 'fdb'): (2,),
    ('Ninostyle Chibi.iro', 'chocobo'): (2,),
    ('[Tsunamods]-Arranged-Soundtrack.iro', None): (True,),   # on -> off
}


def _mods_of(data):
    return {k: v for k, v in data.items()
            if k not in (GLOBAL, KEY) and isinstance(v, dict)}


def stamp(saved, factory, version=''):
    """Write the record into `saved` (edited in place, and returned).

    factory = {'__global__': {key: default}, file name: {'enabled': bool,
    'options': {id: default}}} -- the defaults of the running version."""
    if not isinstance(saved, dict):
        return saved
    fg = factory.get(GLOBAL, {})
    sg = saved.get(GLOBAL, {}) if isinstance(saved.get(GLOBAL), dict) else {}
    rec = {'version': version,
           'global': {k: v for k, v in fg.items()
                      if k in sg and sg[k] == v},
           'mods': {}}
    for fn, entry in _mods_of(saved).items():
        f = factory.get(fn)
        if not isinstance(f, dict):
            continue
        r = {}
        if 'enabled' in entry and 'enabled' in f and \
                bool(entry['enabled']) == bool(f['enabled']):
            r['enabled'] = bool(f['enabled'])
        opts = entry.get('options') if isinstance(entry.get('options'),
                                                  dict) else {}
        o = {k: v for k, v in (f.get('options') or {}).items()
             if k in opts and opts[k] == v}
        if o:
            r['options'] = o
        if r:
            rec['mods'][fn] = r
    saved[KEY] = rec
    return saved


def refresh(saved, factory):
    """Move settings left at an old default to the current one. Edits
    `saved` in place; returns [(label, old, new)] for what changed."""
    if not isinstance(saved, dict):
        return []
    changed = []
    rec = saved.get(KEY)
    fg = factory.get(GLOBAL, {})
    sg = saved.get(GLOBAL)
    if isinstance(rec, dict):
        was_g = rec.get('global') or {}
        was_m = rec.get('mods') or {}
    else:                                   # 1.1.4 or older: LEGACY
        was_g, was_m = {}, {}
        if isinstance(sg, dict):
            for k, olds in LEGACY_GLOBAL.items():
                if k in sg and sg[k] in olds:
                    was_g[k] = sg[k]
        for (fn, opt), olds in LEGACY_MODS.items():
            entry = saved.get(fn)
            if not isinstance(entry, dict):
                continue
            r = was_m.setdefault(fn, {})
            if opt is None:
                if 'enabled' in entry and entry['enabled'] in olds:
                    r['enabled'] = entry['enabled']
            else:
                opts = entry.get('options') or {}
                if opt in opts and opts[opt] in olds:
                    r.setdefault('options', {})[opt] = opts[opt]
    if isinstance(sg, dict):
        for k, was in was_g.items():
            if k in fg and k in sg and sg[k] == was and fg[k] != was:
                sg[k] = fg[k]
                changed.append((k, was, fg[k]))
    for fn, r in was_m.items():
        entry, f = saved.get(fn), factory.get(fn)
        if not isinstance(entry, dict) or not isinstance(f, dict):
            continue
        if 'enabled' in r and 'enabled' in f and 'enabled' in entry and \
                bool(entry['enabled']) == bool(r['enabled']) and \
                bool(f['enabled']) != bool(r['enabled']):
            entry['enabled'] = bool(f['enabled'])
            changed.append(('%s: on' % fn, r['enabled'], f['enabled']))
        opts = entry.get('options')
        fo = f.get('options') or {}
        if not isinstance(opts, dict):
            continue
        for k, was in (r.get('options') or {}).items():
            if k in fo and k in opts and opts[k] == was and fo[k] != was:
                opts[k] = fo[k]
                changed.append(('%s: %s' % (fn, k), was, fo[k]))
    return changed
