"""Owner-only PC labels and short-lived, single-use enrollment on power relay."""
import hashlib
import json
from pathlib import Path
import re
import secrets
import time

from .device_nodes import DeviceStore, handler_for
from .pc_remote import RemoteMixin

BASE='https://game.daehoon.games/pc-power'
CAPABILITIES=['text_message','pc_shutdown','pc_cancel_shutdown']
PHONE_CAPABILITIES=['phone_alarm_at','phone_call']


def parse_phone_command(text):
    """'엄마에게 전화 걸어줘' / '폰 등록 [이름]'. Calls name a person, never a number."""
    text=re.sub(r'^(?:대비스|자비스|데비스)\s*','',text.strip(),flags=re.I)
    if re.fullmatch(r'(?:휴대\s*폰|핸드폰|폰|모바일)\s*(?:등록\s*)?(?:해제|삭제)',text):return 'remove',None
    m=re.fullmatch(r'(?:휴대\s*폰|핸드폰|폰|모바일)\s*등록(?:\s+(.{1,24}))?',text)
    if m:return 'register',(m[1] or '내 폰').strip()
    m=re.fullmatch(r'(.{1,40}?)\s*(?:에게|한테|께)\s*(?:전화|통화)\s*(?:좀\s*)?(?:걸어\s*줘|걸어\s*주세요|해\s*줘|해\s*주세요|걸어|해|부탁해)?',text)
    if not m:return None
    name=m[1].strip()
    return ('call',name) if name and not re.search(r'\d{3,}',name) else None


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


