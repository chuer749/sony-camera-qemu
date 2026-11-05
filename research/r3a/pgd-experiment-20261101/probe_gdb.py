import socket
import subprocess
import time
import struct

qemu = subprocess.Popen(['qemu-system-arm', '-M', 'cxd90045', '-kernel', '/inputs/kernel', '-initrd', '/inputs/initrd', '-serial', 'null', '-monitor', 'none', '-display', 'none', '-S', '-gdb', 'tcp:127.0.0.1:12345'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
sock = None
try:
    for i in range(100):
        sock = socket.socket()
        sock.settimeout(8)
        try:
            sock.connect(('127.0.0.1', 12345))
            break
        except ConnectionRefusedError:
            sock.close()
            time.sleep(0.05)
    def cmd(data):
        payload = data.encode()
        sock.sendall(b'$' + payload + b'#' + ('%02x' % (sum(payload) & 255)).encode())
        while sock.recv(1) != b'$':
            pass
        result = bytearray()
        while True:
            c = sock.recv(1)
            if c == b'#':
                break
            result.extend(c)
        sock.recv(2)
        sock.sendall(b'+')
        return result.decode()
    print('stop', cmd('?'))
    print('break TTBR write', cmd('Z0,3b0580,4'))
    print('continue', cmd('c'))
    data = bytes.fromhex(cmd('g'))
    vals = struct.unpack('<16I', data[:64])
    print('registers', ' '.join('r%d=%08x' % (i, v) for i, v in enumerate(vals)))
    print('memory physical alias pre-mmu', cmd('m105800,8'))
    print('remove breakpoint', cmd('z0,3b0580,4'))
    print('watch physical-address', cmd('Z2,105800,4'))
    print('watch virtual-address', cmd('Z2,60105800,4'))
    for i in range(3):
        print('continue', i, cmd('c'))
        data = bytes.fromhex(cmd('g'))
        vals = struct.unpack('<16I', data[:64])
        print('registers', ' '.join('r%d=%08x' % (j, v) for j, v in enumerate(vals)))
        print('virtual pgd', cmd('m60105800,8'))
finally:
    sock.close()
    qemu.terminate()
    try:
        qemu.wait(timeout=3)
    except subprocess.TimeoutExpired:
        qemu.kill()
