"""Read selected kernel symbol addresses from the guest shell over serial."""
import socket
import subprocess
import time
from pathlib import Path

OUT = Path('/outputs')
serial_path = OUT / 'guest-symbols-serial.log'
stderr_path = OUT / 'qemu-symbols-stderr.log'
error_path = OUT / 'probe-symbols-error.log'
qemu_cmd = [
    'qemu-system-arm', '-M', 'cxd90045',
    '-kernel', '/inputs/atags-console.bin',
    '-initrd', '/inputs/initrd-a7m3-variant.img',
    '-drive', 'if=mtd,file=/outputs/r3a-emmc-symbols.img,format=raw',
    '-serial', 'tcp:127.0.0.1:12346,server,nowait',
    '-monitor', 'none', '-display', 'none', '-net', 'none',
]
with stderr_path.open('wb') as stderr_file:
    q = subprocess.Popen(qemu_cmd, stdout=subprocess.DEVNULL, stderr=stderr_file)
    sock = None
    capture = bytearray()
    try:
        for _ in range(200):
            sock = socket.socket()
            sock.settimeout(1)
            try:
                sock.connect(('127.0.0.1', 12346))
                break
            except OSError:
                sock.close()
                sock = None
                if q.poll() is not None:
                    raise RuntimeError('QEMU exited before serial connection')
                time.sleep(0.05)
        if sock is None:
            raise RuntimeError('QEMU serial TCP socket did not accept a connection')

        def wait_for(marker, timeout):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    chunk = sock.recv(4096)
                except TimeoutError:
                    continue
                if not chunk:
                    raise RuntimeError('guest serial connection closed')
                capture.extend(chunk)
                if marker in capture:
                    return
            raise TimeoutError('timed out waiting for %r' % marker)

        wait_for(b'Exception> ', 30)
        sock.sendall(b'exit\r')
        wait_for(b'Good Bye.', 5)
        wait_for(b'/ # ', 5)
        command = (
            b"echo MMAP_SYMS_START; "
            b"grep -E ' (sys_mmap[^ ]*|sys_munmap|sys_mremap|sys_open|sys_ioctl|sys_close)$' /proc/kallsyms; "
            b"echo MMAP_SYMS_END\r"
        )
        sock.sendall(command)
        wait_for(b'MMAP_SYMS_END', 5)
    except Exception as exc:
        error_path.write_text(type(exc).__name__ + ': ' + str(exc) + '\n')
        raise
    finally:
        if sock:
            sock.close()
        q.terminate()
        try:
            q.wait(timeout=3)
        except subprocess.TimeoutExpired:
            q.kill()
            q.wait(timeout=3)
        serial_path.write_bytes(capture)
print('qemu-command', ' '.join(qemu_cmd))
print('serial-bytes', len(capture))
print(capture.decode('utf-8', 'replace'))
