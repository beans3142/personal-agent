"""Offline measurements: no model is called. Token counts use the o200k_base tokenizer as an estimate."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

TOOL_GROUPS = {
    "schedule": ("schedule_add", "schedule_repeat", "schedule_list", "schedule_remove"),
    "memory": ("memory_save", "memory_search", "memory_forget"),
    "route": ("route_search",),
    "map": ("map_search",),
    "mail": ("mail_list", "mail_read", "mail_send"),
    "web": ("web_search", "web_fetch"),
    "android": ("android_open", "android_screen", "android_click", "android_input", "android_credential",
                "android_submit", "android_swipe", "android_key"),
}
GROUP_WORDS = {
    # "알려줘" alone is too common ("방법 알려줘"); a time or reminder word is required.
    "schedule": r"알림|알람|예약|\d+\s*(분|시간)\s*(뒤|전)|일정",
    "memory": r"기억",
    "route": r"경로|가는\s*길|에서\s*\S+역|길찾",
    "map": r"근처|맛집|카페|찾아",
    "mail": r"메일",
    "web": r"검색|날씨|웹",
    "android": r"카톡|카카오|배민|앱|열어",
}


def select_tools(text: str) -> set[str]:
    """Code picks tool groups from words in the request. No match means no tools are sent."""
    chosen = set()
    for group, pattern in GROUP_WORDS.items():
        if re.search(pattern, text):
            chosen.update(TOOL_GROUPS[group])
    return chosen


@dataclass
class Routed:
    tool: str
    args: dict


ROUTES = [
    (re.compile(r"^(\d+)\s*(분|시간)\s*뒤에?\s*(.+?)(?:라고|하라고)\s*알려\s*줘$"), "schedule_add"),
    (re.compile(r"^예약\s*목록\s*(?:보여\s*줘)?$"), "schedule_list"),
    (re.compile(r"^(.+?)(?:라고|이라고)\s*기억해(?:\s*줘)?$"), "memory_save"),
    (re.compile(r"^(\S+?역)에서\s*(\S+?역)\s*가는\s*(?:경로|길)(?:\s*알려\s*줘)?$"), "route_search"),
    (re.compile(r"^(\S+)\s*근처\s*(\S+)\s*찾아\s*줘$"), "map_search"),
    (re.compile(r"^(배민|카톡)\s*열어\s*줘$"), "android_open"),
]


def route(text: str) -> Routed | None:
    """Fixed sentence shapes run a tool directly with values cut out by the pattern."""
    for pattern, tool in ROUTES:
        match = pattern.match(text.strip())
        if not match:
            continue
        groups = match.groups()
        if tool == "schedule_add":
            amount, unit, what = groups
            return Routed(tool, {"kind": "message", "text": what.strip(), "in_minutes": int(amount) * (60 if unit == "시간" else 1)})
        if tool == "schedule_list":
            return Routed(tool, {})
        if tool == "memory_save":
            return Routed(tool, {"text": groups[0].strip()})
        if tool == "route_search":
            return Routed(tool, {"origin": groups[0], "destination": groups[1]})
        if tool == "map_search":
            return Routed(tool, {"query": f"{groups[0]} {groups[1]}"})
        if tool == "android_open":
            return Routed(tool, {"app": {"배민": "baemin", "카톡": "kakao"}[groups[0]]})
    return None


def normalize(text: str) -> set[str]:
    words = re.sub(r"[^\w가-힣]", " ", text).split()
    stems = set()
    for word in words:
        stem = re.sub(r"(이야|이에요|예요|이지|인지|이란|란|은|는|이|가|을|를|에|로|으로|좀|줘|알려|뭐야|뭔지)$", "", word)
        if stem:
            stems.add(stem)
    return stems


class AnswerCache:
    """Reuses an answer when a new simple question shares most of its words with a cached one."""

    def __init__(self, threshold: float = 0.6):
        self.threshold = threshold
        self.entries: list[tuple[set[str], str]] = []

    def get(self, text: str) -> str | None:
        words = normalize(text)
        for cached, answer in self.entries:
            union = words | cached
            if union and len(words & cached) / len(union) >= self.threshold:
                return answer
        return None

    def put(self, text: str, answer: str) -> None:
        self.entries.append((normalize(text), answer))


def schema_tokens(tools, names: set[str] | None, encode) -> int:
    chosen = [tool.schema() for tool in tools if names is None or tool.name in names]
    return len(encode(json.dumps(chosen, ensure_ascii=False))) if chosen else 0
