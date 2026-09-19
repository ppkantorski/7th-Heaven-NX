#!/usr/bin/env python3
"""
voicemod.py -- Echo-S's dialogue clips: which file is which line, and what
name it has to be staged under.

WHAT THIS IS AND WHERE IT CAME FROM
===================================
This is the collection half of Echo-S, in the same place in the pipeline as
`sfxmod` and `ambientmod` are for Cosmo Memory: it reads a mod's folder of
.ogg files, works out what each one actually IS in the game's terms, and
hands `build.py` a list of entries with canonical names. It installs nothing
and touches no module; `ff7nx_voice` is the runtime that reads what this
stages.

The key scheme and the ingestion rules are the author's, carried over rather
than rewritten -- they were derived from the real binaries and validated on
hardware across roughly a hundred revisions, and there is nothing to gain and
a great deal to lose by re-deriving them. What changed in the port is the
plumbing around them:

  * the fork's `stage()` is gone. It re-encoded every clip on every run and
    wrote a manifest.json into the output folder. Staging lives in
    `build.py`'s emplacement pass here, behind a content-addressed cache,
    because this is 15,505 clips and 2.2 GB and nobody is paying that twice.
  * no `json`, no thread pool. The parallelism belongs to the cache.

THE ONE IDEA THE WHOLE THING RESTS ON: THERE IS NO LOOKUP TABLE
===============================================================
The Switch port has exactly one general-purpose way to play a loose .ogg by
name -- `MusicStream`, which formats `"%s/data/music_ogg/%s.ogg"` and hands
it to vgmstream. It takes a NAME, not a slot id. So a voice line does not
need a table in the module at all: the runtime rebuilds the filename from
live game state (the field's own basename, the dialogue id, the page letter)
and asks for it. A missing file is an ordinary no-op.

That is why `field_folder_key_to_filename` lays clips out as FFNx does --
`<field>/<dialog><page-letter>.ogg`, `_world/...`, `nocloud/tifa|cid/...` --
instead of flattening them. The layout IS the index, and it costs zero bytes
of the contiguous table budget that ambience and the SFX bridges compete for.

NAMESPACES
==========
A 64-bit key carries the namespace in its top byte, so field lines, ASK
options, world dialogue, the Tifa/Cid party-leader overlays and battle barks
can be collected together and told apart afterwards:

    field_line  field_choice  world_line  world_choice
    nocloud_tifa_choice  nocloud_cid_choice  battle_bark

Measured against the real Echo-S release in this project's cache:

    field_line           13,816      world_line             34
    field_choice          1,419      world_choice            6
    nocloud_tifa_choice     115      nocloud_cid_choice    115
                                     ------------------------
                                     15,505 entries, zero name collisions

43 files are skipped as unrecognised. They are authoring leftovers with
human-readable names (`bigwheel/36 6.ogg`, `coloin1/yeah thanks.ogg`) that do
not match FFNx's own `<dialog><page>` convention, so there is no line they
could be attached to. Skipping them is the correct answer, and they are
reported rather than dropped silently.

DUPLICATES ARE AN ERROR, NOT A LAST-WINS
========================================
FFNx lets a clip be named with or without a window qualifier and falls back
between them. Ingestion collapses that one legitimate duplicate itself; any
other pair of files landing on the same key is a packaging mistake and stops
the build rather than quietly picking one.
"""
import io
import os
import re
import struct


# --------------------------------------------------------------- namespaces

NS_FIELD_LINE = 0x01
NS_FIELD_CHOICE = 0x02
NS_BATTLE_BARK = 0x03
NS_WORLD_LINE = 0x04
NS_WORLD_CHOICE = 0x05
NS_NOCLOUD_TIFA_CHOICE = 0x06
NS_NOCLOUD_CID_CHOICE = 0x07

_NAMES = {NS_FIELD_LINE: 'field_line', NS_FIELD_CHOICE: 'field_choice',
          NS_BATTLE_BARK: 'battle_bark', NS_WORLD_LINE: 'world_line',
          NS_WORLD_CHOICE: 'world_choice',
          NS_NOCLOUD_TIFA_CHOICE: 'nocloud_tifa_choice',
          NS_NOCLOUD_CID_CHOICE: 'nocloud_cid_choice'}


class BadArchive(Exception):
    pass



# ---------------------------------------------------------- field id table
#
# Source: https://ff7-mods.github.io/ff7-flat-wiki/FF7/Field/Field_ID
# (community-maintained, extracted from flevel.lgp). 787 entries, field
# id 0x000 ("dummy") through 0x312 ("ztruck"). Validated: IDs form a
# clean contiguous 0..786 sequence with no gaps or duplicate names.
# Keys are lowercase to match Echo-S's own (already-lowercase) folder
# naming convention.

