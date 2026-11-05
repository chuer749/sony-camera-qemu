"""Offline GDB probe: replay three statically derived 32-bit ioctl responses."""
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

OUT = Path('/outputs')
OUT.mkdir(parents=True, exist_ok=True)
probe_log = (OUT / 'probe-debugger.log').open('w', buffering=1)


def log(*parts):
    line = ' '.join(str(part) for part in parts)
    print(line, flush=True)
    probe_log.write(line + '\n')


stderr_file = (OUT / 'qemu-stderr.log').open('wb')
serial_path = '/outputs/guest-serial.log'
qemu_cmd = [
    'qemu-system-arm', '-M', 'cxd90045',
    '-kernel', '/inputs/atags-console.bin',
    '-initrd', '/inputs/initrd-a7m3-variant.img',
    '-drive', 'if=mtd,file=/outputs/r3a-emmc-step3.img,format=raw',
    '-serial', f'file:{serial_path}', '-monitor', 'none', '-display', 'none',
    '-net', 'none', '-S', '-gdb', 'tcp:127.0.0.1:12345',
]
q = subprocess.Popen(qemu_cmd, stdout=subprocess.DEVNULL, stderr=stderr_file)
s = None
try:
    for _ in range(100):
        s = socket.socket()
        s.settimeout(10)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            s.connect(('127.0.0.1', 12345))
            break
        except ConnectionRefusedError:
            s.close()
            s = None
            time.sleep(0.05)
    if s is None:
        raise RuntimeError('QEMU GDB stub did not accept a connection')

    def cmd(command):
        packet = command.encode()
        s.sendall(b'$' + packet + b'#' + ('%02x' % (sum(packet) & 255)).encode())
        while s.recv(1) != b'$':
            pass
        response = bytearray()
        while True:
            char = s.recv(1)
            if char == b'#':
                break
            response.extend(char)
        s.recv(2)
        s.sendall(b'+')
        return response.decode()

    log('qemu-command', ' '.join(qemu_cmd))
    log('initial-stop', cmd('?'))
    log('open-breakpoint', cmd('Z0,601bdf48,4'))
    tracked_stacks = set()
    observed_null_opens = []
    pending_open = {}
    pending_ioctl = {}
    descriptors = {}
    return_breaks = set()
    ioctl_breakpoint = False
    deadline = time.monotonic() + 30

    for index in range(3000):
        if time.monotonic() > deadline:
            log('stop', 'overall 30-second budget reached')
            break
        try:
            stop = cmd('c')
        except TimeoutError:
            log('stop', '10-second GDB wait timed out')
            break
        registers = bytearray.fromhex(cmd('g'))
        values = struct.unpack('<16I', registers[:64])
        pc, stack = values[15], values[13] & ~8191

        if pc == 0x601bdf48:
            raw_path = cmd('m%x,40' % values[0])
            try:
                path = bytes.fromhex(raw_path).split(b'\0', 1)[0].decode('utf-8', 'replace')
            except ValueError:
                path = '[unreadable]'
            if path == '/dev/null':
                tracked_stacks.add(stack)
                log('open-null', index, 'stack', hex(stack), 'path', path)
                pending_open[stack] = values[14]
                if values[14] not in return_breaks:
                    log('open-return-breakpoint', hex(values[14]), cmd('Z0,%x,4' % values[14]))
                    return_breaks.add(values[14])
                if not ioctl_breakpoint:
                    log('ioctl-breakpoint', cmd('Z0,601ce9a4,4'))
                    ioctl_breakpoint = True

        elif pc == 0x601ce9a4:
            request, arg = values[1], values[2]
            fd = values[0]
            same_stack = stack in tracked_stacks and fd in descriptors.get(stack, set())
            fd_opens = [opened for opened in observed_null_opens if opened[1] == fd]
            candidate = same_stack or len(fd_opens) == 1
            before = cmd('m%x,4' % arg)
            association = 'same-stack-and-fd' if same_stack else ('single-observed-fd' if len(fd_opens) == 1 else 'ambiguous-or-unmatched')
            log('ioctl-entry', index, 'stack', hex(stack), 'fd', hex(fd),
                'request', hex(request), 'arg', hex(arg), 'association', association,
                'matching-null-opens', len(fd_opens), 'arg-before', before)

            synthetic_value = None
            if candidate and request == 0x80048206:
                synthetic_value = 0x4000
            elif candidate and request == 0xc0048202 and before == '01000000':
                synthetic_value = 0xf1000000
            elif candidate and request == 0x80048263:
                synthetic_value = 1

            if synthetic_value is not None:
                packed = struct.pack('<I', synthetic_value).hex()
                write_result = cmd('M%x,4:%s' % (arg, packed))
                log('synthetic-response', hex(request), 'u32', hex(synthetic_value),
                    'buffer-write', write_result, 'syscall-result', '0')
                cmd('z0,601ce9a4,4')
                struct.pack_into('<I', registers, 0, 0)
                struct.pack_into('<I', registers, 15 * 4, values[14])
                log('set-registers-for-synthetic-return', cmd('G' + registers.hex()))
                log('restore-ioctl-breakpoint', cmd('Z0,601ce9a4,4'))
                continue

            if candidate:
                pending_ioctl[stack] = (values[14], arg, request)
                if values[14] not in return_breaks:
                    log('ioctl-return-breakpoint', hex(values[14]), cmd('Z0,%x,4' % values[14]))
                    return_breaks.add(values[14])
            else:
                log('ioctl-not-associated-with-observed-null-open', hex(fd))

        elif pc in return_breaks:
            if pending_open.get(stack) == pc:
                fd = values[0]
                descriptors.setdefault(stack, set()).add(fd)
                observed_null_opens.append((stack, fd))
                log('open-result', index, 'stack', hex(stack), 'fd', hex(fd),
                    'observed-null-open-count', len(observed_null_opens))
                pending_open.pop(stack)
            if stack in pending_ioctl and pending_ioctl[stack][0] == pc:
                _, arg, request = pending_ioctl.pop(stack)
                log('ioctl-result', index, 'stack', hex(stack), 'request', hex(request),
                    'result', hex(values[0]), 'arg-after', cmd('m%x,4' % arg))
            if pc not in pending_open.values() and all(item[0] != pc for item in pending_ioctl.values()):
                return_breaks.remove(pc)
        else:
            log('unexpected-stop', stop, 'pc', hex(pc))
            break

        cmd('z0,%x,4' % pc)
        cmd('s')
        if pc in (0x601bdf48, 0x601ce9a4) or pc in return_breaks:
            cmd('Z0,%x,4' % pc)

    log('serial-log', serial_path)
    try:
        with open(serial_path, 'rb') as serial_file:
            serial_file.seek(0, 2)
            length = serial_file.tell()
            serial_file.seek(max(0, length - 8192))
            log('guest-serial-tail-start')
            log(serial_file.read().decode(errors='replace'))
    except FileNotFoundError:
        log('guest-serial-file-missing')
finally:
    if s:
        s.close()
    q.terminate()
    try:
        q.wait(timeout=3)
    except subprocess.TimeoutExpired:
        q.kill()
        q.wait(timeout=3)
    stderr_file.close()
    probe_log.close()
