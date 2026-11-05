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


E = []  # elements
def esc(t): return t.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def node(cx, cy, kind, name, label, sub='', dashed=False, tile=58, isz=36):
    t = tile
    dash = ' stroke-dasharray="5 4"' if dashed else ''
    E.append(f'<rect x="{cx-t/2}" y="{cy-t/2}" width="{t}" height="{t}" rx="12" fill="#fff" stroke="#cbd5e1" stroke-width="1.4"{dash}/>')
    E.append(icon(kind, name, cx - isz/2, cy - isz/2, isz))
    E.append(f'<text x="{cx}" y="{cy+t/2+18}" class="lb" text-anchor="middle">{esc(label)}</text>')
    for i, line in enumerate(sub.split('\n') if sub else []):
        E.append(f'<text x="{cx}" y="{cy+t/2+34+i*14}" class="sb" text-anchor="middle">{esc(line)}</text>')


def row(cx, cy, kind, name, label, sub):
    E.append(f'<rect x="{cx-20}" y="{cy-20}" width="40" height="40" rx="9" fill="#fff" stroke="#cbd5e1" stroke-width="1.3"/>')
    E.append(icon(kind, name, cx-13, cy-13, 26))
    E.append(f'<text x="{cx+30}" y="{cy-3}" class="lb">{esc(label)}</text>')
    E.append(f'<text x="{cx+30}" y="{cy+13}" class="mono">{esc(sub)}</text>')


