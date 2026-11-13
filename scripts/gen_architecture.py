"""Generate docs/architecture.svg with embedded real brand icons.

Usage: python scripts/gen_architecture.py docs/architecture.svg
Needs Pillow. Icons are downloaded once from pinned CDNs and embedded, so GitHub renders them.
"""
import base64, io, re, sys, tempfile, urllib.request
from pathlib import Path
from PIL import Image

OUT = Path(sys.argv[1])
CACHE = Path(tempfile.gettempdir()) / 'daevis-arch-icons'; CACHE.mkdir(exist_ok=True)
SI = 'https://cdn.jsdelivr.net/npm/simple-icons@13.21.0/icons/{}.svg'
LU = 'https://cdn.jsdelivr.net/npm/lucide-static@0.460.0/icons/{}.svg'
DEV = 'https://cdn.jsdelivr.net/gh/devicons/devicon@v2.16.0/icons/{}.svg'
PNG = {'nous': 'https://github.com/NousResearch.png?size=128', 'openai': 'https://github.com/openai.png?size=128',
       'baemin': 'https://www.baemin.com/favicon.ico'}
BRAND = {'discord': '5865F2', 'python': '3776AB', 'docker': '2496ED', 'android': '3DDC84', 'n8n': 'EA4B71',
         'kakaotalk': '3C1E1E', 'naver': '03C75A', 'sqlite': '003B57', 'langchain': '1C3C3C',
         'duckduckgo': 'DE5833', 'kakao': '3C1E1E', 'ubuntu': 'E95420'}


def fetch(url, name):
    f = CACHE / name
    if not f.exists():
        req = urllib.request.Request(url, headers={'User-Agent': 'arch-diagram'})
        f.write_bytes(urllib.request.urlopen(req, timeout=20).read())
    return f.read_bytes()


def si(name, x, y, s):
    d = re.search(r' d="([^"]+)"', fetch(SI.format(name), f'si-{name}.svg').decode()).group(1)
    return f'<svg x="{x}" y="{y}" width="{s}" height="{s}" viewBox="0 0 24 24"><path fill="#{BRAND[name]}" d="{d}"/></svg>'


def lu(name, x, y, s, color='#374151'):
    body = fetch(LU.format(name), f'lu-{name}.svg').decode()
    inner = re.search(r'<svg[^>]*>(.*)</svg>', body, re.S).group(1)
    return (f'<svg x="{x}" y="{y}" width="{s}" height="{s}" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{inner}</svg>')


def png(name, x, y, s, round_=True):
    raw = fetch(PNG[name], f'png-{name}.bin')
    im = Image.open(io.BytesIO(raw)).convert('RGBA'); im.thumbnail((128, 128))
    buf = io.BytesIO(); im.save(buf, 'PNG')
    b64 = base64.b64encode(buf.getvalue()).decode()
    clip = f' clip-path="url(#r{name})"' if round_ else ''
    defs = f'<clipPath id="r{name}"><rect x="{x}" y="{y}" width="{s}" height="{s}" rx="{s*0.22:.1f}"/></clipPath>' if round_ else ''
    return f'<defs>{defs}</defs><image x="{x}" y="{y}" width="{s}" height="{s}" href="data:image/png;base64,{b64}"{clip}/>'


def dev(path, x, y, w, h):
    body = fetch(DEV.format(path), 'dev-' + path.replace('/', '-') + '.svg')
    b64 = base64.b64encode(body).decode()
    return f'<image x="{x}" y="{y}" width="{w}" height="{h}" href="data:image/svg+xml;base64,{b64}"/>'


def icon(kind, name, x, y, s):
    return {'si': si, 'lu': lu, 'png': png}[kind](name, x, y, s)


from html import escape


def build(icon, dev):
    return Diagram(icon, dev).render()


