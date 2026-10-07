#!/usr/bin/env python3
"""ff7nx_idleanim.py -- [Tsunamods] Idle Animations (60fps). BUILD 619.

The user (10-06): "implement idle animations 60fps ... so that this mod works
alongside every other enabled mod". The mod ships as an unpacked folder
(iro.is_folder_mod), and on 7th Heaven PC it works through two runtime
mechanisms this port has no equivalent of, so it is applied here as DATA ONLY:
no exe, NSO or code-cave change of any kind.

WHAT THE MOD DOES ON PC
  * <char>N/char/<anim>.a  (Barret ADCB, Tifa ABCD, Aerith AVBF, Red AEAE,
    Cait AEHA, Cid ABIE, Vincent AFDF, Yuffie ACFB): a 700-2000-frame idle
    clip replacing that character's 1-3-frame standing pose BY NAME, inside a
    <Conditional> gated on the RuntimeVar FieldID -- in the fields it excludes
    (cut-scenes: mds7pb_1, semkin_5, datiao_6 ...) 7th Heaven falls through to
    the next mod (here 60 FPS Gameplay's interpolated pose).
  * cloudN/char/ORIC.a: Cloud's clip under a NEW name, and
    cloudanimflevels*/flevel.lgp/<field>.chunk.3 -- whole model-loader
    sections for ~670 fields whose only change is Cloud's (AAAA.HRC) slot-0
    animation -> "ORIC". Four copies gated on exe bytes: Echo-S (0x719C60 =
    233), SaC, Threat, and none of them.

WHAT THIS DOES INSTEAD
  * PRIORITY. With the 7th Heaven auto-sort it sits above 60 FPS Gameplay
    (same category; 7th Heaven's culture-aware name order puts "[" before
    digits -- see 7th_heaven_nx._name_key), so it wins those names where its
    gate is open, exactly as on PC.
  * character clips go into char.lgp under NEW names (OIBA, OITI, ...), and
    in every field whose maplist index passes the folder's FieldID gate each
    model-loader animation entry naming the old clip is repointed to the new
    one (4 letters, same length). Fields the gate excludes keep the old name
    and therefore 60 FPS Gameplay's pose: the fall-through, at build time.
  * Cloud: ORIC.a is added; the variant folder the mod's own gates select
    (Echo-S enabled -> the echo copy) is READ, never shipped: per field, which
    AAAA.HRC records it gives ORIC in slot 0. Only that one entry is changed
    in the BUILT loader, so every other mod's loader changes survive
    (applying the chunk.3 wholesale would put back vanilla NPC models over
    e.g. mds7's std_man10 and drop Echo-S's own). ztruck/midgal's per-scene
    ZNAA records (ff7nx_scenemodel) are not AAAA and are left alone.
  * Nothing from the mod is applied by the generic pipeline (files_for).

Memory: the clips are guest-heap data loaded with the field's models (about
0.3-0.75 MB each, at most one per party member in a field), against the
256 MB heap (FINDINGS-106 / ff7nx_heap). Bone counts are checked against the
clip they replace.

SEVENTH_NX_NO_IDLE_ANIM=1 disables.
"""
from __future__ import annotations

import hashlib
import os
import re
import struct

OFF_ENV = 'SEVENTH_NX_NO_IDLE_ANIM'
NEW = {'barret': 'OIBA', 'tifa': 'OITI', 'aerith': 'OIAE', 'red': 'OIRE',
       'cait': 'OICA', 'cid': 'OICI', 'vincent': 'OIVI', 'yuffie': 'OIYU'}
CLOUD_ANIM = 'ORIC'
CLOUD_HRC = 'AAAA'
ECHO_VAR = 0x719C60
ECHO_VALUE = 233
_MEM = re.compile(r'^\s*byte\s*:\s*(0x[0-9a-f]+|\d+)\s*$', re.I)

CURRENT = None          # set by build_plan (plan_for), read by the passes


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def handles(mod):
    m = getattr(mod, 'manifest', None)
    name = getattr(m, 'name', '') or ''
    if 'idle animations' not in name.lower():
        return False
    return any(str(getattr(o, 'id', '')).lower() == 'cloudanim'
               for o in getattr(m, 'options', ()))


def _anim_header(d):
    if not d or len(d) < 12:
        return None
    return struct.unpack_from('<III', d, 0)


def _files(folder, ext='.a'):
    out = {}
    sub = None
    try:
        sub = next((e for e in os.listdir(folder) if e.lower() == 'char'),
                   None)
    except OSError:
        return out
    if sub is None:
        return out
    d = os.path.join(folder, sub)
    for fn in sorted(os.listdir(d)):
        if fn.lower().endswith(ext) and not fn.startswith('.'):
            out[fn[:-len(ext)].upper()] = os.path.join(d, fn)
    return out


