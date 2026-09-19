#!/usr/bin/env python3
"""Focused regression checks for Cosmos Gaia's native world-texture path."""
from __future__ import annotations

import os
import shutil
import tempfile
import time

import build
import ff7nx_field169
import ff7nx_gaia
import tex


HERE = os.path.dirname(os.path.abspath(__file__))
GAIA = os.path.join(HERE, 'cache', 'CosmosGaia', 'GAIA', 'world')
VAN = os.path.join(HERE, 'cache', '_vanilla', 'world_us.lgp')


def ok(cond, message):
    if not cond:
        raise AssertionError(message)
    print('ok:', message)


def main():
    # Strip exactly one FFNx frame suffix.  The second underscore in fld_02
    # belongs to the native name and must survive.
    ok(build._world_dds_name('pond_00.dds') == ('pond.tex', 0),
       'simple frame-zero name maps to native TEX')
    ok(build._world_dds_name('fld_02_00.dds') == ('fld_02.tex', 0),
       'underscored native name is preserved')
    ok(build._world_dds_name('dfx_17.dds') == ('dfx.tex', 17),
       'nonzero FFNx animation frame is identified')
    ok(build._switch_world_dds('GAIA/world/dfx_00.dds'),
       'frame zero under world is retained at extraction')
    ok(not build._switch_world_dds('GAIA/world/dfx_17.dds'),
       'extra FFNx animation frames remain excluded')
    ok(not build._switch_world_dds('GAIA/mesh/world/wm0.gltf'),
       'Gaia mesh is never mistaken for a native texture')

    if not os.path.isdir(GAIA) or not os.path.isdir(VAN):
        print('skip: extracted CosmosGaia/vanilla world cache is unavailable')
        return

    native = {n.lower() for n in os.listdir(VAN)
              if n.lower().endswith('.tex')}
    mapped = {}
    duplicate = []
    for fn in os.listdir(GAIA):
        parsed = build._world_dds_name(fn)
        if parsed and parsed[1] == 0 and parsed[0] in native:
            if parsed[0] in mapped:
                duplicate.append(parsed[0])
            mapped[parsed[0]] = fn
    ok(not duplicate, 'no native TEX slot has two frame-zero owners')
    ok(len(native) == 415, 'vanilla world archive has the measured 415 TEX slots')
    ok(set(mapped) == native,
       'Gaia provides one exact frame-zero DDS for every native TEX slot')

    # Exercise a high-resolution real source through the same function the
    # archive build calls.  The output must remain native indexed TEX, retain
    # the destination's complete palette block, and obey the GUI cap.  The
    # world renderer showed every 32-bit replacement as black in build 179.
    src_name = 'fld_00.dds'
    src = os.path.join(GAIA, src_name)
    temp = tempfile.mkdtemp(prefix='gaia-world-test-')
    old_cache = build.WORLD_DDS_CACHE
    old_cap = os.environ.get(build.WORLD_TEX_CAP_ENV)
    old_reuse = os.environ.get(build.REUSE_FLEVEL_ENV)
    try:
        build.WORLD_DDS_CACHE = temp
        os.environ[build.WORLD_TEX_CAP_ENV] = '512'
        fake_mod = object()
        logs = []
        out = build._convert_world_dds(
            {'fld.tex': (src, fake_mod)},
            {'fld.tex': os.path.join(VAN, 'fld.tex')}, logs.append)
        payload = open(out['fld.tex'][0], 'rb').read()
        parsed = tex.parse(payload)
        vanilla_payload = open(os.path.join(VAN, 'fld.tex'), 'rb').read()
        vanilla_parsed = tex.parse(vanilla_payload)
        ok(parsed is not None, 'converted Gaia payload reparses as TEX')
        ok(parsed['palette_flag'] == 1 and parsed['bytes_per_pixel'] == 1,
           'converted Gaia payload uses native indexed world format')
        ok(parsed['num_palettes'] == 1
           and parsed['colors_per_palette'] == 256,
           'terrain receives a fresh standard 256-colour palette')
        ok(parsed['palette'] != vanilla_parsed['palette'],
           'Gaia colours are not collapsed onto the native 16-colour palette')
        ok(parsed['width'] == vanilla_parsed['width'] * 2,
           '512 cap selects one uniform 2x native terrain scale')
        ok(max(parsed['width'], parsed['height']) <= 512,
           'converted Gaia payload obeys the world-only 512px cap')
        ok(payload[:4] != b'DDS ', 'DDS bytes never enter world_us.lgp')

        # The GUI writes this exact environment value before build_plan runs.
        # Prove each exposed nonzero choice controls the converter rather than
        # merely changing a label in settings.json.
        for selected_cap in (256, 512, 768):
            os.environ[build.WORLD_TEX_CAP_ENV] = str(selected_cap)
            capped_out = build._convert_world_dds(
                {'fld.tex': (src, fake_mod)},
                {'fld.tex': os.path.join(VAN, 'fld.tex')}, logs.append)
            capped = tex.parse(open(capped_out['fld.tex'][0], 'rb').read())
            factor = {256: 1, 512: 2, 768: 3}[selected_cap]
            ok(capped['width'] == vanilla_parsed['width'] * factor,
               'GUI world cap %d selects uniform %dx native scale'
               % (selected_cap, factor))

        os.environ[build.WORLD_TEX_CAP_ENV] = '512'
        sky_in = {
            'wm_kumo.tex': (os.path.join(GAIA, 'wm_kumo_00.dds'), fake_mod),
            'meteo.tex': (os.path.join(GAIA, 'meteo_00.dds'), fake_mod),
        }
        sky_out = build._convert_world_dds(
            sky_in,
            {n: os.path.join(VAN, n) for n in sky_in}, logs.append)
        cloud = tex.parse(open(sky_out['wm_kumo.tex'][0], 'rb').read())
        meteor = tex.parse(open(sky_out['meteo.tex'][0], 'rb').read())
        ok((cloud['width'], cloud['height']) == (512, 128),
           'Gaia clouds retain their authored aspect at the 512 cap')
        ok((meteor['width'], meteor['height']) == (512, 256),
           'Gaia meteor retains its authored aspect at the 512 cap')
        sky_scales = ff7nx_gaia.special_scales()
        ok(sky_scales['wm_kumo'] == (2.0, 2.0)
           and sky_scales['meteo'] == (2.0, 4.0),
           'archive publishes independent cloud/meteor X/Y UV factors')

        os.environ[build.WORLD_TEX_CAP_ENV] = '768'
        sky_768 = build._convert_world_dds(
            sky_in,
            {n: os.path.join(VAN, n) for n in sky_in}, logs.append)
        meteor_768 = tex.parse(open(
            sky_768['meteo.tex'][0], 'rb').read())
        ok((meteor_768['width'], meteor_768['height']) == (768, 384),
           'active 768 cap gives Gaia meteor 768x384 detail')
        ok(ff7nx_gaia.special_scales()['meteo'] == (3.0, 6.0),
           '768 meteor publishes its nonuniform 3x/6x native UV factors')
        os.environ[build.WORLD_TEX_CAP_ENV] = '512'

        multi_out = build._convert_world_dds(
            {'dfx.tex': (os.path.join(GAIA, 'dfx_00.dds'), fake_mod)},
            {'dfx.tex': os.path.join(VAN, 'dfx.tex')}, logs.append)
        ok(not multi_out,
           'multi-palette runtime effect stays native from frame-zero DDS')

        # Sprite/UI content uses renderer-aware conversion. The shadow is the
        # important boundary: Gaia stores the falloff in alpha, while the
        # native renderer expects transparent index zero plus blended greys.
        runtime_names = {
            'map.tex', 'midlmap.tex', 'midlmap2.tex', 'radar.tex',
            'shadow.tex', 'snow4.tex', 'snow5.tex',
        }
        runtime_out = build._convert_world_dds(
            {n: (os.path.join(GAIA, n[:-4] + '_00.dds'), fake_mod)
             for n in runtime_names},
            {n: os.path.join(VAN, n) for n in runtime_names}, logs.append)
        ok(set(runtime_out) == runtime_names,
           'Gaia map UI and soft shadow are converted, not held vanilla')
        shadow = tex.parse(open(runtime_out['shadow.tex'][0], 'rb').read())
        ok((shadow['width'], shadow['height']) == (256, 256),
           'Gaia shadow keeps its available 256px detail')
        ok(0 in shadow['pixels'] and len(set(shadow['pixels'])) > 2,
           'Gaia shadow has transparent pixels and a soft indexed falloff')
        shadow_pal = shadow['palette']
        visible_greys = {
            shadow_pal[i * 4] for i in set(shadow['pixels']) if i
        }
        ok(min(visible_greys) >= 41 and max(visible_greys) <= 115,
           'Gaia alpha was mapped to the native shadow grey range')

        # THE HALO. The native shadow is colour-KEYED, so there is no partial
        # coverage: every pixel that is not index zero is drawn solid. Keying
        # at alpha >= 1 therefore turned Gaia's long faint tail into a ring of
        # opaque light grey -- 21,575 pixels against vanilla's 16,352-pixel
        # equivalent, +32% area, and very visible over dark ground.
        #
        # Pin the coverage to vanilla's own, which is the criterion
        # build._shadow_alpha_cutoff solves for.
        native = tex.parse(open(os.path.join(VAN, 'shadow.tex'), 'rb').read())
        native_frac = (sum(1 for x in native['pixels'] if x)
                       / float(native['width'] * native['height']))
        drawn = sum(1 for i in shadow['pixels'] if i)
        frac = drawn / float(shadow['width'] * shadow['height'])
        ok(abs(frac - native_frac) < 0.02,
           'Gaia shadow covers vanilla\'s area (%.1f%% vs %.1f%%) -- no halo'
           % (100 * frac, 100 * native_frac))
        ok(frac < 0.30,
           'the faint alpha tail is keyed out rather than drawn solid')
        snow = tex.parse(open(runtime_out['snow4.tex'][0], 'rb').read())
        ok((snow['width'], snow['height']) == (512, 512),
           'Gaia snow overlay obeys the world cap with normalized UVs')

        # Exact six-site renderer correction: each hook must be emitted only
        # for a Gaia scale, with a fully indexed/capped archive alongside it.
        main_path = os.path.join(HERE, 'dump', 'exefs', 'main')
        if os.path.isfile(main_path):
            old_scale = os.environ.get(ff7nx_gaia.SCALE_ENV)
            try:
                os.environ[ff7nx_gaia.SCALE_ENV] = '2'
                import nxmap
                words = ff7nx_gaia.patch_words(nxmap.Main(main_path))
                ok(all(va in words for va, _, _ in ff7nx_gaia.SITES),
                   'all six terrain U/V normalisations receive Gaia hooks')
                ok(ff7nx_gaia.SPECIAL_HOOK in words,
                   'sprite inverse-size correction is paired with archive')
                os.environ[ff7nx_gaia.SCALE_ENV] = '1'
                os.environ.pop(ff7nx_gaia.SPECIAL_ENV, None)
                ok(not ff7nx_gaia.patch_words(nxmap.Main(main_path)),
                   'fully native plan emits no module correction')
            finally:
                if old_scale is None:
                    os.environ.pop(ff7nx_gaia.SCALE_ENV, None)
                else:
                    os.environ[ff7nx_gaia.SCALE_ENV] = old_scale

        # The fast GUI mode must preserve only an exact flevel produced by
        # this builder and restore the cached field size needed by the later
        # exefs/main buffer pass.
        reuse_root = os.path.join(temp, 'reuse-sdout')
        flevel = os.path.join(
            reuse_root, 'atmosphere', 'contents', build.TITLE_ID, build.ROMFS,
            build.ARCHIVES['flevel.lgp'])
        os.makedirs(os.path.dirname(flevel), exist_ok=True)
        with open(flevel, 'wb') as f:
            f.write(b'known flevel')
        cache = os.path.join(temp, 'archive-fp')
        os.makedirs(cache)
        old_fp_cache = build.ARCHIVE_FP_CACHE
        build.ARCHIVE_FP_CACHE = cache
        stat = os.stat(flevel)
        with open(os.path.join(cache, 'flevel.lgp.fp'), 'w') as f:
            f.write('fingerprint\n%d\n%d\n123456\n'
                    % (stat.st_size, stat.st_mtime_ns))
        try:
            reused = build._reuse_existing_flevel(reuse_root, 'fingerprint',
                                                  logs.append)
            ok(reused == flevel and build.FIELD_BG_MAX_RAW == 123456,
               'fast mode verifies flevel and restores its buffer metadata')
            before_mtime = os.stat(flevel).st_mtime_ns
            os.environ[build.REUSE_FLEVEL_ENV] = '1'
            post = ff7nx_field169._patch_flevel(
                reuse_root, None, logs.append, [flevel])
            ok(post == flevel and os.stat(flevel).st_mtime_ns == before_mtime,
               '16:9 post-pass leaves reused flevel byte-for-byte untouched')
            os.environ.pop(build.REUSE_FLEVEL_ENV, None)
            time.sleep(0.001)
            with open(flevel, 'ab') as f:
                f.write(b'changed')
            try:
                build._reuse_existing_flevel(reuse_root, 'fingerprint',
                                             logs.append)
            except RuntimeError:
                refused = True
            else:
                refused = False
            ok(refused, 'fast mode refuses an unverified changed flevel')
        finally:
            build.ARCHIVE_FP_CACHE = old_fp_cache
    finally:
        build.WORLD_DDS_CACHE = old_cache
        if old_cap is None:
            os.environ.pop(build.WORLD_TEX_CAP_ENV, None)
        else:
            os.environ[build.WORLD_TEX_CAP_ENV] = old_cap
        if old_reuse is None:
            os.environ.pop(build.REUSE_FLEVEL_ENV, None)
        else:
            os.environ[build.REUSE_FLEVEL_ENV] = old_reuse
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == '__main__':
    main()
