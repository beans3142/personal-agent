from datetime import datetime, timedelta, timezone

import pytest




from agent import scheduler

NOW = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    return tmp_path / "schedule.json"


def test_add_list_and_remove(store):
    job = scheduler.add_job(store, "message", "물 마시기", NOW)
    assert [j["id"] for j in scheduler.load_jobs(store)] == [job["id"]]
    assert scheduler.remove_job(store, job["id"]) is True
    assert scheduler.load_jobs(store) == []
    assert scheduler.remove_job(store, job["id"]) is False


def test_rejects_unknown_kind_empty_text_and_bad_interval(store):
    with pytest.raises(ValueError):
        scheduler.add_job(store, "shell", "x", NOW)
    with pytest.raises(ValueError):
        scheduler.add_job(store, "message", "  ", NOW)
    with pytest.raises(ValueError):
        scheduler.add_job(store, "message", "x", NOW, every_minutes=0)


def test_future_job_does_not_run(store):
    scheduler.add_job(store, "message", "later", NOW + timedelta(minutes=1))
    sent = []
    assert scheduler.run_due(store, NOW, sent.append, lambda text: text) == 0
    assert sent == []


def test_one_time_message_runs_once_then_disappears(store):
    scheduler.add_job(store, "message", "now", NOW)
    sent = []
    assert scheduler.run_due(store, NOW, sent.append, lambda text: text) == 1
    assert scheduler.run_due(store, NOW, sent.append, lambda text: text) == 0
    assert sent == ["now"]
    assert scheduler.load_jobs(store) == []


def test_recurring_job_skips_missed_runs(store):
    scheduler.add_job(store, "message", "daily", NOW - timedelta(days=3), every_minutes=1440)
    sent = []
    scheduler.run_due(store, NOW + timedelta(minutes=1), sent.append, lambda text: text)
    assert sent == ["daily"]
    job = scheduler.load_jobs(store)[0]
    assert datetime.fromisoformat(job["next_at"]) == NOW + timedelta(days=1)


def test_prompt_job_sends_model_output(store):
    scheduler.add_job(store, "prompt", "요약해", NOW)
    sent = []
    scheduler.run_due(store, NOW, sent.append, lambda text: f"결과:{text}")
    assert sent == ["결과:요약해"]


def test_failed_send_keeps_job_for_retry(store):
    scheduler.add_job(store, "message", "retry", NOW)

    def fail(_text):
        raise OSError("network down")

    assert scheduler.run_due(store, NOW, fail, lambda text: text) == 0
    assert len(scheduler.load_jobs(store)) == 1


def test_one_failure_does_not_block_other_jobs(store):
    scheduler.add_job(store, "prompt", "bad", NOW)
    scheduler.add_job(store, "message", "good", NOW)
    sent = []

    def run_prompt(_text):
        raise RuntimeError("limit reached")

    assert scheduler.run_due(store, NOW, sent.append, run_prompt) == 1
    assert sent == ["good"]
    assert [j["text"] for j in scheduler.load_jobs(store)] == ["bad"]


def test_job_added_while_sending_is_kept(store):
    scheduler.add_job(store, "message", "first", NOW)

    def send(_text):
        scheduler.add_job(store, "message", "added", NOW + timedelta(hours=1))

    scheduler.run_due(store, NOW, send, lambda text: text)
    assert [j["text"] for j in scheduler.load_jobs(store)] == ["added"]


def test_corrupt_store_is_moved_aside(store):
    store.write_text("{broken", encoding="utf-8")
    assert scheduler.load_jobs(store) == []
    assert list(store.parent.glob("schedule.json.corrupt*"))


def test_long_text_is_split_for_discord():
    parts = scheduler.split_text("가" * 4500)
    assert len(parts) == 3
    assert all(len(part) <= 2000 for part in parts)
    assert "".join(parts) == "가" * 4500


def test_long_reply_does_not_split_a_short_metrics_line():
    footer = "-# 처리 26.6초 · 토큰 입력 5,995 / 출력 1,438 · 모델 호출 2회"
    text = "가" * 1980 + "\n\n" + footer
    parts = scheduler.split_text(text)
    assert parts[-1].endswith(footer)
    assert "".join(parts) == text
