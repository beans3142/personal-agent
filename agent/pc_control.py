"""Owner-only PC labels and short-lived, single-use enrollment on power relay."""
import hashlib
import json
from pathlib import Path
import re
import secrets
import time

from .device_nodes import DeviceStore, handler_for

BASE='https://game.daehoon.games/pc-power'
CAPABILITIES=['text_message','pc_shutdown','pc_cancel_shutdown']


def parse_pc_command(text):
    text=re.sub(r'^(?:대비스|자비스|데비스)\s*','',text.strip(),flags=re.I)
    text=re.sub(r'^(?:비서\s*서버|서버|본체)\s*(?:말고|가\s*아니라)\s*','',text)
    if re.fullmatch(r'(?:PC|컴퓨터)\s*(?:목록|상태|관리)',text,re.I):return 'list',None
    for pattern,action in [(r'PC\s*등록\s+(.+)','register'),(r'PC\s*삭제\s+(.+)','remove'),(r'기본\s*PC\s+(.+)','default')]:
        m=re.fullmatch(pattern,text,re.I)
        if m:return action,m[1].strip()
    m=re.fullmatch(r'(.+?)\s*(종료\s*취소|끄기\s*취소|꺼줘|꺼|종료해줘|종료|켜줘|켜|켜기)',text,re.I)
    if not m:return None
    target=m[1].strip()
    if re.search(r'서버|비서|본체|server',target,re.I):return None
    default=bool(re.fullmatch(r'(?:내\s*)?(?:PC|컴|컴퓨터)',target,re.I))
    if not default and not (re.search(r'PC|컴퓨터|데스크톱|데스크탑|노트북|랩탑',target,re.I)):return None
    target=target if not default else None
    return ('cancel' if '취소' in m[2] else 'wake' if m[2].startswith('켜') else 'shutdown'),target


