from agent.phone_kakao import kakao_send

MSG = 'PC -> Server -> 모바일 카톡 전송 테스트입니다'


class KakaoPhone:
    """A scripted Kakao: main -> search -> results -> chat -> sent."""
    def __init__(self, title='반수의신', results=None, bubble=True):
        self.title, self.results, self.bubble = title, results, bubble
        self.state, self.typed, self.calls = 'main', None, []

    def screen(self):
        if self.state == 'main':
            return [{'i': 0, 'desc': 'Search', 'c': True}]
        if self.state == 'search':
            return [{'i': 0, 'e': True, 'c': True}]
        if self.state == 'results':
            return self.results or [{'i': 5, 'text': '반수의신 알림꺼짐 마지막 메시지', 'c': True},
                                    {'i': 6, 'text': 'ㅄ 5 알림꺼짐', 'c': True}, {'i': 7, 'desc': '반수의신', 'c': True}]
        # Kakao puts the chat title in the element's description.
        nodes = [{'i': 0, 'id': 'toolbar_default_title_text', 'desc': self.title}, {'i': 1, 'id': 'message_edit_text', 'e': True}]
        if self.typed:
            nodes.append({'i': 2, 'desc': 'Send', 'c': True})
        if self.state == 'sent' and self.bubble:
            nodes.append({'i': 3, 'id': 'message', 'text': self.typed})
        return nodes

    def __call__(self, op, args):
        self.calls.append((op, args))
        if op == 'tap':
            if self.state == 'chat':
                self.state = 'sent' if args.get('i') == 2 and self.typed else 'chat'
            else:
                self.state = {'main': 'search', 'results': 'chat'}.get(self.state, self.state)
        elif op == 'type':
            if self.state == 'search':
                self.state = 'results'
            else:
                self.typed = args['text']
        screen = {'nodes': self.screen()}
        return screen if op == 'screen' else {'ok': True, 'screen': screen}


def nap(_):
    pass


def test_sends_after_checking_title_and_sees_the_bubble():
    p = KakaoPhone()
    assert kakao_send(p, '반수의신', MSG, sleep=nap) == {'code': 'KAKAO_SENT', 'recipient': '반수의신'}
    assert ('type', {'i': 1, 'text': MSG}) in p.calls
    # The message box is tapped before typing so Kakao shows Send.
    assert p.calls.index(('tap', {'i': 1})) < p.calls.index(('type', {'i': 1, 'text': MSG}))
    # The 1:1 room is chosen, not the group room that also starts with a name.
    assert ('tap', {'i': 5}) in p.calls


def test_wrong_title_or_several_matches_never_send():
    assert kakao_send(KakaoPhone(title='다른사람'), '반수의신', MSG, sleep=nap)['code'] == 'KAKAO_NOT_SENT'
    two = [{'i': 5, 'text': '반수의신 a', 'c': True}, {'i': 6, 'text': '반수의신 b', 'c': True}]
    assert kakao_send(KakaoPhone(results=two), '반수의신', MSG, sleep=nap)['code'] == 'KAKAO_RECIPIENT_REQUIRED'
    none = [{'i': 5, 'text': '검색 결과가 없습니다.'}]
    assert kakao_send(KakaoPhone(results=none), '반수의신', MSG, sleep=nap)['code'] == 'KAKAO_RECIPIENT_REQUIRED'


def test_missing_bubble_is_uncertain_not_retried():
    p = KakaoPhone(bubble=False)
    assert kakao_send(p, '반수의신', MSG, sleep=nap)['code'] == 'KAKAO_UNCERTAIN'
    assert sum(1 for op, a in p.calls if op == 'tap' and a == {'i': 2}) == 1


def test_dry_run_opens_and_verifies_without_typing_the_message():
    p = KakaoPhone()
    assert kakao_send(p, '반수의신', MSG, dry_run=True, sleep=nap)['code'] == 'KAKAO_CHECKED'
    assert not any(op == 'type' and a.get('text') == MSG for op, a in p.calls)


def test_locked_phone_stops_at_once_without_pressing_keys():
    calls = []
    def locked(op, args):
        calls.append(op)
        return {'ok': True, 'screen': {'package': 'com.android.systemui', 'nodes': [{'i': 0, 'id': 'common_date'}]}}
    out = kakao_send(locked, '반수의신', MSG, sleep=nap)
    assert out['code'] == 'KAKAO_NOT_SENT' and 'locked' in out['reason'] and calls == ['open']


def test_pin_locked_phone_reports_locked():
    def pin_locked(op, args):
        return {'package': 'com.kakao.talk', 'locked': True, 'nodes': [{'i': 0}]}
    assert 'locked' in kakao_send(pin_locked, '반수의신', MSG, sleep=nap)['reason']