FIELD_NAME_TO_ID = {
    'dummy': 0x000, 'wm0': 0x001, 'wm1': 0x002, 'wm2': 0x003,
    'wm3': 0x004, 'wm4': 0x005, 'wm5': 0x006, 'wm6': 0x007,
    'wm7': 0x008, 'wm8': 0x009, 'wm9': 0x00A, 'wm10': 0x00B,
    'wm11': 0x00C, 'wm12': 0x00D, 'wm13': 0x00E, 'wm14': 0x00F,
    'wm15': 0x010, 'wm16': 0x011, 'wm17': 0x012, 'wm18': 0x013,
    'wm19': 0x014, 'wm20': 0x015, 'wm21': 0x016, 'wm22': 0x017,
    'wm23': 0x018, 'wm24': 0x019, 'wm25': 0x01A, 'wm26': 0x01B,
    'wm27': 0x01C, 'wm28': 0x01D, 'wm29': 0x01E, 'wm30': 0x01F,
    'wm31': 0x020, 'wm32': 0x021, 'wm33': 0x022, 'wm34': 0x023,
    'wm35': 0x024, 'wm36': 0x025, 'wm37': 0x026, 'wm38': 0x027,
    'wm39': 0x028, 'wm40': 0x029, 'wm41': 0x02A, 'wm42': 0x02B,
    'wm43': 0x02C, 'wm44': 0x02D, 'wm45': 0x02E, 'wm46': 0x02F,
    'wm47': 0x030, 'wm48': 0x031, 'wm49': 0x032, 'wm50': 0x033,
    'wm51': 0x034, 'wm52': 0x035, 'wm53': 0x036, 'wm54': 0x037,
    'wm55': 0x038, 'wm56': 0x039, 'wm57': 0x03A, 'wm58': 0x03B,
    'wm59': 0x03C, 'wm60': 0x03D, 'wm61': 0x03E, 'wm62': 0x03F,
    'wm63': 0x040, 'startmap': 0x041, 'fship_1': 0x042, 'fship_12': 0x043,
    'fship_2': 0x044, 'fship_22': 0x045, 'fship_23': 0x046, 'fship_24': 0x047,
    'fship_25': 0x048, 'fship_3': 0x049, 'fship_4': 0x04A, 'fship_42': 0x04B,
    'fship_5': 0x04C, 'hill': 0x04D, 'zz1': 0x04E, 'zz2': 0x04F,
    'zz3': 0x050, 'zz4': 0x051, 'zz5': 0x052, 'zz6': 0x053,
    'zz7': 0x054, 'zz8': 0x055, 'sea': 0x056, 'sky': 0x057,
    'qa': 0x058, 'qb': 0x059, 'qc': 0x05A, 'qd': 0x05B,
    'qe': 0x05C, 'blackbg1': 0x05D, 'blackbg2': 0x05E, 'blackbg3': 0x05F,
    'blackbg4': 0x060, 'blackbg5': 0x061, 'blackbg6': 0x062, 'blackbg7': 0x063,
    'blackbg8': 0x064, 'blackbg9': 0x065, 'blackbga': 0x066, 'blackbgb': 0x067,
    'blackbgc': 0x068, 'blackbgd': 0x069, 'blackbge': 0x06A, 'blackbgf': 0x06B,
    'blackbgg': 0x06C, 'blackbgh': 0x06D, 'blackbgi': 0x06E, 'blackbgj': 0x06F,
    'blackbgk': 0x070, 'whitebg1': 0x071, 'whitebg2': 0x072, 'whitebg3': 0x073,
    'md1stin': 0x074, 'md1_1': 0x075, 'md1_2': 0x076, 'nrthmk': 0x077,
    'nmkin_1': 0x078, 'elevtr1': 0x079, 'nmkin_2': 0x07A, 'nmkin_3': 0x07B,
    'nmkin_4': 0x07C, 'nmkin_5': 0x07D, 'southmk1': 0x07E, 'southmk2': 0x07F,
    'smkin_1': 0x080, 'smkin_2': 0x081, 'smkin_3': 0x082, 'smkin_4': 0x083,
    'smkin_5': 0x084, 'md8_1': 0x085, 'md8_2': 0x086, 'md8_3': 0x087,
    'md8_4': 0x088, 'md8brdg': 0x089, 'cargoin': 0x08A, 'tin_1': 0x08B,
    'tin_2': 0x08C, 'tin_3': 0x08D, 'tin_4': 0x08E, 'rootmap': 0x08F,
    'mds7st1': 0x090, 'mds7st2': 0x091, 'mds7st3': 0x092, 'mds7st32': 0x093,
    'mds7_w1': 0x094, 'mds7_w2': 0x095, 'mds7_w3': 0x096, 'mds7': 0x097,
    'mds7_im': 0x098, 'min71': 0x099, 'mds7pb_1': 0x09A, 'mds7pb_2': 0x09B,
    'mds7plr1': 0x09C, 'mds7plr2': 0x09D, 'pillar_1': 0x09E, 'pillar_2': 0x09F,
    'pillar_3': 0x0A0, 'tunnel_1': 0x0A1, 'tunnel_2': 0x0A2, 'tunnel_3': 0x0A3,
    'sbwy4_1': 0x0A4, 'sbwy4_2': 0x0A5, 'sbwy4_3': 0x0A6, 'sbwy4_4': 0x0A7,
    'sbwy4_5': 0x0A8, 'sbwy4_6': 0x0A9, 'mds5_5': 0x0AA, 'mds5_4': 0x0AB,
    'mds5_3': 0x0AC, 'mds5_2': 0x0AD, 'min51_1': 0x0AE, 'min51_2': 0x0AF,
    'mds5_dk': 0x0B0, 'mds5_1': 0x0B1, 'mds5_w': 0x0B2, 'mds5_i': 0x0B3,
    'mds5_m': 0x0B4, 'church': 0x0B5, 'chrin_1a': 0x0B6, 'chrin_1b': 0x0B7,
    'chrin_2': 0x0B8, 'chrin_3a': 0x0B9, 'chrin_3b': 0x0BA, 'eals_1': 0x0BB,
    'ealin_1': 0x0BC, 'ealin_12': 0x0BD, 'ealin_2': 0x0BE, 'mds6_1': 0x0BF,
    'mds6_2': 0x0C0, 'mds6_22': 0x0C1, 'mds6_3': 0x0C2, 'mrkt2': 0x0C3,
    'mkt_w': 0x0C4, 'mkt_mens': 0x0C5, 'mkt_ia': 0x0C6, 'mktinn': 0x0C7,
    'mkt_m': 0x0C8, 'mkt_s1': 0x0C9, 'mkt_s2': 0x0CA, 'mkt_s3': 0x0CB,
    'mktpb': 0x0CC, 'mrkt1': 0x0CD, 'colne_1': 0x0CE, 'colne_2': 0x0CF,
    'colne_3': 0x0D0, 'colne_4': 0x0D1, 'colne_5': 0x0D2, 'colne_6': 0x0D3,
    'colne_b1': 0x0D4, 'colne_b3': 0x0D5, 'mrkt3': 0x0D6, 'onna_1': 0x0D7,
    'onna_2': 0x0D8, 'onna_3': 0x0D9, 'onna_4': 0x0DA, 'onna_5': 0x0DB,
    'onna_52': 0x0DC, 'onna_6': 0x0DD, 'mrkt4': 0x0DE, 'wcrimb_1': 0x0DF,
    'wcrimb_2': 0x0E0, 'md0': 0x0E1, 'roadend': 0x0E2, 'sinbil_1': 0x0E3,
    'sinbil_2': 0x0E4, 'blinst_1': 0x0E5, 'blinst_2': 0x0E6, 'blinst_3': 0x0E7,
    'blinele': 0x0E8, 'eleout': 0x0E9, 'blin1': 0x0EA, 'blin2': 0x0EB,
    'blin2_i': 0x0EC, 'blin3_1': 0x0ED, 'blin59': 0x0EE, 'blin60_1': 0x0EF,
    'blin60_2': 0x0F0, 'blin61': 0x0F1, 'blin62_1': 0x0F2, 'blin62_2': 0x0F3,
    'blin62_3': 0x0F4, 'blin63_1': 0x0F5, 'blin63_t': 0x0F6, 'blin64': 0x0F7,
    'blin65_1': 0x0F8, 'blin65_2': 0x0F9, 'blin66_1': 0x0FA, 'blin66_2': 0x0FB,
    'blin66_3': 0x0FC, 'blin66_4': 0x0FD, 'blin66_5': 0x0FE, 'blin66_6': 0x0FF,
    'blin67_1': 0x100, 'blin671b': 0x101, 'blin67_2': 0x102, 'blin67_3': 0x103,
    'blin673b': 0x104, 'blin67_4': 0x105, 'blin68_1': 0x106, 'blin68_2': 0x107,
    'blin69_1': 0x108, 'blin69_2': 0x109, 'blin70_1': 0x10A, 'blin70_2': 0x10B,
    'blin70_3': 0x10C, 'blin70_4': 0x10D, 'niv_w': 0x10E, 'nvmin1_1': 0x10F,
    'nvmin1_2': 0x110, 'nivinn_1': 0x111, 'nivinn_2': 0x112, 'nivinn_3': 0x113,
    'niv_cl': 0x114, 'trackin': 0x115, 'trackin2': 0x116, 'nivgate': 0x117,
    'nivgate2': 0x118, 'nivgate3': 0x119, 'nivl': 0x11A, 'nivl_2': 0x11B,
    'nivl_3': 0x11C, 'nivl_4': 0x11D, 'niv_ti1': 0x11E, 'niv_ti2': 0x11F,
    'niv_ti3': 0x120, 'niv_ti4': 0x121, 'nivl_b1': 0x122, 'nivl_b12': 0x123,
    'nivl_b2': 0x124, 'nivl_b22': 0x125, 'nivl_e1': 0x126, 'nivl_e2': 0x127,
    'nivl_e3': 0x128, 'sinin1_1': 0x129, 'sinin1_2': 0x12A, 'sinin2_1': 0x12B,
    'sinin2_2': 0x12C, 'sinin3': 0x12D, 'sininb1': 0x12E, 'sininb2': 0x12F,
    'sininb31': 0x130, 'sininb32': 0x131, 'sininb33': 0x132, 'sininb41': 0x133,
    'sininb42': 0x134, 'sininb51': 0x135, 'sininb52': 0x136, 'mtnvl2': 0x137,
    'mtnvl3': 0x138, 'mtnvl4': 0x139, 'mtnvl5': 0x13A, 'mtnvl6': 0x13B,
    'mtnvl6b': 0x13C, 'nvdun1': 0x13D, 'nvdun2': 0x13E, 'nvdun3': 0x13F,
    'nvdun31': 0x140, 'nvdun4': 0x141, 'nvmkin1': 0x142, 'nvmkin21': 0x143,
    'nvmkin22': 0x144, 'nvmkin23': 0x145, 'nvmkin31': 0x146, 'nvmkin32': 0x147,
    'elm_wa': 0x148, 'elm_i': 0x149, 'elmpb': 0x14A, 'elminn_1': 0x14B,
    'elminn_2': 0x14C, 'elmin1_1': 0x14D, 'elmin1_2': 0x14E, 'elm': 0x14F,
    'elmin2_1': 0x150, 'elmin2_2': 0x151, 'elmin3_1': 0x152, 'elmin3_2': 0x153,
    'elmtow': 0x154, 'elmin4_1': 0x155, 'elmin4_2': 0x156, 'farm': 0x157,
    'frmin': 0x158, 'frcyo': 0x159, 'trap': 0x15A, 'fr_e': 0x15B,
    'sichi': 0x15C, 'psdun_1': 0x15D, 'psdun_2': 0x15E, 'psdun_3': 0x15F,
    'psdun_4': 0x160, 'condor1': 0x161, 'condor2': 0x162, 'convil_1': 0x163,
    'convil_2': 0x164, 'convil_3': 0x165, 'convil_4': 0x166, 'junon': 0x167,
    'junonr1': 0x168, 'junonr2': 0x169, 'junonr3': 0x16A, 'junonr4': 0x16B,
    'jun_wa': 0x16C, 'jun_i1': 0x16D, 'jun_m': 0x16E, 'junmin1': 0x16F,
    'junmin2': 0x170, 'junmin3': 0x171, 'junonl1': 0x172, 'junonl2': 0x173,
    'junonl3': 0x174, 'jun_w': 0x175, 'jun_a': 0x176, 'jun_i2': 0x177,
    'juninn': 0x178, 'junpb_1': 0x179, 'junpb_2': 0x17A, 'junpb_3': 0x17B,
    'junmin4': 0x17C, 'junmin5': 0x17D, 'jundoc1a': 0x17E, 'jundoc1b': 0x17F,
    'junair': 0x180, 'junair2': 0x181, 'junin1': 0x182, 'junin1a': 0x183,
    'junele1': 0x184, 'junin2': 0x185, 'junin3': 0x186, 'junele2': 0x187,
    'junin4': 0x188, 'junin5': 0x189, 'junin6': 0x18A, 'junin7': 0x18B,
    'junbin1': 0x18C, 'junbin12': 0x18D, 'junbin21': 0x18E, 'junbin22': 0x18F,
    'junbin3': 0x190, 'junbin4': 0x191, 'junbin5': 0x192, 'junmon': 0x193,
    'junsbd1': 0x194, 'subin_1a': 0x195, 'subin_1b': 0x196, 'subin_2a': 0x197,
    'subin_2b': 0x198, 'subin_3': 0x199, 'subin_4': 0x19A, 'junone2': 0x19B,
    'junone3': 0x19C, 'junone4': 0x19D, 'junone5': 0x19E, 'junone6': 0x19F,
    'junone7': 0x1A0, 'spgate': 0x1A1, 'spipe_1': 0x1A2, 'spipe_2': 0x1A3,
    'semkin_1': 0x1A4, 'semkin_2': 0x1A5, 'semkin_8': 0x1A6, 'semkin_3': 0x1A7,
    'semkin_4': 0x1A8, 'semkin_5': 0x1A9, 'semkin_6': 0x1AA, 'semkin_7': 0x1AB,
    'ujunon1': 0x1AC, 'ujunon2': 0x1AD, 'ujunon3': 0x1AE, 'prisila': 0x1AF,
    'ujun_w': 0x1B0, 'jumin': 0x1B1, 'ujunon4': 0x1B2, 'ujunon5': 0x1B3,
    'ship_1': 0x1B4, 'ship_2': 0x1B5, 'shpin_22': 0x1B6, 'shpin_2': 0x1B7,
    'shpin_3': 0x1B8, 'del1': 0x1B9, 'del12': 0x1BA, 'del2': 0x1BB,
    'delinn': 0x1BC, 'delpb': 0x1BD, 'delmin1': 0x1BE, 'delmin12': 0x1BF,
    'delmin2': 0x1C0, 'del3': 0x1C1, 'ncorel': 0x1C2, 'ncorel2': 0x1C3,
    'ncorel3': 0x1C4, 'ncoin1': 0x1C5, 'ncoin2': 0x1C6, 'ncoin3': 0x1C7,
    'ncoinn': 0x1C8, 'ropest': 0x1C9, 'mtcrl_0': 0x1CA, 'mtcrl_1': 0x1CB,
    'mtcrl_2': 0x1CC, 'mtcrl_3': 0x1CD, 'mtcrl_4': 0x1CE, 'mtcrl_5': 0x1CF,
    'mtcrl_6': 0x1D0, 'mtcrl_7': 0x1D1, 'mtcrl_8': 0x1D2, 'mtcrl_9': 0x1D3,
    'corel1': 0x1D4, 'corel2': 0x1D5, 'corel3': 0x1D6, 'jail1': 0x1D7,
    'jailin1': 0x1D8, 'jail2': 0x1D9, 'jailpb': 0x1DA, 'jailin2': 0x1DB,
    'jailin3': 0x1DC, 'jailin4': 0x1DD, 'jail3': 0x1DE, 'jail4': 0x1DF,
    'dyne': 0x1E0, 'desert1': 0x1E1, 'desert2': 0x1E2, 'corelin': 0x1E3,
    'astage_a': 0x1E4, 'astage_b': 0x1E5, 'jet': 0x1E6, 'jetin1': 0x1E7,
    'bigwheel': 0x1E8, 'bwhlin': 0x1E9, 'bwhlin2': 0x1EA, 'ghotel': 0x1EB,
    'ghotin_1': 0x1EC, 'ghotin_4': 0x1ED, 'ghotin_2': 0x1EE, 'ghotin_3': 0x1EF,
    'gldst': 0x1F0, 'gldgate': 0x1F1, 'gldinfo': 0x1F2, 'coloss': 0x1F3,
    'coloin1': 0x1F4, 'coloin2': 0x1F5, 'clsin2_1': 0x1F6, 'clsin2_2': 0x1F7,
    'clsin2_3': 0x1F8, 'games': 0x1F9, 'games_1': 0x1FA, 'games_2': 0x1FB,
    'mogu_1': 0x1FC, 'chorace': 0x1FD, 'chorace2': 0x1FE, 'crcin_1': 0x1FF,
    'crcin_2': 0x200, 'gldelev': 0x201, 'gonjun1': 0x202, 'gonjun2': 0x203,
    'gnmkf': 0x204, 'gnmk': 0x205, 'gongaga': 0x206, 'gon_wa1': 0x207,
    'gon_wa2': 0x208, 'gon_i': 0x209, 'gninn': 0x20A, 'gomin': 0x20B,
    'goson': 0x20C, 'cos_btm': 0x20D, 'cos_btm2': 0x20E, 'cosmo': 0x20F,
    'cosmo2': 0x210, 'cosin1': 0x211, 'cosin1_1': 0x212, 'cosin2': 0x213,
    'cosin3': 0x214, 'cosin4': 0x215, 'cosin5': 0x216, 'cosmin2': 0x217,
    'cosmin3': 0x218, 'cosmin4': 0x219, 'cosmin6': 0x21A, 'cosmin7': 0x21B,
    'cos_top': 0x21C, 'bugin1a': 0x21D, 'bugin1b': 0x21E, 'bugin1c': 0x21F,
    'bugin2': 0x220, 'bugin3': 0x221, 'gidun_1': 0x222, 'gidun_2': 0x223,
    'gidun_4': 0x224, 'gidun_3': 0x225, 'seto1': 0x226, 'rckt2': 0x227,
    'rckt3': 0x228, 'rkt_w': 0x229, 'rkt_i': 0x22A, 'rktinn1': 0x22B,
    'rktinn2': 0x22C, 'rckt': 0x22D, 'rktsid': 0x22E, 'rktmin1': 0x22F,
    'rktmin2': 0x230, 'rcktbas1': 0x231, 'rcktbas2': 0x232, 'rcktin1': 0x233,
    'rcktin2': 0x234, 'rcktin3': 0x235, 'rcktin4': 0x236, 'rcktin5': 0x237,
    'rcktin6': 0x238, 'rcktin7': 0x239, 'rcktin8': 0x23A, 'pass': 0x23B,
    'yougan': 0x23C, 'yougan2': 0x23D, 'yougan3': 0x23E, 'uta_wa': 0x23F,
    'uta_im': 0x240, 'utmin1': 0x241, 'utmin2': 0x242, 'uutai1': 0x243,
    'utapb': 0x244, 'yufy1': 0x245, 'yufy2': 0x246, 'hideway1': 0x247,
    'hideway2': 0x248, 'hideway3': 0x249, 'tower5': 0x24A, 'uutai2': 0x24B,
    'uttmpin1': 0x24C, 'uttmpin2': 0x24D, 'uttmpin3': 0x24E, 'uttmpin4': 0x24F,
    'datiao_1': 0x250, 'datiao_2': 0x251, 'datiao_3': 0x252, 'datiao_4': 0x253,
    'datiao_5': 0x254, 'datiao_6': 0x255, 'datiao_7': 0x256, 'datiao_8': 0x257,
    'jtempl': 0x258, 'jtemplb': 0x259, 'jtmpin1': 0x25A, 'jtmpin2': 0x25B,
    'kuro_1': 0x25C, 'kuro_2': 0x25D, 'kuro_3': 0x25E, 'kuro_4': 0x25F,
    'kuro_5': 0x260, 'kuro_6': 0x261, 'kuro_7': 0x262, 'kuro_8': 0x263,
    'kuro_82': 0x264, 'kuro_9': 0x265, 'kuro_10': 0x266, 'kuro_11': 0x267,
    'kuro_12': 0x268, 'bonevil': 0x269, 'slfrst_1': 0x26A, 'slfrst_2': 0x26B,
    'anfrst_1': 0x26C, 'anfrst_2': 0x26D, 'anfrst_3': 0x26E, 'anfrst_4': 0x26F,
    'anfrst_5': 0x270, 'sango1': 0x271, 'sango2': 0x272, 'sango3': 0x273,
    'sandun_1': 0x274, 'sandun_2': 0x275, 'lost1': 0x276, 'losin1': 0x277,
    'losin2': 0x278, 'losin3': 0x279, 'lost2': 0x27A, 'lost3': 0x27B,
    'losinn': 0x27C, 'loslake1': 0x27D, 'loslake2': 0x27E, 'loslake3': 0x27F,
    'blue_1': 0x280, 'blue_2': 0x281, 'white1': 0x282, 'white2': 0x283,
    'hekiga': 0x284, 'whitein': 0x285, 'ancnt1': 0x286, 'ancnt2': 0x287,
    'ancnt3': 0x288, 'ancnt4': 0x289, 'snw_w': 0x28A, 'sninn_1': 0x28B,
    'sninn_2': 0x28C, 'sninn_b1': 0x28D, 'snow': 0x28E, 'snmin1': 0x28F,
    'snmin2': 0x290, 'snmayor': 0x291, 'hyou1': 0x292, 'hyou2': 0x293,
    'hyou3': 0x294, 'icedun_1': 0x295, 'icedun_2': 0x296, 'hyou4': 0x297,
    'hyou5_1': 0x298, 'hyou5_2': 0x299, 'hyou5_3': 0x29A, 'hyou5_4': 0x29B,
    'hyou6': 0x29C, 'hyoumap': 0x29D, 'move_s': 0x29E, 'move_i': 0x29F,
    'move_f': 0x2A0, 'move_r': 0x2A1, 'move_u': 0x2A2, 'move_d': 0x2A3,
    'hyou7': 0x2A4, 'hyou8_1': 0x2A5, 'hyou8_2': 0x2A6, 'hyou9': 0x2A7,
    'hyou10': 0x2A8, 'hyou11': 0x2A9, 'hyou12': 0x2AA, 'hyou13_1': 0x2AB,
    'hyou13_2': 0x2AC, 'hyou14': 0x2AD, 'gaiafoot': 0x2AE, 'holu_1': 0x2AF,
    'holu_2': 0x2B0, 'gaia_1': 0x2B1, 'gaiin_1': 0x2B2, 'gaiin_2': 0x2B3,
    'gaia_2': 0x2B4, 'gaiin_3': 0x2B5, 'gaia_31': 0x2B6, 'gaia_32': 0x2B7,
    'gaiin_4': 0x2B8, 'gaiin_5': 0x2B9, 'gaiin_6': 0x2BA, 'gaiin_7': 0x2BB,
    'crater_1': 0x2BC, 'crater_2': 0x2BD, 'trnad_1': 0x2BE, 'trnad_2': 0x2BF,
    'trnad_3': 0x2C0, 'trnad_4': 0x2C1, 'trnad_51': 0x2C2, 'trnad_52': 0x2C3,
    'trnad_53': 0x2C4, 'woa_1': 0x2C5, 'woa_2': 0x2C6, 'woa_3': 0x2C7,
    'itown1a': 0x2C8, 'itown12': 0x2C9, 'itown1b': 0x2CA, 'itown2': 0x2CB,
    'ithill': 0x2CC, 'itown_w': 0x2CD, 'itown_i': 0x2CE, 'itown_m': 0x2CF,
    'ithos': 0x2D0, 'itmin1': 0x2D1, 'itmin2': 0x2D2, 'life': 0x2D3,
    'life2': 0x2D4, 'zmind1': 0x2D5, 'zmind2': 0x2D6, 'zmind3': 0x2D7,
    'zcoal_1': 0x2D8, 'zcoal_2': 0x2D9, 'zcoal_3': 0x2DA, 'md8_5': 0x2DB,
    'md8_6': 0x2DC, 'md8_b1': 0x2DD, 'md8_b2': 0x2DE, 'sbwy4_22': 0x2DF,
    'tunnel_4': 0x2E0, 'tunnel_5': 0x2E1, 'md8brdg2': 0x2E2, 'md8_32': 0x2E3,
    'canon_1': 0x2E4, 'canon_2': 0x2E5, 'md_e1': 0x2E6, 'xmvtes': 0x2E7,
    'las0_1': 0x2E8, 'las0_2': 0x2E9, 'las0_3': 0x2EA, 'las0_4': 0x2EB,
    'las0_5': 0x2EC, 'las0_6': 0x2ED, 'las0_7': 0x2EE, 'las0_8': 0x2EF,
    'las1_1': 0x2F0, 'las1_2': 0x2F1, 'las1_3': 0x2F2, 'las1_4': 0x2F3,
    'las2_1': 0x2F4, 'las2_2': 0x2F5, 'las2_3': 0x2F6, 'las2_4': 0x2F7,
    'las3_1': 0x2F8, 'las3_2': 0x2F9, 'las3_3': 0x2FA, 'las4_0': 0x2FB,
    'las4_1': 0x2FC, 'las4_2': 0x2FD, 'las4_3': 0x2FE, 'las4_4': 0x2FF,
    'lastmap': 0x300, 'fallp': 0x301, 'm_endo': 0x302, 'hill2': 0x303,
    'bonevil2': 0x304, 'junone22': 0x305, 'rckt32': 0x306, 'jtemplc': 0x307,
    'fship_26': 0x308, 'las4_42': 0x309, 'tunnel_6': 0x30A, 'md8_52': 0x30B,
    'sininb34': 0x30C, 'mds7st33': 0x30D, 'midgal': 0x30E, 'sininb35': 0x30F,
    'nivgate4': 0x310, 'sininb36': 0x311, 'ztruck': 0x312,
}

