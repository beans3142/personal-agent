"""Time-bounded replies in one explicitly selected Kakao room."""
from __future__ import annotations

import json
import threading
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .android_control import AndroidControl

PREFIX = "com.kakao.talk:id/"


class KakaoRecipientUnavailable(ValueError):
    """No draft or send was attempted; a general lookup may safely follow."""


def read_chat(xml: str, room: str, members: int | None) -> list[dict]:
    root = ET.fromstring(xml)
    titles = [n.get("content-desc") or n.get("text") for n in root.iter("node") if n.get("resource-id") == PREFIX + "toolbar_default_title_text"]
    counts = [n.get("content-desc") or n.get("text") for n in root.iter("node") if n.get("resource-id") == PREFIX + "toolbar_default_title_count"]
    counts = list(filter(None, counts))
    if titles != [room + (str(members) if members is not None else "")] or counts != ([str(members)] if members is not None else []):
        raise ValueError("selected Kakao room is not open")
    result = []
    for bubble in root.iter("node"):
        if bubble.get("resource-id") != PREFIX + "bubble_root":
            continue
        nodes = list(bubble.iter("node"))
        incoming = any(n.get("resource-id") == PREFIX + "profile_layout" for n in nodes)
        texts = [n.get("text") or n.get("content-desc") for n in nodes if n.get("resource-id") == PREFIX + "message"]
        body = "\n".join(filter(None, texts))
        if not body and any(n.get("resource-id") == PREFIX + "message_container" for n in nodes):
            body = "[비텍스트 메시지: 이미지/첨부 내용은 확인하지 못함]"
        if body:
            result.append({"incoming": incoming, "text": body})
    return result


def appended_messages(old: list[dict], current: list[dict]) -> list[dict]:
    if not old:
        return []
    for overlap in range(min(len(old), len(current)), 0, -1):
        if old[-overlap:] == current[:overlap]:
            return current[overlap:]
    raise ValueError("message window lost overlap; automatic replay is unsafe")


def new_messages(old: list[dict], current: list[dict]) -> list[dict]:
    return [m for m in appended_messages(old, current) if m["incoming"]]


class KakaoWatcher:
    def __init__(self, device, path: Path):
        self.control = AndroidControl(device)
        self.path = path
        self._lock = threading.RLock()

    def load(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"enabled": False}

    def save(self, state, keep_stopped=False):
        with self._lock:
            if keep_stopped and not self.load().get("enabled"):
                state["enabled"] = False
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            temp.chmod(0o600)
            temp.replace(self.path)

    def stop(self):
        with self._lock:
            state = self.load()
            state["enabled"] = False
            self.save(state)

    def chat(self, state):
        device = self.control.device
        for attempt in range(3):
            xml = device.dump_hierarchy()
            try:
                return read_chat(xml, state["room"], state["members"])
            except ValueError:
                if attempt == 0:
                    device.open("kakao")
                    continue
            # The tablet chat list exposes a labelled button with the exact
            # title and member count. Never choose a similarly named room.
            observation = self.control.observe()
            prefix = (f"{state['room']}, ", f"me, {state['room']}, ") if state["members"] is None else f"{state['room']}, {state['members']} friends,"
            matches = [n for n in observation["nodes"] if n.get("description", "").startswith(prefix) and n.get("clickable") and (state["members"] is not None or not re.search(r",\s*\d+ friends,", n.get("description", "")))]
            if len(matches) == 1:
                node = matches[0]
                self.control.click(observation["snapshot"], node["id"], node["description"])
        # A match may have been clicked on the final navigation attempt.
        return read_chat(device.dump_hierarchy(), state["room"], state["members"])

    def tick(self, now: datetime, reply):
        state = self.load()
        if not state.get("enabled"):
            return "disabled"
        if now >= datetime.fromisoformat(state["until"]):
            state["enabled"] = False
            self.save(state, keep_stopped=True)
            return "expired"
        if state.get("pending"):
            return "paused"
        current = self.chat(state)
        if "tail" not in state:
            state["tail"] = current
            self.save(state, keep_stopped=True)
            return "baseline"
        incoming = state.pop("backlog", []) + new_messages(state["tail"], current)
        state["tail"] = current
        state["last_poll"] = now.isoformat()
        if not incoming:
            self.save(state, keep_stopped=True)
            return "unchanged"
        state["pending"] = True
        state["pending_incoming"] = incoming
        self.save(state, keep_stopped=True)  # An uncertain send must not resend.
        if not state["enabled"]:
            return "disabled"
        reply({**state, "incoming": incoming, "context": current[-12:]})
        # Keep the pre-generation cursor. Incoming messages that arrived while
        # drafting/sending must still be discovered by the next poll.
        state["pending"] = False
        state.pop("pending_incoming", None)
        state["replies"] = state.get("replies", 0) + 1
        self.save(state, keep_stopped=True)
        return "replied"

    def send_once(self, recipient: str, text: str):
        state = {"room": recipient, "members": None, "until": (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()}
        try:
            self.chat(state)
        except ValueError as error:
            raise KakaoRecipientUnavailable("recipient needs a general lookup") from error
        self.send_text(state, text, check_enabled=False)

    def send_text(self, state, text: str, *, check_enabled=True):
        if not text.strip():
            raise ValueError("empty automatic reply")
        def guard():
            if check_enabled and not self.load().get("enabled"):
                raise ValueError("Kakao automatic replies stopped by owner")
            if datetime.now(timezone.utc) >= datetime.fromisoformat(state["until"]):
                raise ValueError("Kakao reply deadline reached")
            return read_chat(self.control.device.dump_hierarchy(), state["room"], state["members"])
        guard()
        observed = self.control.observe()
        fields = [n for n in observed["nodes"] if n.get("resource") == PREFIX + "message_edit_text"]
        if len(fields) != 1:
            raise ValueError("ambiguous Kakao input")
        self.control.input(observed["snapshot"], fields[0]["id"], text=text)
        guard()
        observed = self.control.observe()
        buttons = [n for n in observed["nodes"] if n.get("description") in ("Send", "전송") or n.get("resource") == PREFIX + "send"]
        if len(buttons) != 1:
            raise ValueError("ambiguous Kakao send button")
        with self._lock:
            before = guard()
            self.control.click(observed["snapshot"], buttons[0]["id"], buttons[0].get("text") or buttons[0].get("description", ""))
        for _ in range(3):
            messages = read_chat(self.control.device.dump_hierarchy(), state["room"], state["members"])
            appended = appended_messages(before, messages) if before else messages
            if any(not m["incoming"] and m["text"] == text for m in appended):
                return
        raise RuntimeError("send result is uncertain; do not retry automatically")
