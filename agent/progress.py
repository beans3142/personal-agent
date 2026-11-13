"""One Discord message that shows a request's steps and becomes its answer.

Tool runners append one line per tool call to `<outbox>/progress.jsonl`. Only
the tool name (and the app name for android_open) is written, so a channel
never sees message text, recipients or other arguments.
"""
import asyncio
import json
import logging
from pathlib import Path
import time


log = logging.getLogger(__name__)
LABELS = {
    "android_screen": "화면 보는 중", "android_open": "앱 여는 중", "android_click": "버튼 누르는 중",
    "android_input": "글자 입력하는 중", "android_submit": "검색하는 중", "android_swipe": "화면 넘기는 중",
    "android_key": "이전 화면으로 가는 중", "android_credential": "로그인 정보 입력하는 중",
    "web_search": "웹 검색하는 중", "web_fetch": "웹 페이지 읽는 중", "map_search": "장소 찾는 중", "route_search": "경로 찾는 중",
    "mail_list": "메일 확인하는 중", "mail_read": "메일 읽는 중", "mail_send": "메일 보내는 중",
    "memory_search": "기억 찾는 중", "memory_save": "기억 저장하는 중", "memory_forget": "기억 지우는 중",
    "schedule_list": "일정 확인하는 중", "schedule_add": "일정 추가하는 중", "schedule_remove": "일정 지우는 중", "schedule_repeat": "반복 일정 설정하는 중",
    "kakao_find_recipient": "카톡 받는 사람 찾는 중", "kakao_send": "카톡 보내는 중", "kakao_send_image": "카톡으로 사진 보내는 중", "image_generate": "이미지 만드는 중", "kakao_self_send": "나에게 카톡 보내는 중",
    "n8n_list": "자동화 목록 보는 중", "n8n_run": "자동화 실행하는 중", "device_nodes": "기기 확인하는 중", "device_jobs": "기기 작업 확인하는 중",
    "device_enqueue": "기기에 작업 보내는 중", "auth_ensure": "로그인 확인하는 중", "owner_control": "기기 제어하는 중",
    "kb_search": "자료 찾는 중", "conversation_search": "이전 대화 찾는 중", "conversation_history": "이전 대화 읽는 중",
    "plan_execute": "여러 단계 실행하는 중", "recipe_list": "작업 순서 보는 중", "recipe_run": "작업 순서 실행하는 중",
    "career_check": "공고 확인하는 중", "career_fit": "공고 적합도 보는 중", "career_story": "자기소개 소재 정리하는 중",
    "discord_post_as_me": "Discord 글 올리는 중", "baemin_search": "배민에서 가게 메뉴 모으는 중",
}
APP_NAMES = {"baemin": "배민", "kakao": "카톡", "coupang": "쿠팡"}
LABELS.update({
    'pc_list': 'PC 연결 확인하는 중', 'pc_screenshot': 'PC 화면 캡처하는 중',
    'pc_windows': '열린 창 확인하는 중', 'pc_ui': 'PC 화면 요소 읽는 중',
    'pc_act': 'PC 조작하는 중', 'pc_open': 'PC에서 파일·프로그램 여는 중',
    'pc_find_files': '파일 이름 검색하는 중', 'pc_find_content': '문서 내용 검색하는 중',
    'pc_send_file': 'PC 파일 전달 준비하는 중', 'pc_drive_upload': 'PC 파일을 구글 드라이브에 올리는 중',
    'phone_alarm': '폰 알람 맞추는 중', 'device_accept': '새 기기 연결 수락하는 중', 'device_deny': '새 기기 연결 거절하는 중', 'device_requests': '연결 요청 확인하는 중', 'calendar_list': '구글 캘린더 확인하는 중', 'calendar_add': '구글 캘린더에 일정 넣는 중', 'pc_save_file': 'PC에 파일 저장하는 중',
    'pc_system': 'PC 시스템 작업하는 중', 'pc_shell': 'PC 명령 실행하는 중',
    'run_code': '코드 실행하는 중', 'routine_list': '반복 작업 목록 확인하는 중',
    'routine_register': '반복 작업 등록하는 중', 'routine_update': '반복 작업 수정하는 중',
    'routine_delete': '반복 작업 삭제하는 중', 'routine_run': '반복 작업 실행하는 중',
})


def _file(outbox):
    return Path(outbox) / "progress.jsonl"


def record(outbox, tool, args):
    if not outbox:
        return
    step = {"tool": str(tool).removeprefix("mcp__daevis__")}
    if step["tool"] == "android_open" and isinstance(args.get("app"), str):
        step["app"] = args["app"][:30]
    try:
        path = _file(outbox)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(step, ensure_ascii=False) + "\n")
    except OSError:
        pass  # Progress is a convenience; it never blocks the tool itself.


def read_steps(outbox):
    try:
        lines = _file(outbox).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    steps = []
    for line in lines:
        try:
            steps.append(json.loads(line))
        except ValueError:
            continue  # A line still being written is read on the next refresh.
    return steps


def render(steps):
    """One line about what is happening now, like a typing indicator."""
    if not steps:
        return "⏳ 요청을 이해하는 중…"
    current = steps[-1]
    text = "⏳ " + LABELS.get(current.get("tool"), "작업하는 중") + "…"
    app = next((s.get("app") for s in reversed(steps) if s.get("app")), None)
    if app and str(current.get("tool", "")).startswith("android_"):
        text += " · " + APP_NAMES.get(app, app)
    return text


def _quiet():
    import discord
    return discord.AllowedMentions.none()


class ProgressMessage:
    def __init__(self, channel, outbox, clock=time.monotonic):
        self.channel, self.outbox, self.clock = channel, outbox, clock
        self.started = clock()
        self.message, self.shown = None, None

    async def start(self):
        self.shown = render([])
        try:
            self.message = await self.channel.send(self.shown, allowed_mentions=_quiet())
        except Exception:
            log.warning("progress message could not be sent; answer will be sent normally")

    async def refresh(self):
        if self.message is None:
            return
        steps = await asyncio.to_thread(read_steps, self.outbox)
        text = render(steps)
        if text == self.shown:
            return
        try:
            await self.message.edit(content=text, allowed_mentions=_quiet())
            self.shown = text
        except Exception:
            log.warning("progress edit failed")

    async def run(self, interval=2.0):
        while True:
            await asyncio.sleep(interval)
            await self.refresh()

    async def finish(self, reply, fallback):
        if self.message is None:
            return await fallback(self.channel, reply)
        from .scheduler import split_text
        parts = split_text(reply or "답변이 비어 있어요.")
        try:
            await self.message.edit(content=parts[0], allowed_mentions=_quiet())
        except Exception:
            log.warning("final edit failed; sending answer as new messages")
            await self.discard()
            return await fallback(self.channel, reply)
        sent = [self.message]
        for part in parts[1:]:
            sent.append(await self.channel.send(part, allowed_mentions=_quiet()))
        return sent

    async def discard(self):
        if self.message is None:
            return
        try:
            await self.message.delete()
        except Exception:
            pass
        self.message = None
