#!/bin/sh
S=/private/tmp/claude-501/-Volumes-NVME-Users-ppkantorski-Downloads-ff7-mods-Claude/03970759-279c-4189-a1cb-4b17a35998c6/scratchpad
cd /Volumes/NVME/Users/ppkantorski/Downloads/ff7_mods/Claude/7th_heaven_nx
[ -f $S/ports_var/$1_$2.py ] && [ -s $S/ports_var/$1_$2.txt ] && exit 0
ESUI_FOLDERS=$S/vf/$1_$2.json $S/venv/bin/python $S/genvar.py $1 $2 >> $S/vres.log 2>$S/ports_var/$1_$2.err || echo "$1 $2: ERROR" >> $S/vres.log
