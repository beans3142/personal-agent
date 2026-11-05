import json

import pytest

from agent.authgate import AuthBlocked, AuthGate, EnvCredentials, Site, build_auth_tool


def test_stale_naver_cookie_on_login_redirect_is_not_authenticated():
    from types import SimpleNamespace
    from agent.authgate import PlaywrightPage
    page = SimpleNamespace(url='https://nid.naver.com/nidlogin.login?url=mail',
        context=SimpleNamespace(cookies=lambda: [{'name': 'NID_AUT'}]))
    wrapped = PlaywrightPage(page)
    assert not wrapped.has('cookie:NID_AUT')
    page.url = 'https://mail.naver.com/v2/folders/0'
    assert wrapped.has('cookie:NID_AUT')


def test_login_verification_handler_runs_once_then_checks_actual_session(vault):
    vault.set('demo', 'u', 'p')
    page = FakePage(after_submit=[['#captcha']])
    auth, browser = gate(vault, page)
    calls = []
    def solve(site, active):
        calls.append(site.name)
        active.present = {'#me'}
        return True
    auth.challenge_handler = solve
    assert auth.ensure('demo') == 'ok'
    assert calls == ['demo'] and browser.saved == ['demo']


def test_unchanged_verification_is_not_solved_repeatedly(vault):
    vault.set('demo', 'u', 'p')
    page = FakePage(after_submit=[['#captcha']])
    auth, browser = gate(vault, page)
    calls = []
    auth.challenge_handler = lambda site, page: calls.append(site.name) or True
    with pytest.raises(AuthBlocked, match='captcha'):
        auth.ensure('demo')
    assert calls == ['demo'] and browser.saved == []


def test_naver_verification_returning_to_login_resumes_credentials_once(vault):
    from dataclasses import replace
    site = replace(SITE, name='naver')
    vault.set('naver', 'u', 'p')
    page = FakePage(after_submit=[['#captcha'], ['#me']])
    page.poll = lambda: None  # Only an actual second submit can reach #me.
    browser = FakeBrowser(page)
    def solved(site, page):
        page.present = {'#id:visible', '#pw:visible'}
        return True
    auth = AuthGate(vault, browser, {'naver': site}, wait=lambda: None, attempts=3, challenge_handler=solved)
    assert auth.ensure('naver') == 'ok'
    assert browser.saved == ['naver']


def test_naver_does_not_resubmit_credentials_without_completed_verification(vault):
    from dataclasses import replace
    site = replace(SITE, name='naver')
    vault.set('naver', 'u', 'p')
    page = FakePage(after_submit=[['#id:visible', '#pw:visible']])
    browser = FakeBrowser(page)
    auth = AuthGate(vault, browser, {'naver': site}, wait=lambda: None, attempts=2)
    with pytest.raises(AuthBlocked, match='login_failed'):
        auth.ensure('naver')


def test_account_protection_stops_before_more_credentials_or_captcha(vault):
    vault.set('demo', 'u', 'p')
    page = FakePage()
    page.auth_block_reason = lambda site: 'account_protected'
    auth, browser = gate(vault, page)
    notices = []
    auth.notify = notices.append
    with pytest.raises(AuthBlocked, match='account_protected'):
        auth.ensure('demo')
    assert not page.filled and not browser.saved and len(notices) == 1


def test_paused_login_does_not_submit_but_a_valid_session_remains_usable(vault):
    vault.set('demo', 'u', 'p')
    page = FakePage()
    auth, browser = gate(vault, page)
    auth.can_login = lambda site: False
    with pytest.raises(AuthBlocked, match='login_paused'):
        auth.ensure('demo')
    assert not page.filled and not browser.saved
    page.present = {'#me'}
    assert auth.ensure('demo') == 'ok'


def test_unexpected_submit_error_also_pauses_instead_of_retrying(vault):
    vault.set('demo', 'u', 'p')
    page = FakePage()
    auth, browser = gate(vault, page)
    outcomes = []
    auth.record_outcome = lambda site, reason: outcomes.append((site, reason))
    page.click = lambda _: (_ for _ in ()).throw(RuntimeError('uncertain submit'))
    with pytest.raises(RuntimeError):
        auth.ensure('demo')
    assert outcomes == [('demo', 'runtime_error')] and not browser.saved


