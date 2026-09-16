#!/usr/bin/env python3
"""
ff7nx_fieldsteps_data.py -- the per-field `CMFS` route trailers the field
footstep runtime reads, and the padded payloads they index.

WHY THE ROUTES ARE NOT IN THE MODULE
====================================
FFNx resolves a field footstep in this order:

    <field_name>_<walkmesh_triangle>_159
    <field_name>_159
    159

Cosmo's configuration has 664 field fallbacks and 2,040 triangle overrides.
There is no room for an index of that in `exefs/main`: the whole contiguous
data budget for every feature combined is about 1,835 bytes, and .data's tail
is immediately followed by the BSS the other bridges use (see `ff7nx_tables`
for the measurement and for the .data-tail trap specifically).

So the table goes in the FIELD FILE, which is where the data it describes
belongs anyway. The nine normal sections stay byte-for-byte parseable; the
field loader already ignores whatever follows them -- normally the LGP
`FINAL FANTASY7` marker, which the reader recognises and steps over.

Two consequences, both load-bearing:

  * the trailer lives in the field's DECOMPRESSED buffer, which is writable,
    so each triangle carries its own sequential cursor and the runtime needs
    no per-route BSS at all;
  * appending literal LZS tokens (`lgp.lzs_append_literals`) means the 664
    existing compressed fields are NOT recompressed -- which on this project's
    flevel is the difference between a few seconds and several minutes.

THE NAME GRAMMAR, WHICH IS THE ONE REAL AMBIGUITY
=================================================
Field filenames contain underscores and numeric suffixes. `fship_1` is a FIELD
NAME, so `[fship_1_159]` is "field fship_1, no triangle" -- not "field fship,
triangle 1". A generic `rsplit('_', 2)` gets that exactly backwards. The only
unambiguous grammar is to match real archive entry names first, longest to
shortest, which is what `_route_key` does.

THE RECORD FORMAT
=================
    header    'CMFS', u16 triangle span, then the fallback record
    record    u8 payload base, u8 count, u8 cursor   (3 bytes, direct-indexed)

    count == 0 and base == 0   fall through to the stock SFX 159 row
    base == 0xFF               Cosmo's `[...] = [0]`, deliberately silent

`audio.fmt` stays at exactly 750 rows; only padded ADPCM payloads are appended
to `audio.dat`, and they are selected through the same physical slot 744 the
world bridge proved.
"""
import os
import re
import struct

import audio_dat
import lgp
import sfxmod


LOGICAL_ID = 159
PHYSICAL_SLOT = 744
MAGIC = b'CMFS'
STOCK_TAIL = b'FINAL FANTASY7'
HEADER = struct.Struct('<4sH')
HEADER_BYTES = HEADER.size + 3       # magic, triangle span, fallback record
SILENT_BASE = 0xFF

_SECTION = re.compile(r'^\s*\[([^\]]+)\]\s*$')
_ROTATION = re.compile(r'^\s*(sequential|shuffle)\s*=\s*\[([^\]]*)\]')


def _field_names(archive_path):
    """Real 9-section field entries, longest first for underscore names."""
    archive = lgp.Archive(archive_path)
    return sorted((entry['name'].lower() for entry in archive.entries
                   if archive.is_field(entry)),
                  key=lambda name: (-len(name), name))


def _route_key(name, field_names):
    """``(field, triangle|None)`` for one FFNx field SFX-159 key.

    Field filenames themselves contain underscores and numeric suffixes --
    e.g. ``fship_1``.  A generic ``rsplit('_', 2)`` would incorrectly turn
    ``[fship_1_159]`` into triangle 1.  Match real archive names first, from
    longest to shortest, which is exactly the only unambiguous grammar.
    """
    suffix = '_%d' % LOGICAL_ID
    key = name.lower()
    if not key.endswith(suffix):
        return None
    stem = key[:-len(suffix)]
    for field in field_names:
        if stem == field:
            return field, None
        prefix = field + '_'
        if stem.startswith(prefix):
            tail = stem[len(prefix):]
            if re.fullmatch(r'-?\d+', tail):
                return field, int(tail)
    return None