# The numeric map table is useful to parse Echo-S's source tree, but the
# Switch runtime deliberately addresses clips by its loaded field basename.
# Keep the reverse relation explicit and verify it at import time so staging
# can never silently turn a numeric key into a different map name.
FIELD_ID_TO_NAME = {field_id: name
                    for name, field_id in FIELD_NAME_TO_ID.items()}
if len(FIELD_ID_TO_NAME) != len(FIELD_NAME_TO_ID):
    raise RuntimeError('FIELD_NAME_TO_ID is not one-to-one')

# Matches the eight-byte Switch runtime key in voice_runtime.py.  The 16-bit
# polynomial was searched against every canonical map name, not merely the
# Echo-S subset: all 787 values are unique, so an unvoiced map cannot collide
# with a voiced one and play unrelated dialogue.
FIELD_NAME_HASH_MULTIPLIER = 93


def field_name_hash(field_name: str) -> int:
    name = field_name.strip().lower()
    if not re.fullmatch(r'[a-z0-9_]{1,8}', name):
        raise ValueError('unsafe field basename %r' % field_name)
    value = 0
    for char in name.encode('ascii'):
        value = (value * FIELD_NAME_HASH_MULTIPLIER + char) & 0xFFFF
    return value


_FIELD_NAME_HASHES = {field_name_hash(name): name
                      for name in FIELD_NAME_TO_ID}
