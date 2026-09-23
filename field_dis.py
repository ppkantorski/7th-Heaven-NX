#!/usr/bin/env python3
r"""
field_dis.py -- disassemble a field's script section, and diff two archives.

    python3 field_dis.py <flevel.lgp> bugin1c --list
    python3 field_dis.py <flevel.lgp> bugin1c --dis
    python3 field_dis.py <flevel.lgp> bugin1c --diff      # vs the vanilla lgp
    python3 field_dis.py <flevel.lgp> bugin1c --opcodes   # histogram, both

WHY THIS EXISTS
===============
MEASURED on hardware, 2026-09-23: with `bugin1c`'s VANILLA script swapped in
(`field_swap.py`), the observatory scene **plays through**.  With the shipped
Echo-S script it stops on a black screen, music still running, part-way
through -- not at the end.

That result ends a five-deep bisection of the runtime (SCR2D initializer
clamp, day/night, the movie frame counter, the field voice runtime, and the
heap abort) in which every candidate came back negative.  The variable is the
script.

So the question is no longer "which of our features breaks it" but "what does
the Echo-S script ask for that the stock one does not, and do we answer it".
That is a diff, and this is the tool that takes it.

Instruction lengths come from `PyFF7.field.instruction_size` -- the same table
`echo_s_flevel.py` already uses to walk these scripts -- not from a table
written for this file.

SECTION 1 LAYOUT (32-byte header)
=================================
    u16 version (0x0502)     u8 n_entities        u8 n_models
    u16 string_table_off     u16 n_akao           u16 scale
    u16 blank[3]             char creator[8]      char name[8]
then n_entities * 8 bytes of entity names, n_akao * u32, then
n_entities * 32 * u16 script entry offsets, then the code.

Entry offsets are relative to the START OF SECTION 1, and several entries
routinely point at the same offset (an unused script is aliased to the
actor's default).  Only distinct targets are disassembled.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import pyff7_path                                              # noqa: E402
pyff7_path.ensure(_HERE)
from PyFF7.field import OP, instruction_size                   # noqa: E402

N_SCRIPTS_PER_ACTOR = 32
HEADER = '<HBBHHH6x8s8s'
HEADER_LEN = 32


class Script:
    __slots__ = ('actor', 'index', 'offset', 'code')

    def __init__(self, actor, index, offset, code):
        self.actor, self.index = actor, index
        self.offset, self.code = offset, code


def parse(section1):
    """-> (meta dict, [entity names], {offset: [(actor, index), ...]})"""
    (ver, n_ent, n_mod, str_off, n_akao, scale,
     creator, name) = struct.unpack_from(HEADER, section1, 0)
    p = HEADER_LEN
    names = []
    for _ in range(n_ent):
        names.append(section1[p:p + 8].rstrip(b'\0').decode('latin-1'))
        p += 8
    p += 4 * n_akao
    entries = []
    for _ in range(n_ent):
        row = struct.unpack_from('<%dH' % N_SCRIPTS_PER_ACTOR, section1, p)
        entries.append(row)
        p += 2 * N_SCRIPTS_PER_ACTOR
    meta = dict(version=ver, n_entities=n_ent, n_models=n_mod,
                string_table=str_off, n_akao=n_akao, scale=scale,
                creator=creator.rstrip(b'\0').decode('latin-1'),
                name=name.rstrip(b'\0').decode('latin-1'),
                code_start=p)
    return meta, names, entries


def _decode_one(section1, off):
    op = section1[off]
    n, _ = OP[op]
    size = instruction_size(section1, off)
    raw = section1[off:off + size]
    return n, size, raw


def walk(section1, start, limit):
    """Linear disassembly from `start` up to `limit`.

    Field script code is contiguous -- no data islands between entry points --
    so a linear walk is exact here.  Anything that decodes to a zero or
    negative size is reported rather than skipped, because that would mean the
    table and the payload disagree and every offset after it is a guess.
    """
    out, off = [], start
    while off < limit:
        try:
            n, size, raw = _decode_one(section1, off)
        except Exception as e:                        # noqa: BLE001
            out.append((off, '??', 0, b'', str(e)))
            break
        if size <= 0:
            out.append((off, n, 0, raw, 'non-positive size'))
            break
        out.append((off, n, size, raw, None))
        off += size
    return out


def scripts_of(section1, meta, entries):
    """{offset: [(actor_index, script_index), ...]} for distinct offsets."""
    where = {}
    for ai, row in enumerate(entries):
        for si, off in enumerate(row):
            where.setdefault(off, []).append((ai, si))
    return where


def _fmt(ins, base):
    off, n, size, raw, err = ins
    body = ' '.join('%02X' % b for b in raw[1:]) if size > 1 else ''
    s = '  +%04X  %-8s %s' % (off - base, n, body)
    if err:
        s += '   !! %s' % err
    return s.rstrip()


def _labelled(section1, meta, names, entries):
    """[(label, [instruction, ...])] -- one block per distinct entry offset."""
    where = scripts_of(section1, meta, entries)
    offs = sorted(o for o in where if o >= meta['code_start'])
    blocks = []
    for i, o in enumerate(offs):
        end = offs[i + 1] if i + 1 < len(offs) else meta['string_table']
        owners = where[o]
        label = ', '.join('%s[%d].s%d' % (names[a] if a < len(names) else '?',
                                          a, s) for a, s in owners)
        blocks.append((label, o, walk(section1, o, end)))
    return blocks


def cmd_list(section1, log=print):
    meta, names, entries = parse(section1)
    log('  %s  (creator %r, name %r)' % (meta['name'], meta['creator'],
                                         meta['name']))
    log('  %d entities, %d models, %d akao, code at +%04X, strings at +%04X'
        % (meta['n_entities'], meta['n_models'], meta['n_akao'],
           meta['code_start'], meta['string_table']))
    for i, n in enumerate(names):
        row = entries[i]
        used = sorted(set(row))
        log('    [%2d] %-10s %d distinct entry offset(s)' % (i, n, len(used)))
    return 0


def cmd_dis(section1, log=print):
    meta, names, entries = parse(section1)
    for label, base, ins in _labelled(section1, meta, names, entries):
        log('')
        log('%s   @ +%04X' % (label, base))
        for x in ins:
            log(_fmt(x, base))
    return 0


def histogram(section1):
    meta, names, entries = parse(section1)
    h = {}
    for _, _, ins in _labelled(section1, meta, names, entries):
        for off, n, size, raw, err in ins:
            h[n] = h.get(n, 0) + 1
    return h


def cmd_opcodes(a_sec, b_sec, a_name, b_name, log=print):
    ha, hb = histogram(a_sec), histogram(b_sec)
    keys = sorted(set(ha) | set(hb))
    log('  %-10s %8s %8s   %s' % ('opcode', a_name, b_name, 'delta'))
    for k in keys:
        x, y = ha.get(k, 0), hb.get(k, 0)
        flag = '' if x == y else ('   <-- %+d' % (y - x))
        log('  %-10s %8d %8d%s' % (k, x, y, flag))
    only_b = [k for k in keys if ha.get(k, 0) == 0]
    only_a = [k for k in keys if hb.get(k, 0) == 0]
    log('')
    log('  only in %s: %s' % (b_name, ', '.join(only_b) or '(none)'))
    log('  only in %s: %s' % (a_name, ', '.join(only_a) or '(none)'))
    return 0


def cmd_diff(a_sec, b_sec, a_name, b_name, log=print):
    import difflib
    def lines(sec):
        meta, names, entries = parse(sec)
        out = []
        for label, base, ins in _labelled(sec, meta, names, entries):
            out.append('=== %s' % label)
            out += [_fmt(x, base) for x in ins]
        return out
    d = difflib.unified_diff(lines(a_sec), lines(b_sec),
                             fromfile=a_name, tofile=b_name, lineterm='', n=2)
    for line in d:
        log(line)
    return 0


def _section1(path, field):
    import lgp as L
    ar = L.Archive(path)
    e = ar.index.get(field)
    if e is None:
        raise SystemExit('  ! %s is not in %s' % (field, path))
    return L.split_sections(ar.decompressed(e))[0]


def _find_vanilla(flevel):
    root = flevel
    rel = (('dump', 'romfs', 'ff7', 'workingdir', 'data', 'field',
            'flevel.lgp'),
           ('game_data_files', 'field', 'flevel.lgp'))
    for _ in range(14):
        nxt = os.path.dirname(root)
        if nxt == root:
            return None
        root = nxt
        for parts in rel:
            cand = os.path.join(root, *parts)
            if os.path.exists(cand) and \
                    os.path.abspath(cand) != os.path.abspath(flevel):
                return cand
    return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('flevel')
    ap.add_argument('field')
    ap.add_argument('--vanilla-lgp')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--dis', action='store_true')
    ap.add_argument('--diff', action='store_true')
    ap.add_argument('--opcodes', action='store_true')
    a = ap.parse_args(argv)

    shipped = _section1(a.flevel, a.field)
    if a.list:
        return cmd_list(shipped)
    if a.dis:
        return cmd_dis(shipped)

    van = a.vanilla_lgp or _find_vanilla(a.flevel)
    if not van or not os.path.exists(van):
        print('  ! need --vanilla-lgp')
        return 1
    vanilla = _section1(van, a.field)
    if a.opcodes:
        return cmd_opcodes(vanilla, shipped, 'vanilla', 'shipped')
    return cmd_diff(vanilla, shipped, 'vanilla', 'shipped')


if __name__ == '__main__':
    raise SystemExit(main())
