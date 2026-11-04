"""Actual isolated comparison adapter. Read one historical task JSON from stdin.

Run from the project root: python -m scripts.compare_runner --engine existing
(or hermes). No Discord websocket is opened. Side effects require both
DAEVIS_COMPARE_SENDS=1 and an owner-local authorization JSON file selected by
DAEVIS_COMPARE_AUTHORIZATION_FILE, containing owner_id, tasks (task id list),
expires_at (timezone-aware ISO timestamp), and authorization (recorded request).
The harness attempt ledger remains responsible for preventing repeat runs.
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TASK_IDS = {"kakao_self", "transit", "mail_self", "discord_private_qa"}
TEST_TEXT = "javis 테스트"
INSTRUCTIONS = (
    "너는 개인 비서 daevis다. 이번 요청 하나만 실행하고 종료한다. "
    "도구 결과가 blocked/error/denied이거나 전송 결과가 불확실하면 즉시 중단하고 재시도하지 않는다. "
    "요청하지 않은 전송, 저장, 예약, 다른 수신자 조작은 금지한다. "
    "성공을 추측하지 말고 실제 도구 결과로만 보고한다."
)


def authorization(task_id, env):
    if env.get("DAEVIS_COMPARE_SENDS") != "1":
        return False
    try:
        path = env["DAEVIS_COMPARE_AUTHORIZATION_FILE"]
        record = json.loads(Path(path).read_text(encoding="utf-8"))
        expires = datetime.fromisoformat(record["expires_at"])
        return (bool(env.get("OWNER_ID")) and record.get("owner_id") == env["OWNER_ID"]
                and isinstance(record.get("tasks"), list) and task_id in record["tasks"]
                and isinstance(record.get("authorization"), str) and bool(record["authorization"].strip())
                and expires.tzinfo is not None and expires > datetime.now(timezone.utc))
    except (KeyError, ValueError, TypeError, OSError):
        return False


def prepare_task(task, env):
    if not isinstance(task, dict) or task.get("id") not in TASK_IDS:
        raise ValueError("unknown comparison task")
    task_id = task["id"]
    # Caller-supplied prompt/side_effect fields are deliberately ignored.
    if task_id == "discord_private_qa":
        return None, "Private Discord DM roundtrip/access QA requires external owner UI evidence; no public message or model action was performed."
    if task_id != "transit" and not authorization(task_id, env):
        return None, "Not sent: explicit send flag and unexpired owner authorization record required."
    if task_id == "kakao_self":
        return "kakao_self_send 도구로 카카오톡 나와의 채팅에만 javis 테스트를 정확히 한 번 전송해 줘. text는 javis 테스트만 사용하고 다른 방은 열거나 보내지 마.", None
    if task_id == "transit":
        tomorrow = datetime.now(timezone(timedelta(hours=9))).date() + timedelta(days=1)
        return f"{tomorrow.isoformat()} 오전 11시 출발 행신역에서 선릉역 대중교통 경로를 route_search로 조회해 줘.", None
    if task_id == "mail_self":
        return "네이버 메일 나에게 쓰기. 수신자는 나 자신, 제목과 본문은 모두 javis 테스트. 정확히 한 번 전송해 줘.", None
    raise ValueError("unsupported comparison task")


def _requested_route(args):
    try:
        departure = datetime.fromisoformat(args.get("depart_at", ""))
    except (TypeError, ValueError):
        return False
    kst = timezone(timedelta(hours=9))
    departure = departure.replace(tzinfo=kst) if departure.tzinfo is None else departure.astimezone(kst)
    tomorrow = datetime.now(kst).date() + timedelta(days=1)
    return (args.get("origin") == "행신역" and args.get("destination") == "선릉역"
            and departure.date() == tomorrow and departure.hour == 11 and departure.minute == 0 and departure.second == 0)


def confirmed_receipt(task_id, receipts):
    """Accept only explicit sanitized worker receipts, never assistant prose."""
    target = {"transit": "route_search", "mail_self": "mail_send", "kakao_self": "kakao_self_send"}.get(task_id)
    if not target or not isinstance(receipts, list):
        return False
    for receipt in receipts:
        if not isinstance(receipt, dict) or receipt.get("confirmed") is not True:
            continue
        if receipt.get("tool") not in {target, f"mcp__daevis__{target}"}:
            continue
        if task_id == "transit" and _requested_route(receipt):
            return True
        if task_id in {"mail_self", "kakao_self"} and receipt.get("recipient_is_self") is True:
            return True
    return False


def _route_receipt(args, output):
    try:
        value = json.loads(output)
    except (TypeError, ValueError):
        return False
    return (_requested_route(args)
            and isinstance(value, dict) and bool(value.get("routes")) and value.get("source") == "네이버 지도")


def existing(task_id, prompt, data, env):
    # Lazy imports keep readiness and guard checks entirely offline.
    from agent.android_control import AndroidControl, UiaDevice, build_android_tools
    from agent.authgate import AuthGate, EnvCredentials, PlaywrightBrowser, build_auth_tool
    from agent.codex import CodexAuth, CodexModel
    from agent.loop import Tool, TurnMetrics, run_turn
    from agent.memory import Memory
    from agent.naver_mail import build_mail_tool, owner_address
    from agent.tools import build_tools
    from agent.kakao_self import build_kakao_self_tools

    data.mkdir(parents=True, exist_ok=True)
    memory = Memory(data / "agent.db")
    browser = PlaywrightBrowser(data / "sessions")
    gate = AuthGate(EnvCredentials(env), browser)
    android = AndroidControl(UiaDevice(env.get("ANDROID_SERIAL")), env=env, outbox=data / "outbox", approve=lambda *_: False)
    candidates = build_tools(memory, data / "schedule.json", env) + build_android_tools(android)
    candidates += [build_auth_tool(gate), build_mail_tool(gate, browser, env)]
    candidates += build_kakao_self_tools(android.device, data)
    target = {"transit": "route_search", "mail_self": "mail_send", "kakao_self": "kakao_self_send"}[task_id]
    receipt, attempted = [], [False]

    def permitted(name, args):
        if name != target:
            return False
        if name == "route_search":
            return _requested_route(args)
        if attempted[0] or not authorization(task_id, env):
            return False
        if name == "mail_send":
            return (args.get("to", "") in {"", owner_address(env)}
                    and args.get("subject") == TEST_TEXT and args.get("body") == TEST_TEXT)
        if name == "kakao_self_send":
            return args == {"text": TEST_TEXT}
        return False

    def wrap(tool):
        def run(args):
            # Independent execution guard: legacy needs_approval flags alone
            # do not cover all write tools. A write gets at most one attempt.
            if not permitted(tool.name, args):
                return "denied: action is outside the exact authorized comparison task"
            if task_id != "transit":
                attempted[0] = True
            output = str(tool.run(args))
            if tool.name == "route_search" and _route_receipt(args, output):
                receipt.append("route_search returned structured Naver transit alternatives for the requested endpoints")
            elif tool.name == "mail_send" and output.startswith("sent from "):
                receipt.append("mail_send confirmed send to the owner's own address; inbox receipt not independently verified")
            elif tool.name == "kakao_self_send" and output == "KAKAO_SENT":
                receipt.append("Native self-chat tool confirmed the outgoing bubble in the owner's own chat; human reading is not established")
            return output
        return Tool(tool.name, tool.description, tool.parameters, run, needs_approval=True)

    tools = [wrap(tool) for tool in candidates if tool.name == target]
    metrics = TurnMetrics()
    model = CodexModel(CodexAuth(data / "codex-auth.json"), env.get("AGENT_MODEL", "gpt-5.6-luna"))
    run_turn(model, tools, INSTRUCTIONS, [], prompt, permitted, max_steps=8, metrics=metrics)
    return {"success": bool(receipt), "evidence": "; ".join(receipt) or "No matching successful tool receipt; assistant text is not proof.",
            "input_tokens": metrics.input_tokens if not metrics.missing_usage else None,
            "output_tokens": metrics.output_tokens if not metrics.missing_usage else None,
            "model_calls": metrics.model_calls, "tool_calls": metrics.tool_calls,
            "model": ", ".join(metrics.models) or None}


def hermes(task_id, prompt, data, env):
    from agent.hermes_runner import HermesRunner
    python = env.get("HERMES_PYTHON")
    if not python:
        return {"success": False, "evidence": "HERMES_PYTHON is not configured; no worker started."}
    # Restrict this worker to the single task tool. MCP approval gates still
    # apply; this adapter never approves an unrelated action on its behalf.
    target = {"transit": "route_search", "mail_self": "mail_send", "kakao_self": "kakao_self_send"}[task_id]
    scoped = {**env, "DAEVIS_MCP_ALLOW_TOOLS": target}
    runner = HermesRunner(python, str(ROOT / "deploy" / "hermes-worker.py"), data=data, env=scoped)
    outcome = runner.run(prompt, INSTRUCTIONS, [], public=False, owner_request=task_id != "transit")
    verified = confirmed_receipt(task_id, getattr(outcome, "receipts", None))
    evidence = ("Worker supplied a matching confirmed tool receipt; inbox receipt/human reading not established."
                if verified else "Hermes completed without a matching confirmed task receipt; completion remains unverified.")
    return {"success": verified, "evidence": evidence,
            "input_tokens": outcome.input_tokens, "output_tokens": outcome.output_tokens,
            "model_calls": outcome.model_calls, "tool_calls": outcome.tool_calls, "model": outcome.model}


def run_task(task, engine, *, env=None, data=None):
    env = dict(os.environ if env is None else env)
    data = Path(data or env.get("AGENT_DATA", ROOT / "data"))
    prompt, reason = prepare_task(task, env)
    if reason:
        return {"success": False, "evidence": reason}
    started = time.monotonic()
    try:
        run = {"existing": existing, "hermes": hermes}[engine]
        result = run(task["id"], prompt, data, env)
    except Exception as error:
        # Exceptions can contain credentials, chat data and URLs. Emit only type.
        result = {"success": False, "evidence": f"Adapter failed ({type(error).__name__}); execution outcome may be uncertain. Do not retry blindly."}
    return {**result, "elapsed_seconds": time.monotonic() - started}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=["existing", "hermes"], required=True)
    args = parser.parse_args()
    try:
        task = json.load(sys.stdin)
        # Never mix model/browser logging or private answers into JSON stdout.
        with contextlib.redirect_stdout(sys.stderr):
            result = run_task(task, args.engine)
    except (ValueError, TypeError):
        result = {"success": False, "evidence": "Invalid comparison task JSON; nothing executed."}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