class PCRegistry(DeviceStore):
    def __init__(self,path,clock=time.time):
        super().__init__(path,clock)
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS pcs(alias TEXT PRIMARY KEY,label TEXT,node TEXT UNIQUE,is_default INTEGER DEFAULT 0);
                CREATE UNIQUE INDEX IF NOT EXISTS pc_default ON pcs(is_default) WHERE is_default=1;
                CREATE TABLE IF NOT EXISTS pc_invites(digest TEXT PRIMARY KEY,label TEXT,expires REAL);
                CREATE TABLE IF NOT EXISTS pc_shutdowns(job TEXT PRIMARY KEY,accepted REAL,cancel_id TEXT);
                CREATE TABLE IF NOT EXISTS pc_displays(job TEXT PRIMARY KEY,channel TEXT,message TEXT,finished INTEGER DEFAULT 0);
            ''')

    @staticmethod
    def alias(label):
        if not isinstance(label,str) or not re.fullmatch(r'[A-Za-z0-9가-힣 _-]{1,24}',label.strip()):raise ValueError('invalid PC label')
        value=re.sub(r'\s+','',label).casefold()
        if value in {'서버','server','비서','본체','대비스','자비스','pc','내pc','컴퓨터','내컴퓨터'}:raise ValueError('reserved PC label')
        return value

    def invite(self,label):
        alias=self.alias(label)
        with self._db() as db:
            if db.execute('SELECT 1 FROM pcs WHERE alias=?',(alias,)).fetchone():raise ValueError('already registered')
            if db.execute('SELECT count(*) FROM pcs').fetchone()[0]>=16:raise ValueError('PC registration limit')
            db.execute('DELETE FROM pc_invites WHERE label=? OR expires<=?',(label,self.clock()))
            code=secrets.token_urlsafe(32)
            db.execute('INSERT INTO pc_invites VALUES(?,?,?)',(hashlib.sha256(code.encode()).hexdigest(),label.strip(),self.clock()+900))
        return code

    def redeem(self,code):
        if not isinstance(code,str) or not re.fullmatch(r'[A-Za-z0-9_-]{40,64}',code):raise ValueError('invalid registration')
        digest=hashlib.sha256(code.encode()).hexdigest()
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            invitation=db.execute('SELECT * FROM pc_invites WHERE digest=? AND expires>?',(digest,self.clock())).fetchone()
            if not invitation:raise ValueError('registration expired or used')
            label=invitation['label'];alias=self.alias(label)
            if db.execute('SELECT 1 FROM pcs WHERE alias=?',(alias,)).fetchone():raise ValueError('already registered')
            if db.execute('SELECT count(*) FROM pcs').fetchone()[0]>=16:raise ValueError('PC registration limit')
            token=secrets.token_urlsafe(32);node='pc-'+secrets.token_hex(8)
            default=int(not db.execute('SELECT 1 FROM pcs WHERE is_default=1').fetchone())
            db.execute('INSERT INTO nodes(name,digest,capabilities) VALUES(?,?,?)',(node,hashlib.sha256(token.encode()).hexdigest(),json.dumps(CAPABILITIES)))
            db.execute('INSERT INTO pcs VALUES(?,?,?,?)',(alias,label,node,default))
            db.execute('DELETE FROM pc_invites WHERE digest=?',(digest,))
        return {'node':node,'token':token,'label':label,'url':BASE,'actions':CAPABILITIES}

    def resolve(self,label):
        with self._db() as db:
            row=db.execute('SELECT * FROM pcs WHERE '+('alias=?' if label is not None else 'is_default=1'),
                           (self.alias(label),) if label is not None else ()).fetchone()
            # Exact registered names win, even if the name itself ends in PC.
            if not row and label is not None and re.search(r'PC$',label,re.I):
                short=re.sub(r'\s*PC$','',label,flags=re.I).strip()
                if short:row=db.execute('SELECT * FROM pcs WHERE alias=?',(self.alias(short),)).fetchone()
            if not row and label is not None:
                # Preserve exact names before resolving conversational prefixes/spelling.
                normalize=lambda s:re.sub(r'\s+','',s).casefold().replace('데스크탑','데스크톱')
                query=normalize(label)
                choices={query}
                if label.startswith('내 '):choices.add(normalize(label[2:]))
                matches=[r for r in db.execute('SELECT * FROM pcs') if normalize(r['label']) in choices]
                if len(matches)==1:row=matches[0]
        if not row:raise ValueError('unknown PC; use PC 목록')
        return dict(row)

    def inventory(self):
        states={n['name']:n for n in self.list_nodes()}
        with self._db() as db:pcs=[dict(r) for r in db.execute('SELECT * FROM pcs ORDER BY label')]
        return [{**p,'online':bool(states.get(p['node'],{}).get('enabled') and states.get(p['node'],{}).get('last_seen')
                    and self.clock()-states[p['node']]['last_seen']<=20)} for p in pcs]

    def set_default(self,label):
        pc=self.resolve(label)
        with self._db() as db:
            db.execute('UPDATE pcs SET is_default=0')
            db.execute('UPDATE pcs SET is_default=1 WHERE node=?',(pc['node'],))
        return pc['label']

    def remove(self,label):
        pc=self.resolve(label);self.revoke(pc['node'])
        with self._db() as db:db.execute('DELETE FROM pcs WHERE node=?',(pc['node'],))
        return pc['label']

    def submit(self,label,action,request_key):
        pc=self.resolve(label)
        with self._db() as db:
            node=db.execute('SELECT seen,digest FROM nodes WHERE name=?',(pc['node'],)).fetchone()
            if not node or not node['digest'] or not node['seen'] or self.clock()-node['seen']>20:raise ValueError('PC offline')
            if action=='cancel':
                db.execute("UPDATE jobs SET status='cancelled' WHERE node=? AND action='pc_shutdown' AND status='queued'",(pc['node'],))
        if action not in ('shutdown','cancel'):raise ValueError('unsupported PC action')
        job=self.enqueue(pc['node'],'pc_shutdown' if action=='shutdown' else 'pc_cancel_shutdown',{},request_key,15)
        if action=='cancel':
            with self._db() as db:
                db.execute("INSERT INTO pc_shutdowns(job,cancel_id) SELECT id,? FROM jobs WHERE node=? AND action='pc_shutdown' AND status IN ('dispatched','succeeded') ON CONFLICT(job) DO UPDATE SET cancel_id=excluded.cancel_id",(job['id'],pc['node']))
        return {**job,'label':pc['label'],'node':pc['node']}

    def ack(self,node,token,job_id,status):
        super().ack(node,token,job_id,status)
        if status=='succeeded':
            with self._db() as db:
                row=db.execute("SELECT action FROM jobs WHERE id=?",(job_id,)).fetchone()
                if row and row['action']=='pc_shutdown':
                    db.execute('INSERT INTO pc_shutdowns(job,accepted) VALUES(?,?) ON CONFLICT(job) DO UPDATE SET accepted=COALESCE(pc_shutdowns.accepted,excluded.accepted)',(job_id,self.clock()))

    def shutdown_progress(self,job_id):
        with self._db() as db:
            self._expire(db)
            row=db.execute("SELECT j.*,m.accepted,m.cancel_id,p.label,n.seen,n.digest FROM jobs j LEFT JOIN pc_shutdowns m ON m.job=j.id LEFT JOIN pcs p ON p.node=j.node LEFT JOIN nodes n ON n.name=j.node WHERE j.id=? AND j.action='pc_shutdown'",(job_id,)).fetchone()
            if not row:raise ValueError('unknown shutdown')
            r=dict(row);newer=bool(db.execute("SELECT 1 FROM jobs WHERE node=? AND action='pc_shutdown' AND rowid>(SELECT rowid FROM jobs WHERE id=?)",(r['node'],job_id)).fetchone());cancel=db.execute('SELECT status FROM jobs WHERE id=?',(r['cancel_id'],)).fetchone() if r['cancel_id'] else None
        remaining=max(0,int(r['accepted']+60-self.clock())) if r['accepted'] is not None else None
        online=bool(r['digest'] and r['seen'] and self.clock()-r['seen']<=20)
        phase=r['status']
        if phase=='cancelled' or cancel and cancel['status']=='succeeded':phase='cancelled'
        elif cancel:phase='cancelling' if cancel['status'] in ('queued','dispatched') else 'cancel_uncertain'
        elif r['accepted'] is not None:
            phase='waiting' if remaining else 'checking' if online else 'disconnected'
        elif phase=='succeeded':phase='unverified'  # Legacy receipt has no countdown timestamp.
        if newer and phase not in ('cancelled','failed','expired'):phase='superseded'
        return {'id':job_id,'node':r['node'],'label':r['label'] or '등록 해제된 PC','phase':phase,
                'remaining':remaining,'created':r['created'],'power_off_confirmed':False,
                'cancellable':bool(r['label'] and r['digest'] and not cancel and not newer and
                    (r['status']=='queued' or online and r['status'] in ('dispatched','succeeded') and (remaining is None or remaining>5)))}

    def cancel_shutdown(self,job_id,request_key):
        # Target is the immutable job/node, never the current default or reused label.
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE');self._expire(db)
            r=db.execute("SELECT j.*,m.accepted,m.cancel_id,n.seen,n.digest FROM jobs j LEFT JOIN pc_shutdowns m ON m.job=j.id LEFT JOIN nodes n ON n.name=j.node WHERE j.id=? AND j.action='pc_shutdown'",(job_id,)).fetchone()
            if not r:raise ValueError('unknown shutdown')
            if db.execute("SELECT 1 FROM jobs WHERE node=? AND action='pc_shutdown' AND rowid>(SELECT rowid FROM jobs WHERE id=?)",(r['node'],job_id)).fetchone():raise ValueError('newer shutdown exists')
            if r['cancel_id']:return {'id':r['cancel_id'],'status':'queued','deduplicated':True}
            if r['status']=='cancelled':return {'id':job_id,'status':'cancelled','deduplicated':True}
            if r['status']=='queued':
                db.execute("UPDATE jobs SET status='cancelled' WHERE id=?",(job_id,))
                return {'id':job_id,'status':'cancelled'}
            if r['status'] not in ('dispatched','succeeded') or not r['digest'] or self.clock()-r['seen']>20 or r['accepted'] is not None and self.clock()>=r['accepted']+55:
                raise ValueError('cancellation window not confirmed')
            cancel_id=secrets.token_hex(16);now=self.clock()
            deadline=(r['accepted'] if r['accepted'] is not None else r['created'])+55
            if now>=deadline:raise ValueError('cancellation window elapsed')
            db.execute("INSERT INTO jobs(id,node,action,payload,request_key,created,expires,status) VALUES(?,?,'pc_cancel_shutdown','{}',?,?,?,'queued')",(cancel_id,r['node'],request_key,now,min(now+15,deadline)))
            db.execute('INSERT INTO pc_shutdowns(job,accepted,cancel_id) VALUES(?,?,?) ON CONFLICT(job) DO UPDATE SET cancel_id=excluded.cancel_id',(job_id,r['accepted'],cancel_id))
            return {'id':cancel_id,'status':'queued'}

    def save_display(self,job_id,channel,message):
        with self._db() as db:db.execute('INSERT OR IGNORE INTO pc_displays(job,channel,message) VALUES(?,?,?)',(job_id,str(channel),str(message)))

    def displays(self,all_records=False):
        with self._db() as db:return [dict(r) for r in db.execute('SELECT * FROM pc_displays'+('' if all_records else ' WHERE finished=0'))]

    def finish_display(self,job_id):
        with self._db() as db:db.execute('UPDATE pc_displays SET finished=1 WHERE job=?',(job_id,))

    def job(self,job_id):
        with self._db() as db:
            self._expire(db);r=db.execute('SELECT status FROM jobs WHERE id=?',(job_id,)).fetchone()
        return dict(r) if r else {'status':'unknown'}


def pc_handler(registry,public_dir):
    parent=handler_for(registry)
    class Handler(parent):
        def do_GET(self):
            filenames={'/client.py':'device-node.py','/install.ps1':'install-pc.ps1'}
            if self.path not in filenames:self.send_error(404);return
            data=(Path(public_dir)/filenames[self.path]).read_bytes()
            self.send_response(200);self.send_header('Content-Type','text/plain; charset=utf-8')
            self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(data)))
            self.end_headers();self.wfile.write(data)

        def do_POST(self):
            if self.path!='/enroll':return super().do_POST()
            self.connection.settimeout(10)
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=256:raise ValueError('invalid length')
                body=json.loads(self.rfile.read(length))
                if not isinstance(body,dict) or set(body)!={'code'}:raise ValueError('invalid enrollment')
                reply=registry.redeem(body['code'])
                reply['sha256']=hashlib.sha256((Path(public_dir)/'device-node.py').read_bytes()).hexdigest();code=200
            except (ValueError,TypeError,KeyError):reply={'error':'invalid or expired registration'};code=400
            except Exception:reply={'error':'registration unavailable'};code=500
            data=json.dumps(reply).encode()
            self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    return Handler
