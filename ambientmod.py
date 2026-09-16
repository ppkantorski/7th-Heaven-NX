"""
ambientmod.py -- FFNx `Ambient/` per-location ambience, parsed and staged.

WHAT THIS IS AND WHAT IT IS NOT
===============================
`sfxmod.py` owns the fixed 750-slot SFX archive. This module owns the layer
that CANNOT go in that archive, and the reason is worth stating once because
it is the whole design:

An `sfx/` mapping is keyed on a GAME SOUND ID -- a slot every caller in the
engine already knows about. An `Ambient/` mapping is keyed on a PLACE:

    [field_66]                 the field with id 66
    [field_66_3]               that field, walkmap triangle 3
    [bat_1]                    battle formation 1
    sequential = [ "1580" ]    the .ogg to loop there
    fade_in  = 0.50
    fade_out = 0.50
    volume   = 60

There is no sound id anywhere in that. Folding `1580.ogg` into an archive slot
would make one continuous location loop into a global sound effect with a
global lifetime -- it would start wherever that slot is played and never stop
when you left the place it belongs to. So this module does not touch
`audio.dat` at all. It produces two things:

  * the loose `.ogg` loops, staged under `data/music_ogg/ambient/`, which is
    the path the port's own `MusicStream`/`NativeOggPlayer` already resolves;
  * a merged {location: mapping} dict, which `ff7nx_ambient.tables_from_config`
    turns into the compact field/battle tables its runtime cave indexes.

Playback is the cave's job, not this module's. Nothing here claims a location
will sound -- it only guarantees that if the cave asks for `ambient/1580.ogg`,
that file is on the card and the table says 1580 for that field.

WHY THE PARSER IS HAND-WRITTEN
==============================
Same reason `sfxmod.parse_config` is: the project takes no TOML dependency,
Python's own `tomllib` is 3.11+, and these files are a narrow, stable shape.
Cosmo Memory's real `WM/sfx/config.toml` also opens with a large ASCII-art
comment block, so the parser has to ignore comments properly rather than
scan for brackets.

Unknown keys are dropped rather than carried: a mapping this project cannot
honour should not travel far enough to look supported. `fade_in`, `fade_out`
and `volume` are kept because the runtime bridge reads them; everything else
FFNx allows here is currently ignored.

LAYERING
========
Identical to the SFX layer, and for the same reason -- it is how 7th Heaven
composes option folders. Cosmo Memory ships `FA/Ambient` (field), `BA/Ambient`
(battle) and `FA+BA/Ambient` (both, with different fade values), and the
user's option selection decides which are active. Later configs win PER
LOCATION, not per file: a later folder that re-declares `field_66` replaces
that one entry and leaves every other entry the earlier folder set. Merging
whole files instead would silently drop 600 mappings the moment a mod shipped
a small override folder.
"""
import json
import os
import re

CONFIG_NAME = 'config.toml'
AMBIENT_DIR = 'ambient'

# The location keys this project understands. Anything else in the file --
# FFNx allows several more -- is dropped by parse_config rather than passed
# on, so a mapping that cannot be honoured never reaches the table builder.
#
#   field_<id>              a whole field
#   field_<id>_<triangle>   one walkmap triangle in that field
#   bat_<id>                one battle formation
_LOCATION = re.compile(r'^(?:field_\d+(?:_\d+)?|bat_\d+)$')

_SECTION = re.compile(r'^\[([^\]]+)\]')
# re.M because a section body is several lines joined back together and both
# of these are line-anchored; re.S so a list broken across lines still closes
# on its first `]` (non-greedy), which some hand-edited configs do.
_LIST = re.compile(r'^(sequential|shuffle)\s*=\s*\[(.*?)\]', re.S | re.M)
_NUMBER = re.compile(r'^(fade_in|fade_out|volume)\s*=\s*([-+0-9.eE]+)', re.M)
_ITEM = re.compile(r'"([^"]*)"|\'([^\']*)\'|([0-9]+)')


def _strip_comment(line):
    """Drop a trailing `#` comment that is not inside a quoted string."""
    out, quote = [], None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in '"\'':
            quote = ch
            out.append(ch)
        elif ch == '#':
            break
        else:
            out.append(ch)
    return ''.join(out)


def parse_config(text):
    """
    {location key: {'sequential'|'shuffle': [ogg id, ...], ...}} from one file.

    OGG ids come back as STRINGS, quoted or bare, because that is how they are
    used downstream -- as a filename stem. Cosmo writes them quoted in the
    Ambient files and bare in `Base/sfx/config.toml`, so both are accepted.

    A section with no `sequential`/`shuffle` list is dropped: it names a place
    with nothing to play there, which is not a mapping.
    """
    tracks, key = {}, None
    body = []
    def flush():
        if key is None:
            return
        chunk = '\n'.join(body)
        meta = {}
        m = _LIST.search(chunk)
        if m:
            items = [a or b or c for a, b, c in _ITEM.findall(m.group(2))]
            # MEASURED against the shipping mod, not defensive padding:
            # Cosmo Memory's `FA/Ambient/config.toml` line 3034 is
            #
            #     [field_705]
            #     sequential = [ "X" ]
            #
            # -- an authoring placeholder for a field whose loop was never
            # chosen. It is not commented out. A non-numeric id is not a
            # filename stem, so it names no loop: drop the item, and with it
            # the section if that was the only one. Passing it on instead
            # makes `int()` raise in the table builder and takes the entire
            # ambient bridge down over one undecided field.
            items = [v.strip() for v in items if v.strip().isdigit()]
            if items:
                meta[m.group(1)] = items
        for name, value in _NUMBER.findall(chunk):
            try:
                meta[name] = float(value)
            except ValueError:
                pass
        if 'sequential' in meta or 'shuffle' in meta:
            tracks[key] = meta

    for raw in text.splitlines():
        line = _strip_comment(raw).strip()
        if not line:
            body.append('')
            continue
        m = _SECTION.match(line)
        if m:
            flush()
            name = m.group(1).strip()
            key = name if _LOCATION.match(name) else None
            body = []
            continue
        body.append(line)
    flush()
    return tracks


