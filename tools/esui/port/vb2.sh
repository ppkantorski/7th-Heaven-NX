#!/bin/sh
S=/private/tmp/claude-501/-Volumes-NVME-Users-ppkantorski-Downloads-ff7-mods-Claude/03970759-279c-4189-a1cb-4b17a35998c6/scratchpad
cd /Volumes/NVME/Users/ppkantorski/Downloads/ff7_mods/Claude/7th_heaven_nx
ESUI_FOLDERS=$S/vf/patrick_all.json $S/venv/bin/python $S/vb2.py $1 > $S/vb2/$1.res 2>&1
