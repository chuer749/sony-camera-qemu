"""Bypass grm_gles init in an isolated, pre-created research disk clone."""
import hashlib
import json
import os
import struct
import sys
from pathlib import Path

p = Path(__file__).resolve().parents[3] / 'local/r3a-research/pgd-experiment-20261101'
module = (p / 'r3a-grm_gles.ko').read_bytes()
assert module[:6] == b'\x7fELF\x01\x01'
h = struct.unpack_from('<16sHHIIIIIHHHHHH', module)
sections = [struct.unpack_from('<10I', module, h[6] + i*h[11]) for i in range(h[12])]
names_section = sections[h[13]]
names = module[names_section[4]:names_section[4]+names_section[5]]
lookup = lambda i: names[i:names.index(b'\0', i)].decode()
init = next(s for s in sections if lookup(s[0]) == '.init.text')
symtab = next(s for s in sections if s[1] == 2)
strtab = sections[symtab[6]]
strings = module[strtab[4]:strtab[4]+strtab[5]]
target = None
for i in range(symtab[5] // symtab[9]):
    name, value, size, info, other, shndx = struct.unpack_from('<IIIBBH', module, symtab[4]+i*symtab[9])
    name = strings[name:strings.index(b'\0', name)].decode(errors='replace')
    if name == 'init_module':
        assert sections[shndx] == init and size >= 8
        target = init[4] + value
assert target == 0x1970
expected = bytes.fromhex('0dc0a0e1f0d82de9')
replacement = bytes.fromhex('0000a0e31eff2fe1')
assert module[target:target+8] == expected
manifest = json.loads((p/'r3a-emmc-manifest.json').read_text())
part = manifest['partitions']['15']
expected_partition_hash = os.environ.get('R3A_EXPECTED_PARTITION15_SHA256')
if not expected_partition_hash or part['sha256_read1'] != expected_partition_hash:
    raise SystemExit('Set R3A_EXPECTED_PARTITION15_SHA256 from your local manifest before patching')
logical, within = divmod(target, 1024)
assert logical == 6 and within == 0x170
# Independently observed using debugfs stat on the hashed source partition:
# file blocks 0..11 map to ext2 blocks 150942..150953.
physical_block = 150942 + logical
disk_offset = manifest['user_area_offset'] + part['synthetic_start_sector']*512 + physical_block*1024 + within
disk = p/(sys.argv[1] if len(sys.argv)>1 else 'r3a-emmc-shell-run.img')
assert disk.parent == p and disk.name.startswith('r3a-emmc-') and disk.name.endswith('.img')
with disk.open('r+b') as f:
    f.seek(disk_offset)
    assert f.read(8) == expected
    f.seek(disk_offset); f.write(replacement); f.flush()
    f.seek(disk_offset); assert f.read(8) == replacement
print('module_sha256', hashlib.sha256(module).hexdigest())
print('module_offset',hex(target),'disk_offset',hex(disk_offset))
print('original',expected.hex(),'replacement',replacement.hex())
print('Bypass only; /dev/sugilite and graphics remain unimplemented.')
