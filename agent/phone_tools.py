"""Server tools that operate the owner's phone through the Galaxy app's accessibility service.

Each tool is one `phone_task` job for the newest registered phone ("phone" on the relay).
The app's always-on link takes it within a second, runs it and returns the new screen
(uploaded as a file when large). Payment buttons wait for the owner's approval, like the
server Android tools.
"""
import json
import time
import uuid

from .approvals import PAYMENT, screen_amounts
from .loop import Tool

FINAL = ('succeeded', 'failed', 'expired', 'uncertain', 'cancelled', 'unknown')


class PhoneError(RuntimeError):
    pass


def phone_link(post, download, node='agent-main', sleep=time.sleep, monotonic=time.monotonic):
    """run(op, args) -> result dict, over the relay with the main agent's credentials."""
    def run(op, args, timeout=40):
        job = post('/request', {'node': node, 'target': 'phone', 'action': 'phone_task', 'payload': {'op': op, 'args': args},
                                'request_key': 'phone-' + uuid.uuid4().hex, 'ttl_seconds': 60})
        deadline = monotonic() + timeout
        state = {'status': 'queued'}
        while monotonic() < deadline:
            state = post('/request-status', {'node': node, 'id': job['id'], 'wait': 20})
            if state['status'] in FINAL:
                break
            sleep(0.2)
        result = state.get('result') or {}
        if state['status'] == 'succeeded':
            if result.get('data_file'):
                _, data = download(result['data_file'])
                result = json.loads(data)
            return result
        if state['status'] in ('queued', 'expired'):
            raise PhoneError('폰이 요청을 받지 않았어요. 대비스 앱의 연결(알림창 "대비스 연결됨")을 확인해 주세요.')
        if state['status'] in ('dispatched', 'uncertain'):
            raise PhoneError('폰에서 결과를 확인하지 못했어요. 실행됐을 수도 있으니 같은 동작을 바로 반복하지 마세요.')
        raise PhoneError(result.get('error') or f'폰 작업이 {state["status"]} 상태로 끝났어요.')
    return run


def build_phone_tools(phone, approve):
    """phone(op, args) runs one step on the phone; approve(kind, detail) asks the owner."""
    last = {'nodes': None}

    def remember(result):
        screen = result.get('screen') if 'screen' in result else result if 'nodes' in result else None
        if screen is not None:
            last['nodes'] = screen.get('nodes') or []
        return result

    def step(op, keys):
        def run(args):
            try:
                return json.dumps(remember(phone(op, {k: args[k] for k in keys if k in args})), ensure_ascii=False)
            except Exception as error:
                return f'error: {error}'
        return run

    def tap(args):
        nodes = last['nodes']
        if 'i' in args:
            if nodes is None:
                return 'error: 먼저 phone_screen으로 화면을 읽어 주세요'
            node = next((n for n in nodes if n.get('i') == args['i']), None)
            if node is None:
                return 'error: 화면에 없는 번호예요. phone_screen으로 다시 읽어 주세요'
            label = ' '.join(filter(None, (node.get('text'), node.get('desc'))))
            if PAYMENT.search(label):
                # Payment is the one tap that waits for the owner.
                if not approve('payment', {'button': label, 'package': 'phone', 'amounts': screen_amounts(
                        [{'text': n.get('text', ''), 'description': n.get('desc', '')} for n in nodes])}):
                    return 'error: 소유자가 결제를 승인하지 않아 누르지 않았어요'
        return step('tap', ('i', 'x', 'y'))(args)

    integer, text = {'type': 'integer'}, {'type': 'string'}
    def schema(props, required=()):
        return {'type': 'object', 'properties': props, 'required': list(required), 'additionalProperties': False}
    return [
        Tool('phone_screen', '소유자 폰의 지금 화면을 읽는다(앱, 요소 번호 i, 글자, 누를 수 있는지 c, 입력칸 e, 위치 b). 다른 phone_* 도구는 실행 뒤 새 화면(screen)을 같이 돌려준다.',
             schema({}), step('screen', ())),
        Tool('phone_tap', '폰 화면 요소 i(phone_screen 번호)나 좌표 x,y를 누른다. 결제 버튼은 소유자 승인 뒤에만 누른다.',
             schema({'i': integer, 'x': integer, 'y': integer}), tap),
        Tool('phone_type', '폰 입력칸 i(생략하면 지금 입력칸)에 text를 넣는다(기존 내용을 바꾼다).',
             schema({'i': integer, 'text': text}, ('text',)), step('type', ('i', 'text'))),
        Tool('phone_key', '폰 버튼: back, home, recents, enter(입력칸에서 보내기·검색).',
             schema({'key': {'type': 'string', 'enum': ['back', 'home', 'recents', 'enter']}}, ('key',)), step('key', ('key',))),
        Tool('phone_open', '폰에서 앱을 연다. app은 패키지 이름(카카오톡 com.kakao.talk, 배민 com.sampleapp, 캘린더 com.samsung.android.calendar) 또는 앱 이름.',
             schema({'app': text}, ('app',)), step('open', ('app',))),
        Tool('phone_scroll', '폰 화면 요소 i(생략하면 화면 전체)를 down/up으로 스크롤한다.',
             schema({'i': integer, 'dir': {'type': 'string', 'enum': ['down', 'up']}}, ('dir',)), step('scroll', ('i', 'dir'))),
    ]
