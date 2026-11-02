from agent.authgate import AuthBlocked
from agent.naver_mail import build_mail_tool, build_message, owner_address


class FakeSmtp:
    sent = []
    logins = []

    def __init__(self, host, port, timeout):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        FakeSmtp.logins.append(user)

    def send_message(self, message):
        FakeSmtp.sent.append(message)


class FakePage:
    def __init__(self):
        self.composed = []

    def goto(self, url):
        pass

    def compose_mail(self, to, subject, body):
        self.composed.append((to, subject, body))


class FakeBrowser:
    def __init__(self):
        self.page = FakePage()
        self.saved = []

    def open(self, site):
        return self.page

    def save(self, site):
        self.saved.append(site.name)

    def close(self):
        pass


class FakeGate:
    def __init__(self, error=None):
        self.error = error

    def ensure(self, site):
        if self.error:
            raise AuthBlocked(self.error)
        return "ok"


ENV = {"NAVER_ID": "me@naver.com"}


def test_owner_address_adds_domain():
    assert owner_address({"NAVER_ID": "me"}) == "me@naver.com"
    assert owner_address(ENV) == "me@naver.com"


def test_empty_recipient_means_mail_to_self_from_owner():
    message = build_message(ENV, "", "제목", "본문")
    assert message["From"] == "me@naver.com"
    assert message["To"] == "me@naver.com"


def test_app_password_uses_smtp_with_owner_address():
    FakeSmtp.sent, FakeSmtp.logins = [], []
    browser = FakeBrowser()
    env = {**ENV, "NAVER_APP_PASSWORD": "x"}
    result = build_mail_tool(FakeGate(), browser, env, FakeSmtp).run({"subject": "s", "body": "b"})
    assert "from me@naver.com" in result
    assert FakeSmtp.logins == ["me@naver.com"]
    assert browser.page.composed == []


def test_without_app_password_uses_browser_session():
    browser = FakeBrowser()
    build_mail_tool(FakeGate(), browser, ENV).run({"subject": "제목", "body": "본문"})
    assert browser.page.composed == [("me@naver.com", "제목", "본문")]
    assert browser.saved == ["naver"]


def test_blocked_login_is_reported_and_nothing_is_sent():
    browser = FakeBrowser()
    result = build_mail_tool(FakeGate("second_factor_timeout"), browser, ENV).run({"subject": "s", "body": "b"})
    assert result == "blocked: second_factor_timeout"
    assert browser.page.composed == []
