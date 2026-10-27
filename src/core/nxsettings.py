"""nxsettings -- BUILD 756: which settings a user may change, and the tested
values every other setting starts at.

USER_KEYS are the settings the Settings dialog always shows. Every other
`__global__` setting is a developer setting: tuning knobs, memory budgets and
A/B switches, where a wrong value breaks the build or the game rather than
changing a preference. LOCKED holds the values the project is tested and
shipped with.

BUILD 804: the build always uses what settings.json holds. Developer Settings
(Settings > GUI, remembered) only shows or hides the developer rows, and a
note beside the toggle says whether they are at the LOCKED values
(modified_keys). Before 804, turning it off laid LOCKED over settings.json,
so a developer value silently stopped applying the moment its row was
hidden. LOCKED now only fills a key settings.json does not have yet. Show the
rows for one run with:

    SEVENTH_NX_DEV=1 python3 7th_heaven_nx.py

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os

DEV_ENV = 'SEVENTH_NX_DEV'

# Shown in the Settings dialog in both modes.
USER_KEYS = frozenset((
    'widescreen',
    'model_shadows',
    'scaler',
    'fxaa',
    'video_shader',
    'movie_quality',
    'analog_360',
    'no_autorun',
    'no_cheats',
    'voice_normalize',
    'dock_resolution',              # BUILD 762: output resolution
    'title_screen',                 # BUILD 763b: title background
    'gui_theme',                    # window colours (nxthemes); not a build input
    'install_selection',            # BUILD 814: Install Mods' check boxes; not a build input
    'developer_settings',           # Settings > GUI toggle: same as SEVENTH_NX_DEV
))

# The shipped configuration: the developer settings' defaults, and what
# Default/Modified beside the Developer Settings toggle compares against.
LOCKED = {
    # frame rate
    'fps_60': True,
    'movie_30fps': True,
    'limiter_fps': 66,
    # rendering and memory
    'field_buffer': 6,              # BUILD 788: 2560x1440, 2 px per HD texel,
                                    # reduced to the 1080p screen (was 4.5)
    'gfx_pool_mb': 256,
    'field_frame': True,
    'texture_cache': 'small',
    # texture caps
    'field_tex_cap': 512,
    'world_tex_cap': 768,
    'battle_bg_tex_cap': 768,
    'spell_tex_cap': 768,
    'minigame_tex_cap': 768,
    'esui_texscale': 2,             # BUILD 760: Enhanced Stock UI menu art
    'spell_fx_cap': 768,
    # field backgrounds
    'field_bg_page_px': 768,
    'field_bg_ladder_v2': True,
    'field_bg_budget_mb': 0.0,
    'field_bg_truecolor_pages': 3,
    'field_bg_max_pages': 20,
    'field_bg_replace_only': 2,
    'field_bg_d1_px': 256,
    'field_bg_d1_art': True,
    'margin_art': 2,
    'field_bg_window_cap': True,
    'field_bg_fx_split': True,
    'field_bg_compact_frame_safe': True,
    'field_bg_clamp_palettes': False,
    'field_bg_preserve_cell_coords': False,
    'field_bg_multipal': False,
    'field_bg_fx_base_fields': '',
    # movies
    'movie_fit': 'native',
    'movie_colour': 'bt709',
    # background scaler
    'hd_black_point': 11,           # BUILD 805: 255ths; BUILD 822: 11 (was 12, the HD shaders' own)
}

assert not USER_KEYS & set(LOCKED), 'a key cannot be both user and locked'


# The GUI's "Developer Settings" toggle (saved as __global__.developer_
# settings). The developer rows are shown when either it or SEVENTH_NX_DEV
# says so.
_override = [False]


def env_dev_mode():
    """True when launched with SEVENTH_NX_DEV=1 (or true/yes/on)."""
    return os.environ.get(DEV_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def set_dev_override(on):
    _override[0] = bool(on)


def dev_mode():
    """Developer rows shown: SEVENTH_NX_DEV, or the saved Developer Settings
    toggle (set_dev_override). Changes what the dialog shows, never what a
    build uses."""
    return env_dev_mode() or _override[0]


def effective_global(saved_global):
    """The `__global__` settings a build actually uses: the saved values,
    with LOCKED filling any developer setting that is not saved yet."""
    out = dict(LOCKED)
    out.update(saved_global or {})
    return out


def modified_keys(global_):
    """The developer settings in `global_` that differ from LOCKED, sorted.
    Empty means Developer Settings are at their defaults."""
    return sorted(k for k, v in LOCKED.items()
                  if k in global_ and global_[k] != v)
