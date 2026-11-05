import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import sony_vm as vm


class OfflineTests(unittest.TestCase):
    def test_vendor_lock(self):
        self.assertEqual(vm.verify_vendor(),'020603daf84aff35069a1071a6385f80fb41f397')

    def test_candidate_never_uses_a7m3_recipe(self):
        c=vm.profile('ilce-7rm3a-1.00')
        self.assertTrue(vm.preflight(c,{}))

    def test_missing_partitions(self):
        errors=vm.preflight(vm.profile('ilce-7m3-4.03'),{})
        self.assertEqual(len(errors),8)

    def test_foreign_firmware_rejected(self):
        c=vm.profile('ilce-7m3-4.03')
        items={'nflasha'+n+'.bin':{'size':size,'sha256':'0'*64}
               for n,size in c['required_partitions'].items()}
        self.assertEqual(len(vm.preflight(c,items)),3)

    def test_auto_selection_requires_all_exact_firmware_pins(self):
        c=vm.profile('ilce-7m3-4.03')
        items={'nflasha'+n+'.bin':{'sha256':value} for n,value in c['exact_partition_hashes'].items()}
        self.assertEqual(vm.select_profile('auto',items)['id'],c['id'])
        items['nflasha15.bin']['sha256']='foreign'
        self.assertIsNone(vm.select_profile('auto',items)['recipe'])

    def test_inventory_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'file').write_bytes(b'input');(p/'nflasha3.bin').symlink_to(p/'file')
            with self.assertRaises(ValueError):vm.inventory(p)

    def test_inventory_hash_and_nonpartition_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'nflasha3.bin').write_bytes(b'abc');(p/'unrelated').write_text('x')
            self.assertEqual(vm.inventory(p),{'nflasha3.bin':{'size':3,'sha256':hashlib.sha256(b'abc').hexdigest()}})

    def test_profile_path_traversal(self):
        with self.assertRaises(ValueError):vm.profile('../outside')

    def test_prepare_rejects_existing_and_nested_output(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'nflasha3.bin').write_bytes(b'keep')
            for out in (p,p/'new'):
                with self.assertRaises(ValueError):vm.prepare(p,out,vm.profile('ilce-7m3-4.03'))
            self.assertEqual((p/'nflasha3.bin').read_bytes(),b'keep')
            self.assertFalse((p/'new').exists())

    def test_failed_preflight_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'input').mkdir()
            with self.assertRaises(ValueError):vm.prepare(p/'input',p/'output',vm.profile('ilce-7rm3a-1.00'))
            self.assertFalse((p/'output').exists())

    def test_output_parent_symlink_cannot_bypass_input_separation(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'input').mkdir();(p/'alias').symlink_to(p/'input',target_is_directory=True)
            with self.assertRaisesRegex(ValueError,'must be separate'):
                vm.prepare(p/'input',p/'alias/output',vm.profile('ilce-7m3-4.03'))
            self.assertFalse((p/'input/output').exists())

    def test_failed_patch_does_not_publish_partial_vm(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'input').mkdir();(p/'input/nflasha3.bin').write_bytes(b'keep')
            with patch.object(vm,'preflight',return_value=[]),patch.object(vm,'extract_boot'),patch.object(vm,'patch',side_effect=ValueError('patch failed')):
                with self.assertRaises(ValueError):vm.prepare(p/'input',p/'output',vm.profile('ilce-7m3-4.03'))
            self.assertFalse((p/'output').exists())
            self.assertEqual((p/'input/nflasha3.bin').read_bytes(),b'keep')

    def test_native_emmc_matches_upstream_ebr_reader_and_gpu_patch(self):
        spec=importlib.util.spec_from_file_location('gpu',vm.UPSTREAM/'scripts/patch-grmgles.py')
        gpu=importlib.util.module_from_spec(spec);spec.loader.exec_module(gpu)
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);inp=p/'input';inp.mkdir()
            source=inp/'nflasha15.bin';offset=gpu.EXT2_BLOCK*1024+gpu.OFF_IN_BLOCK
            with source.open('wb') as f:
                f.truncate(vm.SECTORS[15]*512);f.seek(offset);f.write(gpu.ORIG_PROLOGUE)
            parts=vm.make_emmc(inp,p/'disk.img')
            self.assertEqual((p/'disk.img').stat().st_size,vm.IMAGE_SIZE)
            for n in range(5,26):
                self.assertEqual(gpu.find_logical_lba(p/'disk.img',n),parts[n]['start'])
            vm.patch('patch-grmgles.py',p/'disk.img')
            with (p/'disk.img').open('rb') as f:
                f.seek(vm.BOOT_OFFSET+parts[15]['start']*512+offset)
                self.assertEqual(f.read(8),gpu.PATCH)
            with source.open('rb') as f:
                f.seek(offset);self.assertEqual(f.read(8),gpu.ORIG_PROLOGUE)

    def test_partition_regions_do_not_overlap(self):
        parts,extended=vm.layout();last=0
        for n,p in parts.items():
            self.assertGreaterEqual(p['start'],last)
            if p['ebr'] is not None:self.assertGreaterEqual(p['ebr'],last)
            last=p['start']+p['sectors']
        self.assertLessEqual(last,extended+3000000)

    def test_real_kernel_patches_on_synthetic_instruction_fixture(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);data=bytearray(4117760);base=0x60108000
            for address,word in [(0x60108068,0xE4803004),(0x60108028,0xEB00003A),(0x602988CC,0xE58630D4)]:
                struct.pack_into('<I',data,address-base,word)
            (p/'kernel').write_bytes(data)
            vm.patch('patch-pgdpatch.py',p/'kernel',p/'step1')
            vm.patch('patch-atagfix.py',p/'step1',p/'step2')
            vm.patch('patch-console.py',p/'step2',p/'step3')
            actual=(p/'step3').read_bytes()
            self.assertEqual(struct.unpack_from('<I',actual,0x602988CC-base)[0],0xE58620D4)
            self.assertEqual((p/'kernel').read_bytes(),data)

    def test_wrong_kernel_bytes_fail_without_modifying_source(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);data=b'\x00'*4117760;(p/'kernel').write_bytes(data)
            with self.assertRaises(subprocess.CalledProcessError):
                vm.patch('patch-console.py',p/'kernel',p/'out')
            self.assertEqual((p/'kernel').read_bytes(),data)
            self.assertFalse((p/'out').exists())

    def test_incomplete_vm_manifest_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);vm.write_json(p/'vm.json',{'schema':1,'profile':'ilce-7m3-4.03','machine':'cxd90045','artifacts':{}})
            with self.assertRaisesRegex(ValueError,'Incomplete'):vm.verify_vm(p)

    def test_complete_generation_with_synthetic_fat_and_partition_data(self):
        # No Sony data: a valid FAT16 volume containing synthetic boot files.
        kernel=bytearray(4117760);base=0x60108000
        for address,word in [(0x60108068,0xE4803004),(0x60108028,0xEB00003A),(0x602988CC,0xE58630D4)]:
            struct.pack_into('<I',kernel,address-base,word)
        initrd=bytearray(2178048);struct.pack_into('<H',initrd,1080,0xef53)
        initrd[2016076:2016076+14]=b'Backup_load=0 '
        initrd[2016172:2016172+18]=b'/usr/kmod/udate.ko'
        raw=bytearray(32768*512)
        struct.pack_into('<H',raw,11,512);raw[13]=1
        struct.pack_into('<H',raw,14,1);raw[16]=1
        struct.pack_into('<H',raw,17,512);struct.pack_into('<H',raw,19,32768)
        struct.pack_into('<H',raw,22,128);raw[510:512]=b'\x55\xaa'
        raw[512:516]=b'\xf8\xff\xff\xff';cluster=2
        for index,(name,data) in enumerate([(b'VMLINUX BIN',kernel),(b'INITRD  IMG',initrd)]):
            off=129*512+index*32;raw[off:off+11]=name;raw[off+11]=0x20
            struct.pack_into('<H',raw,off+26,cluster);struct.pack_into('<I',raw,off+28,len(data))
            count=(len(data)+511)//512
            for c in range(cluster,cluster+count):
                struct.pack_into('<H',raw,512+c*2,c+1 if c<cluster+count-1 else 0xffff)
            start=161*512+(cluster-2)*512;raw[start:start+len(data)]=data
            cluster+=count
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);inp=p/'input';inp.mkdir();(inp/'nflasha3.bin').write_bytes(raw)
            with (inp/'nflasha15.bin').open('wb') as f:
                f.truncate(vm.SECTORS[15]*512);f.seek(173903*1024+324)
                f.write(bytes.fromhex('0dc0a0e1f0d82de9'))
            items=vm.inventory(inp);c=vm.profile('ilce-7m3-4.03')
            c['required_partitions']={'3':len(raw),'15':vm.SECTORS[15]*512}
            c['exact_partition_hashes']={n:items['nflasha'+n+'.bin']['sha256'] for n in ('3','15')}
            c['boot_hashes']={'vmlinux.bin':hashlib.sha256(kernel).hexdigest(),
                             'initrd-4.03.img':hashlib.sha256(initrd).hexdigest()}
            modified=bytes(initrd).replace(b'Backup_load=0 ',b'Backup_load=1 ').replace(b'/usr/kmod/udate.ko',b'/usr/kmod/udate.zz')
            c['output_hashes']={'vmlinux-console.bin':'3f45fc51a245c0e797e94fd0e59a9eb21b8bdd0efb1a59e962401bd1bf75633b',
                               'initrd-qemu.img':hashlib.sha256(modified).hexdigest()}
            vm.prepare(inp,p/'vm',c)
            manifest=json.loads((p/'vm/vm.json').read_text())
            self.assertEqual(manifest['state'],'generated-not-boot-verified')
            self.assertEqual(vm.inventory(inp),items)
            with patch.object(vm,'profile',return_value=c):
                vm.verify_vm(p/'vm')
                result=vm.main(['run',str(p/'vm'),'--qemu','/unused/qemu','--dry-run'])
                self.assertEqual(result,0)
                with (p/'vm/initrd-qemu.img').open('r+b') as f:f.write(b'!')
                with self.assertRaisesRegex(ValueError,'artifact changed'):vm.verify_vm(p/'vm')


if __name__=='__main__':unittest.main()
