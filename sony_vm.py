#!/usr/bin/env python3
"""Offline Sony firmware workspace builder. No USB or camera access."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
UPSTREAM = ROOT / 'third_party/a7m3'
BOOT_OFFSET = 0x80000
IMAGE_SIZE = BOOT_OFFSET + 2 * 1024**3
SECTORS = {1:16256, 2:40960, 3:98304, 5:122880, 6:32768, 7:16384,
           8:16384, 9:16384, 10:24576, 11:1024000, 12:81920, 13:8192,
           14:8192, 15:614400, 16:286720, 17:204800, 18:40960,
           **{n:8192 for n in range(19,26)}}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def profile(name):
    if not re.fullmatch(r'[a-z0-9.-]+', name):
        raise ValueError('Invalid profile ID')
    p = ROOT / 'profiles' / (name + '.json')
    if not p.is_file():
        raise ValueError('Unknown profile: ' + name)
    return json.loads(p.read_text())


def verify_vendor():
    lock = json.loads((UPSTREAM / 'UPSTREAM.json').read_text())
    for name, expected in lock['files'].items():
        if digest(UPSTREAM/name) != expected:
            raise ValueError('Upstream file changed: ' + name)
    return lock['commit']


def select_profile(name, items):
    if name != 'auto':
        return profile(name)
    matches=[]
    for path in sorted((ROOT/'profiles').glob('*.json')):
        config=json.loads(path.read_text())
        pins=config.get('exact_partition_hashes',{})
        if config.get('recipe') and pins and all(
            items.get('nflasha'+n+'.bin',{}).get('sha256')==expected
            for n,expected in pins.items()):
            matches.append(config)
    if len(matches)>1:
        raise ValueError('Multiple firmware profiles match; choose --profile explicitly')
    return matches[0] if matches else {'id':'auto-unmatched','recipe':None}


def inventory(folder):
    folder = Path(folder).resolve(strict=True)
    if not folder.is_dir():
        raise ValueError('Input must be a directory of regular partition files')
    items = {}
    for p in sorted(folder.iterdir()):
        if re.fullmatch(r'nflasha(?:[0-9]+|B[01])\.bin', p.name):
            if p.is_symlink() or not p.is_file():
                raise ValueError('Input must be a regular file: ' + p.name)
            items[p.name] = {'size': p.stat().st_size, 'sha256': digest(p)}
    return items


def preflight(config, items):
    errors = []
    if config.get('recipe') != 'a7m3-4.03':
        return ['This model/version has no verified build recipe; inventory only']
    for n, size in config['required_partitions'].items():
        info = items.get('nflasha'+n+'.bin')
        if not info:
            errors.append('Missing partition: nflasha'+n+'.bin')
        elif info['size'] != size:
            errors.append('Partition size mismatch: nflasha'+n+'.bin')
    for n, expected in config['exact_partition_hashes'].items():
        info = items.get('nflasha'+n+'.bin')
        if info and info['sha256'] != expected:
            errors.append('Firmware/layout fingerprint mismatch: nflasha'+n+'.bin')
    for name, info in items.items():
        suffix = name[7:-4]
        if suffix.isdigit() and int(suffix) in SECTORS:
            if info['size'] > SECTORS[int(suffix)] * 512:
                errors.append('Partition exceeds synthetic capacity: ' + name)
    return errors


def layout():
    """MBR + EBR layout with 1 MiB alignment; partition 4 is a container."""
    align = lambda n: (n+2047)//2048*2048
    parts = {}
    cursor = 2048
    for n in (1,2,3):
        parts[n] = {'start':cursor, 'sectors':SECTORS[n], 'ebr':None}
        cursor = align(cursor+SECTORS[n])
    extended = cursor
    for n in range(5,26):
        start = align(cursor+1)
        parts[n] = {'start':start, 'sectors':SECTORS[n], 'ebr':cursor}
        cursor = align(start+SECTORS[n])
    if cursor > extended+3000000 or extended+3000000 > 4194304:
        raise ValueError('Synthetic partition table exceeds capacity')
    return parts, extended


def partition_entry(kind, start, sectors):
    return struct.pack('<B3sB3sII', 0, b'\xfe\xff\xff', kind,
                       b'\xfe\xff\xff', start, sectors)


def make_emmc(folder, target):
    parts, extended = layout()
    with Path(target).open('xb') as out:
        out.truncate(IMAGE_SIZE)
        mbr = bytearray(512)
        for n in (1,2,3):
            p=parts[n]
            mbr[446+(n-1)*16:446+n*16] = partition_entry(0x0b,p['start'],p['sectors'])
        mbr[494:510] = partition_entry(0x05, extended, 3000000)
        mbr[510:] = b'\x55\xaa'
        out.seek(BOOT_OFFSET); out.write(mbr)
        for n in range(5,26):
            p=parts[n]; ebr=bytearray(512)
            kind = 0x0b if n in (10,11,12,18) else 0x83
            ebr[446:462] = partition_entry(kind,p['start']-p['ebr'],p['sectors'])
            if n < 25:
                next_ebr=parts[n+1]['ebr']
                ebr[462:478] = partition_entry(0x05,next_ebr-extended,extended+3000000-next_ebr)
            ebr[510:] = b'\x55\xaa'
            out.seek(BOOT_OFFSET+p['ebr']*512);out.write(ebr)
        for n,p in parts.items():
            src=Path(folder)/('nflasha%d.bin'%n)
            if not src.exists():
                continue
            if src.stat().st_size > p['sectors']*512:
                raise ValueError('Oversized partition: '+src.name)
            with src.open('rb') as inp:
                pos=BOOT_OFFSET+p['start']*512
                for chunk in iter(lambda: inp.read(1024*1024),b''):
                    if chunk.count(0) != len(chunk):
                        out.seek(pos);out.write(chunk)
                    pos += len(chunk)
    return parts


def fat_class():
    spec=importlib.util.spec_from_file_location('sony_fat', UPSTREAM/'scripts/fat-extract.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.Fat


def extract_boot(folder, dest, config):
    fat=fat_class()(Path(folder)/'nflasha3.bin')
    for source, target in [('VMLINUX.BIN','vmlinux.bin'),('INITRD.IMG','initrd-4.03.img')]:
        matches=[e for e in fat.walk() if e.path.upper()=='/'+source and not e.is_dir]
        if len(matches)!=1:
            raise ValueError('Missing or ambiguous boot file: '+source)
        ent=matches[0];data=fat.read_chain(ent.cluster,ent.size)
        if len(data)!=ent.size or hashlib.sha256(data).hexdigest()!=config['boot_hashes'][target]:
            raise ValueError('Boot file fingerprint mismatch: '+source)
        (dest/target).write_bytes(data)


def patch(name, *args):
    subprocess.run([sys.executable,str(UPSTREAM/'scripts'/name),*map(str,args)],check=True)


def prepare(folder, output, config):
    verify_vendor()
    folder=Path(folder).resolve(strict=True);output=Path(output).resolve()
    if output.exists():
        raise ValueError('Output already exists; choose a new directory')
    if folder == output or folder in output.parents or output in folder.parents:
        raise ValueError('Input and output directories must be separate')
    items=inventory(folder)
    if isinstance(config,str):
        config=select_profile(config,items)
    errors=preflight(config,items)
    if errors:
        raise ValueError('\n'.join(errors))
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.sony-vm-',dir=output.parent) as tmp:
        work=Path(tmp)
        extract_boot(folder,work,config)
        for script,src,dst in [
            ('patch-pgdpatch.py','vmlinux.bin','vmlinux-pgdpatch.bin'),
            ('patch-atagfix.py','vmlinux-pgdpatch.bin','vmlinux-atagfix.bin'),
            ('patch-console.py','vmlinux-atagfix.bin','vmlinux-console.bin'),
            ('patch-initrd.py','initrd-4.03.img','initrd-qemu.img')]:
            patch(script,work/src,work/dst)
        for name,expected in config['output_hashes'].items():
            if digest(work/name)!=expected:
                raise ValueError('Patched output mismatch: '+name)
        parts=make_emmc(folder,work/'qemu-emmc-2g.img')
        patch('patch-grmgles.py',work/'qemu-emmc-2g.img','--lba',parts[15]['start'])
        if inventory(folder)!=items:
            raise ValueError('Inputs changed during generation; discard output')
        artifacts={n:{'size':(work/n).stat().st_size,'sha256':digest(work/n)} for n in
                   ('vmlinux-console.bin','initrd-qemu.img','qemu-emmc-2g.img')}
        write_json(work/'vm.json',{'schema':1,'profile':config['id'],'machine':config['machine'],
            'state':'generated-not-boot-verified','created_at':datetime.now(timezone.utc).isoformat(),
            'upstream_commit':verify_vendor(),'inputs':items,'artifacts':artifacts,
            'synthetic_partitions':parts,'limitations':config['limitations']})
        # Rename only after every stage succeeds; no partial VM at the requested path.
        work.rename(output)
    return output


def verify_vm(folder):
    folder=Path(folder).resolve(strict=True)
    manifest=json.loads((folder/'vm.json').read_text())
    if manifest.get('schema')!=1:
        raise ValueError('Unknown VM schema')
    config=profile(manifest['profile'])
    if config.get('recipe')!='a7m3-4.03' or manifest['machine']!=config['machine']:
        raise ValueError('Unsupported VM recipe/machine')
    required={'vmlinux-console.bin','initrd-qemu.img','qemu-emmc-2g.img'}
    if set(manifest['artifacts'])!=required:
        raise ValueError('Incomplete VM artifact manifest')
    for name in required:
        p=folder/name;info=manifest['artifacts'][name]
        if p.is_symlink() or not p.is_file() or p.stat().st_size!=info['size'] or digest(p)!=info['sha256']:
            raise ValueError('VM artifact changed: '+name)
        if name in config['output_hashes'] and info['sha256']!=config['output_hashes'][name]:
            raise ValueError('VM boot file does not match profile: '+name)
    if manifest['artifacts']['qemu-emmc-2g.img']['size']!=IMAGE_SIZE:
        raise ValueError('Incorrect eMMC size')
    return folder,manifest


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('profiles');sub.add_parser('doctor')
    p=sub.add_parser('inspect');p.add_argument('input');p.add_argument('--profile',default='auto');p.add_argument('--report')
    p=sub.add_parser('prepare');p.add_argument('input');p.add_argument('--profile',default='auto');p.add_argument('--output',required=True)
    p=sub.add_parser('verify');p.add_argument('vm')
    p=sub.add_parser('run');p.add_argument('vm');p.add_argument('--qemu',required=True)
    p.add_argument('--dry-run',action='store_true')
    args=parser.parse_args(argv)
    try:
        if args.command=='profiles':
            for p in sorted((ROOT/'profiles').glob('*.json')):
                c=json.loads(p.read_text());print(c['id']+'  '+c['status'])
        elif args.command=='doctor':
            print(json.dumps({'python':sys.version.split()[0], 'upstream_commit':verify_vendor(),
                'qemu':shutil.which('qemu-system-arm'),'docker':shutil.which('docker'),
                'camera_access':False,'note':'Docker executable presence does not confirm engine availability'},indent=2))
        elif args.command=='inspect':
            items=inventory(args.input);c=select_profile(args.profile,items);errors=preflight(c,items)
            report={'profile':c['id'],'partitions':items,'build_ready':not errors,'reasons':errors}
            if args.report:
                dest=Path(args.report).absolute()
                if dest.exists() or dest.is_symlink() or Path(args.input).resolve() in dest.resolve().parents:
                    raise ValueError('Report must be new and outside the input directory')
                write_json(dest,report)
            print(json.dumps(report,ensure_ascii=False,indent=2))
            return 0 if not errors else 2
        elif args.command=='prepare':
            print('Generated: '+str(prepare(args.input,args.output,args.profile)))
        else:
            folder,manifest=verify_vm(args.vm)
            if args.command=='verify':
                print('Artifact integrity verified; boot status: '+manifest['state'])
            else:
                cmd=[args.qemu,'-M',manifest['machine'],'-kernel',str(folder/'vmlinux-console.bin'),
                     '-initrd',str(folder/'initrd-qemu.img'),'-drive',
                     'if=mtd,file=qemu-emmc-2g.img,format=raw,snapshot=on',
                     '-serial','stdio','-display','none','-nic','none']
                if args.dry_run:
                    print(json.dumps(cmd,indent=2))
                else:
                    # Private writable VM overlay; generated base image remains unchanged.
                    subprocess.run(cmd,cwd=folder,check=True)
    except (ValueError,OSError,KeyError,subprocess.CalledProcessError) as e:
        print('Error: '+str(e),file=sys.stderr);return 2
    return 0


if __name__=='__main__':
    sys.exit(main())
