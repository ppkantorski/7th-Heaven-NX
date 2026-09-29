#!/bin/sh
# Rebuild worldfar.bin from worldfar.c (aarch64 gcc or clang). The build does
# NOT run this: worldfar.bin is committed. ff7nx_worldfar checks the blob is
# position independent by construction (no .data/.bss/.rodata, no relocations).
set -e
cd "$(dirname "$0")"
CC=${CC:-gcc}
FLAGS="-O2 -ffreestanding -fno-builtin -nostdlib -fno-pic -mcmodel=tiny \
 -fno-stack-protector -fno-asynchronous-unwind-tables -fno-unwind-tables \
 -fno-exceptions -fno-jump-tables -mstrict-align -fno-tree-loop-distribute-patterns -ffixed-x18 -Wall -Werror"
$CC $FLAGS -c worldfar.c -o /tmp/worldfar.o
for base in 0x0 0x123450; do
  printf 'SECTIONS { . = %s; .text : { *(.text.worldfar_draw) *(.text.worldfar_camera) *(.text.worldfar_bend) *(.text.worldfar_input) *(.text.worldfar_move) *(.text.worldfar_alloc) *(.text.worldfar_sink) *(.text.worldfar_sky) *(.text*) } /DISCARD/ : { *(.eh_frame*) *(.comment) *(.note*) } }\n' $base > /tmp/wf.ld
  ld -T /tmp/wf.ld -o /tmp/worldfar_$base.elf /tmp/worldfar.o
  objcopy -O binary -j .text /tmp/worldfar_$base.elf /tmp/worldfar_$base.bin
done
cmp /tmp/worldfar_0x0.bin /tmp/worldfar_0x123450.bin
if objdump -h /tmp/worldfar.o | grep -E '\.(data|bss|rodata)' | awk '$3 != "00000000"' | grep -q .; then
  echo "worldfar.o has data sections"; exit 1; fi
cp /tmp/worldfar_0x0.bin worldfar.bin
nm /tmp/worldfar_0x0.elf | awk '$2=="T" && $3 ~ /^worldfar_/ {sub("worldfar_","",$3); print $3, $1}' | sort > worldfar.entry
sha256sum worldfar.c | cut -d" " -f1 > worldfar.src.sha256
echo "worldfar.bin $(wc -c < worldfar.bin) bytes, entries: $(cat worldfar.entry | tr "\n" " ")"
