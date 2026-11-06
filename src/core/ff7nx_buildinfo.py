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
went into the build. Mods that are installed but were off are named at the
end. Plain ASCII text, so any editor on any system shows it the same.

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
WIDTH = 72

_MONTHS = ('January', 'February', 'March', 'April', 'May', 'June', 'July',
           'August', 'September', 'October', 'November', 'December')
_DAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday',
         'Saturday', 'Sunday')


def _v(text):
    text = (text or '').strip()
    return text if not text or text[:1] in 'vV' else 'v' + text


def _when(t):
    lt = time.localtime(t)
    hour = lt.tm_hour % 12 or 12
    return '%s, %s %d, %d at %d:%02d %s' % (
        _DAYS[lt.tm_wday], _MONTHS[lt.tm_mon - 1], lt.tm_mday, lt.tm_year,
        hour, lt.tm_min, 'AM' if lt.tm_hour < 12 else 'PM')


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


def _dots(name, value, indent=4):
    name = _ascii(name).strip() or '?'
    value = _ascii(value).strip()
    room = WIDTH - indent - len(value) - 2
    if len(name) > room - 2:
        name = name[:max(8, room - 5)].rstrip() + '...'
    return ' ' * indent + name + ' ' + '.' * max(2, room - len(name)) + \
        ' ' + value


def render(version, mods, settings_by_mod, installed=(), when=None):
    """The text of build.txt.

    mods             the enabled mods, highest priority first (build.Mod)
    settings_by_mod  {mod.filename: {option id: value}} as built
    installed        every installed mod, for the "not enabled" list"""
    when = time.time() if when is None else when
    rule = '=' * WIDTH
    out = [rule, '7th Heaven NX - Build'.center(WIDTH).rstrip(), rule, '',
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
            gap = WIDTH - len(head) - len(ver)
            head = head + ' ' * max(2, gap) + ver
        out.append('-' * WIDTH)
        out.append(head)
        out.append('-' * WIDTH)
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
            lines.append(_dots(opt.name or opt.id, _label(opt, value)))
        out.extend(lines or ['    (no options)'])
        out.append('')
    off = sorted((_ascii(m.display_name) for m in installed
                  if m not in mods), key=str.casefold)
    if off:
        out.append('-' * WIDTH)
        out.append('Installed but not enabled:')
        out.append('-' * WIDTH)
        out.extend('    ' + name for name in off)
        out.append('')
    out.append(rule)
    return '\n'.join(out) + '\n'


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
