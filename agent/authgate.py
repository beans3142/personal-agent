"""Keeps site logins alive. Passwords come from the environment and never reach the model.

A captcha is never solved or bypassed: the login stops and reports it.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from .loop import Tool

LOGIN_ATTEMPTS = 6
SECOND_FACTOR_ATTEMPTS = 60
POLL_SECONDS = 5


class AuthBlocked(Exception):
    pass


@dataclass(frozen=True)
class Site:
    name: str
    login_url: str
    check_url: str
    user_selector: str
    password_selector: str
    submit_selector: str
    logged_in_selector: str
    captcha_selector: str
    second_factor_selector: str


SITES = {
    "discord": Site(
        name="discord",
        login_url="https://discord.com/login",
        check_url="https://discord.com/channels/@me",
        user_selector="input[name='email']",
        password_selector="input[name='password']",
        submit_selector="button[type='submit']",
        logged_in_selector="nav[aria-label] [data-list-id='guildsnav']",
        captcha_selector="iframe[src*='hcaptcha']",
        second_factor_selector="input[autocomplete='one-time-code']",
    ),
    "naver": Site(
        name="naver",
        login_url="https://nid.naver.com/nidlogin.login",
        check_url="https://www.naver.com/",
        user_selector="#id",
        password_selector="#pw",
        submit_selector="button[id^='loginBtn']:visible",
        logged_in_selector="a[href*='nid.naver.com/nidlogin.logout']",
        captcha_selector="#captcha, #chptcha",
        second_factor_selector="#push_title, #otp",
    ),
    "kakao": Site(
        name="kakao",
        login_url="https://accounts.kakao.com/login/?continue=https%3A%2F%2Faccounts.kakao.com%2Fweblogin%2Faccount",
        check_url="https://accounts.kakao.com/weblogin/account",
        user_selector="input[name='loginId']",
        password_selector="input[name='password']",
        submit_selector="button[type='submit']",
        logged_in_selector="a[href*='logout'], .thumb_profile",
        captcha_selector="iframe[src*='captcha'], #captchaContainer",
        second_factor_selector="input[name='passcode'], .tit_certify",
    ),
}


class EnvCredentials:
    def __init__(self, env):
        self.env = env

    def get(self, site: str) -> tuple[str, str] | None:
        user = self.env.get(f"{site.upper()}_ID")
        password = self.env.get(f"{site.upper()}_PASSWORD")
        return (user, password) if user and password else None


class AuthGate:
    def __init__(self, credentials, browser, sites: dict[str, Site] = SITES,
                 wait=lambda: time.sleep(POLL_SECONDS), attempts: int = LOGIN_ATTEMPTS,
                 second_factor_attempts: int = SECOND_FACTOR_ATTEMPTS, notify=lambda text: None):
        self.second_factor_attempts = second_factor_attempts
        self.notify = notify
        self.credentials = credentials
        self.browser = browser
        self.sites = sites
        self.wait = wait
        self.attempts = attempts

    def ensure(self, name: str) -> str:
        """Returns "ok" when the site session is usable. Raises AuthBlocked with a status otherwise."""
        site = self.sites.get(name)
        if site is None:
            raise AuthBlocked("unknown_site")
        page = self.browser.open(site)
        try:
            page.goto(site.check_url)
            if page.has(site.logged_in_selector):
                return "ok"
            credentials = self.credentials.get(name)
            if credentials is None:
                raise AuthBlocked("no_credentials")
            page.goto(site.login_url)
            page.fill(site.user_selector, credentials[0])
            page.fill(site.password_selector, credentials[1])
            page.click(site.submit_selector)
            return self._await_result(site, page)
        finally:
            self.browser.close()

    def _await_result(self, site: Site, page) -> str:
        told = False
        remaining = self.attempts
        while remaining > 0:
            if page.has(site.logged_in_selector):
                self.browser.save(site)
                return "ok"
            if page.has(site.captcha_selector):
                raise AuthBlocked("captcha")
            if page.has(site.second_factor_selector) and not told:
                told = True
                remaining = self.second_factor_attempts
                self.notify(f"{site.name} 로그인 인증을 휴대폰에서 승인해 주세요.")
            remaining -= 1
            self.wait()
            page.poll()
        raise AuthBlocked("second_factor_timeout" if told else "login_failed")


class PlaywrightBrowser:
    """Real browser. Sessions are saved per site, and each call uses a new context."""

    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)

    def _state(self, site: Site) -> Path:
        return self.state_dir / f"{site.name}.json"

    def open(self, site: Site):
        from playwright.sync_api import sync_playwright

        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.playwright = sync_playwright().start()
        self.instance = self.playwright.chromium.launch(headless=True)
        state = self._state(site)
        self.context = self.instance.new_context(
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            storage_state=str(state) if state.exists() else None,
        )
        return PlaywrightPage(self.context.new_page())

    def save(self, site: Site) -> None:
        self.context.storage_state(path=str(self._state(site)))
        self._state(site).chmod(0o600)

    def close(self) -> None:
        self.instance.close()
        self.playwright.stop()


class PlaywrightPage:
    def __init__(self, page):
        self.page = page

    def goto(self, url: str) -> None:
        self.page.goto(url, wait_until="domcontentloaded", timeout=30000)

    def fill(self, selector: str, value: str) -> None:
        self.page.fill(selector, value, timeout=15000)

    def click(self, selector: str) -> None:
        self.page.click(selector, timeout=15000)

    def has(self, selector: str) -> bool:
        return self.page.locator(selector).count() > 0

    def poll(self) -> None:
        self.page.wait_for_timeout(500)

    def compose_mail(self, to: str, subject: str, body: str) -> None:
        self.page.locator("#recipient_input_element, input[aria-label*='받는']").first.fill(to, timeout=20000)
        self.page.keyboard.press("Enter")
        self.page.locator("#subject_title, input[aria-label*='제목']").first.fill(subject, timeout=20000)
        self.page.frame_locator("iframe[title*='편집'], iframe.se2_input_wysiwyg").first.locator("body").fill(body, timeout=20000)
        self.page.get_by_role("button", name="보내기").first.click(timeout=20000)
        self.page.wait_for_timeout(3000)

    def send_message(self, selector: str, text: str) -> None:
        self.page.click(selector, timeout=20000)
        self.page.keyboard.type(text)
        self.page.keyboard.press("Enter")
        self.page.wait_for_timeout(2000)


def build_auth_tool(gate: AuthGate) -> Tool:
    def run(args: dict) -> str:
        try:
            return json.dumps({"status": gate.ensure(args["site"])})
        except AuthBlocked as error:
            return json.dumps({"status": str(error)})

    return Tool(
        "auth_ensure",
        "사이트 로그인 상태를 확인하고 끊겼으면 다시 로그인한다. 비밀번호는 코드가 처리하므로 사용자에게 묻거나 받지 않는다. 결과는 상태만 돌려준다.",
        {"type": "object", "properties": {"site": {"type": "string", "enum": list(SITES)}}, "required": ["site"]},
        run,
    )
