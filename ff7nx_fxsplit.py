#!/usr/bin/env python3
"""Give the STATIC palettes of a mixed additive FX page their Cosmos art.

BUILD 579. The general counterpart of ``ff7nx_fxmargin``'s hardware-proven
mds5_5 palette split (build 139).

WHY. ``ff7nx_fxpages`` converts an additive FX page (slots 15..23) in place,
but only when every palette on it resolves to ONE static Cosmos DDS. One
animated palette on the page -- a Cosmos "runtime state" sequence FFNx swaps
while the palette cycles -- vetoes the whole page, because a truecolor page
has no palette and cannot animate. So every static effect sharing a page with
an animated one stayed 1997 art at 256px: the Chocobo Square save crystal in
chorace/chorace2 (palette 10, one DDS) sits on page 15 with the racing
lights (palettes 5/6/7/11/12, 11..33 states each). Measured on the build-574
archive: 100 such pages in 83 fields, 17,847 tile references.

WHAT. The animated palettes stay exactly where they are, on the original
paletted page, still animated. Each STATIC resolved DDS gets its own clone:
a new depth-2 page in a free slot of the same additive band (15..23), the
full Cosmos atlas at page size with alpha premultiplied into the colour (the
build-139 encoding), and only that palette's tiles have their FX page byte
repointed to it. Nothing else in a tile moves -- the packed UV is normalised,
so the same cell is sampled at the new resolution -- and the base page, the
palette index, the blend mode and every animation record are untouched.

The FINDINGS-194 module ladder already makes a depth-2 page in 15..23
additive; mds5_2/mds5_3/mds5_5 have drawn from exactly such clones since
build 139.

REFUSALS (the page is left byte-identical):
  * not a depth-1 FX-only page in 15..23, or any reference not blend mode 1;
  * no palette on it is animated (that page is ``ff7nx_fxpages``'s) or no
    palette is static (nothing to split);
  * a palette with no Cosmos DDS at all (exact or palette 0);
  * not enough free band slots, the per-field page ceiling, or the byte
    budgets the caller passes -- all-or-nothing per page.

``SEVENTH_NX_NO_FX_SPLIT=1`` disables the pass.

BUILD 580 -- FROZEN HD FRAMES, BY REQUEST, PER (FIELD, PALETTE).
Some effects are a palette ANIMATION in the game and a Cosmos state sequence
on PC: jet's "Welcome! Gold Saucer" / "THRILL... SUSPENCE..." sign is
palettes 13 and 9 on page 15, 64 and 61 Cosmos frames of a neon chase. A
truecolor page cannot cycle a palette, so HD there means ONE frame. For the
(field, palette) pairs in `FREEZE` the palette is treated as static using its
BRIGHTEST Cosmos frame (the whole sign lit; measured frames range 4%..100%
of that). The two messages still alternate -- that is tile state, not
palette -- but the chase no longer runs. Everything else on the page keeps
animating.

    SEVENTH_NX_FX_FREEZE=0                    none (the 1997 animated sign)
    SEVENTH_NX_FX_FREEZE=jet:9,13;field:pal   replace the list
"""
from __future__ import annotations

import collections
import os

import numpy as np

import diag_common as DC
import field_bg_native as FN
import field_bg_repack as FR
import ff7nx_fxmargin as FXM
import ff7nx_fxpages as XP
import ff7nx_marginblack as MB

OFF_ENV = 'SEVENTH_NX_NO_FX_SPLIT'
FREEZE_ENV = 'SEVENTH_NX_FX_FREEZE'
# BUILD 581: empty by default. Freezing jet's sign (BUILD 580) made its two
# alternating messages draw over each other on hardware; a frozen frame is
# not the effect. Kept only as an explicit opt-in.
FREEZE = {}
FX_LO, FX_HI = 0x0F, 0x18
T_FX = FN.TILE_TEXTURE_ID2
T_BLEND_MODE = 30


def enabled():
    return (XP.enabled() and os.environ.get(OFF_ENV, '').strip().lower()
            not in ('1', 'true', 'yes', 'on'))


