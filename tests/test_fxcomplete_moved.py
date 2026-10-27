"""BUILD 656: ff7nx_fxcomplete never converts an FX slot that holds cells
field_bg_compact moved there from another page (snow's grey squares).

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
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import ff7nx_cosmosrestore as CR      # noqa: E402
import ff7nx_fxcomplete as FC         # noqa: E402
import lgp                            # noqa: E402

BUILT = os.environ.get('FXC_BUILT_FLEVEL', os.path.join(
    ROOT, 'sdout', 'atmosphere', 'contents', '0100A5B00BDC6000', 'romfs',
    'ff7', 'workingdir', 'data', 'field', 'flevel.lgp'))
CHUNK = os.path.join(CR.CHUNK_DIR, 'snow.chunk.9')


@pytest.mark.skipif(not (os.path.exists(BUILT) and os.path.exists(CHUNK)),
                    reason='needs a built flevel and the Cosmos chunk cache')
def test_snow_light_slots_are_seen_as_moved():
    a = lgp.Archive(BUILT)
    s9 = lgp.split_sections(a.decompressed(a.index['snow']))[8]
    moved = FC.moved_slots(s9, open(CHUNK, 'rb').read())
    assert moved.get(15) == 16 and moved.get(16) == 17


def test_own_page_cells_are_not_moved():
    a = lgp.Archive(os.path.join(ROOT, 'game_data_files', 'field',
                                 'flevel.lgp'))
    s9 = lgp.split_sections(a.decompressed(a.index['snow']))[8]
    assert FC.moved_slots(s9, s9) == {}


def test_reference_prefers_the_mod_chunk(tmp_path, monkeypatch):
    monkeypatch.setattr(CR, 'CHUNK_DIR', str(tmp_path))
    (tmp_path / 'zz.chunk.9').write_bytes(b'CHUNK')
    assert FC.reference_section9(None, None, 'zz') == b'CHUNK'
