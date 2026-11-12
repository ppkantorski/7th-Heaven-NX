"""BUILD 914: other languages' field archives get the built backgrounds.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)

import lgp  # noqa: E402
import ff7nx_langfields as LF  # noqa: E402


def field(tag, **over):
    secs = [(b'%s-%d' % (tag, i)) * 4 for i in range(9)]
    secs[7] = bytes(range(0x40)) + tag
    for k, v in over.items():
        secs[int(k[1:])] = v
    return secs


def raw(secs, trailer=b''):
    return lgp.join_sections(secs) + trailer


def test_background_taken_text_kept():
    v = field(b'V')
    l_ = list(v)
    l_[0] = b'spanish script and text'
    b = list(v)
    b[0], b[2] = b'echo-s english script', b'idle animation models'
    b[3], b[8] = b'HD palettes', b'HD background'
    b[7] = b'\xAA' * 0x38 + v[7][0x38:] + b'gateway change'
    out, took = LF.merge_field(raw(v), raw(l_), raw(b, b'CMFS-trailer'))
    s = lgp.split_sections(out)
    assert took
    assert s[0] == b'spanish script and text'      # dialogue stays Spanish
    assert s[2] == v[2]                             # models: the language's
    assert s[3] == b'HD palettes' and s[8] == b'HD background'
    assert s[7] == b'\xAA' * 0x38 + v[7][0x38:]     # header only, no gateway
    assert out.endswith(b'CMFS-trailer')


def test_painted_text_background_kept():
    v = field(b'V')
    l_ = list(v)
    l_[8] = b'background with Spanish sign'
    b = list(v)
    b[3], b[8] = b'HD palettes', b'HD background'
    out, took = LF.merge_field(raw(v), raw(l_), raw(b, b'CMFS'))
    s = lgp.split_sections(out)
    assert not took
    assert s[8] == b'background with Spanish sign' and s[3] == v[3]
    assert out.endswith(b'CMFS')                     # footsteps still come
    # no trailer and nothing taken: the language's payload is reused
    assert LF.merge_field(raw(v), raw(l_), raw(b)) == (None, False)


def test_selection_env(monkeypatch):
    monkeypatch.setenv(LF.ENV, 'es, de,xx,es')
    assert LF.selected() == ['es', 'de']
    monkeypatch.setenv(LF.ENV, '')
    assert LF.selected() == []
