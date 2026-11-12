from datetime import datetime, timezone
import pytest
from agent.simple_reply import simple_reply

NOW = datetime(2026, 11, 12, 15, 7, tzinfo=timezone.utc)

@pytest.mark.parametrize('query', ['지금 몇 시야?', '대비스, 지금 몇시야', '현재 시간 알려줘'])
def test_clock_uses_korea_time(query):
    assert simple_reply(query, now=NOW, enabled=True) == '지금 한국 시간으로 오전 12시 7분이에요.'

@pytest.mark.parametrize('query', ['오늘 날짜 알려줘', '오늘 며칠이야?'])
def test_date_crosses_midnight(query):
    assert simple_reply(query, now=NOW, enabled=True) == '오늘은 2026년 11월 13일 금요일이에요.'

@pytest.mark.parametrize('query', ['오늘 무슨 요일이야', '오늘 요일 알려줘'])
def test_weekday(query):
    assert simple_reply(query, now=NOW, enabled=True) == '오늘은 금요일이에요.'

@pytest.mark.parametrize('query', ['내일 몇 시에 출발해?', '오늘 날씨 알려줘', '오늘 날짜를 메일로 보내줘', '지금 몇 시야라고 카톡 보내줘', '지금 몇 시야? 그리고 일정 알려줘', '한국 말고 미국 지금 몇시야'])
def test_only_exact_read_only_intents_are_handled(query):
    assert simple_reply(query, now=NOW, enabled=True) is None


def test_ab_switch_preserves_the_old_path():
    assert simple_reply('지금 몇 시야', now=NOW, enabled=False) is None


def test_clock_is_read_again_instead_of_caching_the_answer():
    later = NOW.replace(minute=8)
    assert simple_reply('지금 몇시야', now=NOW, enabled=True) != simple_reply('지금 몇시야', now=later, enabled=True)

