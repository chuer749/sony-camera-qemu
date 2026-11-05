#!/usr/bin/env python3
"""Fetch the pinned QEMU source and apply the upstream machine patch."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
REVISION='1909382c0d3533051cead7fde70ca8e84807ee14'
REPOSITORY='https://github.com/ma1co/qemu.git'


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',required=True)
    args=ap.parse_args();dest=Path(args.output).resolve()
    if dest.exists():raise SystemExit('Choose a new source directory; existing trees are never reset')
    sys.path.insert(0,str(ROOT))
    from sony_vm import verify_vendor
    verify_vendor()
    def run(*cmd):subprocess.run(list(cmd),check=True)
    run('git','init',str(dest))
    run('git','-C',str(dest),'fetch','--depth','1',REPOSITORY,REVISION)
    run('git','-C',str(dest),'checkout','--detach','FETCH_HEAD')
    actual=subprocess.check_output(['git','-C',str(dest),'rev-parse','HEAD'],text=True).strip()
    if actual!=REVISION:raise SystemExit('QEMU revision mismatch')
    patch=ROOT/'third_party/a7m3/patches/qemu-a7m3.patch'
    run('git','-C',str(dest),'apply','--check',str(patch))
    run('git','-C',str(dest),'apply',str(patch))
    run('git','-C',str(dest),'diff','--check')
    (dest/'.sony-vm-source.json').write_text(json.dumps({'repository':REPOSITORY,'commit':REVISION,
        'patch_sha256':hashlib.sha256(patch.read_bytes()).hexdigest()},indent=2)+'\n')
    print('Patched QEMU source ready: '+str(dest))


if __name__=='__main__':main()