def _one_config(text, field_names):
    """One config's field SFX-159 routes, preserving its own final value."""
    out, current = {}, None
    for line in text.splitlines():
        match = _SECTION.match(line)
        if match:
            current = _route_key(match.group(1).strip(), field_names)
            continue
        if current is None:
            continue
        match = _ROTATION.match(line)
        if not match:
            continue
        values = [int(item) for item in re.findall(r'-?\d+', match.group(2))]
        if values:
            out[current] = tuple(sfxmod.rotation_order(values))
    return out


def parse_configs(texts, archive_path):
    """Merge active config texts in normal 7th-Heaven order.

    Later folders replace a field/triangle route whole, the same precedence
    rule used for normal numeric rows and FFNx's layered lookup.
    """
    names = _field_names(archive_path)
    merged = {}
    for text in texts:
        merged.update(_one_config(text, names))
    return merged


def _encode_payloads(entries, sequences, oggs, cache_dir):
    """Encode each unique non-silent sequence once using SFX 159's format."""
    template = entries[LOGICAL_ID - 1]
    physical = entries[PHYSICAL_SLOT - 1]
    if template.empty or template.loop:
        raise ValueError('stock SFX %d is not a usable one-shot template'
                         % LOGICAL_ID)
    if physical.empty or not physical.fmt:
        raise ValueError('field footsteps require an encoded physical slot')
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
    payloads, descriptors = [], {}
    for values in sequences:
        if values == (0,):
            descriptors[values] = (SILENT_BASE, 0)
            continue
        start = len(payloads)
        for ogg_id in values:
            source = oggs.get(ogg_id)
            if not source:
                raise ValueError('field footstep %d.ogg is not active' % ogg_id)
            _cached, wav = sfxmod._encode_cached(source, template, cache_dir)
            entry = audio_dat.entry_from_wav(wav, like=template)
            if (entry.loop or entry.sample_rate != template.sample_rate or
                    entry.block_align != template.block_align or
                    entry.channels != template.channels):
                raise ValueError('field footstep %d does not match SFX %d '
                                 'format' % (ogg_id, LOGICAL_ID))
            if audio_dat.canonical_adpcm_fmt(entry.fmt) != physical.fmt:
                raise ValueError('field footstep %d does not match physical '
                                 'slot %d ADPCM format block' %
                                 (ogg_id, PHYSICAL_SLOT))
            payloads.append(entry.data)
        descriptors[values] = (start, len(values))
    if not payloads:
        raise ValueError('field footstep configuration only contains silence')
    if len(payloads) > SILENT_BASE:
        raise ValueError('field footstep payload index exceeds one-byte trailer')
    stride = max(map(len, payloads))
    stride = ((stride + template.block_align - 1) // template.block_align
              * template.block_align)
    return [item + b'\0' * (stride - len(item)) for item in payloads], stride, descriptors


def prepare(entries, texts, oggs, archive_path, cache_dir=None):
    """Return the compact field maps plus padded audio payloads.

    The caller has already run the world-footstep preparation, which reserves
    physical archive slot 744.  The field bridge deliberately reuses that
    channel-safe slot; field and world modes cannot run together.
    """
    routes = parse_configs(texts, archive_path)
    if not routes:
        return None
    if entries[PHYSICAL_SLOT - 1].empty:
        raise ValueError('field footsteps require world slot %d to be reserved'
                         % PHYSICAL_SLOT)
    by_field = {}
    for (field, triangle), values in routes.items():
        if triangle is not None and not 0 <= triangle <= 0xFFFF:
            raise ValueError('%s triangle %d is outside u16 range'
                             % (field, triangle))
        by_field.setdefault(field, {})[triangle] = values
    # Every mapping currently has a field-level fallback. Keep the runtime
    # correct if a future mod omits one: unresolved triangles use SFX 159.
    sequences = sorted({values for values in routes.values()
                        if values != (0,)})
    payloads, stride, descriptors = _encode_payloads(entries, sequences,
                                                       oggs, cache_dir)
    records = {}
    for field, table in by_field.items():
        records[field] = {triangle: descriptors.get(values, (SILENT_BASE, 0))
                          for triangle, values in table.items()}
    return {
        'fields': records,
        'payloads': payloads,
        'stride': stride,
        'variants': len(payloads),
        'routes': len(routes),
        'triangles': sum(1 for (_field, triangle) in routes
                         if triangle is not None),
        'fallbacks': sum(1 for (_field, triangle) in routes
                         if triangle is None),
    }


