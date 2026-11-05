"""Track guest /dev/null opens and later ioctls by ARM kernel stack identity."""
import socket,struct,subprocess,time,sys
q=subprocess.Popen(['qemu-system-arm','-M','cxd90045','-kernel','/inputs/kernel','-initrd','/inputs/initrd','-drive','if=mtd,file=/data/emmc.img,format=raw','-serial','file:/tmp/guest-serial.log','-monitor','none','-display','none','-S','-gdb','tcp:127.0.0.1:12345'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
s=None
try:
    for _ in range(100):
        s=socket.socket();s.settimeout(8);s.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
        try:s.connect(('127.0.0.1',12345));break
        except ConnectionRefusedError:s.close();time.sleep(.05)
    def cmd(x):
        p=x.encode();s.sendall(b'$'+p+b'#'+('%02x'%(sum(p)&255)).encode())
        while s.recv(1)!=b'$':pass
        r=bytearray()
        while True:
            c=s.recv(1)
            if c==b'#':break
            r.extend(c)
        s.recv(2);s.sendall(b'+');return r.decode()
    print('initial',cmd('?'));print('open-break',cmd('Z0,601bdf48,4'))
    stacks=set(); pending={}; pending_ioctl={}; descriptors={}; ret_breaks=set(); ioctl_on=False; end=time.monotonic()+55
    for i in range(3000):
        if time.monotonic()>end:print('time budget reached');break
        try:stop=cmd('c')
        except TimeoutError:print('timeout waiting');break
        regs=bytearray.fromhex(cmd('g')); vals=struct.unpack('<16I',regs[:64]); pc=vals[15]; stack=vals[13]&~8191
        if pc==0x601bdf48:
            raw=cmd('m%x,40'%vals[0])
            try:path=bytes.fromhex(raw).split(b'\0',1)[0].decode('utf-8','replace')
            except ValueError:path='[unreadable]'
            if path=='/dev/null':
                stacks.add(stack); print('open-null',i,'stack',hex(stack),'path',path,'user-frame',cmd('m%x,48'%(stack+8192-72)),flush=True)
                pending[stack]=vals[14]
                if vals[14] not in ret_breaks:
                    print('return-break',hex(vals[14]),cmd('Z0,%x,4'%vals[14])); ret_breaks.add(vals[14])
                if not ioctl_on:
                    print('ioctl-break',cmd('Z0,601ce9a4,4'));ioctl_on=True
        elif pc==0x601ce9a4:
            if stack in stacks:
                matched=vals[0] in descriptors.get(stack,set())
                print('ioctl-candidate',i,'stack',hex(stack),'fd',hex(vals[0]),'request',hex(vals[1]),'arg',hex(vals[2]),'fd-match',matched,'arg-before',cmd('m%x,4'%vals[2]),flush=True)
                mock=None
                if matched and vals[1]==0x80048206 and '--mock-size' in sys.argv:mock='00400000'
                if matched and vals[1]==0xc0048202 and '--mock-address' in sys.argv and cmd('m%x,4'%vals[2])=='01000000':mock='000000f1'
                if mock is not None:
                    # Research-only synthetic response derived from this guest's
                    # driver, not a substitute for a real device model.
                    print('mock-put-user',hex(vals[1]),mock,cmd('M%x,4:%s'%(vals[2],mock)),flush=True)
                    cmd('z0,601ce9a4,4')
                    struct.pack_into('<I',regs,0,0)
                    struct.pack_into('<I',regs,15*4,vals[14])
                    print('mock-return',cmd('G'+regs.hex()),flush=True)
                    print('restore-ioctl-break',cmd('Z0,601ce9a4,4'),flush=True)
                    continue
                if matched:
                    pending_ioctl[stack]=(vals[14],vals[2],vals[1])
                    if vals[14] not in ret_breaks:
                        print('ioctl-return-break',hex(vals[14]),cmd('Z0,%x,4'%vals[14]));ret_breaks.add(vals[14])
        elif pc in ret_breaks:
            if pending.get(stack)==pc:
                descriptors.setdefault(stack,set()).add(vals[0]); pending.pop(stack)
                print('open-result',i,'stack',hex(stack),'fd',hex(vals[0]),flush=True)
            if stack in pending_ioctl and pending_ioctl[stack][0]==pc:
                _,arg,req=pending_ioctl.pop(stack)
                print('ioctl-result',i,'stack',hex(stack),'request',hex(req),'result',hex(vals[0]),'arg-after',cmd('m%x,4'%arg),flush=True)
            if pc not in pending.values() and all(x[0]!=pc for x in pending_ioctl.values()):ret_breaks.remove(pc)
        else:
            print('unexpected-stop',stop,'pc',hex(pc));break
        cmd('z0,%x,4'%pc);cmd('s')
        if pc in (0x601bdf48,0x601ce9a4) or pc in ret_breaks:cmd('Z0,%x,4'%pc)
    print('serial-tail-start')
    try:
        with open('/tmp/guest-serial.log','rb') as f:
            f.seek(0,2);n=f.tell();f.seek(max(0,n-4096));print(f.read().decode(errors='replace'))
    except FileNotFoundError:pass
finally:
    if s:s.close()
    q.terminate()
    try:q.wait(timeout=3)
    except subprocess.TimeoutExpired:q.kill()
