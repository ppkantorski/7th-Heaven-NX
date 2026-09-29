#!/bin/sh
# Rebuild texprobe.bin from texprobe.c (DIAGNOSTIC; see texprobe.py).
set -e
cd "$(dirname "$0")"
CC=${CC:-gcc}
FLAGS="-O2 -ffreestanding -fno-builtin -nostdlib -fno-pic -mcmodel=tiny \
 -fno-stack-protector -fno-asynchronous-unwind-tables -fno-unwind-tables \
 -fno-exceptions -fno-jump-tables -mstrict-align -fno-tree-loop-distribute-patterns -ffixed-x18 -Wall -Werror"
$CC $FLAGS -c texprobe.c -o /tmp/texprobe.o
for base in 0x0 0x123450; do
  printf 'SECTIONS { . = %s; .text : { *(.text.tp_frame) *(.text.tp_load) *(.text.tp_ret) *(.text*) } /DISCARD/ : { *(.eh_frame*) *(.comment) *(.note*) } }\n' $base > /tmp/tp.ld
  ld -T /tmp/tp.ld -o /tmp/texprobe_$base.elf /tmp/texprobe.o
  objcopy -O binary -j .text /tmp/texprobe_$base.elf /tmp/texprobe_$base.bin
done
cmp /tmp/texprobe_0x0.bin /tmp/texprobe_0x123450.bin
if objdump -h /tmp/texprobe.o | grep -E '\.(data|bss|rodata)' | awk '$3 != "00000000"' | grep -q .; then
  echo "texprobe.o has data sections"; exit 1; fi
cp /tmp/texprobe_0x0.bin texprobe.bin
nm /tmp/texprobe_0x0.elf | awk '$2=="T" && $3 ~ /^tp_/ {print $3, $1}' | sort > texprobe.entry
echo "texprobe.bin $(wc -c < texprobe.bin) bytes, entries: $(cat texprobe.entry | tr "\n" " ")"
