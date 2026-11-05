"""Create research-only kernel copies with a new appended ATAGS trampoline."""
import hashlib
import struct
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ARTIFACTS = ROOT / 'local/r3a-research/pgd-experiment-20261101'
SOURCE = ROOT / 'local/r3a-firmware-backup-20261101/extracted/VMLINUX.BIN'
data = SOURCE.read_bytes()
assert len(data) % 4 == 0
assert data[0x28:0x2c] == bytes.fromhex('3a0000eb')
assert data[0x68:0x6c] == bytes.fromhex('043080e4')
base = 0x60108000
stub = base + len(data)
def branch(src, dst, link=False):
    delta = dst - (src + 8)
    assert delta % 4 == 0 and -(1 << 25) <= delta < (1 << 25)
    return (0xeb000000 if link else 0xea000000) | ((delta >> 2) & 0xffffff)
for with_pgd in (False, True):
    out = bytearray(data)
    struct.pack_into('<I', out, 0x28, branch(base + 0x28, stub, True))
    if with_pgd:
        out[0x68:0x6c] = bytes.fromhex('040000ea')
    out.extend(struct.pack('<4I', 0xe59f2004, branch(stub + 4, base + 0x118), 0, 0x10000))
    p = ARTIFACTS / ('atags-plus-pgd.bin' if with_pgd else 'atags-only.bin')
    with p.open('xb') as f: f.write(out)
    print(p.name, 'bytes', len(out), 'stub_offset', hex(len(data)), 'sha256', hashlib.sha256(out).hexdigest())
