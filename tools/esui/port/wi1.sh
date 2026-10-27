#!/bin/sh
S=/private/tmp/claude-501/-Volumes-NVME-Users-ppkantorski-Downloads-ff7-mods-Claude/03970759-279c-4189-a1cb-4b17a35998c6/scratchpad
cd /Volumes/NVME/Users/ppkantorski/Downloads/ff7_mods/Claude/7th_heaven_nx
PK='{}'
[ -n "$2" ] && PK="$2"
$S/venv/bin/python $S/whereis.py $1 ../../7th_heaven_nx/sdout/atmosphere/contents/0100A5B00BDC6000/exefs/main "$PK" > $S/wi/$1$3.txt 2>&1
