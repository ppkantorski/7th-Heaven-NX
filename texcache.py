#!/usr/bin/env python3
r"""
texcache.py -- stop the texture manager recycling deleted textures. One word,
on a BUILT main. No rebuild.

    python3 texcache.py sdout/atmosphere/contents/0100A5B00BDC6000/exefs/main --off
    python3 texcache.py <same main> --on      (the port's recycling back)
    python3 texcache.py <same main> --show

WHY (PROBE-599)
The port's texture manager does not destroy a deleted texture: delete
(+0x42D0) parks the object in a cache keyed ONLY by width x height (up to 10
per size), and the next upload of that size (+0x4620) takes it and just
copies the new pixels in (+0x47AC..+0x4894) -- it never redefines the
object's storage the way a fresh upload does (+0x46F4: vtable[120] on the
texture and on its 4x filtered copy).

Objects made by the loader's OTHER path -- framebuffer-copy textures
(tex_header version 100, +0x10D7050, storage mode 2, no pixels, e.g. the
screen transitions) -- go into the same cache. A model texture that inherits
one gets its pixels written into storage defined for something else.

texprobe v3 on the second fship_25 entry: every texture's image and palette
are identical to the clean visit and every id is live and owned by its slot
(texguard) -- so what is wrong is the object behind the id, and this cache is
the only way an id gets an object that was used before.

--off patches +0x4364 `b.hi +0x43D4` (keep it when the size already has >9
cached) to `b +0x43D4`: every delete destroys. The lookup in +0x4620 then
never finds anything, and nothing sits in the cache.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import a64 as A                              # noqa: E402
import diag_toggle as D                      # noqa: E402

SITE = 0x4364
STOCK_WORD = 0x54000388                      # b.hi +0x43D4
DESTROY = 0x43D4
OFF_WORD = A.b(SITE, DESTROY)                # b +0x43D4


def main(argv):
    if len(argv) < 3 or argv[2] not in ('--on', '--off', '--show'):
        print(__doc__)
        return 2
    path = argv[1]
    cur = D.read_words(path, [SITE])[SITE]
    if cur not in (STOCK_WORD, OFF_WORD):
        print('  +0x%X is %08X -- neither the stock word nor this patch; '
              'refusing' % (SITE, cur))
        return 1
    if argv[2] == '--show':
        print('  texture recycling is %s'
              % ('OFF (every delete destroys)' if cur == OFF_WORD else 'on (stock)'))
        return 0
    want = OFF_WORD if argv[2] == '--off' else STOCK_WORD
    if cur == want:
        print('  already %s' % argv[2][2:])
        return 0
    D.write_words(path, {SITE: want}, 'texcache %s' % argv[2][2:])
    print('  texture recycling -> %s' % ('OFF' if want == OFF_WORD else 'on'))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
