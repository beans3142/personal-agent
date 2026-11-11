"""Server tools that operate the owner's Windows PC.

Each tool becomes one `pc_task` job on the power relay for the PC's Daevis agent
(desktop/remote.py). The PC long-polls, so a step usually starts within a second.
Files from the PC (found files, screenshots) are downloaded into the request's
outbox `files/` folder; the gateway sends that folder to the owner afterwards.
"""
import json
import os
from pathlib import Path
import re
import time
import urllib.parse
import urllib.request
import uuid

from .loop import Tool

DISCORD_LIMIT = 10 * 1024 * 1024  # Bigger files go to the owner as a download link instead.
FINAL = ('succeeded', 'failed', 'expired', 'uncertain', 'cancelled', 'unknown')


class PCError(Exception):
    pass


def _norm(label):
    text = re.sub(r'\s+', '', str(label or '')).casefold().replace('데스크탑', '데스크톱')
    text = re.sub(r'^내', '', text)
    text = re.sub(r'(pc|컴퓨터)$', '', text) or text
    return {'desktop': '데스크톱', 'laptop': '노트북'}.get(text, text)


class PCLink:
    def __init__(self, post, download, upload, node='agent-main', sleep=time.sleep, monotonic=time.monotonic):
        self.post, self.download, self.upload, self.node = post, download, upload, node
        self.sleep, self.monotonic = sleep, monotonic

    def pcs(self):
        return self.post('/pc/list', {'node': self.node})['pcs']

    def target(self, label=None):
        pcs = self.pcs()
        if not pcs:
            raise PCError('등록된 PC가 없어요. Discord에서 "PC 등록 이름"으로 먼저 등록해 주세요.')
        if label:
            matches = [p for p in pcs if _norm(p['label']) == _norm(label)]
            if not matches:
                raise PCError(f'"{label}" 이름의 등록된 PC가 없어요. 등록된 PC: ' + ', '.join(p['label'] for p in pcs))
            pc = matches[0]
        else:
            online = [p for p in pcs if p['online']]
            default = [p for p in pcs if p['default'] and p['online']]
            pc = (default or online or [p for p in pcs if p['default']] or pcs)[0]
        if not pc['online']:
            raise PCError(f'{pc["label"]} PC가 연결되어 있지 않아요. PC가 켜져 있고 Daevis가 실행 중인지 확인해 주세요.')
        return pc['node']

    def run(self, op, args, pc=None, timeout=60):
        node = self.target(pc)
        job = self.post('/request', {'node': self.node, 'target': node, 'action': 'pc_task', 'payload': {'op': op, 'args': args},
                                     'request_key': 'pc-' + uuid.uuid4().hex, 'ttl_seconds': min(300, int(timeout) + 30)})
        deadline = self.monotonic() + timeout + 40
        state = {'status': 'queued'}
        while self.monotonic() < deadline:
            state = self.post('/request-status', {'node': self.node, 'id': job['id'], 'wait': 20})
            if state['status'] in FINAL:
                break
            self.sleep(0.2)
        result = state.get('result') or {}
        if state['status'] == 'succeeded':
            if result.get('data_file'):
                _, data = self.download(result['data_file'])
                result = json.loads(data)
            return result
        if state['status'] == 'expired':
            raise PCError('PC가 요청을 받지 않았어요. PC가 잠자기 상태이거나 Daevis가 멈췄을 수 있어요.')
        if state['status'] in ('queued', 'dispatched', 'uncertain'):
            raise PCError('PC에서 결과를 확인하지 못했어요. 실행됐을 수도 있으니 같은 동작을 바로 반복하지 마세요.')
        raise PCError(result.get('message') or result.get('error') or f'PC 작업이 {state["status"]} 상태로 끝났어요.')

    def share(self, file_id):
        return self.post('/pc/share', {'node': self.node, 'id': file_id})

    def fetch(self, meta, folder):
        """Download an attachment into folder, keeping its name unique."""
        name, data = self.download(meta['id'])
        name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', Path(name).name) or 'file'
        folder.mkdir(parents=True, exist_ok=True)
        stem, suffix = os.path.splitext(name)
        path, n = folder / name, 1
        while path.exists():
            path, n = folder / f'{stem} ({n}){suffix}', n + 1
        path.write_bytes(data)
        return path


