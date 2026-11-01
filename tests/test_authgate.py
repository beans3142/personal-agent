import json

import pytest

from agent.authgate import AuthBlocked, AuthGate, EnvCredentials, Site, build_auth_tool

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


def test_valid_session_does_not_use_password(vault):
    vault.set("demo", "u", "p")
    page = FakePage(present=["#me"])
    auth, browser = gate(vault, page)
    assert auth.ensure("demo") == "ok"
    assert page.filled == {}


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
