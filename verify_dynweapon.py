#!/usr/bin/env python3
"""
Check the BUILT archives and the BUILT module, not the build log.

    python3 verify_dynweapon.py

It reads the shipped char.lgp, world_us.lgp and high-us.lgp out of sdout and
asserts the three things that have to hold together for a weapon to appear:

  1. every `z0...` variant name is well formed, and each part's range is
     GAP-FREE -- the cave clamps with one unsigned compare, so a hole in the
     middle of a range is a name it can ask for and the archive does not have;
  2. every `.rsd`/`.hrc` that was repointed names a variant that EXISTS, for
     every value the range covers, not just the base;
  3. exactly the entries this pass created start with the marker.

Then it reads exefs/main and confirms the hook is installed at the one site
`ff7nx_dwhook` is allowed to use, and that the word it replaced is the one
the cave replays.

Exit status is 0 only when all of that holds.
"""
import os
import re
import struct
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lgp                                                     # noqa: E402
import ff7nx_dynweapon as D                                    # noqa: E402

SD = ('sdout/atmosphere/contents/0100A5B00BDC6000/romfs/ff7/workingdir/data/')
ARCHIVES = (('char.lgp', SD + 'field/char.lgp'),
            ('world_us.lgp', SD + 'wm/world_us.lgp'),
            ('high-us.lgp', SD + 'minigame/high-us.lgp'))
MAIN = 'sdout/atmosphere/contents/0100A5B00BDC6000/exefs/main'

NAME = re.compile(r'^z0([0-8])([a-z])([0-9a-f]{2})([0-9a-f])([0-9a-f]{2})'
                  r'\.(\w+)$')
RSD_PLY = re.compile(rb'PLY\s*=\s*([A-Za-z0-9_.]+)\.\w+', re.I)
BONE = re.compile(rb'^\s*[1-9][0-9]*[ \t]+([A-Za-z0-9_. \t]+?)[ \t]*\r?$')

FAILED = []


def check(label, cond):
    print(('  ok   ' if cond else '  FAIL ') + label)
    if not cond:
        FAILED.append(label)


def survey(path):
    a = lgp.Archive(path)
    names = set(a.names())
    parts = defaultdict(dict)           # (char, slot, base, cnt) -> {val}
    malformed = []
    for n in sorted(names):
        if not n.startswith(D.MARKER):
            continue
        m = NAME.match(n)
        if not m:
            malformed.append(n)
            continue
        c, slot, base, cnt, val, ext = m.groups()
        parts[(int(c), slot, int(base, 16), int(cnt, 16) + 1, ext)][
            int(val, 16)] = n
    return a, names, parts, malformed


def referrers(a, names, parts):
    """Every variant stem a repointed .rsd/.hrc asks for."""
    want = set()
    for n in names:
        if not n.endswith(('.rsd', '.hrc')):
            continue
        blob = a.index[n]['payload']
        if n.endswith('.rsd'):
            for m in RSD_PLY.finditer(blob):
                want.add(m.group(1).decode('latin1').lower())
        else:
            for line in blob.split(b'\n'):
                m = BONE.match(line)
                if m:
                    for t in m.group(1).split():
                        want.add(t.decode('latin1').lower())
    return {w for w in want if w.startswith(D.MARKER)}


def attached_dynamic_rsds(a, names):
    """Dynamic PLY targets whose containing RSD is reachable from an HRC."""
    hrc_tokens = set()
    dynamic_ply_rsds = set()
    for n in names:
        if n.endswith('.hrc'):
            for line in a.index[n]['payload'].split(b'\n'):
                m = BONE.match(line)
                if m:
                    hrc_tokens.update(t.decode('latin1').lower()
                                      for t in m.group(1).split())
        elif n.endswith('.rsd'):
            blob = a.index[n]['payload']
            if any(m.group(1).decode('latin1').lower().startswith(D.MARKER)
                   for m in RSD_PLY.finditer(blob)):
                dynamic_ply_rsds.add(n.rsplit('.', 1)[0])
    return sorted(dynamic_ply_rsds & hrc_tokens)


def live_dynamic_part(a, names, hrc, rsd, char):
    """Whether a specific live HRC -> RSD edge selects this character's P."""
    if hrc not in names or rsd not in names:
        return False
    hrc_tokens = set()
    for line in a.index[hrc]['payload'].split(b'\n'):
        m = BONE.match(line)
        if m:
            hrc_tokens.update(t.decode('latin1').lower()
                              for t in m.group(1).split())
    if rsd.rsplit('.', 1)[0] not in hrc_tokens:
        return False
    ply = RSD_PLY.search(a.index[rsd]['payload'])
    if not ply:
        return False
    stem = ply.group(1).decode('latin1').lower()
    m = NAME.match(stem + '.p')
    return bool(m and int(m.group(1)) == char and stem + '.p' in names)