def _trailer(records):
    """Encode one field's direct-index table.

    Header fallback and each triangle record are ``base, count, cursor``.
    ``count == 0, base == 0`` means fall through to stock ID 159; ``base ==
    0xFF`` means this config intentionally selected FFNx's silent ``[0]``.
    """
    fallback = records.get(None, (0, 0))
    triangles = {key: value for key, value in records.items()
                 if key is not None}
    span = max(triangles, default=-1) + 1
    if span > 0xFFFF:
        raise ValueError('field triangle span exceeds trailer format')
    out = bytearray(HEADER.pack(MAGIC, span))
    out.extend((fallback[0], fallback[1], 0))
    rows = [(0, 0)] * span
    for triangle, value in triangles.items():
        rows[triangle] = value
    for base, count in rows:
        out.extend((base, count, 0))
    return bytes(out)


def read_trailer(raw):
    """Return a trailer at the canonical end of the nine sections, or None."""
    sections = lgp.split_sections(raw)
    canonical = lgp.join_sections(sections)
    tail = raw[len(canonical):]
    # Stock fields frequently carry the LGP marker inside their decompressed
    # payload. It is ignored by the game, but the new trailer follows it so
    # the original compressed section stream can remain byte-identical.
    if tail.startswith(STOCK_TAIL):
        tail = tail[len(STOCK_TAIL):]
    if len(tail) < HEADER_BYTES:
        return None
    magic, span = HEADER.unpack_from(tail)
    if magic != MAGIC or len(tail) != HEADER_BYTES + span * 3:
        return None
    return tail


def apply_to_flevel(src, dest, plan, replace_existing=False):
    """Append/replace trailers in a completed flevel archive.

    Recompose the nine declared sections before appending our data.  That is
    intentional: it removes the stock ignored 14-byte LGP marker and also
    makes a second build replace, rather than stack, an old CMFS trailer.
    """
    fields = plan.get('fields') if plan else None
    if not fields:
        return None
    archive = lgp.Archive(src)
    payloads = {}
    total_raw = 0
    for field, records in sorted(fields.items()):
        entry = archive.index.get(field)
        if entry is None or not archive.is_field(entry):
            raise ValueError('field footstep route names missing field %s' % field)
        trailer = _trailer(records)
        if replace_existing:
            raw = archive.decompressed(entry)
            canonical = lgp.join_sections(lgp.split_sections(raw))
            old = read_trailer(raw)
        else:
            raw = canonical = old = None
        if old is not None:
            # A previous CMFS build is uncommon (normally flevel was rebuilt
            # earlier in this run), but stacking trailers would select stale
            # data. Canonical re-encoding is the safe replacement path.
            payloads[field] = archive.encode_field(canonical + trailer)
        else:
            # Preserve all existing compressed field tokens and append only
            # literal trailer bytes. This avoids recompressing 664 fields.
            payloads[field] = lgp.lzs_append_literals(entry['payload'], trailer)
        total_raw += len(trailer)
    archive.replace(payloads)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    archive.write(dest)

    # Validate the actual archive, not the temporary source bytes.
    check = lgp.Archive(dest)
    # The primitive's exact LZS round-trip is covered by the static test. At
    # build time decode a deterministic spread of the archive to catch an
    # LGP write/header failure without spending two full passes over 664 large
    # fields every normal build.
    ordered = sorted(fields)
    checks = sorted({ordered[0], ordered[len(ordered) // 2], ordered[-1]})
    for field in checks:
        records = fields[field]
        got = read_trailer(check.decompressed(check.index[field]))
        if got != _trailer(records):
            raise ValueError('field footstep trailer did not survive for %s' % field)
    return {'fields': len(fields), 'raw_bytes': total_raw,
            'routes': plan['routes'], 'triangles': plan['triangles'],
            'fallbacks': plan['fallbacks']}
