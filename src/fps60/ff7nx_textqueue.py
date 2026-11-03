#!/usr/bin/env python3
"""
ff7nx_textqueue.py -- battle text queue timings at 60 / 30 FPS (BUILD 882).

THE BUG: Sense ("Zemzelett  Level:17" / "HP:285/285  MP:36/36" / "Weak
against Wind.") flashed each line for about 0.33 s instead of about 1.33 s.

`add_text_to_display_queue` (x86 0x434..., ARM .text 0x7CEF..0x7CF224) puts
one line in the 64-entry queue at 0xBF1EB8 as

    { short buffer_idx; short field_2; byte wait_frames; byte n_frames; }

and update_display_text_queue counts both bytes down once per battle tick.
The stock function stores

    wait_frames = wait - 1
    n_frames    = n ? n : get_n_frames_display_action_string()   (inlined)

`battle-text` already scaled the inlined message-speed formula (the n == 0
path), which is why ordinary battle text was right. The explicit n branch and
the wait were never scaled, so every caller that passes its own duration --
Sense passes one per line -- ran at a quarter of the wall time at 60 FPS.

FFNx (voice.cpp ff7_add_text_to_display_queue) stores

    wait_frames = wait * battle_frame_multiplier - 1
    n_frames    = n ? min(n * battle_frame_multiplier, 255) : <message speed>

This reproduces that in place, with no cave. Both branches recompute
`idx * 6` (the slot offset) that the function already holds in guest ECX
(stored at 0x7CF0C4 and not written again before 0x7CF148), so the redundant
recomputations make room for the scale and a clamp:

  block A, 0x7CF070..0x7CF0C4 (the wait):
      wait = min(byte * M - 1, 255)    (unsigned: wait 0 -> 255, as stock)
      edx = wait; eax = ecx = idx*6; slot[+4] = wait
  block B, 0x7CF114..0x7CF144 (the explicit n branch):
      edx = ecx (= idx*6); al = n (as stock); w21 = min(n * M, 255)

The only branches into 0x7CF070..0x7CF1E0 are the function's own (0x7CF110
cbz, 0x7CF148 b), checked against the dump main.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

GROUP = 'battle-text'

TRANSLATE = 0x010FC3A0          # guest address -> host pointer helper
A_BASE = 0x007CF070
B_BASE = 0x007CF114

A_STOCK = (0x39C00008, 0x51000508, 0xB9000A68, 0xB9401668, 0x51001100,
           0x9424B4C7, 0xB9400008, 0x0B080508, 0x531F7908, 0x39402275,
           0x0B140109, 0x11001120, 0xB9000268, 0x9424B4BF, 0x39000015,
           0xB9401668, 0x51001100, 0x9424B4BB, 0xB9400008, 0x0B080508,
           0x531F7908, 0xB9000668)
B_STOCK = (0xB9401668, 0x51001100, 0x9424B4A1, 0xB9400008, 0x0B080508,
           0x531F7908, 0xB9000A68, 0xB9401668, 0x11004100, 0x9424B49A,
           0x39400015, 0xB9400A68, 0x39000275)

NOP = 0xD503201F


def _bl(frm, to):
    return 0x94000000 | (((to - frm) >> 2) & 0x3FFFFFF)


def _lsl_w(rd, rn, s):
    return 0x53000000 | (((32 - s) % 32) << 16) | ((31 - s) << 10) | (rn << 5) | rd


def _block_a(shift):
    w = [
        0x39400008,             # ldrb w8, [x0]          wait (unsigned)
        _lsl_w(8, 8, shift),    # lsl  w8, w8, #shift    * M
        0x51000508,             # sub  w8, w8, #1
        0x7103FD1F,             # cmp  w8, #0xff
        0x52801FE9,             # mov  w9, #0xff
        0x1A899108,             # csel w8, w8, w9, ls    clamp (0 -> 255)
        0xB9000A68,             # str  w8, [x19, #8]     edx = wait
        0xB9401668,             # ldr  w8, [x19, #0x14]  esp
        0x51001100,             # sub  w0, w8, #4
        None,                   # bl   translate         &idx
        0xB9400008,             # ldr  w8, [x0]
        0x0B080508,             # add  w8, w8, w8, lsl #1
        0x531F7908,             # lsl  w8, w8, #1        idx * 6
        0x39402275,             # ldrb w21, [x19, #8]
        0x0B140109,             # add  w9, w8, w20
        0x11001120,             # add  w0, w9, #4        &slot.wait_frames
        0xB9000268,             # str  w8, [x19]         eax = idx*6
        0xB9000668,             # str  w8, [x19, #4]     ecx = idx*6 (was 0x7CF0AC..C4)
        None,                   # bl   translate
        0x39000015,             # strb w21, [x0]
        NOP,
        NOP,
    ]
    for i, v in enumerate(w):
        if v is None:
            w[i] = _bl(A_BASE + 4 * i, TRANSLATE)
    return w


def _block_b(shift):
    w = [
        0xB9400668,             # ldr  w8, [x19, #4]     ecx = idx*6
        0xB9000A68,             # str  w8, [x19, #8]     edx = idx*6 (as stock)
        0xB9401668,             # ldr  w8, [x19, #0x14]
        0x11004100,             # add  w0, w8, #0x10
        None,                   # bl   translate         &n
        0x39400015,             # ldrb w21, [x0]
        0x39000275,             # strb w21, [x19]        al = n (as stock)
        _lsl_w(21, 21, shift),  # lsl  w21, w21, #shift  * M
        0x7103FEBF,             # cmp  w21, #0xff
        0x52801FE8,             # mov  w8, #0xff
        0x1A8892B5,             # csel w21, w21, w8, ls  clamp
        0xB9400A68,             # ldr  w8, [x19, #8]     idx*6 (as stock)
        NOP,
    ]
    for i, v in enumerate(w):
        if v is None:
            w[i] = _bl(B_BASE + 4 * i, TRANSLATE)
    return w


def patches(multiplier):
    """(label, offset, stock, new) rows for battle_frame_multiplier 4 or 2."""
    shift = {4: 2, 2: 1}[multiplier]
    out = []
    for name, base, stock, new in (
            ('wait_frames * %d - 1' % multiplier, A_BASE, A_STOCK,
             _block_a(shift)),
            ('explicit n_frames * %d' % multiplier, B_BASE, B_STOCK,
             _block_b(shift))):
        assert len(stock) == len(new)
        for i, (old, w) in enumerate(zip(stock, new)):
            if old == w:
                continue
            out.append(('add_text_to_display_queue %s (+%d)' % (name, i),
                        base + 4 * i, old, w))
    return out


def x2_table():
    """{offset: (label, word)} for ff7nx_30fps.NSO_X2['battle-text']."""
    return {off: (label, new) for label, off, _old, new in patches(2)}