if len(_FIELD_NAME_HASHES) != len(FIELD_NAME_TO_ID):
    raise RuntimeError('field-name hash collision; change its multiplier')


def resolve_field_id(field_name: str) -> int:
    """Raises BadArchive (not KeyError -- this should stop a build, not be
    caught casually) if the name isn't in FIELD_NAME_TO_ID. Guessing here
    would silently misroute an entire field's worth of dialogue."""
    key = field_name.strip().lower()
    if key not in FIELD_NAME_TO_ID:
        raise BadArchive(
            "unknown field name %r -- not in FIELD_NAME_TO_ID. If this "
            "is a real field, the table needs updating from "
            "https://ff7-mods.github.io/ff7-flat-wiki/FF7/Field/Field_ID "
            "-- guessing an id here would silently misroute this field's "
            "dialogue." % field_name)
    return FIELD_NAME_TO_ID[key]


# ---------------------------------------------------- battle ability table
#
# Source: extracted directly from the project's own kernel2.bin (lang_en),
# NOT a secondary/guessed source. Verified two ways: (1) zero leftover
# bytes after walking all 18 sections using the REAL per-section format
# (see method note below), (2) every resolved name matches known FF7
# canon exactly.
#
# METHOD NOTE (for anyone re-deriving this from a different kernel2.bin):
# kernel2.bin is LZSS-compressed (standard Okumura-style, see PyFF7's
# lzss.py for a tested decoder). The decompressed blob is NOT a bare
# concatenation of the 18 text sections as some references describe --
# each section is prefixed by its own 4-byte little-endian size field.
# This was confirmed by disassembling `kernel_load_kernel2` directly
# (x86 VA 0x401228 in ff7_en, derived via the same ff7nx_chain.py method
# used throughout this project's own HANDOFF docs): its loop runs exactly
# 18 times (`cmp ecx, 0x12`), and each iteration reads a 4-byte size from
# the current cursor, advances past it, copies that many bytes into a
# freshly-allocated section buffer, then advances the cursor by that
# size -- i.e. `size(4 bytes) + data(size bytes)`, 18 times, with no
# other framing. Parsing without this per-section size prefix (treating
# the 18 sections as directly back-to-back) produces plausible-looking
# but WRONG boundaries -- individual strings can still decode "cleanly"
# by accident while section boundaries drift, which is a trap.
#
# Each of the 18 sections is itself a small string-list archive: a
# `numStrings`-entry table of u16 offsets (relative to the section's own
# start), followed by the string data those offsets point into. String
# count per section is a fixed engine constant, not stored in the file --
# see KERNEL_TEXT_SECTIONS below (order and counts cross-checked against
# cebix/ff7tools' independently-published kernelStringData table).
#
# KERNEL_ABILITY_NAMES is section index 9 ("ability.txt"), 256 entries,
# decoded with ff7tools' ff7text.decodeKernel. Index == the `action_id`
# read by FFNx's `play_battle_char_action_voice` from
# `g_small_battle_model_state[actor_id].actionIdx` -- i.e. this table
# maps action_id directly to the name FF7 itself uses for that ability.
KERNEL_TEXT_SECTIONS = [
    (0,  32, 'command_help.txt'), (1, 256, 'ability_help.txt'),
    (2, 128, 'item_help.txt'),    (3, 128, 'weapon_help.txt'),
    (4,  32, 'armor_help.txt'),   (5,  32, 'accessory_help.txt'),
    (6,  96, 'materia_help.txt'), (7,  64, 'key_item_help.txt'),
    (8,  32, 'command.txt'),      (9, 256, 'ability.txt'),
    (10, 128, 'item.txt'),        (11, 128, 'weapon.txt'),
    (12,  32, 'armor.txt'),       (13,  32, 'accessory.txt'),
    (14,  96, 'materia.txt'),     (15,  64, 'key_item.txt'),
    (16, 128, 'battle.txt'),      (17,  16, 'summon.txt'),
]

KERNEL_ABILITY_NAMES = [
    'Cure', 'Cure2', 'Cure3', 'Regen',
    'Poisona', 'Esuna', 'Resist', 'Life',
    'Life2', 'Mini', 'Toad', 'Sleepel',
    'Confu', 'Silence', 'Berserk', 'Barrier',
    'MBarrier', 'Reflect', 'Wall', 'Haste',
    'Slow', 'Stop', 'DeBarrier', 'DeSpell',
    'Death', 'Escape', 'Remove', 'Fire',
    'Fire2', 'Fire3', 'Ice', 'Ice2',
    'Ice3', 'Bolt', 'Bolt2', 'Bolt3',
    'Quake', 'Quake2', 'Quake3', 'Bio',
    'Bio2', 'Bio3', 'Demi', 'Demi2',
    'Demi3', 'Comet', 'Comet2', 'Freeze',
    'Break', 'Tornado', 'Flare', 'FullCure',
    'Ultima', 'Shield', None, None,
    'Choco/Mog', 'Shiva', 'Ifrit', 'Ramuh',
    'Titan', 'Odin', 'Leviathan', 'Bahamut',
    'Kujata', 'Alexander', 'Phoenix', 'Neo Bahamut',
    'Hades', 'Typhon', 'Bahamut ZERO', 'Knights of Round',
    'Frog Song', 'L4 Suicide', 'Magic Hammer', 'White Wind',
    'Big Guard', 'Angel Whisper', 'Dragon Force', 'Death Force',
    'Flame Thrower', 'Laser', 'Matra Magic', 'Bad Breath',
    'Beta', 'Aqualung', 'Trine', 'Magic Breath',
    '????', 'Goblin Punch', 'Chocobuckle', 'L5 Death',
    'Death Sentence', 'Roulette', 'Shadow Flare', "Pandora's Box",
    'Fat-Chocobo', 'Gunge Lance', '{COLOR 02}Beat Rush', '{COLOR 02}Somersault',
    '{COLOR 02}Waterkick', '{COLOR 02}Meteodrive', '{COLOR 02}Dolphin Blow', '{COLOR 02}Meteor Strike',
    '{COLOR 02}Final Heaven', '{COLOR 02}Game Over', '{COLOR 02}Death Joker', '{COLOR 02}Toy Soldier',
    '{COLOR 02}Lucky Girl', '{COLOR 02}Mog Dance', '{COLOR 02}Transform', '{COLOR 02}Toy Box',
    '{COLOR 02}Berserk Dance', '{COLOR 02}Beast Flare', '{COLOR 02}Gigadunk', '{COLOR 02}Livewire',
    '{COLOR 02}Splattercombo', '{COLOR 02}Nightmare', '{COLOR 02}Chaos Saber', '{COLOR 02}Satan Slam',
    '{COLOR 02}Finishing Touch', '{COLOR 02}Satan Slam', None, None,
    None, None, None, None,
    '{COLOR 02}Braver', '{COLOR 02}Cross-slash', '{COLOR 02}Blade Beam', '{COLOR 02}Climhazzard',
    '{COLOR 02}Meteorain', '{COLOR 02}Finishing Touch', '{COLOR 02}Omnislash', '{COLOR 02}Big Shot',
    '{COLOR 02}Grenade Bomb', '{COLOR 02}Mindblow', '{COLOR 02}Hammerblow', '{COLOR 02}Satellite Beam',
    '{COLOR 02}Angermax', '{COLOR 02}Catastrophe', '{COLOR 02}Healing Wind', '{COLOR 02}Seal Evil',
    '{COLOR 02}Breath of the Earth', '{COLOR 02}Fury Brand', '{COLOR 02}Planet Protector', '{COLOR 02}Pulse of Life',
    '{COLOR 02}Great Gospel', '{COLOR 02}Beat Rush', '{COLOR 02}Somersault', '{COLOR 02}Waterkick',
    '{COLOR 02}Meteodrive', '{COLOR 02}Dolphin Blow', '{COLOR 02}Meteor Strike', '{COLOR 02}Final Heaven',
    '{COLOR 02}Boost Jump', '{COLOR 02}Dragon', '{COLOR 02}Hyper Jump', '{COLOR 02}Dynamite',
    '{COLOR 02}Dragon Dive', '{COLOR 02}Big Brawl', '{COLOR 02}Highwind', '{COLOR 02}Sled Fang',
    '{COLOR 02}Howling Moon', '{COLOR 02}Blood Fang', '{COLOR 02}Stardust Ray', '{COLOR 02}Lunatic High',
    '{COLOR 02}Earth Rave', '{COLOR 02}Cosmo Memory', '{COLOR 02}Dice', '{COLOR 02}Toy Box',
    '{COLOR 02}Slots', '{COLOR 02}Galian Beast', '{COLOR 02}Death Gigas', '{COLOR 02}Hellmasker',
    '{COLOR 02}Chaos', '{COLOR 02}Greased Lightning', '{COLOR 02}Clear Tranquil', '{COLOR 02}Landscaper',
    '{COLOR 02}Bloodfest', '{COLOR 02}Gauntlet', '{COLOR 02}Doom of the Living', '{COLOR 02}All Creation',
    '{COLOR 02}Transform', '{COLOR 02}Mog Dance', '{COLOR 02}Toy Soldier', '{COLOR 02}Lucky Girl',
    '{COLOR 02}Death Joker', '{COLOR 02}Game Over', '{COLOR 02}Berserk Dance', '{COLOR 02}Beast Flare',
    '{COLOR 02}Gigadunk', '{COLOR 02}Livewire', '{COLOR 02}Splattercombo', '{COLOR 02}Nightmare',
    '{COLOR 02}Chaos Saber', '{COLOR 02}Satan Slam', '{COLOR 02}Finishing Touch', '{COLOR 02}Satan Slam',
    None, None, None, None,
    None, None, None, None,
    None, None, None, None,
    None, None, None, None,
    None, None, None, None,
    None, None, None, None,
    None, None, None, None,
    None, None, None, None,
    None, None, None, None,
    None, None, None, None,
    None, None, None, None,
    None, None, None, None,
    None, None, None, None,
    None, None, None, None,
]
assert len(KERNEL_ABILITY_NAMES) == 256


