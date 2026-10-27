"""
ff7nx_titleart.py -- the title-screen background a mod ships, resolved the
way 7th Heaven resolves it. BUILD 763.

WHAT WAS WRONG
==============
FFNx replaces the title background with `menu/buster_00.dds` from whichever
active mod folder 7th Heaven's virtual file system returns first. Only
Nitpick's copy reached the Switch (ff7nx_nitpick, BUILD 641); Enhanced Stock
UI skips its title sheet on purpose (ff7nx_esui.TITLE_SHEETS: "the title
screen is its own screen") and nothing else read the file. So Echo-S's
backdrop, ESUI's "ESUI Start Screen" option and ESUI's Echo-S variant all
built the stock 256x256 buster.tex.

WHAT THIS DOES
==============
choose() walks every enabled mod in load order (later wins, as build_plan's
`mods`) and each mod's active folders in 7th Heaven's precedence
(iro.active_folders: Conditionals that are known live, then ModFolders) and
takes the last `<folder>/menu/buster_00.dds` -- the file 7th Heaven would
serve. With the shipped profile that is decided by the mods' own options:

  Enhanced Stock UI  ECHOS\\Texture  <Conditional> enhanced = 1, gated on
                     Byte:0x719C60 == 233 (Echo-S's exe byte): ESUI's art
                     with the Echo-S backdrop and both logos
  Enhanced Stock UI  Start          ESUI Start Screen = ESUI: the HD Buster
                     Sword (used when the Echo-S variant is not live)
  Echo-S             Echo-S         the Echo-S backdrop (when ESUI does not
                     supply one)
  Nitpick            title_screen_*  left to ff7nx_nitpick when it wins

The picture is converted exactly as Nitpick's is (one 256-colour palette,
SCALE x the native 256x256, marked with its logical scale for the UV hook),
and drawn full-screen with UV 0..1 as FFNx does (ff7nx_nitpick.TITLE_QUAD).

TEXT POSITION
=============
Stock. Neither Echo-S nor ESUI moves NEW GAME / Continue?: Echo-S ships no
title HEXT, and ESUI's Start\\hext\\all.txt has its two lines (0x9261C0 x,
0x9261C2 y) inside a `{` comment, so 7th Heaven applies nothing either (its
preview, 7h\\startena.png, shows the stock centred menu). Only Nitpick's
Midgar title moves them, and that stays with ff7nx_nitpick.

THE "BUSTER SWORD" CHOICE (BUILD 763b, the default)
==================================================
The user's pick: ESUI's HD Buster Sword (Start\\Menu\\buster_00.DDS,
1427x934) without its bottom-right logo, with the sword where the vanilla
title has it. Whatever the mods' options say.

  vanilla  buster.tex texels 0..150 x 0..150 drawn in x86 0x7224D7's quad
           (185,180)-(485,501) of the 640x480 screen (ff7nx_nitpick)
  ESUI     the same painting, redrawn; stretched to the full screen as FFNx
           does it sits up and to the right and slightly smaller

So the ESUI picture, stretched full-screen, is scaled by (SWORD_SX, SWORD_SY)
and moved by (SWORD_TX, SWORD_TY) game units. Those four numbers were fitted
by registration against the vanilla title rendered at 2 px a unit
(normalised correlation 0.995; coarse grid then 0.004 / 0.5-unit steps).
The logo (source x >= 80 %, y >= 70 %, nothing else of the art is there) is
painted black first. A different ESUI picture (any other size) is refused and
the build falls back to the mods' choice.

BUILD 767: THE HAND-REFINED ART
==============================
`sword` now shows the project's own picture, assets/title/
buster_sword_title.png: the user's hand-refined repaint of the composite
above (same 4:3 framing, the sword where the vanilla title draws it; 1448 x
1086 as delivered, resized to COMPOSE_W x COMPOSE_H here). It no longer needs
Enhanced Stock UI. Without the file, `sword` falls back to the ESUI
composite (763b), and without ESUI to the mods' choice.

SEVENTH_NX_TITLE_SCREEN = sword (default) | mods | stock.
SEVENTH_NX_TITLEART=0 is the same as `stock` (Nitpick's still applies).

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import hashlib
import os

ENV = 'SEVENTH_NX_TITLEART'
MODE_ENV = 'SEVENTH_NX_TITLE_SCREEN'
MODES = ('sword', 'mods', 'stock')
DEFAULT_MODE = 'sword'

import nxpaths  # noqa: E402  (BUILD 767: the project's own art)
SWORD_ART = os.path.join(nxpaths.APP, 'assets', 'title', 'buster_sword_title.png')
ESUI_SWORD = 'start/menu/buster_00.dds'
ESUI_SWORD_SIZE = (1427, 934)
SWORD_SX, SWORD_SY = 1.116, 1.04      # x/y scale after the full-screen stretch
SWORD_TX, SWORD_TY = -67.5, 60.0      # game units (640x480)
LOGO_BOX = (0.80, 0.70)               # source fraction: x >= .80 and y >= .70
COMPOSE_W, COMPOSE_H = 1440, 1080     # the 4:3 title area at 1080 lines
SCALE = 4                       # 1024x1024 for the 1440x1080 4:3 title area
ARCHIVE = 'menu_us.lgp'
TEX = 'buster.tex'
NAMES = ('menu/buster_00.dds',)
VERSION = b'titleart-3'

CURRENT = None


def enabled():
    return os.environ.get(ENV, '1').strip().lower() not in ('0', 'off', 'no',
                                                            'false')


def mode():
    raw = os.environ.get(MODE_ENV, '').strip().lower()
    if not enabled():
        return 'stock'
    return raw if raw in MODES else DEFAULT_MODE


def _esui_sword(mods):
    """{'mod', 'entry', 'folder', 'compose'} for ESUI's sword, or None."""
    import iro
    import ff7nx_esui
    for mod in reversed(list(mods)):
        if not ff7nx_esui.handles(mod):
            continue
        for e in iro.list_entries(mod.path):
            e = e if isinstance(e, str) else e[0]
            if _norm(e) == ESUI_SWORD:
                return {'mod': mod, 'entry': e, 'folder': 'Start',
                        'nitpick': False, 'compose': 'vanilla_layout'}
    return None


