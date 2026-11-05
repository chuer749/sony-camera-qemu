#!/usr/bin/env python3
"""Replay the published ZV-E10 connection helpers without replacing USB drivers."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parent
REFERENCE=ROOT/'local/reference-replay/linux'
REPORT=ROOT/'local/reference-asuser-20261101.json'
MARKER=ROOT/'local/reference-asuser-attempt-used.json'
LOG=ROOT/'local/reference-asuser-20261101.log'


def source_check():
    lock=json.loads((REFERENCE/'REFERENCE.json').read_text())
    for name,expected in lock['files'].items():
        if hashlib.sha256((REFERENCE/name).read_bytes()).hexdigest()!=expected:
            raise ValueError('Reference file modified: '+name)
    src=REFERENCE/'vendor/Sony-PMCA-RE'
    command=['git','-c','safe.directory='+str(src.resolve()),'-C',str(src)]
    if subprocess.check_output(command+['rev-parse','HEAD'],text=True).strip()!=lock['pmca_commit']:
        raise ValueError('Wrong upstream PMCA revision')
    if subprocess.check_output(command+['status','--porcelain','--untracked-files=no'],text=True).strip():
        raise ValueError('Upstream PMCA source modified')
    return lock


def read_version(common,camera):
    response,data=common.read_path(camera,'/version.txt')
    if response not in (0,1) or not data or len(data)>128:
        raise ValueError('Invalid /version.txt response')
    return data.decode('ascii').rstrip('\0\r\n')


def require_context(uid,manager_uid,expected_uid):
    if uid!=0 or manager_uid!=expected_uid:
        raise ValueError('Require administrator identity in the owner user bootstrap session')


def attempt(report,save,common):
    import usb.core
    import usb.util
    from pmca.commands import usb as commands
    from pmca.usb.driver.generic import libusb as transport
    sys.path.insert(0,str(ROOT.parent))
    from usb_handoff_check import registry_camera

    auth_class=commands.SonySenserAuthDevice
    original_start=auth_class.start;original_stop=auth_class.stop
    original_backend=transport.UsbBackend
    auth_objects=[];failure_backends=[];normal=None;expected_port=None
    location=instance=None;started=time.monotonic();state={'starts':0,'stops':0}
    def event(kind,**data):
        report['events'].append({'t':round(time.monotonic()-started,4),'kind':kind,**data});save()
    def observed_start(self):
        auth_objects.append(self);state['starts']+=1
        if state['starts']>2:raise ValueError('Refusing repeated connection attempts')
        event('upstream_start_called',pid=hex(self.driver.getId()[1]))
        return original_start(self)
    def observed_stop(self):
        state['stops']+=1;event('upstream_stop_called')
        return original_stop(self)
    def callback(camera):
        event('authenticated_callback_entered')
        report['version_txt']=read_version(common,camera)
        report['version_read_passed']=True;event('version_read_passed')
    def at_port(pids):
        found=[d for d in usb.core.find(find_all=True,idVendor=0x054c)
               if d.idProduct in pids and (d.bus,tuple(d.port_numbers or ()))==expected_port]
        if len(found)>1:raise ValueError('Ambiguous devices at original port')
        return found[0] if found else None
    def timeout(signum,frame):raise TimeoutError('Reference session exceeded 90 seconds')
    old_alarm=signal.signal(signal.SIGALRM,timeout)
    try:
        location,instance=registry_camera(with_instance=True)
        devices=list(usb.core.find(find_all=True,idVendor=0x054c))
        if len(devices)!=1 or devices[0].idProduct!=0x0d98:
            raise ValueError('Require exactly one normal-mode IIIA')
        normal=devices[0];expected_port=(normal.bus,tuple(normal.port_numbers or ()))
        if not expected_port[1]:raise ValueError('Missing physical USB port identity')
        event('normal_identity_checked',location_id=hex(location))
        # Observe only the lifecycle. Original UsbBackend and authentication
        # implementation remain intact; all requests come from upstream code.
        auth_class.start=observed_start;auth_class.stop=observed_stop
        signal.alarm(90)
        common.connect(callback,driver='libusb')
        if not report.get('version_read_passed'):
            raise ValueError('Reference entry returned without the version callback')
    except BaseException as error:
        report['error']=type(error).__name__+': '+str(error)
        tb=error.__traceback__
        while tb is not None:
            obj=tb.tb_frame.f_locals.get('self')
            if isinstance(obj,original_backend):failure_backends.append(obj)
            tb=tb.tb_next
        event('reference_call_failed')
    finally:
        signal.alarm(0);signal.signal(signal.SIGALRM,old_alarm)
        if state['starts'] and not state['stops']:
            try:
                target=None
                for _ in range(6):
                    target=at_port((0x0336,0x02a9))
                    if target is not None:break
                    time.sleep(.5)
                target=target or at_port((0x0d98,))
                if target is None:raise ValueError('Matching device unavailable for cleanup')
                target.ctrl_transfer(0x43,1,0xc800,0x2855,b'',timeout=5000)
                report['outer_stop_transfer_returned']=True
                usb.util.dispose_resources(target)
            except BaseException as e:report['cleanup_stop_error']=str(e)
        handles=[a.driver.backend.dev for a in auth_objects]+[b.dev for b in failure_backends]
        for device in handles:
            try:usb.util.release_interface(device,0)
            except Exception as e:event('release_note',error=str(e))
        if normal is not None:
            try:
                _,current=registry_camera(with_instance=True)
                if current==instance:
                    target=next((d for d in handles if d.idProduct==0x0d98),normal)
                    if not target.is_kernel_driver_active(0):
                        target.attach_kernel_driver(0);event('normal_driver_attach_returned')
            except Exception as e:event('attach_note',error=str(e))
        for device in handles+([normal] if normal is not None else []):
            try:usb.util.dispose_resources(device)
            except Exception as e:event('dispose_note',error=str(e))
        if normal is not None:
            for _ in range(24):
                try:
                    d=at_port((0x0d98,))
                    if d is not None:
                        try:
                            if d.is_kernel_driver_active(0) and registry_camera()==location:
                                report['normal_mode_and_driver_observed']=True;break
                        finally:usb.util.dispose_resources(d)
                except Exception:pass
                time.sleep(.5)
        auth_class.start=original_start;auth_class.stop=original_stop
        report['original_transport_class_retained']=transport.UsbBackend is original_backend
        report['service_start_calls']=state['starts'];report['service_stop_calls']=state['stops']
        passed=report.get('version_read_passed') and report.get('normal_mode_and_driver_observed') and not report.get('error')
        report['status']='version_read_and_normal_usb_observed' if passed else 'failed_do_not_retry'
        event('finished')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true')
    parser.add_argument('--attempt',action='store_true');args=parser.parse_args()
    lock=source_check()
    sys.path.insert(0,str(REFERENCE/'host'))
    import zve10_common as common
    from service_probe import library_info
    info=library_info(Path(os.environ['LIBUSB_PATH']))
    from pmca.commands.usb import senserShellCommand
    manager_uid=int(subprocess.check_output(['/bin/launchctl','manageruid'],text=True).strip())
    report={'time_utc':datetime.now(timezone.utc).isoformat(),'status':'prepared','reference_commit':lock['commit'],
            'pmca_commit':lock['pmca_commit'],'python':sys.version,'effective_uid':os.geteuid(),
            'bootstrap_manager_uid':manager_uid,'library':info,'events':[],
            'terminal_requested':False,'file_write_requested':False,'memory_requested':False,
            'allowed_read_path':'/version.txt','whole_camera_health_verified':False}
    if args.check:
        print(json.dumps(report,indent=2));return 0
    if not args.attempt:parser.error('Use --check or --attempt')
    require_context(os.geteuid(),manager_uid,ROOT.stat().st_uid)
    with MARKER.open('x') as f:json.dump({'time_utc':report['time_utc'],'report':str(REPORT)},f)
    with REPORT.open('x') as f:json.dump(report,f)
    def save():REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    def terminate(signum,frame):raise KeyboardInterrupt('Termination requested')
    old=signal.signal(signal.SIGTERM,terminate)
    try:attempt(report,save,common)
    except BaseException as e:
        report['status']='failed_do_not_retry';report['outer_error']=str(e);save()
    finally:
        signal.signal(signal.SIGTERM,old);owner=ROOT.stat()
        for p in (MARKER,REPORT):os.chown(p,owner.st_uid,owner.st_gid)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report['status']=='version_read_and_normal_usb_observed' else 1


if __name__=='__main__':raise SystemExit(main())
