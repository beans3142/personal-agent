import json

from agent.loop import Tool, TurnMetrics, run_turn, trim_history


def call(name, args, call_id="c1"):
    return {"type": "function_call", "name": name, "arguments": json.dumps(args), "call_id": call_id}


def text(value):
    return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": value}]}


class FakeModel:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.inputs = []

    def respond(self, instructions, items, tools):
        self.inputs.append(list(items))
        return self.replies.pop(0)


def echo_tool(**kwargs):
    return Tool("echo", "echo", {"type": "object", "properties": {}}, lambda args: f"got {args['v']}", **kwargs)


def allow(_name, _args):
    return True


def deny(_name, _args):
    return False


def outputs(items):
    return [item["output"] for item in items if item.get("type") == "function_call_output"]


def test_plain_answer_uses_one_model_call():
    model = FakeModel([text("안녕")])
    history = []
    assert run_turn(model, [], "sys", history, "hi", allow) == "안녕"
    assert len(model.inputs) == 1
    assert history[0]["role"] == "user"


def test_request_metrics_sum_all_model_calls_and_tools():
    class Metered(FakeModel):
        model = "requested-model"
        def respond(self, *args):
            self.last_response_metadata = {"model": "reported-model", "usage": {"input_tokens": 100, "output_tokens": 10}}
            return super().respond(*args)
    metrics = TurnMetrics()
    model = Metered([call("echo", {"v": 1})], [text("done")])
    run_turn(model, [echo_tool()], "sys", [], "go", allow, metrics=metrics)
    assert (metrics.model_calls, metrics.tool_calls) == (2, 1)
    assert (metrics.input_tokens, metrics.output_tokens) == (200, 20)
    footer = metrics.footer(4.5)
    assert "reported-model" in footer and "200" in footer and "20" in footer
    assert "모델 호출 2회" in footer and "4.5초" in footer


def test_missing_usage_is_marked_unavailable_instead_of_zero():
    metrics = TurnMetrics()
    run_turn(FakeModel([text("hi")]), [], "sys", [], "go", allow, metrics=metrics)
    assert "토큰 미제공" in metrics.footer(1)


def test_obsolete_android_trees_are_not_resent_to_model():
    count = []
    def screen(_):
        count.append(1)
        return json.dumps({"snapshot": len(count), "nodes": ["large old UI tree"]})
    model = FakeModel([call("android_screen", {}, "a")], [call("android_screen", {}, "b")], [text("done")])
    run_turn(model, [Tool("android_screen", "", {}, screen)], "sys", [], "go", allow)
    assert json.loads(outputs(model.inputs[-1])[0])["superseded"] is True
    assert json.loads(outputs(model.inputs[-1])[1])["snapshot"] == 2


def test_repeated_action_errors_stop_even_with_observations_between_attempts():
    def fail(_):
        raise ValueError("target changed")
    replies = []
    for i in range(5):
        replies.extend([[call("click", {}, f"c{i}")], [call("android_screen", {}, f"s{i}")]])
    model = FakeModel(*replies)
    result = run_turn(model, [Tool("click", "", {}, fail), Tool("android_screen", "", {}, lambda _: "screen")], "sys", [], "go", allow, max_steps=20)
    assert "3회" in result and "완료되지" in result
    assert len(model.inputs) == 5


def test_tool_result_goes_back_to_model():
    model = FakeModel([call("echo", {"v": 1})], [text("끝")])
    history = []
    assert run_turn(model, [echo_tool()], "sys", history, "go", allow) == "끝"
    assert outputs(model.inputs[1]) == ["got 1"]


def test_multiple_steps_run_in_order():
    model = FakeModel(
        [call("echo", {"v": 1}, "a")],
        [call("echo", {"v": 2}, "b")],
        [text("둘 다 끝")],
    )
    history = []
    assert run_turn(model, [echo_tool()], "sys", history, "go", allow) == "둘 다 끝"
    assert outputs(history) == ["got 1", "got 2"]


def test_denied_tool_is_not_run_and_model_is_told():
    ran = []
    tool = Tool("pay", "pay", {"type": "object", "properties": {}}, lambda args: ran.append(1) or "paid", needs_approval=True)
    model = FakeModel([call("pay", {})], [text("취소함")])
    assert run_turn(model, [tool], "sys", [], "buy", deny) == "취소함"
    assert ran == []
    assert "denied" in outputs(model.inputs[1])[0]


def test_approved_tool_runs():
    ran = []
    tool = Tool("pay", "pay", {"type": "object", "properties": {}}, lambda args: ran.append(1) or "paid", needs_approval=True)
    model = FakeModel([call("pay", {})], [text("완료")])
    run_turn(model, [tool], "sys", [], "buy", allow)
    assert ran == [1]


def test_safe_tool_never_asks_for_approval():
    asked = []
    model = FakeModel([call("echo", {"v": 1})], [text("끝")])
    run_turn(model, [echo_tool()], "sys", [], "go", lambda n, a: asked.append(n) or True)
    assert asked == []


def test_tool_error_goes_to_model_instead_of_crashing():
    def boom(_args):
        raise OSError("network down")

    tool = Tool("boom", "boom", {"type": "object", "properties": {}}, boom)
    model = FakeModel([call("boom", {})], [text("실패했어요")])
    assert run_turn(model, [tool], "sys", [], "go", allow) == "실패했어요"
    assert "network down" in outputs(model.inputs[1])[0]


def test_unknown_tool_and_bad_json_are_reported_to_model():
    bad = {"type": "function_call", "name": "echo", "arguments": "{broken", "call_id": "b"}
    model = FakeModel([call("nope", {}, "a"), bad], [text("끝")])
    run_turn(model, [echo_tool()], "sys", [], "go", allow)
    results = outputs(model.inputs[1])
    assert "unknown tool" in results[0]
    assert "invalid arguments" in results[1]


def test_long_tool_result_is_cut_to_save_tokens():
    tool = Tool("big", "big", {"type": "object", "properties": {}}, lambda args: "x" * 50000)
    model = FakeModel([call("big", {})], [text("끝")])
    run_turn(model, [tool], "sys", [], "go", allow, max_result_chars=100)
    assert len(outputs(model.inputs[1])[0]) < 200


def test_android_screen_preserves_input_after_large_chat_list():
    nodes = [{"id": i, "text": "chat list entry " * 20} for i in range(40)]
    nodes.append({"id": 281, "resource": "message_edit_text", "input": True})
    screen = {"snapshot": "fresh", "nodes": nodes}
    tool = Tool("android_screen", "", {}, lambda _: json.dumps(screen))
    model = FakeModel([call("android_screen", {})], [text("done")])
    run_turn(model, [tool], "sys", [], "go", allow)
    observed = json.loads(outputs(model.inputs[1])[0])
    assert observed == screen
    assert observed["nodes"][-1]["input"] is True


def test_step_limit_stops_endless_tool_calls():
    model = FakeModel(*[[call("echo", {"v": i}, str(i))] for i in range(5)])
    reply = run_turn(model, [echo_tool()], "sys", [], "go", allow, max_steps=3)
    assert len(model.inputs) == 3
    assert "한도" in reply


def test_trim_history_starts_at_a_user_message():
    history = [
        {"role": "user", "content": "1"},
        {"type": "function_call", "call_id": "a"},
        {"type": "function_call_output", "call_id": "a"},
        {"role": "user", "content": "2"},
        text("answer"),
    ]
    assert trim_history(history, 3) == history[3:]


def test_trim_history_keeps_short_history():
    history = [{"role": "user", "content": "1"}]
    assert trim_history(history, 10) == history
