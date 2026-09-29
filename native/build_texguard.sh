#!/bin/sh
# Rebuild texguard.bin from texguard.c (see texguard.py).
set -e
cd "$(dirname "$0")"
CC=${CC:-gcc}
FLAGS="-O2 -ffreestanding -fno-builtin -nostdlib -fno-pic -mcmodel=tiny \
 -fno-stack-protector -fno-asynchronous-unwind-tables -fno-unwind-tables \
 -fno-exceptions -fno-jump-tables -mstrict-align -fno-tree-loop-distribute-patterns -ffixed-x18 -Wall -Werror"
$CC $FLAGS -c texguard.c -o /tmp/texguard.o
for base in 0x0 0x123450; do
  printf 'SECTIONS { . = %s; .text : { *(.text.tg_check) *(.text.tg_store) *(.text.tg_forget) *(.text*) } /DISCARD/ : { *(.eh_frame*) *(.comment) *(.note*) } }\n' $base > /tmp/tg.ld
  ld -T /tmp/tg.ld -o /tmp/texguard_$base.elf /tmp/texguard.o
  objcopy -O binary -j .text /tmp/texguard_$base.elf /tmp/texguard_$base.bin
done
cmp /tmp/texguard_0x0.bin /tmp/texguard_0x123450.bin
if objdump -h /tmp/texguard.o | grep -E '\.(data|bss|rodata)' | awk '$3 != "00000000"' | grep -q .; then
  echo "texguard.o has data sections"; exit 1; fi
cp /tmp/texguard_0x0.bin texguard.bin
nm /tmp/texguard_0x0.elf | awk '$2=="T" && $3 ~ /^tg_/ {print $3, $1}' | sort > texguard.entry
echo "texguard.bin $(wc -c < texguard.bin) bytes, entries: $(cat texguard.entry | tr "\n" " ")"
