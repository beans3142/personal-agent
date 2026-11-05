import json
import pytest
from agent.desktop_login import parse_answer, DesktopChallenge


def test_desktop_worker_does_not_inherit_login_credentials(monkeypatch):
    from types import SimpleNamespace
    from agent.desktop_login import DesktopChat
    monkeypatch.setenv('NAVER_PASSWORD', 'must-not-reach-worker')
    def run(argv, **kwargs):
        assert argv[-2:] == ['agent.desktop_login', '--ask']
        assert 'NAVER_PASSWORD' not in kwargs['env']
        assert kwargs['timeout'] == 90
        assert set(json.loads(kwargs['input'])) == {'image', 'question'}
        return SimpleNamespace(returncode=0, stdout='{"answer":"5"}')
    assert parse_answer(DesktopChat(run=run).ask(b'image', 'quantity?')) == '5'


def test_answer_is_structured_short_text_only():
    assert parse_answer('{"answer":"17"}') == '17'
    for raw in ['17','{"answer":null}','{"answer":"x","command":"click"}','{"answer":"a\\nb"}']:
        with pytest.raises(ValueError): parse_answer(raw)


def test_owner_denial_never_captures_or_uploads(tmp_path):
    calls=[]
    helper=DesktopChallenge(lambda *a: calls.append(a),tmp_path,approve=lambda *_:False)
    class Page:
        def login_challenge(self,site):pytest.fail('denial must precede capture')
    assert helper(type('Site',(),{'name':'naver'})(),Page()) is False
    assert calls == []


def test_duplicate_challenge_is_never_resubmitted(tmp_path):
    uploads=[]; submits=[]
    class Page:
        def login_challenge(self,site):return {'image':b'fake-image','question':'Number of items?','key':'test'}
        def submit_login_challenge(self,key,answer):submits.append((key,answer));return True
    helper=DesktopChallenge(lambda image,question:uploads.append(image) or '{"answer":"5"}',tmp_path,approve=lambda *_:True)
    site=type('Site',(),{'name':'naver'})()
    assert helper(site,Page()) is True
    assert helper(site,Page()) is False
    assert len(uploads)==1 and submits==[('test','5')]
    assert 'fake-image' not in (tmp_path/'desktop-challenge.json').read_text()


def test_verification_iframe_from_other_origin_is_never_captured():
    from types import SimpleNamespace
    from agent.authgate import PlaywrightPage, AuthBlocked
    iframe = SimpleNamespace(count=lambda: 1, get_attribute=lambda _: 'https://attacker.example/image',
        screenshot=lambda: pytest.fail('must not capture another origin'))
    page = PlaywrightPage(SimpleNamespace(url='https://nid.naver.com/nidlogin.login', locator=lambda _: iframe))
    with pytest.raises(AuthBlocked, match='origin'):
        page.login_challenge('naver')


def test_changed_challenge_never_receives_a_stale_answer():
    from types import SimpleNamespace
    from agent.authgate import PlaywrightPage
    page = PlaywrightPage(None)
    page._login_challenge = ('old-key', SimpleNamespace(get_attribute=lambda _: 'changed'), 'original',
        SimpleNamespace(fill=lambda _: pytest.fail('stale answer must not be entered')), None)
    assert page.submit_login_challenge('old-key', '5') is False


def test_separate_naver_verification_page_uses_its_confirm_button():
    from types import SimpleNamespace
    from agent.authgate import PlaywrightPage
    empty = SimpleNamespace(count=lambda: 0)
    image = SimpleNamespace(count=lambda: 1, get_attribute=lambda _: 'https://nid.naver.com/image', screenshot=lambda: b'crop')
    target = SimpleNamespace(count=lambda: 1)
    confirm = SimpleNamespace(count=lambda: 1)
    locators = {'#captchaimg:visible': image, '#captcha:visible, #chptcha:visible': target}
    page = PlaywrightPage(SimpleNamespace(url='https://nid.naver.com/nidlogin.login',
        locator=lambda selector: locators.get(selector, empty), get_by_role=lambda *a, **k: confirm))
    assert page.login_challenge('naver')['image'] == b'crop'
    assert page._login_challenge[-1] is confirm