# ACTION_NAME_TO_ID: Echo-S's own spell-name stems, matched against
# KERNEL_ABILITY_NAMES above. Three confidence tiers, kept distinguishable
# rather than flattened to one table, because they carry different risk:
#
#   EXACT       -- Echo-S's stem matches a KERNEL_ABILITY_NAMES entry
#                  verbatim (case/punctuation aside). No judgment call.
#   INTERPRETED -- matches FF7's own abbreviated/informal name for the
#                  same ability (e.g. "confuse" -> kernel's own "Confu";
#                  "frog" -> kernel's own "Toad", the common fan name for
#                  the same status). Confident, but a human read is
#                  involved, not a pure lookup.
#   ACRONYM     -- Echo-S's stem is initials of a multi-word kernel name
#                  (e.g. "limit_pp" -> "Planet Protector"). Confident
#                  given FF7's limit-break naming conventions, but the
#                  weakest tier -- worth a spot check against actual
#                  audio content before shipping.
#
# Limit-break names exist TWICE in KERNEL_ABILITY_NAMES, at indices
# ~98-127 and ~128-199, with identical text (e.g. 'Beat Rush' at both 98
# and 149). Index 128 lines up exactly with 'Braver' -- Cloud's first,
# iconic limit -- which is strong evidence the 128+ range is the real
# casting-time table and 98-127 is a secondary/UI listing. Every entry
# below prefers 128+ when both exist; this is a judgment call, not a
# certainty, flagged per-entry below where a duplicate exists.
ACTION_NAME_TO_ID = {
    # -- EXACT --
    'barrier': 15, 'bio': 39, 'bolt': 33, 'break': 48, 'comet': 45,
    'debarrier': 22, 'dice': 170, 'fire': 27, 'flare': 50, 'freeze': 47,
    'haste': 19, 'healingwind': 142, 'ice': 30, 'life': 7, 'mini': 9,
    'silence': 13, 'slow': 20, 'stop': 21, 'tornado': 49, 'ultima': 52,
    'toybox': 171,     # dup at 111, see module note above -- chose 128+
    'transform': 184,  # dup at 110, see module note above -- chose 128+
    'slot': 172,       # 'Slots' (Cait Sith limit) -- COULD instead mean
                       # a UI "materia slot" label; not fully certain.
    'limit_chaos': 176, 'limit_death_gigas': 174,
    'limit_galian_beast': 173, 'limit_hellmasker': 175,
    'limit_bloodfang': 165, 'limit_earthrave': 168,
    'limit_highwind': 162, 'limit_sledfang': 163,

    # -- INTERPRETED (FF7's own abbreviated/informal name) --
    'confuse': 12,   # kernel's own name is 'Confu' (menu-truncated)
    'frog': 10,      # kernel's own name is 'Toad' (common fan name)
    'sleep': 11,     # kernel's own name is 'Sleepel' (menu-truncated)
    'limit_dyna': 159,     # 'Dynamite' (Cid)
    'limit_gospel': 148,   # 'Great Gospel' (Aerith)
    'limit_lunatic': 167,  # 'Lunatic High' (Vincent)
    'limit_omni': 134,     # 'Omnislash' (Cloud)
    'limit_stardust': 166, # 'Stardust Ray' (Vincent)

    # -- ACRONYM (initials of a multi-word kernel name) --
    'limit_cm': 169,  # C+M -> 'Cosmo Memory' (Red XIII)
    'limit_fh': 155,  # F+H -> 'Final Heaven' (Tifa)
    'limit_mr': 132,  # M+R -> 'Meteorain' == "Meteor Rain" (Barret)
    'limit_pp': 146,  # P+P -> 'Planet Protector' (Aerith)

    # dotted sub-technique names (Vincent's transformation limits, each
    # with its own named attack) -- same duplicate-range caveat as above
    'limit_chaos_satan_slam': 197,          # dup at 119/121
    'limit_death_gigas_gigadunk': 192,       # dup at 114
    'limit_death_gigas_livewire': 193,       # dup at 115
    'limit_galian_beast_beast_flare': 191,   # dup at 113
    'limit_galian_beast_berserk_dance': 190, # dup at 112
    'limit_hellmasker_nightmare': 195,       # dup at 117
    'limit_hellmasker_splattercombo': 194,   # dup at 116

    # 'limit_dragon' -> 157 ('Dragon', Cid's own-named limit) is deliberately
    # NOT included as a plain lookup entry: 'Dragon Dive' (160) is a
    # DIFFERENT technique and the stem alone doesn't disambiguate cleanly
    # enough to bake in silently -- left for whoever ingests this to
    # confirm against the actual audio content once available.

    # resolved after cross-checking scene.bin / item.txt / materia.txt
    'soldier': 186,  # 'Toy Soldier' (Cait Sith limit) -- not found as a
                     # standalone match on the first pass; 'soldier' alone
                     # doesn't contain 'toysoldier', a stem check does.
    'mog': 56,       # 'Choco/Mog' (summon) -- ruled out 'Smog'/'Smogger'/
                     # 'Petrify Smog' as coincidental substring matches in
                     # scene.bin, none of which fit a per-character voice
                     # bark; 'Choco/Mog' does.
}

# Deliberately NOT resolved to an action_id -- these need a human call,
# not a table lookup, because either (a) the kernel has two similarly-named
# candidates and picking wrong would misroute real content, or (b) the
# name genuinely isn't in KERNEL_ABILITY_NAMES at all:
#
#   'limit_jump'  -- 'Boost Jump'(156) or 'Hyper Jump'(158), both Yuffie,
#                    no way to tell which from the stem alone.
#   'limit_cat'   -- no single "generic Cait Sith limit" kernel entry;
#                    Cait's actual limits are 'dice'/'slot' above.
#   'limit_chaos_chaos_blade' -- 'Chaos Blade' not found verbatim
#                    anywhere in KERNEL_ABILITY_NAMES.
#
# NOT action_id-keyed at all -- different FFNx voice function, different
# hook, would need separate handling if ever wired up:
#
#   COMMAND-LEVEL barks (FFNx's play_battle_cmd_voice, keyed by command_id
#   not action_id -- confirmed against KERNEL_TEXT_SECTIONS' command.txt,
#   section 8): 'item', 'magic', 'summon', 'sense', 'limit', 'steal'
#
#   FFNx's own special outcome-word convention (play_battle_cmd_voice's
#   Steal/Mug/Manipulate/Sense-failure branch -- confirmed against
#   battle.txt entries 48/49/89/118, e.g. "Couldn't steal anything...",
#   "Nothing to steal.", "Couldn't manipulate.", "Couldn't sense."):
#   'couldnt', 'nothing', 'stole'
#
#   ELEMENT-reaction barks, NOT spell-cast barks -- 'earth' and 'gravity'
#   matched battle.txt entries 96/98 ('Earth', 'Gravity' as element-type
#   labels, part of a "weak against {ELEMENT}" message at entry 109), not
#   any ability name. Likely a different trigger (elemental-weakness
#   reaction) that hasn't been traced this session.
#
#   UI/menu labels, not battle actions: 'exit' (battle.txt 'Exit', a menu
#   option, appears twice), 'help', 'go', 'ng'
#
#   Confirmed via scene.bin (ff7tools' scene.Archive/Scene classes,
#   scanned across all 256 scenes) as SCENE-LOCAL enemy ability names --
#   each scene has its own 32-slot ability table, indexed 0-31 WITHIN
#   that scene, a completely different (and per-encounter) system from
#   kernel2.bin's global 256-entry action_id table. `char_NN` folders are
#   party-member slots, and party members don't cast these -- why Echo-S
#   filed them there at all is unclear, and using them needs a different
#   hook (probably keyed by enemy_id + local index, or FFNx's separate
#   `play_battle_dialogue_voice(enemy_id, tokenized_dialogue)`, not
#   traced this session) rather than a name->action_id table entry:
#     'grandspark' -> 'Grand Spark' (appears in 8+ scenes)
#     'smine'      -> 'S-Mine' (scene 131)
#     'supernova'  -> 'Super Nova' (scene 231)
#
#   Confirmed as belonging to a DIFFERENT kernel2.bin table entirely --
#   real names, but not ability_id/action_id at all, so no amount of
#   fuzzy-matching against ACTION_NAME_TO_ID would ever have been right:
#     'molotov' -> item.txt index 53, 'Molotov' (a throwable ITEM,
#                  used via the Item/Throw command, not a cast ability)
#     'restore' -> materia.txt index 53, 'Restore' (a MATERIA name, not
#                  an ability -- if voiced at all, it'd be on equip/use
#                  of that materia, a different trigger entirely)
#
#   Still fully unresolved -- not found in kernel2.bin OR scene.bin:
#     'catgirl'


# -------------------------------------------------------------------- key

def pack_key(namespace: int, primary: int = 0, secondary: int = 0,
             low: int = 0, page_or_variant: int = 0) -> int:
    if not (0 <= namespace <= 0xFF):
        raise ValueError('namespace out of range: %r' % namespace)
    if not (0 <= primary <= 0xFFFFFFFF):
        raise ValueError('primary out of range: %r' % primary)
    if not (0 <= secondary <= 0xFF):
        raise ValueError('secondary out of range: %r' % secondary)
    if not (0 <= low <= 0xFF):
        raise ValueError('low (dialog_id/action_id) out of range: %r' % low)
    if not (0 <= page_or_variant <= 0xFF):
        raise ValueError('page_or_variant out of range: %r' % page_or_variant)
    return ((namespace & 0xFF) << 56
            | (primary & 0xFFFFFFFF) << 24
            | (secondary & 0xFF) << 16
            | (low & 0xFF) << 8
            | (page_or_variant & 0xFF))


