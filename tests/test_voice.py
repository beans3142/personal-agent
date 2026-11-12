import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest

from agent.device_nodes import DeviceStore
from agent.pc_control import PCRegistry


def registry(tmp_path, clock=lambda: 1000):
    r = PCRegistry(tmp_path / 'pc.sqlite3', clock=clock)
    pc = r.redeem(r.invite('데스크톱'))
    r.ensure_voice()
    return r, pc


# --- relay storage ----------------------------------------------------------

def test_pc_may_send_only_voice_text_to_the_voice_relay(tmp_path):
    r, pc = registry(tmp_path)
    job = r.request(pc['node'], pc['token'], 'voice', 'voice_command', {'text': '오늘 날씨 어때'}, 'v1', 300)
    assert job['status'] == 'queued'
    for action, payload in [('pc_shutdown', {}), ('voice_command', {'text': ''}), ('voice_command', {'text': 'a' * 501}),
                            ('voice_command', {'text': 'x', 'extra': 1})]:
        with pytest.raises((ValueError, PermissionError)):
            r.request(pc['node'], pc['token'], 'voice', action, payload, 'v-' + action + str(len(payload)), 300)


def test_pcs_registered_later_are_also_allowed_to_speak(tmp_path):
    r, _ = registry(tmp_path)
    laptop = r.redeem(r.invite('노트북'))
    assert r.request(laptop['node'], laptop['token'], 'voice', 'voice_command', {'text': '안녕'}, 'v2', 300)['status'] == 'queued'


def test_issuer_sees_progress_and_final_reply_on_its_receipt(tmp_path):
    r, pc = registry(tmp_path)
    job = r.request(pc['node'], pc['token'], 'voice', 'voice_command', {'text': '안녕'}, 'v3', 300)
    [taken] = r.voice_jobs()
    assert taken['id'] == job['id'] and taken['payload'] == {'text': '안녕'} and taken['source'] == pc['node']
    assert r.voice_jobs() == []
    r.voice_progress(job['id'], {'say': '서버를 켜고 있어요.'})
    status = r.request_status(pc['node'], pc['token'], job['id'])
    assert status['status'] == 'dispatched' and status['result'] == {'say': '서버를 켜고 있어요.'}
    r.voice_finish(job['id'], 'succeeded', {'reply': '안녕하세요.'})
    assert r.request_status(pc['node'], pc['token'], job['id'])['result'] == {'reply': '안녕하세요.'}


def test_agent_node_is_enrolled_once_and_receives_forwarded_text(tmp_path):
    now = [1000.0]
    r, pc = registry(tmp_path, clock=lambda: now[0])
    token = r.enroll_agent()
    with pytest.raises(ValueError):
        r.enroll_agent()
    assert not r.agent_online()
    forwarded = r.forward_to_agent('오늘 일정 알려줘', 'agent-v1')
    job = r.poll('agent-main', token)
    assert job['id'] == forwarded['id'] and job['action'] == 'voice_command' and job['payload'] == {'text': '오늘 일정 알려줘'}
    assert r.agent_online()
    r.ack('agent-main', token, job['id'], 'succeeded', {'reply': '오늘은 일정이 없어요.'})
    assert r.job_result(forwarded['id'])['result'] == {'reply': '오늘은 일정이 없어요.'}


# --- relay behaviour --------------------------------------------------------

class FakeRegistry:
    def __init__(self, online=True, agent_results=()):
        self.online, self.agent_results = online, list(agent_results)
        self.finished, self.progress, self.forwarded = [], [], []
    def voice_finish(self, job_id, status, result): self.finished.append((job_id, status, result))
    def voice_progress(self, job_id, result): self.progress.append(result)
    def agent_online(self): return self.online
    def forward_to_agent(self, text, key, via='voice'): self.forwarded.append(text); return {'id': 'a1'}
    def job_result(self, job_id): return self.agent_results.pop(0) if self.agent_results else {'status': 'dispatched', 'result': None}


def bot_with(reg, dm):
    owner = SimpleNamespace(create_dm=AsyncMock(return_value=dm))
    bot = SimpleNamespace(pc_registry=reg, owner_id=1, fetch_user=AsyncMock(return_value=owner),
                          control=SimpleNamespace(start=lambda: 'started'), handle_phone=AsyncMock(), handle_pc=AsyncMock(), handle=AsyncMock())
    return bot


