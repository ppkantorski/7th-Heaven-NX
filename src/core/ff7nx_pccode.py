"""Option folders that are PC x86 code, and so do nothing on the Switch.

Gameplay mods like Postscriptthree's Gameplay Tweaks (PS3Tweaks.iro) are
almost entirely FFNx HEXT: each option folder carries a `hext/` file that
rewrites x86 instructions in ff7.exe, sometimes with a KERNEL.BIN, a field
script chunk or a price table that only makes sense alongside that code.

This port does not run the exe's x86 code. exefs/main is a prebuilt ARM64
recompilation, so a .text patch changes nothing (see build_exe). What used to
happen to such a folder was worse than nothing: its code was skipped file by
file, but its DATA still went in --

  * Item Randomizer: ItemRando.txt skipped, ShopPrices.txt (178 price and
    text writes) applied, so the shop prices changed and nothing else did;
  * Multi-Linked Slots: the code skipped, its KERNEL.BIN (new slot types the
    stock code does not understand) installed over the Switch kernel;
  * Character Order Randomizer: its md1stin field script installed with
    none of the code it hands over to;
  * Enemy Stat Multiplier: x86 routines written into .data caves nothing
    ever calls.

So a declared option folder is treated as ONE unit. It is PC-only when its
HEXT patches .text and everything else in it is game-logic data that the
code reads (.bin/.dat, field `*.chunk.N`) or documentation. Such a folder is
left out WHOLE, along with:

  * declared folders nested inside it with no content of their own
    (CharacterOrderRando/FirstScreen), and
  * data-only HEXT folders of the same mod whose writes land in a cave a
    dropped folder writes (the scaling percentages, the randomizer scaler):
    a mod-private cave is code, wherever the section table says it is.

A folder that ships real content (models, textures, audio, movies) is never
touched here, whatever its HEXT does -- Echo-S's `Echo-S/` folder has code
HEXT next to 900 assets. Mods with a dedicated Switch translation (Enhanced
Stock UI, Double Input Fix, Nitpick, Idle Animations, Echo-S) are exempt
entirely; their HEXT is handled by their own modules.

SEVENTH_NX_HEXT_TEXT=apply (the same switch build_exe honours) turns this
off.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os
import re

import exe_patch
import iro

ENV = 'SEVENTH_NX_HEXT_TEXT'

# .text of the 1.02 ff7.exe every HEXT file targets, as the Switch's own
# ff7_en lays it out (0x401000 + 0x3B5000).
X86_TEXT = (0x401000, 0x7B6000)

ECHO_S_MOD_ID = '09e81530-3f09-46b9-831b-df431b8f319c'

_CHUNK = re.compile(r'\.chunk\.\d+$')
_LOGIC_EXT = ('.bin', '.dat')
_DOC_EXT = ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.txt', '.md', '.pdf',
            '.rtf', '.url')

# Folders whose fix this program already applies itself, by the last
# component of the folder name: what to tell the player instead.
BUILT_IN = {
    'witemfix': 'Built in: Settings > W-Item bug fix',
}

_cache = {}
_xlate = {}
_tool = None
_tool_error = None     # why the translator is unavailable, for the GUI


def _translator():
    """(stock ff7_en bytes, nxmap.Main of the stock main) or None."""
    global _tool, _tool_error
    if _tool is None:
        _tool = False
        try:
            import build
            import ff7nx_hextarm
            import nxmap
            import nxpaths
            if not ff7nx_hextarm.available():
                _tool_error = 'capstone is not installed'
                return None
            dump = build.find_game_dump(nxpaths.ROOT)
            if dump is not None and dump.exe and dump.nso:
                with open(dump.exe, 'rb') as f:
                    exe = f.read()
                _tool = (exe, nxmap.Main(dump.nso))
            else:
                _tool_error = 'needs the full game dump (ff7_en + exefs/main)'
        except BaseException as exc:                           # noqa: BLE001
            _tool = False
            _tool_error = '%s: %s' % (type(exc).__name__, exc)
    return _tool or None


def translate(raw):
    """ARM64 words for one HEXT file's text (ff7nx_hextarm.plan), or None
    when it cannot be translated (or there is no dump to translate with)."""
    if isinstance(raw, bytes):
        raw = raw.decode('utf-8', 'replace')
    if raw in _xlate:
        return _xlate[raw]
    tool = _translator()
    words = None
    if tool is not None:
        import ff7nx_hextarm
        try:
            words = ff7nx_hextarm.plan(tool[0], tool[1], raw)
        except ff7nx_hextarm.Refused:
            words = None
        except Exception:                                      # noqa: BLE001
            words = None
    _xlate[raw] = words
    return words


def groups(raws):
    """BUILD 868: HEXT files that port only together -- a PS3Tweaks option
    and the files layered on it (Two Accessories' ESUICompat; the Enemy
    Randomizer with Double Bosses and the scaling files). raws: [(key,
    text)] of one mod, in load order. Returns [(keys, words)] for each group
    (up to four files, the largest first) whose patches together are one
    ported option; a file in no group is translated alone, as before."""
    import itertools
    import ff7nx_ps3tweaks
    parsed = []
    for key, raw in raws:
        if isinstance(raw, bytes):
            raw = raw.decode('utf-8', 'replace')
        try:
            parsed.append((key, raw, list(exe_patch.parse_hext(raw))))
        except Exception:                                      # noqa: BLE001
            continue
    ports = [set(p.code) | set(p.data) for p in ff7nx_ps3tweaks.PORTS]
    out, used = [], set()
    for n, (ka, ra, pa) in enumerate(parsed):
        if ka in used or not pa or ff7nx_ps3tweaks.match(pa) is None:
            continue
        keys_a = {va for va, _b in pa}
        room = [pk for pk in ports if keys_a <= pk]
        cand = [(j, x) for j, x in enumerate(parsed)
                if x[0] != ka and x[0] not in used and x[2] and
                any({va for va, _b in x[2]} <= pk for pk in room)]
        hit = None
        for size in (3, 2, 1):
            for combo in itertools.combinations(cand, size):
                files = sorted(combo + ((n, parsed[n]),))   # load order
                patches = [p for _j, x in files for p in x[2]]
                if ff7nx_ps3tweaks.match(patches) is None:
                    continue
                words = translate('\n'.join(x[1] for _j, x in files))
                if words:
                    hit = ([ka] + [x[0] for _j, x in files if x[0] != ka],
                           words)
                    break
            if hit:
                break
        if hit:
            out.append((tuple(hit[0]), hit[1]))
            used.update(hit[0])
    return out


def pairable(raws):
    """BUILD 868: the keys of every file in SOME group groups() could make
    of these (a choice of options): each file of a ported option that ports
    only with another (Double Bosses over the Enemy Randomizer), whichever
    of the mod's options are picked with it. raws: as groups()."""
    import itertools
    import ff7nx_ps3tweaks
    parsed = []
    for key, raw in raws:
        if isinstance(raw, bytes):
            raw = raw.decode('utf-8', 'replace')
        try:
            pats = list(exe_patch.parse_hext(raw))
        except Exception:                                      # noqa: BLE001
            continue
        if pats:
            parsed.append((key, pats))
    ports = [set(p.code) | set(p.data) for p in ff7nx_ps3tweaks.PORTS]
    order = {k: n for n, (k, _p) in enumerate(parsed)}
    out = set()
    for ka, pa in parsed:
        if ff7nx_ps3tweaks.match(pa) is None:
            continue
        keys_a = {va for va, _b in pa}
        room = [pk for pk in ports if keys_a <= pk]
        cand = [x for x in parsed if x[0] != ka and
                any({va for va, _b in x[1]} <= pk for pk in room)]
        for size in (1, 2, 3):
            for combo in itertools.combinations(cand, size):
                if all(x[0] in out for x in combo):
                    continue
                files = sorted(list(combo) + [(ka, pa)],    # load order
                               key=lambda x: order[x[0]])
                if ff7nx_ps3tweaks.match([p for _k, x in files
                                          for p in x]) is not None:
                    out.update(k for k, _x in files)
    return out