def compose_vanilla_layout(dds):
    """ESUI's sword art -> a 4:3 RGBA picture (numpy, COMPOSE_W x COMPOSE_H)
    with the logo removed and the sword where the vanilla title draws it."""
    import numpy as np
    from PIL import Image
    import dds_decode
    rgba, w, h = dds_decode.decode_dds(dds)
    if (w, h) != ESUI_SWORD_SIZE:
        raise ValueError('ESUI sword art is %dx%d, fitted for %dx%d'
                         % ((w, h) + ESUI_SWORD_SIZE))
    src = np.frombuffer(rgba, np.uint8).reshape(h, w, 4).copy()
    src[int(h * LOGO_BOX[1]):, int(w * LOGO_BOX[0]):] = 0
    src[..., 3] = 255
    k = COMPOSE_W / 640.0
    a = 1.0 / (SWORD_SX * (640.0 / w) * k)
    e = 1.0 / (SWORD_SY * (480.0 / h) * k)
    out = Image.fromarray(src).transform(
        (COMPOSE_W, COMPOSE_H), Image.AFFINE,
        (a, 0, -SWORD_TX * k * a, 0, e, -SWORD_TY * k * e),
        resample=Image.BICUBIC, fillcolor=(0, 0, 0, 255))
    return np.ascontiguousarray(np.asarray(out.convert('RGBA'), np.uint8))


class _ArtSource:
    """Stands in for a mod where the build expects one (archive_files'
    (src, mod) pairs, logs): the art is the project's, not a mod's."""
    manifest = None

    def __init__(self, path):
        self.path = path
        self.filename = os.path.basename(path)
        self.display_name = 'title art (project)'


def sword_art_picture(path=None):
    """BUILD 767: the hand-refined title art -> RGBA (numpy, COMPOSE_W x
    COMPOSE_H). It is drawn in the 4:3 title area like the 763b composite;
    any 4:3 size is accepted (resized, LANCZOS), anything else refused."""
    import numpy as np
    from PIL import Image
    im = Image.open(path or SWORD_ART).convert('RGB')
    w, h = im.size
    if abs(w * 3 - h * 4) > max(w, h) * 0.01:
        raise ValueError('title art is %dx%d, not 4:3' % (w, h))
    if (w, h) != (COMPOSE_W, COMPOSE_H):
        im = im.resize((COMPOSE_W, COMPOSE_H), Image.LANCZOS)
    return np.ascontiguousarray(np.asarray(im.convert('RGBA'), np.uint8))