def _resolve(root, folder):
    path = root
    for part in folder.replace('\\', '/').split('/'):
        if not part:
            continue
        hit = next((e for e in os.listdir(path) if e.lower() == part.lower()),
                   None)
        if hit is None:
            return None
        path = os.path.join(path, hit)
    return path if os.path.isdir(path) else None


def plan_for(mod, settings, echo, read=None, log=lambda *_: None):
    """What the mod asks for with these options -- a plain dict:
         chars  [(char, old, new, path, field_gate)]
         cloud  (ORIC path, variant folder path) or None
    """
    import iro
    m = mod.manifest
    eff = m.defaults()
    eff.update(settings or {})

    def rt_read(spec):
        if spec.strip().lower() == 'fieldid':
            return None
        mm = _MEM.match(spec)
        if mm:
            va = int(mm.group(1), 0)
            if va == ECHO_VAR:
                return ECHO_VALUE if echo else 0
        v = read(spec) if read else None
        return 0 if v is None else v
    out = {'chars': [], 'cloud': None, 'notes': [], 'mod': mod.path}
    oric = None
    variants = []
    for folder, cond in m.folders:
        if not iro.evaluate(cond, eff):
            continue
        rt = m.folder_conditions.get(folder)
        if rt is not None and iro.evaluate_runtime(rt, rt_read) is False:
            continue
        path = _resolve(mod.cache, folder)
        if path is None:
            out['notes'].append('%s: folder missing' % folder)
            continue
        low = folder.lower()
        if low.startswith('cloudanimflevels'):
            if path not in variants:
                variants.append(path)
            continue
        char = re.sub(r'\d+$', '', low)
        files = _files(path)
        if char == 'cloud':
            if CLOUD_ANIM in files:
                oric = files[CLOUD_ANIM]
            continue
        if char not in NEW or len(files) != 1:
            out['notes'].append('%s: not understood (%d clips)'
                                % (folder, len(files)))
            continue
        (old, p), = files.items()
        gate = rt if rt is not None and any(
            v.strip().lower() == 'fieldid' for v in iro.runtime_vars(rt)) \
            else None
        out['chars'] = [c for c in out['chars'] if c[0] != char]
        out['chars'].append((char, old, NEW[char], p, gate))
    if oric and len(variants) == 1:
        out['cloud'] = (oric, variants[0])
    elif oric and not variants:
        out['notes'].append('Cloud: no field-loader variant is active for '
                            'these options, so ORIC is not used (as on PC)')
    elif len(variants) > 1:
        out['notes'].append('Cloud: %d loader variants active, not applied'
                            % len(variants))
    return out


def fingerprint():
    """Folded into the flevel archive key."""
    if CURRENT is None or disabled():
        return ''
    h = hashlib.sha1(b'IDLEANIM-1')
    files = [c[3] for c in CURRENT['chars']]
    if CURRENT['cloud']:
        files.append(CURRENT['cloud'][0])
        h.update(os.path.basename(CURRENT['cloud'][1]).lower().encode())
    for c in CURRENT['chars']:
        h.update(repr((c[0], c[1], c[2], repr(c[4]))).encode())
    for path in files:                  # contents: names repeat across the
        with open(path, 'rb') as f:     # mod's variant folders
            h.update(hashlib.sha1(f.read()).digest())
    return h.hexdigest()


# ------------------------------------------------------------ model loader

def loader_records(sec3):
    """[(hrc name upper, [(offset, entry bytes)])] -- entry = u16 len, name,
    u16. Raises unless the walk ends exactly at the section end."""
    _v, n, _s = struct.unpack_from('<HHH', sec3, 0)
    off = 6
    out = []
    for _ in range(n):
        ln = struct.unpack_from('<H', sec3, off)[0]
        off += 2 + ln + 2
        hrc = sec3[off:off + 8].split(b'\0')[0].decode('latin1')
        off += 8 + 4
        na = struct.unpack_from('<H', sec3, off)[0]
        off += 2 + 30
        an = []
        for _a in range(na):
            al = struct.unpack_from('<H', sec3, off)[0]
            an.append((off, bytes(sec3[off:off + 2 + al + 2])))
            off += 2 + al + 2
        out.append((hrc.split('.')[0].upper(), an))
    if off != len(sec3):
        raise ValueError('model loader is %d bytes, walked %d'
                         % (len(sec3), off))
    return out


def _entry_base(entry):
    al = struct.unpack_from('<H', entry, 0)[0]
    return entry[2:2 + al].split(b'\0')[0].split(b'.')[0].decode(
        'latin1').upper()


