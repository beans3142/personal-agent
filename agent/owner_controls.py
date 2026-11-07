"""Owner-authenticated report preferences and bounded power operations."""
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
import time
import re
from contextlib import contextmanager

REPORTS={'trend_daily':'일간 트렌드','trend_weekly':'주간 트렌드','tech':'주요 기술 블로그','jobs':'채용 공고','events':'행사'}
BASE='https://game.daehoon.games/pc-power'


def power_intent_allowed(action,text,pc=None):
    if action not in ('server_stop','pc_shutdown','pc_cancel','pc_wake'):return True
    if not isinstance(text,str):return False
    # Quoted instructions and capability questions are not execution requests.
    plain=re.sub(r'```[\s\S]*?```|`[^`]*`|["“][^"”]*["”]|\x27[^\x27]*\x27','',text)
    if re.search(r'만약|가정|끝나면|완료되면|나중에|다음에|내일',plain):return False
    if re.search(r'라는|라고\s*(?:말|하|보내|적)|번역|예시|설명|문장|방법|이유|왜|말하면|어떻게|가능(?:해|하니|한가)|수\s*있(?:어|나|니)',plain):return False
    pc_target=bool(re.search(r'(?<![a-z])PC(?![a-z])|컴퓨터|노트북|데스크톱|데스크탑|컴\s|컴(?:을|좀)|내\s*컴',plain,re.I) or pc and pc in plain)
    server=bool(re.search(r'서버|본체',plain,re.I) or not pc_target and re.search(r'비서|대비스',plain))
    if action=='server_stop' and not server:return False
    if action=='server_stop' and re.search(r'(?:서버|본체)(?:를|는|은|가)?\s*(?:말고|아니라|끄지|종료하지)',plain):return False
    if action.startswith('pc_') and not pc_target:return False
    if action=='pc_cancel':return bool(re.search(r'취소|끄지\s*마|종료하지\s*마',plain))
    if re.search(r'끄지|꺼지지|종료하지|하지\s*마|취소|않[아으]?|아니',plain):return False
    verb=r'(?:켜|깨워|기동)' if action=='pc_wake' else r'(?:꺼|끄|종료|셧다운|전원.{0,6}(?:내려|내리|차단)|shutdown)'
    ending=r'(?:\s*(?:줘|줄래|주세요|해줘|해줄래|해|해라|시켜줘|시켜|하자|자|고\s*싶어|하고\s*싶어|를\s*부탁해))?'
    target_tail=r'\s+(?:내\s*)?(?:데스크톱|데스크탑|노트북|PC|컴퓨터)' if action=='pc_shutdown' else r'(?!)'
    return bool(re.search(verb+ending+r'\s*(?:[.!?]*\s*$|[,;]|그리고|'+target_tail+r'\s*[.!?]*$)',plain,re.I))


