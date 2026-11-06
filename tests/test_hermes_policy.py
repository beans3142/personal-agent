import importlib.util
import json
from pathlib import Path

import pytest


def guard(**kwargs):
    spec = importlib.util.spec_from_file_location('hermes_policy', Path(__file__).parents[1] / 'deploy/hermes_policy.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.TurnGuard(**kwargs)


def test_reported_aggregate_threshold_between_calls_and_latched_stop():
    policy = guard()
    assert policy.before_step(0) is None
    assert policy.before_step(59999) is None
    assert policy.before_step(60000) == 'input_token_limit'
    assert policy.before_step(100) == 'input_token_limit'


def test_threshold_is_configurable_and_missing_usage_is_not_fabricated():
    policy = guard(max_input_tokens=10)
    assert policy.before_step(None) is None
    assert policy.before_step(9) is None
    assert policy.before_step(11) == 'input_token_limit'


def test_three_same_action_errors_ignore_interleaved_metadata_success():
    policy = guard()
    assert policy.observe_tool('mcp__daevis__android_input', 'error: stale snapshot') is None
    assert policy.observe_tool('tool_describe', {'schema': {}}) is None
    assert policy.observe_tool('mcp__daevis__android_screen', '{"nodes":[]}') is None
    assert policy.observe_tool('mcp__daevis__android_input', 'error:  STALE   snapshot ') is None
    assert policy.observe_tool('tool_search', {'tools': []}) is None
    assert policy.observe_tool('mcp__daevis__android_input', 'error: stale snapshot') == 'repeated_snapshot_error'


def test_successful_action_resets_repeated_error_streak():
    policy = guard()
    assert policy.observe_tool('mail_send', 'error: failed send') is None
    assert policy.observe_tool('mail_send', 'error: failed send') is None
    assert policy.observe_tool('memory_search', []) is None
    assert policy.observe_tool('mail_send', 'error: failed send') is None
    assert policy.observe_tool('mail_send', 'error: failed send') is None
    assert policy.observe_tool('mail_send', 'error: failed send') == 'repeated_tool_error'


def test_different_errors_and_different_tools_are_distinct():
    policy = guard()
    for name, result in [('a', 'error: one'), ('a', 'error: two'), ('b', 'error: two'), ('b', 'error: two')]:
        assert policy.observe_tool(name, result) is None
    assert policy.observe_tool('b', 'error: two') == 'repeated_tool_error'


@pytest.mark.parametrize('result', [
    'denied: owner rejected', {'status': 'denied'}, {'error': 'denied: user said no'},
    json.dumps({'result': json.dumps({'result': 'denied: not approved'})}),
])
def test_owner_denial_stops_immediately_without_echoing_result(result):
    policy = guard()
    assert policy.observe_tool('mail_send', result) == 'owner_denied'
    assert policy.before_step(0) == 'owner_denied'


@pytest.mark.parametrize('result', [
    'error: secret value', 'blocked: secret value', {'error': 'secret value'},
    {'isError': True, 'content': [{'type': 'text', 'text': 'secret value'}]},
    {'status': 'error', 'message': 'secret value'}, {'status': 'failed'},
    {'status': 'uncertain'}, {'status': 'blocked'},
    json.dumps({'result': json.dumps({'error': 'secret value'})}),
])
def test_error_shapes_are_recognized_with_sanitized_reasons(result):
    policy = guard(max_repeated_errors=2)
    assert policy.observe_tool('mail_send', result) is None
    assert policy.observe_tool('mail_send', result) == 'repeated_tool_error'


def test_success_payload_containing_error_word_does_not_count_as_error():
    policy = guard(max_repeated_errors=1)
    assert policy.observe_tool('memory_search', {'records': ['an error happened yesterday']}) is None
    assert policy.observe_tool('tool_describe', 'How to detect error: prefixes') is None
    assert policy.observe_tool('mail_send', {'status': 'accepted'}) is None


def test_metadata_failures_do_not_erase_action_failures():
    policy = guard()
    assert policy.observe_tool('android_input', 'error: unavailable') is None
    assert policy.observe_tool('android_screen', 'error: snapshot unavailable') is None
    assert policy.observe_tool('android_input', 'error: unavailable') is None
    assert policy.observe_tool('tool_describe', 'error: no schema') is None
    assert policy.observe_tool('android_input', 'error: unavailable') == 'repeated_tool_error'


def test_repeated_snapshot_observation_errors_stop():
    policy = guard()
    for _ in range(2):
        assert policy.observe_tool('mcp__daevis__android_screen', 'error: cannot read') is None
    assert policy.observe_tool('mcp__daevis__android_screen', 'error: cannot read') == 'repeated_snapshot_error'


def test_metadata_success_does_not_hide_repeated_snapshot_failures():
    policy = guard()
    for _ in range(2):
        assert policy.observe_tool('android_screen', 'error: cannot read') is None
        assert policy.observe_tool('tool_describe', {'schema': {}}) is None
    assert policy.observe_tool('android_screen', 'error: cannot read') == 'repeated_snapshot_error'


def test_guard_retains_message_digest_without_private_error_text():
    policy = guard()
    assert policy.observe_tool('mail_send', {'error': 'private contact secret'}) is None
    assert 'private contact secret' not in repr(vars(policy))


@pytest.mark.parametrize('kwargs', [{'max_input_tokens': 0}, {'max_repeated_errors': 0}, {'max_repeated_errors': True}])
def test_invalid_limits_rejected(kwargs):
    with pytest.raises(ValueError):
        guard(**kwargs)


def test_model_usage_failure_is_classified_without_echoing_provider_text():
    spec=importlib.util.spec_from_file_location('hermes_policy',Path(__file__).parents[1]/'deploy/hermes_policy.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    assert module.model_failure_reason({'completed':False,'error':'HTTP429: The usage limit has been reached'})=='usage_limit_reached'
    assert module.model_failure_reason({'completed':True,'final_response':'usage_limit_reached is an error code'}) is None
