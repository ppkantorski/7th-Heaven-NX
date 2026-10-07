#!/usr/bin/env python3
"""ff7nx_scenemodel.py -- per-scene field model variants. BUILD 618z11.

The user (hardware 10-06): "in midgal and ztruck, cloud's dynamic weapon
should not be on his back ... the scene that comes after ztruck [midgal] ...
thats when he receives his sword"; "in ztruck, can we make zack the one who
is holding specifically the buster sword weapon on his back? ... and it
shouldnt be on his back in the scene after ... in the scene after ztruck he
is holding it in his hand".

WHAT 7TH HEAVEN DOES. Ninostyle Chibi Fixes gates its model folders on
the live FieldID and plot (PPV):
  * `Dynamic Weapons\\Cloud\\Field` (AAAA.HRC with the extra chest part AAAD1,
    the equipped weapon on Cloud's back) is OFF in FieldID 782 = midgal,
    so there Cloud has no sword on his back and BHFF.HRC's hand part BHJB1
    is the fb folder's static Buster Sword (he picks Zack's up);
  * `Dynamic Weapons\\Zack\\Field` (EJDC.HRC WITHOUT the Buster Sword on
    Zack's back, EJDF1) is ON only at PPV 1185..1186 -- the Zack flashback.
A static archive cannot read FieldID or PPV, so the build had the
everywhere-else versions in both scenes.

WHAT THIS DOES. The same decision, made per field at build time. For each
scene, the model loader's HRC name is pointed at a variant of that model
built from the user's own char.lgp -- the same skeleton and bone count (so
every animation still fits), only the parts list of one bone changed:

  ztruck  AAAA  (Cloud)        chest: AAAD1 removed (no weapon on his back)
          IBAD  (Zack, sitting) chest: + EJDF1 (Ninostyle's field Buster
                               Sword, the one EJDC wears on its back)
  midgal  AAAA  (Cloud)        chest: AAAD1 removed
          BHFF  (Cloud holding) r_hand: BHJB1 -> its static Buster Sword
                               (BHJC1.P, the range's value-0 member; the
                               dynamic copy asks for Z00C.., which the
                               runtime turns into the equipped weapon)
          EJDC  (Zack carrying Cloud) left as it is: the sword on his
                               back until he runs off; the Zack who
                               returns sword in hand is IBGD (618z12)

New char.lgp entries (ZNxx, a prefix no archive uses): the variant .hrc
files and one .rsd. Nothing outside the user's install is shipped. The eye
and mouth textures FFNx-style facial animation looks up by model name
(eye_<hrc>_<n>.tex) are copied in flevel to the variant's name, so Zack
keeps his blinking. A variant whose source is not as measured (another
model mod) is refused and the scene keeps the general model.

SEVENTH_NX_NO_SCENE_MODEL=1 disables.
"""
from __future__ import annotations

import os
import re
import struct

OFF_ENV = 'SEVENTH_NX_NO_SCENE_MODEL'
PREFIX = 'zn'
# variant hrc -> (source hrc, bone, op, part, replacement)
#   op 'drop': remove `part`; 'add': append `part`; 'swap': part -> repl
VARIANTS = {
    'ZNAA': ('AAAA', 'chest', 'drop', 'AAAD1', None),
    'ZNIB': ('IBAD', 'chest', 'add', 'EJDF1', None),
    'ZNBH': ('BHFF', 'r_hand', 'swap', 'BHJB1', 'ZNBS'),
}
# new rsd -> (source rsd, PLY/MAT/GRP stem to name instead)
RSDS = {'ZNBS': ('BHJB1', 'BHJC1')}
SCENES = {
    'ztruck': {'AAAA': 'ZNAA', 'IBAD': 'ZNIB'},
    # EJDC (entity zax) keeps the sword on its back: it is the Zack who
    # carries Cloud in, sets him down and runs off the bottom; the Zack who
    # comes back with the sword in hand is IBGD (entity zaxs, which starts
    # where zax left). BUILD 618z12, the user: "make zack's sword on his
    # back for the scene up to the point where he is wielding a weapon".
    'midgal': {'AAAA': 'ZNAA', 'BHFF': 'ZNBH'},
}
FACE_RE = r'^(eye|mouth)_%s(r?)_(\d+)\.tex$'


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


