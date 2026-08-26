#!/usr/bin/env python3
"""
ff7nx_field169.py -- build integration for 16:9 field.

Wires ff7nx_framing (the framing half) and ff7nx_fieldwide (the content
half) into a build as ONE switch, because they are two halves of one change
and neither is a partial step:

    framing alone   -> a wider view onto a field whose parallax still wraps
                       at 320 units and whose camera still walks 53 units
                       too far. Edge artefacts.
    fieldwide alone -> 108 units of camera travel given up on 341 fields,
                       with no upside at all (README-widescreen-v5).

So this is driven by a single value of the EXISTING widescreen setting,
`SEVENTH_NX_WIDESCREEN=field`, rather than two switches that can disagree.
`ff7nx_widescreen.mode()` returns '' for that value, so the known-bad
`stretch` and `fit` patches stay off and cannot compose with it.

Three things happen, in this order:

    1. exefs/main  <- ff7nx_framing   (viewport 640->854, game_w cave)
    2. exefs/main  <- ff7nx_fieldwide (parallax clip/wrap 352->459, 160->213)
    3. flevel.lgp  <- camera_range narrowed on the 341 qualifying fields

Steps 1 and 2 both edit `main` and are CHAINED -- 2 reads 1's output. Both
run after apply_fps_patches and on ITS output, for the reason documented
there: a pass that bases on the dump's stock module silently reverts 110
word patches and 24 caves.

INSTALL
-------
Two edits, no changes to build.py:

  7th_heaven_nx.py, WIDESCREEN_CHOICES -- add the row:

      ('field', '16:9 field -- framing + content'),

  7th_heaven_nx.py, right after the apply_widescreen line in run_build():

      import ff7nx_field169
      produced += ff7nx_field169.apply(SDOUT_DIR, DUMP, log, produced)
"""
import os
import shutil

MODE = 'field'
WIDESCREEN_ENV = 'SEVENTH_NX_WIDESCREEN'


def enabled():
    return os.environ.get(WIDESCREEN_ENV, '').strip().lower() == MODE


def _same_file(a, b):
    try:
        return (os.path.getsize(a) == os.path.getsize(b)
                and open(a, 'rb').read() == open(b, 'rb').read())
    except OSError:
        return False


def _patch_main(sdout, dump, log, produced):
    """
    Steps 1 and 2, chained. Returns the destination path, or None.

    Carries over apply_widescreen's refusal: if `main` is already in sdout
    and is NOT something this build produced, stop rather than base on the
    dump and throw away an earlier run's 60 FPS work.
    """
    import build
    import ff7nx_framing
    import ff7nx_fieldwide

    dest = os.path.join(sdout, 'atmosphere', 'contents', build.TITLE_ID,
                        'exefs', 'main')
    fresh = {os.path.normpath(os.path.abspath(p)) for p in produced}
    built = os.path.normpath(os.path.abspath(dest)) in fresh
    src = dest if built else dump.nso

    if not built and os.path.exists(dest) and not _same_file(dest, dump.nso):
        log(f'! 16:9 field: {dest}')
        log('  already holds a module this build did not produce -- most '
            'likely a patched one from an earlier run. Basing on the dump\'s '
            'stock copy would silently throw those patches away, so nothing '
            'was written.')
        log('  Turn the 60 FPS switch on so both passes run together, or '
            'delete sdout/ and rebuild.')
        return None

    log(f'  base main   {src}'
        + ('   (60 FPS output)' if built else '   (from dump)'))

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    step1 = dest + '.f169-a'
    step2 = dest + '.f169-b'
    try:
        if not ff7nx_framing.apply_to_nso(src, step1, log):
            log('! 16:9 field: framing FAILED, nothing written')
            return None
        if not ff7nx_fieldwide.apply_to_nso(step1, step2, log):
            log('! 16:9 field: parallax FAILED, nothing written')
            return None
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        os.replace(step2, dest)
    finally:
        for tmp in (step1, step2):
            if os.path.exists(tmp):
                os.remove(tmp)
    return dest


