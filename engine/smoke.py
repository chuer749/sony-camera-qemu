#!/usr/bin/env python3
"""Run an original ARM UART test program; no Sony firmware is required."""
import argparse
import json
from pathlib import Path
import struct
import subprocess
import tempfile

TOKEN=b'SONY-VM-ENGINE-OK\n'


def test_program():
    # ARM32: load PL011 base/control, enable TX/RX, print token, loop.
    words=[0,0,0xe5801030]
    for char in TOKEN:
        words.extend([0xe3a01000|char,0xe5801000])
    words.append(0xeafffffe)
    base_index=len(words)
    words.extend([0xf2000000,0x301])
    words[0]=0xe59f0000|(base_index*4-8)
    words[1]=0xe59f1000|((base_index+1)*4-12)
    return struct.pack('<'+'I'*len(words),*words)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--qemu',required=True)
    args=ap.parse_args()
    version=subprocess.check_output([args.qemu,'--version'],text=True).splitlines()[0]
    with tempfile.TemporaryDirectory(prefix='sony-vm-engine-') as d:
        folder=Path(d);(folder/'arm-test.bin').write_bytes(test_program())
        (folder/'empty-initrd').write_bytes(b'\0'*4096)
        cmd=[args.qemu,'-M','cxd90045','-kernel',str(folder/'arm-test.bin'),
             '-initrd',str(folder/'empty-initrd'),'-serial','stdio',
             '-display','none','-monitor','none','-nic','none']
        try:
            result=subprocess.run(cmd,capture_output=True,timeout=4)
            output=result.stdout;error=result.stderr
        except subprocess.TimeoutExpired as e:
            output=e.stdout or b'';error=e.stderr or b''
        if TOKEN not in output:
            raise SystemExit('Engine UART test failed: '+error.decode(errors='replace')[-3000:])
    print(json.dumps({'qemu':version,'machine':'cxd90045',
        'arm_execution_and_uart':'passed','sony_firmware_used':False,
        'camera_firmware_boot':'not-tested'},indent=2))


if __name__=='__main__':main()
