"""ff7nx_tunnelgate.py -- tunnel_2: Tifa no longer gets stuck running in place
at the laser gate. BUILD 908.

THE SCENE. Vanilla tunnel_2 has no walking: it is a choice menu. Echo-S makes
it walkable with Barret in the lead. Walking into the beams (line1 / line2)
runs cloud s4, which:

  1. REQ ballet s8 -- Barret walks to (0, 550), waits 15 frames, then SPLITs
     Cloud and Tifa out of himself to their marks. SPLIT holds Barret's
     priority-6 slot until both have arrived.
  2. Straight into Barret's "Whadda we do, Cloud?" and the three-way choice,
     without waiting for step 1.
  3. "Have a good look inside": REQEW tifa s4 (Tifa MOVEs to the duct), then
     REQ ballet s4, which REQs tifa s5 (Tifa runs back, CMOVE) and sets the
     flag Cloud polls before the second choice.
  4. "Don't jump down": ballet s6 REQs tifa s6 (JOIN) and polls a flag that
     tifa s6 sets. "Jump down": ballet s5 JOINs.

THE BUG (field-script race; Echo-S only, vanilla never splits here). Nothing
waits for the SPLIT in step 1, or for Tifa's run back in step 3:

  * Clicking through the first dialogue before the split ends sends Tifa's
    own MOVE (step 3) while SPLIT is still walking her to another mark. Two
    movers, two targets: she runs in place and neither script ends.
  * Same window: REQ ballet s4 is DROPPED, because Barret's priority-6 slot
    is still busy with the SPLIT. The flag never changes and Cloud polls for
    ever.
  * Clicking through the second choice before Tifa's run back (tifa s5) ends:
    ballet s6's REQ tifa s6 is dropped (same slot), so the JOIN never runs and
    Barret polls for ever. Or ballet s5's JOIN fights her CMOVE.

The second visit to the gate is where players hit it, because they skip
dialogue they have already heard. A slow first read lets everything finish.

THE FIX (two bytes, same length):

  cloud s4   REQ ballet s8 -> REQEW (01 -> 03). The dialogue starts once
             Barret has walked and Cloud and Tifa have stepped out (also waits
             out Barret's walk-in from the field intro, ballet s9, if the
             player heads straight for the beams).
  ballet s4  REQ tifa s5 -> REQEW (01 -> 03). Barret's next line waits until
             Tifa is back on her mark, so her slot is free for the JOIN.

Every other REQ in the scene targets an idle slot once these two wait.
An archive-wide census (Claude/_scratch/splitrace/scan.py, built vs vanilla:
async REQ of a SPLIT/JOIN script followed by moving another party member, or
a same-priority REQ that would be dropped) finds no other Echo-S-only case.

SAFETY. Each patch is an exact byte sequence that must occur once in the
field's section 1, replaced by one of the same length; anything else refuses
and leaves the field alone. Idempotent.

SEVENTH_NX_NO_TUNNELGATE=1 disables.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os

OFF_ENV = 'SEVENTH_NX_NO_TUNNELGATE'


def _h(s):
    return bytes.fromhex(s.replace(' ', ''))


# field -> [(what, original bytes, patched bytes)]
FIELDS = {
    'tunnel_2': [
        ('the gate dialogue waits for Barret\'s walk and SPLIT',
         # UC 1, MENU 1, SETBYTE 5/07=1, REQ ballet s8, MSPED, DIR, SOLID, WAIT
         _h('33 01  4a 01  80 50 07 01  01 09 c8  b2 00 80 03  b3 00 a0'
            '  c7 01  24 10 00'),
         _h('33 01  4a 01  80 50 07 01  03 09 c8  b2 00 80 03  b3 00 a0'
            '  c7 01  24 10 00')),
        ('Barret waits for Tifa to run back before his next line',
         # DIRA cloud, WAIT 8, REQ tifa s5, SETBYTE 5/07=2, WAIT 4, RET
         _h('b6 08  24 08 00  01 0a c5  80 50 07 02  24 04 00  00'),
         _h('b6 08  24 08 00  03 0a c5  80 50 07 02  24 04 00  00')),
    ],
}


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def fix_sec1(name, sec1):
    """(section 1, patches applied). Raises if a sequence is not as expected."""
    out = sec1
    n = 0
    for what, old, new in FIELDS[name]:
        if len(old) != len(new):
            raise AssertionError('%s: length changes' % what)
        if out.count(new) == 1 and out.count(old) == 0:
            continue                                    # already applied
        if out.count(old) != 1:
            raise ValueError('%s: the Echo-S sequence occurs %d time(s), '
                             'expected 1' % (what, out.count(old)))
        i = out.find(old)
        out = out[:i] + new + out[i + len(old):]
        n += 1
    return out, n


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    st = {'names': [], 'refused': []}
    if disabled():
        return st
    encode = encode or archive.encode_field
    for name in FIELDS:
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            parts[0], n = fix_sec1(name, parts[0])
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:120]))
            continue
        if n:
            payloads[name] = encode(lgp.join_sections(parts))
            st['names'].append(name)
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  TUNNEL GATE (BUILD 908): tunnel_2 waits for Barret\'s '
                   'SPLIT and Tifa\'s run back -- no Tifa running in place '
                   'at the laser gate. %s=1 disables.' % OFF_ENV)
    for name, why in st.get('refused', ()):
        out.append('  ! tunnel gate %s: %s' % (name, why))
    return '\n'.join(out)
