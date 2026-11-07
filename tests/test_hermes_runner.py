import json
from types import SimpleNamespace

import pytest

from agent.hermes_runner import HermesRunner, parse_events


def test_trace_uses_reported_metrics_and_kakao_activity_without_echoing_payload():
    events = [{"type":"tool", "name":"android_screen", "kakao_used":True},
              {"type":"result", "text":"private chat", "input_tokens":1200, "output_tokens":12, "model_calls":2, "tool_calls":1, "model":"returned-model", "completed":True}]
    out = parse_events("\n".join(json.dumps(x) for x in events))
    assert out.kakao_used and out.text == "private chat"
    assert "1.2k" in out.footer(2.5) and "returned-model" in out.footer(2.5)
    assert "private chat" not in out.footer(2.5)


def test_missing_or_failed_completion_never_looks_successful():
    with pytest.raises(RuntimeError): parse_events('{"type":"tool","name":"mail_send"}')
    with pytest.raises(RuntimeError): parse_events('{"type":"result","completed":false,"text":"not done"}')


def test_usage_limit_without_tools_is_explained_without_implying_a_send():
    from agent.hermes_runner import HermesError
    with pytest.raises(HermesError) as e:
        parse_events('{"type":"result","completed":false,"stop_reason":"usage_limit_reached","tool_calls":0,"input_tokens":null,"output_tokens":null}')
    assert '사용량 한도' in str(e.value)
    assert '전송 여부' not in str(e.value)


def test_runner_uses_stdin_and_process_group_without_shell_and_scopes_public(tmp_path):
    calls=[]
    def run(argv, **kwargs):
        calls.append((argv,kwargs))
        return SimpleNamespace(returncode=0, stdout='{"type":"result","completed":true,"text":"ok","model_calls":1}', stderr="")
    runner=HermesRunner("python-worker", "worker.py", run=run, env={"OWNER_ID":"123"}, data=tmp_path)
    assert runner.run('quotes $(secret)', 'system', [], public=True).text == 'ok'
    argv, kw=calls[0]
    assert argv == ["python-worker", "worker.py"] and "shell" not in kw
    assert json.loads(kw["input"])["text"] == 'quotes $(secret)'
    assert kw["env"]["DAEVIS_MCP_PUBLIC"] == "1"
    assert kw["env"]["DAEVIS_MCP_OWNER_REQUEST"] == "123"
    assert kw["timeout"] <= 310


def test_worker_error_is_not_exposed(tmp_path):
    runner=HermesRunner("python", "worker", run=lambda *a,**k: SimpleNamespace(returncode=1,stdout="",stderr="secret"),env={},data=tmp_path)
    with pytest.raises(RuntimeError,match="Hermes") as error:
        runner.run("hi","system",[])
    assert "secret" not in str(error.value)


def test_scheduled_worker_has_no_owner_write_lease(tmp_path):
    seen=[]
    def run(*a,**k):
        seen.append(k['env'])
        return SimpleNamespace(returncode=0,stdout='{"type":"result","completed":true,"text":"ok"}',stderr='')
    HermesRunner('p','w',data=tmp_path,env={'OWNER_ID':'1'},run=run).run('task','system',[],owner_request=False)
    assert not seen[0].get('DAEVIS_MCP_OWNER_REQUEST')
    assert seen[0]['DAEVIS_MCP_READ_ONLY']=='1'


def test_each_runner_turn_has_fresh_trusted_request_id(tmp_path):
    seen = []
    def run(*args, **kwargs):
        seen.append(kwargs['env']['DAEVIS_MCP_REQUEST_ID'])
        return SimpleNamespace(returncode=0, stdout='{"type":"result","completed":true,"text":"ok"}', stderr='')
    runner = HermesRunner('python', 'worker', data=tmp_path,
                          env={'OWNER_ID': '1', 'DAEVIS_MCP_REQUEST_ID': 'old'}, run=run)
    runner.run('one', 'system', [])
    runner.run('two', 'system', [])
    assert len(set(seen)) == 2 and all(len(value) == 32 and value != 'old' for value in seen)


def test_iteration_limit_keeps_the_summary_instead_of_a_generic_failure():
    out = parse_events('{"type":"result","completed":false,"stop_reason":"iteration_limit","text":"초밥집 3곳을 찾았어요.","model_calls":20,"tool_calls":21}')
    assert "초밥집 3곳을 찾았어요." in out.text
    assert out.text.startswith("⚠️ 단계 한도")
    assert "전송 여부" not in out.text


def test_iteration_limit_without_summary_is_still_a_failure():
    from agent.hermes_runner import HermesError
    with pytest.raises(HermesError) as e:
        parse_events('{"type":"result","completed":false,"stop_reason":"iteration_limit","text":"","model_calls":20}')
    assert "단계 한도" in str(e.value)