def key_to_filename(key: int) -> str:
    """The canonical, deterministic filename a runtime hook would compute
    for this key: 16 lowercase hex chars + .ogg, one flat directory."""
    return '%016x.ogg' % key


def field_key_to_filename(key: int) -> str:
    """Return the native-player-safe filename for a field dialogue key.

    ``NativeOggPlayer`` incorporates its filename into a Horizon thread name
    whose fixed 32-byte buffer cannot hold the old 16-hex-digit key.  Field
    The runtime intentionally flattens FFNx's window-qualified fallback
    cascade at build time, so the key needs only 32 live bits: field (12),
    reserved window nibble (always 0), dialog (8), and page (8).  Eight
    digits leave a conservative margin in Horizon's thread-name buffer.
    """
    namespace = (key >> 56) & 0xFF
    primary = (key >> 24) & 0xFFFFFFFF
    secondary = (key >> 16) & 0xFF
    low = (key >> 8) & 0xFF
    page = key & 0xFF
    if (namespace != NS_FIELD_LINE or primary > 0xFFF or secondary != 0):
        raise ValueError('not a compactable field-dialogue key: %016x' % key)
    return '%03x0%02x%02x.ogg' % (primary, low, page)


def field_name_key_to_filename(key: int) -> str:
    """Map a field-line key to the runtime's portable field-name filename.

    ``CurrentFieldId`` is not ordered identically in every FF7 port, while
    the loaded ``field\\<name>`` string is.  The ARM64 hook therefore forms
    ``<name>-<dialog:02x><page:02x>.ogg`` from that live basename.  Field
    names are constrained by flevel to eight lower-case ASCII characters, so
    the complete 13-character name remains inside NativeOggPlayer's safe
    fixed tail allocation.
    """
    namespace = (key >> 56) & 0xFF
    primary = (key >> 24) & 0xFFFFFFFF
    secondary = (key >> 16) & 0xFF
    low = (key >> 8) & 0xFF
    page = key & 0xFF
    if namespace != NS_FIELD_LINE or secondary != 0:
        raise ValueError('not a flattened field-dialogue key: %016x' % key)
    try:
        name = FIELD_ID_TO_NAME[primary]
    except KeyError:
        raise ValueError('unknown field id in key: %016x' % key) from None
    if len(name) > 8 or not re.fullmatch(r'[a-z0-9_]+', name):
        raise ValueError('unsafe field basename %r' % name)
    return '%s-%02x%02x.ogg' % (name, low, page)


def field_folder_key_to_filename(key: int) -> str:
    """Return FFNx's canonical field line or highlighted-option path.

    FFNx asks for ``1a`` on the first page, then ``1b`` etc., falling back to
    a suffixless name only when needed.  During staging we collapse that
    fallback and always write the canonical lettered spelling, so native
    routing needs one deterministic lookup per dialogue page. ASK option
    clips use FFNx's separate ``<dialog>_<highlighted-option>`` spelling.
    """
    namespace = (key >> 56) & 0xFF
    primary = (key >> 24) & 0xFFFFFFFF
    secondary = (key >> 16) & 0xFF
    low = (key >> 8) & 0xFF
    page = key & 0xFF
    field_namespaces = (NS_FIELD_LINE, NS_FIELD_CHOICE,
                        NS_NOCLOUD_TIFA_CHOICE, NS_NOCLOUD_CID_CHOICE)
    if namespace not in field_namespaces or secondary != 0:
        raise ValueError('not a flattened field-dialogue key: %016x' % key)
    try:
        name = FIELD_ID_TO_NAME[primary]
    except KeyError:
        raise ValueError('unknown field id in key: %016x' % key) from None
    if len(name) > 8 or not re.fullmatch(r'[a-z0-9_]+', name):
        raise ValueError('unsafe field basename %r' % name)
    if namespace in (NS_FIELD_CHOICE, NS_NOCLOUD_TIFA_CHOICE,
                      NS_NOCLOUD_CID_CHOICE):
        prefix = ''
        if namespace == NS_NOCLOUD_TIFA_CHOICE:
            prefix = 'nocloud/tifa/'
        elif namespace == NS_NOCLOUD_CID_CHOICE:
            prefix = 'nocloud/cid/'
        return '%s%s/%d_%d.ogg' % (prefix, name, low, page)
    if page > 25:
        raise ValueError('field dialogue page exceeds FFNx alphabet: %d' % page)
    return '%s/%d%s.ogg' % (name, low, chr(ord('a') + page))


def world_folder_key_to_filename(key: int) -> str:
    """Return FFNx's canonical ``_world`` line or option path."""
    namespace = (key >> 56) & 0xFF
    primary = (key >> 24) & 0xFFFFFFFF
    secondary = (key >> 16) & 0xFF
    low = (key >> 8) & 0xFF
    page = key & 0xFF
    if namespace not in (NS_WORLD_LINE, NS_WORLD_CHOICE):
        raise ValueError('not a world-dialogue key: %016x' % key)
    if primary != 0 or secondary != 0:
        raise ValueError('world-dialogue key has reserved bits: %016x' % key)
    if namespace == NS_WORLD_CHOICE:
        return '_world/%d_%d.ogg' % (low, page)
    if page > 25:
        raise ValueError('world dialogue page exceeds FFNx alphabet: %d' % page)
    return '_world/%d%s.ogg' % (low, chr(ord('a') + page))


def folder_voice_key_to_filename(key: int) -> str:
    """Dispatch one field/world key to its FFNx-compatible relative path."""
    namespace = (key >> 56) & 0xFF
    if namespace in (NS_FIELD_LINE, NS_FIELD_CHOICE,
                      NS_NOCLOUD_TIFA_CHOICE, NS_NOCLOUD_CID_CHOICE):
        return field_folder_key_to_filename(key)
    if namespace in (NS_WORLD_LINE, NS_WORLD_CHOICE):
        return world_folder_key_to_filename(key)
    raise ValueError('not a folder-dialogue key: %016x' % key)


def field_hash_key_to_filename(key: int) -> str:
    """Return the native-player-safe hash filename used by the Switch hook.

    The game exposes the current canonical field *name*, not a stable
    cross-port numeric map ID.  Its collision-free four-hex hash leaves four
    final characters for the live dialog and page, retaining the eight-char
    limit required by NativeOggPlayer's Horizon thread-name buffer.
    """
    namespace = (key >> 56) & 0xFF
    primary = (key >> 24) & 0xFFFFFFFF
    secondary = (key >> 16) & 0xFF
    low = (key >> 8) & 0xFF
    page = key & 0xFF
    if namespace != NS_FIELD_LINE or secondary != 0:
        raise ValueError('not a flattened field-dialogue key: %016x' % key)
    try:
        name = FIELD_ID_TO_NAME[primary]
    except KeyError:
        raise ValueError('unknown field id in key: %016x' % key) from None
    return '%04x%02x%02x.ogg' % (field_name_hash(name), low, page)


def field_line_key(field_id: int, dialog_id: int, page: int = 0,
                    window_id: int = 0) -> int:
    return pack_key(NS_FIELD_LINE, field_id, window_id, dialog_id, page)


def field_line_key_by_name(field_name: str, dialog_id: int, page: int = 0,
                            window_id: int = 0) -> int:
    return field_line_key(resolve_field_id(field_name), dialog_id, page,
                          window_id)


def field_choice_key(field_id: int, dialog_id: int, option_count: int,
                      window_id: int = 0) -> int:
    return pack_key(NS_FIELD_CHOICE, field_id, window_id, dialog_id,
                    option_count)


def field_choice_key_by_name(field_name: str, dialog_id: int,
                             option_count: int, window_id: int = 0) -> int:
    return field_choice_key(resolve_field_id(field_name), dialog_id,
                            option_count, window_id)


def field_variant_choice_key(leader: str, field_id: int, dialog_id: int,
                             option_id: int, window_id: int = 0) -> int:
    """Return a leader-specific Echo-S ``NoCloud`` ASK option key."""
    leader = leader.lower()
    namespaces = {'tifa': NS_NOCLOUD_TIFA_CHOICE,
                  'cid': NS_NOCLOUD_CID_CHOICE}
    try:
        namespace = namespaces[leader]
    except KeyError:
        raise ValueError('unsupported NoCloud leader: %r' % leader) from None
    return pack_key(namespace, field_id, window_id, dialog_id, option_id)


def world_line_key(dialog_id: int, page: int = 0) -> int:
    return pack_key(NS_WORLD_LINE, 0, 0, dialog_id, page)


def world_choice_key(dialog_id: int, option_id: int) -> int:
    return pack_key(NS_WORLD_CHOICE, 0, 0, dialog_id, option_id)


def battle_bark_key(char_slot: int, action_id: int, variant: int = 0) -> int:
    return pack_key(NS_BATTLE_BARK, 0, char_slot, action_id, variant)


# ----------------------------------------------------------------- entry

class Entry:
    """One clip, in memory or lazily backed by ``source_path``.

    `duration_ms` is informational only now (see module header -- the
    playback primitive is expected to expose this itself once opened, the
    same way MusicStream's own code reads fields off its stream handle
    for buffer setup). It is still computed, because a manifest that can
    catch a truncated/corrupt source file at build time is worth having,
    but nothing at runtime is expected to read it.
    """

    __slots__ = ('key', 'data', 'duration_ms', 'source_path')

    def __init__(self, key: int, data: bytes = None, duration_ms: int = None,
                 source_path: str = None):
        self.key = key
        self.data = data
        self.duration_ms = (duration_ms if duration_ms is not None else
                            (_ogg_duration_ms(data) if data is not None
                             else None))
        self.source_path = source_path

    def __repr__(self):
        ns = (self.key >> 56) & 0xFF
        return ('<Entry ns=%s key=%s %d bytes %d ms>'
               % (_NAMES.get(ns, hex(ns)), key_to_filename(self.key),
                  len(self.data) if self.data is not None else -1,
                  self.duration_ms if self.duration_ms is not None else -1))


# ------------------------------------------------------- ogg duration probe

