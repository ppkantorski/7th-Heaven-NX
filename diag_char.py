#!/usr/bin/env python3
"""
Diagnose a built char.lgp against the vanilla one, on real files.

Usage:
    python3 diag_char.py <vanilla_char.lgp> <built_char.lgp>

vanilla  = workingdir/data/field/char.lgp  (your untouched dump)
built    = sdout/.../data/field/char.lgp   (what the tool produced)

Paste the entire output back. Nothing is written.
"""
import os
import re
import struct
import sys


def read_lgp(path):
    """Return (creator, [(name, payload), ...])."""
    with open(path, 'rb') as f:
        data = f.read()
    creator = data[:12]
    n = struct.unpack('<i', data[12:16])[0]
    entries = []
    off = 16
    toc = []
    for _ in range(n):
        e = data[off:off + 27]
        name = e[:20].split(b'\0')[0].decode('ascii', 'replace')
        offset = struct.unpack('<I', e[20:24])[0]
        toc.append((name, offset))
        off += 27
    for name, offset in toc:
        size = struct.unpack('<I', data[offset + 20:offset + 24])[0]
        payload = data[offset + 24:offset + 24 + size]
        entries.append((name, payload))
    return creator, entries


def resolve(ref, names):
    """Map an rsd/hrc reference to an entry name, trying extension maps."""
    ref = ref.lower()
    base, _, ext = ref.rpartition('.')
    for c in (ref, base + '.p', base + '.tex', base + '.rsd', ref + '.rsd'):
        if c in names:
            return c
    return None


def trace_model(hrc_name, entry_by_name):
    """Follow a .hrc through its .rsd -> .p/.tex references."""
    names = set(entry_by_name)
    hrc = entry_by_name[hrc_name].decode('latin1', 'replace')
    unresolved = []
    rsd_refs = []
    for line in hrc.splitlines():
        m = re.match(r'^\d+[ \t]+(.+)$', line)
        if m:
            rsd_refs += [t for t in m.group(1).split() if t.strip()]
    for r in rsd_refs:
        e = resolve(r, names)
        if e is None:
            unresolved.append(('rsd', r))
            continue
        rsd = entry_by_name[e].decode('latin1', 'replace')
        for mm in re.finditer(r'(?:PLY|MAT|GRP|TEX\[\d+\])=(\S+)', rsd):
            if resolve(mm.group(1), names) is None:
                unresolved.append((e, mm.group(1)))
    return len(rsd_refs), unresolved


def main(van_path, built_path):
    vc, ve = read_lgp(van_path)
    bc, be = read_lgp(built_path)
    vmap = {n.lower(): p for n, p in ve}
    bmap = {n.lower(): p for n, p in be}

    print('=== headers ===')
    print(f'vanilla creator : {vc}')
    print(f'built   creator : {bc}')
    print(f'creator matches Switch (\\x00\\x00SQUARESOFT): '
          f'{bc == bytes([0, 0]) + b"SQUARESOFT"}')
    print(f'vanilla entries : {len(ve)}')
    print(f'built   entries : {len(be)}   '
          f'({len(be) - len(ve):+d} vs vanilla)')

    # Was the mod actually applied? Count entries whose bytes differ.
    shared = set(vmap) & set(bmap)
    changed = sum(1 for n in shared if vmap[n] != bmap[n])
    added = set(bmap) - set(vmap)
    print(f'\n=== did the mod get in? ===')
    print(f'entries changed vs vanilla : {changed}')
    print(f'entries added   vs vanilla : {len(added)}')
    if changed == 0 and not added:
        print('  !! built char.lgp is IDENTICAL to vanilla — the mod did '
              'NOT get applied. The problem is upstream of the archive.')

    # Compression sanity: FF7 char models are stored raw. If vanilla entries
    # look LZSS-compressed but built ones do not (or vice versa), that breaks.
    def looks_lzss(p):
        # A raw .hrc starts with ':HEADER' or ':SKELETON'; .rsd with '@RSD'.
        return not (p[:1] in (b':', b'@') or p[:2] == b'\x00\x00')
    print(f'\n=== spot-check a few entries ===')
    for n in list(shared)[:4]:
        print(f'  {n:14s} vanilla {len(vmap[n]):>7}b  built {len(bmap[n]):>7}b  '
              f'{"differ" if vmap[n] != bmap[n] else "same"}')

    # Trace field models in the BUILT archive.
    print(f'\n=== model reference trace (built archive) ===')
    hrcs = [n for n in bmap if n.endswith('.hrc')]
    print(f'.hrc skeletons in built char.lgp : {len(hrcs)}')
    traced = 0
    total_unresolved = 0
    for hrc in hrcs:
        try:
            nref, unresolved = trace_model(hrc, bmap)
        except Exception:
            continue
        traced += 1
        if unresolved:
            total_unresolved += len(unresolved)
            if total_unresolved <= 20:
                print(f'  {hrc}: {len(unresolved)} unresolved '
                      f'-> {unresolved[:4]}')
    print(f'traced {traced} models; total unresolved references: '
          f'{total_unresolved}')
    if traced and total_unresolved == 0:
        print('  => every model resolves. The archive is COMPLETE and '
              'correct; a blank screen is then a load/path problem, not '
              'the file.')
    elif total_unresolved:
        print('  => models reference pieces that are not in the archive. '
              'This is why they render blank.')


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