# ------------------------------------------------------------- the models

def _bones(text):
    """[(name, parent, length, [parts], line index of the parts line)]."""
    lines = text.split('\n')
    i = 0
    while i < len(lines) and not lines[i].strip().startswith(':BONES'):
        i += 1
    n = int(lines[i].split()[1])
    i += 1
    out = []
    for _ in range(n):
        while not lines[i].strip():
            i += 1
        name = lines[i].strip()
        parent = lines[i + 1].strip()
        length = lines[i + 2].strip()
        toks = lines[i + 3].split()
        if not toks or int(toks[0]) != len(toks) - 1:
            raise ValueError('bone %s: parts line %r' % (name, lines[i + 3]))
        out.append((name, parent, length, toks[1:], i + 3))
        i += 4
    return lines, out


def variant_hrc(src, bone, op, part, repl):
    """The source .hrc with one bone's parts list changed (bytes in/out,
    the file's own line endings kept)."""
    crlf = b'\r\n' in src
    text = src.decode('latin1').replace('\r\n', '\n')
    lines, bones = _bones(text)
    hit = [b for b in bones if b[0] == bone]
    if len(hit) != 1:
        raise ValueError('no single bone %r' % bone)
    _n, _p, _l, parts, at = hit[0]
    up = [p.upper() for p in parts]
    if op == 'drop':
        if part.upper() not in up:
            raise ValueError('%s has no part %s' % (bone, part))
        parts = [p for p in parts if p.upper() != part.upper()]
    elif op == 'add':
        if part.upper() in up:
            raise ValueError('%s already has %s' % (bone, part))
        parts = parts + [part]
    elif op == 'swap':
        if part.upper() not in up:
            raise ValueError('%s has no part %s' % (bone, part))
        parts = [repl if p.upper() == part.upper() else p for p in parts]
    else:
        raise ValueError(op)
    lines[at] = ' '.join([str(len(parts))] + parts)
    out = '\n'.join(lines)
    if crlf:
        out = out.replace('\n', '\r\n')
    return out.encode('latin1')


def variant_rsd(src, stem):
    """The source .rsd naming `stem` for PLY/MAT/GRP."""
    text = src.decode('latin1')
    n = 0
    for key in ('PLY', 'MAT', 'GRP'):
        text, k = re.subn(r'(?im)^(%s\s*=\s*)[A-Za-z0-9_]+(\.\w+)' % key,
                          lambda m: m.group(1) + stem + m.group(2), text)
        n += k
    if n != 3:
        raise ValueError('rsd has %d of PLY/MAT/GRP' % n)
    return text.encode('latin1')


def plan_char(get):
    """{new lower name: payload} for char.lgp; `get(lower name)` -> bytes or
    None. Raises when a source is not as measured."""
    out = {}
    for new, (stem_src, stem) in RSDS.items():
        src = get(stem_src.lower() + '.rsd')
        if src is None:
            raise ValueError('%s.rsd missing' % stem_src)
        if get(stem.lower() + '.p') is None:
            raise ValueError('%s.p missing' % stem)
        out[new.lower() + '.rsd'] = variant_rsd(src, stem)
    for new, (srcname, bone, op, part, repl) in VARIANTS.items():
        src = get(srcname.lower() + '.hrc')
        if src is None:
            raise ValueError('%s.hrc missing' % srcname)
        need = repl if op == 'swap' else (part if op == 'add' else None)
        if need and get(need.lower() + '.rsd') is None and \
                need.lower() + '.rsd' not in out:
            raise ValueError('%s.rsd missing' % need)
        out[new.lower() + '.hrc'] = variant_hrc(src, bone, op, part, repl)
    return out


