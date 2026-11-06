from types import SimpleNamespace
from agent.runtime_health import sample_health


def test_health_reconnects_existing_loopback_emulator_after_host_reboot(tmp_path):
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=1 if len(calls) == 1 else 0, stdout='1\n')
    samples = sample_health(tmp_path, {'ANDROID_SERIAL': '127.0.0.1:15555'},
        get=lambda *a, **k: SimpleNamespace(status_code=200), run=run,
        read_memory=lambda: 'MemAvailable: 999999 kB')
    assert samples['android'] is True
    assert calls[1] == ['adb', 'connect', '127.0.0.1:15555']
    assert len(calls) == 3


def test_health_ignores_revoked_nodes_even_if_previously_seen(tmp_path):
    from agent.device_nodes import DeviceStore
    store = DeviceStore(tmp_path / 'device-nodes.sqlite3')
    token = store.enroll('retired', [])
    store.poll('retired', token)
    store.revoke('retired')
    samples = sample_health(tmp_path, {}, get=lambda *args, **kwargs: type('Response', (), {'status_code': 200})(),
                            read_memory=lambda: 'MemAvailable: 999999 kB')
    assert 'node:retired' not in samples


def test_health_probes_distinguish_failure_from_unavailable(tmp_path):
    samples=sample_health(tmp_path,{'ANDROID_SERIAL':'127.0.0.1:15555'},
        get=lambda *a,**k:SimpleNamespace(status_code=200),
        run=lambda *a,**k:SimpleNamespace(returncode=0,stdout='1\n'),
        read_memory=lambda:'MemAvailable: 1048576 kB\n')
    assert samples == {'android':True,'n8n':True,'memory':True}


def test_unconfigured_android_does_not_trigger_false_alert(tmp_path):
    def fail(*a,**k): raise OSError('private error')
    samples=sample_health(tmp_path,{},get=fail,run=fail,read_memory=fail)
    assert samples == {'android':None,'n8n':False,'memory':None}


def test_enrolled_online_node_is_included_in_health_samples(tmp_path):
    from agent.device_nodes import DeviceStore
    store=DeviceStore(tmp_path/'device-nodes.sqlite3')
    token=store.enroll('qa-node',[])
    store.poll('qa-node',token)
    samples=sample_health(tmp_path,{'DAEVIS_MONITOR_NODES':'qa-node'},get=lambda *a,**k:SimpleNamespace(status_code=200),read_memory=lambda:'MemAvailable: 1048576 kB')
    assert samples.get('node:qa-node') is True


def test_optional_pc_offline_is_not_a_server_incident(tmp_path):
    from agent.device_nodes import DeviceStore
    s=DeviceStore(tmp_path/'device-nodes.sqlite3');t=s.enroll('pc-green',[]);s.poll('pc-green',t)
    import sqlite3
    with sqlite3.connect(s.path) as db:db.execute('UPDATE nodes SET seen=1')
    samples=sample_health(tmp_path,{},get=lambda *a,**k:SimpleNamespace(status_code=200),read_memory=lambda:'MemAvailable: 1048576 kB')
    assert 'node:pc-green' not in samples
