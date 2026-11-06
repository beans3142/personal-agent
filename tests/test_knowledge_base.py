import json
from agent.knowledge_base import KnowledgeBase


def test_search_returns_provenance_status_and_excludes_private_for_public(tmp_path):
    p=tmp_path/'index.json';p.write_text(json.dumps({'entries':[
        {'id':'power','title':'서버 전원','body':'서버 꺼줘 명령은 전원 봇이 처리한다.','status':'verified','verified_at':'2026-11-06','source':'agent/power.py','visibility':'public'},
        {'id':'device','title':'서버 장비','body':'개인 장비 별칭','status':'partial','verified_at':'2026-11-06','source':'registry','visibility':'private'}]}),encoding='utf-8')
    kb=KnowledgeBase(p)
    assert {x['id'] for x in kb.search('서버',public=True)}=={'power'}
    assert {x['id'] for x in kb.search('서버',public=False)}=={'power','device'}
    result=kb.search('서버꺼줘')[0]
    assert result['source']=='agent/power.py' and result['status']=='verified'


def test_missing_kb_is_not_an_invented_answer(tmp_path):
    assert KnowledgeBase(tmp_path/'absent.json').search('아무거나')==[]