def aaaa_names(sec3):
    """The record (character file) names of the AAAA.HRC records, in order."""
    _v, n, _s = struct.unpack_from('<HHH', sec3, 0)
    off = 6
    out = []
    for _ in range(n):
        ln = struct.unpack_from('<H', sec3, off)[0]
        nm = bytes(sec3[off + 2:off + 2 + ln]).split(b'\0')[0].lower()
        off += 2 + ln + 2
        hrc = sec3[off:off + 8].split(b'\0')[0].split(b'.')[0].upper()
        off += 8 + 4
        na = struct.unpack_from('<H', sec3, off)[0]
        off += 2 + 30
        for _a in range(na):
            off += 2 + struct.unpack_from('<H', sec3, off)[0] + 2
        if hrc == CLOUD_HRC.encode():
            out.append(nm)
    return out


def cloud_flags(chunk3):
    """([True/False per AAAA record]: slot 0 is ORIC, the ORIC entry,
    {record name: flag})."""
    recs = [an for hrc, an in loader_records(chunk3) if hrc == CLOUD_HRC]
    flags = [bool(an) and _entry_base(an[0][1]) == CLOUD_ANIM for an in recs]
    oric = next((an[0][1] for an, f in zip(recs, flags) if f), None)
    names = aaaa_names(chunk3)
    by_name = dict(zip(names, flags)) if len(set(names)) == len(names) \
        else None
    return flags, oric, by_name


def plan_field(sec3, field_id, chars, flags=None, oric_entry=None,
               by_name=None):
    """(new section 3, {what: count}); unchanged input -> same bytes."""
    import iro
    s3 = bytes(sec3)
    recs = loader_records(s3)
    edits = []                      # (offset, old entry, new entry)
    done = {}
    for char, old, new, _p, gate in chars:
        if gate is not None and iro.evaluate_runtime(
                gate, lambda s: field_id if s.strip().lower() == 'fieldid'
                else None) is not True:
            continue
        for _hrc, an in recs:
            for off, e in an:
                if _entry_base(e) != old:
                    continue
                al = struct.unpack_from('<H', e, 0)[0]
                nm = e[2:2 + al]
                k = nm.upper().find(old.encode())
                if k != 0:
                    continue
                cased = new.lower() if nm[:4].islower() else new
                edits.append((off, e, e[:2] + cased.encode() + e[6:]))
                done[new] = done.get(new, 0) + 1
    if flags is not None and oric_entry is not None:
        built = [an for hrc, an in recs if hrc == CLOUD_HRC]
        bnames = aaaa_names(s3)
        if len(built) == len(flags):
            pick = flags
        elif by_name is not None and all(n in by_name for n in bnames):
            pick = [by_name[n] for n in bnames]
        elif all(flags):
            pick = [True] * len(built)
        elif not any(flags):
            pick = [False] * len(built)
        else:
            raise ValueError('%d AAAA records, the mod has %d (mixed)'
                             % (len(built), len(flags)))
        for an, on in zip(built, pick):
            if not on or not an or _entry_base(an[0][1]) == CLOUD_ANIM:
                continue
            off, e = an[0]
            al = struct.unpack_from('<H', e, 0)[0]
            if al >= 5 and e[2 + 4:3 + 4] == b'.':
                # the vanilla spelling, "ACFE.aki" -> "ORIC.aki": the mod
                # writes a bare "ORIC", which the engine also resolves, but
                # the same-length form is the one every vanilla loader uses
                ne = e[:2] + CLOUD_ANIM.encode() + e[6:]
            else:
                ne = oric_entry
            edits.append((off, e, ne))
            done[CLOUD_ANIM] = done.get(CLOUD_ANIM, 0) + 1
    if not edits:
        return s3, done
    out = bytearray()
    at = 0
    for off, e, ne in sorted(edits):
        if s3[off:off + len(e)] != e:
            raise ValueError('entry moved')
        out += s3[at:off] + ne
        at = off + len(e)
    out += s3[at:]
    out = bytes(out)
    struct.unpack_from('<H', out, 0)
    if [h for h, _ in loader_records(out)] != [h for h, _ in recs]:
        raise ValueError('re-parse disagrees')
    return out, done


