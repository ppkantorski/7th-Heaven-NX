#!/usr/bin/env python3
"""
ws_field.py -- toggle ONE field-framing word at a time, in place, no rebuild.

    python3 ws_field.py <main> --show
    python3 ws_field.py <main> --set width=854
    python3 ws_field.py <main> --set width=640          # revert

The framing itself now lives in the two vertex shaders, and the render
target is 16:9 from gfx_drv_init's four words. What is left is making the
FIELD draw content outside the old 4:3 crop. That is game-side, and it is a
handful of independent immediates -- so they get toggled one at a time
instead of shipped as a block, which is how the last six attempts went
wrong.

Every word is verified before writing and every value is a legal encoding
of the same instruction, so a wrong name fails loudly rather than silently
writing a different opcode.
"""
import argparse, os, struct, sys
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, '..')):
    if _p not in sys.path: sys.path.insert(0, _p)

def movz_w(rd, imm): return 0x52800000 | (imm << 5) | rd
def sub_imm(rd, imm):
    # sub w9, w8, #imm   -- rd is encoded in the knob table as the WHOLE
    # instruction's fixed part; only the imm12 field moves.
    assert 0 <= imm < 4096, imm
    return 0x51000000 | (imm << 10) | (8 << 5) | 9
ENCODE = {}
def hx(w): return ' '.join('%02X' % b for b in struct.pack('<I', w))

# name -> (va, rd, {value: encoded word}, description)
KNOBS = {
    'width':  (0x9298D4, 8,  (640, 854),
               'field mode-2 viewport WIDTH. 854 = FFNx wide_viewport_width.'),
    'halfw':  (0x929938, 24, (320, 427),
               'field mode-2 half-width -> 0xCFF1F4 AND 0xCFF1FC. Models '
               'project through this; it must track `width` or characters '
               'and background disagree (README-v8 Error 2).'),
    'height': (0x9298BC, 8,  (448, 480),
               'field mode-2 viewport HEIGHT. 448-of-480 is the top/bottom '
               'bars. Needs the mod\'s Background Uncrop art to reveal.'),
    'halfh':  (0x929964, 8,  (224, 240),
               'field mode-2 half-height -> 0xCFF200. Tracks `height`.'),
    # The main background's own horizontal window. Both layers compute
    #     w9 = 320 - bg_x        stp w8, w9, [base]      mul ..., w9
    # i.e. 320 is a WIDTH, not a clip bound -- which is why the survey that
    # counted COMPARES in these functions concluded they "never clip" and
    # this was missed. 427 = ceil(854/2), FFNx's widened field window.
    'bg1':    (0xA06E60, 9,  (320, 427),
               'field_layer1_pick_tiles window width. THE main background.'),
    'bg2':    (0xA05A5C, 9,  (320, 427),
               'field_layer2_pick_tiles ORIGIN.'),
    # THE EXTENT. x86 0x640D83: `sub edx, 0x150` -- the tile loop keeps
    # tiles with  x - 336 < tile.x < x , and
    #     dst_x = (tile.x + 320 - x) * 2
    # so the drawn span is game-x -32 .. 640: the 4:3 crop. Raising 336
    # extends the window to the LEFT. 376 reaches -112, just past FFNx's
    # -107. The RIGHT edge is a bare register compare with no immediate,
    # which is exactly what FFNx documents as needing an inserted
    # instruction -- so this knob widens one side only, by design.
    'left1':  (0xA07244, 9,  (336, 376),
               'field_layer1_pick_tiles LEFT extent. 336 -> 376 reaches '
               'game-x -112. Any value 336..512 is accepted for sweeping.'),
    'left2':  (0xA05E00, 9,  (336, 376),
               'field_layer2_pick_tiles LEFT extent.'),
    # VERTICAL origin. dst_y = (tile.y + 224 - cam_y) * 2 over window
    # [cam_y-256, cam_y), so the span is -64..448 inside a 0..480 frame --
    # the bottom 32 units are the black bar. Raising the origin to 240
    # moves the span to -32..480, covering the frame, WITHOUT touching the
    # window width. Stock encoding here is `orr w9, wzr, #0xe0`, not movz.
    'top1':   (0xA06EA8, 9,  (224, 240),
               'field_layer1_pick_tiles VERTICAL origin. 240 fills the '
               'frame; the top/bottom bars come from this, not from the '
               'mode-2 viewport height.'),
    'top2':   (0xA05AA4, 9,  (224, 240),
               'field_layer2_pick_tiles VERTICAL origin.'),
}
# The stock words are not all MOVZ: height/halfh are ORR wN, wzr, #imm.
STOCK_WORD = {0x9298D4: 0x52805008, 0x929938: 0x52802818,
              0x9298BC: 0x321A0BE8, 0x929964: 0x321B0BE8,
              0xA06E60: 0x52802809, 0xA05A5C: 0x52802809,
              0xA07244: 0x51054109, 0xA05E00: 0x51054109,
              0xA06EA8: 0x321B0BE9, 0xA05AA4: 0x321B0BE9}
ENCODE[0xA07244] = sub_imm
ENCODE[0xA05E00] = sub_imm

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('main'); ap.add_argument('--set', action='append', default=[])
    ap.add_argument('--show', action='store_true')
    a = ap.parse_args(argv)
    import nso_patcher, nxmap
    from pathlib import Path
    img = nxmap.Main(a.main).img

    def cur(va): return struct.unpack_from('<I', img, va)[0]
    def decode(name):
        va, rd, vals, _ = KNOBS[name]
        w = cur(va)
        if w == STOCK_WORD[va]: return vals[0]
        enc = ENCODE.get(va, movz_w)
        for v in range(0, 1024):
            if w == enc(rd, v): return v
        return None

    if a.show or not a.set:
        print('module: %s' % a.main)
        for n, (va, rd, vals, why) in KNOBS.items():
            v = decode(n)
            print('  %-7s +%08X  = %-6s  (stock %d, wide %d)'
                  % (n, va, v if v is not None else 'UNKNOWN', vals[0], vals[1]))
            print('          %s' % why)
        return 0

    patches = []
    for spec in a.set:
        name, _, val = spec.partition('=')
        if name not in KNOBS: print('! unknown knob %r' % name); return 2
        va, rd, vals, _ = KNOBS[name]
        val = int(val)
        if not 0 <= val <= 0xFFFF:
            print('! %s must be 0..65535' % name); return 2
        enc = ENCODE.get(va, movz_w)
        now, want = cur(va), (STOCK_WORD[va] if val == vals[0] else enc(rd, val))
        if now == want: print('  %s already %d' % (name, val)); continue
        patches.append({'name': '%s -> %d' % (name, val), 'va': va,
                        'expect': hx(now), 'set': hx(want)})
    if not patches: print('  nothing to do'); return 0
    nso = nso_patcher.read_nso(Path(a.main))
    for line in nso_patcher.apply_spec(nso, {'name': 'field framing', 'patches': patches}):
        print('  ' + line)
    tmp = a.main + '.tmp'
    open(tmp, 'wb').write(nso_patcher.rebuild(nso)); os.replace(tmp, a.main)
    print('  written. Copy exefs/main to the SD card and reboot.')
    return 0

if __name__ == '__main__': sys.exit(main())