def disabled():
    return os.environ.get(ENV, '').lower() == 'apply'


def exempt(mod):
    """Mods whose HEXT has a Switch translation of its own."""
    import ff7nx_difix
    import ff7nx_esui
    import ff7nx_idleanim
    import ff7nx_nitpick
    if (getattr(getattr(mod, 'manifest', None), 'mod_id', '') or '').lower() \
            == ECHO_S_MOD_ID:
        return True
    for m in (ff7nx_esui, ff7nx_difix, ff7nx_nitpick, ff7nx_idleanim):
        try:
            if m.handles(mod):
                return True
        except Exception:                                      # noqa: BLE001
            pass
    return False


def _norm(folder):
    return (folder or '').replace('\\', '/').strip('/').lower()


def _is_hext(rel):
    parts = rel.split('/')
    return (len(parts) >= 2 and parts[-2] == 'hext'
            and parts[-1].endswith(('.txt', '.hext')))


def _read(mod, rel):
    cache = getattr(mod, 'cache', None)
    if cache:
        full = os.path.join(cache, *rel.split('/'))
        if os.path.isfile(full):
            with open(full, 'rb') as f:
                return f.read()
    try:
        return iro.read_one(mod.path, rel)
    except Exception:                                          # noqa: BLE001
        return None


def _patches(mod, rel):
    raw = _read(mod, rel)
    if raw is None:
        return []
    try:
        return list(exe_patch.parse_hext(raw.decode('utf-8', 'replace')))
    except Exception:                                          # noqa: BLE001
        return []


