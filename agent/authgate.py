"""Keeps site logins alive. Passwords come from the environment and never reach the model.

Optional owner-approved image assistance is bounded; unsupported verification
and phone/OTP steps remain blocked or handed to the owner.
"""

from __future__ import annotations

import json
import re
import time
import base64
from dataclasses import dataclass
from pathlib import Path

from .loop import Tool

LOGIN_ATTEMPTS = 6
SECOND_FACTOR_ATTEMPTS = 60
POLL_SECONDS = 5


class AuthBlocked(Exception):
    pass


def verification_image(marker):
    """Copy the already loaded image at native resolution, without a new GET.

    Fetching a captcha URL again can generate a different problem. Canvas copies
    the existing pixels only; cross-origin/unsupported elements use a screenshot.
    """
    if hasattr(marker, 'evaluate'):
        try:
            encoded = marker.evaluate('''img => {
                if (!(img instanceof HTMLImageElement) || !img.complete || !img.naturalWidth ||
                    img.naturalWidth * img.naturalHeight > 4000000) return null;
                const canvas = document.createElement('canvas');
                canvas.width = img.naturalWidth; canvas.height = img.naturalHeight;
                canvas.getContext('2d').drawImage(img, 0, 0);
                return canvas.toDataURL('image/png');
            }''')
            if isinstance(encoded, str) and encoded.startswith('data:image/png;base64,') and len(encoded) <= 2800000:
                raw = base64.b64decode(encoded.split(',', 1)[1], validate=True)
                if raw.startswith(b'\x89PNG\r\n\x1a\n') and len(raw) <= 2 * 1024 * 1024:
                    return raw
        except Exception:
            pass
    return marker.screenshot()


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
    user_next_selector: str | None = None
    remember_selector: str | None = None


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
        check_url="https://mail.naver.com/v2/folders/0",
        user_selector="#id",
        password_selector="#pw",
        submit_selector="button[id^='loginBtn']:visible",
        logged_in_selector="naver:mailbox",
        captcha_selector="#captcha:visible, #chptcha:visible, iframe[id^='ncaptcha-iframe-']:visible",
        second_factor_selector="#push_title:visible, #otp:visible",
        remember_selector="#loginStay",
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
        remember_selector="input[name='saveSignedIn']",
    ),
    "google": Site(
        name="google",
        login_url="https://accounts.google.com/v3/signin/identifier?continue=https%3A%2F%2Fmyaccount.google.com%2F&flowName=GlifWebSignIn",
        check_url="https://myaccount.google.com/",
        user_selector="#identifierId",
        password_selector="input[name='Passwd']",
        submit_selector="#passwordNext button, #passwordNext",
        logged_in_selector="a[href*='SignOutOptions'], a[href*='accounts.google.com/SignOut']",
        captcha_selector="#captchaimg, iframe[src*='recaptcha']",
        second_factor_selector="#totpPin, [data-challengetype]",
        user_next_selector="#identifierNext button, #identifierNext",
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
                 second_factor_attempts: int = SECOND_FACTOR_ATTEMPTS, notify=lambda text: None,
                 challenge_handler=None, can_login=lambda site: True, record_outcome=lambda site, status: None):
        self.second_factor_attempts = second_factor_attempts
        self.notify = notify
        self.credentials = credentials
        self.browser = browser
        self.sites = sites
        self.wait = wait
        self.attempts = attempts
        self.challenge_handler = challenge_handler
        self.can_login = can_login
        self.record_outcome = record_outcome

    def ensure(self, name: str) -> str:
        """Returns "ok" when the site session is usable. Raises AuthBlocked with a status otherwise."""
        site = self.sites.get(name)
        if site is None:
            raise AuthBlocked("unknown_site")
        page = self.browser.open(site)
        try:
            page.goto(site.check_url)
            self._check_known_block(site, page)
            if page.has(site.logged_in_selector):
                self.browser.save(site)
                self.record_outcome(site.name, 'ok')
                return "ok"
            if not self.can_login(site.name):
                raise AuthBlocked('login_paused')
            credentials = self.credentials.get(name)
            if credentials is None:
                raise AuthBlocked("no_credentials")
            page.goto(site.login_url)
            page.fill(site.user_selector, credentials[0])
            if site.user_next_selector:
                page.click(site.user_next_selector)
            page.fill(site.password_selector, credentials[1])
            if site.remember_selector:
                page.check(site.remember_selector)
            page.click(site.submit_selector)
            status = self._await_result(site, page)
            self.record_outcome(site.name, status)
            return status
        except AuthBlocked as error:
            if str(error) != 'login_paused':
                self.record_outcome(site.name, str(error))
            raise
        except Exception:
            self.record_outcome(site.name, 'runtime_error')
            raise
        finally:
            self.browser.close()

    def refresh(self, name: str) -> str:
        """Extends a saved session. Never uses a password."""
        site = self.sites[name]
        if not self.browser.has_session(site):
            return "no_session"
        page = self.browser.open(site)
        try:
            page.goto(site.check_url)
            if not page.has(site.logged_in_selector):
                return "expired"
            self.browser.save(site)
            return "ok"
        finally:
            self.browser.close()

    def refresh_all(self) -> dict[str, str]:
        results = {}
        for name in self.sites:
            try:
                results[name] = self.refresh(name)
            except Exception as error:
                results[name] = f"error: {error}"
        return results

    def _await_result(self, site: Site, page) -> str:
        told = False
        challenge_attempted = False
        resume_naver_login = False
        mail_check_attempted = False
        remaining = self.attempts
        while remaining > 0:
            self._check_known_block(site, page)
            if site.name == 'naver' and not mail_check_attempted and page.has('cookie:NID_AUT'):
                mail_check_attempted = True
                page.goto(site.check_url)
            if page.has(site.logged_in_selector):
                page.goto(site.check_url)
                if not page.has(site.logged_in_selector):
                    raise AuthBlocked('session_not_verified')
                self.browser.save(site)
                return "ok"
            if page.has(site.captcha_selector):
                if self.challenge_handler is not None and not challenge_attempted:
                    challenge_attempted = True
                    if self.challenge_handler(site, page):
                        resume_naver_login = site.name == 'naver'
                        page.poll()
                        continue
                raise AuthBlocked("captcha")
            if resume_naver_login and page.has(site.user_selector + ':visible') and page.has(site.password_selector + ':visible'):
                # Naver's separate verification page returns to an empty sign-in
                # form. Resume once; waiting alone never authenticates it.
                resume_naver_login = False
                credentials = self.credentials.get(site.name)
                if credentials is None:
                    raise AuthBlocked('no_credentials')
                page.fill(site.user_selector, credentials[0])
                page.fill(site.password_selector, credentials[1])
                if site.remember_selector:
                    page.check(site.remember_selector)
                page.click(site.submit_selector)
                mail_check_attempted = False
                page.poll()
                continue
            if page.has(site.second_factor_selector) and not told:
                told = True
                remaining = self.second_factor_attempts
                self.notify(f"{site.name} 로그인 인증을 휴대폰에서 승인해 주세요.")
            remaining -= 1
            self.wait()
            page.poll()
        raise AuthBlocked("second_factor_timeout" if told else "login_failed")

    def _check_known_block(self, site, page):
        detect = getattr(page, 'auth_block_reason', None)
        reason = detect(site.name) if detect else None
        if reason:
            self.notify(f'{site.name}: {reason} — 계정 보호조치 해제에 소유자 본인 확인이 필요합니다. 자동 재시도하지 않습니다.')
            raise AuthBlocked(reason)


