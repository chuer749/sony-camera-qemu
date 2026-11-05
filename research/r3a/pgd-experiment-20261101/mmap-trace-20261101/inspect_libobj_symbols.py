"""Read exported GRM symbols from the local ARM ELF dynamic symbol table."""
import hashlib
import struct
from pathlib import Path

root = Path(__file__).resolve().parents[4]
path = root / 'local/r3a-research/pgd-experiment-20261101/r3a-libObj.so'
blob = path.read_bytes()
header = struct.unpack_from('<16sHHIIIIIHHHHHH', blob)
section_offset, section_size, section_count, names_index = (
    header[6], header[11], header[12], header[13]
)
sections = [
    struct.unpack_from('<10I', blob, section_offset + index * section_size)
    for index in range(section_count)
]
names_section = sections[names_index]
section_names = blob[names_section[4]:names_section[4] + names_section[5]]


def cstring(table, offset):
    end = table.index(b'\0', offset)
    return table[offset:end].decode('utf-8', 'replace')


print('sha256', hashlib.sha256(blob).hexdigest())
for section in sections:
    if section[1] != 11:  # SHT_DYNSYM
        continue
    strings_section = sections[section[6]]
    strings = blob[strings_section[4]:strings_section[4] + strings_section[5]]
    print('dynamic-symbol-table', cstring(section_names, section[0]),
          'file-offset', hex(section[4]), 'entry-size', section[9])
    for offset in range(section[4], section[4] + section[5], section[9]):
        name_offset, value, size, info, other, index = struct.unpack_from('<IIIBBH', blob, offset)
        name = cstring(strings, name_offset) if name_offset else ''
        if 'gperm' in name.lower():
            print('symbol', name, 'value', hex(value), 'size', hex(size),
                  'binding', info >> 4, 'type', info & 0xf, 'section-index', index)
