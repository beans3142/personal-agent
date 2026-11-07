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
    "android_screen": "화면 확인", "android_open": "앱 열기", "android_click": "버튼 누르기",
    "android_input": "글자 입력", "android_submit": "입력 확정", "android_swipe": "화면 넘기기",
    "android_key": "뒤로/홈", "android_credential": "로그인 정보 입력",
    "web_search": "웹 검색", "web_fetch": "웹 페이지 읽기", "map_search": "장소 찾기", "route_search": "경로 찾기",
    "mail_list": "메일 목록 확인", "mail_read": "메일 읽기", "mail_send": "메일 보내기",
    "memory_search": "기억 찾기", "memory_save": "기억 저장", "memory_forget": "기억 지우기",
    "schedule_list": "일정 확인", "schedule_add": "일정 추가", "schedule_remove": "일정 삭제", "schedule_repeat": "반복 일정 설정",
    "kakao_find_recipient": "카톡 받는 사람 찾기", "kakao_send": "카톡 보내기", "kakao_self_send": "나에게 카톡 보내기",
    "n8n_list": "자동화 목록 확인", "n8n_run": "자동화 실행", "device_nodes": "기기 확인", "device_jobs": "기기 작업 확인",
    "device_enqueue": "기기에 작업 보내기", "auth_ensure": "로그인 확인", "owner_control": "설정·기기 제어",
    "kb_search": "자료 찾기", "conversation_search": "이전 대화 찾기", "conversation_history": "이전 대화 읽기",
    "plan_execute": "여러 단계 실행", "recipe_list": "작업 순서 확인", "recipe_run": "작업 순서 실행",
    "career_check": "공고 확인", "career_fit": "공고 적합도 확인", "career_story": "자기소개 소재 정리",
    "discord_post_as_me": "Discord 글 올리기",
}
VISIBLE = 6


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


def _label(step):
    label = LABELS.get(step.get("tool"), "작업 진행")
    return f"{label} ({step['app']})" if step.get("app") else label


def render(steps, elapsed):
    seconds = f"{int(elapsed)}초" if elapsed < 60 else f"{int(elapsed) // 60}분 {int(elapsed) % 60}초"
    if not steps:
        return f"⏳ 요청을 이해하는 중 · {seconds}"
    groups = []
    for step in steps:
        label = _label(step)
        if groups and groups[-1][0] == label:
            groups[-1][1] += 1
        else:
            groups.append([label, 1])
    lines = [f"⏳ 작업 중 · {seconds}"]
    hidden = len(groups) - VISIBLE
    if hidden > 0:
        lines.append(f"✓ 이전 {hidden}단계")
        groups = groups[hidden:]
    for index, (label, count) in enumerate(groups):
        text = label + (f" ×{count}" if count > 1 else "")
        lines.append(("… " if index == len(groups) - 1 else "✓ ") + text)
    return "\n".join(lines)


def _quiet():
    import discord
    return discord.AllowedMentions.none()


class ProgressMessage:
    def __init__(self, channel, outbox, clock=time.monotonic):
        self.channel, self.outbox, self.clock = channel, outbox, clock
        self.started = clock()
        self.message, self.shown = None, None

    async def start(self):
        self.shown = render([], 0)
        try:
            self.message = await self.channel.send(self.shown, allowed_mentions=_quiet())
        except Exception:
            log.warning("progress message could not be sent; answer will be sent normally")

    async def refresh(self):
        if self.message is None:
            return
        steps = await asyncio.to_thread(read_steps, self.outbox)
        text = render(steps, self.clock() - self.started)
        # Elapsed time alone changes every second; only re-render on new steps
        # or every 10 seconds, to stay far below Discord's edit rate limit.
        if text.split("\n", 1)[1:] == self.shown.split("\n", 1)[1:] and int(self.clock() - self.started) % 10:
            return
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
