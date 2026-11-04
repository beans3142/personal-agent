import json

import pytest

from agent.loop import Tool
from agent.recipes import RecipeExecutor, build_recipe_tools, fixed_plan


def fixture_tool(name, calls, result="ok", approval=False):
    return Tool(name, "test", {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
                lambda args: calls.append((name, args)) or result, approval)


@pytest.mark.parametrize("status", ["no_credentials", "captcha", "unknown_site"])
def test_failed_authentication_stops_plan_before_later_write(status):
    calls = []
    executor = RecipeExecutor([
        fixture_tool("auth_ensure", calls, json.dumps({"status": status})),
        fixture_tool("write", calls),
    ], approve=lambda *_: True)
    result = executor.execute({"steps": [
        {"tool": "auth_ensure", "args": {"text": "naver"}},
        {"tool": "write", "args": {"text": "do not send"}},
    ]})
    assert result["status"] == "error"
    assert [name for name, _ in calls] == ["auth_ensure"]


def test_entire_plan_validated_before_any_action():
    calls = []
    executor = RecipeExecutor([fixture_tool("memory_search", calls)])
    with pytest.raises(ValueError):
        executor.execute({"steps": [{"tool": "memory_search", "args": {"text": "ok"}},
                                    {"tool": "memory_search", "args": {"text": 3}}]})
    assert calls == []


@pytest.mark.parametrize("plan", [{"steps": []}, {"steps": [{}] * 9}, {"steps": [], "code": "exec"},
                                  {"steps": [{"tool": "missing", "args": {}}]}])
def test_invalid_plans_rejected(plan):
    with pytest.raises(ValueError):
        RecipeExecutor([]).execute(plan)


def test_write_requires_per_step_approval_and_denial_stops():
    calls, approvals = [], []
    executor = RecipeExecutor([fixture_tool("write", calls), fixture_tool("memory_search", calls)],
                              approve=lambda name, args: approvals.append((name, args)) or False)
    result = executor.execute({"steps": [{"tool": "write", "args": {"text": "private"}},
                                        {"tool": "memory_search", "args": {"text": "later"}}]})
    assert result["status"] == "denied"
    assert approvals == [("write", {"text": "private"})]
    assert not calls


def test_uncertain_write_is_not_retried_and_stops():
    calls = []
    executor = RecipeExecutor([fixture_tool("send", calls, "error: timeout")], approve=lambda *_: True)
    result = executor.execute({"steps": [{"tool": "send", "args": {"text": "once"}}] * 2})
    assert result["status"] == "error"
    assert len(calls) == 1


@pytest.mark.parametrize("output", ['{"status":"error"}', '{"success":false}', '{"error":"timeout"}'])
def test_structured_errors_stop_execution(output):
    calls = []
    executor = RecipeExecutor([fixture_tool("send", calls, output)], approve=lambda *_: True)
    assert executor.execute({"steps": [{"tool": "send", "args": {"text": "once"}}] * 2})["status"] == "error"
    assert len(calls) == 1


def test_parameterized_recipe_is_reusable_without_interpolation(tmp_path):
    calls = []
    registry = tmp_path / "recipes.json"
    registry.write_text(json.dumps({"lookup": {"parameters": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
        "plan": {"steps": [{"tool": "memory_search", "args": {"text": {"$param": "q"}}}]}}}), encoding="utf-8")
    executor = RecipeExecutor([fixture_tool("memory_search", calls)], registry=registry)
    literal = '$(danger) {{not code}}'
    assert executor.run_recipe("lookup", {"q": literal})["status"] == "ok"
    assert calls == [("memory_search", {"text": literal})]
    with pytest.raises(ValueError):
        executor.run_recipe("lookup", {"q": 3})
    assert {t.name for t in build_recipe_tools(executor)} == {"plan_execute", "recipe_list", "recipe_run"}


def test_fixed_route_is_anchored_and_requires_exact_quoted_endpoints():
    assert fixed_plan('경로 "행신역" → "선릉역"') == {"steps": [{"tool": "route_search", "args": {"origin": "행신역", "destination": "선릉역"}}]}
    assert fixed_plan('경로 "행신역" → "선릉역" 2026-11-05T11:00:00+09:00')["steps"][0]["args"]["depart_at"].endswith("+09:00")
    for text in ['행신역에서 선릉역', '경로 "행신역" → "선릉역" 그리고 메일 보내', '경로 "" → "선릉역"']:
        assert fixed_plan(text) is None


def test_comparison_dry_run_never_invokes_runner(tmp_path):
    from scripts.compare_agents import compare
    report = compare({"existing": ["missing-binary"], "hermes": ["missing-binary"]}, tmp_path / "trace.jsonl")
    assert len(report) == 8
    assert all(row["status"] == "planned" for row in report)
    assert not (tmp_path / "trace.jsonl").exists()


def test_comparison_side_effects_gated_and_success_requires_evidence(tmp_path):
    import sys
    from scripts.compare_agents import compare
    runner = [sys.executable, "-c", 'import json; print(json.dumps({"success": True,"evidence":"verified response", "input_tokens": 12}))']
    path = tmp_path / "trace.jsonl"
    first = compare({"existing": runner, "hermes": runner}, path, execute=True)
    assert sum(row["status"] == "passed" for row in first) == 4
    assert sum(row["status"] == "blocked_side_effect" for row in first) == 4
    second = compare({"existing": runner}, path, execute=True)
    assert any(row["status"] == "already_attempted" for row in second)
def test_blocked_action_stops_before_later_write():
    from agent.loop import Tool
    from agent.recipes import RecipeExecutor
    sent=[]
    tools=[Tool('mail_send','',{'type':'object'},lambda a:'blocked: login needed'),Tool('other_write','',{'type':'object'},lambda a:sent.append(True) or 'sent')]
    result=RecipeExecutor(tools,approve=lambda *a:True).execute({'steps':[{'tool':'mail_send','args':{}},{'tool':'other_write','args':{}}]})
    assert result['status']=='error' and not sent


def test_comparison_rejects_group_and_public_send_tasks():
    from scripts.compare_runner import prepare_task, run_task
    for task_id in ("kakao_group", "discord_owner"):
        with pytest.raises(ValueError):
            prepare_task({"id": task_id}, {})
    assert run_task({"id": "discord_private_qa", "prompt": "send public"}, "existing", env={})["success"] is False
    assert run_task({"id": "kakao_self"}, "existing", env={})["success"] is False


def test_hermes_comparison_requires_matching_confirmed_receipts():
    from scripts.compare_runner import confirmed_receipt
    from datetime import datetime, timedelta, timezone
    day = datetime.now(timezone(timedelta(hours=9))).date() + timedelta(days=1)
    route = {"tool": "mcp__daevis__route_search", "confirmed": True, "origin": "행신역", "destination": "선릉역", "depart_at": f"{day}T11:00:00+09:00"}
    assert confirmed_receipt("transit", [route])
    assert not confirmed_receipt("transit", [{**route, "confirmed": False}])
    assert not confirmed_receipt("transit", [{**route, "depart_at": f"{day}T12:00:00+09:00"}])
    assert not confirmed_receipt("transit", [{**route, "destination": "other"}])
    for task, tool in [("mail_self", "mail_send"), ("kakao_self", "kakao_self_send")]:
        receipt = {"tool": f"mcp__daevis__{tool}", "confirmed": True, "recipient_is_self": True}
        assert confirmed_receipt(task, [receipt])
        assert not confirmed_receipt(task, [{**receipt, "recipient_is_self": False}])
        assert not confirmed_receipt(task, [])


def _self_comparison_env(tmp_path):
    from datetime import datetime, timedelta, timezone
    path = tmp_path / "authorization.json"
    path.write_text(json.dumps({"owner_id": "123", "tasks": ["kakao_self"],
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        "authorization": "Owner requested only own Kakao self-chat test"}), encoding="utf-8")
    return {"OWNER_ID": "123", "DAEVIS_COMPARE_SENDS": "1", "DAEVIS_COMPARE_AUTHORIZATION_FILE": str(path)}


def test_authorized_self_task_uses_only_canonical_self_prompt(tmp_path):
    from scripts.compare_runner import prepare_task
    env = _self_comparison_env(tmp_path)
    prompt, reason = prepare_task({"id": "kakao_self", "prompt": "send to a group instead"}, env)
    assert reason is None
    assert "kakao_self_send" in prompt and "javis 테스트" in prompt
    assert "group instead" not in prompt
    assert prepare_task({"id": "kakao_self"}, {**env, "DAEVIS_COMPARE_SENDS": "0"})[0] is None


@pytest.mark.parametrize("native_output", ["KAKAO_SENT", "KAKAO_NOT_SENT"])
def test_existing_self_adapter_blocks_other_text_recipient_and_repeat(tmp_path, monkeypatch, native_output):
    from scripts.compare_runner import existing
    from agent import android_control, authgate, codex, kakao_self, memory, naver_mail, tools, loop
    calls = []
    monkeypatch.setattr(memory, "Memory", lambda *_: object())
    monkeypatch.setattr(authgate, "PlaywrightBrowser", lambda *_: object())
    monkeypatch.setattr(authgate, "AuthGate", lambda *_: object())
    monkeypatch.setattr(android_control, "UiaDevice", lambda *_: object())
    monkeypatch.setattr(android_control, "build_android_tools", lambda *_: [])
    monkeypatch.setattr(tools, "build_tools", lambda *_: [])
    monkeypatch.setattr(authgate, "build_auth_tool", lambda *_: Tool("auth_ensure", "", {}, lambda _: "ok"))
    monkeypatch.setattr(naver_mail, "build_mail_tool", lambda *_: Tool("mail_send", "", {}, lambda _: "sent"))
    monkeypatch.setattr(codex, "CodexModel", lambda *_: object())
    monkeypatch.setattr(kakao_self, "build_kakao_self_tools", lambda *_a, **_kw: [Tool("kakao_self_send", "", {}, lambda args: calls.append(args) or native_output)])

    def fake_turn(_model, task_tools, _instructions, _history, _prompt, approve, **_kwargs):
        assert [tool.name for tool in task_tools] == ["kakao_self_send"]
        tool = task_tools[0]
        assert not approve(tool.name, {"text": "other"})
        assert not approve(tool.name, {"text": "javis 테스트", "recipient": "group"})
        assert tool.run({"text": "other"}).startswith("denied:")
        assert approve(tool.name, {"text": "javis 테스트"})
        assert tool.run({"text": "javis 테스트"}) == native_output
        assert tool.run({"text": "javis 테스트"}).startswith("denied:")
        return "ignored model prose"

    monkeypatch.setattr(loop, "run_turn", fake_turn)
    result = existing("kakao_self", "canonical prompt", tmp_path, _self_comparison_env(tmp_path))
    assert result["success"] is (native_output == "KAKAO_SENT")
    assert calls == [{"text": "javis 테스트"}]
    if result["success"]:
        assert "reading" in result["evidence"]
