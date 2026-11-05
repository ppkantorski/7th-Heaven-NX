#!/usr/bin/env python3
"""ff7nx_newthreat.py -- New Threat 2.0 (Sega Chief) on the Switch port.
BUILD 889.

Every function here is a no-op unless New Threat 2.0 is enabled with its core
files on (`gameplay = 1`). A build without it is byte-for-byte what it was.

WHAT THE MOD IS
  * `flevel.lgp/<field>.chunk.N` for all 702 fields: N = 1 (scripts), 3
    (model loader), 5, 7, 8 and one 2 (blackbgh's camera). PC sections.
  * KERNEL.BIN / kernel2.bin, battle/scene.bin, ~2,400 battle.lgp files,
    char.lgp / world_us.lgp models, four menu font sheets, three tracks.
  * hext/NT_01.txt -- one FFNx patch file for ff7.exe: x86 code (damage
    formulas, Octa Magic, item menu ...) AND .data tables (starting stats,
    materia stat bonuses, the limit table, every shop list and price, and
    the 7th Heaven mod flag Byte:0x914B1A = 1).

WHAT THE GENERIC PIPELINE DID WRONG WITH IT, AND WHAT THIS DOES INSTEAD
  1. HEXT. build_exe skips a file that touches .text WHOLE, so none of NT's
     tables reached ff7_en: shops sold the vanilla stock, the materia stat
     table was vanilla while NT's kernel indexes NT's. Here the file is split
     into its commented features. A feature whose patches are all .data /
     .rdata goes into ff7_en (the exe channel); a code feature that only
     changes an immediate goes to ff7nx_hextarm as ARM64 words; the rest
     (code caves: formula rewrites) is PC-only and is listed in the log.
     The file's `+400C00` / `+401600` global offsets (FFNx Hext::
     parseGlobalOffset: `+X` sets the offset to X, `-X` to -X) are resolved
     here and the data file is written with absolute addresses.
     exe_patch.parse_hext ignores those lines; it is deliberately left as
     it is, so every other mod's exe stays byte-identical.
  2. RUNTIME FLAG. Wizard Staff (kernel2 NT20 vs MSN), Idle Animations
     (cloudanimflevelsthreat) and ESUI (Icons/NT, forcenoaddon/nt) choose
     their New Threat variant from Byte:0x914B1A. The plan read the STOCK
     exe, so they chose the vanilla one -- Wizard Staff's vanilla-based
     kernel2 replaced NT's item/materia/spell text. `reader()` answers those
     gates from the exe WITH NT's data patches, as 7th Heaven's live read
     would.
  3. FIELD SCRIPTS. The chunk pass dropped PC section 1 in raw. PC field text
     carries FFNx-only markup (293 runs in 104 fields) and PC button glyphs
     (289), which this font draws as broken characters. `port_field` puts
     NT's section 1 through the same PC -> Switch normalisation Echo-S's
     importer uses (echo_s_flevel), minus its Echo-S-only features (time
     actor, inn pickers, voice speaker tags), and its section 3 through the
     same portability test / HRC remap.
  4. FieldID <Conditional>s (ConditionalMidgalBat: Cloud's battle model for
     one field; ConditionalVolcano: one field's music) were KEPT, i.e.
     applied in every field. A static build cannot follow a live field ID,
     so they are left out: those two places get the normal asset.
  5. Multi-Linked Slots: NT's option is a kernel only; the slot code comes
     from Postscriptthree's Gameplay Tweaks' MLS Enabler. Without it the
     kernel's slot types mean nothing to the stock code, so the folder is
     left out unless that enabler is on.

SEVENTH_NX_NO_NEWTHREAT=1 turns all of this off (generic behaviour).

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os
import re
import struct

OFF_ENV = 'SEVENTH_NX_NO_NEWTHREAT'

# Only the English 2.0 build is verified here (its hext and chunks were
# measured). The ESP build and Combat Only fall back to generic behaviour.
NT20_ID = 'efc4bc61-7f80-4635-b583-f52c5c9d239f'
MOD_IDS = {NT20_ID}
CORE_OPTION = 'gameplay'
MLS_FOLDER = 'optionmultilinkedslots/enabled'
PS3TWEAKS_ID = '5f9f1ce0-a402-46e1-b8af-e4c09f7c40a8'
FLAG_VA = 0x914B1A

CURRENT = None      # set by build_plan (plan_for)
STATS = {}          # flevel port counters, for the log


def disabled():
    return os.environ.get(OFF_ENV, '').strip() not in ('', '0')


def _mod_id(mod):
    return (getattr(getattr(mod, 'manifest', None), 'mod_id', '') or '').lower()


def handles(mod):
    return not disabled() and _mod_id(mod) in MOD_IDS


def _opt(mod, settings, ident):
    want = ident.casefold()
    for k, v in (settings or {}).items():
        if str(k).casefold() == want:
            return v
    for o in getattr(getattr(mod, 'manifest', None), 'options', ()):
        if o.id.casefold() == want:
            return o.default
    return None


def core_on(mod, settings):
    try:
        return int(_opt(mod, settings, CORE_OPTION)) == 1
    except (TypeError, ValueError):
        return False


def find(mods, settings_by_mod):
    """The enabled New Threat 2.0 mod with its core files on, or None."""
    for m in mods or ():
        if handles(m) and core_on(m, (settings_by_mod or {}).get(m.filename)):
            return m
    return None


# ------------------------------------------------------------------ hext

_OFFSET_LINE = re.compile(r'^\s*([+-])\s*([0-9A-Fa-f]+)\s*$')


def features(text):
    """[{'name', 'patches': [(va, bytes)]}] -- one per commented feature.

    A run of comment lines names the next feature; patch lines join the
    current one. Addresses carry FFNx's global offset (`+X` sets it to X,
    `-X` to -X, exactly as Hext::parseGlobalOffset)."""
    import exe_patch
    out, cur, offset = [], None, 0
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith('#'):
            label = s.lstrip('#').strip()
            if cur is None or cur['patches']:
                cur = {'name': label, 'patches': []}
                out.append(cur)
            else:
                cur['name'] = (cur['name'] + ' / ' + label).strip(' /')
            continue
        m = _OFFSET_LINE.match(s)
        if m:
            offset = int(m.group(2), 16) * (1 if m.group(1) == '+' else -1)
            continue
        if s.startswith(('.', '{')):
            continue
        for va, blob in exe_patch.parse_hext(s):
            if cur is None:
                cur = {'name': '(top)', 'patches': []}
                out.append(cur)
            cur['patches'].append((va + offset, bytes(blob)))
    return [f for f in out if f['patches']]


def _hext_text(patches):
    return '\n'.join('%X = %s' % (va, ' '.join('%02X' % b for b in blob))
                     for va, blob in patches) + '\n'


def classify(text, exe):
    """(data features, code features) for one NT hext file. A feature with
    any .text patch is code as a whole -- its .data writes are the cave the
    code jumps to."""
    import exe_patch
    pe = exe_patch.parse_pe(exe)
    base = pe['image_base']

    def section(va):
        if va < base:
            va += base
        for name, sva, vsz, _f, _r in pe['sections']:
            if sva <= va < sva + vsz:
                return name
        return None

    data, code = [], []
    for f in features(text):
        secs = {section(va) for va, _b in f['patches']}
        if '.text' in secs:
            code.append(f)
        elif secs <= {'.data', '.rdata'}:
            data.append(f)
        else:
            code.append(f)          # unmapped: nothing we can place
    return data, code


def _work_dir():
    import nxpaths
    d = os.path.join(nxpaths.ROOT, 'cache', '_newthreat')
    os.makedirs(d, exist_ok=True)
    return d


def split_hext(path, exe=None, translate=None):
    """{'data_path', 'data': [features], 'arm': [(feature, words)],
        'pc_only': [feature names]} for one of NT's hext files.

    `exe`: stock ff7_en bytes; `translate(text) -> words | None` is
    ff7nx_hextarm through ff7nx_pccode (both default from the dump)."""
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        text = f.read()
    if exe is None or translate is None:
        import ff7nx_pccode
        tool = ff7nx_pccode._translator()
        if tool is None:
            raise RuntimeError('needs the full game dump (ff7_en + '
                               'exefs/main) and capstone')
        exe = exe or tool[0]
        translate = translate or ff7nx_pccode.translate
    data, code = classify(text, exe)
    arm, pc_only = [], []
    for f in code:
        words = None
        try:
            words = translate(_hext_text(f['patches']))
        except Exception:                                      # noqa: BLE001
            words = None
        if words:
            arm.append((f, words))
        else:
            pc_only.append(f['name'])
    out = {'data': data, 'arm': arm, 'pc_only': pc_only, 'data_path': None}
    if data:
        stem = os.path.splitext(os.path.basename(path))[0]
        dp = os.path.join(_work_dir(), '%s (New Threat data).txt' % stem)
        body = ('# BUILD 889: the .data/.rdata features of New Threat 2.0\'s '
                '%s, global offsets resolved.\n' % os.path.basename(path))
        for f in data:
            body += '# %s\n%s' % (f['name'], _hext_text(f['patches']))
        old = None
        if os.path.exists(dp):
            with open(dp, 'r', encoding='utf-8') as fh:
                old = fh.read()
        if old != body:                 # keep the mtime when unchanged
            with open(dp, 'w', encoding='utf-8') as fh:
                fh.write(body)
        out['data_path'] = dp
    return out


def root_hext(mod):
    """NT's ungated hext files (its root `hext/` folder), in name order."""
    d = os.path.join(mod.cache, 'hext')
    if not os.path.isdir(d):
        return []
    return [os.path.join(d, n) for n in sorted(os.listdir(d))
            if n.lower().endswith(('.txt', '.hext'))]


