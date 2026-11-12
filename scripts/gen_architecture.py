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


# Overview diagram: component boundaries first; tool names live in README's MCP map.
from html import escape

W, H = 1600, 1300
LAYERS = {name: [] for name in ('base', 'edges', 'nodes', 'labels')}
INK, MUTED, BLUE, TEAL = '#14263d', '#62748a', '#3068c8', '#087f8c'


def put(layer, markup):
    LAYERS[layer].append(markup)


def text(x, y, value, size=15, fill=INK, weight=400, layer='labels', **attrs):
    extra = ' '.join(f'{k.replace("_", "-")}="{escape(str(v), quote=True)}"' for k, v in attrs.items())
    put(layer, f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{fill}" {extra}>{escape(value)}</text>')


def rect(layer, x, y, w, h, fill='white', stroke='#dce4ee', radius=16, **attrs):
    extra = ' '.join(f'{k.replace("_", "-")}="{escape(str(v), quote=True)}"' for k, v in attrs.items())
    put(layer, f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{stroke}" {extra}/>')


def badge(x, y, label, width, fill='#eaf1ff', color=BLUE):
    rect('nodes', x, y, width, 26, fill, 'none', 13)
    text(x + width / 2, y + 18, label, 12, color, 600, text_anchor='middle')


def card(key, x, y, w, h, title, lines, kind='lu', logo='box', color=BLUE):
    # data-* geometry is used by the visual layout checker.
    put('nodes', f'<g data-card="{key}" data-x="{x}" data-y="{y}" data-w="{w}" data-h="{h}">')
    rect('nodes', x, y, w, h, 'white', '#dce4ee', 14)
    compact = h <= 88
    icon_y, title_y, body_y = (12, 31, 58) if compact else (16, 38, 69)
    rect('nodes', x + 16, y + icon_y, 30, 30, '#f0f4fa', 'none', 9)
    put('nodes', icon(kind, logo, x + 21, y + icon_y + 5, 20))
    text(x + 56, y + title_y, title, 18, INK, 650, layer='nodes')
    for i, label in enumerate(lines):
        text(x + 18, y + body_y + 21 * i, label, 14, MUTED, layer='nodes')
    put('nodes', '</g>')


def arrow(points, color=BLUE, both=False, dashed=False):
    path = 'M' + ' L'.join(f'{x},{y}' for x, y in points)
    dash = 'stroke-dasharray="5 5"' if dashed else ''
    marker = 'direct' if color == TEAL else 'request'
    start = f'marker-start="url(#back-{marker})"' if both else ''
    put('edges', f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round" {dash} {start} marker-end="url(#arrow-{marker})"/>')


# Heading and execution boundary.
rect('base', 0, 0, W, H, '#f8fafc', 'none', 0)
text(48, 38, 'DAEVIS  /  SYSTEM ARCHITECTURE', 12, MUTED, 700, letter_spacing='2')
text(48, 83, '대비스 시스템 아키텍처', 32, INK, 750)
text(48, 116, '요청이 들어오는 곳, 판단하는 곳, 실제로 실행하는 곳을 구분합니다.', 17, MUTED)
rect('base', 326, 148, 918, 934, '#f0f4f9', '#c1cfdf', 24, stroke_dasharray='8 6')
rect('base', 1092, 132, 128, 32, '#f8fafc', 'none', 0)
put('labels', dev('amazonwebservices/amazonwebservices-original-wordmark', 1100, 130, 48, 29))
text(1156, 152, '실행 영역', 13, MUTED, 600)
for x, label in ((48, 'DEVICE  /  내 기기'), (348, 'RELAY  /  항상 켜짐'),
                 (692, 'AGENT  /  필요할 때 실행'), (1276, 'SERVICES  /  외부 연결')):
    text(x, 187, label, 12, MUTED, 700, letter_spacing='.5')

# Devices: the desktop app is also the remote-control executor.
card('pc', 48, 220, 244, 144, 'Windows PC', ['Daevis 앱 · 호출어 인식', '화면·파일·입력 실행', '음성 응답 · 원격 제어 스위치'], logo='monitor')
card('phone', 48, 404, 244, 130, '휴대폰', ['앱·음성으로 요청', '알람·전화 실행 노드'], logo='smartphone')
card('discord', 48, 574, 244, 130, 'Discord', ['소유자 DM · 서버 멘션', '대화·알림·결제 승인'], kind='si', logo='discord')

# Always-on relay.
rect('base', 348, 220, 268, 484, 'white', '#cbd9e8', 18)
put('nodes', si('python', 368, 241, 28))
text(408, 264, '상시 중계 서버', 20, INK, 700)
text(368, 291, '전원 봇 + 작업 큐', 14, MUTED)
for y in (312, 414, 496, 608):
    put('nodes', f'<path d="M368,{y} H596" stroke="#e6ecf3"/>')
text(368, 345, '요청과 결과 중계', 17, INK, 650)
text(368, 372, '작업 큐 · 진행 · 대화 기록', 14, MUTED)
text(368, 394, '결제 답변 · 파일 첨부', 14, MUTED)
text(368, 448, '필요할 때 본체 켜기', 17, INK, 650)
text(368, 476, 'PC·폰 요청을 본체로 전달', 14, MUTED)
text(368, 530, '정해진 명령 바로 처리', 17, INK, 650)
text(368, 558, '시간·날짜 · 기기 전원', 14, MUTED)
text(368, 580, '모델 호출 없이 응답', 14, MUTED)
badge(368, 631, '상시 실행', 84, '#e4f3ef', TEAL)
text(368, 682, '본체가 꺼져 있어도 요청 수신', 13, MUTED)

# Main agent. A standby holds imports, never an active request lease.
rect('base', 668, 220, 560, 840, '#f9fbfe', '#cbd9e8', 18)
text(692, 260, '에이전트 서버', 22, INK, 700)
badge(1094, 240, 'ARM · 4GB', 110, '#e4f3ef', TEAL)
card('gateway', 692, 292, 224, 112, '소유자 관문', ['Discord · 요청별 대화', '권한과 대화 범위 확인'], logo='shield-check')
card('hermes', 956, 292, 248, 112, 'Hermes 실행부', ['격리 worker · 1개 사전 준비', '모델과 도구를 번갈아 호출'], kind='png', logo='nous')
card('direct', 692, 448, 224, 104, '정형 요청 처리', ['카톡 · 길찾기 · 모델 우회'], logo='route')
card('mcp', 956, 448, 248, 104, 'daevis MCP', ['도구 허용 목록 · 요청 권한'], logo='plug')

# Capabilities are grouped, rather than repeating every tool function.
rect('base', 692, 600, 512, 338, '#edf3fa', 'none', 16)
text(712, 632, '실행 도구', 18, INK, 700)
text(712, 653, '도구 연결은 요청마다 새로 생성', 13, MUTED)
card('web', 712, 672, 224, 74, '웹·메일·지도', ['검색 · 로그인 세션'], logo='globe')
card('pc-tools', 956, 672, 224, 74, 'PC 원격 제어', ['중계 → 내 PC에서 실행'], logo='monitor')
card('android', 712, 758, 224, 74, 'Android·카톡', ['ReDroid · 화면 기반 조작'], kind='si', logo='android')
card('code', 956, 758, 224, 74, '코드 실행', ['일회용 Docker · 결과 파일'], kind='si', logo='docker')
card('memory', 712, 844, 224, 74, '기억·대화', ['SQLite · 요청별 기록'], kind='si', logo='sqlite')
card('workflow', 956, 844, 224, 74, '예약·자동화', ['예약 루프 · n8n'], kind='si', logo='n8n')
card('operations', 692, 968, 512, 72, '운영과 실행 제어', ['상태 점검 · 유휴 종료 · 요청별 권한 · 반복 제한'], logo='activity')

# External providers are outside the AWS execution boundary.
card('models', 1276, 292, 276, 112, '추론 모델', ['ChatGPT 구독 · Claude 대체', '코드 관문을 통과한 요청 처리'], kind='png', logo='openai')
card('services', 1276, 600, 276, 224, '연결된 서비스', ['웹 검색 · 서비스 페이지', '네이버 메일 · 로그인', '네이버 지도 · 카카오 로컬'], logo='globe')
card('locations', 1276, 864, 276, 176, '실행 위치 구분', ['PC 조작 → 내 Windows PC', '코드 실행 → 본체의 Docker', '앱 조작 → 서버의 ReDroid'], logo='layers')

# Main requests. All paths stay in the gutters, never across component text.
arrow([(292, 292), (348, 292)], both=True)
arrow([(292, 462), (348, 462)], both=True)
arrow([(616, 348), (692, 348)])
text(653, 335, 'long-poll', 11, BLUE, text_anchor='middle')
arrow([(292, 638), (316, 638), (316, 738), (644, 738), (644, 348)])
put('edges', '<circle cx="644" cy="348" r="4" fill="#3068c8"/>')
text(465, 729, 'Discord는 본체가 직접 수신', 13, BLUE, text_anchor='middle')
arrow([(916, 348), (956, 348)])
arrow([(1204, 348), (1276, 348)], both=True)
text(1240, 334, '추론', 12, BLUE, text_anchor='middle')
arrow([(804, 404), (804, 448)], color=TEAL)
arrow([(1080, 404), (1080, 448)])
text(1096, 431, 'stdio', 12, BLUE)
arrow([(804, 552), (804, 600)], color=TEAL)
arrow([(1080, 552), (1080, 600)])
arrow([(1204, 626), (1276, 626)])

# Quiet legend fills no runtime boundary and makes the arrows self-contained.
text(48, 837, '흐름 읽기', 16, INK, 650)
arrow([(48, 866), (84, 866)])
text(96, 871, '요청 · 결과 전달', 14, MUTED)
arrow([(48, 902), (84, 902)], color=TEAL)
text(96, 907, '모델을 거치지 않는 경로', 14, MUTED)
text(48, 950, '점선 테두리 = AWS 실행 영역', 13, MUTED)
text(48, 978, '소유자 요청으로 시작하고,', 14, MUTED)
text(48, 1000, '결제 단계에서 승인을 받습니다.', 14, MUTED)

# A separate sequence keeps the long PC return path out of the overview.
text(48, 1122, 'PC 제어 · 파일 전달의 왕복 경로', 20, INK, 700)
steps = [
    ('request', '본체 도구', ['pc_* 요청'], 'plug'),
    ('queue', '상시 중계', ['작업 큐 · long-poll'], 'server'),
    ('execute', '내 PC 에이전트', ['화면·파일·입력 실행'], 'monitor'),
    ('attachment', '중계 첨부 저장', ['파일 ID · 다운로드 링크'], 'file-up'),
    ('result', '본체 결과 처리', ['첨부 준비 · 답변 생성'], 'inbox'),
    ('delivery', 'Discord · 앱', ['답변 · 첨부 · 파일 받기'], 'send'),
]
for i, (key, title, lines, logo) in enumerate(steps):
    x = 48 + i * 256
    card('flow-' + key, x, 1140, 222, 88, title, lines, logo=logo)
    if i < len(steps) - 1:
        arrow([(x + 222, 1184), (x + 256, 1184)])
text(48, 1271, '도구별 호출 이름과 연결 코드는 README의 MCP 도구 지도에서 확인할 수 있습니다.', 13, MUTED)

STYLE = '<style>text{font-family:"Malgun Gothic","Noto Sans KR",Arial,sans-serif}path{stroke-linecap:round}</style>'
DEFS = '<defs>'
for marker, color in (('request', BLUE), ('direct', TEAL)):
    DEFS += (f'<marker id="arrow-{marker}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
             f'<path d="M1 1 L9 5 L1 9" fill="none" stroke="{color}" stroke-width="1.7"/></marker>'
             f'<marker id="back-{marker}" viewBox="0 0 10 10" refX="1" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
             f'<path d="M9 1 L1 5 L9 9" fill="none" stroke="{color}" stroke-width="1.7"/></marker>')
DEFS += '</defs>'
svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-labelledby="diagram-title diagram-desc">'
       '<title id="diagram-title">대비스 시스템 아키텍처</title>'
       '<desc id="diagram-desc">PC·휴대폰·Discord 요청이 상시 중계 또는 소유자 관문으로 들어가고, Hermes와 MCP 도구를 통해 실행된다. PC 제어와 파일 전달은 상시 중계를 왕복한다.</desc>'
       + STYLE + DEFS + ''.join(''.join(LAYERS[layer]) for layer in LAYERS) + '</svg>')
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(svg, encoding='utf-8')
print(OUT, len(svg))