class Diagram:
    width, height = 2240, 1660
    ink, muted = '#182b42', '#5c6f83'
    blue, teal, orange, gray = '#245daa', '#087f82', '#a45b20', '#738399'
    # Reader-facing labels explain responsibility, not implementation identifiers.
    node_labels = {
        'owner': ('사용자', '글·음성으로 요청'),
        'phone': ('휴대폰 앱', '요청 전송 · 답변 확인'),
        'discord': ('Discord', '메시지·음성 요청 · 결제 승인'),
        'desktop': ('PC 앱', '글·음성 요청과 답변 표시'),
        'stt': ('음성 인식', '말소리를 텍스트로 변환'),
        'tts': ('음성 출력', '답변을 음성으로 읽기'),
        'windows': ('PC 제어 프로그램', '서버의 작업을 PC에서 실행'),
        'power': ('서버 전원 관리', '에이전트 서버 시작·종료'),
        'relay': ('기기 요청 중계', '앱 요청을 처리 서버로 전달'),
        'relay-api': ('요청·결과 접수', '기기와 서버의 요청·결과 교환'),
        'queue': ('작업 대기열·기기 등록', '대기 작업 · 인증 · 실행 상태'),
        'blobs': ('파일 전달용 저장소', '스크린샷·첨부파일 임시 보관'),
        'gateway': ('Discord 메시지 처리', '소유자 확인 · 메시지 수신·응답'),
        'router': ('요청 분류', '바로 처리할지 AI를 쓸지 결정'),
        'hermes': ('요청별 실행 관리', '요청마다 분리된 처리 환경 제공'),
        'mcp': ('도구 호출·권한 확인', 'AI가 허용된 도구만 실행'),
        'voice-agent': ('기기 요청 수신', '중계 서버의 요청을 가져와 처리'),
        'recipes': ('정해진 순서로 작업 실행', '여러 도구를 정해진 순서로 호출'),
        'prepared': ('실행 환경 사전 준비', '프로그램 로딩을 미리 완료'),
        'approval': ('결제 승인 요청', '사용자 답변을 확인한 뒤 진행'),
        'simple': ('단순 질문 바로 응답', '시간·날짜는 AI 호출 없이 처리'),
        'kakao-direct': ('카톡 전송 직접 처리', '수신자와 전송 결과 확인'),
        'guard': ('AI 작업 실행', '판단 · 도구 사용 · 답변 생성'),
        'outbox': ('결과·승인 정보 교환', '첨부파일·승인 상태 전달'),
        'scheduler': ('예약 실행', '시간이 된 알림과 작업 실행'),
        'health': ('상태 점검', '오류와 서비스 상태 확인'),
        'idle': ('유휴 시 서버 종료', '작업이 없으면 서버 자동 종료'),
        'node-api': ('기기 작업 전달', '등록 기기에 작업 전달'),
        'memory-db': ('기억 저장소', '사용자 정보·메모'),
        'conversation-db': ('대화 기록 저장소', '주제별 대화와 이어 묻기'),
        'schedule-file': ('예약 저장소', '예약 시간·반복 조건'),
        'device-db': ('기기 등록 저장소', '등록 정보·인증·작업 상태'),
        'llm': ('AI 모델 서비스', 'ChatGPT · Claude'),
    }
    tool_labels = {
        'memory': ('기억 저장·검색', '사용자 정보와 메모 보관', 'SQLite'),
        'schedule': ('예약 관리', '알림 등록·반복·취소', '예약 실행기가 시간에 맞춰 처리'),
        'web': ('웹 검색·페이지 읽기', '검색 결과와 본문 수집', '인터넷 정보 조회'),
        'map': ('장소 검색·길찾기', '위치·경로·이동 시간 조회', '네이버 지도 · 카카오 로컬'),
        'mail': ('메일 조회·발송', '받은 메일 확인 · 메일 보내기', '네이버 메일'),
        'browser': ('브라우저 자동화', '웹페이지 조작·로그인 세션 사용', 'Playwright · Chromium'),
        'android': ('안드로이드 앱 조작', '앱 화면 읽기 · 누르기 · 입력', 'ReDroid · Android'),
        'kakao': ('카카오톡 전송', '대상 확인 · 메시지 보내기', '전송 결과 확인 · 중복 방지'),
        'n8n': ('자동화 실행', '등록된 작업 흐름 실행', 'n8n · Docker'),
        'devices': ('등록 기기 제어', '전화 · 알람 · 전원 요청', '등록 상태와 작업 결과 관리'),
        'pc': ('Windows PC 제어', '화면 읽기 · 클릭·입력 · 파일 검색', '등록된 사용자 PC에서 실행'),
        'sandbox': ('코드 실행', '요청별 독립 환경에서 실행', '결과 파일 반환 · Docker'),
    }
    external_labels = {
        'search': ('웹 검색 서비스', 'DuckDuckGo'),
        'maps': ('지도·장소 서비스', '네이버 지도 · 카카오 로컬'),
        'mail-service': ('네이버 메일', '받은 메일 · 보낸 메일'),
        'web-apps': ('웹 서비스', '로그인한 웹페이지'),
        'app-services': ('앱 서비스', '카카오톡 · 배달 앱'),
    }
    edge_labels = {
        'HTTPS / JSON': '요청·답변', 'Gateway / REST': '메시지·응답',
        'dispatch': '요청 배분', 'registry calls': '대기 작업 확인',
        'SQLite': '작업 저장·조회', 'file I/O': '파일 저장·읽기',
        'EC2 Start / Stop': '서버 켜기·끄기', 'HTTPS /agent/poll': '기기 요청 전달',
        'engine.run()': '실행 요청', 'MCP / stdio': '도구 호출',
        'HTTPS': 'AI 판단 요청·응답', 'route / recipe': '정해진 작업',
        'date / time': '시간·날짜', 'quoted message': '카톡 전송',
        'direct Python call': 'AI 없이 도구 실행', 'preload': '미리 준비',
        'JSON / stdin': '요청 전달', 'payment': '결제 확인',
        'approval file IPC': '승인 요청·답변', 'SQLite I/O': '기억 저장·검색',
        'mail': '메일 조회·발송', 'app HTTPS': '앱 통신', 'UI': '조작',
        'HTTPS /request': 'PC 작업 요청', 'audio / UI': '입력',
        'STT text': '인식한 말', '/pc/poll · /ack': '작업·실행 결과',
    }

    def __init__(self, icon, dev):
        self.icon, self.dev = icon, dev
        self.layers = {key: [] for key in ('base', 'edges', 'nodes', 'labels')}

    def text(self, x, y, label, size=14, color=None, weight=400, anchor='middle', layer='labels'):
        self.layers[layer].append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color or self.ink}" font-weight="{weight}" text-anchor="{anchor}">{escape(label)}</text>')

    def box(self, x, y, w, h, label, fill='#f5f8fc', dashed=False):
        dash = 'stroke-dasharray="9 6"' if dashed else ''
        self.layers['base'].append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{fill}" stroke="#bdcad9" stroke-width="1.4" {dash}/>')
        if label:
            self.text(x + 20, y + 28, label, 18, weight=650, anchor='start')

    def node(self, key, cx, cy, title, source, kind='lu', logo='box', width=224):
        title, source = self.node_labels[key]
        self.layers['nodes'].append(f'<g data-node="{key}" data-x="{cx-width/2}" data-y="{cy-36}" data-w="{width}" data-h="126">')
        self.layers['nodes'].append(f'<rect data-shape="true" x="{cx-28}" y="{cy-28}" width="56" height="56" rx="10" fill="white" stroke="#b9c9dc" stroke-width="1.3"/>')
        self.layers['nodes'].append(self.icon(kind, logo, cx - 18, cy - 18, 36))
        self.text(cx, cy + 48, title, 17, weight=650, layer='nodes')
        self.text(cx, cy + 70, source, 12, self.muted, layer='nodes')
        self.layers['nodes'].append('</g>')

    def tool(self, key, cy, title, api, source, kind='lu', logo='box'):
        title, api, source = self.tool_labels[key]
        self.layers['nodes'].append(f'<g data-node="{key}" data-x="1350" data-y="{cy-38}" data-w="472" data-h="91">')
        self.layers['nodes'].append(f'<rect data-shape="true" x="1360" y="{cy-20}" width="40" height="40" rx="8" fill="white" stroke="#b9c9dc"/>')
        self.layers['nodes'].append(self.icon(kind, logo, 1368, cy - 12, 24))
        self.text(1420, cy - 8, title, 17, weight=650, anchor='start', layer='nodes')
        self.text(1420, cy + 13, api, 13, self.blue, anchor='start', layer='nodes')
        self.text(1420, cy + 34, source, 12, self.muted, anchor='start', layer='nodes')
        self.layers['nodes'].append('</g>')

    def external(self, key, cy, title, protocol, kind='lu', logo='globe'):
        title, protocol = self.external_labels[key]
        self.layers['nodes'].append(f'<g data-node="{key}" data-x="1922" data-y="{cy-32}" data-w="286" data-h="80">')
        self.layers['nodes'].append(f'<rect data-shape="true" x="1932" y="{cy-20}" width="40" height="40" rx="8" fill="white" stroke="#c7d1dd"/>')
        self.layers['nodes'].append(self.icon(kind, logo, 1940, cy - 12, 24))
        self.text(1988, cy - 3, title, 17, weight=650, anchor='start', layer='nodes')
        self.text(1988, cy + 21, protocol, 12, self.muted, anchor='start', layer='nodes')
        self.layers['nodes'].append('</g>')

    def wire(self, points, label=None, at=None, color=None, dashed=False, both=False, arrow=True):
        color = color or self.blue
        marker = {self.blue: 'rpc', self.teal: 'control', self.orange: 'ipc', self.gray: 'data'}[color]
        path = 'M' + ' L'.join(f'{x},{y}' for x, y in points)
        attrs = (' stroke-dasharray="6 5"' if dashed else '') + (f' marker-end="url(#a-{marker})"' if arrow else '') + (f' marker-start="url(#b-{marker})"' if both else '')
        self.layers['edges'].append(f'<path data-edge="true" d="{path}" fill="none" stroke="{color}" stroke-width="1.7" stroke-linejoin="round"{attrs}/>')
        if label:
            self.text(*at, self.edge_labels.get(label, label), 12, color)

    def components(self):
        n, t = self.node, self.text
        self.layers['base'].append(f'<rect width="{self.width}" height="{self.height}" fill="white"/>')
        t(40, 46, '대비스 · 요청부터 실행과 응답까지', 28, weight=700, anchor='start')
        self.box(340, 90, 1540, 1380, '', '#fbfcfe', True)
        self.layers['labels'].append(self.dev('amazonwebservices/amazonwebservices-original-wordmark', 366, 74, 66, 40))
        self.box(380, 132, 1448, 228, '항상 켜져 있는 중계 서버', '#f3f7fc')
        self.box(380, 420, 1448, 1036, '필요할 때 켜지는 에이전트 서버')
        t(180, 112, '사용자와 요청 수단', 18, weight=650)
        t(1932, 144, '연결된 외부 서비스', 18, weight=650, anchor='start')
        n('owner', 180, 180, 'Owner', 'Discord / PC / Phone', logo='user')
        n('phone', 180, 340, 'Android App / Node', 'android/ · device-node.py', logo='smartphone', width=280)
        n('discord', 180, 540, 'Discord', 'DM · mentions · approvals', 'si', 'discord')
        self.box(40, 694, 280, 710, '사용자의 Windows PC', '#f9fbfd')
        n('desktop', 180, 768, 'Desktop App', 'app.py · core.py', logo='monitor')
        n('stt', 180, 932, 'Whisper STT', 'faster-whisper · GPU', logo='mic')
        n('tts', 180, 1096, 'TTS', 'Windows / Edge TTS', logo='volume-2')
        n('windows', 180, 1260, 'Windows Agent', 'agent.py · remote.py · pc_ops.py', logo='monitor', width=260)
        t(180, 1380, '화면 · 입력 · 파일 · 명령 실행', 13, self.muted)
        n('power', 500, 220, 'Power Bot', 'power_bot.py', 'si', 'python')
        n('relay', 750, 220, 'Voice Relay', 'voice_relay.py', logo='audio-lines')
        n('relay-api', 1000, 220, 'Relay API', 'pc_control.py', logo='server')
        n('queue', 1250, 220, 'Job Queue / Registry', 'pc-control.sqlite3', 'si', 'sqlite', width=260)
        n('blobs', 1600, 220, 'Attachment Store', 'pc-files/', logo='files')
        for args in [
            ('gateway',500,540,'Discord Gateway','hermes_gateway.py','si','python'),
            ('router',710,540,'Request Router','gateway.py','lu','split'),
            ('hermes',920,540,'HermesRunner','hermes_runner.py','lu','workflow'),
            ('mcp',1130,540,'MCP Server','mcp_server.py','lu','plug'),
            ('voice-agent',500,760,'Voice Agent','voice_agent.py','lu','audio-lines'),
            ('recipes',710,760,'RecipeExecutor','recipes.py','lu','workflow'),
            ('prepared',920,760,'PreparedExecutor','warm_worker.py','lu','layers'),
            ('approval',1130,760,'ChannelApproval','approvals.py','lu','key-round'),
            ('simple',500,960,'simple_reply()','simple_reply.py','lu','clock'),
            ('kakao-direct',710,960,'KakaoWatcher','kakao_watch.py','si','kakaotalk'),
            ('guard',920,960,'Hermes Worker','hermes-worker.py · hermes_policy.py','png','nous'),
            ('outbox',1130,960,'Request Outbox','outbox/<request_id>/','lu','inbox'),
            ('scheduler',500,1160,'Scheduler','gateway.schedule_loop','lu','calendar-clock'),
            ('health',710,1160,'Health Monitor','runtime_health.py','lu','activity'),
            ('idle',920,1160,'Idle Shutdown','power.py','lu','power'),
            ('node-api',1130,1160,'Device Node API',':8791 · device_nodes.py','lu','smartphone'),
            ('memory-db',500,1320,'Memory Store','agent.db','si','sqlite'),
            ('conversation-db',710,1320,'Conversation Store','conversations.sqlite3','si','sqlite'),
            ('schedule-file',920,1320,'Schedule Store','schedule.json','lu','file-json'),
            ('device-db',1130,1320,'Device Registry','device-nodes.sqlite3','si','sqlite'),
        ]:
            n(*args)
        t(1358,451,'실제로 작업을 수행하는 도구',18,weight=650,anchor='start')
        self.rows = [
            ('memory',490,'Memory · SQLite','memory_search · memory_save · memory_forget','memory.py','si','sqlite'),
            ('schedule',566,'Scheduler','schedule_add · schedule_list · schedule_remove','scheduler.py · schedule.json','lu','calendar-clock'),
            ('web',642,'Web Search / Fetch','web_search · web_fetch','tools.py','lu','globe'),
            ('map',718,'Maps / Transit','route_search · map_search','tools.py · transit.py','lu','map-pin'),
            ('mail',794,'Naver Mail','mail_list · mail_read · mail_send','naver_mail.py','si','naver'),
            ('browser',870,'Browser Worker / Chromium','auth_ensure · Playwright / CDP','authgate.py · browser_worker.py','lu','globe'),
            ('android',946,'Android · ReDroid','android_screen · android_click · android_input','android_control.py · ADB / uiautomator2','si','android'),
            ('kakao',1022,'KakaoTalk','kakao_send · kakao_self_send · kakao_find_recipient','kakao_watch.py · kakao_self.py','si','kakaotalk'),
            ('n8n',1098,'n8n · Docker','n8n_list · n8n_run','n8n_tools.py · HTTP','si','n8n'),
            ('devices',1174,'Device Nodes','device_nodes · device_jobs · device_enqueue','device_nodes.py · device-nodes.sqlite3','lu','smartphone'),
            ('pc',1250,'Windows PC Control','pc_find_files · pc_act · pc_shell · pc_send_file','pc_tools.py · Relay API','lu','monitor'),
            ('sandbox',1326,'Code Execution · Docker','run_code · non-root · 512MB · 300s','sandbox.py','si','docker'),
        ]
        for args in self.rows:
            self.tool(*args)
        n('llm',2080,220,'LLM Providers','Codex OAuth · Anthropic','png','openai',width=256)
        for args in [
            ('search',642,'DuckDuckGo','HTTPS','si','duckduckgo'),
            ('maps',718,'Map Services','Naver Maps · Kakao Local','si','kakao'),
            ('mail-service',794,'Naver Mail','IMAP / SMTP / HTTPS','si','naver'),
            ('web-apps',870,'Web Applications','HTTPS · session cookies','lu','globe'),
            ('app-services',946,'App Services','KakaoTalk · Baemin','si','kakaotalk'),
        ]:
            self.external(*args)

    def connections(self):
        w, b, t, g, o = self.wire, self.blue, self.teal, self.gray, self.orange
        w([(208,768),(316,768),(316,112),(1000,112),(1000,192)],'HTTPS / JSON',(680,102),both=True)
        w([(208,340),(316,340),(316,112)],both=True)
        w([(208,540),(472,540)],'Gateway / REST',(252,500),both=True)
        w([(528,220),(722,220)],'dispatch',(625,207))
        w([(778,220),(972,220)],'registry calls',(875,207))
        w([(1028,220),(1222,220)],'SQLite',(1125,207),color=g,both=True)
        w([(1000,192),(1000,176),(1600,176),(1600,192)],'file I/O',(1360,165),color=g,both=True)
        w([(472,220),(360,220),(360,398),(380,398),(380,430)],'EC2 Start / Stop',(455,412),color=t,dashed=True)
        w([(972,220),(880,220),(880,376),(398,376),(398,760),(472,760)],'HTTPS /agent/poll',(620,366),both=True)
        w([(472,760),(400,760),(400,540),(472,540)])
        w([(528,540),(682,540)])
        w([(738,540),(892,540)],'engine.run()',(775,516))
        w([(948,960),(1020,960),(1020,540),(1102,540)],'MCP / stdio',(1060,520))
        w([(892,960),(820,960),(820,392),(1870,392),(1870,220),(2052,220)],'HTTPS',(1590,382),both=True)
        w([(1158,540),(1310,540)])
        w([(1310,490),(1310,1326)],arrow=False)
        for _,cy,*_ in self.rows:
            w([(1310,cy),(1360,cy)])
        w([(682,540),(610,540),(610,760),(682,760)],'route / recipe',(670,691),color=t)
        w([(610,760),(610,960),(528,960)],'date / time',(540,887),color=t)
        w([(610,960),(682,960)],'quoted message',(748,895),color=t)
        w([(738,760),(815,760),(815,1408),(1310,1408),(1310,1326)],'direct Python call',(940,1428),color=t)
        w([(948,540),(1035,540),(1035,720),(948,720),(948,760)],'preload',(1100,689),color=g,dashed=True)
        w([(948,760),(1010,760),(1010,960),(948,960)],'JSON / stdin',(1110,887),color=g,dashed=True)
        w([(1158,540),(1210,540),(1210,760),(1158,760)],'payment',(1240,689),color=o,dashed=True)
        w([(1158,760),(1210,760),(1210,960),(1158,960)],color=o,dashed=True)
        w([(1102,960),(1030,960),(1030,1376),(392,1376),(392,540),(472,540)],'approval file IPC',(740,1445),color=o,dashed=True,both=True)
        w([(472,540),(410,540),(410,1056),(920,1056)],color=g,dashed=True,arrow=False)
        for cx in (500,710,920):
            w([(cx,1056),(cx,1132)],color=g,dashed=True)
        w([(528,1160),(600,1160),(600,1260),(840,1260),(840,1320),(892,1320)],color=g,dashed=True)
        w([(472,1320),(404,1320),(404,490),(1360,490)],'SQLite I/O',(600,478),color=g,dashed=True,both=True)
        w([(410,1056),(370,1056),(370,1418),(630,1418),(630,1320),(682,1320)],color=g,dashed=True)
        w([(1158,1160),(1250,1160),(1250,1320),(1158,1320)],color=g,dashed=True)
        w([(1360,1174),(1290,1174),(1290,1320),(1158,1320)],'기기 상태 저장',(1250,1148),color=g)
        for cy in (642,718,794,870,946):
            label = {642:'검색·본문',718:'경로·장소',794:'메일 조회·발송',870:'웹페이지 조작',946:'앱 통신'}[cy]
            w([(1822,cy),(1932,cy)],label,(1998,cy-30))
        w([(1380,1002),(1380,966)],'UI',(1403,989),color=t)
        w([(1822,1250),(1850,1250),(1850,112),(1000,112),(1000,192)],'HTTPS /request',(1910,1237))
        w([(152,180),(26,180),(26,932),(152,932)],'audio / UI',(90,684),color=g,dashed=True)
        for cy in (340,540,768):
            w([(26,cy),(152,cy)],color=g,dashed=True)
        w([(208,932),(300,932),(300,768),(208,768)],'STT text',(268,858),color=g,dashed=True)
        w([(152,768),(68,768),(68,1096),(152,1096)],color=g,dashed=True)
        w([(208,1260),(316,1260),(316,768)],'/pc/poll · /ack',(264,1245),both=True)

    def sequence(self):
        self.text(40,1514,'PC 원격 제어와 파일 전송',20,weight=650,anchor='start')
        steps=[('제어 작업 요청','실행할 작업을 중계 서버로 전달'),('중계 서버','등록된 PC의 작업 대기열에 저장'),('사용자 PC','화면 조작·파일 검색·명령 실행'),('파일 임시 보관','PC가 스크린샷·파일을 업로드'),('결과 수신','작업 결과·첨부파일을 가져오기'),('사용자에게 전달','Discord·PC 앱에서 답변·파일 확인')]
        for i,(title,detail) in enumerate(steps):
            x=40+i*364
            self.layers['base'].append(f'<rect x="{x}" y="1534" width="330" height="74" rx="8" fill="#f5f8fc" stroke="#d1dce8"/>')
            self.text(x+14,1561,title,17,weight=650,anchor='start')
            self.text(x+14,1589,detail,12,self.muted,anchor='start')
            if i<5:
                self.wire([(x+330,1570),(x+364,1570)])
        for x,c,d,label in [(40,self.blue,False,'요청·결과 전달'),(280,self.teal,False,'AI 없이 처리·전원 제어'),(610,self.gray,True,'저장·실행 환경 관리'),(960,self.orange,True,'결제 승인')]:
            self.wire([(x,1633),(x+46,1633)],color=c,dashed=d)
            self.text(x+60,1638,label,13,self.muted,anchor='start')

    def render(self):
        self.components()
        self.connections()
        self.sequence()
        style='<style>text{font-family:"Malgun Gothic","Noto Sans KR",Arial,sans-serif}path{stroke-linecap:round}</style>'
        defs='<defs>'
        for key,color in [('rpc',self.blue),('control',self.teal),('ipc',self.orange),('data',self.gray)]:
            defs+=(f'<marker id="a-{key}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M1 1 L9 5 L1 9" fill="none" stroke="{color}" stroke-width="1.7"/></marker>'
                   f'<marker id="b-{key}" viewBox="0 0 10 10" refX="1" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M9 1 L1 5 L9 9" fill="none" stroke="{color}" stroke-width="1.7"/></marker>')
        defs+='</defs>'
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width}" height="{self.height}" viewBox="0 0 {self.width} {self.height}" role="img" aria-labelledby="arch-title arch-desc">'
                '<title id="arch-title">대비스의 요청 처리·도구 실행·결과 전달 구조</title>'
                '<desc id="arch-desc">사용자의 글과 음성 요청을 받아 중계하고, AI 또는 정해진 처리로 도구를 실행한 뒤 답변과 파일을 전달한다. 각 노드는 역할과 처리 내용을 표시한다.</desc>'
                +style+defs+''.join(''.join(self.layers[k]) for k in self.layers)+'</svg>')


svg = build(icon, dev)
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(svg, encoding='utf-8')
print(OUT, len(svg))