# ------------------------------------------------------- runtime reader

def reader(read, mods, settings_by_mod, log=lambda *_: None):
    """`read` answering exe RuntimeVars as 7th Heaven's live read would with
    New Threat loaded: from the exe with NT's DATA patches applied. Returns
    `read` itself when NT is not on (or the exe cannot be re-read)."""
    nt = find(mods, settings_by_mod)
    if nt is None or read is None:
        return read
    with_hext = getattr(read, 'with_hext', None)
    if with_hext is None:
        return read
    paths = []
    for p in root_hext(nt):
        try:
            res = split_hext(p)
        except Exception as exc:                               # noqa: BLE001
            log('! New Threat: %s not read for the RuntimeVar gates (%s)'
                % (os.path.basename(p), exc))
            continue
        if res['data_path']:
            paths.append(res['data_path'])
    if not paths:
        return read
    try:
        new = with_hext(paths)
    except Exception as exc:                                   # noqa: BLE001
        log('! New Threat: RuntimeVar gates read from the stock exe (%s)'
            % exc)
        return read
    try:
        flag = new('Byte:0x%X' % FLAG_VA)
    except Exception:                                          # noqa: BLE001
        flag = None
    log('New Threat 2.0: RuntimeVar gates read with its exe data applied '
        '(Byte:0x%X = %s), so Wizard Staff, Idle Animations and Enhanced '
        'Stock UI pick their New Threat variants' % (FLAG_VA, flag))
    new.with_hext = with_hext
    return new


