#!/bin/sh
# Rebuild leakprobe.bin from leakprobe.c (DIAGNOSTIC; see leakprobe.py).
set -e
cd "$(dirname "$0")"
CC=${CC:-gcc}
FLAGS="-O2 -ffreestanding -fno-builtin -nostdlib -fno-pic -mcmodel=tiny \
 -fno-stack-protector -fno-asynchronous-unwind-tables -fno-unwind-tables \
 -fno-exceptions -fno-jump-tables -mstrict-align -fno-tree-loop-distribute-patterns -ffixed-x18 -Wall -Werror"
$CC $FLAGS -c leakprobe.c -o /tmp/leakprobe.o
for base in 0x0 0x123450; do
  printf 'SECTIONS { . = %s; .text : { *(.text.lp_frame) *(.text*) } /DISCARD/ : { *(.eh_frame*) *(.comment) *(.note*) } }\n' $base > /tmp/lp.ld
  ld -T /tmp/lp.ld -o /tmp/leakprobe_$base.elf /tmp/leakprobe.o
  objcopy -O binary -j .text /tmp/leakprobe_$base.elf /tmp/leakprobe_$base.bin
done
cmp /tmp/leakprobe_0x0.bin /tmp/leakprobe_0x123450.bin
if objdump -h /tmp/leakprobe.o | grep -E '\.(data|bss|rodata)' | awk '$3 != "00000000"' | grep -q .; then
  echo "leakprobe.o has data sections"; exit 1; fi
cp /tmp/leakprobe_0x0.bin leakprobe.bin
nm /tmp/leakprobe_0x0.elf | awk '$2=="T" && $3 ~ /^lp_/ {print $3, $1}' | sort > leakprobe.entry
echo "leakprobe.bin $(wc -c < leakprobe.bin) bytes, entries: $(cat leakprobe.entry | tr "\n" " ")"
