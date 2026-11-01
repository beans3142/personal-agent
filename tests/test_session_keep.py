from agent.authgate import AuthGate, Site

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
    remember_selector="#keep",
)


class Page:
    def __init__(self, logged_in=False):
        self.logged_in = logged_in
        self.checked = []
        self.filled = []

    def goto(self, url):
        pass

    def fill(self, selector, value):
        self.filled.append(selector)

    def check(self, selector):
        self.checked.append(selector)

    def click(self, selector):
        self.logged_in = True

    def has(self, selector):
        return selector == "#me" and self.logged_in

    def poll(self):
        pass


class Browser:
    def __init__(self, page, session=True):
        self.page = page
        self.session = session
        self.saved = []
        self.closed = 0

    def has_session(self, site):
        return self.session

    def open(self, site):
        return self.page

    def save(self, site):
        self.saved.append(site.name)

    def close(self):
        self.closed += 1


class Creds:
    def get(self, site):
        return ("u", "p")


def gate(browser):
    return AuthGate(Creds(), browser, {"demo": SITE}, wait=lambda: None)


def test_login_turns_on_keep_signed_in_before_submit():
    page = Page()
    gate(Browser(page, session=False)).ensure("demo")
    assert page.checked == ["#keep"]


def test_refresh_saves_a_live_session_without_password():
    page = Page(logged_in=True)
    browser = Browser(page)
    assert gate(browser).refresh("demo") == "ok"
    assert browser.saved == ["demo"]
    assert page.filled == []
    assert browser.closed == 1


def test_refresh_reports_expired_session_without_password():
    page = Page()
    browser = Browser(page)
    assert gate(browser).refresh("demo") == "expired"
    assert browser.saved == []
    assert page.filled == []


def test_refresh_skips_site_without_saved_session():
    browser = Browser(Page(), session=False)
    assert gate(browser).refresh("demo") == "no_session"
    assert browser.closed == 0


def test_refresh_all_keeps_going_after_one_site_fails():
    class Broken(Browser):
        def open(self, site):
            raise OSError("browser crashed")

    results = gate(Broken(Page())).refresh_all()
    assert results["demo"].startswith("error")
