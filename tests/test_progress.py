import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from agent.progress import ProgressMessage, read_steps, record, render


def test_record_keeps_tool_and_app_only(tmp_path):
    record(tmp_path, "android_open", {"app": "coupang"})
    record(tmp_path, "kakao_send", {"recipient": "private-name", "text": "private-text"})
    steps = read_steps(tmp_path)
    assert steps == [{"tool": "android_open", "app": "coupang"}, {"tool": "kakao_send"}]
    raw = (tmp_path / "progress.jsonl").read_text(encoding="utf-8")
    assert "private" not in raw


def test_record_without_outbox_is_noop():
    record(None, "web_search", {})


def test_render_shows_only_the_current_step_with_app():
    steps = [{"tool": "android_open", "app": "baemin"}, {"tool": "android_click"}, {"tool": "android_swipe"}]
    assert render(steps) == "⏳ 화면 넘기는 중… · 배민"


def test_render_without_steps_says_thinking():
    assert render([]) == "⏳ 요청을 이해하는 중…"


def test_render_has_no_history_or_timer():
    from agent.progress import LABELS
    text = render([{"tool": name} for name in list(LABELS)[:12]])
    assert "\n" not in text and "✓" not in text and "초" not in text


def test_app_name_is_shown_only_for_phone_steps():
    assert render([{"tool": "android_open", "app": "baemin"}, {"tool": "web_search"}]) == "⏳ 웹 검색하는 중…"


def test_unknown_tool_has_generic_label():
    assert render([{"tool": "mystery"}]) == "⏳ 작업하는 중…"


def channel():
    status = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock(), id=77)
    return SimpleNamespace(send=AsyncMock(return_value=status)), status


def test_progress_edits_only_when_text_changes(tmp_path):
    async def run():
        ch, status = channel()
        clock = [0.0]
        progress = ProgressMessage(ch, tmp_path, clock=lambda: clock[0])
        await progress.start()
        await progress.refresh()
        assert status.edit.await_count == 0
        record(tmp_path, "web_search", {})
        await progress.refresh()
        clock[0] = 30.0  # Time passing alone never triggers an edit.
        await progress.refresh()
        assert status.edit.await_count == 1
        assert "웹 검색" in status.edit.await_args.kwargs["content"]
    asyncio.run(run())


def test_finish_turns_status_into_answer_and_sends_overflow(tmp_path):
    async def run():
        ch, status = channel()
        progress = ProgressMessage(ch, tmp_path)
        await progress.start()
        sent = await progress.finish("a" * 2500, fallback=AsyncMock())
        assert status.edit.await_args.kwargs["content"] == "a" * 2000
        assert ch.send.await_count == 2 and sent[0] is status
    asyncio.run(run())


def test_finish_falls_back_when_status_could_not_be_sent(tmp_path):
    async def run():
        ch, _ = channel()
        ch.send.side_effect = RuntimeError("no permission")
        progress = ProgressMessage(ch, tmp_path)
        await progress.start()
        fallback = AsyncMock(return_value=["x"])
        assert await progress.finish("answer", fallback=fallback) == ["x"]
        fallback.assert_awaited_once_with(ch, "answer")
    asyncio.run(run())


def test_discard_removes_status_message(tmp_path):
    async def run():
        ch, status = channel()
        progress = ProgressMessage(ch, tmp_path)
        await progress.start()
        await progress.discard()
        status.delete.assert_awaited_once()
    asyncio.run(run())
