"""BUILD 666: facial textures first in flevel.lgp, lazily loaded ones capped.

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
import importlib.util
import os
import struct
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import lgp                                                    # noqa: E402
import tex                                                    # noqa: E402
import ff7nx_eyefast as EF                                    # noqa: E402

VANILLA = os.path.join(ROOT, 'game_data_files', 'field', 'flevel.lgp')


def make_tex(w, h, seed=1):
    hdr = bytearray(tex.HEADER_LEN)
    struct.pack_into('<I', hdr, tex.O_VERSION, 1)
    struct.pack_into('<I', hdr, tex.O_WIDTH, w)
    struct.pack_into('<I', hdr, tex.O_HEIGHT, h)
    struct.pack_into('<I', hdr, tex.O_BYTES_PER_PIXEL, 4)
    struct.pack_into('<I', hdr, tex.O_BITS_PER_PIXEL, 32)
    px = bytearray(w * h * 4)
    for y in range(h):
        for x in range(w):
            o = (y * w + x) * 4
            a = 255 if (x // 8 + y // 8 + seed) % 2 else 0
            px[o:o + 4] = bytes(((x * 7) & 255, (y * 5) & 255, seed & 255, a))
    return bytes(hdr) + bytes(px)


def load_old_lgp():
    import importlib.machinery
    loader = importlib.machinery.SourceFileLoader(
        'lgp_pre666', os.path.join(ROOT, 'tests', 'fixtures', 'lgp.py.pre666'))
    spec = importlib.util.spec_from_loader('lgp_pre666', loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope='module')
def built(tmp_path_factory):
    if not os.path.exists(VANILLA):
        pytest.skip('no vanilla flevel.lgp')
    a = lgp.Archive(VANILLA)
    big = make_tex(512, 512)
    a.add('eye_zzzz_2.tex', big)
    a.add('eye_zzzzr_3.tex', make_tex(512, 512, 3))
    a.add('b_eye2_4.tex', make_tex(256, 256, 4))
    a.add('mouth_zzzz_2.tex', make_tex(32, 32, 5))
    a.index['c_eye2.tex']['payload'] = make_tex(512, 512, 6)   # suffix-less
    stats = EF.cap_entries(a, px=64)
    d = tmp_path_factory.mktemp('ef')
    dest = str(d / 'flevel.lgp')
    a.write(dest)
    return a, stats, dest


def toc(path):
    with open(path, 'rb') as f:
        f.read(lgp.CREATOR_LEN)
        n = struct.unpack('<i', f.read(4))[0]
        out = []
        for _ in range(n):
            e = f.read(lgp.TOC_ENTRY_LEN)
            out.append((e[:20].split(b'\0')[0].decode(),
                        struct.unpack('<I', e[20:24])[0]))
    return out


def test_payloads_and_toc_survive(built):
    a, _stats, dest = built
    b = lgp.Archive(dest)
    assert [e['name'] for e in b.entries] == [e['name'] for e in a.entries]
    assert b.middle == a.middle
    for x, y in zip(a.entries, b.entries):
        assert x['payload'] == y['payload'], x['name']


def test_every_toc_offset_names_its_entry(built):
    _a, _s, dest = built
    data = open(dest, 'rb').read()
    for name, off in toc(dest):
        assert data[off:off + 20].split(b'\0')[0].decode() == name
        size = struct.unpack_from('<I', data, off + 20)[0]
        assert off + 24 + size <= len(data) - len(lgp.TERMINATOR)
    assert data.endswith(lgp.TERMINATOR)


def test_loose_first_blink_ahead(built):
    _a, _s, dest = built
    t = dict(toc(dest))
    loose = [o for n, o in t.items() if '.' in n]
    fields = [o for n, o in t.items() if '.' not in n]
    assert max(loose) < min(fields)
    assert t['eye_zzzz_2.tex'] < t['eye_zzzzr_3.tex']
    assert t['mouth_zzzz_2.tex'] < t['b_eye2_4.tex']
    assert t['c_eye2.tex'] < t['blackbgb.xone'.lower()] if 'blackbgb.xone' in t else True
    first = min(t.values())
    assert first == lgp.CREATOR_LEN + 4 + len(t) * lgp.TOC_ENTRY_LEN + 3602


def test_caps_only_indexed_facial(built):
    a, stats, _d = built
    p = lambda n: tex.parse(a.index[n]['payload'])            # noqa: E731
    assert (p('eye_zzzz_2.tex')['width'], p('eye_zzzz_2.tex')['height']) == (64, 64)
    assert p('eye_zzzzr_3.tex')['width'] == 64
    assert p('b_eye2_4.tex')['width'] == 64
    assert p('mouth_zzzz_2.tex')['width'] == 32               # already small
    assert p('c_eye2.tex')['width'] == 512                    # open eye: kept
    assert stats['capped'] == 3


def test_premultiplied_average():
    w = h = 128
    data = bytearray(make_tex(w, h))
    # left half opaque red, right half transparent GREEN: no green may bleed
    for y in range(h):
        for x in range(w):
            o = tex.HEADER_LEN + (y * w + x) * 4
            data[o:o + 4] = bytes((0, 0, 255, 255)) if x < 64 else bytes((0, 255, 0, 0))
    out, dims = EF.cap_texture(bytes(data), 32)
    assert dims == (128, 128, 32, 32)
    px = out[tex.HEADER_LEN:]
    for x in range(32):
        b, g, r, al = px[x * 4:x * 4 + 4]
        assert g == 0
        assert (al == 255 and r == 255) if x < 16 else al == 0


def test_other_archives_written_exactly_as_before(tmp_path):
    if not os.path.exists(VANILLA):
        pytest.skip('no vanilla flevel.lgp')
    old = load_old_lgp()
    a = lgp.Archive(VANILLA)
    a.add('eye_zzzz_2.tex', make_tex(8, 8))
    o = old.Archive(VANILLA)
    o.add('eye_zzzz_2.tex', make_tex(8, 8))
    a.write(str(tmp_path / 'char.lgp'))
    o.write(str(tmp_path / 'char_old.lgp'))
    assert open(tmp_path / 'char.lgp', 'rb').read() == \
        open(tmp_path / 'char_old.lgp', 'rb').read()


def test_env_off_and_temporaries(tmp_path, monkeypatch):
    if not os.path.exists(VANILLA):
        pytest.skip('no vanilla flevel.lgp')
    a = lgp.Archive(VANILLA)
    assert lgp.default_data_order(a, 'x/flevel.lgp.fieldsteps-tmp') is not None
    assert lgp.default_data_order(a, 'x/flevel.lgp') is not None
    assert lgp.default_data_order(a, 'x/char.lgp') is None
    monkeypatch.setenv(lgp.LOOSE_FIRST_ENV, '0')
    assert lgp.default_data_order(a, 'x/flevel.lgp') is None
    monkeypatch.setenv(EF.PX_ENV, '0')
    assert EF.cap_px() == 0


def test_rank():
    assert lgp.loose_rank('eye_ajif_2.tex') == 0
    assert lgp.loose_rank('eye_ajifr_2.tex') == 0
    assert lgp.loose_rank('b_eye2r.tex') == 0
    assert lgp.loose_rank('chi_red5_2.tex') == 0
    assert lgp.loose_rank('eye_ajif_5.tex') == 1
    assert lgp.loose_rank('y_mouth_3.tex') == 1
    assert lgp.loose_rank('hand_1.tex') == 2
    assert lgp.loose_rank('flevel.siz') == 2
    assert not EF.is_lazy_facial('c_eye2.tex')
    assert EF.is_lazy_facial('c_eye2_3.tex')
    assert not EF.is_lazy_facial('sibuki1.tex')
