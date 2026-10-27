#!/bin/sh
cd /Volumes/NVME/Users/ppkantorski/Downloads/ff7_mods/Claude/7th_heaven_nx
/private/tmp/claude-501/-Volumes-NVME-Users-ppkantorski-Downloads-ff7-mods-Claude/03970759-279c-4189-a1cb-4b17a35998c6/scratchpad/venv/bin/python - $1 <<'PY'
import sys,io; S='/private/tmp/claude-501/-Volumes-NVME-Users-ppkantorski-Downloads-ff7-mods-Claude/03970759-279c-4189-a1cb-4b17a35998c6/scratchpad'
sys.path.insert(0,S); sys.path.insert(0,'.'); sys.path.insert(0,'_scratch/esui')
import esui_port as P, align as AL
AL.build_new=lambda pre: P.NEW_FULL
AL.EDITS=[]; AL.STRUCTS=[]; AL.GROUPS={}
sys.argv=['align','0x'+sys.argv[1]]
AL.main(); AL.resolve_groups()
PY
