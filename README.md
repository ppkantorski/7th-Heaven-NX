# 7th Heaven NX

Applies 7th Heaven `.iro` mods to the Nintendo Switch version of Final
Fantasy VII. Reads your own ripped game data, works out which parts of each
mod the Switch can actually use, and writes a ready-to-copy SD card tree.

## Setup

Put this script in its own folder and arrange it like so:

```
7th_heaven_nx.py
iro.py
lgp.py
build.py
workingdir/          <- your ripped game data
    data/field/char.lgp
    data/field/flevel.lgp
    data/battle/battle.lgp
    ...
mods/                <- drop .iro files here
cache/               <- created automatically
sdout/               <- created automatically
```

`workingdir/` is the folder you dump out of your own console with
nxdumptool. If a game update is installed, dump the **update's** RomFS —
modding the base copy fails silently.

Python 3 with tkinter. No other dependencies, nothing to install.

## Use

```
python3 7th_heaven_nx.py          # UI
python3 7th_heaven_nx.py --cli    # rebuild using saved settings
```

Tick the mods you want, pick options on the right, press Build. Copy the
contents of `sdout/` onto the root of your SD card.

Mods lower in the list win where two touch the same file, and any override
is reported in the log.

## What it does

Each enabled mod is extracted to `cache/`, and its `mod.xml` is read to find
the configurable options — Barret v3.0 versus v2.0, which Tifa model, and so
on. Only the folders whose `ActiveWhen` condition passes are used, so the
result matches what 7th Heaven would install on PC.

Every selected file is then matched **by name against the real contents of
your archives**. That's exact rather than heuristic: if `rtaa` exists in your
`battle.lgp`, that's where it goes. Files are routed to:

| Kind | Destination |
|---|---|
| Entries matching an archive | that archive |
| `<field>.chunk.<n>` | spliced into that section of the field in `flevel.lgp` |
| `.ogg` | `data/music_ogg/` |
| `.dds`, `.png`, `.jpg` | skipped — FFNx only |

Archives are rebuilt with the original TOC order and lookup table preserved,
so no filename hash needs reimplementing. Fields are re-encoded as valid
uncompressed LZS, which grows affected entries about 12.5% but is always
correct.

Output lands at:

```
sdout/atmosphere/contents/0100A5B00BDC6000/romfs/ff7/workingdir/...
```

## Field-model reference fixup

Field models (`.hrc` skeletons, `.rsd` resource descriptors) name their
sub-files in plain text, and PC mods write those references uppercase — a
bone points to `AAAB`, an `.rsd` to `PLY=AAAC.PLY` and `TEX[0]=cl.TIM`. The
Switch stores LGP entry names lowercase and resolves them case-sensitively,
so an uppercase reference finds nothing and the mesh loads **empty** — the
model appears as a blank/invisible figure.

The tool rewrites these references to lowercase automatically when building
`char.lgp`, touching only the filename portion — keywords (`PLY=`, `:BONES`,
`NTEX=`) and skeleton/bone names are left alone. This is why models come out
solid rather than blank. The log reports how many model files were fixed.

## Efficiency

Extraction is cached and keyed on each `.iro`'s size and modification time.
Change a mod file and only that mod is re-extracted; change only your option
selections and nothing is re-extracted at all. Archives are only rebuilt if
something targets them.

Settings persist in `settings.json`, so `--cli` reproduces your last build.

## Limitations

**FFNx texture mods do nothing.** Upscale packs — Cosmos Limit Break, SYW,
Remako — ship thousands of DDS or PNG files that only exist because a
Windows-only driver injects them while the game draws. The Switch has no
external texture loader, so there is nowhere to put them. The tool counts and
skips them rather than pretending. This also rules out widescreen, 60fps and
shaders, all of which are renderer features rather than data.

**Mods that add new filenames are refused.** Replacing an existing entry is
safe because the lookup table can be carried over untouched. Adding one would
require regenerating that table, including the conflict handling for names
that hash to the same slot. Rather than risk writing a corrupt archive, such
mods are reported and skipped. Use PyFF7's `lgp_pack.py` manually for those.

**Section 9 chunks are the only field patching implemented.** Other sections
work mechanically but haven't been tested in game.

## Verified

- IRO reading, uncompressed and LZMA, against three real archives
- `mod.xml` parsing against NinoStyle Battle: 13 options, 19 folders, correct
  `ActiveWhen` resolution
- LGP round-trip byte-identical across 741 entries
- Rebuilt `flevel.lgp` boots on hardware
- Field-section splicing across 682 fields, boots on hardware
- Full pipeline end to end, including cache reuse on a second run

`char.lgp` and `battle.lgp` output was validated structurally against a
synthetic archive, not on hardware.
