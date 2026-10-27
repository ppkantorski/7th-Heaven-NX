#!/usr/bin/env python3
"""
Echo-S's two non-voice prerequisites: the FFNx music patches and the kernel.

WHY THESE HAVE THEIR OWN TEST
=============================
`build.py` already merges Echo-S's field scripts into flevel. Those scripts
are FFNx-authored, and FFNx applies two unconditional music patches to the PC
exe before any mod's script ever runs. Ship the scripts without the patches
and the symptom is "towns have no BGM and battles inherit the wrong track" --
which reads as a field or soundtrack bug, not a script bug, and costs a
hardware round trip to attribute. So the pair is asserted here:

  * both sites still hold their stock word in the STOCK dump AND in a module
    that has already been through the 60 FPS preset, widescreen, analog-360
    and all five Cosmo Memory audio bridges (that second one is the claim
    that actually matters -- it is what "no collision" means in this project);
  * installing them changes exactly two game instructions and nothing else:
    no BSS growth, no segment growth, no contiguous table spend;
  * a site that somebody else has already claimed is refused, not overwritten.

The kernel half is here for the same reason in miniature: Echo-S ships PC
containers, the Switch frames them differently, and the weapon table carries
SFX ids that Cosmo Memory's battle_char bridges read. A blind copy is a
kernel the port cannot parse; a blind merge is Barret and Vincent going
silent in battle.

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
import shutil
import struct
import sys
import tempfile
import unittest

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import a64 as A                                                # noqa: E402
import echo_s_kernel                                           # noqa: E402
import ff7nx_audio_cave as AC                                  # noqa: E402
import ff7nx_echomusic as EM                                   # noqa: E402

MD = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)

STOCK_MAIN = os.path.join(ROOT, 'dump', 'exefs', 'main')
DUMP_MAIN = os.path.join(ROOT, 'dump', 'exefs', 'main')
BUILT_MAIN = os.path.join(ROOT, 'sdout', 'atmosphere', 'contents',
                          '0100A5B00BDC6000', 'exefs', 'main')

SWITCH_KERNEL = os.path.join(ROOT, 'dump', 'romfs', 'ff7', 'workingdir',
                             'data', 'lang-en', 'kernel')
ECHO_KERNEL = os.path.join(ROOT, 'cache', 'Echo-s', 'Echo-S', 'kernel')


def text_of(path):
    with open(path, 'rb') as handle:
        _segs, raw = AC.segments(handle.read())
    return raw[0]


def word_at(blob, va):
    return struct.unpack_from('<I', blob, va)[0]


def bss_of(path):
    with open(path, 'rb') as handle:
        return struct.unpack_from('<I', handle.read(0x40), 0x3C)[0]


class Sites(unittest.TestCase):
    """Both hook sites, in every module this project actually patches."""

    def _check(self, path):
        text = text_of(path)
        self.assertEqual(word_at(text, EM.LOCK_SITE), EM.LOCK_SITE_ORIGINAL,
                         '%s: the music-lock site is not stock' % path)
        self.assertEqual(word_at(text, EM.FIELD_SOUND_CALL),
                         EM.FIELD_SOUND_CALL_ORIGINAL,
                         '%s: the AKAO call site is not stock' % path)

    def test_stock_module(self):
        if not os.path.isfile(STOCK_MAIN):
            self.skipTest('exefs/main not available')
        self._check(STOCK_MAIN)

    def test_dump_module(self):
        if not os.path.isfile(DUMP_MAIN):
            self.skipTest('dump/exefs/main not available')
        self._check(DUMP_MAIN)

    def test_fully_patched_module(self):
        """
        The real claim. This module has the 60 FPS preset, widescreen,
        analog-360 and every Cosmo Memory bridge in it already; three of the
        ambient hooks are branches by now. If either Echo-S site had been
        taken, this is where it shows up.
        """
        if not os.path.isfile(BUILT_MAIN):
            self.skipTest('no built sdout module to check against')
        self._check(BUILT_MAIN)

    def test_ffnx_sound_call_target_is_the_stock_interpreter(self):
        """The site is a BL to the AKAO interpreter, recomputed not copied."""
        self.assertEqual(EM.FIELD_SOUND_CALL_ORIGINAL,
                         A.bl(EM.FIELD_SOUND_CALL, EM.FIELD_SOUND_TARGET))


class Install(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = (BUILT_MAIN if os.path.isfile(BUILT_MAIN) else STOCK_MAIN)
        if not os.path.isfile(cls.base):
            raise unittest.SkipTest('no module available to patch')

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='echomusic-')
        self.out = os.path.join(self.tmp, 'main')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_installs_and_links_both_sites(self):
        report = EM.apply_to_nso(self.base, self.out)
        text = text_of(self.out)
        self.assertEqual(word_at(text, EM.LOCK_SITE),
                         A.b(EM.LOCK_SITE, report['lock']['cave_entry']))
        self.assertEqual(word_at(text, EM.FIELD_SOUND_CALL),
                         A.bl(EM.FIELD_SOUND_CALL,
                              report['sound']['cave_entry']))
        # A BL, not a B: the sanitiser wrapper has to return.
        self.assertEqual(word_at(text, EM.FIELD_SOUND_CALL) >> 26, 0x25)

    def test_spends_no_bss_and_no_segment_growth(self):
        EM.apply_to_nso(self.base, self.out)
        self.assertEqual(bss_of(self.out), bss_of(self.base))
        self.assertEqual(len(text_of(self.out)), len(text_of(self.base)))
        self.assertEqual(len(text_of(self.out)), len(text_of(self.base)))

    def test_changes_only_padding_and_the_two_sites(self):
        """
        Every word that moved is either one of the two hook sites or a hole
        that was zero before. That is the whole safety argument for taking
        caves out of the padding pool, so it is asserted rather than trusted.
        """
        EM.apply_to_nso(self.base, self.out)
        before, after = text_of(self.base), text_of(self.out)
        sites = {EM.LOCK_SITE, EM.FIELD_SOUND_CALL}
        moved = [va for va in range(0, len(before), 4)
                 if before[va:va + 4] != after[va:va + 4]]
        self.assertTrue(moved)
        for va in moved:
            if va in sites:
                continue
            self.assertEqual(word_at(before, va), 0,
                             'cave word +0x%X overwrote live code %08X'
                             % (va, word_at(before, va)))

    def test_cave_body_is_what_ffnx_does(self):
        """Disassemble the sanitiser: mask to a byte, fold 0xDA onto 0xF0."""
        report = EM.apply_to_nso(self.base, self.out)
        text = text_of(self.out)
        seen, va, guard = [], report['sound']['cave_entry'], 0
        while guard < 64:
            guard += 1
            word = word_at(text, va)
            ins = next(MD.disasm(text[va:va + 4], va), None)
            self.assertIsNotNone(ins, 'undecodable cave word at +0x%X' % va)
            if ins.mnemonic == 'b':                    # hop to the next hole
                va = int(ins.op_str.lstrip('#'), 16)
                continue
            seen.append(('%s %s' % (ins.mnemonic, ins.op_str)).strip())
            if ins.mnemonic == 'ret':
                break
            va += 4
        body = ' | '.join(seen)
        self.assertIn('and w0, w0, #0xff', body)
        self.assertIn('cmp w0, #0xda', body)
        self.assertIn('mov w0, #0xf0', body)
        self.assertIn('bl #0x%x' % EM.FIELD_SOUND_TARGET, body)
        self.assertTrue(body.endswith('ret'), body)

    def test_refuses_a_site_somebody_else_already_owns(self):
        """
        Fail closed. `expect_word` is the one check that makes every module
        pass in this project composable, so prove it fires here too.
        """
        EM.apply_to_nso(self.base, self.out)                # first install
        second = os.path.join(self.tmp, 'again')
        with self.assertRaises(ValueError):
            EM.apply_to_nso(self.out, second)

    def test_composes_after_the_cosmo_bridges(self):
        """
        Installing onto the module the real build produces must work, and
        must leave every ambient hook exactly as it found it.
        """
        if not os.path.isfile(BUILT_MAIN):
            self.skipTest('no built sdout module to compose against')
        EM.apply_to_nso(BUILT_MAIN, self.out)
        before, after = text_of(BUILT_MAIN), text_of(self.out)
        for va in (0x947CF0, 0x947CF4, 0x8FB20, 0xF1E0EC, 0xD0C3D4):
            self.assertEqual(word_at(before, va), word_at(after, va),
                             'Echo-S music patch disturbed +0x%X' % va)


class Kernel(unittest.TestCase):
    """Section-level merge of Echo-S's PC kernel into the Switch container."""

    @classmethod
    def setUpClass(cls):
        # Both directories hold a file called kernel2.bin, so these are keyed
        # by SIDE, not by basename. Keying on the basename silently made the
        # PC file its own stock copy and turned every assertion below into a
        # tautology -- which is how this test first "passed".
        want = {'switch_kernel': os.path.join(SWITCH_KERNEL, 'kernel.bin'),
                'switch_kernel2': os.path.join(SWITCH_KERNEL, 'kernel2.bin'),
                'echo_kernel': os.path.join(ECHO_KERNEL, 'KERNEL.BIN'),
                'echo_kernel2': os.path.join(ECHO_KERNEL, 'kernel2.bin')}
        if not all(os.path.isfile(p) for p in want.values()):
            raise unittest.SkipTest('Switch or Echo-S kernel not extracted')
        cls.f = {k: open(p, 'rb').read() for k, p in want.items()}

    def test_pc_and_switch_containers_really_do_differ(self):
        """The premise. If these ever matched, the merge would be pointless."""
        self.assertNotEqual(self.f['switch_kernel'], self.f['echo_kernel'])
        self.assertNotEqual(self.f['switch_kernel2'], self.f['echo_kernel2'])
        # Same logical section count, different framing -- which is exactly
        # why a copy is wrong and a merge is right.
        switch, _ = echo_s_kernel._gzip_sections(self.f['switch_kernel'])
        echo, _ = echo_s_kernel._gzip_sections(self.f['echo_kernel'])
        self.assertEqual(len(switch), len(echo))

    def test_kernel_merge_reparses_as_a_switch_container(self):
        merged, changed = echo_s_kernel.merge_gzip_container(
            self.f['switch_kernel'], self.f['echo_kernel'],
            preserve_weapon_sfx=True)
        self.assertTrue(changed, 'Echo-S changed no kernel section at all')
        sections, _suffix = echo_s_kernel._gzip_sections(merged)
        stock, _ = echo_s_kernel._gzip_sections(self.f['switch_kernel'])
        self.assertEqual([k for k, _ in sections], [k for k, _ in stock])

    def test_weapon_normal_hit_sfx_stays_switch_native(self):
        """
        Cosmo Memory's battle_char routes key off the STOCK normal-hit SFX id.
        Echo-S rewrites Barret's and Vincent's for FFNx's numeric path, so
        importing them is what makes those two characters silent on Switch.

        Echo-S's own top-level KERNEL.BIN happens not to touch the weapon
        table -- its `Vanilla` and `Legionz` alternates are the ones that do,
        and those are dropped before they reach the build. The guard is still
        asserted against a real weapon section, using that alternate as the
        adversarial input, so it stays meaningful if a future Echo-S release
        moves those bytes into the file we do take.
        """
        adversary = os.path.join(ECHO_KERNEL, 'Vanilla', 'KERNEL.BIN')
        if not os.path.isfile(adversary):
            self.skipTest('Echo-S Vanilla kernel not extracted')
        with open(adversary, 'rb') as handle:
            echo = handle.read()

        def weapons(blob):
            for kind, raw in echo_s_kernel._gzip_sections(blob)[0]:
                if kind == echo_s_kernel._WEAPON_SECTION_KIND:
                    return raw
            return None

        stock, echo_sec = weapons(self.f['switch_kernel']), weapons(echo)
        self.assertIsNotNone(stock, 'no weapon section in the Switch kernel')
        off = echo_s_kernel._WEAPON_NORMAL_HIT_SFX_OFFSET
        size = echo_s_kernel._WEAPON_RECORD_SIZE
        count = echo_s_kernel._WEAPON_RECORD_COUNT

        # The adversary really does move SFX ids, or the guard proves nothing.
        moved = [w for w in range(count)
                 if echo_sec[w * size + off] != stock[w * size + off]]
        self.assertTrue(moved, 'the PC kernel no longer moves any SFX id')

        merged, _ = echo_s_kernel.merge_gzip_container(
            self.f['switch_kernel'], echo, preserve_weapon_sfx=True)
        out = weapons(merged)
        leaked = [w for w in range(count)
                  if out[w * size + off] != stock[w * size + off]]
        self.assertEqual(leaked, [],
                         'normal-hit SFX came from Echo-S for weapon(s) %s'
                         % leaked)

        # And the guard is NARROW: every byte the merge left alone is a byte
        # the PC file also left alone. On the Echo-S release in this cache the
        # SFX ids are in fact the only thing that file changes, so the merged
        # section comes back identical to stock -- which is the correct result
        # and worth pinning, because "identical" here means the guard caught
        # all of it rather than that the merge did nothing.
        elsewhere = [i for i in range(len(stock))
                     if out[i] != stock[i] and i % size != off]
        self.assertEqual(elsewhere, [],
                         '%d non-SFX byte(s) diverged' % len(elsewhere))
        self.assertEqual(
            sorted(moved),
            sorted(i // size for i in range(len(stock))
                   if echo_sec[i] != stock[i]),
            'the PC weapon table changed something other than SFX ids; the '
            'preserve rule now drops a real Echo-S weapon edit')

    def test_kernel2_merge_takes_echos_text(self):
        merged, changed = echo_s_kernel.merge_kernel2(
            self.f['switch_kernel2'], self.f['echo_kernel2'])
        self.assertEqual(len(echo_s_kernel._kernel2_sections(merged)), 18)
        self.assertTrue(changed, 'Echo-S changed no kernel2 section at all')
        out = echo_s_kernel._kernel2_sections(merged)
        echo = echo_s_kernel._kernel2_sections(self.f['echo_kernel2'])
        stock = echo_s_kernel._kernel2_sections(self.f['switch_kernel2'])
        for i in range(18):
            self.assertEqual(out[i], echo[i] if i in changed else stock[i])

    def test_merging_a_file_with_itself_changes_nothing(self):
        """A no-op merge must be a no-op, or every build churns the kernel."""
        merged, changed = echo_s_kernel.merge_kernel2(
            self.f['switch_kernel2'], self.f['switch_kernel2'])
        self.assertEqual(changed, ())
        self.assertEqual(echo_s_kernel._kernel2_sections(merged),
                         echo_s_kernel._kernel2_sections(
                             self.f['switch_kernel2']))