# ------------------------------------------------------------- folders

def plan_for(mods, settings_by_mod, log=lambda *_: None):
    """Build-wide facts files_for needs (set as CURRENT by build_plan)."""
    nt = find(mods, settings_by_mod)
    if nt is None:
        return None
    mls_code = False
    for m in mods:
        if _mod_id(m) == PS3TWEAKS_ID:
            try:
                mls_code = int(_opt(m, (settings_by_mod or {}).get(
                    m.filename), 'MLSEnabler')) == 1
            except (TypeError, ValueError):
                mls_code = False
    return {'mod': nt.filename, 'mls_code': mls_code}


def dropped(mod, folder):
    """Why one of NT's declared folders is left out of this build, or None."""
    if not handles(mod):
        return None
    import iro
    man = mod.manifest
    low = (folder or '').replace('\\', '/').strip('/').lower()
    rt = man.folder_conditions.get(folder)
    if rt is not None and folder in getattr(man, 'conditional_folders', ()):
        names = {v.strip().lower() for v in iro.runtime_vars(rt)}
        if names and names <= {'fieldid'}:
            return ('gated on the live field ID, which a static build cannot '
                    'follow -- applying it would put it in every field')
    if low == MLS_FOLDER and CURRENT is not None and not CURRENT['mls_code']:
        return ('Multi-Linked Slots needs the slot code from Postscriptthree\'s '
                'Gameplay Tweaks (MLS Enabler); without it this kernel\'s slot '
                'types mean nothing to the game')
    return None


