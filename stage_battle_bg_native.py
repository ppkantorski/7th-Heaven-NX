#!/usr/bin/env python3
"""
One-time setup: stages the Avalanche Arisen native battle background files
(from the original PC release archive, "AVALANCHE ARISEN STEAM") into
battle_bg_native/, where build.py's BATTLE_BG_NATIVE_DIR expects them.

Usage:
    python3 stage_battle_bg_native.py "/path/to/AVALANCHE ARISEN STEAM/Battle_Add" .

Run this once after unzipping your copy of the AVALANCHE ARISEN STEAM
archive, from your 7th_heaven_nx project directory (or pass its path as
the 2nd argument). Only needs to be re-run if that source archive changes.
"""
import os, shutil, sys

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    src = sys.argv[1]
    project_dir = sys.argv[2] if len(sys.argv) > 2 else '.'
    dest = os.path.join(project_dir, 'battle_bg_native')
    os.makedirs(dest, exist_ok=True)
    n = 0
    for fn in os.listdir(src):
        full = os.path.join(src, fn)
        if os.path.isfile(full):
            shutil.copy2(full, os.path.join(dest, fn.lower()))
            n += 1
    print(f'staged {n} files into {dest}')

if __name__ == '__main__':
    main()
