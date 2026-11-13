"""Authenticated APK distribution on the existing phone relay. CI publishes only tested builds."""
import hashlib
import hmac
import io
import json
from pathlib import Path
import threading
import time
import zipfile

MAX_APK = 20_000_000


class UpdateStore:
    def __init__(self, folder):
        self.folder=Path(folder); self.folder.mkdir(parents=True,exist_ok=True)
        self.lock=threading.Lock()

    def metadata(self):
        try:return json.loads((self.folder/'latest.json').read_text())
        except FileNotFoundError:return {'version':0}

    def publish(self, version, data):
        if type(version) is not int or not 0<version<2**31 or not 0<len(data)<=MAX_APK:
            raise ValueError('invalid update')
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if not {'AndroidManifest.xml','classes.dex'}<=set(z.namelist()):raise ValueError('invalid APK')
        except zipfile.BadZipFile:raise ValueError('invalid APK') from None
        digest=hashlib.sha256(data).hexdigest()
        meta={'version':version,'sha256':digest,'size':len(data)}
        with self.lock:
            current=self.metadata()
            if version<current['version'] or version==current['version'] and current.get('sha256')!=digest:
                raise ValueError('older or replaced build')
            target=self.folder/(str(version)+'-'+digest+'.apk')
            if not target.exists():target.write_bytes(data)
            temp=self.folder/'latest.tmp';temp.write_text(json.dumps(meta));temp.replace(self.folder/'latest.json')
        return meta

    def download(self, version):
        meta=self.metadata()
        if type(version) is not int or version!=meta['version'] or version==0:raise ValueError('update changed')
        return (self.folder/(str(version)+'-'+meta['sha256']+'.apk')).read_bytes()

    def report(self, node, body):
        if type(body['installed']) is not int or not 0<body['installed']<2**31 or type(body['install_allowed']) is not bool:
            raise ValueError('invalid client version')
        if not isinstance(body['state'],str) or len(body['state'])>80 or not isinstance(body['version_name'],str) or len(body['version_name'])>80:
            raise ValueError('invalid client status')
        with self.lock:
            path=self.folder/'clients.json'
            try:clients=json.loads(path.read_text())
            except FileNotFoundError:clients={}
            clients[node]={k:body[k] for k in ('installed','version_name','state','install_allowed')}
            clients[node]['reported_at']=time.time()
            temp=self.folder/'clients.tmp';temp.write_text(json.dumps(clients));temp.replace(path)


def update_handler(parent, registry, folder, publisher_digest):
    store=UpdateStore(folder)
    class Handler(parent):
        def do_POST(self):
            if self.path not in ('/phone/update/check','/phone/update/apk','/phone/update/publish'):
                return super().do_POST()
            self.connection.settimeout(45)
            binary=False
            try:
                auth=self.headers.get('Authorization','')
                if not auth.startswith('Bearer '):raise PermissionError()
                token=auth[7:]
                size=int(self.headers.get('Content-Length','0'))
                if self.path.endswith('/publish'):
                    if not publisher_digest or not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(),publisher_digest):raise PermissionError()
                    if not 0<size<=MAX_APK:raise ValueError('invalid APK size')
                    reply=store.publish(int(self.headers.get('X-Daevis-Version','0')),self.rfile.read(size))
                else:
                    if not 0<size<=1024:raise ValueError('invalid request')
                    body=json.loads(self.rfile.read(size))
                    expected=[{'node','version'}] if self.path.endswith('/apk') else [{'node'},{'node','installed','version_name','state','install_allowed'}]
                    if not isinstance(body,dict) or set(body) not in expected:raise ValueError('invalid request')
                    registry._auth(body['node'],token)
                    with registry._db() as db:
                        if not db.execute('SELECT 1 FROM phones WHERE node=?',(body['node'],)).fetchone():raise PermissionError()
                    if self.path.endswith('/apk'):reply=store.download(body['version']);binary=True
                    else:
                        if 'installed' in body:store.report(body['node'],body)
                        reply=store.metadata()
                code=200
            except PermissionError:code,reply=403,{'error':'unauthorized'}
            except (ValueError,TypeError,KeyError):code,reply=400,{'error':'invalid update request'}
            except Exception:code,reply=503,{'error':'update unavailable'}
            data=reply if binary else json.dumps(reply).encode()
            self.send_response(code);self.send_header('Content-Type','application/vnd.android.package-archive' if binary else 'application/json')
            self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    return Handler
