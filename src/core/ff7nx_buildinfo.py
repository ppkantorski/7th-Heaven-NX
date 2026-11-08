#!/usr/bin/env python3
"""ff7nx_buildinfo.py -- build.txt, what a build was made with.
BUILD 894.

Written at the end of every build that produced output, in the title's
own folder, sdout/atmosphere/contents/0100A5B00BDC6000/ -- beside exefs/
and romfs/, where Atmosphere ignores it, and removed with the rest when
that folder is deleted. It says: the
7th Heaven NX version, when the build ran, and every enabled mod in
priority order with its version and the options it was built with -- the
EFFECTIVE options, after 7th Heaven compatibility rules, i.e. exactly what
went into the build. Mods that are installed but off are NOT listed (11-07:
they contribute nothing to the build, so naming them only adds noise). The
time carries the local time zone ("8:50 AM PST"). Plain ASCII text, so any
editor on any system shows it the same.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os
import time

FILE_NAME = 'build.txt'
TITLE_ID = '0100A5B00BDC6000'


def path_for(sdout):
    """sdout/atmosphere/contents/<title>/build.txt"""
    return os.path.join(sdout, 'atmosphere', 'contents', TITLE_ID, FILE_NAME)
WIDTH = 72        # narrowest the file gets
MAX_WIDTH = 120   # 11-07: the file widens so every option line fits in full
                  # ("Upscaled + Extended Backgrounds (Recommended)"), up to this

_MONTHS = ('January', 'February', 'March', 'April', 'May', 'June', 'July',
           'August', 'September', 'October', 'November', 'December')
_DAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday',
         'Saturday', 'Sunday')


def _v(text):
    text = (text or '').strip()
    return text if not text or text[:1] in 'vV' else 'v' + text


def _zone(lt):
    """Short name of the local time zone in effect at `lt`: 'PST', 'CET'...

    macOS / Linux give the abbreviation directly. Windows gives the long
    name ('Pacific Standard Time'), which is shortened to its initials. If
    neither yields a plain short word, the UTC offset is used ('UTC-08:00')."""
    name = (getattr(lt, 'tm_zone', None) or '').strip()
    if name and not (name.isalpha() and name.isupper() and len(name) <= 5):
        words = [w for w in name.replace('(', ' ').replace(')', ' ').split()
                 if w[:1].isalpha()]
        initials = ''.join(w[0] for w in words).upper()
        name = initials if 2 <= len(initials) <= 5 and len(words) > 1 else ''
    if name and name.isalpha():
        return name
    off = getattr(lt, 'tm_gmtoff', None)
    if off is None:
        off = -(time.altzone if lt.tm_isdst > 0 and time.daylight
                else time.timezone)
    sign = '+' if off >= 0 else '-'
    off = abs(int(off)) // 60
    return 'UTC%s%02d:%02d' % (sign, off // 60, off % 60)


def _when(t):
    lt = time.localtime(t)
    hour = lt.tm_hour % 12 or 12
    return '%s, %s %d, %d at %d:%02d %s %s' % (
        _DAYS[lt.tm_wday], _MONTHS[lt.tm_mon - 1], lt.tm_mday, lt.tm_year,
        hour, lt.tm_min, 'AM' if lt.tm_hour < 12 else 'PM', _zone(lt))


def _ascii(text):
    text = str(text).replace('—', '-').replace('–', '-') \
        .replace('’', "'").replace('‘', "'") \
        .replace('“', '"').replace('”', '"').replace('…', '...')
    return text.encode('ascii', 'replace').decode('ascii')


def _label(opt, value):
    for v, label in opt.values:
        if v == value:
            word = (label or '').strip()
            if not word:
                return 'On' if value else 'Off'
            return {'on': 'On', 'off': 'Off'}.get(word.lower(), word)
    return str(value)


def _is_heading(opt):
    bare = opt.name.strip().strip('=').strip()
    return len(opt.values) <= 1 and (opt.name.strip().startswith('=')
                                     or not bare)


def _dots(name, value, indent=4, width=WIDTH):
    name = _ascii(name).strip() or '?'
    value = _ascii(value).strip()
    room = width - indent - len(value) - 2
    if len(name) > room - 2:
        name = name[:max(8, room - 5)].rstrip() + '...'
    return ' ' * indent + name + ' ' + '.' * max(2, room - len(name)) + \
        ' ' + value


def _render(version, mods, settings_by_mod, when, W, need):
    """build.txt laid out at width W; appends to `need` the width each
    option / title line wants to be shown in full."""
    rule = '=' * W
    out = [rule, '7th Heaven NX - Build'.center(W).rstrip(), rule, '',
           'Built with 7th Heaven NX %s.' % _v(version),
           'Built on %s.' % _when(when), '']
    n = len(mods)
    out.append('%d mod%s enabled, highest priority first.'
               % (n, '' if n == 1 else 's'))
    out.append('')
    num_w = len(str(max(n, 1)))
    for i, mod in enumerate(mods, 1):
        man = getattr(mod, 'manifest', None)
        name = _ascii(mod.display_name)
        ver = _v(getattr(man, 'version', '') if man else '')
        head = '%*d. %s' % (num_w, i, name)
        if ver:
            need.append(len(head) + 2 + len(ver))
            gap = W - len(head) - len(ver)
            head = head + ' ' * max(2, gap) + ver
        out.append('-' * W)
        out.append(head)
        out.append('-' * W)
        opts = list(getattr(man, 'options', ()) if man else ())
        chosen = settings_by_mod.get(mod.filename, {}) or {}
        lines, pending = [], None
        for opt in opts:
            if _is_heading(opt):
                pending = opt.name.strip().strip('=').strip() or opt.id
                continue
            if not opt.values:
                continue
            value = chosen.get(opt.id)
            if value is None:
                low = opt.id.casefold()
                value = next((v for k, v in chosen.items()
                              if str(k).casefold() == low), opt.default)
            if pending:
                if lines:
                    lines.append('')
                lines.append('    [%s]' % _ascii(pending))
                pending = None
            nm, lab = _ascii(opt.name or opt.id).strip() or '?', _ascii(_label(opt, value)).strip()
            need.append(4 + len(nm) + 4 + len(lab))
            lines.append(_dots(opt.name or opt.id, _label(opt, value), width=W))
        out.extend(lines or ['    (no options)'])
        out.append('')
    out.append(rule)
    return '\n'.join(out) + '\n'


def render(version, mods, settings_by_mod, installed=(), when=None):
    """The text of build.txt.

    mods             the enabled mods, highest priority first (build.Mod)
    settings_by_mod  {mod.filename: {option id: value}} as built
    installed        accepted for compatibility; installed-but-disabled mods
                     are not listed (they add nothing to the build)

    The width is WIDTH, or wider (up to MAX_WIDTH) when an option's name and
    value need more room to be shown in full."""
    when = time.time() if when is None else when
    need = []
    _render(version, mods, settings_by_mod, when, MAX_WIDTH, need)
    width = max(WIDTH, min(MAX_WIDTH, max(need, default=0)))
    return _render(version, mods, settings_by_mod, when, width, [])


def width_of(text):
    """The width a rendered build.txt was laid out at (its rule length)."""
    return len(text.split('\n', 1)[0])


def write(sdout, version, mods, settings_by_mod, installed=(), when=None):
    """Write build.txt into the title folder (path_for); returns its path."""
    path = path_for(sdout)
    text = render(version, mods, settings_by_mod, installed, when)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='ascii', newline='\n') as f:
        f.write(text)
    os.replace(tmp, path)
    return path
