#!/usr/bin/env python3
"""One diagnostic session using the original PMCA flow and an explicit libusb.

Only FileControl READ of /version.txt is allowed after authentication.
This is a new, separately authorized experiment; the old stop guard stays intact.
"""
import argparse
import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parent
REV='a82f5baaa8e9c3d9f28f94699e860fb2e48cc8e0'
SOURCE=ROOT/'local/pmca-reference'
LIBRARY=ROOT/'local/libusb-runtime/lib/libusb-1.0.dylib'
PID_NORMAL=0x0d98
SERVICE_PIDS=(0x0336,0x02a9)
VERSION_PAYLOAD=struct.pack('<HH',2,16)+b'/version.txt'+b'\0'*4


def library_info(path):
    class Version(ctypes.Structure):
        _fields_=[('major',ctypes.c_uint16),('minor',ctypes.c_uint16),
                  ('micro',ctypes.c_uint16),('nano',ctypes.c_uint16),
                  ('rc',ctypes.c_char_p),('describe',ctypes.c_char_p)]
    lib=ctypes.CDLL(str(path));lib.libusb_get_version.restype=ctypes.POINTER(Version)
    v=lib.libusb_get_version().contents
    result={'path':str(path),'version':[v.major,v.minor,v.micro,v.nano],
            'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    if result['version'][:3]!=[1,0,30]:
        raise ValueError('This experiment requires libusb 1.0.30')
    return result


def validate_source():
    def git(*args):
        return subprocess.check_output(['git','-c','safe.directory='+str(SOURCE),'-C',str(SOURCE),*args],text=True).strip()
    if git('rev-parse','HEAD')!=REV or git('status','--porcelain','--untracked-files=no'):
        raise ValueError('PMCA must be an unchanged checkout of the pinned revision')


class SmallSink(io.BytesIO):
    def write(self,data):
        if self.tell()+len(data)>4096:
            raise ValueError('Version response exceeds 4096 bytes')
        return super().write(data)


def permitted_service_packet(pfunc,payload):
    if pfunc!=0xff01 or payload!=VERSION_PAYLOAD:
        raise ValueError('Only fixed FileControl READ /version.txt is allowed')


def run(report,save):
    # Imports and source checks themselves do not communicate with the camera.
    validate_source()
    sys.path.insert(0,str(SOURCE));sys.path.insert(0,str(ROOT.parent))
    import usb.core
    import usb.util
    import usb.backend.libusb1
    backend=usb.backend.libusb1.get_backend(find_library=lambda name:str(LIBRARY))
    if backend is None:raise ValueError('Explicit libusb backend could not be loaded')
    original_get_backend=usb.backend.libusb1.get_backend
    usb.backend.libusb1.get_backend=lambda *a,**k:backend
    from pmca.commands import usb as command
    from pmca.usb.driver.generic import libusb as transport
    from pmca.usb.driver.generic import GenericUsbException
    from usb_handoff_check import registry_camera

    started=time.monotonic();deadline=started+90
    opened=[];events=report['events'];state={'phase':'preflight','transfers':0,'starts':{},'stop_calls':0}
    original_backend=transport.UsbBackend
    original_auth=command.SonySenserAuthDevice
    original_device=command.SonySenserDevice
    expected_port=None;normal=None;location=None;instance=None

    def event(kind,**data):
        events.append({'t':round(time.monotonic()-started,4),'phase':state['phase'],'kind':kind,**data})
        save()

    def budget():
        if time.monotonic()>deadline or state['transfers']>=128:
            raise TimeoutError('Session time/transfer budget exceeded')
        state['transfers']+=1

    def record_error(error):
        event('usb_error',type=type(error).__name__,message=str(error),
              errno=getattr(error,'errno',None),backend_error_code=getattr(error,'backend_error_code',None))

    class DiagnosticBackend(original_backend):
        def __init__(self,device):
            self.dev=device;self.detached=False
            if (device.idVendor!=0x054c or device.idProduct not in (PID_NORMAL,*SERVICE_PIDS)
                    or (device.bus,tuple(device.port_numbers or ()))!=expected_port):
                raise ValueError('Refusing another USB device/physical port')
            super().__init__(device);opened.append(self)

        def reset(self):
            budget()
            active=self.dev.is_kernel_driver_active(0)
            event('upstream_reset_hook',driver_active=active,pid=hex(self.dev.idProduct))
            if active:
                # Exactly the detach used by the upstream reset hook.
                self.dev.detach_kernel_driver(0);self.detached=True
                event('driver_detached')

        def write(self,ep,data):
            budget();event('bulk_write',endpoint=hex(ep),length=len(data))
            try:
                count=self.dev.write(ep,data)
                if count!=len(data):raise ValueError('Short USB write')
            except usb.core.USBError as e:
                record_error(e);raise GenericUsbException() from e

        def read(self,ep,length,timeout=None):
            budget();event('bulk_read',endpoint=hex(ep),length=length)
            try:return self.dev.read(ep,length,timeout).tobytes()
            except usb.core.USBError as e:
                record_error(e);raise GenericUsbException() from e

        def vendorRequestOut(self,request,value,index,data=b''):
            if (request,value,index,data) not in [(1,0x37ff,0xd7aa,b''),(1,0xc800,0x2855,b'')]:
                raise ValueError('Control request outside start/stop whitelist')
            if value==0x37ff:
                budget();pid=self.dev.idProduct
                if state['starts'].get(pid):raise ValueError('No repeated service start')
                state['starts'][pid]=True;report['service_start_attempted']=True
                event('start_control',pid=hex(pid))
            else:
                state['stop_calls']+=1;event('stop_control',pid=hex(self.dev.idProduct))
            try:
                self.dev.ctrl_transfer(0x43,request,value,index,data)
                event('control_returned',operation='start' if value==0x37ff else 'stop')
                if value==0xc800:report['stop_transfer_returned']=True
            except usb.core.USBError as e:
                record_error(e);raise GenericUsbException() from e

    class DiagnosticAuth(original_auth):
        def authenticate(self):
            state['phase']='normal_auth' if self.driver.getId()[1]==PID_NORMAL else 'service_auth'
            event('authentication_begin')
            super().authenticate()
            report[state['phase']+'_passed']=True;event('authentication_passed')
            state['phase']='reenumeration'

    class ReadOnlyDevice(original_device):
        def sendSenserPacket(self,pfunc,data,oData=None):
            permitted_service_packet(pfunc,data)
            return super().sendSenserPacket(pfunc,data,oData)

    def callback(camera):
        state['phase']='version_read';event('callback_entered')
        sink=SmallSink()
        response,_=camera.dev.sendSenserPacket(0xff01,VERSION_PAYLOAD,sink)
        value=sink.getvalue()
        if response not in (0,1) or not value or len(value)>128:
            raise ValueError('Unexpected version response')
        report['version_txt']=value.decode('ascii').rstrip('\0\r\n')
        report['version_read_passed']=True;event('version_read_passed',bytes=len(value))
        state['phase']='upstream_stop'

    def at_port(pids):
        found=[d for d in usb.core.find(find_all=True,idVendor=0x054c,backend=backend)
               if d.idProduct in pids and (d.bus,tuple(d.port_numbers or ()))==expected_port]
        if len(found)>1:raise ValueError('Ambiguous physical port')
        return found[0] if found else None

    try:
        location,instance=registry_camera(with_instance=True)
        devices=list(usb.core.find(find_all=True,idVendor=0x054c,backend=backend))
        if len(devices)!=1 or devices[0].idProduct!=PID_NORMAL:
            raise ValueError('Expected exactly one Sony camera in normal IIIA mode')
        normal=devices[0];expected_port=(normal.bus,tuple(normal.port_numbers or ()))
        if not expected_port[1]:raise ValueError('Missing USB port identity')
        cfg=list(normal.get_active_configuration())
        if len(cfg)!=1 or (cfg[0].bInterfaceClass,cfg[0].bInterfaceSubClass,cfg[0].bInterfaceProtocol)!=(8,6,80):
            raise ValueError('Unexpected normal-mode interface')
        event('identity_checked',descriptor_model='ILCE-7RM3A',location_id=hex(location))
        transport.UsbBackend=DiagnosticBackend
        command.SonySenserAuthDevice=DiagnosticAuth
        command.SonySenserDevice=ReadOnlyDevice
        state['phase']='original_pmca_connect'
        # Same original entry point used by zve10_common.connect().
        command.senserShellCommand(driverName='libusb',complete=callback)
        if not report.get('version_read_passed'):raise ValueError('Upstream returned without completing callback')
    except BaseException as e:
        report['failure_phase']=state['phase'];report['error']=type(e).__name__+': '+str(e)
        event('session_failed')
    finally:
        state['phase']='cleanup'
        if report.get('service_start_attempted') and not state['stop_calls']:
            # Upstream's initial authentication is outside its finally block.
            # Establish our outer cleanup before invoking the original entry.
            try:
                target=None
                for _ in range(6):
                    target=at_port(SERVICE_PIDS)
                    if target is not None:break
                    time.sleep(0.5)
                target=target or at_port((PID_NORMAL,))
                if target is None:raise ValueError('No matching device for cleanup stop')
                DiagnosticBackend(target).vendorRequestOut(1,0xc800,0x2855)
            except BaseException as e:
                report['cleanup_stop_error']=type(e).__name__+': '+str(e);event('cleanup_stop_failed')
        for b in opened:
            try:usb.util.release_interface(b.dev,0)
            except Exception as e:event('release_note',error=str(e))
        if any(b.detached for b in opened):
            try:
                _,current=registry_camera(with_instance=True)
                if current==instance:
                    captured=next(b for b in opened if b.detached)
                    captured.dev.attach_kernel_driver(0);event('normal_driver_attach_returned')
            except Exception as e:event('attach_note',error=str(e))
        for b in opened:
            try:usb.util.dispose_resources(b.dev)
            except Exception as e:event('dispose_note',error=str(e))
        if normal is not None:
            usb.util.dispose_resources(normal)
        for _ in range(24):
            try:
                d=at_port((PID_NORMAL,)) if expected_port else None
                if d is not None:
                    try:
                        if d.is_kernel_driver_active(0) and registry_camera()==location:
                            report['normal_mode_and_driver_observed']=True;break
                    finally:usb.util.dispose_resources(d)
            except Exception:pass
            time.sleep(0.5)
        transport.UsbBackend=original_backend
        command.SonySenserAuthDevice=original_auth
        command.SonySenserDevice=original_device
        usb.backend.libusb1.get_backend=original_get_backend
        good=(report.get('version_read_passed') and report.get('stop_transfer_returned')
              and report.get('normal_mode_and_driver_observed') and not report.get('error'))
        report['status']='version_read_and_usb_return_passed' if good else 'failed_do_not_retry'
        report['whole_camera_health_verified']=False
        event('finished')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--check',action='store_true');ap.add_argument('--attempt-once',action='store_true')
    ap.add_argument('--output',type=Path)
    args=ap.parse_args()
    sys.pycache_prefix=str(ROOT/'local/python-cache')
    validate_source();info=library_info(LIBRARY)
    if args.check:
        sys.path.insert(0,str(SOURCE))
        from pmca.commands.usb import senserShellCommand
        print(json.dumps({'source':REV,'library':info,'imports':'passed','usb_access':False},indent=2));return 0
    if not args.attempt_once or args.output is None:ap.error('Use --check or --attempt-once --output NEW_FILE')
    if sys.platform!='darwin' or os.geteuid()!=0:raise SystemExit('macOS administrator execution required')
    os.environ['LIBUSB_DEBUG']='4'
    marker=ROOT/'local/zve10-method-attempt-used.json'
    report={'time_utc':datetime.now(timezone.utc).isoformat(),'method':'original PMCA + libusb 1.0.30',
            'source':REV,'library':info,'events':[],'status':'starting',
            'old_service_stop_marker_retained':(ROOT.parent/'SERVICE_TESTS_STOPPED.txt').exists(),
            'terminal_enabled':False,'memory_access_requested':False,'backup_access_requested':False,
            'file_write_requested':False,'allowed_read_path':'/version.txt'}
    args.output=args.output.resolve();args.output.parent.mkdir(parents=True,exist_ok=True)
    # Exclusive files prevent accidental second execution, even after failure.
    with marker.open('x') as f:json.dump({'time_utc':report['time_utc'],'report':str(args.output)},f)
    with args.output.open('x') as f:json.dump(report,f)
    os.chmod(args.output,0o600)
    def save():
        with args.output.open('w') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    def interrupted(signum,frame):raise KeyboardInterrupt('Termination requested')
    old=signal.signal(signal.SIGTERM,interrupted)
    try:run(report,save)
    except BaseException as e:
        report['status']='failed_do_not_retry';report['outer_error']=type(e).__name__+': '+str(e);save()
    finally:
        signal.signal(signal.SIGTERM,old)
        owner=ROOT.stat()
        for p in (args.output,marker):os.chown(p,owner.st_uid,owner.st_gid)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report['status']=='version_read_and_usb_return_passed' else 1


if __name__=='__main__':raise SystemExit(main())