class _Manifest:
    def __init__(self, mod_id):
        self.mod_id = mod_id
        self.folders = []
        self.hext = []
        self.folder_conditions = {}
        self.options = []


class _Mod:
    """The smallest thing `build_plan` will accept, over a real directory."""

    def __init__(self, root, mod_id, subdir):
        self.path = self.cache = root
        self.filename = 'Echo-s.iro'
        self.stem = 'Echo-s'
        self.display_name = 'Echo-S (stub)'
        self.manifest = _Manifest(mod_id)
        self.error = None
        self.skipped_images = 0
        self.subdir = subdir

    def files_for(self, _settings, read=None, log=None):
        picked = []
        for dirpath, _dirs, files in os.walk(os.path.join(self.cache,
                                                          *self.subdir)):
            for name in files:
                full = os.path.join(dirpath, name)
                picked.append((os.path.relpath(full, self.cache)
                               .replace(os.sep, '/'), full, None))
        return sorted(picked)


class Routing(unittest.TestCase):
    """
    Which of Echo-S's kernel files the plan actually takes.

    Echo-S's `Echo-S/kernel/` folder holds four different KERNEL.BINs and
    three kernel2.bins, and mod.xml describes none of that -- it just takes
    the whole `Echo-S` folder. Through the ordinary loose-file router three of
    them key on the same name and the last one off the filesystem wins, which
    is a silent, directory-order-dependent choice between incompatible
    packages. Both halves are asserted: Echo-S is narrowed to the exact pair,
    and any OTHER mod's kernel files still go down the old path untouched.
    """

    ECHO_CACHE = os.path.join(ROOT, 'cache', 'Echo-s')

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(cls.ECHO_CACHE, 'Echo-S', 'kernel')):
            raise unittest.SkipTest('Echo-S not extracted')
        import build                                           # noqa: E402
        cls.build = build

    def _plan(self, mod_id):
        mod = _Mod(self.ECHO_CACHE, mod_id, ('Echo-S', 'kernel'))
        return self.build.build_plan([mod], {}, {'flevel.lgp': set()},
                                     log=lambda *_a: None)

    def test_echo_s_takes_exactly_the_two_top_level_records(self):
        plan = self._plan(self.build.ECHO_S_MOD_ID)
        self.assertEqual(sorted(plan.echo_kernel),
                         ['kernel.bin', 'kernel2.bin'])
        self.assertEqual(plan.loose, {},
                         'an Echo-S kernel file leaked into the raw-copy path')
        taken = {os.path.basename(src)
                 for src, _mod in plan.echo_kernel.values()}
        self.assertEqual(taken, {'KERNEL.BIN', 'kernel2.bin'})
        for src, _mod in plan.echo_kernel.values():
            parent = os.path.basename(os.path.dirname(src))
            self.assertEqual(parent, 'kernel',
                             'took %s out of an alternate package' % src)

    def test_the_alternate_packages_are_reported_not_silently_dropped(self):
        plan = self._plan(self.build.ECHO_S_MOD_ID)
        skipped = {os.path.basename(r) for r in plan.echo_kernel_skipped}
        for name in ('window.bin', 'OldSpellNames_KERNEL.BIN',
                     'OldSpellNames_kernel2.bin'):
            self.assertIn(name, skipped)
        # Legionz ships a battle.lgp entry inside the kernel folder too; it
        # belongs to the alternate package, not to this build's battle archive.
        self.assertEqual(plan.archive_files, {})

    def test_another_mods_kernel_is_completely_unaffected(self):
        """
        The narrowing is keyed on Echo-S's manifest GUID. Modern Spell Names
        and Enhanced Stock UI both ship kernel2.bin and must keep going down
        the path they always did.
        """
        plan = self._plan('deadbeef-0000-0000-0000-000000000000')
        self.assertEqual(plan.echo_kernel, {})
        self.assertEqual(plan.echo_kernel_skipped, [])
        self.assertIn(('kernel', 'kernel2.bin'), plan.loose)
        self.assertIn(('kernel', 'kernel.bin'), plan.loose)


if __name__ == '__main__':
    unittest.main()