def run_relay(bot, text):
    from agent.voice_relay import handle_voice_job
    asyncio.run(handle_voice_job(bot, {'id': 'j1', 'source': 'pc-1', 'payload': {'text': text}}, sleep=AsyncMock()))


def test_rule_commands_run_on_the_power_bot_and_speak_what_it_posted():
    dm = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=7)))
    reg = FakeRegistry()
    bot = bot_with(reg, dm)
    async def handle_phone(message, action, value):
        await message.channel.send('📞 **엄마**(010-****-5678)에게 전화를 걸었어요.')
    bot.handle_phone = handle_phone
    run_relay(bot, '엄마한테 전화 걸어줘')
    job_id, status, result = reg.finished[0]
    assert status == 'succeeded' and result['reply'] == '엄마(010-****-5678)에게 전화를 걸었어요.'
    assert reg.forwarded == []
    assert '🎤' in dm.send.await_args_list[0].args[0]  # The DM records what was heard.


def test_questions_start_the_server_when_needed_then_relay_the_answer():
    dm = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=7)))
    reg = FakeRegistry(online=False, agent_results=[{'status': 'dispatched', 'result': None},
                                                    {'status': 'succeeded', 'result': {'reply': '내일은 맑아요.'}}])
    started = []
    bot = bot_with(reg, dm)
    bot.control = SimpleNamespace(start=lambda: started.append(1) or 'started')
    run_relay(bot, '내일 날씨 어때')
    assert started and reg.progress and '켜' in reg.progress[0]['say']
    assert reg.forwarded == ['내일 날씨 어때']
    assert reg.finished[0][1:] == ('succeeded', {'reply': '내일은 맑아요.'})


def test_running_server_is_not_started_again():
    dm = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=7)))
    reg = FakeRegistry(online=True, agent_results=[{'status': 'succeeded', 'result': {'reply': '네.'}}])
    bot = bot_with(reg, dm)
    bot.control = SimpleNamespace(start=lambda: pytest.fail('must not start'))
    run_relay(bot, '안녕')
    assert reg.progress == [] and reg.finished[0][2] == {'reply': '네.'}


def test_unanswered_question_ends_with_a_spoken_failure():
    dm = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=7)))
    reg = FakeRegistry(online=True, agent_results=[{'status': 'uncertain', 'result': None}])
    run_relay(bot_with(reg, dm), '안녕')
    assert reg.finished[0][1] == 'failed' and '답' in reg.finished[0][2]['reply']


# --- main agent side ----------------------------------------------------------

def test_spoken_reply_drops_footer_markdown_and_links():
    from agent.voice_agent import spoken
    reply = "**지라시스시** 추천해요.\n- [메뉴](https://x.y) 12,900원\n\n-# 처리 3.1초 · 모델 호출 1회"
    text = spoken(reply)
    assert text == '지라시스시 추천해요. 메뉴 12,900원'
    assert len(spoken('가' * 1000)) <= 300


def test_voice_turn_answers_in_the_owner_dm_and_returns_speech():
    from agent.voice_agent import voice_turn
    dm = SimpleNamespace(id=55, send=AsyncMock(), typing=None)
    gateway = SimpleNamespace(owner_dm=AsyncMock(return_value=dm), turn_lock=asyncio.Lock(),
                              turn=lambda chat, text, approve: '**좋아요**\n\n-# 처리 1초', approver=lambda channel: None,
                              send_long=AsyncMock(), send_outbox=AsyncMock(), watch_approvals=AsyncMock())
    result = asyncio.run(voice_turn(gateway, '안녕'))
    assert result == {'reply': '좋아요', 'text': '**좋아요**\n\n-# 처리 1초'}
    sent = gateway.send_long.await_args.args[1]
    assert sent.startswith('🎤 안녕') and '좋아요' in sent


def test_agent_poller_acknowledges_each_job_with_its_reply():
    from agent.voice_agent import poll_once
    posts = []
    def post(endpoint, body):
        posts.append((endpoint, body))
        return {'job': {'id': 'a1', 'action': 'voice_command', 'payload': {'text': '안녕'}, 'expires': 9e9}} if endpoint == '/poll' else {'ok': True}
    async def answer(text, via, report, topic=None):
        return {'reply': '네, ' + text}
    asyncio.run(poll_once(post, answer, 'agent-main'))
    assert posts[-1] == ('/ack', {'node': 'agent-main', 'id': 'a1', 'status': 'succeeded', 'result': {'reply': '네, 안녕'}})


