import json

from agent.login_automation import LoginImagePermission


def configured(tmp_path, active=lambda: True, request='trusted-request'):
    (tmp_path / 'login-policy.json').write_text(json.dumps({'naver': {'enabled': True, 'solver': 'desktop'}}))
    return LoginImagePermission(tmp_path, request, active)


def test_owner_delegated_naver_challenge_is_allowed_once_per_request(tmp_path):
    permission = configured(tmp_path)
    args = {'site': 'naver', 'solver': 'ChatGPT desktop'}
    assert permission('login_image_challenge', args)
    assert not permission('login_image_challenge', args)
    assert not LoginImagePermission(tmp_path, 'trusted-request', lambda: True)('login_image_challenge', args)
    assert LoginImagePermission(tmp_path, 'next-request', lambda: True)('login_image_challenge', args)


def test_lease_policy_and_scope_are_required_and_revocation_is_live(tmp_path):
    permission = configured(tmp_path)
    args = {'site': 'naver', 'solver': 'ChatGPT desktop'}
    assert not permission('other_action', args)
    assert not permission('login_image_challenge', {**args, 'site': 'google'})
    assert not permission('login_image_challenge', {**args, 'solver': 'other'})
    assert not LoginImagePermission(tmp_path, 'x', lambda: False)('login_image_challenge', args)
    assert not LoginImagePermission(tmp_path, None, lambda: True)('login_image_challenge', args)
    (tmp_path / 'login-policy.json').write_text('{"naver":{"enabled":false,"solver":"desktop"}}')
    assert not permission('login_image_challenge', args)
    assert not (tmp_path / 'login-automation-budget.json').exists()


def test_missing_or_invalid_policy_denies_without_any_budget_write(tmp_path):
    permission = LoginImagePermission(tmp_path, 'id', lambda: True)
    assert not permission.enabled('naver')
    (tmp_path / 'login-policy.json').write_text('invalid')
    assert not permission.enabled('naver')
    assert not permission('login_image_challenge', {'site': 'naver', 'solver': 'ChatGPT desktop'})


def test_failed_attempt_pauses_new_requests_until_explicit_owner_resume(tmp_path):
    permission = configured(tmp_path)
    assert permission.can_login('naver')
    permission.record_outcome('naver', 'captcha')
    another = LoginImagePermission(tmp_path, 'another-request', lambda: True)
    assert not another.can_login('naver')
    assert not another('login_image_challenge', {'site': 'naver', 'solver': 'ChatGPT desktop'})
    another.resume()
    assert another.can_login('naver')
