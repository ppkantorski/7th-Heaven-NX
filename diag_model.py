#!/usr/bin/env python3
"""
diag_model.py -- compare one field model between vanilla and built char.lgp.

    python3 diag_model.py --vanilla char.lgp --built sdout/.../char.lgp --hrc BEEC

BEEC.HRC is the Sector 7 shop dog (`:SKELETON dog2_sk`, 28 bones, 22 RSDs,
every one of them NTEX=0 in vanilla -- the whole model is untextured
flat-shaded geometry).

For every part of the model this prints where it came from and whether it
changed, so a mixed vanilla/mod model is visible at a glance. A field model
is an .hrc naming a bone tree, one .rsd per bone, one .p per rsd and zero or
more .tex per rsd; replacing some of those and not others is what puts a
polygon somewhere it does not belong.

What to look for, in order:

  BONE LENGTHS CHANGED but the .p files did not (or the reverse). The .hrc
  carries a length per bone and the .p geometry is authored against it. Mix
  them and a part is drawn at the wrong distance from its joint -- a slab
  sticking out of a foot is precisely that.

  TEX  appearing where vanilla had none.

  MISSING parts -- a .p or .rsd named by the model that is in neither
  archive.
"""
import argparse
import re
import struct
import sys

RE_TOKEN = re.compile(rb'(?m)^\s*(\d+)\s+([A-Za-z0-9_]{1,8}(?:\s+[A-Za-z0-9_]{1,8})*)\s*$')
RE_TEX = re.compile(rb'TEX\[\d+\]\s*=\s*([A-Za-z0-9_]+)\.\w+', re.I)
RE_PLY = re.compile(rb'PLY\s*=\s*([A-Za-z0-9_]+)\.\w+', re.I)


def load_lgp(path):
    d = open(path, 'rb').read()
    n = struct.unpack('<i', d[12:16])[0]
    p, idx = 16, {}
    for _ in range(n):
        rec = d[p:p + 27]
        p += 27
        name = rec[:20].split(b'\0')[0].decode('ascii', 'replace').lower()
        idx.setdefault(name, struct.unpack('<I', rec[20:24])[0])
    out = {}
    for name, off in idx.items():
        sz = struct.unpack('<I', d[off + 20:off + 24])[0]
        out[name] = d[off + 24:off + 24 + sz]
    return out


def bones(blob):
    """
    [(bone_name, length, [rsd, ...]), ...] in declaration order.

    An .hrc bone is four consecutive non-blank lines -- name, parent, length,
    then "<n> RSD..." -- separated by blank lines whose count is not reliable
    (CRLF sources double them). Blank lines are dropped first so the -3/-1
    offsets hold whatever the line endings are.
    """
    if not blob:
        return []
    lines = [l.strip() for l in blob.replace(b'\r\n', b'\n').split(b'\n')]
    lines = [l for l in lines if l]
    out = []
    for i, l in enumerate(lines):
        m = RE_TOKEN.match(l)
        if not m or int(m.group(1)) < 1:
            continue
        name = lines[i - 3].decode('ascii', 'replace') if i >= 3 else '?'
        try:
            length = float(lines[i - 1])
        except (ValueError, IndexError):
            length = None
        out.append((name, length,
                    [t.decode('ascii', 'replace').lower()
                     for t in m.group(2).split()]))
    return out


def rsd_refs(blob):
    if not blob or b'@RSD' not in blob[:16]:
        return None, []
    ply = RE_PLY.search(blob)
    return (ply.group(1).decode('ascii', 'replace').lower() if ply else None,
            [t.decode('ascii', 'replace').lower() for t in RE_TEX.findall(blob)])


def state(van, built, key):
    a, b = van.get(key), built.get(key)
    if b is None:
        return 'MISSING', a, b
    if a is None:
        return 'ADDED  ', a, b
    return ('same   ' if a == b else 'CHANGED'), a, b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--vanilla', required=True)
    ap.add_argument('--built', required=True)
    ap.add_argument('--hrc', default='BEEC', help='model, e.g. BEEC')
    a = ap.parse_args()

    van, built = load_lgp(a.vanilla), load_lgp(a.built)
    hrc = a.hrc.lower() + '.hrc'
    if hrc not in van and hrc not in built:
        sys.exit('%s is in neither archive' % hrc)

    st, vb, bb = state(van, built, hrc)
    print('%s  %s   vanilla %s bytes, built %s bytes'
          % (hrc, st, len(vb) if vb else '-', len(bb) if bb else '-'))
    vbones, bbones = bones(vb), bones(bb)
    print('   bones: vanilla %d, built %d' % (len(vbones), len(bbones)))

    if vb and bb and vb != bb:
        vl = {n: l for n, l, _ in vbones}
        bl = {n: l for n, l, _ in bbones}
        moved = [n for n in vl if n in bl and vl[n] != bl[n]]
        if moved:
            print('   BONE LENGTHS CHANGED (%d):' % len(moved))
            for n in moved[:12]:
                print('       %-10s %s -> %s' % (n, vl[n], bl[n]))
        gone = sorted(set(vl) - set(bl))
        new = sorted(set(bl) - set(vl))
        if gone:
            print('   bones removed: %s' % ', '.join(gone))
        if new:
            print('   bones added:   %s' % ', '.join(new))

    print()
    print('   %-10s %-8s %-10s %-8s %-10s %-8s  %s'
          % ('BONE', 'RSD', '', 'PLY', '', 'TEX', ''))
    counts = {}
    suspects = []
    for name, length, rsds in (bbones or vbones):
        for rsd in rsds:
            rkey = rsd + '.rsd'
            rst, rv, rbl = state(van, built, rkey)
            counts[rst.strip()] = counts.get(rst.strip(), 0) + 1
            ply_v, tex_v = rsd_refs(rv)
            ply_b, tex_b = rsd_refs(rbl)
            ply = ply_b or ply_v
            pst = state(van, built, ply + '.p')[0] if ply else 'no ply '
            counts[pst.strip()] = counts.get(pst.strip(), 0) + 1
            tex = ','.join(tex_b) if tex_b else '-'
            print('   %-10s %-8s %-10s %-8s %-10s %s'
                  % (name[:10], rsd.upper(), rst,
                     (ply or '-').upper(), pst, tex))
            if tex_b and not tex_v:
                suspects.append('%s: textured by the mod, untextured in vanilla'
                                % rsd.upper())
            if rst == 'CHANGED' and pst == 'same   ':
                suspects.append('%s: rsd replaced but its .p left vanilla'
                                % rsd.upper())
            if rst == 'same   ' and pst == 'CHANGED':
                suspects.append('%s: .p replaced but its .rsd left vanilla'
                                % rsd.upper())
            for t in tex_b:
                if t + '.tex' not in built:
                    suspects.append('%s: references %s.tex, not in the archive'
                                    % (rsd.upper(), t.upper()))

    print()
    print('   parts: ' + ', '.join('%s %d' % (k, v)
                                   for k, v in sorted(counts.items())))
    if suspects:
        print('\n   SUSPECTS:')
        for s in dict.fromkeys(suspects):
            print('       ' + s)
    else:
        print('\n   nothing mixed -- this model is wholly vanilla or wholly '
              'from the mod')


if __name__ == '__main__':
    sys.exit(main())
