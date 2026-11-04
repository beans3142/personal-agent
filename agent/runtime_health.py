"""Read-only health probes; no model calls, actions, or device wakeups."""
from pathlib import Path
import subprocess
import httpx


def sample_health(data, env, *, get=httpx.get, run=subprocess.run,
                  read_memory=lambda:Path('/proc/meminfo').read_text()):
    samples={'android':None,'n8n':None,'memory':None}
    try:
        samples['n8n']=get('http://127.0.0.1:5678/healthz',timeout=5).status_code==200
    except Exception: samples['n8n']=False
    if env.get('ANDROID_SERIAL'):
        try:
            result=run(['adb','-s',env['ANDROID_SERIAL'],'shell','getprop','sys.boot_completed'],capture_output=True,text=True,timeout=5)
            if result.returncode and env['ANDROID_SERIAL'] == '127.0.0.1:15555':
                # ADB's local TCP registration is lost at host reboot. Reconnect
                # only our existing loopback emulator, never a remote device.
                run(['adb','connect',env['ANDROID_SERIAL']],capture_output=True,text=True,timeout=5)
                result=run(['adb','-s',env['ANDROID_SERIAL'],'shell','getprop','sys.boot_completed'],capture_output=True,text=True,timeout=5)
            samples['android']=result.returncode==0 and result.stdout.strip()=='1'
        except Exception: samples['android']=False
    try:
        available=next(int(line.split()[1]) for line in read_memory().splitlines() if line.startswith('MemAvailable:'))
        samples['memory']=available>=384*1024
    except Exception: pass
    db=Path(data)/'device-nodes.sqlite3'
    if db.exists():
        from .device_nodes import DeviceStore
        for node in DeviceStore(db).list_nodes():
            if node.get('enabled') and node.get('last_seen'):
                samples['node:'+node['name']]=bool(node['online'])
    return samples