def freeze_map(env=None):
    e = os.environ if env is None else env
    raw = e.get(FREEZE_ENV, '').strip()
    if not raw:
        return dict(FREEZE)
    if raw.lower() in ('0', 'off', 'none', 'no'):
        return {}
    out = {}
    for part in raw.split(';'):
        if ':' not in part:
            continue
        f, pals = part.split(':', 1)
        out[f.strip().lower()] = frozenset(
            int(x) for x in pals.split(',') if x.strip())
    return out


def _brightest_state(provider, field, slot, q, px):
    """The Cosmos runtime frame with the most light, resampled, or None."""
    try:
        import dds_decode
        best, best_v = None, -1.0
        for path, entry in provider.state_slots.get((field, slot, q), ()):
            reader = provider.readers.get(path)
            if reader is None:
                reader = provider.readers[path] = FR.IroReader(path)
            blob = reader.read(entry)
            if not blob:
                return None
            rgba, w, h = dds_decode.decode_dds(blob)
            img = np.frombuffer(FR.resample_rgba(rgba, w, h, px),
                                np.uint8).reshape(px, px, 4)
            v = float((img[..., :3].astype(np.float32).sum(-1)
                       * (img[..., 3] / 255.0)).sum())
            if v > best_v:
                best, best_v = np.ascontiguousarray(img), v
        return best
    except Exception:                                          # noqa: BLE001
        return None


