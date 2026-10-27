# 7th Heaven NX

[![platform](https://img.shields.io/badge/platform-Switch-898c8c?logo=C++.svg)](https://gbatemp.net/forums/nintendo-switch.283/)
[![language](https://img.shields.io/badge/language-Python-3572A5.svg)](https://github.com/topics/python)
[![GPLv3 License](https://img.shields.io/badge/license-GPLv3-189c11.svg)](https://www.gnu.org/licenses/gpl-3.0.en.html)
[![Latest Version](https://img.shields.io/github/v/release/ppkantorski/7th-Heaven-NX?label=latest&color=blue)](https://github.com/ppkantorski/7th-Heaven-NX/releases/latest)
[![GitHub Downloads](https://img.shields.io/github/downloads/ppkantorski/7th-Heaven-NX/total?color=6f42c1)](https://somsubhra.github.io/github-release-stats/?username=ppkantorski&repository=7th-Heaven-NX&page=1&per_page=300)
[![GitHub issues](https://img.shields.io/github/issues/ppkantorski/7th-Heaven-NX?color=222222)](https://github.com/ppkantorski/7th-Heaven-NX/issues)
[![GitHub stars](https://img.shields.io/github/stars/ppkantorski/7th-Heaven-NX)](https://github.com/ppkantorski/7th-Heaven-NX/stargazers)
[![Discord](https://img.shields.io/badge/discord-7th%20Heaven%20NX-5865F2?logo=discord&logoColor=white)](https://discord.gg/r6zJPPfqtr)

**7th Heaven NX** brings PC 7th Heaven mods to **Final Fantasy VII on Nintendo Switch**. You point it at your own game dump, pick your mods, and it builds an Atmosphère LayeredFS folder you copy straight onto your SD card.

<!-- TODO: banner -->
<!-- [![7th Heaven NX](.pics/banner.png)](https://github.com/ppkantorski/7th-Heaven-NX) -->

On PC, mods like Cosmo Memory, Echo-S and Enhanced Stock UI depend on FFNx, a replacement graphics driver for the game. The Switch has no FFNx, so 7th Heaven NX does that work itself: it repacks the mods into the formats the Switch port reads, and patches the game's own ARM64 module for everything a data file alone can't do (60 FPS, widescreen, HD shaders, voice acting, model shadows and more).

---

## Screenshots

<!-- TODO: screenshots -->
<!-- ![Slideshow](.pics/slideshow.gif) -->

---

## Features

### For Players
- Runs the mods you already know from 7th Heaven, built from the same `.iro` packages
- **Install Mods** window that checks the 7th Heaven catalogs, then installs or updates every supported mod in one click. Untick a mod to leave it out of Install all (remembered between runs), and **Open mods folder** takes you straight to your `mods/`
- Per-mod options, the same ones 7th Heaven shows, with a *Modifies* column so you can see which parts of the game each choice touches
- Load order resolved exactly like 7th Heaven (category auto-sort, first match wins), so conflicts come out the same as on PC
- **16:9 widescreen** across fields, battles, the world map and movies, using each mod's own widescreen config
- **60 FPS** in menus, battles, fields and the world map, following the 60/30 FPS Gameplay mod's choice
    - Camera interpolation at 60 FPS: the walking camera and scripted cutscene pans move in fractions of a unit instead of stepping, movie-synced cameras are interpolated, and battle effects are drawn smoothly between their 15 Hz steps
- **1080p output**, docked or handheld, with no reboot needed to switch
- HD background upscaling, HD anti-aliasing and an HD movie shader, all tuned for this game
- Characters, enemies and vehicles cast **shadows shaped like themselves** instead of round blobs
- **Echo-S voice acting** with lip movement, plus even dialogue loudness so no line is much quieter or louder than the next
- 360° analog movement, an optional off switch for auto-run, and a way to disable the right-stick HP/MP/Limit cheat so one stray click can't spoil a run
- Movies re-encoded to the size and colour space the Switch actually displays, at a quality you choose
- Fast rebuilds: changing a setting only rebuilds the archives it affects
- Release builds for Windows, macOS and Linux (x64 and arm64), no Python needed

### Supported Mods

The **Install Mods** window lists every mod below and fetches it from the 7th Heaven catalog.

| Mod | Category |
|---|---|
| [Tsunamods] Cosmo Memory | Field backgrounds, widescreen |
| [Tsunamods] Wizard Staff | Modern spell names |
| Nitpick | Title, attract mode, art fixes |
| [Tsunamods] Enhanced Stock UI | Menus, fonts, battle UI |
| [Tsunamods] Echo-S | Voice acting |
| Cosmos FMV (30 fps) | Movies |
| Double Input Bugfix (and menu input QoL) | Gameplay |
| [Tsunamods] Idle Animations - 60fps | Animations |
| 60/30 FPS Gameplay | Frame rate |
| Ninostyle Chibi Fixes and Additions | Field models |
| NinoStyle Battle Models | Battle models |
| [Tsunamods] Avalanche Arisen Battle Scenes | Battle backgrounds |
| AxlRose's Blender v1 | Textures |
| NinoStyle Chibi Field Models | Field models |
| Cosmos Limit Break | Field backgrounds, widescreen |
| [Tsunamods] Arranged Soundtrack | Music |
| [Tsunamods] SYW Unified Minigame Textures | Minigames |
| [Tsunamods] SYW Unified Spells Textures | Spells and summons |
| Cosmos Gaia | World map |

Other `.iro` mods can be dropped into `mods/` and will be built, but only the ones above have been played through on hardware.

### Beyond FFNx

Where FFNx already solves a problem (field camera ranges, shadow rules, effect interpolation), 7th Heaven NX does it the same way, so a mod looks the way its author intended. A lot of the work goes past that, though. These are fixes and additions you won't get from FFNx on PC.

**Models and textures**
- Hand fixes for models that break when mods are combined, such as characters whose new bodies don't fit the field's original animation, or recolours that come out blotchy
- Texture edge cleanup: the dark rims and black gutters around texture islands and background tiles are cleaned up, so filtering and upscaling don't leave seams or halos
- Model shadows that follow FFNx's rules but don't need a shadow map, which saves graphics memory the Switch doesn't have to spare

**Fields**
- Per-field background repairs, such as HD tiles that were left at their 1997 resolution, black squares where the sky should show through, and stray edges between layers
- 16:9 margins filled with matching art and the right parallax, instead of black bars or stretched edges
- Palette-animated lights (flickering signs, lamps, orbs) rebuilt in full colour, with every animation frame kept
- Screen shakes, jumps and scrolls retimed so they play at their original pace at 60 FPS

**Battle**
- More effects widened for 16:9, including Ultima's screen-filling glow, which FFNx leaves at 4:3
- **W-Item duplication fixed.** In the original game, choosing a first item with W-Item and then backing out at the second target gives the first item back without cancelling the pick, so repeating it duplicates items. Backing out of the item list after the first pick also lost that item. Both now behave correctly: backing out undoes the first pick, and nothing is gained or lost. Set `SEVENTH_NX_WITEM_FIX=0` to keep the original behaviour

**World map**
- An orbit camera for filming: L and R swing the camera around you on foot, on chocobos and in every vehicle, without changing which way you're heading
- Zoom in closer than the game normally allows, and tilt with the right stick. Pressing ZL+ZR on their own puts everything back behind you

**Audio and voice**
- Echo-S lip sync works out who actually says each line, so the right mouth moves even when the line runs from someone else's script
- Dialogue loudness evened out across every voice line, instead of one fixed gain for all of them

**Quality of life**
- HD Buster Sword title screen and HD countdown timer digits
- Window colour sliders that move one step per press
- Movies resampled to the exact area the console draws and converted to the colour space its shader expects, so packs made from the SD originals don't look washed out

---

## Requirements

- A Switch running [Atmosphère](https://github.com/Atmosphere-NX/Atmosphere)
- Your own dump of Final Fantasy VII (`0100A5B00BDC6000`) with update **1.0.3_5** applied, as an `exefs/` and `romfs/` folder
  > The patches are written for this exact version of the game. Before a build starts, 7th Heaven NX checks the build ID of `exefs/main` and the checksum of the game's internal exe. If either one is from another version, the build stops with an "unsupported game version" error.
- A PC running Windows, macOS or Linux to do the build

---

## Installation

### Release Build (Recommended)

1. Download the zip for your system from the [latest release](https://github.com/ppkantorski/7th-Heaven-NX/releases/latest) and unzip it anywhere you can write to. That folder is your 7th Heaven NX folder.
2. Dump the game's ExeFS and RomFS from your Switch with update 1.0.3_5 installed, so the update's files are merged in rather than the base game's alone (for example with [nxdumptool](https://github.com/DarkMatterCore/nxdumptool)). Put them in `dump/` inside the folder, so you have `dump/exefs/main` and `dump/romfs/ff7/workingdir/`.
3. Run **7th Heaven NX** (`7th Heaven NX.exe` on Windows, `7th Heaven NX.app` on macOS).
4. Open **Install Mods**, tick the mods you want and press **Install all**. They appear in the mod list as soon as they finish.
5. Turn mods on in the list, set their options, then press **Build SD Output**.
6. Copy the contents of `sdout/` to the root of your SD card.

FFmpeg is downloaded once on the first run, into `modules/ffmpeg/`.

> The builds aren't code-signed. On Windows, SmartScreen may stop it the first time: choose **More info → Run anyway**. On macOS, right-click the app and choose **Open**, or run `xattr -dr com.apple.quarantine "7th Heaven NX.app"`.

### From Source

```sh
python3 7th_heaven_nx.py
```

Python 3.9+ with tkinter is all you need up front. On the first run it installs whatever else is missing (Pillow, numpy, scipy, lz4, capstone and a few extras) into a private `.venv/`, downloads PyFF7 into `common/`, and fetches FFmpeg if it isn't on your PATH. Your system Python is left alone. Delete `.venv/` to start over.

To build with your saved settings and no window:

```sh
python3 7th_heaven_nx.py --cli
```

---

## In-Game Controls

These are added on top of the game's own controls.

| Input | Where | Action |
|---|---|---|
| Left stick | Field | Walk in the exact direction of the stick (360° movement) |
| `L` / `R` | World map | Orbit the camera around you, on foot or in any vehicle |
| `L` + `R` | World map | Put the orbit back to zero |
| `ZL` / `ZR` | World map | Zoom out / in, down to ¾ of the game's closest distance |
| Right stick up / down | World map | Tilt the camera |
| `ZL` + `ZR` (alone) | World map | The game's view toggle, and resets zoom, tilt and orbit behind you |
| Left / Right | Window colour menu | One step per press, repeats when held |

---

## Settings Overview

Open **Settings** from the main window.

- **GUI**
  - **Theme**: colours of the 7th Heaven NX window
  - **Developer Settings**: shows or hides the advanced settings (see below)
- **Display**
  - **Aspect ratio**: 4:3 or 16:9. 16:9 needs a widescreen mod such as Cosmos Limit Break
  - **Output resolution**: always 1080p, dynamic (1080p docked, 720p handheld), or 720p as shipped
  - **Title screen**: HD Buster Sword, whatever your mods set, or the original
- **Graphics**
  - **Background upscaling**: Original, HD lite (recommended), HD, xBR, Crisp or HD bilinear
  - **Anti-aliasing**: Original, HD fast (recommended), HD or Off
  - **Movie shader**: Original or HD
  - **Movie quality**: higher looks better and takes longer to build
  - **Model shadows**: shadows shaped like the model instead of round blobs
- **Controls**
  - **360° movement**, **Disable auto-run**, **Disable the right-stick cheat**
- **Voice**
  - **Dialogue loudness**: evens out Echo-S lines. Even, louder, quieter, or off

**About Dev**, in the bottom-left corner of Settings, has a short page about the developer.

Everything else starts at values that have been tested on hardware. **Developer Settings** shows the rest: frame pacing, field render resolution, texture caps, field background paging, graphics memory and more. Most of those exist for testing, and several can break a build if you push them too far.

Turning Developer Settings off only hides those rows. The build keeps using whatever you set them to, and a note next to the toggle reads **Default** or **Modified (n)**, so you always know when something differs from the tested values. **Restore defaults** puts them all back. You can also show them for one run with `SEVENTH_NX_DEV=1`.

---

## File Layout

### Your 7th Heaven NX folder

A release build keeps everything next to the program. Only `mods/`, `dump/` and the settings are yours to edit; the rest is created and managed by the program.

```
7th Heaven NX/
├── 7th Heaven NX(.exe)          ← the program (on macOS: 7th Heaven NX.app)
├── _internal/                   ← the program's own files (Windows and Linux; inside the .app on macOS)
├── settings.json                ← your mod list, load order, mod options and settings
├── dump/                        ← your game dump (you provide this)
│   ├── exefs/
│   │   └── main
│   └── romfs/
│       └── ff7/
│           ├── resources/
│           ├── shaders/
│           └── workingdir/
├── mods/                        ← .iro mods (Install Mods puts them here)
├── cache/                       ← extracted mods and built archives, so rebuilds are fast (safe to delete)
├── data/                        ← per-user data written by builds
├── modules/
│   └── ffmpeg/                  ← FFmpeg, downloaded on the first run
├── sdout/                       ← build output: copy its contents to your SD card
└── 7th_heaven_nx.log            ← log file (windowed builds, which have no console)
```

Running from source, the same user folders sit next to `7th_heaven_nx.py`, alongside the code:

```
7th-Heaven-NX/
├── 7th_heaven_nx.py             ← entry point (the only Python file at the root)
├── src/                         ← the program's modules, by subsystem
├── tests/                       ← test suite
├── tools/                       ← release builder, diagnostics and probes
├── data/                        ← static data (mod sources, default settings, tables)
├── native/                      ← compiled ARM64 payloads patched into exefs/main, with their C sources
├── shaders/                     ← the HD shader sets
├── assets/                      ← icons and title art
├── common/
│   └── PyFF7/                   ← PyFF7, downloaded on the first run
├── docs/                        ← developer notes (see docs/DEVELOPING.md)
├── .venv/                       ← private Python packages, installed on the first run
├── settings.json, dump/, mods/, cache/, sdout/   ← as above
└── attic/                       ← local backups and retired files (not in the repo)
```

### What goes on the SD card

A build writes only the files your mods and settings change. Anything not listed is read from the game as usual. Which of these appear depends on the mods you turn on.

```
sdmc:/
└── atmosphere/
    └── contents/
        └── 0100A5B00BDC6000/
            ├── exefs/
            │   └── main                       ← patched game module (60 FPS, widescreen, fixes and the rest)
            └── romfs/
                └── ff7/
                    ├── resources/
                    │   └── ff7_1.02/
                    │       └── ff7_en         ← the game's PC exe, with the mods' data patches
                    ├── shaders/               ← HD upscaling, anti-aliasing, movie,
                    │                            widescreen and model shadow shaders
                    └── workingdir/
                        └── data/
                            ├── field/         ← flevel.lgp (field scenes and backgrounds),
                            │                    char.lgp (field models)
                            ├── battle/        ← battle.lgp (battle models), magic.lgp (spells and summons),
                            │                    battle scene files (stage*.dat)
                            ├── wm/            ← world_us.lgp (world map), wm0.far (far terrain)
                            ├── menu/          ← menu_us.lgp (menus, fonts, battle UI)
                            ├── minigame/      ← chocobo, coaster, condor, highway, snowboard
                            │                    and submarine archives
                            ├── cd/            ← moviecam.lgp (movie cameras, for 60 FPS movies)
                            ├── sound/         ← audio.dat and audio.fmt (sound effects, footsteps)
                            ├── music_ogg/     ← music, ambience and Echo-S voice lines (one folder per field)
                            ├── movies/        ← movies (.mp4)
                            ├── lang-en/       ← English text: kernel/ (kernel.bin, kernel2.bin, window.bin)
                            │                    and battle/scene.bin, plus movies/ (the localized movies)
                            └── lang-de/, lang-es/, lang-fr/, lang-ja/   ← each language's localized movies
```

To uninstall, delete `atmosphere/contents/0100A5B00BDC6000/`.

---

## Building from Source

### Prerequisites

- Python 3.9+ with tkinter
- [PyInstaller](https://pyinstaller.org) (only for release zips)

### 1. Clone

```sh
git clone https://github.com/ppkantorski/7th-Heaven-NX
cd 7th-Heaven-NX
```

### 2. Run

```sh
python3 7th_heaven_nx.py
```

`7th_heaven_nx.py` is the only Python file at the root. It puts every folder under `src/` on the import path through `src/nxpaths.py`, so modules keep flat import names (`import build`, `import ff7nx_60fps`). Module file names have to stay unique across `src/` for that reason.

### 3. Test

```sh
python3 -m pytest -q tests
```

Some tests need `dump/exefs/main` or a built `sdout/` and skip when those aren't there.

For the source layout, the fast-rebuild variables and how to add a module, see [docs/DEVELOPING.md](docs/DEVELOPING.md).

### 4. Release zip (optional)

```sh
python3 tools/release.py
```

The zip and its `.sha256` go to `release/`. PyInstaller can't cross-compile, so each platform has to be built on that platform. Pushing a version tag (`git tag v1.0.0 && git push origin v1.0.0`) runs `.github/workflows/release.yml`, which builds all six and puts them on a draft release. The tag has to match `VERSION` in `7th_heaven_nx.py`.

A release ships only the program's own files: no game files, no stock shaders, and nothing from `dump/`, `mods/`, `cache/` or `sdout/`. Run `python3 tools/release.py --list` to see exactly what goes in.

---

## Credits

- [FFNx](https://github.com/julianxhokaxhiu/FFNx) and the [7th Heaven](https://github.com/tsunamods-codes/7th-Heaven) team, whose work this follows closely
- [PyFF7](https://github.com/niemasd/PyFF7) for FF7's file formats
- [Ad0ky](https://github.com/Ad0ky) for help with Echo-S and Cosmo Memory
- Tsunamods and every mod author listed above, whose mods this exists to run

---

## Contributing

Contributions are welcome. Please open an [issue](https://github.com/ppkantorski/7th-Heaven-NX/issues/new/choose) or submit a [pull request](https://github.com/ppkantorski/7th-Heaven-NX/compare). If a mod you use isn't supported yet, an issue with the mod's name and what goes wrong is the best place to start. For questions, bug reports or just to talk about the project, join the [7th Heaven NX Discord](https://discord.gg/r6zJPPfqtr).

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/X8X3VR194) [![sponsor](https://github.com/ppkantorski/ppkantorski/raw/refs/heads/main/.pics/sponsor-icon.png)](https://github.com/sponsors/ppkantorski)

---

## License

Licensed under [GPLv3](LICENSE).

7th Heaven NX ships no game files. You need your own copy of Final Fantasy VII for Nintendo Switch.

Copyright © 2026 ppkantorski