def setting_conflicts(active, effective, log=lambda *_: None):
    """NT's <Setting> rules that FORBID another mod's option values (7th
    Heaven refuses such a profile; the generic rule engine only reads
    Require). Returns error lines."""
    nt = find(active, effective)
    if nt is None:
        return []
    import iro
    path = os.path.join(nt.cache, 'mod.xml')
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            text = f.read()
    except OSError:
        return []
    by_id = {_mod_id(m): m for m in active}
    bad = []
    for block in re.findall(r'(?is)<Setting>(.*?)</Setting>', text):
        fields = dict((k, v.strip()) for k, v in
                      re.findall(r'(?is)<(MyID|MyValue|ModID|TheirID)>(.*?)<',
                                 block))
        forbid = [v.strip() for v in
                  re.findall(r'(?is)<Forbid>(.*?)</Forbid>', block)]
        if not forbid or not all(fields.get(k) for k in
                                 ('MyID', 'MyValue', 'ModID', 'TheirID')):
            continue
        target = by_id.get(fields['ModID'].lower())
        if target is None:
            continue
        try:
            if int(_opt(nt, effective.get(nt.filename), fields['MyID'])) \
                    != int(fields['MyValue']):
                continue
            have = _opt(target, effective.get(target.filename),
                        fields['TheirID'])
            if have is None:
                continue        # the option does not exist (renamed): no rule
            if str(int(have)) in forbid:
                bad.append('%s (%s=%s) forbids %s: %s = %s'
                           % (nt.display_name, fields['MyID'],
                              fields['MyValue'], target.display_name,
                              fields['TheirID'], have))
        except (TypeError, ValueError):
            continue
    return bad


# --------------------------------------------------------------- flevel

def _repair_akao(s1):
    """A first AKAO entry pointing 1..3 bytes past its header (an offset
    table not moved with the strings) is retargeted at the header; the
    Switch's music dispatcher needs it. Returns (section, n)."""
    import echo_s_flevel as E
    if len(s1) < E._SECTION1_FIXED_HEADER:
        return s1, 0
    actors = s1[2]
    count = struct.unpack_from('<H', s1, 6)[0]
    table = E._SECTION1_FIXED_HEADER + actors * 8
    if table + count * 4 > len(s1) or not count:
        return s1, 0
    off = struct.unpack_from('<I', s1, table)[0]
    if s1[off:off + 4] == b'AKAO':
        return s1, 0
    for back in (1, 2, 3):
        if off >= back and s1[off - back:off - back + 4] == b'AKAO':
            out = bytearray(s1)
            struct.pack_into('<I', out, table, off - back)
            return bytes(out), 1
    return s1, 0


def _button_fixes(s1, field):
    """echo_s_flevel.FIELD_BUTTON_FIXES (PC -> Switch button names, and
    "7th Heaven" -> "7th Heaven NX") wherever NT's text has them. The table
    is counted against Echo-S's text, so each rule is held to what NT's
    section actually contains; a mismatch skips the field's fixes."""
    import echo_s_flevel as E
    rules = E.FIELD_BUTTON_FIXES.get((field or '').lower())
    if not rules:
        return s1, 0
    try:
        so = struct.unpack_from('<H', s1, 4)[0]
        strings = s1[so:]
    except struct.error:
        return s1, 0
    mine = [(old, new, strings.count(old)) for old, new, _n in rules
            if strings.count(old)]
    if not mine:
        return s1, 0
    try:
        return E.apply_field_button_fixes(s1, field, rules=mine)
    except Exception:                                          # noqa: BLE001
        return s1, 0


