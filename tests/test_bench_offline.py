import json
from pathlib import Path

import pytest

from bench.offline import AnswerCache, route, select_tools

REQUESTS = [json.loads(line) for line in Path("bench/requests.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.parametrize("request_", REQUESTS, ids=[r["id"] for r in REQUESTS])
def test_selected_tools_cover_every_expected_tool(request_):
    assert set(request_["expect_tools"]) <= select_tools(request_["text"])


def test_simple_questions_send_no_tools():
    simple = [r for r in REQUESTS if r["type"] == "simple"]
    assert all(select_tools(r["text"]) == set() for r in simple)


def test_router_extracts_values():
    assert route("10분 뒤에 빨래 꺼내라고 알려줘").args == {"kind": "message", "text": "빨래 꺼내", "in_minutes": 10}
    assert route("행신역에서 선릉역 가는 경로 알려줘").args == {"origin": "행신역", "destination": "선릉역"}
    assert route("배민 열어줘").args == {"app": "baemin"}


def test_router_refuses_anything_with_extra_meaning():
    assert route("내일 오전 11시에 행신역에서 선릉역 가는 경로 찾고, 출발 30분 전에 알림 맞춰줘") is None
    assert route("카톡 나와의 채팅에 '우유 사기' 메모 보내줘") is None
    assert route("광합성이 뭐야?") is None


def test_routed_tool_matches_expected_tool():
    for request_ in REQUESTS:
        routed = route(request_["text"])
        if routed:
            assert request_["expect_tools"] == [routed.tool], request_["id"]


def test_cache_hits_reworded_duplicate_and_misses_different_question():
    cache = AnswerCache()
    cache.put("광합성이 뭐야? 한 문장으로", "답")
    assert cache.get("광합성이 뭔지 한 문장으로 알려줘") == "답"
    assert cache.get("1달러는 몇 센트야?") is None