class ControlStore:
    def __init__(self,path,clock=time.time):
        self.path=Path(path);self.clock=clock;self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS preferences(kind TEXT PRIMARY KEY,enabled INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS logins(digest TEXT PRIMARY KEY,expires REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS sessions(digest TEXT PRIMARY KEY,expires REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,result TEXT NOT NULL,created REAL NOT NULL)')
        self.path.chmod(0o600)
    @contextmanager
    def db(self):
        connection=sqlite3.connect(self.path,timeout=10)
        try:
            with connection:yield connection
        finally:connection.close()
    def preferences(self,changes=None):
        changes={} if changes is None else changes
        if not isinstance(changes,dict) or any(k not in REPORTS or type(v) is not bool for k,v in changes.items()):raise ValueError('invalid report settings')
        with self.db() as db:
            for key,value in changes.items():db.execute('INSERT INTO preferences VALUES(?,?) ON CONFLICT(kind) DO UPDATE SET enabled=excluded.enabled',(key,int(value)))
            saved=dict(db.execute('SELECT kind,enabled FROM preferences'))
        return {k:bool(saved.get(k,True)) for k in REPORTS}
    def issue_login(self):
        code=secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute('DELETE FROM logins WHERE expires<=?',(self.clock(),))
            db.execute('INSERT INTO logins VALUES(?,?)',(self.digest(code),self.clock()+900))
        return code
    @staticmethod
    def digest(value):return hashlib.sha256(str(value).encode()).hexdigest()
    def login(self,code):
        with self.db() as db:
            deleted=db.execute('DELETE FROM logins WHERE digest=? AND expires>?',(self.digest(code),self.clock())).rowcount
            if not deleted:raise ValueError('login expired or used')
            token=secrets.token_urlsafe(32)
            db.execute('INSERT INTO sessions VALUES(?,?)',(self.digest(token),self.clock()+30*86400))
        return token
    def authorized(self,token):
        with self.db() as db:return bool(db.execute('SELECT 1 FROM sessions WHERE digest=? AND expires>?',(self.digest(token),self.clock())).fetchone())

    def once(self,key,perform):
        if not isinstance(key,str) or not 1<=len(key)<=200:raise ValueError('invalid operation id')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT result FROM operations WHERE id=?',(key,)).fetchone()
            if row:return json.loads(row[0])
            value=perform();db.execute('INSERT INTO operations VALUES(?,?,?)',(key,json.dumps(value,default=str),self.clock()))
            return value


def settings_html():
    return '''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>보고서 수신 설정</title><style>body{font:17px/1.8 system-ui;max-width:650px;margin:30px auto;padding:18px}label{display:block;padding:12px;border-bottom:1px solid #ddd}input{width:22px;height:22px;margin-right:12px}button{padding:10px;font:inherit;margin-top:18px}a{color:#245cc0}</style><h1>보고서 수신 설정</h1><p>보고서 5종의 Discord 자동 수신 여부입니다. 끄더라도 웹의 기록은 열어볼 수 있습니다. 기본값은 5종 모두 매일 오전 9시 수신입니다. 주간 보고서는 한 주의 내용을 다루며, 매일 갱신본을 받을 수 있습니다.</p><p id="status">확인 중…</p><form id="prefs" hidden></form><p><a href="https://personal-agent-brief.vercel.app/">보고서 목록으로</a></p><script>
const base='/pc-power/briefing/api';const status=document.getElementById('status');const form=document.getElementById('prefs');
async function request(data){const r=await fetch(base,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});if(!r.ok)throw new Error('auth');return r.json()}
async function start(){try{let code=location.hash.slice(1);history.replaceState(null,'',location.pathname);if(code)await request({action:'login',code});const data=await request({action:'get'});status.textContent='웹·Discord 버튼·채팅이 같은 설정을 사용합니다.';form.hidden=false;for(const [key,name]of Object.entries(data.labels)){const label=document.createElement('label');const input=document.createElement('input');input.type='checkbox';input.name=key;input.checked=data.preferences[key];label.append(input,document.createTextNode(name));form.append(label)}const b=document.createElement('button');b.textContent='수신 설정 저장';form.append(b)}catch(e){status.textContent='본인 확인이 필요합니다. 대비스 Discord DM에 “브리핑 웹 설정”이라고 보내 받은 15분짜리 링크로 접속하세요.'}}
form.addEventListener('submit',async e=>{e.preventDefault();const changes={};for(const i of form.querySelectorAll('input'))changes[i.name]=i.checked;try{await request({action:'set',changes});status.textContent='저장했습니다.'}catch(e){status.textContent='저장하지 못했습니다. 다시 로그인해 주세요.'}});start();</script></html>'''


def control_handler(parent,store,bot,token_digest):
    from http.cookies import SimpleCookie
    class Handler(parent):
        def reply(self,code,value,cookie=None):
            data=json.dumps(value,ensure_ascii=False).encode()
            self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store')
            if cookie:self.send_header('Set-Cookie','daevis_brief='+cookie+'; Path=/pc-power/briefing; HttpOnly; Secure; SameSite=Strict; Max-Age=2592000')
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        def do_GET(self):
            if self.path.rstrip('/')!='/briefing':return super().do_GET()
            data=settings_html().encode();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer');self.send_header('X-Frame-Options','DENY');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        def do_POST(self):
            if self.path not in ('/owner','/briefing/api'):return super().do_POST()
            self.connection.settimeout(10)
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=4096:raise ValueError('invalid request')
                body=json.loads(self.rfile.read(length));cookie=None
                if not isinstance(body,dict):raise ValueError('invalid request')
                if self.path=='/owner':
                    token=self.headers.get('Authorization','').removeprefix('Bearer ')
                    if not token or not hmac.compare_digest(store.digest(token),token_digest):return self.reply(403,{'error':'unauthorized'})
                    result=dispatch_control(store,bot,body)
                else:
                    if self.headers.get('Origin')!='https://game.daehoon.games':return self.reply(403,{'error':'origin'})
                    if body.get('action')=='login':
                        cookie=store.login(body.get('code',''));result={'ok':True}
                    else:
                        jar=SimpleCookie();jar.load(self.headers.get('Cookie',''));session=jar.get('daevis_brief')
                        if not session or not store.authorized(session.value):return self.reply(403,{'error':'login required'})
                        if body.get('action') not in ('get','set'):raise ValueError('invalid action')
                        result={'preferences':store.preferences(body.get('changes') if body['action']=='set' else None),'labels':REPORTS}
                self.reply(200,result,cookie)
            except (ValueError,KeyError,TypeError):self.reply(400,{'error':'invalid request or expired login'})
            except Exception:self.reply(503,{'error':'control unavailable'})
    return Handler


def dispatch_control(store,bot,body):
    action=body.get('action')
    allowed={'preferences','web_login','pc_list','pc_shutdown','pc_cancel','pc_wake','server_status','server_stop'}
    if action not in allowed or any(k not in ('action','changes','pc','request_id','owner_text','chat_scope') for k in body):raise ValueError('unsupported action')
    if 'pc' in body and (not isinstance(body['pc'],str) or not 1<=len(body['pc'])<=40):raise ValueError('invalid PC')
    if not power_intent_allowed(action,body.get('owner_text'),body.get('pc')):raise ValueError('current request does not clearly authorize this target/action')
    if action=='preferences':return {'preferences':store.preferences(body.get('changes')),'labels':REPORTS}
    if action=='web_login':return {'url':BASE+'/briefing/#'+store.issue_login()}
    if action=='pc_list':return {'pcs':bot.pc_registry.inventory()}
    selected=None
    if action in ('pc_shutdown','pc_cancel','pc_wake'):
        selected=bot.pc_registry.resolve(body.get('pc'));items=bot.pc_registry.inventory()
        normalize=lambda s:re.sub(r'\s+','',str(s)).casefold().replace('데스크탑','데스크톱')
        original=normalize(body.get('owner_text',''));label=normalize(selected['label'])
        named={r['node'] for r in items if normalize(r['label']) in original}
        if named and selected['node'] not in named:raise ValueError('selected PC differs from named PC')
        category=next((name for name in ('노트북','데스크톱') if name in original),None)
        if category:
            choices=[r for r in items if category in normalize(r['label'])]
            if selected['node'] not in {r['node'] for r in choices} or len(choices)>1 and label not in original:raise ValueError('ambiguous PC category')
        if body.get('pc') and label not in original and not selected.get('is_default') and not category:raise ValueError('non-default PC not named')
    if action in ('pc_shutdown','pc_cancel'):
        pc=selected
        key=body['request_id']+':'+action+':'+pc['node']
        explicit=label in original or bool(re.search(r'(?:내)?(?:pc|컴퓨터|컴)(?:을|좀|전원|꺼|종료)',original))
        if action=='pc_shutdown' and not explicit:
            import asyncio
            return store.once(key,lambda:asyncio.run_coroutine_threadsafe(bot.confirm_pc_shutdown(pc['label']),bot.loop).result(timeout=15))
        job=bot.pc_registry.submit(body.get('pc'),'shutdown' if action=='pc_shutdown' else 'cancel',key)
        scope=body.get('chat_scope','')
        if action=='pc_shutdown' and isinstance(scope,str) and re.fullmatch(r'(?:guild:)?[0-9]{10,22}',scope):
            import asyncio
            async def publish():
                channel=await bot.fetch_channel(int(scope.removeprefix('guild:')))
                return await bot.publish_shutdown(job,channel)
            try:job.update(asyncio.run_coroutine_threadsafe(publish(),bot.loop).result(timeout=12))
            except Exception:pass  # The command was submitted; never repeat it for a UI failure.
        return job
    if action=='pc_wake':
        from .pc_wake import wake_pc
        pc=selected;config=Path('data/pc-wake.json')
        if json.loads(config.read_text()).get('pc_node')!=pc['node']:raise ValueError('no wake config for selected PC')
        return store.once(body['request_id']+':wake:'+pc['node'],lambda:wake_pc(config))
    if action=='server_status':
        s=bot.control.status();return {'state':s['state']}
    if action=='server_stop':
        from threading import Timer
        def queue():
            Timer(30,bot.control.stop).start();return {'status':'scheduled','delay_seconds':30}
        return store.once(body['request_id']+':server_stop',queue)
    raise ValueError('unsupported action')


class OwnerClient:
    def __init__(self,token_file,post=None):self.token_file=Path(token_file);self.post=post
    def request(self,body):
        import requests
        response=(self.post or requests.post)(BASE+'/owner',json=body,headers={'Authorization':'Bearer '+self.token_file.read_text().strip()},timeout=15)
        response.raise_for_status();return response.json()
