#!/usr/bin/env python3
"""ff7nx_fxcomplete.py -- the archive-wide pass for additive FX pages that
are still 256px paletted, using only rules already proven on hardware.

BUILD 618t. After mrkt2 (pulse composite) looked "much better" on hardware
the question was how many fields still have the same problem. The
archive-wide audit (_scratch/c6/fxaudit.py + fxclass.py, every field,
vanilla vs build, per FX page) found 341 FX pages in 219 fields still
paletted. Classified by WHY they are paletted:

  pulse <= 2.7x   88 pages  the script pulses a palette's brightness, Cosmos
                            ships the pulse as hash frames, the frames'
                            brightness range is at most 2.7x (mrkt2 2.2,
                            mtnvl3 2.6/1.8) -> median frame, like mrkt2 and
                            crater_2.                              CONVERT
  multi-sheet     14 pages  each palette loads its own single sheet (incl.
                            palettes the script sets ONCE, e.g. icedun_2's
                            ADPAL at load: Cosmos captured that one state as
                            a single hash sheet)  -> per-cell composite,
                            like hyou5_2 / junbin5 18-19.          CONVERT
  single           6 pages  one shared sheet; only the old budget held them
                            back (fxpages reserves pages for later passes)
                                                                   CONVERT
  pulse > 2.7x   158 pages  real fades / flashes (ancnt1 30x, whitein 40x,
                            uutai1 7.9x): a truecolor page would freeze
                            them.                                  KEEP
  scripted, no Cosmos frames 12 pages (ghotel's fog pulse, BUILD 579)  KEEP
  non-additive blend  30 pages  the depth-2 FX band draws additively
                            (FINDINGS-194); average/subtract would change.
                                                                   KEEP
  no Cosmos art, base pages, palette cycles, Cosmos-animated     KEEP

RULES (per page; any doubt leaves the page byte-identical):
  * FX-only (not a base page), every record blend mode 1;
  * every palette on the page has a Cosmos sheet: a static palette its
    selected sheet; a script-written palette needs hash frames -- one frame
    = set once (static), >= 2 frames = a pulse whose brightness range over
    that palette's own cells is <= MAX_PULSE (median frame used);
  * a palette with hash frames the script does NOT write is Cosmos's own
    animation -> page kept;
  * each cell is sampled by one palette (checked);
  * pages are converted largest first while the field stays inside the
    field memory cap (MB_CAP, or MB_OVERRIDE) and the raw cap.
Fields with their own measured pass (ff7nx_fxpcstatic FIELDS, fxpages
DEFER_FIELDS) and EXCLUDE are skipped.

SEVENTH_NX_NO_FX_COMPLETE=1 disables; SEVENTH_NX_FX_COMPLETE_ONLY=a,b
limits it to the named fields.
"""
from __future__ import annotations

import collections
import os
import struct

import diag_common as DC
import field_bg_native as FN
import field_bg_repack as FR

OFF_ENV = 'SEVENTH_NX_NO_FX_COMPLETE'
ONLY_ENV = 'SEVENTH_NX_FX_COMPLETE_ONLY'
MAX_PULSE = 2.7
# BUILD 618u. eals_1's waterfall (hardware 10-03: "100% broken") is a palette
# CYCLE that Cosmos captured as 152 hash frames of nearly equal brightness,
# so the brightness test alone took it for a pulse and froze it. A pulse is a
# handful of frames that are scaled copies of each other (crater_2: 12-15
# frames, structure residual 0.02; mrkt2: 8 frames, 0.21); a cycle is many
# frames whose picture moves.
MAX_FRAMES = 64
MAX_RESIDUAL = 0.1
MIN_CELLS = 4          # a palette on fewer cells cannot veto a page
FX_LO, FX_HI = 0x0F, 0x18
EXCLUDE = frozenset(('uutai1', 'ujunon1', 'ujunon4', 'ujunon5'))
# Every field the user has confirmed on hardware is left exactly as it was
# confirmed (FIELD-TRACKER section 3). kuro_1's purple sky, for one, is a
# scripted palette whose Cosmos frame is far darker than the stored colours.
CONFIRMED = frozenset((
    'crater_1', 'crater_2', 'trnad_1', 'zcoal_1', 'zcoal_2', 'zcoal_3', 'sea',
    'sky', 'trnad_2', 'trnad_51', 'md8_3', 'mogu_1', 'mtcrl_6', 'las4_1',
    'smkin_3', 'desert1', 'desert2', 'jail3', 'kuro_1', 'las2_1', 'las2_3',
    'colne_6', 'ancnt2', 'cosmo2', 'bugin3', 'md_e1', 'woa_3', 'hyou3',
    'hyou5_2', 'fship_2', 'fship_22', 'fship_23', 'fship_24', 'fship_25',
    'qb', 'junair', 'las0_3', 'junonl2', 'ithill', 'junonr2', 'del3',
    'mrkt2', 'junbin5'))