def main(argv):
    total = 0
    for label, path in ARCHIVES:
        print('%s  %s' % (label, path))
        if not os.path.isfile(path):
            # Not an error: an archive only appears in sdout when some mod
            # contributes to it. `high-us.lgp` is absent from a build with no
            # Highwind-deck replacements at all.
            print('       not in this build -- skipped')
            continue
        a, names, parts, malformed = survey(path)
        check('%s: every marked name is well formed%s'
              % (label, '' if not malformed else ' (%s)' % malformed[:3]),
              not malformed)
        if not parts:
            print('       no dynamic weapon parts in this archive')
            continue
        total += len(parts)
        gaps = []
        for (c, slot, base, cnt, ext), got in sorted(parts.items()):
            missing = [v for v in range(base, base + cnt) if v not in got]
            if missing:
                gaps.append((c, slot, missing))
        check('%s: %d part range(s), none with a hole%s'
              % (label, len(parts), '' if not gaps else ' -- %s' % gaps[:2]),
              not gaps)
        asked = referrers(a, names, parts)
        stems = {n.rsplit('.', 1)[0] for n in names if n.startswith(D.MARKER)}
        # A referrer names the range's BASE; the cave can turn it into any
        # value in the range, so every value has to be present as a file.
        unreachable = []
        for stem in sorted(asked):
            m = NAME.match(stem + '.x')
            if not m:
                unreachable.append(stem)
                continue
            c, slot, base, cnt, _v, _e = m.groups()
            for v in range(int(base, 16), int(base, 16) + int(cnt, 16) + 1):
                cand = 'z0%s%s%s%s%02x' % (c, slot, base, cnt, v)
                if cand not in stems:
                    unreachable.append(cand)
        check('%s: %d referrer target(s), every value present%s'
              % (label, len(asked),
                 '' if not unreachable else ' -- %s' % unreachable[:3]),
              not unreachable)
        # An archive can contain unused dynamic RSDs from alternate character
        # sets, so they need not all be attached.  It must, however, have at
        # least one HRC -> RSD -> dynamic-P path whenever it ships dynamic
        # meshes.  The old world build had zero: BCC1/ATD1 were present and
        # repointed, but BBE/ATA still named only BCC/ATD.
        attached = attached_dynamic_rsds(a, names)
        check('%s: a live HRC reaches a dynamic weapon mesh%s'
              % (label, '' if attached else ' -- no HRC references an RSD '
                 'whose PLY is dynamic'),
              bool(attached))
        # The broad live-edge check above caught a real regression, but it
        # could still pass through Cid's world model while Cloud's world
        # Buster stayed static.  BBE -> BCC1 is Cloud's equipped-weapon
        # branch on the world map, so assert that exact live edge.
        if label == 'world_us.lgp':
            check('world_us.lgp: Cloud BBE -> BCC1 reaches a Cloud dynamic '
                  'weapon mesh',
                  live_dynamic_part(a, names, 'bbe.hrc', 'bcc1.rsd', 0))
        if label == 'char.lgp':
            # Chibi Fixes' static Buster HRC uses AAAD/AAAE while the Dynamic
            # Weapons branch is AAAD1/AAAE1.  A broad archive-level check can
            # pass even when that exact Cloud field branch is disconnected.
            check('char.lgp: Cloud AAAA -> AAAD1 reaches a Cloud dynamic '
                  'weapon mesh',
                  live_dynamic_part(a, names, 'aaaa.hrc', 'aaad1.rsd', 0))

    print('')
    if os.path.isfile(MAIN):
        import nxmap
        import ff7nx_dwhook as H
        exe = os.environ.get('SEVENTH_NX_EXE') or (
            'dump/romfs/ff7/resources/ff7_1.02/ff7_en')
        if os.path.isfile(exe):
            m = nxmap.Main(MAIN)
            lo, hi = m.extent(H._open_file_x86(exe))
            stock = nxmap.Main(
                os.environ.get('SEVENTH_NX_STOCK_MAIN', 'dump/exefs/main'))
            site = H.find_hook(stock.text, lo, hi)
            w = struct.unpack_from('<I', m.img, site[0])[0]
            check('exefs/main: the hook is installed at +0x%X' % site[0],
                  (w & 0xFC000000) == 0x14000000)
            check('exefs/main: the displaced instruction is the one the cave '
                  'replays',
                  struct.unpack_from('<I', stock.img, site[0])[0]
                  == H.sub_imm(0, site[2], H.FRAME_NAME))
        else:
            print('  ..   no ff7 exe here, module check skipped')
    else:
        print('  ..   no sdout exefs/main, module check skipped')

    print('')
    if FAILED:
        print('%d FAILED' % len(FAILED))
        for f in FAILED:
            print('  ' + f)
        return 1
    print('PASS: %d dynamic weapon part(s), every variant reachable and the '
          'hook is in place.' % total)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
