import json
from types import SimpleNamespace
import pytest
from agent.desktop_text import DesktopText


def test_general_chat_worker_excludes_credentials_and_limits_input(monkeypatch):
    monkeypatch.setenv('NAVER_PASSWORD','secret')
    def run(argv, **kw):
        assert argv[-2:] == ['agent.desktop_text','--ask']
        assert 'NAVER_PASSWORD' not in kw['env']
        assert json.loads(kw['input']) == {'prompt':'일반 질문'}
        return SimpleNamespace(returncode=0,stdout=json.dumps({'text':'DIRECT: 답변'}))
    assert DesktopText(run=run).ask('일반 질문') == 'DIRECT: 답변'
    with pytest.raises(ValueError): DesktopText(run=run).ask('x'*12001)


def test_engine_does_not_preempt_the_general_chat_lane():
    from agent.gateway import Gateway
    saved=[]
    bot=SimpleNamespace(memory=SimpleNamespace(load_history=lambda _:[],save_history=lambda _,h:saved.extend(h)),
        android=None,engine=SimpleNamespace(run=lambda *a,**k:pytest.fail('engine called')),
        quick_lane=SimpleNamespace(try_answer=lambda _: '짧은 답',name='ChatGPT Chat'))
    output=Gateway.turn(bot,'dm:1','삼각형의 내각 합은?',lambda *_:False)
    assert output.startswith('짧은 답') and '토큰 미제공' in output
    assert 'ChatGPT Chat' in output
    assert saved[-1]['content']=='짧은 답'