def merge_configs(texts):
    """
    Layer several config files, later ones winning PER LOCATION.

    A later file that re-declares one location replaces that location's whole
    mapping, including its fade values -- FFNx's own semantics, and the reason
    `FA+BA` can restate the same 634 fields with different fades without the
    earlier folder's values bleeding through on the ones it happens to omit.
    """
    merged = {}
    for text in texts:
        merged.update(parse_config(text))
    return merged


def collect(files):
    """
    (config texts in order, {ogg id: path}) from a build plan's ambient files.

    `files` is [(archive-relative path, absolute path), ...] in APPLICATION
    order, which is what makes "later wins" mean the same thing here as it
    does everywhere else in the build.

    Matching is on the directory component `Ambient/`, case-insensitively,
    because mods disagree about its case and a basename test would also claim
    `sfx/config.toml`. An `.ogg` outside an `Ambient/` directory is not an
    ambient loop and is left for whichever layer does own it.
    """
    configs, oggs = [], {}
    for rel, full in files:
        parts = rel.replace('\\', '/').split('/')
        if not any(p.lower() == AMBIENT_DIR for p in parts[:-1]):
            continue
        name = parts[-1]
        low = name.lower()
        if low == CONFIG_NAME:
            try:
                with open(full, 'rb') as f:
                    configs.append(f.read().decode('utf-8', 'replace'))
            except OSError:
                continue
        elif low.endswith('.ogg'):
            # Keyed on the numeric stem with leading zeros stripped, because
            # a config writes `1580` and the file may be `1580.ogg` or
            # `01580.ogg`. Later files win, matching the config layering.
            stem = os.path.splitext(name)[0]
            oggs[stem.lstrip('0') or '0'] = full
    return configs, oggs


class StageResult:
    def __init__(self):
        self.locations = 0
        self.field_locations = 0
        self.battle_locations = 0
        self.copied = 0
        self.copied_bytes = 0
        self.missing = []            # (location key, ogg id) with no file


def stage(config, oggs, dest):
    """
    Write the referenced loops and a `manifest.json` under `dest`.

    Only loops a location actually names are copied -- Cosmo ships more than
    the active option set references, and staging the rest would put tens of
    MB on the card that nothing can ever ask for.

    A location whose .ogg is not in the pool is recorded in `missing` and
    dropped from the manifest rather than written as a dangling reference:
    the runtime bridge treats a missing file the way the port's own loader
    does (log and continue), but a manifest that promises a file which is not
    there is a build bug, and it should be visible in the build log as one.

    Returns a StageResult. The manifest is for inspection and for the
    build-time table builder; nothing on the console reads it.
    """
    os.makedirs(dest, exist_ok=True)
    res = StageResult()
    manifest = {'locations': {}}
    staged = {}
    for key in sorted(config):
        meta = config[key]
        ids = meta.get('sequential') or meta.get('shuffle') or []
        if not ids:
            continue
        ogg_id = str(ids[0]).lstrip('0') or '0'
        src = oggs.get(ogg_id)
        if not src:
            res.missing.append((key, str(ids[0])))
            continue
        name = '%s.ogg' % ogg_id
        if ogg_id not in staged:
            out = os.path.join(dest, name)
            with open(src, 'rb') as inp, open(out, 'wb') as outp:
                blob = inp.read()
                outp.write(blob)
            staged[ogg_id] = name
            res.copied += 1
            res.copied_bytes += len(blob)
        entry = {'ogg': staged[ogg_id]}
        for name_ in ('fade_in', 'fade_out', 'volume'):
            if name_ in meta:
                entry[name_] = meta[name_]
        manifest['locations'][key] = entry
        res.locations += 1
        if key.startswith('field_'):
            res.field_locations += 1
        else:
            res.battle_locations += 1
    with open(os.path.join(dest, 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=1, sort_keys=True)
    return res


def describe(res):
    """Build-log lines for a StageResult."""
    out = ['ambient: %d location(s) staged (%d field, %d battle), %d loop '
           'file(s), %.1f MB'
           % (res.locations, res.field_locations, res.battle_locations,
              res.copied, res.copied_bytes / 1048576.0)]
    if res.missing:
        shown = ', '.join('%s->%s' % pair for pair in res.missing[:6])
        out.append('       %d location(s) name an .ogg the active folders do '
                   'not ship (%s%s) -- dropped'
                   % (len(res.missing), shown,
                      ', ...' if len(res.missing) > 6 else ''))
    return out
