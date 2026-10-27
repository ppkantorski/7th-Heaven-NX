"""nxthemes -- the window's colour themes.

One palette per theme, chosen in Settings > GUI and saved as
`__global__.gui_theme`. The GUI reads it once at startup, so a change applies
on the next launch (the dialog offers to restart). It only colours this
window; nothing in a build reads it.

Every palette has the same keys:

    BG_APP          window and panel background
    BG_ROW          mod rows, fields, buttons
    BG_ROW_HOVER    row / button under the pointer
    BG_ROW_SEL      selected row
    BORDER          1px edges and separators
    ACCENT          Build button, progress bars, checked boxes, focus
    ACCENT_HOVER    the Build button under the pointer
    ACCENT_SOFT     links, selected mod name, "Modifies" archives
    ON_ACCENT       text drawn on ACCENT / ACCENT_HOVER
    CHECK_TICK      the tick inside a checked box
    TEXT_PRIMARY    body text; the header icons are tinted to it
    TEXT_SECONDARY  blurbs, column notes
    TEXT_MUTED      disabled and estimated values
    LOG_BG          build log background
    LOG_TEXT        build log text
    GOOD            "game dump found - VALID"
    WARN            "game dump found - INVALID" / "game dump not found"

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

DEFAULT = 'crimson'

# (key, label, palette), in the order the Settings list shows them.
THEMES = (
    # Near-black neutrals with a deep red accent. White on the red rather
    # than dark text: #c8323c is too dark for dark text to read on.
    ('crimson', 'Crimson Night', {
        'BG_APP': '#0c0c0e',
        'BG_ROW': '#17171a',
        'BG_ROW_HOVER': '#1f1f23',
        'BG_ROW_SEL': '#3a161b',
        'BORDER': '#2b2b30',
        'ACCENT': '#c8323c',
        'ACCENT_HOVER': '#dc4650',
        'ACCENT_SOFT': '#f07c84',
        'ON_ACCENT': '#ffffff',
        'CHECK_TICK': '#ffffff',
        'TEXT_PRIMARY': '#ececee',
        'TEXT_SECONDARY': '#9b9ba3',
        'TEXT_MUTED': '#63636b',
        'LOG_BG': '#08080a',
        'LOG_TEXT': '#d2d2d7',
        'GOOD': '#6fcf97',
        'WARN': '#f2a93b',
    }),
    ('abyss', 'Abyss Blue', {
        'BG_APP': '#0b1220',
        'BG_ROW': '#151d30',
        'BG_ROW_HOVER': '#1a2438',
        'BG_ROW_SEL': '#1c3a5e',
        'BORDER': '#232e46',
        'ACCENT': '#4f9dff',
        'ACCENT_HOVER': '#8ec2ff',
        'ACCENT_SOFT': '#8ec2ff',
        'ON_ACCENT': '#08111e',
        'CHECK_TICK': '#ffffff',
        'TEXT_PRIMARY': '#e8ecf4',
        'TEXT_SECONDARY': '#8b93a8',
        'TEXT_MUTED': '#5c6579',
        'LOG_BG': '#070b14',
        'LOG_TEXT': '#c9d2e3',
        'GOOD': '#5fd39a',
        'WARN': '#f5b942',
    }),
    # Black and greys only. The accent is a light grey, so text and ticks on
    # it are dark.
    ('graphite', 'Graphite', {
        'BG_APP': '#0e0e0f',
        'BG_ROW': '#18181a',
        'BG_ROW_HOVER': '#212124',
        'BG_ROW_SEL': '#303034',
        'BORDER': '#2d2d31',
        'ACCENT': '#bdbdc2',
        'ACCENT_HOVER': '#dcdce0',
        'ACCENT_SOFT': '#e2e2e6',
        'ON_ACCENT': '#111113',
        'CHECK_TICK': '#111113',
        'TEXT_PRIMARY': '#ececee',
        'TEXT_SECONDARY': '#9a9aa0',
        'TEXT_MUTED': '#626268',
        'LOG_BG': '#09090a',
        'LOG_TEXT': '#cfcfd3',
        'GOOD': '#8fd4a8',
        'WARN': '#f0b44c',
    }),
    ('emerald', 'Emerald', {
        'BG_APP': '#08120e',
        'BG_ROW': '#0f1d17',
        'BG_ROW_HOVER': '#14261e',
        'BG_ROW_SEL': '#163b2a',
        'BORDER': '#1e3329',
        'ACCENT': '#2fb86c',
        'ACCENT_HOVER': '#5fd492',
        'ACCENT_SOFT': '#86e2ad',
        'ON_ACCENT': '#04140b',
        'CHECK_TICK': '#ffffff',
        'TEXT_PRIMARY': '#e6f0eb',
        'TEXT_SECONDARY': '#8aa197',
        'TEXT_MUTED': '#587066',
        'LOG_BG': '#050c09',
        'LOG_TEXT': '#c6d8ce',
        'GOOD': '#86e2ad',
        'WARN': '#f2b84b',
    }),
)

CHOICES = [(key, label) for key, label, _p in THEMES]
LABELS = dict(CHOICES)
BY_LABEL = {label: key for key, label in CHOICES}
_PALETTES = {key: p for key, _l, p in THEMES}


def palette(key):
    """The palette for `key`; the default theme's for anything unknown."""
    return dict(_PALETTES.get(key) or _PALETTES[DEFAULT])
