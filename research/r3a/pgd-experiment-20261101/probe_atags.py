import socket, struct, subprocess, time
q = subprocess.Popen(['qemu-system-arm','-M','cxd90045','-kernel','/inputs/kernel','-initrd','/inputs/initrd','-serial','null','-monitor','none','-display','none','-S','-gdb','tcp:127.0.0.1:12345'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
s = None
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
    print('stop',cmd('?'))
    for addr in ('108028','108118','108144','3b0580'):
        print('break',addr,cmd('Z0,'+addr+',4'))
    for _ in range(6):
        print('continue',cmd('c'))
        d=bytes.fromhex(cmd('g')); vals=struct.unpack('<16I',d[:64]); print('registers',' '.join('r%d=%08x'%(i,v) for i,v in enumerate(vals)))
        pc=vals[15]; print('atags_memory',cmd('m10000,32'))
        print('remove',hex(pc),cmd('z0,%x,4'%pc))
        if pc==0x3b0580: break
finally:
    if s: s.close()
    q.terminate()
    try: q.wait(timeout=3)
    except subprocess.TimeoutExpired: q.kill()
