from pathlib import Path

from agent.authgate import Site
from agent.qr_login import QR_SITES, qr_login

SITE = Site(
    name="demo", login_url="https://demo/login", check_url="https://demo/home",
    user_selector="#id", password_selector="#pw", submit_selector="#go",
    logged_in_selector="#me", captcha_selector="#c", second_factor_selector="#o",
)


class Page:
    def __init__(self, logged_after=2):
        self.polls = 0
        self.logged_after = logged_after
        self.visited = []
        self.filled = []
        self.shots = []

    def goto(self, url):
        self.visited.append(url)

    def fill(self, selector, value):
        self.filled.append(selector)

    def poll(self):
        self.polls += 1

    def has(self, selector):
        return selector == "#me" and self.polls >= self.logged_after

    def screenshot(self, path):
        self.shots.append(path)


class Browser:
    def __init__(self, page):
        self.page = page
        self.saved = []
        self.closed = 0

    def open(self, site):
        return self.page

    def save(self, site):
        self.saved.append(site.name)

    def close(self):
        self.closed += 1


def run(page, attempts=5, sent=None):
    browser = Browser(page)
    images = sent if sent is not None else []
    result = qr_login(SITE, "https://demo/qr", browser, lambda text, path: images.append((text, path)),
                      Path("shot.png"), wait=lambda: None, attempts=attempts)
    return result, browser, images


def test_sends_qr_image_saves_session_and_uses_no_password():
    page = Page()
    result, browser, images = run(page)
    assert result == "ok"
    assert page.visited == ["https://demo/qr"]
    assert page.filled == []
    assert len(images) == 1 and images[0][1] == Path("shot.png")
    assert browser.saved == ["demo"]
    assert browser.closed == 1


def test_times_out_without_saving_and_closes_browser():
    result, browser, _ = run(Page(logged_after=99), attempts=3)
    assert result == "timeout"
    assert browser.saved == []
    assert browser.closed == 1


def test_expired_code_is_reloaded_and_sent_again():
    sent = []
    page = Page(logged_after=99)
    browser = Browser(page)
    qr_login(SITE, "https://demo/qr", browser, lambda text, path: sent.append(path), Path("s.png"),
             wait=lambda: None, attempts=4, refresh_every=2)
    assert len(sent) == 2
    assert page.visited == ["https://demo/qr", "https://demo/qr"]


def test_naver_and_discord_have_qr_pages():
    assert "naver" in QR_SITES and "discord" in QR_SITES