def relay_link(base, token, node='agent-main'):
    """PCLink over HTTPS with the main agent's node credentials."""
    from .voice_agent import relay_post
    base = base.rstrip('/')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    post = relay_post(base, token)

    def download(file_id):
        request = urllib.request.Request(base + '/pc/download', data=json.dumps({'node': node, 'id': file_id}).encode(), method='POST',
                                         headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        with opener.open(request, timeout=120) as response:
            return urllib.parse.unquote(response.headers.get('X-Daevis-Name', 'file')), response.read()

    def upload(audience, name, data):
        request = urllib.request.Request(base + '/pc/upload', data=data, method='POST', headers={
            'Authorization': 'Bearer ' + token, 'Content-Type': 'application/octet-stream', 'X-Daevis-Node': node,
            'X-Daevis-Audience': audience, 'X-Daevis-Name': urllib.parse.quote(name)})
        with opener.open(request, timeout=120) as response:
            return json.loads(response.read(65536))
    return PCLink(post, download, upload, node)


def _schema(properties, required=()):
    return {'type': 'object', 'properties': {**properties, 'pc': {'type': 'string', 'description': 'PC 이름. 생략하면 기본 PC'}},
            'required': list(required), 'additionalProperties': False}


def build_pc_tools(link, outbox, files_root):
    """outbox() is the current request's outbox; pc_save_file only reads files under files_root (run_code output)."""
    def call(op, keys=(), timeout=60, after=None):
        def run(args):
            try:
                step = {k: args[k] for k in keys if k in args}
                result = link.run(op, step, args.get('pc'), timeout=int(args.get('timeout', timeout)) if op == 'shell' else timeout)
                if after:
                    result = after(args, result)
                return json.dumps(result, ensure_ascii=False)
            except PCError as error:
                return f'error: {error}'
            except Exception as error:
                return f'error: PC 작업 실패 ({type(error).__name__}); 결과가 불확실하니 같은 동작을 바로 반복하지 마세요'
        return run

    def deliver(args, result):
        meta = result.pop('file')
        if meta.get('size', 0) > DISCORD_LIMIT:
            share = link.share(meta['id'])
            result.update(link=share['url'], link_expires_hours=share['expires_hours'], name=meta.get('name'),
                          note='Discord 첨부 한도(10MB)를 넘어 다운로드 링크로 보낸다. 답변에 링크를 그대로 적는다.')
            return result
        path = link.fetch(meta, outbox() / 'files')
        result['sent_to_owner'] = path.name
        return result

    def save(args):
        path = Path(str(args.get('path', ''))).resolve()
        roots = [Path(files_root).resolve(), outbox().resolve()]
        if not path.is_file() or not any(path.is_relative_to(root) for root in roots):
            return 'error: run_code 결과나 이번 요청의 파일만 PC로 보낼 수 있어요'
        try:
            node = link.target(args.get('pc'))
            meta = link.upload(node, path.name, path.read_bytes())
            return call('save_file', ('file', 'name'), 120)({'file': meta['id'], 'name': path.name, **({'pc': args['pc']} if args.get('pc') else {})})
        except PCError as error:
            return f'error: {error}'

    text = {'type': 'string'}
    integer = {'type': 'integer'}
    return [
        Tool('pc_list', '등록된 PC와 연결 상태.', _schema({}),
             lambda args: json.dumps(link.pcs(), ensure_ascii=False)),
        Tool('pc_screenshot', 'PC 화면(모든 모니터)을 찍어 소유자에게 이미지로 보낸다. 화면 좌표 = origin + 이미지 좌표 / scale.',
             _schema({'max_width': integer}), call('screenshot', ('max_width',), 60, deliver)),
        Tool('pc_windows', 'PC에 열린 창 목록(제목, 프로그램, 위치, 앞에 있는 창).', _schema({}), call('windows')),
        Tool('pc_ui', 'PC 창 하나의 UI 요소를 e1, e2... 번호로 본다(UI Automation). window는 창 제목 일부, 생략하면 맨 앞 창. 번호는 다음 pc_ui까지 유효.',
             _schema({'window': text, 'limit': integer, 'depth': integer}), call('ui', ('window', 'limit', 'depth'), 90)),
        Tool('pc_act', 'PC 조작. action: click/double_click/right_click/invoke/focus/type/set_value/key/scroll. '
                       'element(pc_ui 번호) 또는 x,y 화면 좌표. type/set_value는 text, key는 keys(예: ctrl+s, alt+f4, win+r, enter), scroll은 amount(음수=아래). type은 선택된 내용을, set_value는 전체 내용을 덮어쓰니 기존 문서는 입력 전후로 pc_ui의 value를 확인한다.',
             _schema({'action': {'type': 'string', 'enum': ['click', 'double_click', 'right_click', 'invoke', 'focus', 'type', 'set_value', 'key', 'scroll']},
                      'element': text, 'x': integer, 'y': integer, 'text': text, 'keys': text, 'amount': integer}, ('action',)),
             call('act', ('action', 'element', 'x', 'y', 'text', 'keys', 'amount'))),
        Tool('pc_open', 'PC에서 파일, 폴더, URL, 프로그램(실행 창에 넣는 이름: notepad, chrome, ms-settings: 등)을 연다. 이미 열린 창이 앞으로 올 수도 있으니 소유자의 작업 중인 창인지 pc_windows로 확인한다.',
             _schema({'target': text}, ('target',)), call('open', ('target',))),
        Tool('pc_find_files', 'PC 전체에서 파일 이름 검색(사용자 폴더와 C 외 드라이브, 최신순). query는 이름에 모두 들어갈 단어, exts는 확장자 목록(예: ["pptx","pdf"]), roots로 폴더 지정. "내 데스크톱/노트북에서"는 보통 등록된 PC 이름(pc 인자)이지 바탕화면 폴더가 아니다. 발표자료처럼 형식이 여럿이면 exts를 넓게(pptx, ppt, key, pdf, html) 잡는다.',
             _schema({'query': text, 'exts': {'type': 'array', 'items': text}, 'roots': {'type': 'array', 'items': text}, 'limit': integer},
                     ('query',)), call('find', ('query', 'exts', 'roots', 'limit'), 70)),
        Tool('pc_send_file', 'PC 파일 하나(100MB 이하)를 소유자에게 보낸다. 10MB 이하는 Discord 첨부, 더 크면 24시간 다운로드 링크를 돌려주니 답변에 링크를 적는다. path는 pc_find_files가 준 전체 경로.',
             _schema({'path': text}, ('path',)), call('send_file', ('path',), 180, deliver)),
        Tool('pc_save_file', '서버 파일(run_code 결과 등)을 PC의 다운로드 폴더에 저장한다.', _schema({'path': text}, ('path',)), save),
        Tool('pc_system', 'PC 볼륨·미디어·잠금·절전·종료. action: mute/volume_up/volume_down/volume(level 0~100)/play_pause/next/previous/stop/lock/sleep/shutdown/restart/cancel_shutdown. 종료와 재시작은 60초 뒤.',
             _schema({'action': {'type': 'string', 'enum': ['mute', 'volume_up', 'volume_down', 'volume', 'play_pause', 'next', 'previous', 'stop',
                                                          'lock', 'sleep', 'shutdown', 'restart', 'cancel_shutdown']},
                      'level': integer, 'times': integer}, ('action',)), call('system', ('action', 'level', 'times'))),
        Tool('pc_shell', 'PC에서 PowerShell 명령을 소유자 권한으로 실행하고 출력과 종료 코드를 받는다. timeout 초(최대 280).',
             _schema({'command': text, 'timeout': integer, 'cwd': text}, ('command',)), call('shell', ('command', 'timeout', 'cwd'), 60)),
    ]


PC_TOOL_NAMES = frozenset({'pc_list', 'pc_screenshot', 'pc_windows', 'pc_ui', 'pc_act', 'pc_open', 'pc_find_files', 'pc_send_file',
                           'pc_save_file', 'pc_system', 'pc_shell'})