def _encode(img, px):
    enc = np.ascontiguousarray(img, np.uint8).copy()
    a = enc[..., 3].astype(np.uint16)
    enc[..., :3] = ((enc[..., :3].astype(np.uint16) * a[..., None] + 127)
                    // 255).astype(np.uint8)
    enc[..., 3] = 255
    return FR.rgba_to_565_buf(enc.tobytes(), px * px, width=px, black_ok=True)


def new_stats():
    return {'fields': 0, 'pages': 0, 'tiles': 0, 'kept_animated': 0,
            'frozen': 0,
            'bytes': 0, 'veto_art': 0, 'veto_fit': 0, 'veto_budget': 0,
            'names': []}


def split_section9(name, sec9, art, px, max_raw_delta=None,
                   max_runtime_delta=None, reserve_pages=0,
                   animated=frozenset()):
    """Return ``(section9, stats)``."""
    st = new_stats()
    if not enabled() or name.lower() in FXM.TARGET_FIELDS:
        return sec9, st
    provider = getattr(art, 'provider', None)
    if provider is None:
        return sec9, st
    field = name.lower()
    frozen = freeze_map().get(field, frozenset())
    try:
        plist, tex_start, tex_end = FN.parse_texture_block(sec9, px)
        pages = {p.slot: p for p in plist if p is not None}
        tiles = MB.read_tiles(sec9, DC.survey(sec9), pages)
    except Exception:                                          # noqa: BLE001
        return sec9, st

    fx_refs = collections.defaultdict(list)
    base_slots = set()
    for t in tiles:
        base_slots.add(t.slot)
        fx = sec9[t.off + T_FX]
        if fx:
            fx_refs[fx].append(t)

    plans = []          # (slot, [(q, image, [tiles])], kept_tiles)
    for slot in range(FX_LO, FX_HI):
        page = pages.get(slot)
        refs = fx_refs.get(slot, ())
        if (page is None or page.depth != 1 or page.px != FN.D1_PAGE_PX
                or page.size_flag or not refs or slot in base_slots):
            continue
        if any(sec9[t.off + T_BLEND_MODE] != 1 for t in refs):
            continue
        by_q = collections.defaultdict(list)
        missing = False
        for t in refs:
            q = XP._selected_palette(provider, field, slot, t.pal)
            if q is None:
                missing = True
                break
            by_q[q].append(t)
        if missing:
            continue
        live = set() if XP.static_animated() else set(animated)
        animated_q = {q for q in by_q
                      if ((field, slot, q) in provider.ambiguous_slots
                          or any(t.pal in live for t in by_q[q]))
                      and not (q in frozen and all(
                          t.pal in frozen for t in by_q[q]))}
        static = [q for q in sorted(by_q) if q not in animated_q]
        if not animated_q or not static:
            continue
        groups = []
        seen = {}
        bad = False
        for q in static:
            if (field, slot, q) in provider.ambiguous_slots:
                img = _brightest_state(provider, field, slot, q, px)
                st['frozen'] += len(by_q[q])
            else:
                img = FXM._provider_rgba(art, field, slot, q, px)
            if (img is None or img.shape != (px, px, 4)
                    or not np.any(img[..., 3] >= 8)):
                bad = True
                break
            # Two palettes that resolve to byte-identical art share a clone.
            key = img.tobytes()
            if key in seen:
                groups[seen[key]][2].extend(by_q[q])
                continue
            seen[key] = len(groups)
            groups.append((q, img, list(by_q[q])))
        if bad:
            st['veto_art'] += 1
            continue
        kept = sum(len(by_q[q]) for q in animated_q)
        plans.append((slot, groups, kept))

    if not plans:
        return sec9, st

    cap = FR.max_total_pages()
    present = len(pages)
    free = [s for s in range(FX_LO, FX_HI) if s not in pages]
    raw_left = max_raw_delta if max_raw_delta is not None else 1 << 60
    run_left = max_runtime_delta if max_runtime_delta is not None else 1 << 60
    d2_raw = FN.stored_bytes(px, 2)
    d2_run = FR._page_bytes(px, 2)
    buf = bytearray(sec9)
    new_pages = {}
    # Most-referenced page first: if only one fits, it is the one seen most.
    plans.sort(key=lambda p: -sum(len(g[2]) for g in p[1]))
    for slot, groups, kept in plans:
        n = len(groups)
        if len(free) < n or (cap and present + len(new_pages) + n
                             + reserve_pages > cap):
            st['veto_fit'] += 1
            continue
        if n * d2_raw > raw_left or n * d2_run > run_left:
            st['veto_budget'] += 1
            continue
        for _q, img, gtiles in groups:
            ns = free.pop(0)
            new_pages[ns] = FN.Page(ns, 0, 2, _encode(img, px), px)
            for t in gtiles:
                buf[t.off + T_FX] = ns
            st['tiles'] += len(gtiles)
        raw_left -= n * d2_raw
        run_left -= n * d2_run
        st['bytes'] += n * d2_raw
        st['pages'] += n
        st['kept_animated'] += kept

    if not new_pages:
        return sec9, st
    plist2, ts2, te2 = FN.parse_texture_block(bytes(buf), px)
    for s, p in new_pages.items():
        plist2[s] = p
    out = FN.replace_texture_block(bytes(buf), plist2, ts2, te2)
    st['fields'] = 1
    st['names'] = [name]
    return out, st


def merge(total, one):
    for k in ('fields', 'pages', 'tiles', 'kept_animated', 'bytes', 'frozen',
              'veto_art', 'veto_fit', 'veto_budget'):
        total[k] = total.get(k, 0) + one.get(k, 0)
    total.setdefault('names', []).extend(one.get('names', ()))
    return total


def summarise(st):
    if not st or not (st.get('fields') or st.get('veto_fit')
                      or st.get('veto_budget') or st.get('veto_art')):
        return None
    return ('FX PALETTE SPLIT (BUILD 579): static palettes on mixed '
            'additive pages given their own Cosmos truecolor page -- %d '
            'page(s), %d tile reference(s) in %d field(s); %d animated '
            'reference(s) left animating on the original page; %d '
            'reference(s) frozen on their brightest Cosmos frame by %s. '
            'Vetoed: %d '
            'no free slot/page ceiling, %d byte budget, %d art. Set %s=1 to '
            'disable.' % (st['pages'], st['tiles'],
                          st['fields'], st['kept_animated'],
                          st.get('frozen', 0), FREEZE_ENV, st['veto_fit'],
                          st['veto_budget'], st['veto_art'], OFF_ENV))
