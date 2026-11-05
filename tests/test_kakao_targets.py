import json
import pytest
from agent.kakao_targets import KakaoTargets


def test_verified_targets_survive_restart_and_track_frequency(tmp_path):
    p=tmp_path/'targets.json';cache=KakaoTargets(p)
    cache.remember('friends', 'friends', 'group', 5)
    cache.remember('person', 'person', 'individual', None)
    cache.remember('friends', 'friends', 'group', 6)
    reopened=KakaoTargets(p)
    assert reopened.frequent()[0]['hits']==2
    assert reopened.get('friends','group')['members']==6
    assert reopened.get('friends','individual') is None
    assert set(reopened.get('friends','group'))=={'alias','title','kind','members','verified_at','hits'}


def test_expired_or_invalid_cache_is_not_used(tmp_path,monkeypatch):
    p=tmp_path/'targets.json';cache=KakaoTargets(p)
    cache.remember('person','person','individual',None)
    monkeypatch.setattr('agent.kakao_targets.time.time',lambda:10**12)
    assert cache.get('person','individual') is None and cache.frequent()==[]
    p.write_text('broken')
    assert cache.get('person','individual') is None


def test_group_requires_a_real_observed_count(tmp_path):
    cache=KakaoTargets(tmp_path/'targets.json')
    for count in [None,True,1,'5']:
        with pytest.raises(ValueError):cache.remember('room','room','group',count)