def _patch_flevel(sdout, dump, log, produced):
    """
    Step 3. Narrow camera_range on the qualifying fields.

    Prefers a flevel.lgp this build just wrote (so a chunk mod's fields are
    the ones transformed); falls back to the dump's, copying it into sdout.
    Cosmos Limit Break ships only .chunk.9 sections, so the two agree on
    camera ranges -- verified, 341/711 either way -- but preferring the
    built one keeps that from being an assumption.
    """
    import build
    import lgp
    import ff7nx_fieldwide

    rel = build.ARCHIVES['flevel.lgp']
    dest = os.path.join(sdout, 'atmosphere', 'contents', build.TITLE_ID,
                        build.ROMFS, rel)
    fresh = {os.path.normpath(os.path.abspath(p)) for p in produced}
    built = os.path.normpath(os.path.abspath(dest)) in fresh

    # A verified flevel reused by build.apply_plan already contains this
    # camera-clamp data from its normal build.  Re-running the archive pass
    # here would defeat SEVENTH_NX_REUSE_FLEVEL, take the same minutes the
    # user is trying to avoid, and change the file's mtime so the next fast
    # run could no longer verify it against the cache record.  Keep the data
    # half byte-for-byte while `_patch_main` above still refreshes the module
    # half alongside all the other requested executable patches.
    if os.environ.get(build.REUSE_FLEVEL_ENV, '').strip().lower() in (
            '1', 'true', 'yes', 'on'):
        if not os.path.isfile(dest):
            # Normally unreachable because apply_plan validates first, but
            # retain a local refusal if this helper is called independently.
            log('! 16:9 field: fast flevel reuse requested but the existing '
                'sdout archive is missing; camera clamp not touched')
            return None
        log('  camera clamp: kept from the verified reused flevel.lgp '
            '(data-side rebuild skipped)')
        return dest

    if built or os.path.exists(dest):
        src = dest
        log('  base flevel %s   (%s)'
            % (src, 'this build' if built else 'already in sdout'))
    else:
        if not dump or not dump.workingdir:
            log('! 16:9 field: no flevel.lgp and no workingdir; camera clamp '
                'skipped -- the framing will be wider than the clamp allows')
            return None
        src = os.path.join(dump.workingdir, rel)
        if not os.path.exists(src):
            log(f'! 16:9 field: {src} not found; camera clamp skipped')
            return None
        log(f'  base flevel {src}   (from dump)')

    try:
        archive = lgp.Archive(src)
    except Exception as exc:
        log(f'! 16:9 field: cannot read flevel.lgp ({exc}); clamp skipped')
        return None

    # Route the LZS pass through build's content-keyed cache. It is pure
    # Python and costs minutes over 341 fields on a cold run; cached, a
    # rebuild is effectively free. Same cache the Cosmos chunk pass uses, so
    # a field both passes touch is only ever compressed once per content.
    encode = getattr(build, '_encode_field_cached', None)

    changed = 0
    last = [0.0]

    def tick(done, total, name):
        import time
        if time.time() - last[0] > 5.0:
            last[0] = time.time()
            log(f'    ... {done} field(s) re-encoded (latest {name})')

    for _name, _before, _after in ff7nx_fieldwide.transform_archive_fields(
            archive, log=lambda *_: None, encode=encode, progress=tick):
        changed += 1
    if not changed:
        log('  camera clamp: no field qualified -- nothing written')
        return None

    tmp = dest + '.f169-tmp'
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        archive.write(tmp)
        # same table check _build_flevel does: a rebuild that loses the
        # lookup/conflict tables produces an archive the game cannot read.
        if lgp.Archive(tmp).middle != archive.middle:
            log('  ! 16:9 field: flevel tables did not survive rebuild; '
                'rejected, camera clamp not applied')
            return None
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    size = os.path.getsize(dest)
    log(f'  camera clamp: {changed} field(s) narrowed, wrote flevel.lgp '
        f'({size:,} bytes)')
    return dest


def apply(sdout, dump, log=lambda *_: None, produced=()):
    """
    The whole pass. Returns a list of newly-produced paths.

    Nothing is written unless SEVENTH_NX_WIDESCREEN=field.
    """
    if not enabled():
        return []
    if dump is None or not dump.nso:
        log('! 16:9 field: needs exefs/main from a full game dump; skipped')
        return []

    log('')
    log('applying 16:9 field (framing + content) ...')

    out = []
    main_dest = _patch_main(sdout, dump, log, produced)
    if main_dest is None:
        log('! 16:9 field: FAILED -- the rest of the build is still valid, '
            'this tree just stays 4:3')
        return []
    fresh = {os.path.normpath(os.path.abspath(p)) for p in produced}
    if os.path.normpath(os.path.abspath(main_dest)) not in fresh:
        out.append(main_dest)

    flevel_dest = _patch_flevel(sdout, dump, log, list(produced) + out)
    if flevel_dest and os.path.normpath(os.path.abspath(flevel_dest)) \
            not in fresh:
        out.append(flevel_dest)

    log('')
    log('  What to look for on hardware:')
    log('    - the field should be WIDER, not stretched. Character and')
    log('      background pixel size must be unchanged from a 4:3 build.')
    log('    - menus, battle and the start screen must be UNCHANGED. They')
    log('      read a different viewport rect and nothing here touches it.')
    log('    - on fields with no art past the 4:3 crop, expect black at the')
    log('      sides. 370 of 711 fields are in that group.')
    log('  If ANYTHING is stretched, this patch set is wrong -- say so')
    log('  rather than tuning numbers, because stretch means the model is')
    log('  wrong and three builds have already been spent on that.')
    return out
