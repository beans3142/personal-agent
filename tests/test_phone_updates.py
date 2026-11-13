import hashlib
import io
import zipfile
import pytest
from agent.phone_updates import UpdateStore


def apk():
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as z:
        z.writestr(zipfile.ZipInfo('AndroidManifest.xml'),'test');z.writestr(zipfile.ZipInfo('classes.dex'),'test')
    return data.getvalue()


def test_publish_and_download_are_version_bound(tmp_path):
    store=UpdateStore(tmp_path)
    metadata=store.publish(10,apk())
    assert metadata['version']==10
    assert metadata['sha256']==hashlib.sha256(apk()).hexdigest()
    assert store.download(10)==apk()
    with pytest.raises(ValueError):store.download(9)


def test_rejects_invalid_apk_and_downgrade(tmp_path):
    store=UpdateStore(tmp_path)
    with pytest.raises(ValueError):store.publish(1,b'not an apk')
    store.publish(2,apk())
    with pytest.raises(ValueError):store.publish(1,apk())
    assert store.metadata()['version']==2


def test_same_build_is_idempotent_but_not_replaceable(tmp_path):
    store=UpdateStore(tmp_path)
    first=store.publish(1,apk())
    assert store.publish(1,apk())==first
    changed=io.BytesIO(apk())
    with zipfile.ZipFile(changed,'a') as z:z.writestr('asset','different')
    with pytest.raises(ValueError):store.publish(1,changed.getvalue())


def test_empty_store_and_invalid_versions(tmp_path):
    store=UpdateStore(tmp_path)
    assert store.metadata()=={'version':0}
    for version in [0,-1,True,'2']:
        with pytest.raises(ValueError):store.publish(version,apk())


def test_http_requires_separate_publisher_and_registered_phone(tmp_path):
    import threading
    import requests
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from agent.pc_control import PCRegistry
    from agent.phone_updates import update_handler
    registry=PCRegistry(tmp_path/'nodes.sqlite3')
    phone=registry.enroll('phone-test',['phone_task'])
    pc=registry.enroll('pc-test',['text_message'])
    with registry._db() as db:db.execute("INSERT INTO phones(alias,label,node) VALUES('phone','test','phone-test')")
    class Quiet(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
    token='test-publisher'
    handler=update_handler(Quiet,registry,tmp_path/'updates',hashlib.sha256(token.encode()).hexdigest())
    server=ThreadingHTTPServer(('127.0.0.1',0),handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base='http://127.0.0.1:'+str(server.server_port)
    try:
        assert requests.post(base+'/phone/update/check',json={'node':'phone-test'},timeout=3).status_code==403
        assert requests.post(base+'/phone/update/check',json={'node':'pc-test'},headers={'Authorization':'Bearer '+pc},timeout=3).status_code==403
        assert requests.post(base+'/phone/update/publish',data=apk(),headers={'Authorization':'Bearer '+phone,'X-Daevis-Version':'3'},timeout=3).status_code==403
        r=requests.post(base+'/phone/update/publish',data=apk(),headers={'Authorization':'Bearer '+token,'X-Daevis-Version':'3'},timeout=3)
        assert r.status_code==200
        headers={'Authorization':'Bearer '+phone}
        r=requests.post(base+'/phone/update/check',json={'node':'phone-test'},headers=headers,timeout=3)
        assert r.json()['version']==3 and r.headers['Cache-Control']=='no-store'
        r=requests.post(base+'/phone/update/apk',json={'node':'phone-test','version':3},headers=headers,timeout=3)
        assert r.content==apk()
    finally:server.shutdown();server.server_close();thread.join()
