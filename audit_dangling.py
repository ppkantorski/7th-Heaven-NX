#!/usr/bin/env python3
"""
audit_dangling.py -- tiles that point at a page which is not present.

HANDOFF-78 3.4: vanilla ALREADY ships dangling references (cosmo 32, cosmo2
620, fr_e 125, gaiin_7 200, junair 1; fx pointing at a missing page in
bugin1a, las4_2, las4_3, trnad_3). So the number that matters is not the
count, it is the count MINUS vanilla's.

Compaction frees pages by marking them absent. If any tile that referenced a
freed slot was not rewritten -- a gap in the freeze fixpoint, an fx partner
missed -- the tile is left pointing at an absent page. That is a null texture
handle at draw time, which is a crash candidate rather than a black square.

    python3 audit_dangling.py <built.lgp> --vanilla <vanilla.lgp>
"""
import argparse, collections
import lgp, field_bg_native as FN, field_bg_compact as FC


def scan(path):
    arc = lgp.Archive(path)
    out = {}
    for nm in sorted(arc.index):
        e = arc.index[nm]
        if not arc.is_field(e):
            continue
        try:
            parts = lgp.split_sections(arc.decompressed(e))
            sec9 = parts[8]
            pages, ts, te = FN.parse_texture_block(sec9, FN.VANILLA_PX)
            spans = FN._layer_tile_spans(sec9, sec9.find(b'BACK'), ts)
        except Exception:
            continue
        present = {p.slot for p in pages if p is not None}
        main = fx = 0
        for off in spans:
            tex = sec9[off + FC.T_TEXID]
            fxs = sec9[off + FC.T_FX_PAGE]
            if tex not in present:
                main += 1
            if fxs and fxs not in present:
                fx += 1
        out[nm] = (main, fx, len(present))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('built')
    ap.add_argument('--vanilla', required=True)
    ap.add_argument('--top', type=int, default=25)
    args = ap.parse_args()
    van = scan(args.vanilla)
    new = scan(args.built)
    rows = []
    tm = tf = 0
    for nm, (m, f, npg) in new.items():
        vm, vf, vpg = van.get(nm, (0, 0, 0))
        dm, df = m - vm, f - vf
        if dm > 0 or df > 0:
            rows.append((nm, dm, df, m, f, vm, vf, npg))
            tm += max(0, dm)
            tf += max(0, df)
    print()
    print('  tiles pointing at an ABSENT page, built vs vanilla')
    print(f'  built   {args.built}')
    print()
    print(f'  fields measured                      {len(new):>8,}')
    print(f'  fields with NEW dangling main refs   '
          f'{sum(1 for r in rows if r[1] > 0):>8,}')
    print(f'  fields with NEW dangling fx refs     '
          f'{sum(1 for r in rows if r[2] > 0):>8,}')
    print(f'  NEW dangling main tile refs (total)  {tm:>8,}')
    print(f'  NEW dangling fx   tile refs (total)  {tf:>8,}')
    print()
    if rows:
        print(f'  worst {args.top} (delta main, delta fx, built m/f, '
              f'vanilla m/f, pages):')
        for nm, dm, df, m, f, vm, vf, npg in sorted(
                rows, key=lambda r: -(r[1] + r[2]))[:args.top]:
            print(f'    {nm:<14} {dm:>+6} {df:>+6}   built {m}/{f}'
                  f'   vanilla {vm}/{vf}   pages {npg}')
    else:
        print('  no field gained a dangling reference.')
    print()


if __name__ == '__main__':
    main()