def _norm(p):
    return p.replace('\\', '/').lower()


def choose(mods, settings_by_mod, read=None, log=lambda *_: None):
    """{'mod', 'entry', 'folder'} of the art 7th Heaven would serve, or None."""
    import iro
    import ff7nx_nitpick
    best = None
    for mod in mods:
        man = getattr(mod, 'manifest', None)
        if man is None:
            continue
        try:
            ents = [e if isinstance(e, str) else e[0]
                    for e in iro.list_entries(mod.path)]
            folders = iro.active_folders(
                man, settings_by_mod.get(mod.filename, {}), read=read)
        except Exception:                                    # noqa: BLE001
            continue
        low = {}
        for e in ents:
            low.setdefault(_norm(e), e)
        for folder in folders:                    # last write wins
            pre = _norm(folder).rstrip('/') + '/'
            for n in NAMES:
                hit = low.get(pre + n)
                if hit is not None:
                    best = {'mod': mod, 'entry': hit, 'folder': folder,
                            'nitpick': ff7nx_nitpick.handles(mod)}
    return best


def plan(mods, settings_by_mod, read=None, log=lambda *_: None):
    """Set CURRENT for this build; returns it."""
    global CURRENT
    CURRENT = None
    m = mode()
    if m == 'stock':
        log('Title screen: original (Nitpick\'s, if its option is on)')
        return None
    pick = None
    if m == 'sword' and os.path.isfile(SWORD_ART):
        CURRENT = {'mod': _ArtSource(SWORD_ART),
                   'entry': 'assets/title/buster_sword_title.png',
                   'folder': 'assets/title', 'nitpick': False,
                   'compose': 'art', 'art': SWORD_ART}
        log('Title screen: the Buster Sword, hand-refined art '
            '(assets/title/buster_sword_title.png, BUILD 767)')
        return CURRENT
    if m == 'sword':
        pick = _esui_sword(mods)
        if pick is None:
            log('Title screen: Buster Sword needs Enhanced Stock UI enabled; '
                'using the mods\' choice')
        else:
            CURRENT = pick
            log('Title screen: Enhanced Stock UI\'s Buster Sword, vanilla '
                'layout, no logo (BUILD 763b)')
            return CURRENT
    pick = choose(mods, settings_by_mod, read, log)
    if pick is None:
        return None
    if pick['nitpick']:
        log('Title screen: Nitpick\'s %s (ff7nx_nitpick)' % pick['folder'])
        return None
    CURRENT = pick
    log('Title screen: %s -- %s\\menu\\buster_00.dds (BUILD 763)'
        % (pick['mod'].display_name if hasattr(pick['mod'], 'display_name')
           else pick['mod'].filename, pick['folder']))
    return CURRENT


def build_texture(archive_paths, cache_dir, log=lambda *_: None):
    """Path of the built buster.tex, or None."""
    import iro
    import lgp
    import ff7nx_nitpick
    if CURRENT is None:
        return None
    if ARCHIVE not in archive_paths:
        log('  ! title screen: %s is not in this dump' % ARCHIVE)
        return None
    v = lgp.Archive(archive_paths[ARCHIVE]).index.get(TEX)
    if v is None:
        log('  ! title screen: %s has no %s' % (ARCHIVE, TEX))
        return None
    vanilla = v['payload']
    comp = CURRENT.get('compose')
    if comp == 'art':                          # BUILD 767
        with open(CURRENT['art'], 'rb') as f:
            dds = f.read()
    else:
        dds = iro.read_one(CURRENT['mod'].path, CURRENT['entry'])
    key = hashlib.sha256(VERSION + vanilla + dds + repr(
        (SCALE, comp, SWORD_SX, SWORD_SY, SWORD_TX, SWORD_TY, LOGO_BOX,
         COMPOSE_W, COMPOSE_H)).encode()).hexdigest()[:24]
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, 'buster.%s.tex' % key)
    if not os.path.isfile(path):
        try:
            pic = (sword_art_picture(CURRENT['art']) if comp == 'art'
                   else compose_vanilla_layout(dds) if comp else dds)
            data = ff7nx_nitpick.build_texture(vanilla, pic, SCALE)
        except Exception as exc:                             # noqa: BLE001
            log('  ! title screen: %s -- stock kept' % exc)
            return None
        with open(path + '.tmp', 'wb') as f:
            f.write(data)
        os.replace(path + '.tmp', path)
    return path
