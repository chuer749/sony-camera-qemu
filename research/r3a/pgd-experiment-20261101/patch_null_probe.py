"""Redirect one A7R3A guest library device path in a throwaway disk clone."""
import hashlib, json, sys
from pathlib import Path
p=Path(__file__).resolve().parents[3] / 'local/r3a-research/pgd-experiment-20261101'
lib=(p/'r3a-libObj.so').read_bytes()
needle=b'/dev/dmpgles2\0'
assert lib.count(needle)==1 and lib.find(needle)==0xe945d0
block_index, offset=divmod(0xe945d0,1024)
assert block_index==14929 and offset==0x1d0
# debugfs bmap on the original nflasha15 returned block 134040.
m=json.loads((p/'r3a-emmc-manifest.json').read_text())
disk_offset=m['user_area_offset']+m['partitions']['15']['synthetic_start_sector']*512+134040*1024+offset
replacement=b'/dev/null\0'.ljust(len(needle),b'\0')
disk=p/(sys.argv[1] if len(sys.argv)>1 else 'r3a-emmc-null-test.img')
assert disk.parent == p and disk.name.startswith('r3a-emmc-') and disk.name.endswith('.img')
with disk.open('r+b') as f:
    f.seek(disk_offset); assert f.read(len(needle))==needle
    f.seek(disk_offset); f.write(replacement); f.flush()
    f.seek(disk_offset); assert f.read(len(needle))==replacement
print('lib_sha256',hashlib.sha256(lib).hexdigest(),'offset',hex(disk_offset))
print('probe only: /dev/null is not a graphics device')
