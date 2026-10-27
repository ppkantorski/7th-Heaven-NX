#!/usr/bin/env python3
"""
test_movies.py -- FMV conversion for the Switch port.

    python3 test_movies.py [--samples DIR]

The interesting checks here need real video, so the test looks for a pair of
sample files and skips the transcode section if they are absent:

    <samples>/southmk.avi   the mod's file -- WebM/VP8+Vorbis despite the name
    <samples>/southmk.mp4   the file the Switch port itself ships

Default sample directory is ./samples, override with --samples.

WHAT IS BEING PROVED
====================
That a converted movie is structurally indistinguishable from the port's own
file: same codec, profile, pixel format, dimensions, frame rate, FRAME COUNT
and duration. Frame count is the one that matters most -- FF7 hands the
current movie frame to game code, so a file with a different number of frames
than the port expects is a file whose timing has changed.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import argparse
import os
import shutil
import sys
import tempfile

import movies

FAIL = []


def check(cond, msg):
    print(('  ok    ' if cond else '  FAIL  ') + msg)
    if not cond:
        FAIL.append(msg)


ap = argparse.ArgumentParser()
ap.add_argument('--samples', default=os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'samples'))
args = ap.parse_args()

SRC = os.path.join(args.samples, 'southmk.avi')
VAN = os.path.join(args.samples, 'southmk.mp4')

# ------------------------------------------------------------ pure logic
print('routing and policy (no ffmpeg needed)\n')

check('.avi' in movies.MOVIE_EXT and '.webm' in movies.MOVIE_EXT
      and '.mp4' in movies.MOVIE_EXT,
      'the containers an FMV mod might ship are all recognised')
check(movies.MOVIE_DIR == 'data/movies',
      'destination is data/movies, where the port looks')

check(not hasattr(movies, 'decide_fps') and
      not hasattr(movies, 'MOVIE_FPS_MATCH'),
      'there is no frame-rate setting left -- a movie is built at whatever '
      'rate its mod ships')

names = [n for n, _crf, _lab in movies.QUALITY_LEVELS]
crfs = [c for _n, c, _lab in movies.QUALITY_LEVELS]
check(names == ['maximum', 'high', 'balanced', 'small'],
      'quality levels, best first: %s' % names)
check(crfs == sorted(crfs),
      'and their crfs ascend as quality drops: %s' % crfs)
check(movies.QUALITY_DEFAULT == 'high',
      'default is `high` -- crf %d, about the bitrate the port itself ships'
      % movies.crf_for('high'))
check(movies.crf_for('maximum') == 10 and movies.crf_for('small') == 23,
      'maximum is crf 10, smallest is crf 23')
check(movies.crf_for('nonsense') == movies.crf_for(movies.QUALITY_DEFAULT),
      'an unrecognised level falls back to the default instead of failing '
      'the build')
check(movies.TARGET_PRESET == 'veryfast',
      'preset is fixed at veryfast -- it trades time for SIZE, not quality, '
      'so it is not a second dial')

check(not movies.already_target({'vcodec': 'vp8', 'pix_fmt': 'yuv420p',
                                 'profile': None, 'container': 'matroska,webm',
                                 'has_audio': True, 'acodec': 'vorbis'}),
      'a WebM/VP8 file is not mistaken for the target format')
check(movies.already_target({'vcodec': 'h264', 'pix_fmt': 'yuv420p',
                             'profile': 'High', 'container': 'mov,mp4,m4a',
                             'has_audio': True, 'acodec': 'aac'}),
      'an H.264/AAC mp4 is recognised as already correct')

# ------------------------------------------------------------- transcode
if not (os.path.exists(SRC) and os.path.exists(VAN)):
    print('\nsamples not found in %s -- transcode checks skipped' % args.samples)
    print('   (put southmk.avi and southmk.mp4 there to run them)')
    print('\n%s' % ('FAILED: %d' % len(FAIL) if FAIL else 'all checks passed'))
    sys.exit(1 if FAIL else 0)

if not movies.have_ffmpeg():
    print('\nffmpeg/ffprobe not on PATH -- transcode checks skipped')
    print('\n%s' % ('FAILED: %d' % len(FAIL) if FAIL else 'all checks passed'))
    sys.exit(1 if FAIL else 0)

print('\nthe mod file and the port\'s own file\n')
src = movies.probe(SRC)
van = movies.probe(VAN)
check(src['vcodec'] == 'vp8' and 'webm' in src['container'],
      'the mod ships WebM/VP8 in a file named .avi (%s/%s) -- the extension '
      'is not the format' % (src['container'], src['vcodec']))
check(van['vcodec'] == 'h264' and van['acodec'] == 'aac',
      'the port ships H.264 + AAC (%s/%s)' % (van['vcodec'], van['acodec']))

print('\nconversion output vs the port\'s file\n')
out_dir = tempfile.mkdtemp()
dest = os.path.join(out_dir, 'southmk.mp4')
r = movies.convert(SRC, dest, vanilla=VAN, log=print)   # default quality
out = r['out']

for field, label in (('vcodec', 'codec'), ('pix_fmt', 'pixel format'),
                     ('width', 'width'), ('height', 'height'),
                     ('fps_exact', 'frame rate'), ('nb_frames', 'FRAME COUNT'),
                     ('acodec', 'audio codec')):
    check(out[field] == van[field],
          '%-12s %s == %s' % (label, out[field], van[field]))

check((out['profile'] or '').lower() == (van['profile'] or '').lower(),
      'H.264 profile %s == %s' % (out['profile'], van['profile']))
check(abs(out['duration'] - van['duration']) < 0.005,
      'duration %.3fs vs %.3fs (within one frame)'
      % (out['duration'], van['duration']))
check(out['level'] <= van['level'],
      'H.264 level %.1f <= the port\'s own %.1f'
      % (out['level'] / 10.0, van['level'] / 10.0))
check(not r['borrowed_audio'],
      'the mod had its own audio, so none was borrowed from the original')

print('\nsilent source borrows the original soundtrack\n')
mute = os.path.join(out_dir, 'mute.webm')
os.system('ffmpeg -y -loglevel error -i "%s" -an -c:v copy -t 3 "%s"'
          % (SRC, mute))
if os.path.exists(mute):
    mi = movies.probe(mute)
    check(mi is not None and not mi['has_audio'], 'built a silent source')
    r2 = movies.convert(mute, os.path.join(out_dir, 'borrow.mp4'),
                        vanilla=VAN, log=print)
    check(r2['borrowed_audio'], 'audio was taken from the game\'s own file')
    check(r2['out']['acodec'] == 'aac', 'and encoded as AAC')

print('\na 30 fps source stays 30 fps, and its audio stays attached\n')
# Built here rather than shipped: a genuine 30 fps VP8/Vorbis file, which is
# the shape a 30 fps FMV pack ships. This one also reproduces the audio
# timestamp fault described in movies.py -- its Vorbis track lands on a
# 32/11025 timebase, and a naive re-encode gave an AAC track claiming 77
# seconds of presentation time for 16 seconds of sound.
src30 = os.path.join(out_dir, 'src30.avi')
os.system('ffmpeg -y -loglevel error -i "%s" -r 30 -c:v libvpx -b:v 1M '
          '-c:a libvorbis "%s"' % (SRC, src30))
i30 = movies.probe(src30)
if i30 and abs(i30['fps'] - 30.0) < 0.01:
    check(True, 'built a 30 fps VP8/Vorbis source (%d frames)' % i30['nb_frames'])
    r30 = movies.convert(src30, os.path.join(out_dir, 'out30.mp4'),
                         vanilla=VAN, log=print)
    o30 = r30['out']
    check(o30['fps_exact'] == '30/1',
          'it is built at 30 fps (%s), with no setting able to change that'
          % o30['fps_exact'])
    check(o30['nb_frames'] > van['nb_frames'] * 1.9,
          'and keeps the extra frames: %d vs the port\'s %d'
          % (o30['nb_frames'], van['nb_frames']))
    check(abs(o30['vdur'] - o30['adur']) < 0.2,
          'audio %.2fs agrees with video %.2fs -- the 32/11025 timebase did '
          'not leak into the output' % (o30['adur'], o30['vdur']))
    check(abs(o30['vdur'] - i30['vdur']) < 0.05,
          'and the running time is unchanged at %.2fs' % o30['vdur'])

    # The guard has to be able to fire, or it is decoration.
    real_probe = movies.probe

    def lying_probe(path):
        info = real_probe(path)
        if info and path.endswith('.part.mp4'):
            info['adur'] = (info['vdur'] or 0) + 60.0
        return info
    movies.probe = lying_probe
    try:
        movies.convert(SRC, os.path.join(out_dir, 'guard.mp4'), vanilla=VAN)
        check(False, 'a video/audio duration mismatch is rejected')
    except RuntimeError as exc:
        check('audio track timestamped' in str(exc),
              'a video/audio duration mismatch is rejected, not shipped')
    finally:
        movies.probe = real_probe
    check(not os.path.exists(os.path.join(out_dir, 'guard.mp4.part.mp4')),
          'and the half-written file is cleaned up')

print('\nthe exe is left alone, so the 60 FPS patcher still recognises it\n')
# The regression this pins down: FF7's field loop compares current_movie_frame
# against a hardcoded 1760 and a 30 fps opening reaches it in half the
# wall-clock time, so scaling that constant looks like the fix. It sits at
# file offset 0x23B6B8, INSIDE the .text range ff7nx_60fps.identify_exe()
# hashes to prove the executable is the build its addresses came from.
# Patching it there makes the hash miss, identify_exe aborts, and the WHOLE
# 60 FPS patch set silently does not apply -- the game drops to 30 fps and
# every battle and field fix goes with it.
EXE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ff7_en')
if os.path.exists(EXE):
    import struct as _struct
    exe_bytes = open(EXE, 'rb').read()
    try:
        import ff7nx_60fps as _F
        _F.identify_exe(exe_bytes, log=lambda *a: None)
        clean = True
    except SystemExit:
        clean = False
    check(clean, 'the untouched exe passes identify_exe')

    info = _F.pe_info(exe_bytes)
    text = next(s2 for s2 in info['sections'] if s2['name'] == '.text')
    CUE_OFF = 0x23B6B8
    check(text['raw'] <= CUE_OFF < text['raw'] + _F.TEXT_HASH_LEN,
          'the opening frame cue at 0x%X is inside the hashed .text range '
          '(0x%X..0x%X) -- this is why it cannot be patched before the 60 FPS '
          'step' % (CUE_OFF, text['raw'], text['raw'] + _F.TEXT_HASH_LEN))

    poisoned = exe_bytes[:CUE_OFF] + _struct.pack('<I', 3520) \
        + exe_bytes[CUE_OFF + 4:]
    try:
        _F.identify_exe(poisoned, log=lambda *a: None)
        check(False, 'scaling the cue must make identify_exe abort')
    except SystemExit:
        check(True, 'scaling it DOES abort identify_exe -- proving the '
                    '60 FPS patches would be lost')

    check(not hasattr(movies, 'opening_cue_address'),
          'so no code applies that patch: the helper is gone, not merely '
          'unused')

print('\nthe quality ladder moves size without breaking the format\n')
sizes = {}
for qname, qcrf, _lab in movies.QUALITY_LEVELS:
    qd = os.path.join(out_dir, 'q_%s.mp4' % qname)
    qr = movies.convert(SRC, qd, vanilla=VAN, quality=qname)
    sizes[qname] = os.path.getsize(qd)
    qo = qr['out']
    check(qr['crf'] == qcrf and qo['vcodec'] == 'h264'
          and (qo['profile'] or '').lower() == 'high'
          and qo['pix_fmt'] == 'yuv420p'
          and qo['nb_frames'] == van['nb_frames'],
          '%-9s crf %-3d %6.2f MB, still H.264 High/yuv420p, %d frames'
          % (qname, qr['crf'], sizes[qname] / 1e6, qo['nb_frames']))
ordered = [sizes[n] for n, _c, _l in movies.QUALITY_LEVELS]
check(ordered == sorted(ordered, reverse=True),
      'sizes fall as quality drops: %s'
      % ['%.1fMB' % (v / 1e6) for v in ordered])
check(sizes['maximum'] > sizes['small'] * 3,
      'and the ends of the ladder are far apart (%.1fMB vs %.1fMB)'
      % (sizes['maximum'] / 1e6, sizes['small'] / 1e6))

print('\ntrue lossless is refused by the encoder, not by us\n')
import subprocess
pr = subprocess.run(
    ['ffmpeg', '-y', '-nostdin', '-loglevel', 'error', '-i', SRC,
     '-map', '0:v:0', '-c:v', 'libx264', '-profile:v', 'high',
     '-pix_fmt', 'yuv420p', '-crf', '0', '-an',
     os.path.join(out_dir, 'lossless.mp4')],
    capture_output=True, text=True)
check(pr.returncode != 0 and 'lossless' in pr.stderr.lower(),
      'x264 rejects crf 0 in High profile: %s'
      % pr.stderr.strip().splitlines()[0][:70])
subprocess.run(
    ['ffmpeg', '-y', '-nostdin', '-loglevel', 'error', '-i', SRC,
     '-map', '0:v:0', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
     '-crf', '0', '-an', os.path.join(out_dir, 'lossless2.mp4')],
    capture_output=True, text=True)
li = movies.probe(os.path.join(out_dir, 'lossless2.mp4'))
check(li and '4:4:4' in (li['profile'] or ''),
      'unforced, it lands in %s -- a profile the console cannot decode, at '
      '%.1f MB against the port\'s own %.1f MB'
      % (li['profile'], os.path.getsize(os.path.join(out_dir, 'lossless2.mp4'))
         / 1e6, os.path.getsize(VAN) / 1e6))

# ------------------------------------------------------------ the cache
print('\nre-encoding happens once, not on every build\n')
import time

import build as B

work = tempfile.mkdtemp()
cachedir = os.path.join(work, 'cache')
moddir = os.path.join(cachedir, 'FMVMod')
for rel in ('Common/movies/southmk.avi', 'Common/movies/opening.avi'):
    dst = os.path.join(moddir, rel.replace('/', os.sep))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(SRC, dst)
with open(os.path.join(moddir, 'mod.xml'), 'w') as f:
    f.write('<?xml version="1.0"?><ModInfo><ID>t</ID><Name>FMV</Name>'
            '<ModFolder Folder="Common"/></ModInfo>')
iro_path = os.path.join(work, 'FMVMod.iro')
open(iro_path, 'wb').write(b'')

dumproot = os.path.join(work, 'dump')
wd = os.path.join(dumproot, 'romfs', 'ff7', 'workingdir')
os.makedirs(os.path.join(wd, 'data', 'movies'), exist_ok=True)
shutil.copyfile(VAN, os.path.join(wd, 'data', 'movies', 'southmk.mp4'))
dump = B.GameDump(dumproot, 'dump', wd)
sdout = os.path.join(work, 'sdout')
os.environ.pop(B.MOVIE_CACHE_ENV, None)


def run_build():
    mod = B.Mod(iro_path, cachedir)
    mod._load_manifest()
    plan = B.build_plan([mod], {mod.filename: mod.manifest.defaults()}, {},
                        lambda *a: None)
    t = time.time()
    B.apply_plan(plan, {}, sdout, log=lambda *_: None, dump=dump)
    return time.time() - t


cold = run_build()
warm = run_build()
check(warm < cold / 4,
      'second build reuses the cache: %.1fs -> %.2fs (%.0fx)'
      % (cold, warm, cold / max(warm, 0.01)))

mcache = os.path.join(work, 'cache', 'movies')
check(os.path.isdir(mcache) and len(os.listdir(mcache)) == 2,
      'cache holds both encodes (%s)' % sorted(os.listdir(mcache)))
check(not any('cache' in d.lower() for d, _s, _f in os.walk(sdout)),
      'the cache is NOT inside sdout -- it would be copied to the SD card '
      'and wiped whenever sdout is cleared')

# Re-extracting an .iro rewrites every file with a fresh mtime. The cache is
# keyed on content, so that must not throw the work away.
for rel in ('Common/movies/southmk.avi', 'Common/movies/opening.avi'):
    os.utime(os.path.join(moddir, rel.replace('/', os.sep)), None)
again = run_build()
check(again < cold / 4,
      're-extracting the mod does not bust the cache: %.2fs' % again)

shutil.rmtree(sdout, ignore_errors=True)
rebuilt = run_build()
check(rebuilt < cold / 4,
      'deleting sdout does not bust it either: %.2fs' % rebuilt)

out_file = os.path.join(sdout, 'atmosphere', 'contents', B.TITLE_ID, 'romfs',
                        'ff7', 'workingdir', 'data', 'movies', 'southmk.mp4')
check(os.path.exists(out_file), 'and the movie is back in sdout')
check(os.stat(out_file).st_nlink > 1,
      'placed by hardlink, so it costs no extra disk or copy time')

# Changing something that changes the OUTPUT must re-encode.
os.environ[B.movie_convert.QUALITY_ENV] = 'small'
try:
    flipped = run_build()
    check(flipped > cold / 4,
          'changing the quality setting DOES re-encode (%.1fs) rather than '
          'handing back a file made at the old one' % flipped)
finally:
    os.environ.pop(B.movie_convert.QUALITY_ENV, None)

shutil.rmtree(work, ignore_errors=True)
shutil.rmtree(out_dir, ignore_errors=True)
print('\n%s' % ('FAILED: %d' % len(FAIL) if FAIL else 'all checks passed'))
sys.exit(1 if FAIL else 0)
