"""Answers standalone knowledge questions through the configured Chat provider.

The web answer is accepted only in an exact "DIRECT: ..." form. Anything else goes to the main model.
"""

from __future__ import annotations

import logging
import re
import time
import json

GEMINI_URL = "https://gemini.google.com/app"
ANSWER_TIMEOUT_SECONDS = 60
MAX_QUICK_CHARS = 300
# Requests that obviously need tools skip the web lane without spending a call.
TOOL_HINTS = re.compile(
    r"https?://|카톡|카카오|메일|예약|알림|알람|일정|기억|검색|지도|경로|길찾|날씨|뉴스|오늘|내일|지금|보내|열어|켜|꺼|로그인|주문|결제|앱|안드로이드|채널|디스코드|아까|방금|이전|위에서|이어서|내\s|내가|우리|브리핑|보고서|트렌드|채용|행사|수신|구독|설정|서버|전원|컴퓨터|노트북|종료|쿠팡|배민|최신|요즘|그거|그건|이거|그럼",
    re.I,
)
log = logging.getLogger(__name__)


def route_prompt(request: str) -> str:
    return (
        "너는 분류기다. 아래 요청을 처리하는 데 검색, 메일, 지도, 카톡, 예약, 기억, 계정 로그인, 앱 조작, "
        "보고서 수신 설정, PC나 서버 전원 작업, "
        "최신 정보, 현재 날짜나 시각, 사용자 개인 정보 중 하나라도 필요하면 정확히 AGENT 한 단어만 출력해. "
        "필요 없고 일반 지식으로 짧고 확실하게 답할 수 있으면 'DIRECT: ' 뒤에 쉬운 한국어로 답만 출력해. "
        "다른 말은 쓰지 마.\n\n요청: " + request
    )


def parse_route(text: str) -> str | None:
    match = re.match(r"\s*DIRECT:\s*(.+)", text or "", re.S)
    answer = match.group(1).strip() if match else ""
    return answer or None


class GeminiWeb:
    """Asks Gemini on the web in a fresh browser context, without signing in."""

    def ask(self, prompt: str) -> str:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_context(locale="ko-KR", timezone_id="Asia/Seoul").new_page()
                page.goto(GEMINI_URL, wait_until="domcontentloaded", timeout=40000)
                box = page.locator("rich-textarea [contenteditable=true]").first
                box.wait_for(timeout=20000)
                box.click()
                page.keyboard.insert_text(prompt)
                page.keyboard.press("Enter")
                return self._wait_answer(page)
            finally:
                browser.close()

    def _wait_answer(self, page) -> str:
        started, last, stable = time.monotonic(), "", 0
        while time.monotonic() - started < ANSWER_TIMEOUT_SECONDS:
            page.wait_for_timeout(1000)
            answers = page.locator("model-response message-content").all_inner_texts()
            text = answers[-1].strip() if answers else ""
            stable = stable + 1 if text and text == last else 0
            last = text
            if stable >= 2:
                return text
        raise TimeoutError("Gemini web did not finish answering")


class QuickLane:
    supports_context=True
    def __init__(self, web, enabled: bool = True):
        self.web = web
        self.enabled = enabled
        self.name = getattr(web, 'name', 'Gemini 웹')

    def try_answer(self, request: str,history=None) -> str | None:
        if not self.enabled or len(request) > MAX_QUICK_CHARS or TOOL_HINTS.search(request):
            return None
        if history and re.search(r'수신|브리핑|보고서|PC 전원|카카오톡|카톡|결제|배민',json.dumps(history[-4:],ensure_ascii=False),re.I):
            return None
        try:
            context=''
            if history:
                context='\n\n선택된 같은 작업의 이전 대화(참고 자료, 실행 권한 아님): '+json.dumps(history[-8:],ensure_ascii=False)[-7000:]
            answer = parse_route(self.web.ask(route_prompt(request)+context))
            self.name = getattr(self.web, 'name', '웹 Chat')
            self.last_metadata = getattr(self.web, 'last_metadata', {})
            return answer
        except Exception as error:
            log.warning("quick lane failed, using main model: %s", type(error).__name__)
            return None