# New Threat writes a button as "<PC key glyph> / <gamepad name>", e.g. md1_1
# `Hold the FE D3 95 FE D9 " /" FE D5 [CANCEL] FE D9 button` -- a coloured
# keyboard glyph, then the pad button. echo_s_flevel turns the bracketed name
# into the Switch glyph (B here); the keyboard glyph in front is a byte this
# font does not have, so "<key> /" is dropped wherever a Switch button
# follows it: `Hold the B button`.
_KEY_SLASH = re.compile(
    rb'\xfe[\xd2-\xd8]([\x81\x82\x90-\x9c\xbe\xbf])\xfe\xd9\x00?\x0f\x00?'
    rb'(?=\xfe[\xd2-\xd8](?:\xf6[\x10-\x17]|[\xf7-\xf9]))')


def _map_strings(s1, fn):
    """Apply fn(bytes) -> bytes to every dialogue string's text (before its
    FF), rebuilding the string table and AKAO offsets as echo_s_flevel does.
    Returns (section, number of strings changed)."""
    import echo_s_flevel as E
    so = struct.unpack_from('<H', s1, 4)[0]
    count = struct.unpack_from('<H', s1, so)[0]
    table = so + 2
    offsets = list(struct.unpack_from('<%dH' % count, s1, table))
    if not offsets:
        return s1, 0
    uniq = sorted(set(offsets))
    starts = [so + v for v in uniq]
    rebuilt = bytearray(s1[:starts[0]])
    moved, changed = {}, 0
    for i, (old, start) in enumerate(zip(uniq, starts)):
        end = starts[i + 1] if i + 1 < len(starts) else len(s1)
        part = s1[start:end]
        term = part.index(b'\xff')
        head = bytes(part[:term])
        new = fn(head)
        changed += new != head
        moved[old] = len(rebuilt) - so
        rebuilt += new + part[term:]
    if not changed:
        return s1, 0
    for i, old in enumerate(offsets):
        if moved[old] > 0xffff:
            raise E.EchoFlevelError('string offset exceeds u16')
        struct.pack_into('<H', rebuilt, table + i * 2, moved[old])
    delta = len(rebuilt) - len(s1)
    actors = s1[2]
    akao_count = struct.unpack_from('<H', s1, 6)[0]
    akao_table = E._SECTION1_FIXED_HEADER + actors * 8
    for i in range(akao_count):
        o = akao_table + i * 4
        a = struct.unpack_from('<I', rebuilt, o)[0]
        if a >= starts[0]:
            struct.pack_into('<I', rebuilt, o, a + delta)
    return bytes(rebuilt), changed


# "<key> / [UP]": a direction echo_s_flevel leaves in words; the key glyph's
# byte names the same button, so the pair becomes that one Switch glyph.
_KEY_SLASH_WORD = re.compile(
    rb'(\xfe[\xd2-\xd8])([\x81\x82\x90-\x9c\xbe\xbf])\xfe\xd9\x00?\x0f\x00?'
    rb'\xfe[\xd2-\xd8]\x3b[\x21-\x3a]{1,12}\x3d\xfe\xd9')
# A key glyph on its own (Junon's parade commands, "Press <key>"): the same
# byte -> Switch button table echo_s_flevel uses (PC_BUTTON_GLYPH_BY_BYTE).
_KEY_ALONE = re.compile(
    rb'(\xfe[\xd2-\xd8])([\x81\x82\x90-\x9c\xbe\xbf])(\xfe\xd9)')


def _drop_key_glyphs(s1):
    import echo_s_flevel as E
    table = E.PC_BUTTON_GLYPH_BY_BYTE
    n = [0]

    def swap(m, tail=b'\xfe\xd9'):
        g = table.get(m.group(2)[0])
        if g is None:
            return m.group(0)
        n[0] += 1
        return m.group(1) + g + tail

    def fn(head):
        new, k = _KEY_SLASH.subn(b'', head)
        n[0] += k
        new = _KEY_SLASH_WORD.sub(swap, new)
        new = _KEY_ALONE.sub(lambda m: swap(m, m.group(3)), new)
        return new
    s1, _c = _map_strings(s1, fn)
    return s1, n[0]