def analyse(mod):
    """{declared folder (lower, '/'): reason} for every folder left out."""
    man = getattr(mod, 'manifest', None)
    if man is None or not man.folders or disabled() or exempt(mod):
        return {}
    try:
        st = os.stat(mod.path)
        key = (mod.path, st.st_mtime, st.st_size)
    except OSError:
        key = (mod.path, None, None)
    if key in _cache:
        return _cache[key]

    entries = [e.replace('\\', '/') for e in mod.entries()]
    lows = [(e, e.lower()) for e in entries]
    # files that port together with another of this mod's (BUILD 868)
    paired = pairable(
        [(e, _read(mod, e) or b'') for e, low in lows if _is_hext(low)])
    folders = list(dict.fromkeys(_norm(f) for f, _c in man.folders if f))
    info = {}
    for f in folders:
        pre = f + '/'
        mine = [(e, low) for e, low in lows if low.startswith(pre)]
        code = 0
        ranges = []
        content = 0
        has_hext = False
        for e, low in mine:
            if _is_hext(low):
                has_hext = True
                pats = _patches(mod, e)
                # caves only: two options patching the same CODE site are
                # alternatives, not one feeding the other
                for va, blob in pats:
                    if not X86_TEXT[0] <= va < X86_TEXT[1]:
                        ranges.append((va, va + len(blob)))
                n = sum(1 for va, _b in pats if X86_TEXT[0] <= va < X86_TEXT[1])
                # BUILD 866: a file ff7nx_hextarm can carry to ARM64 is not
                # PC-only any more.
                if n and e not in paired and \
                        translate(_read(mod, e) or b'') is None:
                    code += n
                continue
            tail = low[len(pre):]
            if low.endswith(_LOGIC_EXT) or _CHUNK.search(low):
                continue
            if '/' not in tail and low.endswith(_DOC_EXT):
                continue
            content += 1
        info[f] = {'code': code, 'ranges': ranges, 'content': content,
                   'hext': has_hext}

    dropped = {}
    for f, i in info.items():
        if i['code'] and not i['content']:
            dropped[f] = ('PC only: %d x86 code patch(es), which the Switch '
                          'port does not run' % i['code'])
    if dropped:
        changed = True
        while changed:
            changed = False
            # (not a built-in fix's: what it writes, the Switch build
            # writes its own way -- WItemFix's price word in the table the
            # Item Randomizer's ShopPrices sets as well is no cave)
            taken = [r for f in dropped for r in info[f]['ranges']
                     if f.rsplit('/', 1)[-1] not in BUILT_IN]
            for f, i in info.items():
                if f in dropped or i['content']:
                    continue
                parent = next((d for d in dropped if f.startswith(d + '/')),
                              None)
                if parent is not None:
                    dropped[f] = 'PC only: needs %s' % parent
                    changed = True
                elif i['hext'] and any(a < tb and ta < b
                                       for a, b in i['ranges']
                                       for ta, tb in taken):
                    dropped[f] = ('PC only: writes into the code another of '
                                  'this mod\'s PC-only options adds')
                    changed = True
    for f in list(dropped):
        hint = BUILT_IN.get(f.rsplit('/', 1)[-1])
        if hint:
            dropped[f] = hint
    _cache[key] = dropped
    return dropped


def dropped(mod, folder):
    """Reason `folder` (as declared) is left out of the build, or None."""
    return analyse(mod).get(_norm(folder))


def locked_option(mod, option_id):
    """Why an option of `mod` does nothing on the Switch, or None.

    Locked when every folder the option gates is left out. A preset option
    that gates nothing but turns this mod's own options on (7th Heaven
    compatibility Require rules) is locked when everything it turns on is."""
    gone = analyse(mod)
    if not gone:
        return None
    import build
    man = mod.manifest
    want = option_id.casefold()
    mine = [_norm(f) for f, cond in man.folders
            if want in {i.casefold() for i in build._cond_option_ids(cond)}]
    if mine:
        if all(f in gone for f in mine):
            hints = [gone[f] for f in mine if gone[f].startswith('Built in')]
            if hints:
                return hints[0]
            why = 'PC only: x86 game code, which the Switch port does not run'
            if _tool_error:
                why += ' (translator off: %s)' % _tool_error
            return why
        return None
    own = (man.mod_id or '').lower()
    turns_on = [theirs for my, _v, mid, theirs, req in man.compat_settings
                if my.casefold() == want and mid == own and req != '0']
    if turns_on and all(locked_option(mod, t) for t in turns_on
                        if t.casefold() != want):
        return 'PC only: a preset of options the Switch port cannot run'
    return None


def locked_value(mod, option_id):
    """The value a locked option is shown and built at: its default."""
    for opt in getattr(mod.manifest, 'options', []):
        if opt.id == option_id:
            return opt.default
    return None