def line(pts, label=None, lx=None, ly=None, dashed=False, both=False, color='#111827', anchor='middle'):
    d = 'M' + ' L'.join(f'{x},{y}' for x, y in pts)
    dash = ' stroke-dasharray="6 5"' if dashed else ''
    start = ' marker-start="url(#arrS)"' if both else ''
    E.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.6"{dash}{start} marker-end="url(#arr)"/>')
    if label:
        w = len(label) * 11 + 10
        x0 = lx - w/2 if anchor == 'middle' else lx - 4
        E.append(f'<rect x="{x0}" y="{ly-12}" width="{w}" height="17" rx="3" fill="#fff"/>')
        E.append(f'<text x="{lx}" y="{ly+1}" class="el" text-anchor="{anchor}">{esc(label)}</text>')


def box(x, y, w, h, title, kind=None, name=None, style='4 4', stroke='#64748b'):
    E.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="#f8fafc" stroke="{stroke}" stroke-width="1.5" stroke-dasharray="{style}"/>')
    tx = x + 16
    if kind:
        E.append(icon(kind, name, x+14, y+10, 20)); tx = x + 42
    E.append(f'<text x="{tx}" y="{y+26}" class="bt">{esc(title)}</text>')


W, H = 1680, 1080
# AWS boundary
E.append('<rect x="290" y="56" width="1080" height="930" rx="30" fill="none" stroke="#111827" stroke-width="2.4" stroke-dasharray="18 6 3 6"/>')
E.append('<rect x="306" y="34" width="104" height="44" rx="8" fill="#fff"/>')
E.append(dev('amazonwebservices/amazonwebservices-original-wordmark', 312, 30, 92, 52))

box(320, 110, 330, 170, '상시 서버 · EC2 t3.micro')
box(320, 320, 1020, 640, 'Agent 서버 · EC2 t4g.medium · 쓸 때만 켬', 'si', 'ubuntu')

# left: me + Discord
node(90, 470, 'lu', 'user', '나', '휴대폰 · PC')
node(210, 470, 'si', 'discord', 'Discord', 'DM · 서버 멘션')

# always-on server
node(485, 192, 'si', 'python', '전원 봇', 'power_bot.py')

# main pipeline
node(410, 440, 'si', 'python', 'Discord 게이트웨이', 'hermes_gateway.py')
node(575, 440, 'lu', 'split', '코드 관문', '소유자 · 전원 · 고정 문장')
node(745, 440, 'png', 'nous', 'Hermes Agent', '요청마다 격리 worker')
node(915, 440, 'lu', 'plug', 'daevis MCP 서버', 'stdio · 허용 목록')

# under gateway
node(410, 600, 'lu', 'clock', '예약 루프', 'schedule.json')
node(410, 730, 'lu', 'activity', '상태 점검', '60초 · n8n · Android · 메모리')
node(410, 860, 'lu', 'power', '유휴 자동 종료', '요청 없으면 서버 끔')
# under gate / hermes / mcp
node(575, 650, 'lu', 'route', '고정 문장 직접 실행', '카톡 · 경로 · 모델 0회')
node(745, 600, 'lu', 'shield', '반복 제한', '12회 · 120초 · 토큰 6만\n같은 오류 3회면 중단')
node(745, 780, 'si', 'langchain', 'LangGraph', '도입 예정 · 코드 없음', dashed=True)
node(915, 650, 'lu', 'key', '쓰기 승인', '소유자 DM 승인 · 거절')

# tool column
ROWS = [
    ('si', 'sqlite', '기억 · SQLite', 'memory_search · save · forget'),
    ('lu', 'clock', '예약', 'schedule_add · list · repeat · remove'),
    ('lu', 'globe', '웹 검색 · 읽기', 'web_search · web_fetch'),
    ('lu', 'map-pin', '길찾기 · 장소', 'route_search · map_search'),
    ('si', 'naver', '메일 · 로그인 세션 · Playwright', 'mail_list · mail_read · auth_ensure'),
    ('si', 'android', '앱 조작 · ReDroid (Docker)', 'android_open · screen · click · input'),
    ('si', 'kakaotalk', '카카오톡', 'kakao_send · self_send · find_recipient'),
    ('si', 'n8n', 'n8n 워크플로 (Docker)', 'n8n_list · n8n_run'),
    ('lu', 'smartphone', '기기 노드 중계 :8791', 'device_nodes · enqueue · jobs'),
]
TX, Y0, STEP = 1070, 372, 66
ys = [Y0 + i*STEP for i in range(len(ROWS))]
for (k, n, l, s), y in zip(ROWS, ys):
    row(TX, y, k, n, l, s)
BUS = 1010
E.append(f'<text x="{TX-20}" y="{Y0-36}" class="sb">recipe_run · plan_execute: 허용된 도구만 묶어 실행</text>')
E.append(f'<path d="M{BUS},{ys[0]} L{BUS},{ys[-1]+40}" stroke="#111827" stroke-width="1.6" fill="none"/>')
for y in ys:
    line([(BUS, y), (TX-22, y)])

# externals
EXT = {2: ('si', 'duckduckgo', 'DuckDuckGo'), 3: ('si', 'kakao', '카카오 로컬 · 네이버 지도'),
       4: ('si', 'naver', '네이버 메일 · 로그인'), 5: ('png', 'baemin', '배달의민족 등 앱'),
       6: ('si', 'kakaotalk', '카카오톡 계정'), 8: ('lu', 'laptop', '내 PC · 폰')}
EX = 1440
for i, (k, n, l) in EXT.items():
    y = ys[i]
    line([(1352, y), (EX-22, y)])
    E.append(f'<rect x="{EX-20}" y="{y-20}" width="40" height="40" rx="9" fill="#fff" stroke="#cbd5e1" stroke-width="1.3"/>')
    E.append(icon(k, n, EX-13, y-13, 26))
    E.append(f'<text x="{EX+30}" y="{y+5}" class="lb">{esc(l)}</text>')
# Codex / ChatGPT
node(1470, 192, 'png', 'openai', 'ChatGPT 구독 모델', 'Codex OAuth · API 키 없음')

# edges
line([(120, 470), (179, 470)])
line([(240, 470), (300, 470), (300, 440), (380, 440)], '일반 요청', 340, 432)
line([(210, 440), (210, 192), (454, 192)], '대비스 온라인 · 오프라인', 330, 184)
line([(515, 192), (690, 192), (690, 318)], 'EC2 start · stop', 690, 250)
line([(440, 440), (544, 440)])
line([(605, 440), (714, 440)], 'Hermes로', 660, 432)
line([(776, 440), (884, 440)], '도구 호출', 830, 432)
line([(946, 440), (BUS, 440)])
line([(745, 410), (745, 300), (1150, 300), (1150, 192), (1439, 192)], '추론 요청 · 응답', 1290, 184, both=True)
line([(575, 512), (575, 619)])
line([(575, 722), (575, 935), (BUS, 935)], '같은 도구 함수를 바로 호출', 790, 928)
E.append(f'<path d="M{BUS},{ys[-1]+40} L{BUS},935" stroke="#111827" stroke-width="1.6" fill="none"/>')
line([(745, 512), (745, 569)], dashed=True, color='#64748b')
line([(745, 690), (745, 749)], '긴 승인 작업용', 745, 722, dashed=True, color='#64748b')
line([(915, 512), (915, 619)], '쓰기 도구면', 915, 565)
line([(915, 722), (915, 1030), (210, 1030), (210, 548)], '승인 요청 DM → 승인 · 거절 답장', 520, 1022)
line([(410, 512), (410, 569)], dashed=True, color='#64748b')

CSS = ('<style>.lb{font:600 13px "Pretendard","Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR",sans-serif;fill:#111827}'
       '.sb{font:11.5px "Pretendard","Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR",sans-serif;fill:#6b7280}'
       '.mono{font:11px ui-monospace,Consolas,monospace;fill:#6b7280}'
       '.bt{font:700 14px "Pretendard","Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR",sans-serif;fill:#334155}'
       '.el{font:11.5px "Pretendard","Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR",sans-serif;fill:#374151}</style>')
DEFS = ('<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
        '<path d="M0,0 L10,5 L0,10 z" fill="#111827"/></marker>'
        '<marker id="arrS" viewBox="0 0 10 10" refX="1" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
        '<path d="M10,0 L0,5 L10,10 z" fill="#111827"/></marker></defs>')
svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">{CSS}{DEFS}'
       f'<rect width="{W}" height="{H}" fill="#ffffff"/>' + ''.join(E) + '</svg>')
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(svg, encoding='utf-8')
print(OUT, len(svg))
