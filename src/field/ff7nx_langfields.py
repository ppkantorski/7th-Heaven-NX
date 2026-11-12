"""ff7nx_langfields.py -- BUILD 914: the HD field backgrounds (and the other
language-neutral field changes) in the Spanish, French and German field
archives too. Opt-in: Settings > Languages.

THE PROBLEM. The Switch port keeps one field archive per language --
flevel.lgp (English), sflevel.lgp (Spanish), fflevel.lgp (French),
gflevel.lgp (German), jfleve.lgp (Japanese) -- and the game opens the one
for its system language. The build writes only flevel.lgp, so a game set to
Spanish showed every field as vanilla. No installed mod ships a non-English
field archive (Echo-S, ESUI, Cosmos: English only).

WHAT CAN COME ALONG, measured on the shipped archives (en vs es, 711 shared
fields). Section numbers are 0-based (lgp.split_sections):

    0 script + text      differs in every field     -> the language's own
    1 camera, 2 models   identical; the build edits 2 for Echo-S / Idle
                         Animations, which go with English scripts -> kept
    3 palettes           identical in every field
    4-6                  identical / not edited by the build
    7 triggers           identical; the build edits only the 0x0C..0x37
                         header (camera range for widescreen, layer setup)
    8 background         identical in ~76% (es, fr), ~59% (de), ~24% (ja);
                         where it differs the scenery carries painted text

So per field: when the language's palettes AND background equal English
vanilla, its sections 3 and 8 come from the built flevel, plus section 7's
header (only when its section 7 equals English vanilla). Everything else is
the language's own: script and dialogue, models, doors and exits. A field
whose own background differs keeps it, as today. The footstep trailer (CMFS,
after the nine sections; keyed by walkmesh triangles, identical across
languages) is appended, and the loose entries the build added to flevel
(the facial eye/mouth textures) are added to the archive as they are.

Japanese is not offered: three quarters of its fields keep their own art.

SEVENTH_NX_LANG_FIELDS = comma list of es, fr, de (empty: off).

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import os
import struct

import lgp

VERSION = 'langfields-1'
ENV = 'SEVENTH_NX_LANG_FIELDS'
LANGS = {                       # code -> (archive, name)
    'es': ('sflevel.lgp', 'Spanish'),
    'fr': ('fflevel.lgp', 'French'),
    'de': ('gflevel.lgp', 'German'),
}
FIELD_DIR = 'data/field'
SEC7_HEADER = 0x38              # triggers: name .. layer setup


def selected():
    raw = os.environ.get(ENV, '')
    out = []
    for c in raw.replace(' ', '').lower().split(','):
        if c in LANGS and c not in out:
            out.append(c)
    return out


def _sections_end(raw):
    p = struct.unpack('<9I', raw[6:42])[-1]
    return p + 4 + struct.unpack('<I', raw[p:p + 4])[0]


def merge_field(v_raw, l_raw, b_raw):
    """(raw or None, took_background). None: the language's own field is
    unchanged (no trailer, background kept) -- reuse its payload as it is."""
    v = lgp.split_sections(v_raw)
    l_ = lgp.split_sections(l_raw)
    b = lgp.split_sections(b_raw)
    out = list(l_)
    took = l_[3] == v[3] and l_[8] == v[8]
    if took:
        out[3], out[8] = b[3], b[8]
        if l_[7] == v[7] and len(b[7]) >= SEC7_HEADER \
                and len(l_[7]) >= SEC7_HEADER:
            out[7] = b[7][:SEC7_HEADER] + l_[7][SEC7_HEADER:]
    trailer = b_raw[_sections_end(b_raw):]
    if not took and not trailer:
        return None, False
    return lgp.join_sections(out) + trailer, took


def _sig(path):
    st = os.stat(path)
    return [st.st_size, st.st_mtime_ns]


def fingerprint(vanilla_lang, vanilla_en, built):
    h = hashlib.sha1(VERSION.encode())
    for p in (vanilla_lang, vanilla_en, built):
        h.update(json.dumps(_sig(p)).encode())
    return h.hexdigest()


def _reusable(dest, rec, fp):
    if not (os.path.exists(dest) and os.path.exists(rec)):
        return None
    try:
        with open(rec) as fh:
            r = json.load(fh)
        if r.get('fp') == fp and r.get('sig') == _sig(dest):
            st = dict(r['stats'])
            st['reused'] = True
            return st
    except (OSError, ValueError, KeyError):
        pass
    return None


def build_languages(codes, dump_field_dir, built_flevel, romfs, cache_dir,
                    log=lambda *_: None, workers=4):
    """Write each selected language's field archive into `romfs`.
    Returns [(code, dest, stats)]. The built flevel and English vanilla are
    loaded once for all of them; a language whose inputs are unchanged since
    its last build is kept as it is."""
    vanilla_en = os.path.join(dump_field_dir, 'flevel.lgp')
    todo, out = [], []
    for code in codes:
        fn = LANGS[code][0]
        vanilla_lang = os.path.join(dump_field_dir, fn)
        dest = os.path.join(romfs, FIELD_DIR, fn)
        if not os.path.exists(vanilla_lang):
            log('  ! %s: %s is not in the game dump -- skipped' % (code, fn))
            continue
        fp = fingerprint(vanilla_lang, vanilla_en, built_flevel)
        rec = os.path.join(cache_dir, fn + '.fp')
        st = _reusable(dest, rec, fp)
        if st is not None:
            out.append((code, dest, st))
        else:
            todo.append((code, vanilla_lang, dest, fp, rec))
    if not todo:
        return out
    V = lgp.Archive(vanilla_en)
    vnames = {e['name'].lower() for e in V.entries}
    for k, (code, vanilla_lang, dest, fp, rec) in enumerate(todo):
        # a fresh read of the built flevel per language: each pass drops the
        # built payloads it has merged, so memory stays near one archive
        B = lgp.Archive(built_flevel)
        bfield = {e['name'].lower() for e in B.entries if B.is_field(e)}
        L = lgp.Archive(vanilla_lang)
        fields = [e['name'].lower() for e in L.entries
                  if L.is_field(e) and e['name'].lower() in bfield
                  and e['name'].lower() in vnames]

        def one(field, B=B, L=L):
            be = B.index[field]
            try:
                raw, took = merge_field(V.decompressed(V.index[field]),
                                        L.decompressed(L.index[field]),
                                        B.decompressed(be))
            except Exception as exc:                        # noqa: BLE001
                return field, None, False, 0, str(exc)[:80]
            finally:
                be['payload'] = b''          # merged: release it
            if raw is None:
                return field, None, False, 0, None
            return field, B.encode_field(raw), took, len(raw), None

        st = {'fields': len(fields), 'background': 0, 'kept': 0, 'loose': 0,
              'max_raw': 0, 'reused': False, 'errors': []}
        payloads = {}
        with cf.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            for field, pay, took, n, err in ex.map(one, fields):
                if err:
                    st['errors'].append((field, err))
                    continue
                st['background' if took else 'kept'] += 1
                st['max_raw'] = max(st['max_raw'], n)
                if pay is not None:
                    payloads[field] = pay
        L.replace(payloads)
        payloads = None
        lnames = {e['name'].lower() for e in L.entries}
        for e in B.entries:
            low = e['name'].lower()
            if low in lnames or low in vnames or low in bfield:
                continue
            L.add(low, e['payload'])      # facial textures, as the build has
            st['loose'] += 1
        B = None
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + '.langtmp'
        L.write(tmp, data_order=lgp.loose_first_order(L))
        middle, L = L.middle, None
        if lgp.Archive(tmp).middle != middle:
            os.remove(tmp)
            raise RuntimeError('%s: tables did not survive the write'
                               % LANGS[code][0])
        os.replace(tmp, dest)
        os.makedirs(cache_dir, exist_ok=True)
        with open(rec, 'w') as fh:
            json.dump({'fp': fp, 'sig': _sig(dest),
                       'stats': {x: y for x, y in st.items()
                                 if x != 'reused'}}, fh)
        out.append((code, dest, st))
    return out


def summarise(code, st):
    fn, name = LANGS[code]
    if st.get('reused'):
        return ('  %s (%s): unchanged since the last build -- kept; largest '
                'field %s bytes' % (name, fn, format(st['max_raw'], ',')))
    line = ('  %s (%s): HD backgrounds in %d of %d fields; %d keep their own '
            '(text painted into the scenery); %d facial textures added; '
            'dialogue, menus and scripts stay %s'
            % (name, fn, st['background'], st['fields'], st['kept'],
               st['loose'], name))
    if st.get('errors'):
        line += '\n  ! %s: %d field(s) left as they were: %s' % (
            fn, len(st['errors']),
            ', '.join('%s (%s)' % e for e in st['errors'][:4]))
    return line