# --- PC client ----------------------------------------------------------------

def client():
    spec = importlib.util.spec_from_file_location('voice_pc', Path(__file__).parents[1] / 'deploy/voice-pc.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("heard,expected", [
    ("대비스 오늘 날씨 어때?", "오늘 날씨 어때?"),
    ("데비스, 엄마한테 전화 걸어줘", "엄마한테 전화 걸어줘"),
    ("대비스.", ""),
    ("자비스 PC 꺼줘", "PC 꺼줘"),
    ("오늘 대비스 얘기를 했어", None),
    ("다비스 안녕", None),
    ("재비스 안녕", None),
    ("데비스 안녕", "안녕"),
    ("데이비스 지금 몇 시냐?", "지금 몇 시냐?"),
])
def test_wake_word_must_start_the_utterance(heard, expected):
    assert client().after_wake(heard) == expected


def test_segmenter_returns_one_utterance_after_trailing_silence():
    voice = client()
    seg = voice.Segmenter(rate=16000, frame=480)
    quiet, loud = np.zeros(480, np.float32) + 0.001, np.full(480, 0.2, np.float32)
    frames = [quiet] * 30 + [loud] * 40 + [quiet] * 40
    done = [u for f in frames if (u := seg.feed(f)) is not None]
    assert len(done) == 1 and 0.6 < len(done[0]) / 16000 < 1.8


def test_request_speaks_progress_once_and_then_the_reply():
    voice = client()
    said, states = [], iter([{'status': 'dispatched', 'result': {'say': '서버를 켜고 있어요.'}},
                             {'status': 'dispatched', 'result': {'say': '서버를 켜고 있어요.'}},
                             {'status': 'succeeded', 'result': {'reply': '내일은 맑아요.'}}])
    def post(endpoint, body):
        return {'id': 'r1', 'status': 'queued'} if endpoint == '/request' else next(states)
    voice.ask(post, 'pc-1', '내일 날씨', said.append, sleep=lambda s: None)
    assert said == ['서버를 켜고 있어요.', '내일은 맑아요.']


def test_speech_text_reaches_powershell_through_the_environment_only():
    voice = client()
    argv, env = voice.speech_command("'; Remove-Item C:\\ -Recurse; '")
    assert "Remove-Item" not in " ".join(argv) and env['DAEVIS_SAY'].startswith("';")


def test_prompt_echo_from_noise_is_ignored_but_real_requests_pass():
    voice = client()
    assert voice.echoes_prompt('대비스, 카톡 보내줘') and voice.echoes_prompt('PC 꺼줘')
    assert not voice.echoes_prompt('대비스, 엄마에게 카톡 보내줘') and not voice.echoes_prompt('')


def test_bare_wake_word_is_not_treated_as_prompt_echo():
    assert not client().echoes_prompt('대비스')


def test_wake_word_must_come_from_the_unhinted_pass():
    voice = client()
    assert voice.choose_command('내 배에 딱', '대비스 내 배에 딱') is None
    assert voice.choose_command('대비스 엄마한테 카톡 보내줘', '대비스, 엄마한테 카톡 보내줘') == '엄마한테 카톡 보내줘'
    assert voice.choose_command('대비스 내 데스크톱 거죠', '대비스, 내 데스크톱 꺼줘') == '내 데스크톱 꺼줘'


@pytest.mark.parametrize("command,ok", [("사고", False), ("켜줘", False), ("PC 꺼줘", True), ("엄마한테 전화", True), ("", False)])
def test_too_short_commands_are_ignored(command, ok):
    assert client().usable(command) is ok


def test_command_hint_no_longer_contains_the_wake_word():
    assert '대비스' not in client().PROMPT


def test_hint_words_alone_are_treated_as_echo():
    voice = client()
    assert voice.echoes_prompt('카톡 보내줘, 켜줘') and voice.echoes_prompt('대비스, 카톡 보내줘, 켜줘')
    assert not voice.echoes_prompt('대비스, 엄마에게 카톡 보내줘')


