#!/bin/sh
# Rebuild battleprobe.bin from battleprobe.c (DIAGNOSTIC; see battleprobe.py).
set -e
cd "$(dirname "$0")"
CC=${CC:-gcc}
FLAGS="-Os -fno-inline-small-functions -ffreestanding -fno-builtin -nostdlib -fno-pic -mcmodel=tiny \
 -fno-stack-protector -fno-asynchronous-unwind-tables -fno-unwind-tables \
 -fno-exceptions -fno-jump-tables -mstrict-align -fno-tree-loop-distribute-patterns -ffixed-x18 -Wall -Werror"
T=${TMPDIR:-/tmp}
$CC $FLAGS -c battleprobe.c -o $T/battleprobe.o
for base in 0x0 0x123450; do
  printf 'SECTIONS { . = %s; .text : { *(.text.bp_frame) *(.text*) } /DISCARD/ : { *(.eh_frame*) *(.comment) *(.note*) } }\n' $base > $T/bp.ld
  ld -T $T/bp.ld -o $T/battleprobe_$base.elf $T/battleprobe.o
  objcopy -O binary -j .text $T/battleprobe_$base.elf $T/battleprobe_$base.bin
done
cmp $T/battleprobe_0x0.bin $T/battleprobe_0x123450.bin
if objdump -h $T/battleprobe.o | grep -E '\.(data|bss|rodata)' | awk '$3 != "00000000"' | grep -q .; then
  echo "battleprobe.o has data sections"; exit 1; fi
cp $T/battleprobe_0x0.bin battleprobe.bin
nm $T/battleprobe_0x0.elf | awk '$2=="T" && $3 ~ /^bp_/ {print $3, $1}' | sort > battleprobe.entry
echo "battleprobe.bin $(wc -c < battleprobe.bin) bytes, entries: $(cat battleprobe.entry | tr "\n" " ")"