# woa_1/woa_2: the path glow is a scripted pulse whose Cosmos median frame is
# a fraction of what the build shows today; left until seen on hardware.
HOLD = frozenset(('woa_1', 'woa_2', 'mtcrl_8', 'blin70_1'))  # + 618t render review: a hard-edged box (mtcrl_8), a dark square by a lamp (blin70_1)
OP_RTPAL, OP_RTPAL2 = 0xE8, 0xEE
# BUILD 618t. FX pages Cosmos ships as a fully transparent sheet: FFNx
# draws nothing there. icedun_2's page 24 (110 average-blend records) is
# such a page and its 1997 art drew dark squares and lines over Cosmos's
# cave (hardware 10-03). Its records are removed. Measured archive-wide,
# 8 pages in 6 fields are blank in Cosmos; only the one with a hardware
# report is listed (colne_6 and hyou2 are hardware-accepted as they are).
BLANK = {'icedun_2': (24,)}
MB_OVERRIDE = {}


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def only():
    raw = os.environ.get(ONLY_ENV, '').strip()
    return frozenset(x.strip().lower() for x in raw.split(',') if x.strip())


def _borrowed_scale(art, name, q, slot, cells_by_slot, px):
    """median/`_00` brightness of palette q's pulse on another page, or
    None when no other page measures it within MAX_PULSE."""
    import numpy as np
    import ff7nx_fxmargin as FXM
    import ff7nx_fxpages as FP
    import ff7nx_fxpcstatic as PS
    provider = art.provider
    for other, (page, cells_of) in cells_by_slot.items():
        if other == slot or q not in cells_of:
            continue
        recs = getattr(provider, 'state_slots', {}).get(
            (name.lower(), other, q)) or ()
        cells = cells_of[q]
        if len(recs) < 2 or len(cells) < MIN_CELLS:
            continue
        cs = px // (8 if page.size_flag else 16)

        def lum(img):
            sub = np.concatenate([img[cv * cs:(cv + 1) * cs,
                                      cu * cs:(cu + 1) * cs].reshape(-1, 4)
                                  for cu, cv in cells])
            return float((sub[:, :3] * (sub[:, 3:] / 255.0)).mean())
        lv = sorted(lum(PS._rec_rgba(provider, r, px)) for r in recs)
        if lv[-1] > MAX_PULSE * max(lv[0], 1e-3):
            return None
        sel = FP._selected_palette(provider, name, other, q)
        base = FXM._provider_rgba(art, name, other, sel, px) if sel is not \
            None else None
        if base is None:
            return None
        b = lum(base)
        return min(1.0, lv[len(lv) // 2] / b) if b > 0.5 else None
    return None


def set_once(script):
    """BUILD 618u. Palettes whose every LDPAL/LDPLS lies OUTSIDE any
    backward-jump loop: written once when the field starts (icedun_2:
    STPLS 11/12 -> ADPAL -> LDPAL, then a BGON loop that never reloads).
    With no Cosmos frames installed for them, FFNx draws the page's shared
    `_00` sheet for these palettes, so a truecolor page shows exactly what
    PC shows and loses no animation."""
    import echo_s_flevel as ES
    loads = collections.defaultdict(list)
    loops = []
    try:
        for a, b in ES._routine_blocks(script):
            stream, _ = ES._decode_block(script, a, b)
            for off, op, size in stream:
                if op in (0xE6, 0xEC) and size >= 4:
                    if script[off + 1] & 0x0F:
                        return frozenset()
                    loads[script[off + 3]].append(off)
                elif op == 0x12 and size >= 2:
                    loops.append((off - script[off + 1], off))
                elif op == 0x13 and size >= 3:
                    loops.append((off - struct.unpack_from('<H', script,
                                                           off + 1)[0], off))
    except Exception:                                          # noqa: BLE001
        return frozenset()
    return frozenset(q for q, offs in loads.items()
                     if not any(lo <= o < hi for o in offs
                                for lo, hi in loops))


def rotates(script):
    """True when the field rotates a palette (RTPAL/RTPAL2): a palette
    cycle, which no single Cosmos frame can stand for."""
    import echo_s_flevel as ES
    try:
        for a, b in ES._routine_blocks(script):
            stream, _ = ES._decode_block(script, a, b)
            if any(op in (OP_RTPAL, OP_RTPAL2) for _o, op, _s in stream):
                return True
    except Exception:                                          # noqa: BLE001
        return True
    return False


def _page_plan(art, name, sec9, slot, page, px, refs, scripted,
               cells_by_slot=None, rot=False, once=frozenset()):
    """(RGBA page, info) or raises with the reason."""
    import numpy as np
    import ff7nx_fxmargin as FXM
    import ff7nx_fxpages as FP
    import ff7nx_fxpcstatic as PS
    provider = art.provider
    owner = PS._owners(sec9, slot, page, refs)
    cs = px // (8 if page.size_flag else 16)
    cells_of = collections.defaultdict(list)
    for cell, q in owner.items():
        cells_of[q].append(cell)
    out = np.zeros((px, px, 4), np.uint8)
    info = {'pulse': {}, 'sheets': 0}

    def own(img, cells):
        return np.concatenate([img[cv * cs:(cv + 1) * cs,
                                   cu * cs:(cu + 1) * cs].reshape(-1, 4)
                               for cu, cv in cells])

    def paste(img, cells):
        for cu, cv in cells:
            sl = (slice(cv * cs, (cv + 1) * cs), slice(cu * cs, (cu + 1) * cs))
            out[sl] = img[sl]

    cells_by_slot = cells_by_slot or {}
    for q, cells in sorted(cells_of.items()):
        recs = getattr(provider, 'state_slots', {}).get(
            (name.lower(), slot, q)) or ()
        if q in scripted and rot:
            raise ValueError('field rotates palettes (cycle)')
        if len(recs) >= 2:
            if q not in scripted:
                raise ValueError('palette %d is animated by Cosmos' % q)
            levels = []
            for rec in recs:
                img = PS._rec_rgba(provider, rec, px)
                if img is None or img.shape != (px, px, 4):
                    raise ValueError('palette %d frame unreadable' % q)
                sub = own(img, cells)
                a = sub[:, 3:].astype(np.float32) / 255.0
                levels.append((float((sub[:, :3] * a).mean()), rec[1], img))
            if len(recs) > MAX_FRAMES:
                raise ValueError('palette %d has %d Cosmos frames (a cycle)'
                                 % (q, len(recs)))
            stack = np.stack([own(t[2], cells) for t in levels]).astype(np.float32)
            lum = (stack[..., :3] * (stack[..., 3:] / 255.0)).mean(-1)
            med = np.median(lum, 0)
            mm = float((med * med).sum())
            res = max(float(np.abs(f - (f * med).sum() / max(mm, 1e-6) * med)
                            .sum() / max(np.abs(f).sum(), 1e-6)) for f in lum)
            info.setdefault('resid', {})[q] = (round(res, 3), len(recs))
            if res > MAX_RESIDUAL:
                raise ValueError('palette %d frames move (residual %.2f)'
                                 % (q, res))
            levels.sort(key=lambda t: (t[0], t[1]))
            lo, hi = levels[0][0], levels[-1][0]
            if (hi >= 1.0 and hi > MAX_PULSE * max(lo, 1e-3)
                    and len(cells) < MIN_CELLS):
                # a few cells only: kept if the same palette is measured
                # within MAX_PULSE on a page converted with it (checked in
                # plan_field), else the page is refused there
                info.setdefault('small_fade', {})[q] = round(
                    hi / max(lo, 1e-3), 2)
            elif hi >= 1.0 and hi > MAX_PULSE * max(lo, 1e-3):
                raise ValueError('palette %d pulses %.1fx' % (q, hi / max(
                    lo, 1e-3)))
            info['pulse'][q] = round(hi / max(lo, 1e-3), 2)
            paste(levels[len(levels) // 2][2], cells)
            continue
        scale = 1.0
        if q in scripted and not recs and q in once:
            pass                     # set once at load: FFNx shows `_00`
        elif q in scripted and not recs:
            # BUILD 618t (mtnvl3): Cosmos's widescreen strips sit on a page
            # where the pulsed palette has no frames. If the same palette's
            # pulse is measured on another page of the field (<= MAX_PULSE),
            # the strip cells take the `_00` art scaled to that pulse's
            # median, so strips and centre meet at the same brightness.
            ev = _borrowed_scale(art, name, q, slot, cells_by_slot, px)
            if ev is None:
                raise ValueError('palette %d is script-written, no Cosmos '
                                 'frames' % q)
            scale = ev
            info['borrowed'] = info.get('borrowed', 0) + 1
        sel = FP._selected_palette(provider, name, slot, q)
        if sel is None:
            raise ValueError('palette %d has no Cosmos sheet' % q)
        img = FXM._provider_rgba(art, name, slot, sel, px)
        if img is None or img.shape != (px, px, 4):
            raise ValueError('palette %d sheet unreadable' % q)
        if scale != 1.0:
            img = img.copy()
            img[..., :3] = np.clip(img[..., :3].astype(np.float32) * scale,
                                   0, 255).astype(np.uint8)
        paste(img, cells)
        info['sheets'] += 1
    if not np.any(out[..., 3] >= 8):
        raise ValueError('composite is empty')
    return out, info


def drop_blank(sec9, slots):
    """(new section 9, records removed): FX records whose effect page is in
    `slots` are deleted from their layer (counts rewritten)."""
    import ff7nx_parallaxfill as PF
    survey = DC.survey(sec9)
    rows = PF._layers(sec9, survey['back_start'], survey['tex_start'])
    buf = bytearray(sec9)
    gone = 0
    for _layer, count_at, first, n in sorted(rows, key=lambda r: -r[2]):
        keep = []
        for i in range(n):
            o = first + i * 52
            if sec9[o + 28] and sec9[o + 34] in slots:
                gone += 1
                continue
            keep.append(sec9[o:o + 52])
        if len(keep) == n:
            continue
        buf[first:first + n * 52] = b''.join(keep)
        struct.pack_into('<H', buf, count_at, len(keep))
    return bytes(buf), gone


def plan_field(name, sec9, art, scripted, mb_cap, other_bytes=0,
               raw_cap=None, rot=False, sec3=None, once=frozenset()):
    """(new section 9, converted {slot: info}, refused {slot: why})."""
    import ff7nx_fxpages as FP
    pages_l, ts, _te, px = DC.parse_pages(sec9)
    pm = {p.slot: p for p in pages_l if p is not None}
    refs = collections.defaultdict(list)
    blend = collections.defaultdict(set)
    base = set()
    for _layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        for o in offs:
            base.add(sec9[o + 32])
            fx = sec9[o + 34]
            if sec9[o + 28] and FX_LO <= fx < FX_HI:
                refs[fx].append(o)
                blend[fx].add(sec9[o + 30])
    todo = [s for s in refs if s in pm and pm[s].depth == 1]
    todo.sort(key=lambda s: -len(refs[s]))
    import ff7nx_fxpcstatic as PS
    cells_by_slot = {}
    for s in refs:
        if s not in pm:
            continue
        try:
            own = PS._owners(sec9, s, pm[s], refs[s])
        except Exception:                                      # noqa: BLE001
            continue
        co = collections.defaultdict(list)
        for cell, q in own.items():
            co[q].append(cell)
        cells_by_slot[s] = (pm[s], co)
    refused, images = {}, {}
    # BUILD 618t safety rules, from the 618s hardware results:
    #  * a palette whose 16 lowest entries are a PERMUTATION of another
    #    palette's on the same page animates by palette choice (junbin5's
    #    bloom, ujunon1's smoke): no single Cosmos sheet stands for it;
    #  * a palette refused anywhere in the field for a big pulse refuses
    #    every page it is on, so one effect is never half frozen.
    pal = None
    if sec3 is not None:
        import ff7nx_framesim as FS
        pal = FS._palettes(sec3)[0]
    pals_of = {}
    for s in refs:
        pals_of[s] = {sec9[o + 22] for o in refs[s]}
    perm_pages = set()
    if pal is not None:
        import ff7nx_fxpcstatic as PS
        for s, ps in pals_of.items():
            import ff7nx_fxpages as FP2
            prov = art.provider

            def own_sheet(q):
                return (FP2._selected_palette(prov, name, s, q) == q
                        or bool(getattr(prov, 'state_slots', {}).get(
                            (name.lower(), s, q))))
            for q in ps:
                fam = [r for r in PS._family(pal, q, sorted(ps))
                       if r != q and (pal[r][:16] != pal[q][:16]).any()]
                # permuted palettes are fine only when Cosmos painted each
                # one its own sheet (icedun_2); a shared fallback sheet
                # cannot show the permutation (junbin5)
                if (q in scripted and fam
                        and not all(r in once for r in fam + [q])
                        and not all(own_sheet(r) for r in fam + [q])):
                    perm_pages.add(s)
    for s in todo:
        if s in perm_pages:
            refused[s] = 'palette-permutation animation'
            continue
        if s in base:
            refused[s] = 'base page'
            continue
        if blend[s] != {1}:
            refused[s] = 'blend %s' % sorted(blend[s])
            continue
        try:
            images[s] = _page_plan(art, name, sec9, s, pm[s], px, refs[s],
                                   scripted, cells_by_slot, rot, once)
        except Exception as exc:                               # noqa: BLE001
            refused[s] = str(exc)[:60]
    good = collections.defaultdict(set)
    for s, (_img, info) in images.items():
        for q in pals_of.get(s, ()):
            if q not in info.get('small_fade', {}):
                good[q].add(s)
    for s in list(images):
        for q in images[s][1].get('small_fade', {}):
            if not any(len(cells_by_slot.get(o, (None, {}))[1].get(q, ()))
                       >= MIN_CELLS for o in good[q]):
                refused[s] = 'palette %d fades on few cells' % q
                del images[s]
                break
    bad = set()
    for s, why in refused.items():
        if ('pulses' in why or 'animated by Cosmos' in why or 'fades' in why
                or 'cycle' in why or 'move' in why):
            bad |= pals_of.get(s, set()) & set(scripted)
    for s in list(images):
        if pals_of.get(s, set()) & bad:
            refused[s] = 'shares a fading palette with a kept page'
            del images[s]
            continue
        if images[s][1].get('borrowed') and refused:
            refused[s] = 'borrowed brightness in a partly kept field'
            del images[s]
    if not images:
        raise ValueError('nothing convertible: %s' % refused)
    plist, t0, t1 = FN.parse_texture_block(sec9, px)
    run = sum(FR._page_bytes(p.px, p.depth) for p in plist if p is not None)
    done = {}
    for s in [s for s in todo if s in images]:
        delta = FR._page_bytes(px, 2) - FR._page_bytes(plist[s].px,
                                                        plist[s].depth)
        if run + delta > mb_cap * 1048576.0:
            refused[s] = 'memory cap'
            continue
        img, info = images[s]
        q = plist[s]
        plist[s] = FN.Page(s, q.size_flag, 2, FP._encode_additive(img, px),
                           px)
        run += delta
        done[s] = info
    if not done:
        raise ValueError('memory cap: %s' % refused)
    out = FN.replace_texture_block(sec9, plist, t0, t1)
    if raw_cap is not None and other_bytes + len(out) > raw_cap:
        raise ValueError('raw %d over cap %d' % (other_bytes + len(out),
                                                  raw_cap))
    return out, done, refused, round(run / 1048576.0, 2)


def apply_to_flevel(archive, payloads, art, encode=None, mb_cap=35.0,
                    raw_cap=None, log=lambda *_: None):
    import lgp
    import ff7nx_fxpages as FP
    import ff7nx_fxpcstatic as PS
    import ff7nx_palanim as PA
    total = {'names': [], 'refused': [], 'pages': 0, 'records': 0}
    if disabled() or art is None:
        return total
    want = only()
    skip = (set(EXCLUDE) | set(CONFIRMED) | set(HOLD) | set(PS.FIELDS)
            | {n.lower() for n in FP.DEFER_FIELDS})
    for name in archive.names():
        entry = archive.index[name]
        if want and name.lower() not in want:
            continue
        if name.lower() in skip:
            continue
        try:
            if not archive.is_field(entry):
                continue
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            s9 = parts[8]
            if s9.find(b'BACK') < 0:
                continue
            pl = DC.parse_pages(s9)[0]
            if not any(q is not None and q.depth == 1 and
                       FX_LO <= q.slot < FX_HI for q in pl):
                continue
            scripted = PA.animated_palettes(parts[0])
            if len(scripted) >= 256:
                continue
            other = len(raw) - len(s9)
            blank_note = ''
            if name.lower() in BLANK:
                s9, gone = drop_blank(s9, BLANK[name.lower()])
                parts[8] = s9
                blank_note = ', %d blank-page record(s) removed' % gone
            parts[8], done, refused, mb = plan_field(
                name, s9, art, scripted,
                MB_OVERRIDE.get(name.lower(), mb_cap), other, raw_cap,
                rotates(parts[0]), parts[3], set_once(parts[0]))
        except Exception as exc:                               # noqa: BLE001
            msg = str(exc)
            if not msg.startswith('nothing convertible'):
                total['refused'].append((name, msg[:80]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        total['pages'] += len(done)
        total['names'].append('%s %s (%.1f MB%s)' % (
            name, ','.join(map(str, sorted(done))), mb, blank_note))
    return total


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  FX COMPLETE (BUILD 618t): %d paletted FX page(s) made '
                   'truecolor in %d field(s) (%s). %s=1 disables.'
                   % (st['pages'], len(st['names']),
                      '; '.join(st['names'][:12])
                      + (' ...' if len(st['names']) > 12 else ''), OFF_ENV))
    for name, why in st.get('refused', ())[:8]:
        out.append('  ! fx complete %s: %s' % (name, why))
    return '\n'.join(out)
