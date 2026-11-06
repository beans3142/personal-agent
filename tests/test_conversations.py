from agent.conversations import Conversations


def test_related_followup_persists_but_new_subject_is_separate(tmp_path):
    path=tmp_path/'topics.sqlite3';store=Conversations(path)
    first=store.resolve('dm','트렌드 보고서 쉽게 설명해줘')
    store.append(first,'user','트렌드 보고서 쉽게 설명해줘');store.append(first,'assistant','첫 설명입니다.')
    again=Conversations(path).resolve('dm','좀 더 풀어서 설명해줘')
    assert again['id']==first['id'] and len(store.history(again))==2
    other=store.resolve('dm','내 PC 전원 상태 확인해줘',decide=lambda *_:{'decision':'new','title':'PC 전원'})
    assert other['id']!=first['id'] and not store.history(other)


def test_public_context_cannot_read_private_dm_and_dm_forks_public(tmp_path):
    store=Conversations(tmp_path/'topics.sqlite3')
    public=store.resolve('guild:1','공개 기술 질문');store.append(public,'assistant','공개 답변')
    private=store.resolve('dm','공개 질문 이어서',decide=lambda *_:{'decision':'continue','topic_id':public['id']})
    store.append(private,'user','비공개 개인 내용')
    assert private['id']!=public['id'] and len(store.history(private))==2
    assert all('비공개' not in m['content'] for m in store.history(public))
    assert private['id'] not in [r['id'] for r in store.candidates('guild:1')]


def test_expiry_purges_messages_and_ambiguity_does_not_execute(tmp_path):
    now=[1000.0];store=Conversations(tmp_path/'topics.sqlite3',clock=lambda:now[0])
    first=store.resolve('dm','첫 작업');store.append(first,'user','기록')
    choice=store.resolve('dm','그 작업은?',decide=lambda *_:{'decision':'choose'})
    assert choice['needs_choice'] and choice['choices']
    now[0]+=31*86400
    assert not store.candidates('dm') and not store.history(first)


def test_search_finds_older_topics_without_exposing_dm_to_public(tmp_path):
    store=Conversations(tmp_path/'topics.sqlite3')
    private=store.create('dm','비공개 가입 정보');store.append(private,'user','private marker')
    public=store.create('guild:1','공개 질문');store.append(public,'user','public marker')
    assert not store.search('guild:1','private')
    assert not store.scoped_history('guild:1',private['id'])
    assert store.search('dm','public') and store.search('dm','private')


def test_private_fork_does_not_refresh_original_message_retention(tmp_path):
    now=[1000.0];store=Conversations(tmp_path/'topics.sqlite3',clock=lambda:now[0])
    public=store.create('guild:1','질문');store.append(public,'user','오래된 원문')
    now[0]+=29*86400
    private=store.resolve('dm','이어서',decide=lambda *_:{'decision':'continue','topic_id':public['id']})
    now[0]+=2*86400
    assert not store.history(private)