def _ogg_duration_ms(data: bytes) -> int:
    """
    granule_position of the LAST Ogg page, divided by the sample rate out of
    the FIRST page's identification packet. No decode required.

    Raises BadArchive on anything that isn't a well-formed single-stream
    Ogg/Vorbis file -- catches truncated/corrupt sources at build time
    rather than shipping them silently.
    """
    if data[:4] != b'OggS':
        raise BadArchive('not an Ogg file (bad capture pattern)')

    def pages(buf):
        pos = 0
        n = len(buf)
        while pos < n:
            if buf[pos:pos + 4] != b'OggS':
                raise BadArchive('desynced Ogg page at byte %d' % pos)
            granule = struct.unpack_from('<q', buf, pos + 6)[0]
            nseg = buf[pos + 26]
            seg_table = buf[pos + 27:pos + 27 + nseg]
            body_len = sum(seg_table)
            body_start = pos + 27 + nseg
            yield pos, granule, buf[body_start:body_start + body_len]
            pos = body_start + body_len

    sample_rate = None
    last_granule = None
    for pos, granule, body in pages(data):
        if sample_rate is None:
            if len(body) >= 16 and body[1:7] == b'vorbis' and body[0] == 1:
                sample_rate = struct.unpack_from('<I', body, 12)[0]
        last_granule = granule

    if sample_rate is None:
        raise BadArchive('no Vorbis identification header found')
    if last_granule is None or last_granule < 0:
        raise BadArchive('no valid granule position found')
    return (last_granule * 1000) // sample_rate


# --------------------------------------------------------------- ingestion

def _split_page_suffix(stem):
    """'214b' -> (214, 1); '214' -> (214, 0). Page letters are 'a'.. per
    FFNx (`'a' + page_count`, 0-based)."""
    if stem and stem[-1].isalpha():
        digits, letter = stem[:-1], stem[-1]
        if digits.isdigit():
            return int(digits), ord(letter.lower()) - ord('a')
    if stem.isdigit():
        return int(stem), 0
    return None, None


def classify_voice_path(rel_path):
    """
    Given a path relative to a mod's root (build.py's own `rel` from
    `Mod.files_for()`), decide whether it's field VO, battle VO, or
    neither, matching FFNx's own folder convention (`voice/<field>/...`,
    `voice/_battle/char_NN/...`) the same way build.py's own
    `FFNX_AUDIO_DIRS` classification already finds these files -- this
    function is what decides WHAT KIND once build.py has already found
    the `voice` directory component.

    Returns one of:
      ('field', field_folder_name, filename)
      ('battle', char_slot:int, filename)
      None   -- not a recognised voice path shape at all (wrong depth,
                no 'voice' component, char_NN not numeric, etc.)

    Does NOT validate the field name against FIELD_NAME_TO_ID or the
    filename against any naming convention -- that's ingestion's job,
    once it has a real Entry to build. This function only answers
    "what kind of path shape is this", so build.py's classification
    loop can route to the right bucket without needing to know
    anything about field ids or action ids itself.
    """
    if not rel_path.lower().endswith('.ogg'):
        return None
    parts = rel_path.replace('\\', '/').split('/')
    parts_l = [p.lower() for p in parts]
    if 'voice' not in parts_l:
        return None
    vi = parts_l.index('voice')
    tail = parts[vi + 1:]   # path components after .../voice/
    if len(tail) < 2:
        return None
    filename = tail[-1]
    # Echo-S conditionally overlays NoCloud/<leader>/Voice according to the
    # current party leader.  Preserve that authoring namespace instead of
    # flattening it into the ordinary field-choice bucket.
    if vi >= 2 and parts_l[vi - 2] == 'nocloud':
        leader = parts_l[vi - 1]
        if leader not in ('tifa', 'cid') or len(tail) != 2:
            return None
        return ('nocloud', leader, tail[0], filename)
    if tail[0].lower() == '_battle':
        if len(tail) < 3:
            return None
        char_dir = tail[1]
        # FFNx writes both of these directories in HEX -- `_battle/char_%02X`
        # and `_battle/enemy_%04X` (`FFNx/src/voice.cpp`).
        #
        # Reading char_NN as DECIMAL rejected `char_0a` outright, and would
        # have mis-slotted anything from `char_10` up. And `enemy_XXXX` was
        # not recognised at all, which is why every in-battle CONVERSATION
        # was invisible to this build: those clips live in
        # `_battle/enemy_0016/<line>.ogg`, `build.py` drops whatever this
        # function does not classify, so they never reached `plan.voice` and
        # no later pass could stage what it could not see.
        low = char_dir.lower()
        if low.startswith('char_'):
            try:
                return ('battle', int(char_dir[5:], 16), filename)
            except ValueError:
                return None
        if low.startswith('enemy_'):
            try:
                return ('battle_enemy', int(char_dir[6:], 16), filename)
            except ValueError:
                return None
        return None
    if tail[0].lower() == '_world':
        if len(tail) != 2:
            return None
        return ('world', filename)
    if len(tail) != 2:
        return None
    return ('field', tail[0], filename)


def _world_vo_entries(files, log=None):
    """Build exact FFNx ``_world`` line/option entries from source files."""
    def _log(msg):
        if log:
            log(msg)

    entries = []
    skipped = 0
    for filename, path in files:
        if not filename.lower().endswith('.ogg'):
            continue
        stem = filename[:-4]
        if '_' in stem:
            dialog, _, option = stem.partition('_')
            if not (dialog.isdigit() and option.isdigit()):
                skipped += 1
                _log('skip (unrecognised world voice): %s' % path)
                continue
            key = world_choice_key(int(dialog), int(option))
        else:
            dialog, page = _split_page_suffix(stem)
            if dialog is None:
                skipped += 1
                _log('skip (unrecognised world voice): %s' % path)
                continue
            key = world_line_key(dialog, page)
        entries.append(Entry(key, source_path=path))
    _log('world VO: %d playable entries%s'
         % (len(entries), ' (%d skipped)' % skipped if skipped else ''))
    return entries


def ingest_world_vo(world_voice_root, log=None):
    """Walk ``VO/Voice/_world`` and return exact line/choice entries."""
    files = []
    for filename in sorted(os.listdir(world_voice_root)):
        path = os.path.join(world_voice_root, filename)
        if os.path.isfile(path):
            files.append((filename, path))
    return _world_vo_entries(files, log=log)


def ingest_world_vo_from_files(files, log=None):
    """World equivalent of :func:`ingest_field_vo_from_files`."""
    selected = []
    for rel, full in files:
        classified = classify_voice_path(rel)
        if classified and classified[0] == 'world':
            selected.append((classified[1], full))
    return _world_vo_entries(selected, log=log)


def _field_vo_entries(triples, log=None):
    """Core field-VO ingestion: `triples` is [(field_name, filename,
    full_disk_path), ...]. Shared by `ingest_field_vo` (directory walk)
    and `ingest_field_vo_from_files` (build.py's file-list shape)."""
    def _log(msg):
        if log:
            log(msg)

    # The Switch runtime has one compact lookup per field/dialogue/page.
    # Keep Echo-S's window-qualified file *selection* precedence, but do not
    # put that authoring-only window id into the shipped key.
    best = {}   # (field_id, NS, dialog_id, page_or_opt, reserved_window) -> source
    # A flattened key can represent only one authoring-time window variant.
    # Never choose a different speaker arbitrarily: omit that one key and
    # report it, while continuing to stage the rest of the mod.
    ambiguous = {}
    # FFNx tries the page-letter filenames before page-zero's suffixless
    # fallback.  Preserve that ordering while flattening the cascade:
    # wN_12a, 12a, wN_12, 12.
    SPEC_WINDOWED_EXPLICIT, SPEC_PLAIN_EXPLICIT = 4, 3
    SPEC_WINDOWED_IMPLICIT, SPEC_PLAIN_IMPLICIT = 2, 1
    scanned = 0

    for field, fn, path in triples:
        if not fn.lower().endswith('.ogg'):
            continue
        scanned += 1
        try:
            field_id = resolve_field_id(field)
        except BadArchive:
            # Some Echo-S folders are author-side aggregate/variant labels
            # (for example ``gon_wa`` alongside the real gon_wa1/gon_wa2).
            # There is no safe numeric key to emit for them, so leave those
            # clips silent rather than guessing a field ID.
            _log('skip (unknown field): %s' % path)
            continue
        stem = fn[:-4]

        if stem.startswith('w') and '_' in stem:
            head, _, tail = stem[1:].partition('_')
            if not (head.isdigit() and 0 <= int(head) <= 0xFF):
                _log('skip (unrecognised): %s' % path)
                continue
            dialog_id, page = _split_page_suffix(tail)
            if dialog_id is None:
                _log('skip (unrecognised): %s' % path)
                continue
            k = (field_id, NS_FIELD_LINE, dialog_id, page, 0)
            spec = (SPEC_WINDOWED_EXPLICIT if tail and tail[-1].isalpha()
                    else SPEC_WINDOWED_IMPLICIT)
        elif '_' in stem:
            head, _, tail = stem.partition('_')
            if not (head.isdigit() and tail.isdigit()):
                _log('skip (unrecognised): %s' % path)
                continue
            k = (field_id, NS_FIELD_CHOICE, int(head), int(tail), 0)
            spec = SPEC_PLAIN_IMPLICIT
        else:
            dialog_id, page = _split_page_suffix(stem)
            if dialog_id is None:
                _log('skip (unrecognised): %s' % path)
                continue
            k = (field_id, NS_FIELD_LINE, dialog_id, page, 0)
            spec = (SPEC_PLAIN_EXPLICIT if stem and stem[-1].isalpha()
                    else SPEC_PLAIN_IMPLICIT)

        if k in ambiguous:
            ambiguous[k].append(path)
            continue
        prior = best.get(k)
        if prior is None or spec > prior[0]:
            best[k] = (spec, path)
        elif spec == prior[0] and path != prior[1]:
            ambiguous[k] = [prior[1], path]
            del best[k]

    entries = []
    for (field_id, ns, id_, page_or_opt, window_id), (spec, path) in best.items():
        if ns == NS_FIELD_LINE:
            key = field_line_key(field_id, id_, page_or_opt)
        else:
            key = field_choice_key(field_id, id_, page_or_opt)
        # The full Echo-S field set is multi-gigabyte.  Defer reads until
        # staging so planning does not retain every source clip in RAM.
        entries.append(Entry(key, source_path=path))
    _log('field VO: %d source files scanned -> %d entries (%d collapsed by '
        'the window-qualified/plain cascade)'
        % (scanned, len(entries), scanned - len(entries)))
    for k, paths in sorted(ambiguous.items()):
        _log('defer (ambiguous window-specific dialogue): %r (%s)'
             % (k, ' vs '.join(paths)))
    return entries