def apply_to_char(path, log=lambda *_: None):
    """Add the variants to a finished char.lgp in place. Idempotent."""
    import lgp
    st = {'written': False, 'added': [], 'same': 0, 'refused': None}
    if disabled():
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
            tmp = path + '.scenemodel-tmp'
            a.write(tmp)
            os.replace(tmp, path)
            st['written'] = True
    except Exception as exc:                                   # noqa: BLE001
        st['refused'] = str(exc)[:120]
    return st


def summarise_char(st):
    if st.get('refused'):
        return ('  ! scene models (BUILD 618z11): char.lgp not changed -- %s'
                % st['refused'])
    if st.get('added') or st.get('same'):
        return ('  SCENE MODELS (BUILD 618z11): char.lgp %s (%s). %s=1 '
                'disables.' % ('variants added' if st.get('added')
                               else 'variants already present',
                               ', '.join(st.get('added') or ()) or
                               '%d entries' % st['same'], OFF_ENV))
    return ''


# ------------------------------------------------------------- the fields

def loader_hrcs(sec3):
    """[(offset of the 8-byte hrc name, hrc name)] of a model loader."""
    _ver, n, _na = struct.unpack_from('<HHH', sec3, 0)
    off = 6
    out = []
    for _ in range(n):
        ln = struct.unpack_from('<H', sec3, off)[0]
        off += 2 + ln + 2
        out.append((off, sec3[off:off + 8].split(b'\0')[0].decode('latin1')))
        off += 8 + 4
        na = struct.unpack_from('<H', sec3, off)[0]
        off += 2 + 30
        for _a in range(na):
            al = struct.unpack_from('<H', sec3, off)[0]
            off += 2 + al + 2
    if off > len(sec3):
        raise ValueError('model loader overruns')
    return out


def plan_field(name, sec3):
    """(new section 3, {old: new})."""
    swap = SCENES[name]
    s3 = bytearray(sec3)
    done = {}
    for off, hrc in loader_hrcs(bytes(sec3)):
        stem, _, ext = hrc.partition('.')
        new = swap.get(stem.upper())
        if new is None:
            continue
        if ext.upper() != 'HRC' or len(new) != len(stem):
            raise ValueError('hrc name %r' % hrc)
        s3[off:off + len(stem)] = new.encode('latin1')
        done[stem.upper()] = new
    missing = set(swap) - set(done)
    if missing:
        raise ValueError('no %s in the model loader' % ', '.join(sorted(missing)))
    return bytes(s3), done


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    st = {'names': [], 'refused': [], 'faces': 0}
    if disabled():
        return st
    encode = encode or archive.encode_field
    renamed = {}
    for name in SCENES:
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            parts[2], done = plan_field(name, parts[2])
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:100]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        renamed.update(done)
        st['names'].append('%s: %s' % (name, ', '.join(
            '%s->%s' % kv for kv in sorted(done.items()))))
    # facial textures follow the model name
    names = set(archive.index) | set(payloads)
    for old, new in sorted(renamed.items()):
        rx = re.compile(FACE_RE % re.escape(old.lower()))
        for n in sorted(names):
            m = rx.match(n)
            if not m:
                continue
            dst = '%s_%s%s_%s.tex' % (m.group(1), new.lower(), m.group(2),
                                     m.group(3))
            src = payloads.get(n)
            if src is None:
                src = archive.index[n]['payload']
            if dst in archive.index and archive.index[dst]['payload'] == src:
                continue
            payloads[dst] = src
            st['faces'] += 1
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  SCENE MODELS (BUILD 618z11): per-scene model variants '
                   '(%s); %d facial texture(s) copied to the new names. '
                   '%s=1 disables.' % ('; '.join(st['names']), st['faces'],
                                      OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! scene models %s: not applied -- %s' % (name, why))
    return '\n'.join(out)