def test_quiet_microphone_is_boosted_but_never_clipped_beyond_range():
    voice = client()
    quiet = np.sin(np.linspace(0, 200, 16000)).astype(np.float32) * 0.01
    loud = voice.boost(quiet)
    assert abs(float(np.abs(loud).max()) - 0.4) < 0.01  # 40x cap on a 0.01 peak
    normal = np.full(100, 0.6, np.float32)
    assert voice.boost(normal) is normal
    assert voice.boost(np.zeros(10, np.float32)).max() == 0


def test_quiet_speech_above_room_noise_is_still_segmented():
    voice = client()
    seg = voice.Segmenter(rate=16000, frame=480)
    room, speech = np.zeros(480, np.float32) + 0.0004, np.full(480, 0.004, np.float32)
    frames = [room] * 40 + [speech] * 40 + [room] * 40
    done = [u for f in frames if (u := seg.feed(f)) is not None]
    assert len(done) == 1


def test_voice_turn_adds_download_links_for_files_to_the_app_receipt(tmp_path):
    import json
    from agent.voice_agent import voice_turn, RESULT_LIMIT
    from agent.artifacts import current_outbox
    dm = SimpleNamespace(id=55, send=AsyncMock(), typing=None)
    def turn(chat, text, approve):
        folder = current_outbox(tmp_path) / 'files'
        folder.mkdir(parents=True)
        (folder / '발표.html').write_bytes(b'x')
        return '보냈어요. ' + '가' * 3000
    gateway = SimpleNamespace(owner_dm=AsyncMock(return_value=dm), turn_lock=asyncio.Lock(), turn=turn,
                              approver=lambda channel: None, send_long=AsyncMock(), send_outbox=AsyncMock(), watch_approvals=AsyncMock())
    shared = []
    def share(path):
        shared.append(path.name)
        return 'https://relay.example/share/' + 'a' * 43
    result = asyncio.run(voice_turn(gateway, '발표자료 보내줘', share=share))
    assert shared == ['발표.html'] and gateway.send_outbox.await_count == 1
    assert result['text'].endswith('📎 발표.html: https://relay.example/share/' + 'a' * 43)
    assert len(json.dumps(result, ensure_ascii=False)) <= RESULT_LIMIT


def test_gradual_quiet_speech_is_not_learned_as_room_noise():
    voice = client()
    seg = voice.Segmenter()
    levels = [.0004] * 40 + list(np.linspace(.0004, .004, 30)) + [.004] * 40 + [.0004] * 40
    utterances = [u for level in levels if (u := seg.feed(np.full(480, level, np.float32))) is not None]
    assert len(utterances) == 1
    assert len(utterances[0]) / 16000 > 1


def test_steady_low_room_noise_never_starts_an_utterance():
    voice = client()
    seg = voice.Segmenter()
    assert all(seg.feed(np.full(480, .0006, np.float32)) is None for _ in range(500))


def test_segmenter_keeps_quiet_leading_syllable_before_trigger():
    voice = client()
    seg = voice.Segmenter()
    frames = [np.full(480, .0004, np.float32)] * 40
    # A 240ms quiet first syllable, then the louder remainder of the wake word.
    frames += [np.full(480, .0012, np.float32)] * 8
    frames += [np.full(480, .03, np.float32)] * 20
    frames += [np.full(480, .0004, np.float32)] * 40
    result = [u for f in frames if (u := seg.feed(f)) is not None]
    assert len(result) == 1
    assert np.count_nonzero(np.isclose(result[0], .0012)) == 8 * 480


def test_segmenter_keeps_a_short_pause_inside_one_request():
    voice = client()
    seg = voice.Segmenter()
    room = np.full(480, .0004, np.float32)
    speech = np.full(480, .03, np.float32)
    frames = [room] * 40 + [speech] * 20 + [room] * 22 + [speech] * 30 + [room] * 40
    result = [u for f in frames if (u := seg.feed(f)) is not None]
    assert len(result) == 1


def test_agent_poller_uses_long_poll_when_requested():
    from agent.voice_agent import poll_once
    posts = []
    def post(endpoint, body):
        posts.append((endpoint, body))
        return {'job': None}
    async def answer(*args):
        pytest.fail('no job to answer')
    assert asyncio.run(poll_once(post, answer, 'agent-main', wait=20)) is False
    assert posts == [('/agent/poll', {'node': 'agent-main', 'wait': 20})]
