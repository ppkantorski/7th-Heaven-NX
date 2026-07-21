"""
Diagnostic: report TEX texture formats in a folder tree or LGP archive.

Usage:
    python3 diag_tex.py <folder-or-lgp> [more...]

For each source, classifies every TEX found as paletted or truecolor and
prints per-folder stats. Run it against the extracted mod cache, e.g.:

    python3 diag_tex.py "cache/<ninostyle battle>/Mains" \
                        "cache/<ninostyle battle>/Enemies"

Purpose: settle empirically whether the battle player models that render
correctly on hardware ship paletted or truecolor textures. If Mains are
truecolor and render fine, white enemies are NOT a truecolor issue (but the
death dissolve still is, being palette-driven). If Mains are paletted, the
truecolor->white theory holds for battle.
"""
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lgp  # noqa: E402
import tex  # noqa: E402


def classify(data):
    t = tex.parse(data)
    if not t:
        return None
    if t['palette_flag']:
        return (f'paletted {t["num_palettes"]}x{t["colors_per_palette"]}',
                t['width'], t['height'])
    return (f'truecolor {t["bytes_per_pixel"] * 8}bit',
            t['width'], t['height'])


def scan_folder(root):
    stats = defaultdict(Counter)
    for dirpath, _, files in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        top = rel.split(os.sep)[0] if rel != '.' else '.'
        for fn in files:
            try:
                with open(os.path.join(dirpath, fn), 'rb') as f:
                    c = classify(f.read())
            except OSError:
                continue
            if c:
                kind, w, h = c
                stats[top][f'{kind} ({w}x{h})'] += 1
    return stats


def scan_lgp(path):
    stats = defaultdict(Counter)
    a = lgp.Archive(path)
    for e in a.entries:
        c = classify(e['payload'])
        if c:
            kind, w, h = c
            stats['.'][f'{kind} ({w}x{h})'] += 1
    return stats


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    for src in sys.argv[1:]:
        print(f'== {src}')
        if os.path.isdir(src):
            stats = scan_folder(src)
        else:
            stats = scan_lgp(src)
        for top in sorted(stats):
            total = sum(stats[top].values())
            print(f'  {top}/  ({total} TEX)')
            for kind, n in stats[top].most_common(12):
                print(f'    {n:5d}  {kind}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