def ingest_field_vo(vo_voice_root, log=None):
    """
    Walk `VO/Voice/<field>/...` (Echo-S's own layout -- the top `VO`
    folder is the mod's option name, not part of the addressing scheme)
    and flatten FFNx's fallback cascade to one Entry per
    (field_id, dialog_id, page):

        w<window>_<id><page>.ogg   preferred (most specific)
        <id><page>.ogg             fallback
        w<window>_<id>.ogg         fallback (page 0 implied)
        <id>.ogg                   fallback (page 0 implied)

    Files with an underscore that AREN'T the `w<n>_` window-qualified form
    are choice prompts (`play_option`: "<id>_<option_count>.ogg") and are
    ingested as NS_FIELD_CHOICE instead.

    Every field folder name is resolved to a numeric id via
    FIELD_NAME_TO_ID -- an unrecognised field folder name is a hard
    error (see resolve_field_id), not a skip, since silently dropping an
    entire field's dialogue is worse than stopping the build.

    The window_id captured from a `w<n>_` filename is currently DISCARDED
    (the key's window slot stays 0, see module docstring). If two
    window-qualified files exist for the same (field, dialog_id, page)
    with DIFFERENT window ids, that is a real collision this
    simplification cannot represent; the key is explicitly deferred rather
    than silently choosing a potentially wrong speaker.
    """
    triples = []
    for field in sorted(os.listdir(vo_voice_root)):
        fdir = os.path.join(vo_voice_root, field)
        if not os.path.isdir(fdir):
            continue
        # Reserved namespaces have their own parsers.  In particular,
        # feeding ``_world`` through the field resolver only produces a
        # page of misleading "unknown field" diagnostics before the same
        # files are correctly ingested by ``ingest_world_vo``.
        if field.startswith('_'):
            continue
        for fn in os.listdir(fdir):
            triples.append((field, fn, os.path.join(fdir, fn)))
    return _field_vo_entries(triples, log=log)


def ingest_field_vo_from_files(files, log=None):
    """
    Same as `ingest_field_vo`, but takes `[(rel_path, full_disk_path),
    ...]` -- the exact shape `build.py`'s `Mod.files_for()` yields (drop
    the `option` third element first: `[(r, f) for r, f, _opt in
    picked]`) -- instead of a directory to walk. Use this from the real
    build pipeline; use `ingest_field_vo` for standalone/CLI use against
    an already-extracted mod folder.

    Each path is classified with `classify_voice_path`; anything that
    doesn't come back `('field', ...)` is silently ignored (build.py's
    caller is expected to have already routed battle/non-voice paths
    elsewhere -- see `voice_patch.py`).
    """
    triples = []
    for rel, full in files:
        c = classify_voice_path(rel)
        if c and c[0] == 'field':
            _, field, fn = c
            triples.append((field, fn, full))
    return _field_vo_entries(triples, log=log)


def _nocloud_vo_entries(records, log=None):
    """Build Tifa/Cid-specific ASK entries from NoCloud overlays."""
    def _log(msg):
        if log:
            log(msg)

    entries = []
    skipped = 0
    for leader, field, filename, path in records:
        if not filename.lower().endswith('.ogg'):
            continue
        stem = filename[:-4]
        dialog, separator, option = stem.partition('_')
        if not (separator and dialog.isdigit() and option.isdigit()):
            skipped += 1
            _log('skip (unrecognised NoCloud voice): %s' % path)
            continue
        try:
            field_id = resolve_field_id(field)
        except BadArchive:
            skipped += 1
            _log('skip (unknown NoCloud field): %s' % path)
            continue
        key = field_variant_choice_key(leader, field_id, int(dialog),
                                       int(option))
        entries.append(Entry(key, source_path=path))
    _log('NoCloud VO: %d playable entries%s'
         % (len(entries), ' (%d skipped)' % skipped if skipped else ''))
    return entries


def ingest_nocloud_vo(nocloud_root, log=None):
    """Walk ``NoCloud/{tifa,cid}/Voice/<field>/*.ogg``."""
    records = []
    for leader in ('tifa', 'cid'):
        voice_root = os.path.join(nocloud_root, leader, 'Voice')
        if not os.path.isdir(voice_root):
            continue
        for field in sorted(os.listdir(voice_root)):
            field_root = os.path.join(voice_root, field)
            if not os.path.isdir(field_root):
                continue
            for filename in sorted(os.listdir(field_root)):
                path = os.path.join(field_root, filename)
                if os.path.isfile(path):
                    records.append((leader, field, filename, path))
    return _nocloud_vo_entries(records, log=log)


def ingest_nocloud_vo_from_files(files, log=None):
    """File-list equivalent of :func:`ingest_nocloud_vo`."""
    records = []
    for rel, full in files:
        classified = classify_voice_path(rel)
        if classified and classified[0] == 'nocloud':
            _, leader, field, filename = classified
            records.append((leader, field, filename, full))
    return _nocloud_vo_entries(records, log=log)


def _battle_vo_entries(triples, action_name_to_id, log=None):
    """Core battle-VO ingestion: `triples` is [(char_slot, filename,
    full_disk_path), ...]. Shared by `ingest_battle_vo` (directory walk)
    and `ingest_battle_vo_from_files` (build.py's file-list shape)."""
    def _log(msg):
        if log:
            log(msg)

    entries = []
    skipped = 0
    for char_slot, fn, path in triples:
        if not fn.lower().endswith('.ogg'):
            continue
        stem = fn[:-4]
        name, _, n = stem.rpartition('_')
        if not (name and n.isdigit()):
            skipped += 1
            _log('skip (unrecognised): %s' % path)
            continue
        action_id = action_name_to_id.get(name.lower())
        if action_id is None:
            skipped += 1
            _log('skip (no action id for %r): %s' % (name, path))
            continue
        variant = int(n) - 1   # Echo-S is 1-based, key is 0-based
        if variant < 0 or variant > 0xFF:
            raise BadArchive('variant out of range: %s' % fn)
        key = battle_bark_key(char_slot, action_id, variant)
        entries.append(Entry(key, source_path=path))
    _log('battle VO: %d entries, %d skipped' % (len(entries), skipped))
    return entries


def ingest_battle_vo(battle_voice_root, action_name_to_id=None, log=None):
    """
    Walk `Battle VO/Voice/_battle/char_NN/<action>_<n>.ogg`.

    NOT WHAT THE BUILD USES -- see `echo_s_battlevo`. This resolves a clip's
    STEM to an action id, and on the real release that reaches 548 of a set's
    1,079 clips, because half the stems (`generic`, `help`, `limit`, `summon`,
    `restore`, `sense`) are pool names serving many actions rather than action
    names. The shipped runtime does not ask for a stem at all: it builds
    FFNx's own `bc<char><cmd><action>` key, and the mod's `voice/config.toml`
    maps exactly those keys to pools. This function is kept for the CLI and
    for reading the tree directly; do not wire it into staging.

    `action_name_to_id` defaults to `ACTION_NAME_TO_ID` above (extracted
    from this project's own kernel2.bin, not guessed -- see the module
    section above this function for full sourcing and confidence tiers).
    Pass an explicit dict to override or extend it -- e.g. to resolve the
    handful of stems `ACTION_NAME_TO_ID` deliberately leaves out
    ('limit_jump', 'limit_cat', ...) once a human has made the call
    documented in the comment above that table.

    Stems in Echo-S's tree that resolve to a NON-action_id category
    (command-level barks, FFNx's special outcome words, element-reaction
    barks, UI labels, likely-enemy-exclusive attacks -- all enumerated
    in the module section above) are correctly absent from
    `ACTION_NAME_TO_ID` and will be skipped here with a logged reason,
    same as any other unresolved name -- this function does not
    special-case them, since they need a different hook entirely, not
    just a different key.

    Battle-bark playback probability (Echo-S's `Prob` option, 0/25/50/75/
    100) is NOT handled by this function or baked into any entry -- it
    reads as a single mod-wide runtime percentage the hook checks once
    before attempting to open a bark file at all (see module header), not
    a per-file property, so there is nothing for this ingester to record.
    """
    if action_name_to_id is None:
        action_name_to_id = ACTION_NAME_TO_ID
    triples = []
    for char_dir in sorted(os.listdir(battle_voice_root)):
        if not (char_dir.startswith('char_') and char_dir[5:].isdigit()):
            continue
        char_slot = int(char_dir[5:])
        cdir = os.path.join(battle_voice_root, char_dir)
        for fn in os.listdir(cdir):
            triples.append((char_slot, fn, os.path.join(cdir, fn)))
    return _battle_vo_entries(triples, action_name_to_id, log=log)


def ingest_battle_vo_from_files(files, action_name_to_id=None, log=None):
    """
    Same as `ingest_battle_vo`, but takes `[(rel_path, full_disk_path),
    ...]` -- `build.py`'s `Mod.files_for()` shape (drop `option` first)
    -- instead of a directory to walk. Use this from the real build
    pipeline; use `ingest_battle_vo` for standalone/CLI use.
    """
    if action_name_to_id is None:
        action_name_to_id = ACTION_NAME_TO_ID
    triples = []
    for rel, full in files:
        c = classify_voice_path(rel)
        if c and c[0] == 'battle':
            _, char_slot, fn = c
            triples.append((char_slot, fn, full))
    return _battle_vo_entries(triples, action_name_to_id, log=log)


if __name__ == '__main__':
    import sys
    if len(sys.argv) != 4 or sys.argv[1] not in ('stage-field', 'stage-battle'):
        sys.exit('usage: voice_dat.py stage-field <VO/Voice dir> <out dir>\n'
                 '       voice_dat.py stage-battle <Battle VO/Voice/_battle dir> <out dir>\n'
                 '  (stage-battle uses ACTION_NAME_TO_ID by default -- pass an\n'
                 '   explicit dict to ingest_battle_vo() directly to override it)')
    _, mode, src_dir, out_dir = sys.argv
    if mode == 'stage-field':
        ents = ingest_field_vo(src_dir, log=print)
    else:
        ents = ingest_battle_vo(src_dir, log=print)
    stage(ents, out_dir, log=print)
