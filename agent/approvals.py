"""Payment-only owner approval, answered with buttons where the request was made.

The MCP worker cannot receive Discord button clicks, so it writes a request
file into the per-request outbox and waits for the gateway to write the
owner's answer next to it. Only payment buttons ever reach this path; every
other owner-requested action runs without asking.
"""
import json
import os
from pathlib import Path
import re
import secrets
import time

PAYMENT = re.compile(r"결제|주문하기|주문\s*확정|구매하기|Place order|Pay now|Buy now", re.I)
AMOUNT = re.compile(r"(?:₩\s?\d[\d,]*|\d[\d,]*\s?(?:원|KRW))")
# Unlisted apps show their package name rather than a guessed brand.
APPS = {"com.coupang.mobile": "쿠팡", "com.kakao.talk": "카카오톡", "com.sampleapp": "배달의민족"}  # Baemin's real package (checked on device).
ANSWERS = {"allow", "deny"}
REQUEST_ID = re.compile(r"[0-9a-f]{16}")


def screen_amounts(nodes, limit=3):
    found = []
    for node in nodes:
        for text in (node.get("text", ""), node.get("description", "")):
            for value in AMOUNT.findall(text or ""):
                if value not in found:
                    found.append(value)
    return found[:limit]


def owner_request_active(env) -> bool:
    try:
        return bool(env.get("OWNER_ID")) and env.get("DAEVIS_MCP_OWNER_REQUEST") == env["OWNER_ID"] and float(env.get("DAEVIS_MCP_REQUEST_EXPIRES", "0")) > time.time()
    except (ValueError, TypeError):
        return False


def owner_policy(env, ask):
    """Owner requests run freely except payment, which asks; others run nothing."""
    def approve(name, args):
        if not owner_request_active(env):
            return False
        if name != "payment":
            return True
        return bool(ask(name, args)) and owner_request_active(env)
    return approve


def _folder(outbox):
    return Path(outbox) / "approvals"


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def pending_requests(outbox):
    folder = _folder(outbox)
    requests = []
    for path in sorted(folder.glob("*.json")) if folder.exists() else []:
        if (folder / (path.stem + ".answer")).exists():
            continue
        try:
            requests.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return requests


def answer(outbox, request_id, value):
    if value not in ANSWERS or not REQUEST_ID.fullmatch(str(request_id)):
        raise ValueError("invalid approval answer")
    folder = _folder(outbox)
    if not (folder / f"{request_id}.json").exists():
        return False  # Already timed out and withdrawn: the click changes nothing.
    _write(folder / f"{request_id}.answer", value)
    return True


class ChannelApproval:
    def __init__(self, outbox, timeout=110, sleep=time.sleep, clock=time.monotonic, budget=lambda: None):
        self.outbox = Path(outbox) if outbox else None
        self.timeout, self.sleep, self.clock, self.budget = timeout, sleep, clock, budget

    def __call__(self, name, args) -> bool:
        if self.outbox is None:
            return False
        limit = self.timeout
        remaining = self.budget()
        if remaining is not None:
            limit = min(limit, int(remaining) - 10)  # Answer must land before the owner lease ends.
        if limit <= 0:
            return False
        request_id = secrets.token_hex(8)
        folder = _folder(self.outbox)
        request_path, answer_path = folder / f"{request_id}.json", folder / f"{request_id}.answer"
        _write(request_path, {"id": request_id, "button": str(args.get("button", ""))[:80],
                              "package": str(args.get("package", ""))[:80],
                              "amounts": [str(a)[:30] for a in args.get("amounts", [])][:3], "expires_in": limit})
        deadline = self.clock() + limit
        try:
            while self.clock() < deadline:
                if answer_path.exists():
                    return answer_path.read_text(encoding="utf-8").strip() == '"allow"'
                self.sleep(0.5)
            return False
        finally:
            for path in (request_path, answer_path):
                path.unlink(missing_ok=True)


def _duration(seconds):
    minutes, rest = divmod(int(seconds), 60)
    return " ".join(part for part in (f"{minutes}분" if minutes else "", f"{rest}초" if rest else "") if part) or "0초"


def payment_text(request):
    app = APPS.get(request.get("package", ""), request.get("package") or "알 수 없는 앱")
    lines = ["💳 **결제 확인**", f"앱: {app}", f"누를 버튼: **{request.get('button') or '결제'}**"]
    if request.get("amounts"):
        lines.append("화면 금액: " + " · ".join(request["amounts"]))
    lines.append(f"{_duration(request.get('expires_in', 0))} 안에 고르지 않으면 결제하지 않습니다.")
    return "\n".join(lines)
