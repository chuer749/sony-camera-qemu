"""Bounded remote-debugger probe of guest sys_ioctl; offline research only."""
import socket, struct, subprocess, time
q=subprocess.Popen(['qemu-system-arm','-M','cxd90045','-kernel','/inputs/kernel','-initrd','/inputs/initrd','-drive','if=mtd,file=/data/emmc.img,format=raw','-serial','null','-monitor','none','-display','none','-S','-gdb','tcp:127.0.0.1:12345'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
s=None
try:
    for _ in range(100):
        s=socket.socket(); s.settimeout(8)
        try: s.connect(('127.0.0.1',12345)); break
        except ConnectionRefusedError: s.close(); time.sleep(.05)
    def cmd(x):
        p=x.encode(); s.sendall(b'$'+p+b'#'+('%02x'%(sum(p)&255)).encode())
        while s.recv(1)!=b'$': pass
        v=bytearray()
        while True:
            c=s.recv(1)
            if c==b'#': break
            v.extend(c)
        s.recv(2); s.sendall(b'+'); return v.decode()
    print('initial',cmd('?'))
    print('break_sys_ioctl',cmd('Z0,601ce9a4,4'))
    deadline=time.monotonic()+35
    for i in range(500):
        if time.monotonic()>deadline:
            print('overall time budget reached'); break
        try: status=cmd('c')
        except TimeoutError:
            print('timeout waiting for next stop'); break
        vals=struct.unpack('<16I',bytes.fromhex(cmd('g'))[:64])
        print('stop',i,status,'fd',hex(vals[0]),'request',hex(vals[1]),'arg',hex(vals[2]),'lr',hex(vals[14]),'pc',hex(vals[15]),flush=True)
        if vals[15] != 0x601ce9a4: break
        # Resume must step past the software breakpoint or it immediately
        # re-triggers on the same syscall entry.
        cmd('z0,601ce9a4,4')
        cmd('s')
        cmd('Z0,601ce9a4,4')
finally:
    if s:s.close()
    q.terminate()
    try:q.wait(timeout=3)
    except subprocess.TimeoutExpired:q.kill()