class PCRegistry(RemoteMixin,DeviceStore):
    def __init__(self,path,clock=time.time):
        super().__init__(path,clock)
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS pcs(alias TEXT PRIMARY KEY,label TEXT,node TEXT UNIQUE,is_default INTEGER DEFAULT 0);
                CREATE UNIQUE INDEX IF NOT EXISTS pc_default ON pcs(is_default) WHERE is_default=1;
                CREATE TABLE IF NOT EXISTS pc_invites(digest TEXT PRIMARY KEY,label TEXT,expires REAL);
                CREATE TABLE IF NOT EXISTS pc_shutdowns(job TEXT PRIMARY KEY,accepted REAL,cancel_id TEXT);
                CREATE TABLE IF NOT EXISTS pc_displays(job TEXT PRIMARY KEY,channel TEXT,message TEXT,finished INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS phones(alias TEXT PRIMARY KEY,label TEXT,node TEXT UNIQUE);
                CREATE TABLE IF NOT EXISTS voice_history(job TEXT PRIMARY KEY,node TEXT,via TEXT,text TEXT,reply TEXT,status TEXT,created REAL,finished REAL);
                CREATE INDEX IF NOT EXISTS voice_history_created ON voice_history(created);
                CREATE TABLE IF NOT EXISTS approval_answers(job TEXT,approval TEXT,value TEXT,PRIMARY KEY(job,approval));
            ''')
            if 'kind' not in {r['name'] for r in db.execute('PRAGMA table_info(pc_invites)')}:
                db.execute("ALTER TABLE pc_invites ADD COLUMN kind TEXT DEFAULT 'pc'")
            columns={r['name'] for r in db.execute('PRAGMA table_info(voice_history)')}
            for name in ('thread','topic'):
                if name not in columns:db.execute(f'ALTER TABLE voice_history ADD COLUMN {name} TEXT')

    @staticmethod
    def alias(label):
        if not isinstance(label,str) or not re.fullmatch(r'[A-Za-z0-9가-힣 _-]{1,24}',label.strip()):raise ValueError('invalid PC label')
        value=re.sub(r'\s+','',label).casefold()
        if value in {'서버','server','비서','본체','대비스','자비스','pc','내pc','컴퓨터','내컴퓨터'}:raise ValueError('reserved PC label')
        return value

    def invite(self,label,kind='pc'):
        if kind not in ('pc','phone'):raise ValueError('invalid device kind')
        alias=self.alias(label);table='pcs' if kind=='pc' else 'phones'
        with self._db() as db:
            if db.execute(f'SELECT 1 FROM {table} WHERE alias=?',(alias,)).fetchone():raise ValueError('already registered')
            if db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]>=(16 if kind=='pc' else 4):raise ValueError('registration limit')
            db.execute('DELETE FROM pc_invites WHERE label=? OR expires<=?',(label,self.clock()))
            code=secrets.token_urlsafe(32)
            db.execute('INSERT INTO pc_invites(digest,label,expires,kind) VALUES(?,?,?,?)',(hashlib.sha256(code.encode()).hexdigest(),label.strip(),self.clock()+900,kind))
        return code

    def request(self,source,token,target,action,payload,request_key,ttl_seconds=120):
        # "phone" means the most recently registered phone that is still enrolled.
        if target=='phone':
            with self._db() as db:
                row=db.execute('SELECT p.node FROM phones p JOIN nodes n ON n.name=p.node WHERE n.digest IS NOT NULL ORDER BY p.rowid DESC LIMIT 1').fetchone()
            if not row:raise ValueError('no phone registered')
            target=row['node']
        return super().request(source,token,target,action,payload,request_key,ttl_seconds)

    def redeem(self,code):
        if not isinstance(code,str) or not re.fullmatch(r'[A-Za-z0-9_-]{40,64}',code):raise ValueError('invalid registration')
        digest=hashlib.sha256(code.encode()).hexdigest()
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            invitation=db.execute('SELECT * FROM pc_invites WHERE digest=? AND expires>?',(digest,self.clock())).fetchone()
            if not invitation:raise ValueError('registration expired or used')
            label=invitation['label'];alias=self.alias(label)
            if (invitation['kind'] or 'pc')=='phone':
                if db.execute('SELECT 1 FROM phones WHERE alias=?',(alias,)).fetchone():raise ValueError('already registered')
                token=secrets.token_urlsafe(32);node='phone-'+secrets.token_hex(8)
                db.execute('INSERT INTO nodes(name,digest,capabilities) VALUES(?,?,?)',(node,hashlib.sha256(token.encode()).hexdigest(),json.dumps(PHONE_CAPABILITIES)))
                db.execute('INSERT INTO phones VALUES(?,?,?)',(alias,label,node))
                if db.execute("SELECT 1 FROM nodes WHERE name='voice'").fetchone():
                    db.execute("INSERT OR IGNORE INTO controls VALUES(?,'voice','voice_command')",(node,))
                # The owner's main agent may set alarms on a phone the owner registered.
                if db.execute("SELECT 1 FROM nodes WHERE name='agent-main' AND digest IS NOT NULL").fetchone():
                    db.execute("INSERT OR IGNORE INTO controls VALUES('agent-main',?,'phone_alarm_at')",(node,))
                db.execute('DELETE FROM pc_invites WHERE digest=?',(digest,))
                return {'node':node,'token':token,'label':label,'url':BASE,'actions':PHONE_CAPABILITIES}
            if db.execute('SELECT 1 FROM pcs WHERE alias=?',(alias,)).fetchone():raise ValueError('already registered')
            if db.execute('SELECT count(*) FROM pcs').fetchone()[0]>=16:raise ValueError('PC registration limit')
            token=secrets.token_urlsafe(32);node='pc-'+secrets.token_hex(8)
            default=int(not db.execute('SELECT 1 FROM pcs WHERE is_default=1').fetchone())
            db.execute('INSERT INTO nodes(name,digest,capabilities) VALUES(?,?,?)',(node,hashlib.sha256(token.encode()).hexdigest(),json.dumps(CAPABILITIES)))
            db.execute('INSERT INTO pcs VALUES(?,?,?,?)',(alias,label,node,default))
            if db.execute("SELECT 1 FROM nodes WHERE name='voice'").fetchone():
                db.execute("INSERT OR IGNORE INTO controls VALUES(?,'voice','voice_command')",(node,))
            db.execute('DELETE FROM pc_invites WHERE digest=?',(digest,))
        self.ensure_pc_tasks()  # The main agent may send remote-control steps to the new PC.
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

    # --- voice relay: PCs send recognised speech to the internal 'voice' node ---
    def ensure_voice(self):
        with self._db() as db:
            if not db.execute("SELECT 1 FROM nodes WHERE name='voice'").fetchone():
                # Internal node: its random digest is never handed out, so nothing can poll it over HTTP.
                db.execute("INSERT INTO nodes(name,digest,capabilities) VALUES('voice',?,?)",
                           (hashlib.sha256(secrets.token_bytes(32)).hexdigest(),json.dumps(['voice_command'])))
            for row in db.execute('SELECT node FROM pcs UNION SELECT node FROM phones').fetchall():
                db.execute("INSERT OR IGNORE INTO controls VALUES(?,'voice','voice_command')",(row['node'],))
            if db.execute("SELECT 1 FROM nodes WHERE name='agent-main'").fetchone():
                # Spoken PC/phone/power commands from Discord voice messages come back through the agent.
                db.execute("INSERT OR IGNORE INTO controls VALUES('agent-main','voice','voice_command')")
        self.ensure_pc_tasks()

    def voice_jobs(self):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE');self._expire(db)
            rows=db.execute("SELECT * FROM jobs WHERE node='voice' AND status='queued' ORDER BY created,rowid").fetchall()
            devices={r['node'] for r in db.execute('SELECT node FROM pcs UNION SELECT node FROM phones')}
            for row in rows:
                db.execute("UPDATE jobs SET status='dispatched' WHERE id=?",(row['id'],))
                if row['issuer'] in devices:
                    payload=json.loads(row['payload'])
                    root=db.execute('SELECT thread FROM voice_history WHERE job=?',(payload.get('thread',''),)).fetchone()
                    thread=(root['thread'] or payload['thread']) if root else row['id']
                    db.execute("INSERT OR IGNORE INTO voice_history(job,node,via,text,status,created,thread) VALUES(?,?,?,?,'running',?,?)",
                               (row['id'],row['issuer'],payload.get('via','voice'),payload['text'],row['created'],thread))
        return [{'id':r['id'],'source':r['issuer'],'payload':json.loads(r['payload'])} for r in rows]

    def voice_progress(self,job_id,result):
        with self._db() as db:
            db.execute("UPDATE jobs SET result=? WHERE id=? AND node='voice' AND status='dispatched'",(json.dumps(result,ensure_ascii=False),job_id))

    def voice_finish(self,job_id,status,result):
        if status not in ('succeeded','failed'):raise ValueError('invalid voice status')
        with self._db() as db:
            db.execute("UPDATE jobs SET status=?,result=? WHERE id=? AND node='voice'",(status,json.dumps(result,ensure_ascii=False),job_id))
            topic=result.get('topic') if isinstance(result.get('topic'),str) and re.fullmatch(r'[0-9a-f]{32}',result.get('topic')) else None
            db.execute('UPDATE voice_history SET status=?,reply=?,finished=?,topic=COALESCE(?,topic) WHERE job=?',
                       (status,str(result.get('text') or result.get('reply') or ''),self.clock(),topic,job_id))
            self._prune_voice(db)

    def _prune_voice(self,db):
        # Finished relay jobs only keep the shared job table under its 1000-row cap;
        # the request history lives in voice_history for 30 days.
        now=self.clock()
        old=[r['id'] for r in db.execute("SELECT id FROM jobs WHERE node IN ('voice','agent-main') AND action='voice_command' AND status NOT IN ('queued','dispatched') AND created<?",(now-86400,))]
        for job_id in old:
            db.execute('DELETE FROM jobs WHERE id=?',(job_id,));db.execute('DELETE FROM approval_answers WHERE job=?',(job_id,))
        db.execute('DELETE FROM voice_history WHERE created<?',(now-30*86400,))

    def _pc_auth(self,node,token):
        """The owner's registered PCs and phones."""
        self._auth(node,token)
        with self._db() as db:
            if not db.execute('SELECT 1 FROM pcs WHERE node=? UNION SELECT 1 FROM phones WHERE node=?',(node,node)).fetchone():
                raise PermissionError('registered devices only')

    def thread_topic(self,job_id):
        """The main agent's conversation for the thread that request belongs to, if it has one yet."""
        with self._db() as db:
            row=db.execute('''SELECT topic FROM voice_history WHERE topic IS NOT NULL AND thread=(SELECT thread FROM voice_history WHERE job=?)
                              ORDER BY created DESC LIMIT 1''',(job_id,)).fetchone()
        return row['topic'] if row else None

    def history(self,node,token,before=None,limit=20,q=None):
        """Owner's requests from any registered device, newest first, paged by created time; q searches text and answers."""
        self._pc_auth(node,token)
        if type(limit) is not int or not 1<=limit<=50:raise ValueError('limit must be 1..50')
        if before is not None and (type(before) not in (int,float) or before!=before):raise ValueError('invalid cursor')
        if q is not None and (not isinstance(q,str) or len(q)>100):raise ValueError('invalid search')
        like='%'+(q or '').replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
        with self._db() as db:
            self._expire(db)
            rows=db.execute('''SELECT h.*,COALESCE(p.label,f.label) AS label,j.status AS live FROM voice_history h LEFT JOIN pcs p ON p.node=h.node
                               LEFT JOIN phones f ON f.node=h.node LEFT JOIN jobs j ON j.id=h.job
                               WHERE h.created<? AND (h.text LIKE ? ESCAPE '\\' OR h.reply LIKE ? ESCAPE '\\')
                               ORDER BY h.created DESC,h.rowid DESC LIMIT ?''',
                            (9e18 if before is None else before,like,like,limit+1)).fetchall()
        items=[]
        for r in rows[:limit]:
            status=r['status']
            if status=='running' and r['live'] not in ('queued','dispatched'):status=r['live'] or 'unknown'
            items.append({'id':r['job'],'pc':r['label'] or '등록 해제된 기기','via':r['via'],'text':r['text'],
                          'reply':r['reply'] or '','status':status,'created':r['created'],'finished':r['finished'],'thread':r['thread'] or r['job']})
        with self._db() as db:me=db.execute('SELECT label FROM pcs WHERE node=? UNION SELECT label FROM phones WHERE node=?',(node,node)).fetchone()
        return {'items':items,'more':len(rows)>limit,'me':me['label'] if me else ''}

    def agent_progress(self,token,job_id,result):
        """The main agent's current step for a forwarded request; returns owner answers waiting for it."""
        self._auth('agent-main',token)
        encoded=json.dumps(result,ensure_ascii=False)
        if not isinstance(result,dict) or len(encoded)>2048:raise ValueError('invalid progress')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute("UPDATE jobs SET result=? WHERE id=? AND node='agent-main' AND status='dispatched'",(encoded,job_id)).rowcount:
                raise ValueError('job is not running')
            answers=[{'approval':r['approval'],'value':r['value']} for r in db.execute('SELECT approval,value FROM approval_answers WHERE job=? ORDER BY rowid',(job_id,))]
            db.execute('DELETE FROM approval_answers WHERE job=?',(job_id,))
        return {'answers':answers}

    def approve(self,node,token,voice_id,approval,value):
        """Payment answer from the PC that made the request; the agent picks it up on its next progress report."""
        self._pc_auth(node,token)
        with self._db() as db:
            if not db.execute("SELECT 1 FROM jobs WHERE id=? AND node='voice' AND issuer=?",(voice_id,node)).fetchone():
                raise PermissionError('not a request from this PC')
        if value not in ('allow','deny') or not isinstance(approval,str) or not re.fullmatch(r'[0-9a-f]{16}',approval):
            raise ValueError('invalid approval answer')
        with self._db() as db:
            forwarded=db.execute("SELECT id FROM jobs WHERE node='agent-main' AND request_key=? AND status='dispatched'",('voice-'+voice_id,)).fetchone()
            if not forwarded:raise ValueError('request is not waiting at the main agent')
            db.execute('INSERT OR IGNORE INTO approval_answers(job,approval,value) VALUES(?,?,?)',(forwarded['id'],approval,value))
        return True

    def enroll_agent(self):
        return self.enroll('agent-main',['voice_command'])

    def agent_online(self):
        with self._db() as db:
            row=db.execute("SELECT seen,digest FROM nodes WHERE name='agent-main'").fetchone()
        return bool(row and row['digest'] and row['seen'] and self.clock()-row['seen']<=20)

    def forward_to_agent(self,text,request_key,via='voice',topic=None):
        payload={'text':text} if via=='voice' else {'text':text,'via':via}
        if topic:payload['topic']=topic
        return self.enqueue('agent-main','voice_command',payload,request_key,300)

    def phone_inventory(self):
        states={n['name']:n for n in self.list_nodes()}
        with self._db() as db:phones=[dict(r) for r in db.execute('SELECT * FROM phones ORDER BY label')]
        return [{**p,'online':bool(states.get(p['node'],{}).get('enabled') and states.get(p['node'],{}).get('last_seen')
                    and self.clock()-states[p['node']]['last_seen']<=20)} for p in phones]

    def remove_phones(self):
        with self._db() as db:rows=[dict(r) for r in db.execute('SELECT * FROM phones')]
        for row in rows:self.revoke(row['node'])
        with self._db() as db:db.execute('DELETE FROM phones')
        return [r['label'] for r in rows]

    def phone_call(self,label,payload,request_key):
        with self._db() as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM phones ORDER BY label')]
            if label is not None:rows=[r for r in rows if r['alias']==self.alias(label)]
            if not rows:raise ValueError('no phone registered; use 폰 등록')
            # Prefer a phone whose call receiver is connected (an app-only registration never polls).
            online=[r for r in rows for n in [db.execute('SELECT seen,digest FROM nodes WHERE name=?',(r['node'],)).fetchone()]
                    if n and n['digest'] and n['seen'] and self.clock()-n['seen']<=20]
            phone=(online or rows)[0]
            node=db.execute('SELECT seen,digest FROM nodes WHERE name=?',(phone['node'],)).fetchone()
            if not node or not node['digest'] or not node['seen'] or self.clock()-node['seen']>20:raise ValueError('phone offline')
        job=self.enqueue(phone['node'],'phone_call',payload,request_key,25)
        return {**job,'label':phone['label'],'node':phone['node']}

    def ack(self,node,token,job_id,status,result=None):
        super().ack(node,token,job_id,status,result)
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
            if self.path.startswith('/share/'):return self.share_get()
            filenames={'/client.py':'device-node.py','/install.ps1':'install-pc.ps1','/phone-install.sh':'install-phone.sh'}
            if self.path not in filenames:self.send_error(404);return
            data=(Path(public_dir)/filenames[self.path]).read_bytes()
            self.send_response(200);self.send_header('Content-Type','text/plain; charset=utf-8')
            self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(data)))
            self.end_headers();self.wfile.write(data)

        def do_POST(self):
            if self.path in ('/history','/progress','/approve'):return self.desktop_post()
            if self.path=='/pc/upload':return self.upload()
            if self.path in ('/agent/poll','/pc/poll','/pc/download','/pc/list','/pc/share') or self.path=='/request-status' and self._peek_wait():return self.remote_post()
            if self.path.startswith('/pair/'):return self.pair_post()
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
            self.reply_json(code,reply)

        def pair_post(self):
            # New Daevis apps ask to connect; connected apps and the main agent answer (agent/device_pairing.py).
            from . import device_pairing
            self.connection.settimeout(10)
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=1024:raise ValueError('invalid length')
                body=json.loads(self.rfile.read(length))
                if not isinstance(body,dict):raise ValueError('invalid body')
                auth=self.headers.get('Authorization','')
                reply=device_pairing.handle(registry,self.path,body,auth[7:] if auth.startswith('Bearer ') else None);code=200
            except PermissionError:reply={'error':'unauthorized'};code=401
            except (ValueError,TypeError,KeyError) as error:reply={'error':str(error)[:120]};code=400
            except Exception:reply={'error':'pairing unavailable'};code=500
            self.reply_json(code,reply)

        def _peek_wait(self):
            # /request-status with 'wait' is a long poll; read the body once and keep it for remote_post.
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=4096:return False
                self._body=self.rfile.read(length)
                body=json.loads(self._body)
            except (ValueError,OSError):
                self._body=b'';return True
            if isinstance(body,dict) and 'wait' in body:return True
            self._replay(self._body);return False

        def _replay(self,data):
            import io
            self.rfile=io.BytesIO(data)

        def _token(self):
            authorization=self.headers.get('Authorization','')
            if not authorization.startswith('Bearer '):raise PermissionError('missing bearer')
            return authorization[7:]

        def remote_post(self):
            """Remote PC control: long polls, the main agent's PC list and attachment downloads."""
            self.connection.settimeout(40)
            try:
                data=getattr(self,'_body',None)
                if data is None:
                    length=int(self.headers.get('Content-Length','0'))
                    if not 0<length<=4096:raise ValueError('invalid length')
                    data=self.rfile.read(length)
                body=json.loads(data);token=self._token()
                if not isinstance(body,dict):raise ValueError('invalid object')
                if self.path=='/agent/poll' and set(body)=={'node','wait'}:
                    reply={'job':registry.agent_poll(body['node'],token,body['wait'])}
                elif self.path=='/pc/poll' and set(body)=={'node','wait'}:
                    reply={'job':registry.task_poll(body['node'],token,body['wait'])}
                elif self.path=='/request-status' and set(body)=={'node','id','wait'}:
                    reply=registry.wait_status(body['node'],token,body['id'],body['wait'])
                elif self.path=='/pc/list' and set(body)=={'node'}:
                    if body['node']!='agent-main':raise PermissionError('main agent only')
                    reply={'pcs':registry.agent_pcs(token)}
                elif self.path=='/pc/share' and set(body)=={'node','id'}:
                    reply=registry.file_share(body['node'],token,body['id'])
                    reply['url']=BASE+'/share/'+reply['token']
                elif self.path=='/pc/download' and set(body)=={'node','id'}:
                    name,content=registry.file_get(body['node'],token,body['id'])
                    from urllib.parse import quote
                    self.send_response(200);self.send_header('Content-Type','application/octet-stream')
                    self.send_header('X-Daevis-Name',quote(name));self.send_header('Cache-Control','no-store')
                    self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content)
                    return
                else:raise ValueError('invalid request')
                code=200
            except PermissionError:code,reply=401,{'error':'unauthorized'}
            except (ValueError,TypeError,KeyError,OverflowError):code,reply=400,{'error':'invalid request'}
            except Exception:code,reply=500,{'error':'internal failure'}
            self.reply_json(code,reply)

        def share_get(self):
            """Owner download link for a file too big for Discord; streams the file."""
            from urllib.parse import quote
            try:
                name,path=registry.shared(self.path[len('/share/'):])
                size=path.stat().st_size
            except (ValueError,OSError):
                self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type','application/octet-stream')
            self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+quote(name))
            self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(size));self.end_headers()
            with path.open('rb') as f:
                for chunk in iter(lambda:f.read(1<<20),b''):self.wfile.write(chunk)

        def upload(self):
            """Raw bytes with X-Daevis-Node / -Audience / -Name (URL-encoded) headers."""
            from urllib.parse import unquote
            from .pc_remote import FILE_LIMIT
            self.connection.settimeout(120)
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=FILE_LIMIT:raise ValueError('invalid length')
                token=self._token()
                node,audience=self.headers.get('X-Daevis-Node',''),self.headers.get('X-Daevis-Audience','')
                name=unquote(self.headers.get('X-Daevis-Name',''))
                if not registry.authenticate(node,token):raise PermissionError('unauthorized')
                spool=Path(registry.path).parent/'pc-files'/('upload-'+secrets.token_hex(8)+'.part')
                spool.parent.mkdir(parents=True,exist_ok=True)
                try:
                    with spool.open('wb') as f:
                        left=length
                        while left:
                            chunk=self.rfile.read(min(left,1<<20))
                            if not chunk:raise ValueError('upload ended early')
                            f.write(chunk);left-=len(chunk)
                    reply=registry.file_put(node,token,audience,name,spool);code=200
                finally:
                    spool.unlink(missing_ok=True)
            except PermissionError:code,reply=401,{'error':'unauthorized'}
            except (ValueError,TypeError,KeyError,OverflowError):code,reply=400,{'error':'invalid request'}
            except Exception:code,reply=500,{'error':'internal failure'}
            self.reply_json(code,reply)

        def reply_json(self,code,reply):
            data=json.dumps(reply,ensure_ascii=False).encode()
            self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)

        def desktop_post(self):
            """Desktop app history and payment answers; the main agent's progress reports."""
            self.connection.settimeout(10)
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=4096:raise ValueError('invalid length')
                body=json.loads(self.rfile.read(length))
                authorization=self.headers.get('Authorization','')
                if not authorization.startswith('Bearer '):raise PermissionError('missing bearer')
                token=authorization[7:]
                if not isinstance(body,dict):raise ValueError('invalid object')
                if self.path=='/history' and {'node'}<=set(body)<={'node','before','limit','q'}:
                    reply=registry.history(body['node'],token,body.get('before'),body.get('limit',20),body.get('q'))
                elif self.path=='/progress' and set(body)=={'node','id','result'}:
                    if body['node']!='agent-main':raise PermissionError('main agent only')
                    reply=registry.agent_progress(token,body['id'],body['result'])
                elif self.path=='/approve' and set(body)=={'node','id','approval','value'}:
                    reply={'ok':registry.approve(body['node'],token,body['id'],body['approval'],body['value'])}
                else:raise ValueError('invalid request')
                code=200
            except PermissionError:code,reply=401,{'error':'unauthorized'}
            except (ValueError,TypeError,KeyError,OverflowError):code,reply=400,{'error':'invalid request'}
            except Exception:code,reply=500,{'error':'internal failure'}
            self.reply_json(code,reply)
    return Handler
