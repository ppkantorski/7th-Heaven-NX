#!/usr/bin/env python3
"""
diag_movies.py -- what size are the movies, and what size does the console
actually draw them?

    python3 diag_movies.py sdout                  # the built card
    python3 diag_movies.py "~/mods/Cosmos FMV"    # the mod's own sources
    python3 diag_movies.py sdout --csv out.csv

The second question is the one that was missing. "Is it being downscaled?"
cannot be answered by looking at the file, because nothing in the packer
scales -- the downscale happens on the device, where `video_p.glsl` takes a
single bilinear tap per output pixel. So this prints, per movie:

    name        source WxH   fps   ->  drawn WxH   verdict

where `drawn` is derived from the port's own draw path (see movies.py, whose
constants are asserted against exefs/main by tests/test_movie_scale.py):

    in-game     1440 x min(1440*h/w, 1080)   device pixels
    full screen  720*w/h x 720               device pixels

and the verdict is one of

    1:1          the file is the size the console draws -- ideal
    MINIFIED Nx  the file is larger; the GPU throws the excess away with a
                 2x2 box, which is the "huge file, soft picture" complaint.
                 Rebuild with Movie size = "Fit to what the console draws".
    magnified Nx the file is SMALLER than the console draws. Nothing in the
                 packer can help; install custom_shaders/hd_video/
                 video_p.glsl, which reconstructs magnified movies.

Exit status is 1 if anything is minified, so this can gate a build.
"""
import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import movies as mv                                          # noqa: E402


def walk(root):
    """Every video file under `root`, movies/ directories first."""
    hits = []
    for dirpath, _dirs, files in os.walk(root):
        for f in sorted(files):
            if os.path.splitext(f)[1].lower() in mv.MOVIE_EXT:
                hits.append(os.path.join(dirpath, f))
    hits.sort(key=lambda p: (os.path.basename(os.path.dirname(p)).lower()
                             != 'movies', p.lower()))
    return hits


def verdict(w, h):
    dw, dh = mv.device_footprint(w, h)
    if not w:
        return dw, dh, 'unreadable', 0.0
    ratio = w / float(dw)
    if ratio > 1.02:
        return dw, dh, 'MINIFIED %.2fx' % ratio, ratio
    if ratio < 0.98:
        return dw, dh, 'magnified %.2fx' % (1.0 / ratio), ratio
    return dw, dh, '1:1', ratio


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().split('\n')[0])
    ap.add_argument('root', help='a directory to walk (sdout, a mod, a dump)')
    ap.add_argument('--csv', help='also write a CSV here')
    ap.add_argument('--quiet', action='store_true',
                    help='only print the ones that are not 1:1')
    args = ap.parse_args(argv)

    root = os.path.expanduser(args.root)
    if not os.path.isdir(root):
        print('not a directory: %s' % root)
        return 2
    if not mv.have_ffmpeg():
        print('ffprobe not found -- install ffmpeg')
        return 2

    files = walk(root)
    if not files:
        print('no video files under %s' % root)
        return 2

    # Strip the directory prefix every file shares. On a built card that is
    # `atmosphere/contents/0100A5B00BDC6000/romfs/ff7/workingdir/data/movies`
    # and printing it 117 times hides the only column that varies.
    rels = [os.path.relpath(p, root) for p in files]
    common = os.path.dirname(os.path.commonprefix(
        [r + os.sep for r in rels])) if len(rels) > 1 else ''
    if common:
        print('all under %s%s' % (common, os.sep))
    names = [os.path.relpath(r, common) if common else r for r in rels]
    width = max(20, min(40, max(len(n) for n in names)))

    print('the console draws a movie into at most %dx%d device pixels'
          % (mv.TARGET_W, mv.TARGET_H))
    print('%-*s %-12s %-7s %-8s %-12s %s'
          % (width, 'movie', 'source', 'fps', 'codec', 'drawn', 'verdict'))
    print('-' * (width + 60))

    rows = []
    counts = {'1:1': 0, 'MINIFIED': 0, 'magnified': 0, 'unreadable': 0}
    by_size = {}
    worst = (1.0, None)
    for path, name in zip(files, names):
        info = mv.probe(path)
        if info is None:
            counts['unreadable'] += 1
            print('%-*s %s' % (width, name,
                               'not a video ffprobe understands'))
            continue
        w, h = info['width'] or 0, info['height'] or 0
        dw, dh, v, ratio = verdict(w, h)
        key = v.split()[0]
        counts[key] = counts.get(key, 0) + 1
        if ratio > worst[0]:
            worst = (ratio, name)
        rows.append({
            'movie': name, 'width': w, 'height': h,
            'fps': '%.6g' % info['fps'], 'codec': info['vcodec'],
            'color_space': info['color_space'] or '(untagged)',
            'drawn_w': dw, 'drawn_h': dh, 'verdict': v,
        })
        by_size.setdefault((w, h), []).append(name)
        if args.quiet and v == '1:1':
            continue
        print('%-*s %-12s %-7s %-8s %-12s %s'
              % (width, name, '%dx%d' % (w, h), '%.6g' % info['fps'],
                 info['vcodec'], '%dx%d' % (dw, dh), v))

    print('-' * (width + 60))

    # The shape of the PACK is the thing worth seeing. If one resolution
    # accounts for nearly everything, that resolution is the answer to "how
    # much detail is there", and the odd ones out are worth naming because
    # a single 640x448 file in a 1280x896 pack is a hole in the pack, not a
    # setting.
    if len(by_size) > 1:
        print('resolutions present:')
        for (w, h), who in sorted(by_size.items(),
                                  key=lambda kv: -len(kv[1])):
            shown = '' if len(who) > 4 else '  (%s)' % ', '.join(who)
            print('   %5dx%-5d  %3d file(s)%s' % (w, h, len(who), shown))
    print('%d file(s): %d at 1:1, %d minified, %d magnified, %d unreadable'
          % (len(files), counts.get('1:1', 0), counts.get('MINIFIED', 0),
             counts.get('magnified', 0), counts.get('unreadable', 0)))

    # Colour, the other half of "it does not look right".
    untagged = [r for r in rows if r['color_space'] in ('(untagged)', '')
                or r['color_space'] not in ('bt709',)]
    if untagged:
        print('%d file(s) are not tagged BT.709, which is the matrix '
              'romfs/shaders/video_p.glsl hardcodes -- rebuild with '
              'Movie colour = "Convert to BT.709"' % len(untagged))

    if counts.get('MINIFIED'):
        print('')
        print('  -> %d movie(s) are larger than the console draws. The '
              'biggest is %s at %.2fx.' % (counts['MINIFIED'], worst[1],
                                           worst[0]))
        print('     video_p.glsl samples with ONE bilinear tap, so those '
              'extra pixels are discarded by a 2x2 box filter on the '
              'device.')
        print('     Rebuild with Movie size = "Fit to what the console '
              'draws" to resample them here with Lanczos instead.')
    if counts.get('magnified'):
        print('')
        print('  -> %d movie(s) are SMALLER than the console draws. No '
              'packer setting can add detail they do not have.'
              % counts['magnified'])
        print('     Install custom_shaders/hd_video/video_p.glsl over '
              'romfs/shaders/video_p.glsl; it reconstructs magnified '
              'movies with Catmull-Rom.')

    if args.csv:
        with open(args.csv, 'w', newline='') as f:
            wr = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            wr.writeheader()
            wr.writerows(rows)
        print('\nwrote %s' % args.csv)

    return 1 if counts.get('MINIFIED') else 0


if __name__ == '__main__':
    sys.exit(main())
