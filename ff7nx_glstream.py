#!/usr/bin/env python3
"""
ff7nx_glstream.py -- the per-draw buffer uploads get a streaming hint (BUILD 607).

THE COST (leakprobe v7b on hardware, 606c, Highwind high, zoomed out, facing down)
The port's GL device layer uploads each draw's vertices, indices and uniform
block with glBufferData, re-specifying a buffer every draw (~225 draws a
frame on the world map). Per frame:
    vertex upload   +0x11324D0  228 calls  20.0 us each  4.57 ms
    index upload    +0x11326E0  221 calls   7.1 us each  1.56 ms
    uniform upload  +0x1132A60  223 calls   1.8 us each  0.41 ms
6.5 ms of a 21.9 ms frame. All three pass GL_STATIC_DRAW (0x88E4): the hint
for data written once and drawn many times, the opposite of what they do.

THE CHANGE
Each method's one `mov w3, #0x88e4` (the usage argument) becomes
`mov w3, #0x88e0` (GL_STREAM_DRAW: written once, drawn a few times) -- or
0x88E8 (GL_DYNAMIC_DRAW) with SEVENTH_NX_GL_STREAM=dynamic. Nothing else
changes: same buffers, same sizes, same data, same draws. A usage hint only
tells the driver where to keep the storage; the result on screen is the same
by the GL spec. These are the only three glBufferData calls in the module, so
the change applies everywhere the game draws (fields and battles too).

SEVENTH_NX_GL_STREAM=0 leaves all three stock.
"""
import os
import struct

ENV = 'SEVENTH_NX_GL_STREAM'
GL_BUFFER_DATA = 0x1152040                    # the glBufferData import stub
STOCK = 0x52911C83                            # mov w3, #0x88e4  (GL_STATIC_DRAW)
HINTS = {'stream': 0x88E0, 'dynamic': 0x88E8}
# site (the usage mov) -> (target mov at site-4, GL target name)
SITES = {0x113250C: (0x52911240, 'GL_ARRAY_BUFFER'),          # mov w0, #0x8892
         0x113271C: (0x52911260, 'GL_ELEMENT_ARRAY_BUFFER'),  # mov w0, #0x8893
         0x1132A9C: (0x52914220, 'GL_UNIFORM_BUFFER')}        # mov w0, #0x8a11
AFTER = (0xAA1403E1, 0xAA1303E2)              # mov x1, x20 ; mov x2, x19


def mode(env=None):
    """'stream', 'dynamic' or None (stock)."""
    env = os.environ if env is None else env
    v = env.get(ENV, 'stream').strip().lower()
    if v in ('0', 'off', 'no', 'false', 'stock'):
        return None
    if v in ('dynamic', 'dyn'):
        return 'dynamic'
    return 'stream'


def enabled(env=None):
    return mode(env) is not None


def mov_w3(value):
    return 0x52800000 | (value << 5) | 3


def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def _bl_target(va, w):
    if (w >> 26) != 0x25:
        return None
    imm = w & 0x3FFFFFF
    if imm & 0x2000000:
        imm -= 0x4000000
    return va + 4 * imm


def _context_ok(img, site):
    before, _name = SITES[site]
    return (_word(img, site - 4) == before
            and _word(img, site + 4) == AFTER[0]
            and _word(img, site + 8) == AFTER[1]
            and _bl_target(site + 12, _word(img, site + 12)) == GL_BUFFER_DATA)


def read_state(img):
    """'stock', 'stream', 'dynamic' (all three sites alike), else 'unknown'."""
    names = {STOCK: 'stock'}
    names.update({mov_w3(v): k for k, v in HINTS.items()})
    got = set()
    for site in SITES:
        if not _context_ok(img, site):
            return 'unknown'
        got.add(names.get(_word(img, site), 'unknown'))
    return got.pop() if len(got) == 1 else 'unknown'


def build_patches(img, which='stream'):
    for site in SITES:
        if not _context_ok(img, site):
            raise ValueError('+0x%X is not the expected mov w0, target ; mov w3, usage ; '
                             'mov x1, x20 ; mov x2, x19 ; bl glBufferData' % (site - 4))
        if _word(img, site) != STOCK:
            raise ValueError('+0x%X is %08X, not GL_STATIC_DRAW' % (site, _word(img, site)))
    return {site: mov_w3(HINTS[which]) for site in SITES}


def apply_to_nso(src, dest, log=lambda *_: None, which=None):
    """Write `dest` from `src`. False when nothing was written."""
    from pathlib import Path as _P
    import nxmap
    import nso_patcher
    which = which or mode() or 'stream'
    module = nxmap.Main(str(src))
    state = read_state(module.img)
    if state == which:
        log('  glstream: already %s; nothing to write' % which)
        return False
    if state != 'stock':
        log('! glstream: the three glBufferData sites are %s -- not stock; '
            'nothing was written' % state)
        return False
    try:
        words = build_patches(module.img, which)
    except Exception as exc:                                  # noqa: BLE001
        log('! glstream: %s: %s' % (type(exc).__name__, exc))
        log('  nothing was written; the module is unchanged')
        return False

    def hx(v):
        return ' '.join('%02X' % b for b in struct.pack('<I', v))
    spec = {'name': 'glstream: per-draw buffer uploads use GL_%s_DRAW' % which.upper(),
            'patches': [{'name': '+0x%X' % va, 'va': hex(va),
                         'expect': hx(_word(module.img, va)),
                         'set': hx(words[va])} for va in sorted(words)]}
    nso = nso_patcher.read_nso(_P(str(src)))
    nso_patcher.apply_spec(nso, spec)
    data = nso_patcher.rebuild(nso)
    os.makedirs(os.path.dirname(os.path.abspath(str(dest))), exist_ok=True)
    with open(str(dest), 'wb') as f:
        f.write(data)
    log('  glstream: the vertex, index and uniform uploads (re-specified every '
        'draw) pass GL_%s_DRAW instead of GL_STATIC_DRAW' % which.upper())
    return True


def main(argv):
    """Try it on a BUILT main without a rebuild:
        python3 ff7nx_glstream.py <exefs/main> --on [dynamic] | --off | --show"""
    import shutil
    if len(argv) < 3 or argv[2] not in ('--on', '--off', '--show'):
        print(main.__doc__)
        return 2
    import nxmap
    path, backup = argv[1], argv[1] + '.pre-glstream'
    if argv[2] == '--show':
        print('  glstream is %s' % read_state(nxmap.Main(path).img))
        return 0
    if argv[2] == '--off':
        if not os.path.exists(backup):
            print('  no %s -- rebuild instead' % os.path.basename(backup))
            return 1
        shutil.copyfile(backup, path)
        os.remove(backup)
        print('  glstream -> off')
        return 0
    which = 'dynamic' if len(argv) > 3 and argv[3].startswith('dyn') else 'stream'
    if os.path.exists(backup):
        print('  already on (%s exists); --off first' % os.path.basename(backup))
        return 1
    shutil.copyfile(path, backup)
    tmp = path + '.glstream-tmp'
    if not apply_to_nso(backup, tmp, print, which):
        os.remove(backup)
        return 1
    os.replace(tmp, path)
    print('  glstream -> %s' % which)
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main(sys.argv))