def test_naver_protection_page_is_detected_by_exact_official_host_and_path():
    from types import SimpleNamespace
    from agent.authgate import PlaywrightPage
    page = SimpleNamespace(url='https://nid.naver.com/user2/help/idSafetyRelease?private=not-logged')
    assert PlaywrightPage(page).auth_block_reason('naver') == 'account_protected'
    page.url = 'https://attacker.example/user2/help/idSafetyRelease'
    assert PlaywrightPage(page).auth_block_reason('naver') is None


def test_real_naver_mailbox_accepts_compose_label_with_a_space():
    from types import SimpleNamespace
    from agent.authgate import PlaywrightPage
    seen = []
    class Marker:
        first = property(lambda self: self)
        def wait_for(self, **kwargs): pass
        def count(self): return 1
    def by_text(pattern):
        seen.append(pattern)
        assert pattern.fullmatch('메일 쓰기') and pattern.fullmatch('메일쓰기')
        return Marker()
    page = SimpleNamespace(url='https://mail.naver.com/v2/folders/0', get_by_text=by_text,
        context=SimpleNamespace(cookies=lambda: [{'name': 'NID_AUT'}, {'name': 'NID_SES'}]))
    assert PlaywrightPage(page).has('naver:mailbox')
    page.url = 'https://nid.naver.com/nidlogin.login'
    assert not PlaywrightPage(page).has('naver:mailbox')
    assert len(seen) == 1


def test_naver_post_verification_credentials_are_resubmitted_at_most_once(vault):
    from dataclasses import replace
    site = replace(SITE, name='naver')
    vault.set('naver', 'u', 'p')
    page = FakePage(after_submit=[['#captcha'], ['#id:visible', '#pw:visible']])
    page.poll = lambda: None
    clicks = []
    click = page.click
    page.click = lambda selector: (clicks.append(selector), click(selector))[-1]
    def solved(site, page):
        page.present = {'#id:visible', '#pw:visible'}
        return True
    auth = AuthGate(vault, FakeBrowser(page), {'naver': site}, wait=lambda: None, attempts=3, challenge_handler=solved)
    with pytest.raises(AuthBlocked, match='login_failed'):
        auth.ensure('naver')
    assert clicks == ['#go', '#go']

SITE = Site(
    name="demo",
    login_url="https://demo/login",
    check_url="https://demo/home",
    user_selector="#id",
    password_selector="#pw",
    submit_selector="#go",
    logged_in_selector="#me",
    captcha_selector="#captcha",
    second_factor_selector="#otp",
)


class Creds(dict):
    def set(self, site, user, password):
        self[site] = (user, password)


@pytest.fixture
def vault():
    return Creds()


class FakePage:
    def __init__(self, present=(), after_submit=()):
        self.present = set(present)
        self.after_submit = list(after_submit)
        self.filled = {}
        self.visited = []

    def goto(self, url):
        self.visited.append(url)

    def fill(self, selector, value):
        self.filled[selector] = value

    def click(self, selector):
        assert selector == "#go"
        if self.after_submit:
            self.present = set(self.after_submit.pop(0))

    def has(self, selector):
        return selector in self.present

    def poll(self):
        if self.after_submit:
            self.present = set(self.after_submit.pop(0))


class FakeBrowser:
    def __init__(self, page):
        self.page = page
        self.saved = []

    def open(self, site):
        return self.page

    def save(self, site):
        self.saved.append(site.name)

    def close(self):
        pass


def gate(vault, page, attempts=3):
    browser = FakeBrowser(page)
    return AuthGate(vault, browser, {"demo": SITE}, wait=lambda: None, attempts=attempts), browser


def test_env_credentials_read_both_values():
    creds = EnvCredentials({"NAVER_ID": "u", "NAVER_PASSWORD": "p", "KAKAO_ID": "only-id"})
    assert creds.get("naver") == ("u", "p")
    assert creds.get("kakao") is None
    assert creds.get("other") is None


