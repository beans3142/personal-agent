from datetime import datetime

from agent.transit import KST, candidate_key, choose_candidates, compact_detail, upcoming_timetable


def test_discord_routes_keep_decision_facts_without_raw_panel_dump():
    from agent.transit import format_routes
    route = {'reasons': ['빠른 도착'], 'summary': '1시간1분\n오후 12:11 도착\n1,950원',
        'detail': '환승 1회도보 2분1,950원\n11:10 출발\n빠른환승: 7-2\n빠른하차: 2-4\n' + '중간역 정보 ' * 100}
    reply = format_routes({'routes': [route, route, route]})
    assert '오후 12:11 도착' in reply and '환승 1회' in reply and '도보 2분' in reply
    assert '11:10 출발' in reply and '7-2' in reply and '2-4' in reply
    assert '중간역 정보' not in reply and len(reply) < 600


def test_station_line_suggestions_collapse_only_for_identical_address():
    from types import SimpleNamespace
    import pytest
    from agent.transit import _place
    clicked = []
    texts = ['장소\n지하철,전철\n선릉역 2호선\n서울 강남구 테헤란로 340',
             '장소\n지하철,전철\n선릉역 수인분당선\n서울 강남구 테헤란로 340']
    options = SimpleNamespace(first=SimpleNamespace(wait_for=lambda **k: None),
        all_inner_texts=lambda: texts,
        nth=lambda i: SimpleNamespace(click=lambda: clicked.append(i)))
    page = SimpleNamespace(locator=lambda _: options, get_by_role=lambda *a, **k:
        SimpleNamespace(fill=lambda _: None, input_value=lambda: '선릉역 2호선'))
    assert _place(page, '도착지 입력', '선릉역') == '선릉역 2호선'
    assert clicked == [0]
    texts[1] = texts[1].replace('340', '999')
    with pytest.raises(RuntimeError):
        _place(page, '도착지 입력', '선릉역')
    assert clicked == [0]


def test_three_criteria_deduplicate_then_fill_with_another_fast_route():
    fast = ["최적\n1시간4분\n오후 5:10 도착", "1시간21분\n오후 5:23 도착"]
    walk = "최소시간 · 최소도보\n1시간1분\n오후 5:29 도착"
    selected = choose_candidates(fast, fast[0], walk)
    assert len(selected) == 3
    assert selected[0]["reasons"] == ["빠른 도착", "최소 환승"]
    assert selected[1]["summary"] == walk
    assert candidate_key("최적\nA") == candidate_key("A")


def test_detail_keeps_fast_boarding_but_omits_intermediate_station_list():
    text = "환승 1회\n빠른환승: 8-4\n8개 역 이동\n강매역\n수색역\n시간표\n빠른하차: 3-3, 5-4"
    result = compact_detail(text)
    assert "8-4" in result and "3-3, 5-4" in result
    assert "수색역" not in result


def test_timetable_retains_direction_and_only_next_three_scheduled_trains():
    rows = [["능곡 방면", "강매 방면"], ["15:46\n용문 문산", "15:56\n문산 팔당"],
            ["16:14\n덕소 문산", "16:06\n문산 용문"], ["16:25", "16:20"], ["16:34", "16:28 급"], ["16:44", "16:44"]]
    result = upcoming_timetable(rows, datetime(2026, 11, 2, 16, 0, tzinfo=KST))
    assert result[1]["direction"] == "강매 방면"
    assert result[1]["departures"] == ["16:06 문산 용문", "16:20", "16:28 급"]
    assert "15:56" not in str(result)


def test_after_midnight_departures_are_sorted_before_morning_trains():
    rows = [["동쪽"], ["05:00"], ["05:15"], ["05:30"], ["00:40"], ["00:50"]]
    result = upcoming_timetable(rows, datetime(2026, 11, 3, 0, 30, tzinfo=KST))
    assert result[0]["departures"] == ["00:40", "00:50", "05:00"]
