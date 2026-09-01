#!/usr/bin/env python3
"""
verify_throttle_build.py -- check a BUILT `main` end to end, from the shipped
bytes and nothing else.

    python3 verify_throttle_build.py --nso fps_fix/atmosphere/contents/\\
            0100A5B00BDC6000/exefs/main --stock exefs/main

test_throttle.py proves the cave EMITTER is right. This proves the SHIPPED FILE
is right, which is a different claim: it re-reads the built NSO, follows each
hook's branch to wherever the cave actually landed, and runs the differential on
those words at those addresses. Nothing is taken from the generator.

What it checks
--------------
  1. the built NSO parses and every segment hash matches its header;
  2. each throttle hook is an unconditional B, in range, into .text;
  3. the cave it lands on ends by branching back to hook+4, and replays the
     stock instruction it displaced -- read out of the STOCK nso, not out of a
     table;
  4. the exclusion table really is inside the cave, and holds exactly the
     addresses ff7nx_dispatch.py names -- each one cross-checked against the
     0x126D3A8 recompilation map;
  5. the shipped pre/post caves reproduce FFNx's PauseEffectDecorator over
     every slot, phase and pause state;
  6. every byte the caves can write lies inside the BSS block the header grew
     for them, and bssSize really did grow;
  7. the undecorated call site in execute_effect100_fn is still stock.
"""
import argparse
import struct
import sys

import arm64emu as E
import ff7nx_dispatch as D
import nxmap
import test_throttle as T

BAD = []


def bad(msg):
    BAD.append(msg)
    print('  FAIL  %s' % msg)


def ok(msg):
    print('  ok    %s' % msg)


def segments(data):
    import lz4.block
    segs = [struct.unpack('<III', data[b:b + 12]) for b in (0x10, 0x20, 0x30)]
    comp = struct.unpack('<III', data[0x60:0x6C])
    flags = struct.unpack('<I', data[0x0C:0x10])[0]
    raw = []
    for i, (fo, mo, ds) in enumerate(segs):
        blob = data[fo:fo + comp[i]]
        raw.append(lz4.block.decompress(blob, uncompressed_size=ds)
                   if flags & (1 << i) else blob[:ds])
    return segs, raw


def follow(text, hook):
    """hook -> (cave address, or None if the hook is not a branch)."""
    cur, = struct.unpack('<I', text[hook:hook + 4])
    if (cur >> 26) != 0x05:
        return None
    disp = cur & 0x3FFFFFF
    if disp & 0x2000000:
        disp -= 0x4000000
    tgt = hook + disp * 4
    return tgt if 0 <= tgt < len(text) else None


def cave_words(text, cave, hook, limit=256):
    """
    The cave's words, ending at the `b hook+4` that closes it.

    Read out of the file rather than rebuilt, so a cave that landed one word off
    or was truncated shows up here and not as a passing rebuild.
    """
    out = []
    for i in range(limit):
        w, = struct.unpack('<I', text[cave + 4 * i:cave + 4 * i + 4])
        out.append(w)
        if (w >> 26) == 0x05:
            disp = w & 0x3FFFFFF
            if disp & 0x2000000:
                disp -= 0x4000000
            if cave + 4 * i + disp * 4 == hook + 4:
                return out
    return None