def test_verification_capture_copies_loaded_native_image_without_refetch():
    import base64
    from types import SimpleNamespace
    from agent.authgate import verification_image
    png = b'\x89PNG\r\n\x1a\nloaded-native-pixels'
    marker = SimpleNamespace(evaluate=lambda _: 'data:image/png;base64,'+base64.b64encode(png).decode(),
        screenshot=lambda: pytest.fail('native image should preserve original pixels'))
    assert verification_image(marker) == png
    marker.evaluate = lambda _: None
    marker.screenshot = lambda: b'fallback'
    assert verification_image(marker) == b'fallback'


def test_valid_session_does_not_use_password(vault):
    vault.set("demo", "u", "p")
    page = FakePage(present=["#me"])
    auth, browser = gate(vault, page)
    assert auth.ensure("demo") == "ok"
    assert page.filled == {}
    assert browser.saved == ["demo"]


def test_expired_session_logs_in_again_and_saves(vault):
    vault.set("demo", "u", "p")
    page = FakePage(after_submit=[["#me"]])
    auth, browser = gate(vault, page)
    assert auth.ensure("demo") == "ok"
    assert page.filled == {"#id": "u", "#pw": "p"}
    assert browser.saved == ["demo"]


def test_missing_credentials_are_reported(vault):
    auth, _ = gate(vault, FakePage())
    with pytest.raises(AuthBlocked, match="no_credentials"):
        auth.ensure("demo")


def test_captcha_stops_login_without_saving(vault):
    vault.set("demo", "u", "p")
    auth, browser = gate(vault, FakePage(after_submit=[["#captcha"]]))
    with pytest.raises(AuthBlocked, match="captcha"):
        auth.ensure("demo")
    assert browser.saved == []


def test_second_factor_tells_owner_once_and_waits_for_phone(vault):
    vault.set("demo", "u", "p")
    notices = []
    page = FakePage(after_submit=[["#otp"], ["#otp"], ["#me"]])
    browser = FakeBrowser(page)
    auth = AuthGate(vault, browser, {"demo": SITE}, wait=lambda: None, notify=notices.append)
    assert auth.ensure("demo") == "ok"
    assert len(notices) == 1
    assert browser.saved == ["demo"]


def test_second_factor_timeout_is_reported(vault):
    vault.set("demo", "u", "p")
    page = FakePage(after_submit=[["#otp"]])
    auth = AuthGate(vault, FakeBrowser(page), {"demo": SITE}, wait=lambda: None, second_factor_attempts=2)
    with pytest.raises(AuthBlocked, match="second_factor_timeout"):
        auth.ensure("demo")


def test_wrong_password_result_is_reported(vault):
    vault.set("demo", "u", "p")
    auth, browser = gate(vault, FakePage(after_submit=[[]]), attempts=2)
    with pytest.raises(AuthBlocked, match="login_failed"):
        auth.ensure("demo")
    assert browser.saved == []


def test_two_step_login_enters_id_before_password(vault):
    two_step = Site(**{**SITE.__dict__, "user_next_selector": "#next"})
    order = []

    class StepPage(FakePage):
        def fill(self, selector, value):
            order.append(selector)

        def click(self, selector):
            order.append(selector)
            if selector == "#go":
                self.present = {"#me"}

    vault.set("demo", "u", "p")
    auth = AuthGate(vault, FakeBrowser(StepPage()), {"demo": two_step}, wait=lambda: None)
    assert auth.ensure("demo") == "ok"
    assert order == ["#id", "#next", "#pw", "#go"]


def test_unknown_site_is_rejected(vault):
    auth, _ = gate(vault, FakePage())
    with pytest.raises(AuthBlocked, match="unknown_site"):
        auth.ensure("nope")


def test_model_tool_never_returns_credentials(vault):
    vault.set("demo", "user-secret", "pass-secret")
    auth, _ = gate(vault, FakePage(after_submit=[["#me"]]))
    tool = build_auth_tool(auth)
    result = tool.run({"site": "demo"})
    assert "pass-secret" not in result and "user-secret" not in result
    assert json.loads(result)["status"] == "ok"


def test_model_tool_blocked_result_has_no_credentials(vault):
    vault.set("demo", "user-secret", "pass-secret")
    auth, _ = gate(vault, FakePage(after_submit=[["#captcha"]]))
    result = build_auth_tool(auth).run({"site": "demo"})
    assert "pass-secret" not in result
    assert json.loads(result)["status"] == "captcha"
