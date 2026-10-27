#!/usr/bin/env python3
"""
Echo-S's script and Cosmos Limit Break's art have to survive the SAME field.

THE BUG THIS EXISTS TO PIN
==========================
`_build_flevel` rebuilds a field three times over: a generic per-file section
splice, then Echo-S's section-1 import, then the explicit `.chunk.<n>` pass.
The last of those used to start from `archive.decompressed(entry)` -- vanilla
-- every time, which was harmless for as long as it was the only writer of any
given field.

Echo-S ended that. It ships a script for 703 fields; Cosmos Limit Break ships
a `.chunk.9` for 683; 682 of those are the same field. Rebuilding from vanilla
in the chunk pass therefore threw away the section 1 the Echo-S pass had just
merged, in all but twenty-one fields -- and neither pass could notice, because
each one individually did exactly what it said it did and logged success. The
symptom on hardware would have been "Echo-S installed fine, nobody speaks".

So the assertion here is not "the merge works" (test_echomusic covers that).
It is the composition: after a real `_build_flevel` over a field that BOTH
mods touch, section 1 must be Echo-S's and section 9 must be Cosmos's, and
section 1 must not be vanilla's -- that last one is what stops this test from
passing vacuously if the Echo-S pass silently stopped running.

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
import re
import struct
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import build                                                   # noqa: E402
import echo_s_flevel as EF                                     # noqa: E402
import lgp                                                     # noqa: E402

ECHO_FIELDS = os.path.join(ROOT, 'cache', 'Echo-s', 'Echo-S', 'flevel.lgp')

STOCK = os.path.join(ROOT, 'dump', 'romfs', 'ff7', 'workingdir', 'data',
                     'field', 'flevel.lgp')
ECHO = os.path.join(ROOT, 'cache', 'Echo-s', 'Echo-S', 'flevel.lgp')
CLB = os.path.join(ROOT, 'cache', 'CosmosLimitBreak')

CHUNK_RE = re.compile(r'^(.+)\.chunk\.(\d+)$', re.I)


def cosmos_chunks():
    found = {}
    for dirpath, _dirs, files in os.walk(CLB):
        for name in files:
            match = CHUNK_RE.match(name)
            if match:
                found.setdefault(match.group(1).lower(), {})[
                    int(match.group(2))] = (os.path.join(dirpath, name), None)
    return found


class Compose(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (os.path.isfile(STOCK) and os.path.isdir(ECHO)
                and os.path.isdir(CLB)):
            raise unittest.SkipTest('dump, Echo-S or Cosmos Limit Break '
                                    'not available')
        cls.chunks = cosmos_chunks()
        stock = lgp.Archive(STOCK)
        cls.fields = [n for n in stock.names()
                      if n in cls.chunks and os.path.isfile(
                          os.path.join(ECHO, n))][:3]
        if not cls.fields:
            raise unittest.SkipTest('no field is touched by both mods')
        cls.vanilla = {n: lgp.split_sections(stock.decompressed(stock.index[n]))
                       for n in cls.fields}

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='echo-compose-')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mini_archive(self):
        """
        A three-entry flevel, so the real `_build_flevel` can run in seconds.

        Cutting the archive down rather than mocking the function is
        deliberate: every background, palette and margin pass still runs, and
        those are exactly the passes that would clobber a composed field.
        """
        archive = lgp.Archive(STOCK)
        keep = set(self.fields)
        archive.entries = [e for e in archive.entries if e['name'] in keep]
        archive.index = {e['name']: e for e in archive.entries}
        path = os.path.join(self.tmp, 'mini.lgp')
        archive.write(path)
        return path

    def test_echo_script_and_cosmos_art_both_survive(self):
        mini = self._mini_archive()
        base = lgp.Archive(mini)
        romfs = os.path.join(self.tmp, 'romfs')
        os.makedirs(romfs)
        echo_fields = {n: (os.path.join(ECHO, n), None) for n in self.fields}
        chunks = {n: self.chunks[n] for n in self.fields}

        build._build_flevel(mini, chunks, {}, romfs, lambda *_a: None,
                            echo_fields=echo_fields)

        out = None
        for dirpath, _dirs, files in os.walk(romfs):
            for name in files:
                if name.endswith('.lgp'):
                    out = os.path.join(dirpath, name)
        self.assertIsNotNone(out, '_build_flevel produced no archive')
        result = lgp.Archive(out)

        for field in self.fields:
            got = lgp.split_sections(result.decompressed(result.index[field]))
            with open(os.path.join(ECHO, field), 'rb') as handle:
                merged, imported = EF.merge_field_payload(
                    base.index[field]['payload'], handle.read(),
                    field_name=field)
            # Section 1 always; section 3 travels with it wherever Echo-S
            # edited the model loader, because an entity binds to a model by
            # index into that list. Never anything else -- section 9 here is
            # Cosmos Limit Break's and has to survive.
            self.assertIn(1, imported, field)
            self.assertFalse(set(imported) - {1, 3}, (field, imported))
            want = lgp.split_sections(merged)

            self.assertEqual(got[0], want[0],
                             '%s: section 1 is not Echo-S\'s script -- a '
                             'later pass rebuilt the field from vanilla'
                             % field)
            # Non-vacuity: Echo-S must actually differ from vanilla here, or
            # the assertion above would pass with the feature switched off.
            self.assertNotEqual(got[0], self.vanilla[field][0],
                                '%s: Echo-S script is identical to vanilla, '
                                'so this field proves nothing' % field)

    def test_cosmos_art_still_reaches_the_field(self):
        """
        The other half. Section 9 must come from Cosmos Limit Break's chunk,
        not from vanilla -- the composition has to be a merge in both
        directions, not Echo-S winning the whole field.

        Section 9 is compared against VANILLA rather than against the chunk
        bytes, because the background/palette/margin passes legitimately
        rewrite Cosmos's section afterwards. What must not happen is that it
        comes back byte-identical to the vanilla art.
        """
        mini = self._mini_archive()
        romfs = os.path.join(self.tmp, 'romfs')
        os.makedirs(romfs)
        echo_fields = {n: (os.path.join(ECHO, n), None) for n in self.fields}
        chunks = {n: self.chunks[n] for n in self.fields}
        build._build_flevel(mini, chunks, {}, romfs, lambda *_a: None,
                            echo_fields=echo_fields)
        out = None
        for dirpath, _dirs, files in os.walk(romfs):
            for name in files:
                if name.endswith('.lgp'):
                    out = os.path.join(dirpath, name)
        result = lgp.Archive(out)

        checked = 0
        for field in self.fields:
            if 9 not in chunks[field]:
                continue
            with open(chunks[field][9][0], 'rb') as handle:
                cosmos = handle.read()
            if cosmos == self.vanilla[field][8]:
                continue                  # this chunk IS vanilla; proves none
            got = lgp.split_sections(result.decompressed(result.index[field]))
            self.assertNotEqual(got[8], self.vanilla[field][8],
                                '%s: section 9 fell back to vanilla art'
                                % field)
            checked += 1
        if not checked:
            self.skipTest('no sampled field has a non-vanilla section 9')


if __name__ == '__main__':
    unittest.main()


class PCButtonGlyphs(unittest.TestCase):
    """
    Echo-S names buttons the PC way: `<PC glyph bytes>(NAME)`.

    FFNx draws the glyph and treats the parenthesised name as its fallback.
    Those glyph bytes are not in this port's font, so the reader gets a broken
    character followed by "(CANCEL)". Measured over the release: 262 runs in
    48 fields.

    Replacing the whole dialogue with the Switch archive's own string -- what
    this used to do for md1_1 and nmkin_3 -- costs more than it fixes: the
    port's md1_1 string attributes the tutorial to Barret where Echo-S wrote
    narration (and the clip playing over it is the narrator), and it is four
    lines where Echo-S's is two, behind a `MESSAGE 0,16 ; WAIT 30` that gives
    the box half a second. Substituting only the glyph run keeps the author's
    phrasing, line count and window.
    """

    def test_a_sourced_button_becomes_this_port_s_icon(self):
        import echo_s_flevel as F                               # noqa: E402
        # `95 (CANCEL)` -> the port's own run button, F9.
        run = b'\x95\x08\x23\x21\x2e\x23\x25\x2c\x09'
        out, changed = F._retarget_pc_button_glyphs(run + b'\xff')
        self.assertEqual(out, b'\xf9\xff')
        self.assertEqual(changed, 1)

    def test_mixed_case_names_match_too(self):
        """Echo-S writes both `(CANCEL)` and `(Confirm)`."""
        import echo_s_flevel as F                               # noqa: E402
        out, _ = F._retarget_pc_button_glyphs(
            b'\x98\x08\x23\x4f\x4e\x46\x49\x52\x4d\x09\xff')
        self.assertEqual(out, b'\xf6\x10\xff')

    def test_page_buttons_are_the_ports_l_and_r(self):
        """
        BUILD 609. PGUP/PGDN are L/R: the port's own fship_4 reads "{F6 11}
        arc Left ... {F6 13} arc Right", where Echo-S has "PGUP Rotate left /
        PGDN Rotate right". The glyph byte decides, not the caption.
        """
        import echo_s_flevel as F                               # noqa: E402
        pgup = b'\x99\x08\x30\x27\x35\x30\x09'
        out, changed = F._retarget_pc_button_glyphs(pgup + b'\xff')
        self.assertEqual(out, b'\xf6\x11\xff')
        self.assertEqual(changed, 1)
        pgdn = b'\x9a\x08\x30\x27\x24\x2e\x09'
        out, _ = F._retarget_pc_button_glyphs(pgdn + b'\xff')
        self.assertEqual(out, b'\xf6\x13\xff')
        # games_2's typo: glyph 94 (RIGHT) captioned (LEFT) -> d-pad right
        typo = b'\x94\x08\x2c\x25\x26\x34\x09'
        out, _ = F._retarget_pc_button_glyphs(typo + b'\xff')
        self.assertEqual(out, b'\xf6\x1a\xff')

    def test_ordinary_parentheses_are_untouched(self):
        """
        The open/close codes are also plain punctuation. Only a run that is
        entirely PC glyph bytes, then `(`, then a name, then `)`, is a button.
        """
        import echo_s_flevel as F                               # noqa: E402
        prose = b'\xb2\x08\x2f\x4b\x09\xff'                # “(Ok)
        out, changed = F._retarget_pc_button_glyphs(prose)
        self.assertEqual(out, prose)
        self.assertEqual(changed, 0)

    def test_nothing_past_the_terminator_is_rewritten(self):
        import echo_s_flevel as F                               # noqa: E402
        tail = b'\x95\x08\x23\x21\x2e\x23\x25\x2c\x09'
        out, _ = F._retarget_pc_button_glyphs(b'\xff' + tail)
        self.assertEqual(out, b'\xff' + tail)

    def test_the_wholesale_override_is_retired(self):
        """
        Kept as a mechanism, empty as a policy -- so a future string the port
        genuinely has to own has somewhere to go, and md1_1 keeps Echo-S's.
        """
        import echo_s_flevel as F                               # noqa: E402
        self.assertEqual(F.STOCK_DIALOGUE_OVERRIDES, {})

    def test_the_md1_1_tutorial_keeps_its_author_s_line(self):
        if not os.path.isfile(os.path.join(ECHO_FIELDS, 'md1_1')):
            self.skipTest('Echo-S not extracted')
        import echo_s_flevel as F                               # noqa: E402
        import lgp as L                                         # noqa: E402
        from PyFF7 import text as T                             # noqa: E402
        raw = F._wrapped_field_raw(
            open(os.path.join(ECHO_FIELDS, 'md1_1'), 'rb').read())
        section = L.split_sections(raw)[0]
        cleaned, _ = F.strip_pc_speaker_prefixes(section)

        def dialog(sec, index):
            off, = struct.unpack_from('<H', sec, 4)
            count, = struct.unpack_from('<H', sec, off)
            at = off + struct.unpack_from('<%dH' % count, sec, off + 2)[index]
            return T.decode_field_text(sec[at:sec.index(b'\xff', at)])

        after = dialog(cleaned, 16)
        self.assertIn('✕', after)                  # the port's run button
        self.assertIn('Directional', after)        # and its own wording
        self.assertNotIn('CANCEL', after)          # no PC key name left
        self.assertNotIn('BARRET', after)          # still narration
        self.assertEqual(after.count('\n'), 1)     # still two lines


class ModelLoaderTravelsWithTheScript(unittest.TestCase):
    """
    An entity binds to a model by INDEX into section 3.

    Importing Echo-S's script while keeping the Switch's model loader is
    therefore only valid while the two lists agree. In `mds7pb_1` they do
    not: Echo-S drops the `camera` model, so its script's "model 7" is the
    pinball machine while the Switch loader's index 7 is `nible_camera`, a
    person. The bar's pinball machine rendered as a man.
    """

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(os.path.join(ECHO_FIELDS, 'mds7pb_1')):
            raise unittest.SkipTest('Echo-S not extracted')
        dump = os.path.join(ROOT, 'dump', 'romfs', 'ff7', 'workingdir',
                            'data', 'field', 'flevel.lgp')
        if not os.path.isfile(dump):
            raise unittest.SkipTest('no dump flevel')
        cls.van = lgp.Archive(dump)

    def _entry(self, name):
        return next(e for e in self.van.entries
                    if str(e['name']).lower() == name)

    def test_the_pinball_machine_keeps_its_index(self):
        raw, imported = EF.merge_field_payload(
            self._entry('mds7pb_1')['payload'],
            open(os.path.join(ECHO_FIELDS, 'mds7pb_1'), 'rb').read(),
            field_name='mds7pb_1')
        self.assertIn(3, imported)
        names = EF.model_loader_names(lgp.split_sections(raw)[2])
        self.assertTrue(names[7].endswith('fieldbg_pinbl.char'), names)

    def test_a_field_that_did_not_change_its_loader_imports_script_only(self):
        raw, imported = EF.merge_field_payload(
            self._entry('cargoin')['payload'],
            open(os.path.join(ECHO_FIELDS, 'cargoin'), 'rb').read(),
            field_name='cargoin')
        self.assertEqual(tuple(imported), (1,))

    def test_a_loader_naming_another_field_s_model_is_refused(self):
        """
        The port keys models per field, so a name borrowed from elsewhere is
        an asset this field cannot load. Keep the Switch loader and say so
        rather than shipping a field that cannot resolve its models.

        BUILD 445 moved this off `blin67_2`. That field names its models
        differently but every index already means the same asset here, so
        `remap_model_loader` now answers EQUIVALENT and it is no longer listed
        as unportable -- correctly: nothing about it is mismapped. `mds6_2`
        genuinely borrows `mds7_w2main_n_cloud.char`, an HRC this field does
        not have, which is the case this test is about.
        """
        raw, imported = EF.merge_field_payload(
            self._entry('mds6_2')['payload'],
            open(os.path.join(ECHO_FIELDS, 'mds6_2'), 'rb').read(),
            field_name='mds6_2')
        self.assertNotIn(3, imported)
        self.assertIn('mds6_2', EF.UNPORTABLE_MODEL_LOADERS)

    def test_a_loader_that_only_renames_is_taken_in_echo_s_order(self):
        """
        `mds7pb_2`, the floor below the one that started all this. Echo-S
        renames the models (`Pinball`, `Shake`) so the name check refuses
        them, but the 8-byte HRC id still matches, so the port takes Echo-S's
        ORDER with its own records and model 7 is the pinball machine again
        instead of `nible_camera`, a person.
        """
        raw, imported = EF.merge_field_payload(
            self._entry('mds7pb_2')['payload'],
            open(os.path.join(ECHO_FIELDS, 'mds7pb_2'), 'rb').read(),
            field_name='mds7pb_2')
        self.assertIn(3, imported)
        names = EF.model_loader_names(lgp.split_sections(raw)[2])
        self.assertEqual(names[7], 'mds7pb_2fieldbg_pinbl.char')
        self.assertIn('mds7pb_2', EF.REMAPPED_MODEL_LOADERS)

    def test_the_parser_agrees_with_kujata_on_a_known_field(self):
        names = EF.model_loader_names(
            lgp.split_sections(self.van.decompressed(
                self._entry('mds7pb_1')))[2])
        self.assertEqual(len(names), 12)
        self.assertTrue(names[0].endswith('main_n_cloud.char'))
        self.assertTrue(names[7].endswith('nible_camera.char'))
        self.assertTrue(names[8].endswith('fieldbg_pinbl.char'))
