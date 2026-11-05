import json
from types import SimpleNamespace
from agent.claude_text import ClaudeText, ChatProviders


def test_official_cli_has_no_tools_or_inherited_service_secrets(monkeypatch,tmp_path):
    monkeypatch.setenv('DISCORD_BOT_TOKEN','private')
    calls=[]
    def run(argv,**kw):
        calls.append(argv)
        assert kw['env']['CLAUDE_CODE_OAUTH_TOKEN']=='oauth'
        assert 'DISCORD_BOT_TOKEN' not in kw['env']
        assert kw['capture_output'] is True
        assert argv[argv.index('--tools')+1]==''
        return SimpleNamespace(returncode=0,stdout=json.dumps({'result':'DIRECT: 답','modelUsage':{'claude-test':{}},'usage':{'input_tokens':12,'output_tokens':3}}))
    cli=ClaudeText(run=run,token=lambda:'oauth',work=tmp_path)
    assert cli.ask('질문')=='DIRECT: 답'
    assert cli.last_metadata['model']=='claude-test'


def test_explicit_claude_request_does_not_spend_a_chatgpt_attempt():
    class P:
        def __init__(self,name):self.name=name;self.calls=[]
        def ask(self,p):self.calls.append(p);return 'DIRECT: 답'
    desktop=P('ChatGPT Chat');claude=P('Claude CLI');providers=ChatProviders(desktop,claude)
    assert providers.ask('요청: 클로드로 삼각형을 설명해줘')=='DIRECT: 답'
    assert desktop.calls==[] and len(claude.calls)==1
    assert providers.name=='Claude CLI'