def port_field(field, nt, stock):
    """Section 1 / 3 of New Threat for one field, ported to the Switch.

    nt:    {section number: NT bytes} for 1 and/or 3
    stock: the field's nine Switch sections (vanilla archive)
    Returns {section number: bytes to use}."""
    import echo_s_flevel as E
    out = {}
    st = STATS
    s1 = nt.get(1)
    if s1 is not None:
        try:
            s1, n = E.strip_pc_speaker_prefixes(s1)
            st['markup'] = st.get('markup', 0) + n
            s1, n = _drop_key_glyphs(s1)
            st['keys'] = st.get('keys', 0) + n
            s1, n = _repair_akao(s1)
            if n:
                st.setdefault('akao', []).append(field)
            s1, n = _button_fixes(s1, field)
            if n:
                st.setdefault('buttons', []).append(field)
            before = len(E.RETIRED_MVIEF_WAITS)
            s1, _w = E.retire_endless_mvief_waits(s1, stock[0], field)
            if len(E.RETIRED_MVIEF_WAITS) > before:
                st.setdefault('mvief', []).append(field)
        except Exception as exc:                               # noqa: BLE001
            st.setdefault('raw1', []).append('%s (%s)' % (field, exc))
            s1 = nt[1]
        try:
            E.validate_akao_offsets(s1)
        except Exception as exc:                               # noqa: BLE001
            # NT 2.0's own sininb2 ships its first AKAO block as `\0KAO\n`
            # with the header bytes after it shifted: damaged in the mod,
            # not by this port, and not safely repairable. The Switch music
            # dispatcher skips a cue without the header (no crash), so the
            # script is kept and the field named.
            st.setdefault('akao_bad', []).append('%s (%s)' % (field, exc))
        out[1] = s1
    s3 = nt.get(3)
    if s3 is not None:
        if bytes(s3) == bytes(stock[2]):
            out[3] = s3
        elif E._model_loader_is_portable(s3, stock[2]):
            out[3] = s3
        else:
            r = E.remap_model_loader(s3, stock[2])
            if r is E.EQUIVALENT_MODEL_LOADER:
                out[3] = stock[2]
            elif r is not None:
                out[3] = r
                st.setdefault('remapped', []).append(field)
            else:
                # names another field's models (blackbgh borrows whitebg3's
                # eight). Keeping the Switch loader would leave the script's
                # model indices pointing past its end, so NT's is taken, as
                # 7th Heaven does -- reported, to be checked on hardware.
                out[3] = s3
                st.setdefault('foreign3', []).append(field)
    return out


def flevel_summary():
    st = STATS
    if not st:
        return []
    import echo_s_flevel as E
    lines = ['  New Threat 2.0: field scripts ported to the Switch -- %d PC '
             'markup run(s) removed, %d PC button glyph(s) redrawn, %d PC '
             'key glyph(s) turned into Switch buttons'
             % (st.get('markup', 0), E.BUTTON_GLYPHS_RETARGETED,
                st.get('keys', 0))]
    for key, what in (('akao', 'AKAO music offset repaired (NT points past '
                                'the header)'),
                      ('buttons', 'button names fixed for the Switch'),
                      ('mvief', 'endless MVIEF wait retired'),
                      ('remapped', 'model loader taken in NT\'s order with '
                                   'the Switch\'s records (HRC match)')):
        if st.get(key):
            lines.append('    %s: %s' % (what, ', '.join(sorted(st[key]))))
    if st.get('foreign3'):
        lines.append('    ! model loader names another field\'s models, '
                     'taken as on PC (check on hardware): %s'
                     % ', '.join(sorted(st['foreign3'])))
    if st.get('akao_bad'):
        lines.append('    ! a music cue has no AKAO header in the mod\'s own '
                     'script, so that cue will not play: %s'
                     % '; '.join(st['akao_bad']))
    if st.get('raw1'):
        lines.append('    ! script not normalisable, used as shipped: %s'
                     % '; '.join(st['raw1']))
    return lines
