"""Trace R3A app mmap/munmap syscalls after replaying the observed ioctls."""
import socket
import struct
import subprocess
import time
from pathlib import Path

OUT = Path('/outputs')
OUT.mkdir(parents=True, exist_ok=True)
probe_log = (OUT / 'probe-debugger.log').open('w', buffering=1)
stderr_file = (OUT / 'qemu-stderr.log').open('wb')
serial_path = '/outputs/guest-serial.log'

SYS_OPEN = 0x601bdf48
SYS_IOCTL = 0x601ce9a4
SYS_MMAP2 = 0x6010e828
SYS_MUNMAP = 0x6019dccc

qemu_cmd = [
    'qemu-system-arm', '-M', 'cxd90045',
    '-kernel', '/inputs/atags-console.bin',
    '-initrd', '/inputs/initrd-a7m3-variant.img',
    '-drive', 'if=mtd,file=/outputs/r3a-emmc-mmap.img,format=raw',
    '-serial', f'file:{serial_path}', '-monitor', 'none', '-display', 'none',
    '-net', 'none', '-S', '-gdb', 'tcp:127.0.0.1:12345',
]


def log(*parts):
    line = ' '.join(str(part) for part in parts)
    print(line, flush=True)
    probe_log.write(line + '\n')


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
    log('syscall-symbols', 'sys_open', hex(SYS_OPEN), 'sys_ioctl', hex(SYS_IOCTL),
        'sys_mmap2', hex(SYS_MMAP2), 'sys_munmap', hex(SYS_MUNMAP))
    log('initial-stop', cmd('?'))
    active_entries = {SYS_OPEN}
    log('entry-breakpoint', hex(SYS_OPEN), cmd('Z0,%x,4' % SYS_OPEN))

    tracked_stacks = set()
    observed_opens = []
    pending_open = {}
    pending_ioctl = {}
    pending_mapping = {}
    descriptors = {}
    return_breaks = set()
    deadline = time.monotonic() + 35

    for index in range(6000):
        if time.monotonic() > deadline:
            log('stop', 'overall 35-second budget reached')
            break
        try:
            stop = cmd('c')
        except TimeoutError:
            log('stop', '10-second GDB wait timed out')
            break
        registers = bytearray.fromhex(cmd('g'))
        values = struct.unpack('<16I', registers[:64])
        pc, stack, lr = values[15], values[13], values[14]
        stack_page = stack & ~8191

        if pc == SYS_OPEN:
            raw_path = cmd('m%x,40' % values[0])
            try:
                path = bytes.fromhex(raw_path).split(b'\0', 1)[0].decode('utf-8', 'replace')
            except ValueError:
                path = '[unreadable]'
            if path == '/dev/null':
                tracked_stacks.add(stack_page)
                log('open-null-entry', index, 'stack', hex(stack_page), 'lr', hex(lr))
                if SYS_IOCTL not in active_entries:
                    log('entry-breakpoint', hex(SYS_IOCTL), cmd('Z0,%x,4' % SYS_IOCTL))
                    active_entries.add(SYS_IOCTL)
                pending_open[stack_page] = lr
                if lr not in return_breaks:
                    log('open-return-breakpoint', hex(lr), cmd('Z0,%x,4' % lr))
                    return_breaks.add(lr)

        elif pc == SYS_IOCTL:
            fd, request, arg = values[0], values[1], values[2]
            candidate = stack_page in tracked_stacks and fd in descriptors.get(stack_page, set())
            before = cmd('m%x,4' % arg)
            log('ioctl-entry', index, 'stack', hex(stack_page), 'fd', hex(fd),
                'request', hex(request), 'arg', hex(arg), 'arg-before', before,
                'associated', candidate)
            synthetic_value = None
            if candidate and request == 0x80048206:
                synthetic_value = 0x4000
            elif candidate and request == 0xc0048202 and before == '01000000':
                synthetic_value = 0xf1000000
            elif candidate and request == 0x80048263:
                synthetic_value = 1

            if synthetic_value is not None:
                packed = struct.pack('<I', synthetic_value).hex()
                log('synthetic-response', hex(request), hex(synthetic_value),
                    'write', cmd('M%x,4:%s' % (arg, packed)))
                cmd('z0,%x,4' % SYS_IOCTL)
                struct.pack_into('<I', registers, 0, 0)
                struct.pack_into('<I', registers, 15 * 4, lr)
                log('synthetic-return-registers', cmd('G' + registers.hex()))
                log('restore-ioctl-breakpoint', cmd('Z0,%x,4' % SYS_IOCTL))
                if request == 0x80048263:
                    for address in (SYS_MMAP2, SYS_MUNMAP):
                        if address not in active_entries:
                            log('mapping-entry-breakpoint', hex(address),
                                cmd('Z0,%x,4' % address))
                            active_entries.add(address)
                continue

            if candidate:
                pending_ioctl[stack_page] = (lr, arg, request)
                if lr not in return_breaks:
                    log('ioctl-return-breakpoint', hex(lr), cmd('Z0,%x,4' % lr))
                    return_breaks.add(lr)

        elif pc == SYS_MMAP2 and stack_page in tracked_stacks:
            try:
                raw = cmd('m%x,18' % stack)
                stack_words = struct.unpack('<6I', bytes.fromhex(raw))
            except (ValueError, struct.error):
                raw, stack_words = '[unreadable]', ()
            args = tuple(hex(v) for v in values[:6])
            log('mmap2-entry', index, 'stack', hex(stack_page), 'sp', hex(stack),
                'r0-r5', ','.join(args), 'stack-words',
                ','.join(hex(v) for v in stack_words), 'stack-raw', raw, 'lr', hex(lr))
            pending_mapping[stack_page] = ('mmap2', lr, args, tuple(stack_words))
            if lr not in return_breaks:
                log('mmap2-return-breakpoint', hex(lr), cmd('Z0,%x,4' % lr))
                return_breaks.add(lr)
        elif pc == SYS_MMAP2:
            pass

        elif pc == SYS_MUNMAP and stack_page in tracked_stacks:
            args = (values[0], values[1])
            log('munmap-entry', index, 'stack', hex(stack_page), 'addr', hex(args[0]),
                'length', hex(args[1]), 'lr', hex(lr))
            pending_mapping[stack_page] = ('munmap', lr, args, ())
            if lr not in return_breaks:
                log('munmap-return-breakpoint', hex(lr), cmd('Z0,%x,4' % lr))
                return_breaks.add(lr)
        elif pc == SYS_MUNMAP:
            pass

        elif pc in return_breaks:
            if pending_open.get(stack_page) == pc:
                fd = values[0]
                descriptors.setdefault(stack_page, set()).add(fd)
                observed_opens.append((stack_page, fd))
                log('open-null-result', index, 'stack', hex(stack_page), 'fd', hex(fd))
                pending_open.pop(stack_page)
            if stack_page in pending_ioctl and pending_ioctl[stack_page][0] == pc:
                _, arg, request = pending_ioctl.pop(stack_page)
                log('ioctl-result', index, 'request', hex(request), 'result', hex(values[0]),
                    'arg-after', cmd('m%x,4' % arg))
            if stack_page in pending_mapping and pending_mapping[stack_page][1] == pc:
                kind, _, args, stack_words = pending_mapping.pop(stack_page)
                log(kind + '-result', index, 'stack', hex(stack_page),
                    'result', hex(values[0]), 'entry-args', args,
                    'entry-stack-words', tuple(hex(x) for x in stack_words))
            outstanding = (list(pending_open.values()) +
                           [item[0] for item in pending_ioctl.values()] +
                           [item[1] for item in pending_mapping.values()])
            if pc not in outstanding:
                return_breaks.remove(pc)

        else:
            log('unexpected-stop', stop, 'pc', hex(pc), 'stack', hex(stack_page))
            break

        cmd('z0,%x,4' % pc)
        cmd('s')
        if pc in active_entries or pc in return_breaks:
            cmd('Z0,%x,4' % pc)

    log('serial-log', serial_path)
    try:
        with open(serial_path, 'rb') as serial_file:
            serial_file.seek(0, 2)
            length = serial_file.tell()
            serial_file.seek(max(0, length - 12288))
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
