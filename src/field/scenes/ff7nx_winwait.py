"""ff7nx_winwait.py -- no empty dialogue boxes where Echo-S reuses a window
another character's line still holds. BUILD 885 (the cargoin case is
ff7nx_cargowait, BUILD 884, confirmed on hardware 11-03).

THE MECHANISM (proven by cargoin). A script starts another entity's line
without waiting for it (REQ / REQSW) and then changes the SAME window's size
or position (WINDOW / WSIZW) for its own next line. Echo-S assumes its Auto
Text Advance closes the first box first. With Auto off the box is still open
and the resize leaves an empty box that has to be dismissed.

A census of all 711 fields (every REQ/REQSW whose target script opens a
window, followed in the caller by WINDOW/WSIZW/WMOVE on that window before any
REQEW on that entity) found 9 such sites new in Echo-S. Read one by one:

  junin1a  taityo s17 / hei2 s16  PROVABLE: the soldier's "Yes, sir!" opens
           window 1 at once (AKAO, ANIME, WINDOW, MESSAGE) and Captain Rex
           reaches WSIZW 1 four non-blocking opcodes later. Fixed: that REQ
           becomes REQEW (0x01 -> 0x03), so Rex waits for the soldier's box.
           hei1 answers in window 0 at the same time and is untouched.
  nivgate  cefiros s3 / SkipScen s1  PROVABLE when the box is still up:
           Sephiroth's "Ha ha ha ha ha." opens window 1 at once and his next
           line resizes window 1 after ~4 s of timed animation, so a box kept
           open longer than that empties. Fixed by swapping two same-length
           opcodes: DFANM 0x0C (the looping laugh) now comes first and the REQ
           becomes REQEW, so he laughs while the box is up and carries on as
           Echo-S wrote it once it closes.
  roadend  safe -- Tifa's line waits on a flag set after the other line.
  md8brdg  safe -- a battle always lies between (3 sites).
  junin1a  taityo s1 / AD2 s1 -- depends on a branch and on the 8-opcode tick
           budget; not provable, left alone.
  jtemplb  Aerith and Cloud shout "The Promised Land!!" together on purpose;
           a wait would break that. Left alone.

SAFETY. Each patch is an exact byte sequence that must occur once in the
field's section 1, replaced by one of the same length; anything else refuses
and leaves the field alone. Idempotent.

SEVENTH_NX_NO_WINWAIT=1 disables.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os

OFF_ENV = 'SEVENTH_NX_NO_WINWAIT'


def _h(s):
    return bytes.fromhex(s.replace(' ', ''))


# field -> [(what, original bytes, patched bytes)]
FIELDS = {
    'junin1a': [(
        'Captain Rex waits for the soldier\'s "Yes, sir!"',
        _h('01 14 d0  ae 09 01  b4 00 00 02 0a 01  bd 00 20 00  ae 03 01'
           '  2f 01 08 00 08 00 8e 00 39 00  40 01 40'),
        _h('03 14 d0  ae 09 01  b4 00 00 02 0a 01  bd 00 20 00  ae 03 01'
           '  2f 01 08 00 08 00 8e 00 39 00  40 01 40'))],
    'nivgate': [(
        'Sephiroth laughs, then waits for his laugh box',
        _h('01 14 01  a2 0c 01  24 3c 00  ba 0d 01  ac  a2 0a 01  24 3c 00'
           '  a3 0e 01  ac  a2 00 01  50 01 58 00 40 00 88 00 29 00'
           '  40 01 05'),
        _h('a2 0c 01  03 14 01  24 3c 00  ba 0d 01  ac  a2 0a 01  24 3c 00'
           '  a3 0e 01  ac  a2 00 01  50 01 58 00 40 00 88 00 29 00'
           '  40 01 05'))],
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
        out.append('  WINDOW WAIT (BUILD 885): a speaker waits for the box '
                   'still holding its window (%s) -- no empty boxes with '
                   'Echo-S Auto Text Advance off. %s=1 disables.'
                   % (', '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! window wait %s: %s' % (name, why))
    return '\n'.join(out)