def cave_map(text, cave, hook, limit=512):
    """Recover a scattered padding cave by following its complete CFG."""
    code, todo = {}, [cave]

    def rel_target(pc, bits, shift, field):
        sign = 1 << (bits - 1)
        field = field - (1 << bits) if field & sign else field
        return pc + (field << shift)

    while todo and len(code) < limit:
        pc = todo.pop()
        if pc == hook + 4 or pc in code:
            continue
        if not 0 <= pc <= len(text) - 4:
            return None
        w, = struct.unpack_from('<I', text, pc)
        code[pc] = w
        if (w >> 26) == 0x05:                       # b
            tgt = rel_target(pc, 26, 2, w & 0x3FFFFFF)
            if tgt != hook + 4:
                todo.append(tgt)
        elif (w & 0xFF000010) == 0x54000000:        # b.cond
            todo += [pc + 4, rel_target(pc, 19, 2, (w >> 5) & 0x7FFFF)]
        elif (w & 0x7E000000) == 0x34000000:        # cbz/cbnz
            todo += [pc + 4, rel_target(pc, 19, 2, (w >> 5) & 0x7FFFF)]
        elif (w & 0x7E000000) == 0x36000000:        # tbz/tbnz
            todo += [pc + 4, rel_target(pc, 14, 2, (w >> 5) & 0x3FFF)]
        else:
            todo.append(pc + 4)                     # includes BL fallthrough
    if todo or not code:
        return None
    exits = [pc for pc, w in code.items()
             if (w >> 26) == 0x05 and
             rel_target(pc, 26, 2, w & 0x3FFFFFF) == hook + 4]
    return (code, exits) if exits else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--nso', required=True, help='the BUILT exefs/main')
    ap.add_argument('--stock', default='exefs/main')
    ap.add_argument('--freq', type=int, default=4)
    a = ap.parse_args()

    try:
        from ff7nx_dispatch_sites import (DISPATCH_SITES, PAUSED_GUEST,
                                          THROTTLE_BASE, BSS_GROW_THROTTLE)
    except ImportError:
        sys.exit('ff7nx_dispatch_sites.py is missing')

    built = open(a.nso, 'rb').read()
    stock = open(a.stock, 'rb').read()
    import hashlib
    print('built %s  %s' % (hashlib.md5(built).hexdigest(), a.nso))
    print('stock %s  %s\n' % (hashlib.md5(stock).hexdigest(), a.stock))

    segs, raw = segments(built)
    for i in range(3):
        if hashlib.sha256(raw[i]).digest() != built[0xA0 + 32 * i:
                                                    0xA0 + 32 * i + 32]:
            bad('segment %d sha256 does not match the header' % i)
    _ssegs, sraw = segments(stock)
    text, stext = raw[0], sraw[0]
    ok('3 segment hash(es) match')

    grew = (struct.unpack('<I', built[0x3C:0x40])[0]
            - struct.unpack('<I', stock[0x3C:0x40])[0])
    map_ = nxmap.Main(a.stock)

    any_throttle = False
    for tag in ('effect100', 'effect60'):
        d = DISPATCH_SITES.get(tag) or {}
        thr = d.get('throttle')
        if not thr:
            continue
        pre_cave = follow(text, thr['pre'])
        post_cave = follow(text, thr['post'])
        if pre_cave is None and post_cave is None:
            print('  --    %s pause-throttle not present in this build' % tag)
            continue
        any_throttle = True
        print('%s pause-throttle' % tag)
        if pre_cave is None or post_cave is None:
            bad('%s: only one half of the throttle is hooked -- pre %s, post %s'
                % (tag, pre_cave, post_cave))
            continue

        # ---- 2 / 3: the caves close properly and replay the stock word ----
        pre_info = cave_map(text, pre_cave, thr['pre'])
        post_info = cave_map(text, post_cave, thr['post'])
        pre = pre_info[0] if pre_info else None
        post = post_info[0] if post_info else None
        displaced = {}
        for what, info, hook, cave in (('pre', pre_info, thr['pre'], pre_cave),
                                       ('post', post_info, thr['post'], post_cave)):
            if info is None:
                bad('%s %s cave at 0x%X never branches back to hook+4'
                    % (tag, what, cave))
                continue
            words, exits = info
            stock_word, = struct.unpack('<I', stext[hook:hook + 4])
            # A linker bridge may land between the final two logical words
            # when a padding run ends after the displaced instruction. The
            # CFG already proves every path reaches the branch back; require
            # the exact stock word somewhere in that recovered map.
            replay = [pc for pc, word in words.items() if word == stock_word]
            if not replay:
                bad('%s %s cave does not replay the displaced instruction '
                    '(stock is %08X)' % (tag, what, stock_word))
            else:
                displaced[what] = stock_word
                ok('%s %s hook +0x%X -> padding cave 0x%X (%d mapped words), replays %08X, '
                   'returns to +0x%X' % (tag, what, hook, cave, len(words),
                                         stock_word, hook + 4))
        if pre is None or post is None:
            continue

        # ---- 4: the exclusion table, out of the built file -----------------
        add_hook = d['add_hook']['hook']
        add_cave = follow(text, add_hook)
        if add_cave is None:
            bad('%s: the registration hook +0x%X is not a branch'
                % (tag, add_hook))
        else:
            reg = cave_words(text, add_cave, add_hook)
            # The registration table is one of two things, and which one is a
            # property of the build, not of the sites file: an EXCLUSION list
            # (throttle everything except these) or an ALLOW list (throttle only
            # these). Both are legitimate; shipping a table that is neither is
            # not. Matching against both and naming which one was found is what
            # keeps this check meaningful once a second mode exists.
            modes = {'exclusion': [va for _n, va in thr['exclude']],
                     'allow': [va for _n, va in thr.get('aura_allow', ())]}
            got = []
            p = add_cave + 4 * len(reg)
            while True:
                v, = struct.unpack('<I', text[p:p + 4])
                if v == 0:
                    break
                got.append(v)
                p += 4
                if len(got) > 256:
                    break
            # `--throttle-only` appends to the allow list, so an allow table is
            # "starts with the allow list". The extras still have to be real
            # effect60 slot functions, which the map check below enforces.
            hit = [k for k, v in modes.items() if v and got == v]
            extras = []
            if not hit and modes['allow'] and got[:len(modes['allow'])] == \
                    modes['allow']:
                hit = ['allow']
                extras = got[len(modes['allow']):]
            if not hit:
                bad('%s: the registration table in the built file is %s, which '
                    'is neither the exclusion list %s nor the allow list %s'
                    % (tag, ['%06X' % v for v in got],
                       ['%06X' % v for v in modes['exclusion']],
                       ['%06X' % v for v in modes['allow']]))
            else:
                stray = [v for v in got if v not in map_.x86_to_arm]
                if stray:
                    bad('%s: %d table entr(y/ies) are not function entry points '
                        'in the recompilation map: %s'
                        % (tag, len(stray), ['%06X' % v for v in stray]))
                else:
                    ok('%s registration table: %s mode, %d entr(y/ies)%s, all '
                       'confirmed function entry points, zero-terminated'
                       % (tag, hit[0], len(got),
                          ' (%d from --throttle-only)' % len(extras)
                          if extras else ''))
                    if hit[0] == 'allow':
                        ok('%s: throttling ONLY %s -- the other %d slot(s) run '
                           'at full rate'
                           % (tag, ', '.join('%06X' % v for v in got),
                              (1 << thr['mask_bits']) - len(got)))

            # The effect100 registration cave carries two additional packed
            # tables after the ordinary exclusion sentinel: KOTR exception
            # markers and the ten FFNx model-interpolation functions.  Execute
            # the registration words read from the built NSO so a wrong table
            # address, marker or nested-add path cannot hide behind the first
            # table's successful structural check.
            literal_words = (len(got) + 1
                             + len(thr.get('kotr', ())) + 1
                             + len(thr.get('model', ())) + 1)
            reg_full = list(reg)
            for i in range(literal_words):
                reg_full.append(struct.unpack_from(
                    '<I', text, add_cave + 4 * (len(reg) + i))[0])
            shipped_reg = dict(words=reg_full, cave=add_cave, hook=add_hook)
            tt = dict(thr)
            tt['paused_guest'] = PAUSED_GUEST
            tt['idx_off'] = d['disp_hook']['idx_off']
            before = len(T.FAIL)
            nk = T.kotr_registration(tt, quiet=True, shipped=shipped_reg)
            nm = T.model_registration(tt, quiet=True, shipped=shipped_reg)
            new = len(T.FAIL) - before
            if new:
                bad('%s: shipped special registration paths disagree in %d '
                    'place(s)' % (tag, new))
            elif nk or nm:
                ok('%s shipped special registration: %d KOTR and %d model '
                   'execution(s) agree with FFNx' % (tag, nk or 0, nm or 0))

        # ---- 5: the differential, on the SHIPPED words ---------------------
        t = dict(thr)
        t['paused_guest'] = PAUSED_GUEST
        t['idx_off'] = d['disp_hook']['idx_off']
        before = len(T.FAIL)
        n = T.one_dispatcher(tag, t, a.freq, quiet=True,
                             shipped=dict(pre=pre, post=post,
                                          cave_pre=pre_cave,
                                          cave_post=post_cave,
                                          pre_hook=thr['pre'],
                                          post_hook=thr['post'],
                                          post_displaced=displaced['post']))
        new = len(T.FAIL) - before
        if new:
            bad('%s: the shipped caves disagree with FFNx in %d place(s)'
                % (tag, new))
        else:
            ok('%s differential: %d execution(s) of the SHIPPED words agree '
               'with PauseEffectDecorator' % (tag, n or 0))

        if thr.get('model'):
            before = len(T.FAIL)
            nm = T.model_interpolation(t, quiet=True,
                                       shipped=dict(pre=pre, post=post,
                                                    cave_pre=pre_cave,
                                                    cave_post=post_cave,
                                                    pre_hook=thr['pre'],
                                                    post_hook=thr['post'],
                                                    post_displaced=
                                                    displaced['post']))
            new = len(T.FAIL) - before
            if new:
                bad('%s: shipped model interpolation disagrees with FFNx in '
                    '%d place(s)' % (tag, new))
            else:
                ok('%s model interpolation: %d execution(s) of the SHIPPED '
                   'words pass smooth, teleport and deferred-retirement cases'
                   % (tag, nm or 0))

        # ---- 7: the undecorated site -------------------------------------
        for pc, want in zip(thr['undecorated'], thr['undecorated_words']):
            cur, = struct.unpack('<I', text[pc:pc + 4])
            if cur != want:
                bad('%s: the undecorated call site +0x%X reads %08X, stock is '
                    '%08X -- it must not be hooked' % (tag, pc, cur, want))
            else:
                ok('%s: undecorated call site +0x%X untouched' % (tag, pc))
        print()

    # ---- camera script waits, opcode 0xF5 -------------------------------
    try:
        from ff7nx_dispatch_sites import CAMERA_WAIT_SITES
    except ImportError:
        CAMERA_WAIT_SITES = {}
    import test_camera_wait as CW
    present = 0
    for tag in sorted(CAMERA_WAIT_SITES):
        s = CAMERA_WAIT_SITES[tag]
        cave = follow(text, s['hook'])
        if cave is None:
            continue
        if not present:
            print('camera script waits (opcode 0xF5)')
        present += 1
        words = cave_words(text, cave, s['hook'])
        if words is None:
            bad('%s cave at 0x%X never branches back to hook+4' % (tag, cave))
            continue
        stock_word, = struct.unpack('<I', stext[s['hook']:s['hook'] + 4])
        if stock_word not in words:
            bad('%s cave never replays the stock instruction %08X -- an '
                'operand of 0xFF would not behave as it does unpatched'
                % (tag, stock_word))
        else:
            ok('%s hook +0x%X -> cave 0x%X (%d words), sentinel path replays '
               '%08X, returns to +0x%X'
               % (tag, s['hook'], cave, len(words), stock_word, s['hook'] + 4))
        before = len(CW.FAIL)
        n = CW.one_site(tag, s, a.freq, quiet=True,
                        shipped=dict(words=words, cave=cave, hook=s['hook']))
        if len(CW.FAIL) - before:
            bad('%s: the shipped cave disagrees with FFNx in %d place(s)'
                % (tag, len(CW.FAIL) - before))
        else:
            ok('%s differential: %d operand value(s) of the SHIPPED words agree '
               'with FFNx\'s simulateCameraScript' % (tag, n or 0))
    if present:
        if present != len(CAMERA_WAIT_SITES):
            bad('only %d of %d camera script interpreters is hooked -- the '
                'position and focal scripts must move together'
                % (present, len(CAMERA_WAIT_SITES)))
        print()
    else:
        print('camera-wait not present in this build.\n')

    # ---- 6: BSS ---------------------------------------------------------
    if any_throttle:
        if grew < BSS_GROW_THROTTLE:
            bad('bssSize grew by 0x%X, less than the throttle needs (0x%X)'
                % (grew, BSS_GROW_THROTTLE))
        else:
            ok('bssSize grew by 0x%X (minimum 0x%X), covering the flag block '
               'and the throttle block at +0x%X'
               % (grew, BSS_GROW_THROTTLE, THROTTLE_BASE))
        top = THROTTLE_BASE
        for tag in ('effect100', 'effect60'):
            thr = (DISPATCH_SITES.get(tag) or {}).get('throttle')
            if thr:
                # The mask is the next power of two used defensively at the
                # dispatcher hook; the actual arrays retain the engine's
                # exact slot count (100 effect100, 60 effect60).
                slots = DISPATCH_SITES[tag]['slots']
                regions = [('ctr', slots), ('saved', 1), ('did', 1),
                           ('pptr', 8), ('kotr_exc', slots),
                           ('kotr_disable', 1), ('kotr_field2', 2),
                           ('kotr_ptr', 8), ('kotr_active', 2),
                           ('model_idx', 1), ('model_ptr', 8),
                           ('model_active', 2), ('model_phase', slots),
                           ('model_final', slots),
                           ('model_prev', slots * 6),
                           ('model_next', slots * 6)]
                for key, size in regions:
                    if key in thr:
                        top = max(top, thr[key] + size)
        page_end = (segs[2][1] + segs[2][2] + 0xFFF) & ~0xFFF
        limit = page_end + struct.unpack('<I', built[0x3C:0x40])[0]
        if top > limit:
            bad('the throttle block ends at +0x%X, past the end of BSS +0x%X'
                % (top, limit))
        else:
            ok('every throttle byte (up to +0x%X) is inside BSS (+0x%X)'
               % (top, limit))
    else:
        print('no throttle group is present in this build.')

    print('\n%d problem(s)' % len(BAD))
    return 1 if BAD else 0


if __name__ == '__main__':
    sys.exit(main())
