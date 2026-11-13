import json
import pytest
from agent.pc_control import PCRegistry
from agent.phone_secrets import PhoneSecrets


@pytest.fixture
def vault(tmp_path):
    reg = PCRegistry(tmp_path / 'relay.sqlite3', clock=lambda: 100)
    token = reg.enroll('phone-test', ['phone_task'])
    reg.enroll('agent-main', ['voice_command'])
    with reg._db() as db:
        db.execute("INSERT INTO phones(alias,label,node) VALUES('phone','Test','phone-test')")
    job = reg.enqueue('phone-test', 'phone_task', {'op': 'screen', 'args': {}}, 'test', 60)
    with reg._db() as db:
        db.execute("UPDATE jobs SET status='dispatched',issuer='agent-main' WHERE id=?", (job['id'],))
    return reg, PhoneSecrets(reg, lambda node: 'example-test-password'), token, job['id']


def test_secret_only_for_registered_phone_running_agent_job(vault):
    reg, service, token, job = vault
    assert service.release('phone-test', token, job) == {'password': 'example-test-password'}
    with reg._db() as db:
        assert 'example-test-password' not in json.dumps([dict(r) for r in db.execute('SELECT * FROM jobs')])


def test_one_release_per_job_and_rate_limit_across_jobs(vault):
    reg, service, token, job = vault
    service.release('phone-test', token, job)
    with pytest.raises(PermissionError):
        service.release('phone-test', token, job)
    with reg._db() as db:
        db.execute("UPDATE phone_unlock_attempts SET attempted=0")
    with pytest.raises(PermissionError):
        service.release('phone-test', token, job)


@pytest.mark.parametrize('change', ['queued', 'expired', 'wrong_issuer', 'unregistered'])
def test_disallows_non_active_or_untrusted_jobs(vault, change):
    reg, service, token, job = vault
    with reg._db() as db:
        if change == 'queued': db.execute("UPDATE jobs SET status='queued'")
        if change == 'expired': db.execute('UPDATE jobs SET expires=99')
        if change == 'wrong_issuer': db.execute("UPDATE jobs SET issuer='other'")
        if change == 'unregistered': db.execute('DELETE FROM phones')
    with pytest.raises(PermissionError):
        service.release('phone-test', token, job)


def test_bad_token_and_other_phone_cannot_get_secret(vault):
    reg, service, token, job = vault
    with pytest.raises(PermissionError): service.release('phone-test', 'wrong', job)
    other = reg.enroll('phone-other', ['phone_task'])
    with pytest.raises(PermissionError): service.release('phone-other', other, job)


def test_missing_provider_does_not_expose_exception(vault):
    reg, _, token, job = vault
    def fail(node): raise RuntimeError('private-provider-detail')
    service = PhoneSecrets(reg, fail)
    with pytest.raises(ValueError, match='unavailable') as caught:
        service.release('phone-test', token, job)
    assert 'private-provider-detail' not in str(caught.value)
