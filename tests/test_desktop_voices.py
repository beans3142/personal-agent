"""Character voices: local voice-clone server profiles, emotion choice and fallback."""
import json
from pathlib import Path

import pytest

from desktop import tts


def profile(tmp_path, name='아이리', emotions=('neutral', 'happy', 'sad')):
    folder = tmp_path / name
    folder.mkdir(parents=True)
    data = {'label': name + ' (캐릭터)', 'server': 'http://127.0.0.1:9880', 'lang': 'ko', 'emotions': {}}
    for e in emotions:
        (folder / f'{e}.wav').write_bytes(b'RIFF')
        data['emotions'][e] = {'ref': f'{e}.wav', 'text': f'{e} 기준 문장'}
    (folder / 'profile.json').write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    return folder


def test_profiles_become_voices_after_the_built_in_ones(tmp_path):
    profile(tmp_path)
    (tmp_path / 'broken').mkdir()
    (tmp_path / 'broken' / 'profile.json').write_text('{bad', encoding='utf-8')
    voices = tts.all_voices(tmp_path)
    assert [v.id for v in voices[:len(tts.VOICES)]] == [v.id for v in tts.VOICES]
    clone = voices[-1]
    assert clone.id == 'clone:아이리' and clone.engine == 'clone' and '캐릭터' in clone.label
    assert len(voices) == len(tts.VOICES) + 1


def test_remote_servers_are_refused(tmp_path):
    folder = profile(tmp_path)
    data = json.loads((folder / 'profile.json').read_text(encoding='utf-8'))
    data['server'] = 'http://example.com:9880'
    (folder / 'profile.json').write_text(json.dumps(data), encoding='utf-8')
    assert tts.all_voices(tmp_path) == tts.VOICES  # Answers never leave this PC for an unknown host.


@pytest.mark.parametrize('text,ok,emotion', [
    ('오늘 일정은 회의 하나예요.', True, 'neutral'),
    ('주문을 마쳤어요! 35분 뒤 도착해요.', True, 'happy'),
    ('죄송해요, 답을 받지 못했어요.', False, 'sad'),
    ('결제하려면 승인해 주세요.', True, 'serious'),
])
def test_emotion_follows_the_answer(text, ok, emotion):
    assert tts.emotion_for(text, ok) == emotion


def test_clone_request_uses_the_matching_reference_clip(tmp_path):
    folder = profile(tmp_path)
    sent, played = [], []
    def post(url, body):
        sent.append((url, body))
        return b'RIFFwav'
    speaker = tts.Speaker(edge=lambda *a: pytest.fail(), play=lambda p: played.append(Path(p).read_bytes()),
                          windows=lambda *a: pytest.fail(), folder=tmp_path / 'tmp', voices_dir=tmp_path, clone_post=post)
    (tmp_path / 'tmp').mkdir()
    assert speaker.speak('주문을 마쳤어요!', 'clone:아이리', 10) == 'clone'
    url, body = sent[0]
    assert url == 'http://127.0.0.1:9880/tts'
    assert body['ref_audio_path'] == str(folder / 'happy.wav') and body['prompt_text'] == 'happy 기준 문장'
    assert body['text'] == '주문을 마쳤어요!' and body['text_lang'] == 'ko' and body['speed_factor'] == 1.1
    assert played == [b'RIFFwav']


def test_missing_emotion_uses_neutral_and_server_failure_falls_back(tmp_path):
    profile(tmp_path, emotions=('neutral',))
    sent, spoken = [], []
    def post(url, body):
        sent.append(body['ref_audio_path'])
        raise OSError('server not running')
    speaker = tts.Speaker(edge=lambda text, voice, rate, path: (_ for _ in ()).throw(OSError('offline')), play=lambda p: None,
                          windows=lambda text, rate: spoken.append(text), folder=tmp_path, voices_dir=tmp_path, clone_post=post)
    assert speaker.speak('죄송해요, 실패했어요.', 'clone:아이리', 0) == 'windows'
    assert sent[0].endswith('neutral.wav') and spoken == ['죄송해요, 실패했어요.']