class PlaywrightBrowser:
    """Real browser. Sessions are saved per site, and each call uses a new context."""

    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)

    def _state(self, site: Site) -> Path:
        return self.state_dir / f"{site.name}.json"

    def has_session(self, site: Site) -> bool:
        return self._state(site).exists()

    def open(self, site: Site):
        from playwright.sync_api import sync_playwright

        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.playwright = sync_playwright().start()
        state = self._state(site)
        if site.name == 'naver':
            # Preserve the complete site profile, including browser-bound
            # storage not covered by a cookies/localStorage JSON snapshot.
            profile = self.state_dir / 'naver-browser'
            profile.mkdir(mode=0o700, exist_ok=True)
            profile.chmod(0o700)
            self.context = self.playwright.chromium.launch_persistent_context(
                str(profile), headless=True,
                locale='ko-KR', timezone_id='Asia/Seoul')
            self.instance = self.context
        else:
            self.instance = self.playwright.chromium.launch(headless=True)
            self.context = self.instance.new_context(
                locale="ko-KR", timezone_id="Asia/Seoul",
                storage_state=str(state) if state.exists() else None)
        return PlaywrightPage(self.context.pages[0] if self.context.pages else self.context.new_page())

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

    def check(self, selector: str) -> None:
        self.page.locator(selector).first.check(force=True, timeout=5000)

    def has(self, selector: str) -> bool:
        if selector == 'naver:mailbox':
            from urllib.parse import urlsplit
            url = urlsplit(self.page.url)
            if url.hostname != 'mail.naver.com' or not url.path.startswith('/v2/'):
                return False
            cookies = {c['name'] for c in self.page.context.cookies()}
            if not {'NID_AUT', 'NID_SES'} <= cookies:
                return False
            marker = self.page.get_by_text(re.compile(r'^메일\s*쓰기$'))
            try:
                marker.first.wait_for(state='visible', timeout=10000)
            except Exception:
                return False
            return urlsplit(self.page.url).hostname == 'mail.naver.com' and bool(marker.count())
        if selector.startswith("cookie:"):
            name = selector.split(":", 1)[1]
            if name == 'NID_AUT' and 'nid.naver.com/nidlogin' in self.page.url:
                return False
            return any(cookie["name"] == name for cookie in self.page.context.cookies())
        return self.page.locator(selector).count() > 0

    def screenshot(self, path) -> None:
        self.page.wait_for_timeout(2500)
        self.page.screenshot(path=str(path))

    def poll(self) -> None:
        self.page.wait_for_timeout(500)

    def auth_block_reason(self, site):
        from urllib.parse import urlsplit
        url = urlsplit(self.page.url)
        if site == 'naver' and url.hostname == 'nid.naver.com' and url.path == '/user2/help/idSafetyRelease':
            return 'account_protected'
        return None

    def login_challenge(self, site):
        """Naver-only crop. Never capture the enclosing credential form."""
        import hashlib
        from urllib.parse import urlsplit
        if site != 'naver' or urlsplit(self.page.url).hostname != 'nid.naver.com':
            raise AuthBlocked('unsupported_verification')
        iframe = self.page.locator('iframe[id^="ncaptcha-iframe-"]:visible')
        if iframe.count() == 1:
            source = iframe.get_attribute('src') or ''
            host = urlsplit(source).hostname or ''
            if not host.endswith('.naver.com'):
                raise AuthBlocked('unsupported_verification_origin')
            frame = iframe.element_handle().content_frame()
            if frame is None or frame.locator('input[type=password]').count():
                raise AuthBlocked('unsafe_verification_crop')
            target = frame.get_by_role('textbox')
            submit = frame.get_by_role('button', name=re.compile(r'^(Confirm|확인)$', re.I))
            target.first.wait_for(state='visible', timeout=10000)
            submit.first.wait_for(state='visible', timeout=10000)
            if target.count() != 1 or submit.count() != 1:
                raise AuthBlocked('unsupported_verification_controls')
            question = frame.locator('body').inner_text()[:1000]
            image = iframe.screenshot()
            marker = iframe
        else:
            marker = self.page.locator('#captchaimg:visible')
            if marker.count() != 1:
                raise AuthBlocked('unsupported_verification')
            source = marker.get_attribute('src') or ''
            question_node = self.page.locator('#captcha_info:visible')
            question = question_node.inner_text()[:1000] if question_node.count() else 'Read the verification characters.'
            image = verification_image(marker)
            target = self.page.locator('#captcha:visible, #chptcha:visible')
            submit = self.page.locator("button[id^='loginBtn']:visible")
            if submit.count() == 0:
                submit = self.page.get_by_role('button', name=re.compile(r'^(Confirm|확인)$', re.I))
            if target.count() != 1 or submit.count() != 1:
                raise AuthBlocked('unsupported_verification_controls')
        key = hashlib.sha256(source.encode()).hexdigest()
        self._login_challenge = (key, marker, source, target, submit)
        return {'image': image, 'question': question, 'key': key}

    def submit_login_challenge(self, key, answer):
        saved, marker, source, target, submit = self._login_challenge
        if saved != key or marker.get_attribute('src') != source:
            return False
        target.fill(answer)
        submit.click()
        marker.wait_for(state='hidden', timeout=10000)
        return True

    def compose_mail(self, to: str, subject: str, body: str) -> None:
        if "nid.naver.com" in self.page.url:
            raise AuthBlocked("mail_session_expired")
        self.page.wait_for_selector("#recipient_input_element", timeout=20000)
        self.page.fill("#recipient_input_element", to)
        self.page.keyboard.press("Enter")
        self.page.fill("#subject_title", subject)
        editor = None
        for _ in range(20):
            for frame in self.page.frames:
                if frame.locator(".workseditor-content").count():
                    editor = frame.locator(".workseditor-content").first
            if editor:
                break
            self.page.wait_for_timeout(500)
        if editor is None:
            raise RuntimeError("mail editor not found")
        editor.click()
        self.page.keyboard.type(body)
        self.page.locator(".button_write_task").first.click()
        self.page.wait_for_timeout(5000)
        if "/v2/new" in self.page.url and "보냈" not in self.page.locator("body").inner_text():
            raise RuntimeError("mail was not confirmed as sent")

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
