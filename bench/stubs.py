"""Production tool schemas with fake results, so experiments never send messages or touch accounts."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from agent.android_control import build_android_tools
from agent.authgate import build_auth_tool
from agent.discord_user import build_discord_user_tool
from agent.loop import Tool
from agent.memory import Memory
from agent.naver_mail import build_mail_tool
from agent.tools import build_tools


class _Anything:
    """Stands in for devices, gates and browsers. Only tool schemas are taken from production code."""

    def __getattr__(self, name):
        return lambda *args, **kwargs: {}


UI = {
    "snapshot": "s1",
    "package": "com.kakao.talk",
    "nodes": [
        {"id": 0, "text": "나와의 채팅", "clickable": True},
        {"id": 1, "text": "", "class": "android.widget.EditText", "focused": True, "description": "메시지 입력"},
        {"id": 2, "text": "", "description": "전송", "clickable": True},
    ],
}

FAKE = {
    "memory_save": lambda a: "saved 1",
    "memory_search": lambda a: "[]",
    "memory_forget": lambda a: "True",
    "schedule_add": lambda a: json.dumps({"id": "j1", **a}, ensure_ascii=False),
    "schedule_repeat": lambda a: json.dumps({"id": "j2", **a}, ensure_ascii=False),
    "schedule_list": lambda a: json.dumps([{"id": "j0", "kind": "message", "text": "회의", "next_at": "2026-11-05T09:00:00+09:00"}], ensure_ascii=False),
    "schedule_remove": lambda a: "True",
    "web_search": lambda a: json.dumps([{"title": "검색 결과 1", "url": "https://example.com/1"}, {"title": "검색 결과 2", "url": "https://example.com/2"}], ensure_ascii=False),
    "web_fetch": lambda a: "페이지 본문 요약용 예시 글입니다.",
    "mail_list": lambda a: json.dumps([{"uid": "11", "subject": "회의 안내", "from": "팀장 <a@example.com>"}, {"uid": "10", "subject": "청구서", "from": "카드사"}], ensure_ascii=False),
    "mail_read": lambda a: json.dumps({"subject": "회의 안내", "body": "내일 오전 10시에 3층 회의실에서 주간 회의가 있습니다."}, ensure_ascii=False),
    "route_search": lambda a: json.dumps({"source": "네이버 지도", "routes": [{"summary": "1시간 4분 · 경의중앙선→수인분당선 · 1,950원", "depart": "11:10", "arrive": "12:14"}]}, ensure_ascii=False),
    "map_search": lambda a: json.dumps([{"name": "카페 하나", "address": "서울 강남대로 1"}, {"name": "카페 둘", "address": "서울 강남대로 2"}], ensure_ascii=False),
    "android_open": lambda a: json.dumps({"app": a.get("app"), "opened": "app"}),
    "android_screen": lambda a: json.dumps(UI, ensure_ascii=False),
    "android_click": lambda a: json.dumps({"clicked": a.get("index"), "ok": True}),
    "android_input": lambda a: json.dumps({"entered": True}),
    "android_credential": lambda a: json.dumps({"entered": True}),
    "android_submit": lambda a: json.dumps({"submitted": True}),
    "android_swipe": lambda a: json.dumps({"ok": True}),
    "android_key": lambda a: json.dumps({"ok": True}),
    "mail_send": lambda a: "sent",
    "discord_post_as_me": lambda a: "sent as owner",
    "auth_ensure": lambda a: json.dumps({"status": "ok"}),
}


def production_tools() -> list[Tool]:
    folder = Path(tempfile.mkdtemp())
    any_ = _Anything()
    return (build_tools(Memory(folder / "bench.db"), folder / "schedule.json", env={})
            + build_android_tools(any_)
            + [build_auth_tool(any_), build_discord_user_tool(any_, any_, "token"), build_mail_tool(any_, any_, {})])


def stubbed(tools: list[Tool], log: list) -> list[Tool]:
    def fake(tool):
        def run(args):
            log.append((tool.name, args))
            return FAKE[tool.name](args)
        return Tool(tool.name, tool.description, tool.parameters, run, False)
    return [fake(tool) for tool in tools]
