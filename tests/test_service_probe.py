"""Exercise the real upstream entry point with a simulated USB device only."""
import array
import importlib.util
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import service_probe as probe


class Interface(list):
    bInterfaceNumber=0
    bAlternateSetting=0
    bInterfaceSubClass=6
    bInterfaceProtocol=80


class Config(list):
    def __getitem__(self,key):return super().__getitem__(0 if key==(0,0) else key)


class PacketGuardTests(unittest.TestCase):
    def test_only_literal_version_read_is_permitted(self):
        probe.permitted_service_packet(0xff01,probe.VERSION_PAYLOAD)
        for pfunc,data in [(0xff03,b''),(0xff01,b'\1\0'+probe.VERSION_PAYLOAD[2:]),
                           (0xff01,probe.VERSION_PAYLOAD.replace(b'version',b'setting'))]:
            with self.assertRaises(ValueError):probe.permitted_service_packet(pfunc,data)

    def test_version_response_has_a_hard_limit(self):
        sink=probe.SmallSink();sink.write(b'x'*4096)
        with self.assertRaises(ValueError):sink.write(b'!')


@unittest.skipUnless(probe.SOURCE.exists() and importlib.util.find_spec('usb'),
                     'Requires isolated probe dependencies and pinned PMCA checkout')
class OriginalFlowTests(unittest.TestCase):
    def exercise(self,fail_auth=False,bad_file=False,fail_initial=False):
        import usb.core
        sys.path.insert(0,str(probe.ROOT.parent))
        state={'service':False,'returned':False,'starts':0,'stops':0,'file_reads':0}

        class Device:
            idVendor=0x054c
            bus=1
            port_numbers=(2,2)
            def __init__(self,pid):
                self.idProduct=pid;self.pending=[];self.detached=False;self._ctx=Mock()
                i=Interface([type('Endpoint',(),{'bEndpointAddress':0x81,'bmAttributes':2})(),
                             type('Endpoint',(),{'bEndpointAddress':2,'bmAttributes':2})()])
                i.bInterfaceClass=8 if pid==probe.PID_NORMAL else 255
                self.config=Config([i])
            def __iter__(self):return iter([self.config])
            def get_active_configuration(self):return self.config
            def is_kernel_driver_active(self,i):
                return self.idProduct==probe.PID_NORMAL and (not self.detached or state['returned'])
            def detach_kernel_driver(self,i):self.detached=True
            def attach_kernel_driver(self,i):self.detached=False
            def ctrl_transfer(self,kind,request,value,index,data):
                if value==0x37ff:state['starts']+=1
                elif value==0xc800:
                    state['stops']+=1;state['service']=False;state['returned']=True
                return 0
            def write(self,ep,data):
                if data[:4]==b'USBC':
                    if fail_initial:
                        raise usb.core.USBError('simulated interface creation failure',error_code=-99)
                    _,tag,length,flags,lun,clen,cdb=struct.unpack('<4sIIBBB16s',data)
                    if cdb[0]==0x12:
                        inquiry=bytearray(36);inquiry[4]=31
                        inquiry[8:16]=b'Sony    ';inquiry[16:32]=b'DSC             '
                        self.pending.append(bytes(inquiry[:length]))
                    self.pending.append(struct.pack('<4sIIB',b'USBS',tag,0,0))
                elif len(data)==516:
                    if fail_auth and self.idProduct==probe.PID_NORMAL:
                        raise usb.core.USBError('simulated first auth failure',error_code=-1,errno=5)
                    cmd=(~struct.unpack_from('>H',data)[0])&0xffff
                    answer={1:8,3:4,5:6}[cmd]
                    payload=b'abcd' if cmd==1 else b'\1' if cmd==5 else b''
                    self.pending.append(struct.pack('>HH512s',~answer&0xffff,0,payload))
                    if cmd==5 and self.idProduct==probe.PID_NORMAL:state['service']=True
                else:
                    size,pfunc,sequence=struct.unpack_from('<IHH',data)
                    self_check=data[12:]
                    probe.permitted_service_packet(pfunc,self_check)
                    state['file_reads']+=1
                    value=b'IIIA_TEST_VERSION'
                    response=5 if bad_file else 1
                    self.pending.extend([struct.pack('<IHHBBBB',len(value),pfunc,sequence,0,0,0,response),value])
                return len(data)
            def read(self,ep,length,timeout=None):
                if not self.pending:raise AssertionError('Unexpected USB read')
                data=self.pending.pop(0)
                if len(data)>length:
                    self.pending.insert(0,data[length:]);data=data[:length]
                return array.array('B',data)

        normal=Device(probe.PID_NORMAL);service=Device(0x0336)
        def find(**kwargs):return [service if state['service'] else normal]
        def registry(with_instance=False):
            if state['service']:raise ValueError('Normal mode not present')
            return (123,22 if state['returned'] else 11) if with_instance else 123
        report={'events':[]}
        with patch('usb.backend.libusb1.get_backend',return_value=object()), \
             patch('usb.core.find',side_effect=find), \
             patch('usb.util.release_interface'),patch('usb.util.dispose_resources'), \
             patch('usb_handoff_check.registry_camera',side_effect=registry), \
             patch('service_probe.time.sleep'):
            probe.run(report,lambda:None)
        return report,state

    def test_original_entry_auth_read_and_stop(self):
        r,s=self.exercise()
        self.assertEqual(r['status'],'version_read_and_usb_return_passed')
        self.assertEqual((s['starts'],s['stops'],s['file_reads']),(2,1,1))
        self.assertTrue(r['normal_auth_passed']);self.assertTrue(r['service_auth_passed'])

    def test_first_auth_failure_still_sends_one_stop_and_never_reads(self):
        r,s=self.exercise(fail_auth=True)
        self.assertEqual(r['status'],'failed_do_not_retry')
        self.assertEqual((s['starts'],s['stops'],s['file_reads']),(1,1,0))
        self.assertEqual(r['failure_phase'],'normal_auth')
        self.assertTrue(any(e.get('errno')==5 for e in r['events']))

    def test_bad_version_response_stops_without_retry(self):
        r,s=self.exercise(bad_file=True)
        self.assertEqual(r['status'],'failed_do_not_retry')
        self.assertEqual(s['file_reads'],1);self.assertEqual(s['stops'],1)

    def test_interface_failure_never_enters_service_and_restores_driver(self):
        r,s=self.exercise(fail_initial=True)
        self.assertEqual(r['status'],'failed_do_not_retry')
        self.assertEqual((s['starts'],s['stops'],s['file_reads']),(0,0,0))
        self.assertTrue(r['normal_mode_and_driver_observed'])
        self.assertNotIn('service_start_attempted',r)
        self.assertTrue(any(e.get('backend_error_code')==-99 for e in r['events']))


if __name__=='__main__':unittest.main()
