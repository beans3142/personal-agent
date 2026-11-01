from datetime import datetime, timedelta
from email.message import EmailMessage

import pytest

from agent import scheduler
from agent.loop import Tool
from agent.memory import Memory
from agent.tools import (
    build_tools,
    format_places,
    html_to_text,
    is_public_url,
    message_summary,
    parse_search_results,
)


@pytest.fixture
def memory(tmp_path):
    return Memory(tmp_path / "agent.db")


def by_name(tmp_path, memory, **kwargs):
    tools = build_tools(memory, tmp_path / "schedule.json", env=kwargs)
    return {tool.name: tool for tool in tools}


def test_memory_saves_searches_and_forgets(memory):
    fact_id = memory.save("집은 서울 성북구다")
    memory.save("좋아하는 음식은 치킨이다")
    assert [row["text"] for row in memory.search("치킨")] == ["좋아하는 음식은 치킨이다"]
    assert memory.forget(fact_id) is True
    assert memory.search("성북구") == []
    assert memory.forget(fact_id) is False


def test_memory_recent_returns_newest_first_with_limit(memory):
    for index in range(5):
        memory.save(f"fact {index}")
    assert [row["text"] for row in memory.recent(2)] == ["fact 4", "fact 3"]


def test_memory_rejects_empty_fact(memory):
    with pytest.raises(ValueError):
        memory.save("   ")


def test_history_round_trip_per_chat(memory):
    memory.save_history("a", [{"role": "user", "content": "안녕"}])
    assert memory.load_history("a") == [{"role": "user", "content": "안녕"}]
    assert memory.load_history("b") == []


def test_all_tools_have_valid_schema(tmp_path, memory):
    for tool in by_name(tmp_path, memory).values():
        assert isinstance(tool, Tool)
        assert tool.parameters["type"] == "object"
        assert tool.description


def test_schedule_tool_adds_job_for_given_time(tmp_path, memory):
    tools = by_name(tmp_path, memory)
    at = (datetime.now().astimezone() + timedelta(hours=1)).isoformat()
    tools["schedule_add"].run({"kind": "message", "text": "물", "at": at})
    jobs = scheduler.load_jobs(tmp_path / "schedule.json")
    assert [job["text"] for job in jobs] == ["물"]


def test_schedule_add_is_always_one_time_even_if_model_sends_interval(tmp_path, memory):
    tools = by_name(tmp_path, memory)
    at = (datetime.now().astimezone() + timedelta(hours=1)).isoformat()
    tools["schedule_add"].run({"kind": "message", "text": "물", "at": at, "every_minutes": 1})
    assert scheduler.load_jobs(tmp_path / "schedule.json")[0]["every_minutes"] is None
    assert "every_minutes" not in tools["schedule_add"].parameters["properties"]


def test_schedule_repeat_never_goes_below_thirty_minutes(tmp_path, memory):
    tools = by_name(tmp_path, memory)
    at = (datetime.now().astimezone() + timedelta(hours=1)).isoformat()
    tools["schedule_repeat"].run({"kind": "message", "text": "물", "at": at, "every_minutes": 1})
    assert scheduler.load_jobs(tmp_path / "schedule.json")[0]["every_minutes"] == 30


def test_mail_without_credentials_says_what_is_missing(tmp_path, memory):
    with pytest.raises(RuntimeError, match="NAVER_APP_PASSWORD"):
        by_name(tmp_path, memory)["mail_list"].run({})


def test_map_without_key_says_what_is_missing(tmp_path, memory):
    with pytest.raises(RuntimeError, match="KAKAO_REST_KEY"):
        by_name(tmp_path, memory)["map_search"].run({"query": "카페"})


def test_message_summary_decodes_korean_subject_and_body():
    message = EmailMessage()
    message["Subject"] = "회의 안내"
    message["From"] = "팀장 <a@example.com>"
    message["Date"] = "Mon, 02 Nov 2026 09:00:00 +0900"
    message.set_content("내일 10시입니다")
    parsed = message_summary(message.as_bytes(), include_body=True)
    assert parsed["subject"] == "회의 안내"
    assert "a@example.com" in parsed["from"]
    assert "내일 10시" in parsed["body"]


def test_message_summary_reads_html_only_mail():
    message = EmailMessage()
    message["Subject"] = "x"
    message.add_alternative("<p>본문<script>bad()</script></p>", subtype="html")
    body = message_summary(message.as_bytes(), include_body=True)["body"]
    assert "본문" in body and "bad" not in body


def test_html_to_text_removes_markup():
    assert html_to_text("<style>a{}</style><h1>제목</h1><p>내용</p>").split() == ["제목", "내용"]


def test_parse_search_results():
    page = '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fa.com%2F&x=1">제목 A</a>'
    assert parse_search_results(page) == [{"title": "제목 A", "url": "https://a.com/"}]


def test_format_places_keeps_only_useful_fields():
    text = format_places([{"place_name": "카페", "road_address_name": "서울 강남대로 1", "phone": "02-1", "place_url": "http://u", "x": "1"}])
    assert "카페" in text and "강남대로" in text and "http://u" in text and '"x"' not in text


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://localhost/", "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.1/", "file:///etc/passwd", "ftp://example.com/",
])
def test_private_addresses_are_blocked(url):
    assert is_public_url(url) is False


def test_read_and_memory_tools_do_not_ask_for_approval(tmp_path, memory):
    tools = by_name(tmp_path, memory)
    assert tools["memory_save"].needs_approval is False
    assert tools["mail_list"].needs_approval is False


def test_prompt_job_cannot_repeat_every_minute(tmp_path):
    with pytest.raises(ValueError):
        scheduler.add_job(tmp_path / "s.json", "prompt", "x", datetime.now().astimezone(), every_minutes=1)
    scheduler.add_job(tmp_path / "s.json", "message", "x", datetime.now().astimezone(), every_minutes=1)
