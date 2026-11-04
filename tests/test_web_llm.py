import pytest

from agent.web_llm import QuickLane, parse_route, route_prompt


def test_direct_answer_is_parsed():
    assert parse_route("DIRECT: 서울은 대한민국의 수도예요.") == "서울은 대한민국의 수도예요."


def test_agent_and_unknown_formats_go_to_main_model():
    assert parse_route("AGENT") is None
    assert parse_route("음, 잘 모르겠어요") is None
    assert parse_route("") is None
    assert parse_route("DIRECT:   ") is None


def test_prompt_lists_tool_needs_and_contains_request():
    prompt = route_prompt("1+1은?")
    assert "1+1은?" in prompt
    for word in ("검색", "메일", "카톡", "예약", "최신"):
        assert word in prompt


class FakeWeb:
    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.asked = answer, error, []

    def ask(self, prompt):
        self.asked.append(prompt)
        if self.error:
            raise self.error
        return self.answer


def test_quick_lane_returns_direct_answer():
    assert QuickLane(FakeWeb("DIRECT: 2예요.")).try_answer("1+1은?") == "2예요."


def test_quick_lane_falls_back_when_web_fails():
    assert QuickLane(FakeWeb(error=TimeoutError("slow"))).try_answer("1+1은?") is None


def test_quick_lane_skips_long_or_tool_shaped_requests_without_asking():
    web = FakeWeb("DIRECT: x")
    lane = QuickLane(web)
    assert lane.try_answer("가" * 400) is None
    assert lane.try_answer("카톡으로 엄마한테 늦는다고 보내 줘") is None
    assert lane.try_answer("http://example.com 요약해 줘") is None
    assert web.asked == []


def test_quick_lane_can_be_disabled():
    web = FakeWeb("DIRECT: x")
    assert QuickLane(web, enabled=False).try_answer("1+1은?") is None
    assert web.asked == []
