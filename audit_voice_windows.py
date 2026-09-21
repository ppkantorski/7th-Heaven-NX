#!/usr/bin/env python3
"""
audit_voice_windows.py -- every field where the voice runtime has to make a
choice, listed, so they stop being discovered one scene at a time.

WHY THIS EXISTS
===============
Three bugs in a row were found by playing: Wedge's md8_1 line, Tifa's
fship_25 line, and the three grunts in chrin_1b. Each was a general defect in
the runtime, but each was found because one scene sounded wrong -- and after
the third it stopped being reasonable to keep waiting for the fourth.

Every pattern below is one the runtime resolves with a RULE, not with
per-field knowledge. The point of the report is not to fix fields; it is to
say how much behaviour each rule is responsible for, and to give a list to
listen to after a change.

WHAT IT REPORTS
===============
  burst          several MESSAGEs on DIFFERENT windows with no chance for the
                 service to run between them -- the whole set is published in
                 one script tick and one of them gets the slot.
                 `first_in_burst_wins` decides which.

  mixed burst    a burst where SOME lines are voiced and some are not. This is
                 the chrin_1b shape: the unvoiced one used to take the slot and
                 the scene fell silent.

  reopen         a window used for the SAME dialog more than once, so the
                 record goes `<dialog,7>` then `<dialog,0>` and the line can be
                 requested twice. This is what made the grunts repeat.

  non-blocking   MESSAGE followed by WAIT/WCLS rather than parked on the
                 opcode, so the handler runs ONCE. `publish_on_dialog_change`
                 is what makes those audible at all.

Run it with no arguments. It reads the Echo-S cache and the vanilla archive;
it writes nothing and it is not part of a build.
"""
import collections
import glob
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import echo_s_flevel as EF                                     # noqa: E402
import lgp                                                     # noqa: E402

ECHO = os.path.join('cache', 'Echo-s', 'Echo-S', 'flevel.lgp')
VOICE = os.path.join('cache', 'Echo-s', 'VO', 'Voice')
CACHE = os.path.join('cache', '_voice_audit.json')

MESSAGE, ASK, WAIT, WCLS = 0x40, 0x48, 0x24, 0x51


def voiced_dialogs(field):
    """Dialog ids this field ships a clip for."""
    directory = os.path.join(VOICE, field)
    if not os.path.isdir(directory):
        return None
    out = set()
    for name in os.listdir(directory):
        stem = os.path.splitext(name)[0]
        if stem.startswith('w') and '_' in stem:
            stem = stem[1:].split('_', 1)[1]
        stem = stem.split('_')[0]
        digits = ''.join(c for c in stem if c.isdigit())
        if digits:
            out.add(int(digits))
    return out


def message_sites(section1):
    """[(offset, window, dialog, blocking)] in script order."""
    actors = section1[2]
    akao = struct.unpack_from('<H', section1, 6)[0]
    strings = struct.unpack_from('<H', section1, 4)[0]
    cursor = (EF._SECTION1_FIXED_HEADER + actors * 8 + akao * 4
              + actors * EF._SECTION1_ROUTINE_TABLE_BYTES)
    flat = []
    while cursor < strings:
        try:
            size = EF.instruction_size(section1, cursor)
        except (IndexError, ValueError):
            break
        if size <= 0:
            break
        flat.append((cursor, section1[cursor], size))
        cursor += size
    out = []
    for index, (offset, opcode, size) in enumerate(flat):
        if opcode == MESSAGE and size == 3:
            window, dialog = section1[offset + 1], section1[offset + 2]
        elif opcode == ASK:
            window, dialog = section1[offset + 2], section1[offset + 3]
        else:
            continue
        # Non-blocking idiom: the script does not park on the opcode, it waits
        # or closes the window itself within the next few instructions.
        following = [flat[k][1] for k in range(index + 1, min(index + 4,
                                                              len(flat)))]
        out.append((offset, window, dialog,
                    not (WAIT in following or WCLS in following)))
    return out


def audit_field(field, section1, voiced):
    sites = message_sites(section1)
    seen = collections.Counter((w, d) for _o, w, d, _b in sites)
    report = {
        'sites': len(sites),
        'bursts': [],
        'mixed_bursts': [],
        'reopen': sorted('w%d/d%d' % k for k, n in seen.items() if n > 1),
        'non_blocking': sum(1 for _o, _w, _d, b in sites if b),
    }
    # A burst: consecutive sites on DIFFERENT windows. Consecutive in script
    # order is the best static proxy for "one tick" -- each is its own entity
    # routine ending in RET, so nothing yields between them.
    run = [sites[0]] if sites else []
    for site in sites[1:]:
        if site[1] != run[-1][1] and site[0] - run[-1][0] < 200:
            run.append(site)
            continue
        if len(run) > 1:
            _record(report, run, voiced)
        run = [site]
    if len(run) > 1:
        _record(report, run, voiced)
    return report


def _record(report, run, voiced):
    label = ' '.join('w%d/d%d' % (w, d) for _o, w, d, _b in run)
    report['bursts'].append(label)
    if voiced is None:
        return
    have = [d in voiced for _o, _w, d, _b in run]
    if any(have) and not all(have):
        report['mixed_bursts'].append(
            label + '  voiced=' + ','.join('%d' % d for _o, _w, d, _b in run
                                           if d in voiced))


def main():
    archive = lgp.Archive(os.path.join('dump', 'romfs', 'ff7', 'workingdir',
                                       'data', 'field', 'flevel.lgp'))
    done = {}
    if os.path.exists(CACHE):
        with open(CACHE) as handle:
            done = json.load(handle)
    files = [p for p in sorted(glob.glob(os.path.join(ECHO, '*')))
             if os.path.isfile(p)]
    for path in files:
        field = os.path.basename(path)
        if field in done or field.lower() not in archive.index:
            continue
        try:
            section1 = lgp.split_sections(
                lgp.lzs_decompress(open(path, 'rb').read()[4:]))[0]
        except Exception:                                      # noqa: BLE001
            continue
        done[field] = audit_field(field, section1, voiced_dialogs(field))
        with open(CACHE, 'w') as handle:
            json.dump(done, handle)
    summarise(done)


def summarise(done):
    voiced_fields = [f for f in done if voiced_dialogs(f)]
    bursts = {f: r for f, r in done.items() if r['bursts']}
    mixed = {f: r for f, r in done.items() if r['mixed_bursts']}
    reopen = {f: r for f, r in done.items() if r['reopen']}
    nonblock = {f: r for f, r in done.items() if r['non_blocking']}
    print('%d field(s) audited, %d of them voiced' % (len(done),
                                                      len(voiced_fields)))
    print()
    print('%-42s %s' % ('same-tick bursts (>1 window, no yield)',
                        '%d field(s)' % len(bursts)))
    print('%-42s %s' % ('  ...with a VOICED/UNVOICED mix',
                        '%d field(s)  <-- the chrin_1b shape' % len(mixed)))
    print('%-42s %s' % ('a window reused for the SAME dialog',
                        '%d field(s)  <-- the repeat shape' % len(reopen)))
    print('%-42s %s' % ('non-blocking MESSAGE sites',
                        '%d field(s)' % len(nonblock)))
    print()
    print('mixed bursts, worst first -- listen to these:')
    for field, r in sorted(mixed.items(),
                           key=lambda kv: -len(kv[1]['mixed_bursts']))[:20]:
        print('  %-12s %s' % (field, '; '.join(r['mixed_bursts'][:2])))


if __name__ == '__main__':
    main()
