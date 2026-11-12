from agent.hermes_runner import prompt_cache_scope


def test_prompt_cache_scope_is_stable_but_separates_authority():
    base={'OWNER_ID':'test-owner','AGENT_MODEL':'model-a','DAEVIS_MCP_ALLOW_TOOLS':'b,a'}
    a=prompt_cache_scope(base,False,True,'dm:test')
    assert a==prompt_cache_scope({**base,'DAEVIS_MCP_ALLOW_TOOLS':'a,b'},False,True,'dm:test')
    assert a!=prompt_cache_scope(base,True,True,'dm:test')
    assert a!=prompt_cache_scope(base,False,False,'dm:test')
    assert a!=prompt_cache_scope(base,False,True,'dm:other')
    assert a!=prompt_cache_scope({**base,'OWNER_ID':'other'},False,True,'dm:test')
    assert a!=prompt_cache_scope({**base,'AGENT_MODEL':'other'},False,True,'dm:test')
    assert a!=prompt_cache_scope({**base,'DAEVIS_MCP_ALLOW_TOOLS':'a'},False,True,'dm:test')
    assert a!=prompt_cache_scope({**base,'DAEVIS_MCP_APPROVAL':'discord'},False,True,'dm:test')
    assert 'test-owner' not in a and 'dm:test' not in a
    assert prompt_cache_scope(base,False,True,None) is None


def test_cache_usage_is_reported_without_being_added_to_input_again():
    import json
    from agent.hermes_runner import parse_events
    value=parse_events(json.dumps({'type':'result','completed':True,'text':'ok','input_tokens':1000,'output_tokens':10,'cache_read_tokens':800,'cache_write_tokens':0}))
    assert value.cache_read_tokens==800 and value.cache_write_tokens==0
    assert value.input_tokens==1000
