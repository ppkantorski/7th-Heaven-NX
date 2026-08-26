#!/usr/bin/env python3
"""Resolution rules for the field and world texture caps.

    python3 test_texcap.py [--main dump/exefs/main]

The interesting part is not the downscale -- tex.cap_dimensions has its own
coverage -- it is which cap each archive ends up using, because world_us.lgp
gained its own setting after char.lgp already had one and a CLI build that
predates the new variable has to keep behaving exactly as it did.

Also checks the default against real vanilla data, so "512 is a no-op on
everything vanilla ships" stays a measured claim rather than an assumption.
"""
import argparse
import os
import struct
import sys

import build
import tex


FAIL = []


def ok(cond, what):
    print(('  ok  ' if cond else '  FAIL  ') + what)
    if not cond:
        FAIL.append(what)


def caps(field, world):
    """(char.lgp cap, world_us.lgp cap) for one pair of env values."""
    for key, val in ((build.FIELD_TEX_CAP_ENV, field),
                     (build.WORLD_TEX_CAP_ENV, world)):
        if val is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = str(val)
    return build._field_tex_cap(), build._world_tex_cap()


def vanilla_world_textures(lgp):
    with open(lgp, 'rb') as f:
        data = f.read()
    count = struct.unpack('<I', data[12:16])[0]
    for i in range(count):
        entry = 16 + i * 27
        name = data[entry:entry + 20].split(b'\0')[0].decode('latin1')
        if not name.lower().endswith('.tex'):
            continue
        off = struct.unpack('<I', data[entry + 20:entry + 24])[0]
        size = struct.unpack('<I', data[off + 20:off + 24])[0]
        yield name, data[off + 24:off + 24 + size]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--lgp',
                    default='dump/romfs/ff7/workingdir/data/wm/world_us.lgp')
    args = ap.parse_args(argv)

    saved = {k: os.environ.get(k) for k in
             (build.FIELD_TEX_CAP_ENV, build.WORLD_TEX_CAP_ENV)}
    try:
        print('cap resolution')
        ok(caps(None, None) == (None, None),
           'nothing set leaves both archives uncapped, as before')
        ok(caps(512, None) == (512, 512),
           'the world cap falls back to the field cap when unset')
        ok(caps(0, None) == (None, None),
           'a disabled field cap disables the world fallback with it')
        ok(caps(512, 256) == (512, 256),
           'an explicit world cap overrides the field cap')
        ok(caps(0, 512) == (None, 512),
           'the world can be capped while char.lgp is not')
        ok(caps(512, 0) == (512, None),
           'an explicit 0 turns the world cap off while char.lgp stays on')
        ok(caps(None, 512) == (None, 512),
           'the world cap works with no field cap at all')
        ok(caps(512, 'nonsense') == (512, 512),
           'garbage falls back rather than disabling silently')
        ok(caps(None, -1) == (None, None),
           'a negative world cap reads as off, like the field cap')

        print('\nthe default')
        ok(build.WORLD_TEX_CAP_DEFAULT == 512,
           'the shipped default is 512px')
        ok(build.WORLD_TEX_CAP_DEFAULT >= 256,
           'the default is at least vanilla world_us.lgp\'s own maximum')

        if not os.path.exists(args.lgp):
            print('  -- %s not present; skipping the vanilla measurement'
                  % args.lgp)
        else:
            print('\nvanilla world_us.lgp against the default')
            biggest = 0
            touched = []
            total = 0
            for name, blob in vanilla_world_textures(args.lgp):
                parsed = tex.parse(blob)
                if not parsed:
                    continue
                total += 1
                biggest = max(biggest, parsed['width'], parsed['height'])
                new, _ = tex.cap_dimensions(blob, build.WORLD_TEX_CAP_DEFAULT)
                if new is not None:
                    touched.append(name)
            print('    %d textures, largest dimension %dpx' % (total, biggest))
            ok(total > 300, 'the archive parsed (sanity)')
            ok(biggest <= 256,
               'vanilla world textures top out at 256px, as measured')
            ok(not touched,
               'the 512px default rewrites nothing vanilla ships')
    finally:
        for key, val in saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    print()
    if FAIL:
        print('%d FAILED' % len(FAIL))
        return 1
    print('all good')
    return 0


if __name__ == '__main__':
    sys.exit(main())