def _maplist(archive, payloads):
    e = archive.index.get('maplist')
    d = payloads.get('maplist') if 'maplist' in payloads else (
        e['payload'] if e else None)
    if d is None:
        raise ValueError('no maplist')
    n = struct.unpack_from('<H', d, 0)[0]
    return {d[2 + 32 * i:34 + 32 * i].split(b'\0')[0].decode(
        'latin1').strip().lower(): i for i in range(n)}


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    st = {'fields': 0, 'counts': {}, 'refused': [], 'notes': []}
    cur = CURRENT
    if disabled() or cur is None or not (cur['chars'] or cur['cloud']):
        return st
    st['notes'] = list(cur.get('notes', ()))
    encode = encode or archive.encode_field
    try:
        ids = _maplist(archive, payloads)
    except Exception as exc:                                   # noqa: BLE001
        st['refused'].append(('maplist', str(exc)[:100]))
        return st
    chunks = {}
    if cur['cloud']:
        vdir = cur['cloud'][1]
        sub = next((e for e in os.listdir(vdir)
                    if e.lower() == 'flevel.lgp'), None)
        if sub:
            for fn in os.listdir(os.path.join(vdir, sub)):
                low = fn.lower()
                if low.endswith('.chunk.3'):
                    chunks[low[:-len('.chunk.3')]] = os.path.join(vdir, sub,
                                                                  fn)
    for name in sorted(ids):
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        flags = oric = by_name = None
        try:
            if name in chunks:
                with open(chunks[name], 'rb') as f:
                    flags, oric, by_name = cloud_flags(f.read())
            if not cur['chars'] and not oric:
                continue
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            new3, done = plan_field(parts[2], ids[name], cur['chars'],
                                    flags, oric, by_name)
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:100]))
            continue
        if not done:
            continue
        parts[2] = new3
        payloads[name] = encode(lgp.join_sections(parts))
        st['fields'] += 1
        for k, v in done.items():
            st['counts'][k] = st['counts'].get(k, 0) + v
    return st


def summarise(st):
    out = []
    if st.get('fields'):
        out.append('  IDLE ANIMATIONS (BUILD 619): %d field model loaders '
                   'repointed (%s). %s=1 disables.'
                   % (st['fields'], ', '.join('%s x%d' % kv for kv in
                                              sorted(st['counts'].items())),
                      OFF_ENV))
    for n in st.get('notes', ()):
        out.append('  idle animations: %s' % n)
    for name, why in st.get('refused', ())[:12]:
        out.append('  ! idle animations %s: not applied -- %s' % (name, why))
    if len(st.get('refused', ())) > 12:
        out.append('  ! idle animations: %d more refused'
                   % (len(st['refused']) - 12))
    return '\n'.join(out)


# ------------------------------------------------------------ char.lgp

def plan_char(get):
    """{entry name: bytes} to add to char.lgp; raises on a bone mismatch or
    a name collision with something else."""
    cur = CURRENT
    want = {}
    if cur is None:
        return want
    for char, old, new, path, _g in cur['chars']:
        with open(path, 'rb') as f:
            data = f.read()
        ref = _anim_header(get(old.lower() + '.a'))
        h = _anim_header(data)
        if ref is None or h is None or h[2] != ref[2]:
            raise ValueError('%s: %s has %s bones, %s.a %s'
                             % (char, os.path.basename(path),
                                h and h[2], old, ref and ref[2]))
        want[new.lower() + '.a'] = data
    if cur['cloud']:
        with open(cur['cloud'][0], 'rb') as f:
            data = f.read()
        ref = _anim_header(get('acfe.a'))
        h = _anim_header(data)
        if ref is None or h is None or h[2] != ref[2]:
            raise ValueError('ORIC.a bones %s, Cloud acfe.a %s'
                             % (h and h[2], ref and ref[2]))
        want[CLOUD_ANIM.lower() + '.a'] = data
    return want


def apply_to_char(path, log=lambda *_: None):
    import lgp
    st = {'written': False, 'added': [], 'same': 0, 'refused': None}
    if disabled() or CURRENT is None:
        return st
    try:
        a = lgp.Archive(path)

        def get(n):
            e = a.index.get(n)
            return e['payload'] if e else None
        want = plan_char(get)
        for name, data in sorted(want.items()):
            have = get(name)
            if have == data:
                st['same'] += 1
            elif have is not None:
                a.index[name]['payload'] = data
                st['added'].append(name)
            else:
                a.add(name, data)
                st['added'].append(name)
        if st['added']:
            tmp = path + '.idleanim-tmp'
            a.write(tmp)
            os.replace(tmp, path)
            st['written'] = True
    except Exception as exc:                                   # noqa: BLE001
        st['refused'] = str(exc)[:160]
    return st


def summarise_char(st):
    if st.get('refused'):
        return ('  ! idle animations (BUILD 619): char.lgp not changed -- %s'
                % st['refused'])
    if st.get('added') or st.get('same'):
        return ('  IDLE ANIMATIONS (BUILD 619): char.lgp clips %s (%s).'
                % ('added' if st.get('added') else 'already present',
                   ', '.join(st.get('added') or ()) or
                   '%d entries' % st['same']))
    return ''
