"""Build a private synthetic QEMU disk from A7R3A user-area snapshots.

Uses measured sizes for observed devices; placeholder partition sizes and
the extended partition table are QEMU/A7M3 research conventions, not a claim
about the camera's real on-flash partition table. Never modifies source dumps.
"""
import hashlib
import importlib.util
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
LOCAL = ROOT / 'local'
SRC = LOCAL / 'r3a-firmware-backup-20261101'
META = LOCAL / 'r3a-metadata-20261101.json'
OUT = LOCAL / 'r3a-research/pgd-experiment-20261101/r3a-emmc-base.img'
REPORT = LOCAL / 'r3a-research/pgd-experiment-20261101/r3a-emmc-manifest.json'
spec = importlib.util.spec_from_file_location('sony_vm', ROOT / 'sony_vm.py')
vm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vm)

metadata = json.loads(META.read_text())
layout_text = metadata['metadata']['layout']
observed = {}
for block in layout_text.split('DEVICE:')[1:]:
    name = block.splitlines()[0].strip()
    match = re.fullmatch(r'/sys/class/block/nflasha([0-9]+)', name)
    if not match:
        continue
    fields = dict(re.findall(r'FIELD:([^\r\n]+)\r?\n([^\r\n]*)', block))
    observed[int(match.group(1))] = (int(fields['start']), int(fields['size']))
for n, (_, sectors) in observed.items():
    if n != 4:
        vm.SECTORS[n] = sectors
parts, extended = vm.layout()
for n in observed:
    if n != 4 and n not in parts:
        raise ValueError('Missing synthetic partition %d' % n)
src1, src2 = (SRC / ('nflasha.read%d.bin' % n) for n in (1, 2))
assert src1.stat().st_size == src2.stat().st_size == 3817472 * 512
result = {'model': 'ILCE-7RM3A', 'source': 'two user-area snapshots; read1 used',
          'snapshot_difference_note': 'nflasha2 varies; read1 selected; other observed slices compared',
          'partition_4': 'synthetic extended container; measured nflasha4 is not copied',
          'placeholder_sizes': {str(n): vm.SECTORS[n] for n in parts if n not in observed},
          'image_bytes': vm.IMAGE_SIZE, 'user_area_offset': vm.BOOT_OFFSET,
          'partitions': {}}
with OUT.open('xb') as out, src1.open('rb') as a, src2.open('rb') as b:
    out.truncate(vm.IMAGE_SIZE)
    mbr = bytearray(512)
    for n in (1, 2, 3):
        p = parts[n]
        mbr[446+(n-1)*16:446+n*16] = vm.partition_entry(0x0b, p['start'], p['sectors'])
    mbr[494:510] = vm.partition_entry(0x05, extended, 3000000)
    mbr[510:] = b'\x55\xaa'
    out.seek(vm.BOOT_OFFSET); out.write(mbr)
    for n in range(5, 26):
        p=parts[n]; ebr=bytearray(512)
        kind=0x0b if n in (10,11,12,18) else 0x83
        ebr[446:462]=vm.partition_entry(kind,p['start']-p['ebr'],p['sectors'])
        if n<25:
            nxt=parts[n+1]['ebr']
            ebr[462:478]=vm.partition_entry(0x05,nxt-extended,extended+3000000-nxt)
        ebr[510:]=b'\x55\xaa'
        out.seek(vm.BOOT_OFFSET+p['ebr']*512); out.write(ebr)
    for n, (start, sectors) in sorted(observed.items()):
        if n == 4: continue
        a.seek(start*512); b.seek(start*512)
        target=vm.BOOT_OFFSET+parts[n]['start']*512
        remaining=sectors*512; sha1=hashlib.sha256(); sha2=hashlib.sha256(); diff=0
        while remaining:
            amount=min(1024*1024,remaining); x=a.read(amount); y=b.read(amount)
            if len(x)!=amount or len(y)!=amount: raise ValueError('truncated input')
            sha1.update(x); sha2.update(y)
            if x!=y: diff+=sum(i!=j for i,j in zip(x,y))
            if x.count(0)!=len(x): out.seek(target); out.write(x)
            target+=amount; remaining-=amount
        if n!=2 and diff: raise ValueError('unexpected snapshot difference in %d' % n)
        result['partitions'][str(n)]={'source_start_sector': start, 'sectors': sectors,
            'synthetic_start_sector': parts[n]['start'], 'sha256_read1':sha1.hexdigest(),
            'sha256_read2':sha2.hexdigest(),'different_bytes':diff}
        print('copied', n, 'bytes', sectors*512, 'differences', diff, flush=True)
    out.flush()
with OUT.open('rb') as f:
    for n, rec in result['partitions'].items():
        f.seek(vm.BOOT_OFFSET+rec['synthetic_start_sector']*512)
        remaining=rec['sectors']*512; h=hashlib.sha256()
        while remaining:
            data=f.read(min(1024*1024,remaining))
            if not data: raise ValueError('image truncated')
            h.update(data); remaining-=len(data)
        if h.hexdigest()!=rec['sha256_read1']: raise ValueError('readback mismatch '+n)
        print('verified',n,flush=True)
REPORT.write_text(json.dumps(result,indent=2)+'\n')
print('complete', OUT, flush=True)
