import json

import pytest

from agent.heartbeat import HeartbeatTracker


def test_initial_healthy_unknown_and_empty_baselines_are_silent(tmp_path):
    tracker = HeartbeatTracker(tmp_path / "heartbeat.json")
    assert tracker.update({}) == []
    assert tracker.update({"android": True, "n8n": None}) == []
    assert tracker.update({"android": True, "n8n": None}) == []


def test_two_consecutive_failures_notify_once_then_recover_once(tmp_path):
    tracker = HeartbeatTracker(tmp_path / "heartbeat.json")
    assert tracker.update({"android": False}) == []
    notice = tracker.update({"android": False})
    assert len(notice) == 1 and "Android" in notice[0] and "이상" in notice[0]
    assert tracker.update({"android": False}) == []
    recovered = tracker.update({"android": True})
    assert len(recovered) == 1 and "회복" in recovered[0]
    assert tracker.update({"android": True}) == []
    assert tracker.update({"android": False}) == []
    assert len(tracker.update({"android": False})) == 1


def test_restart_retains_failure_counter_and_deduplicates_notices(tmp_path):
    path = tmp_path / "heartbeat.json"
    assert HeartbeatTracker(path).update({"n8n": False}) == []
    assert len(HeartbeatTracker(path).update({"n8n": False})) == 1
    assert HeartbeatTracker(path).update({"n8n": False}) == []
    assert len(HeartbeatTracker(path).update({"n8n": True})) == 1
    assert HeartbeatTracker(path).update({"n8n": True}) == []


def test_unknown_does_not_recover_or_count_as_consecutive_failure(tmp_path):
    tracker = HeartbeatTracker(tmp_path / "heartbeat.json")
    assert tracker.update({"memory": False}) == []
    assert tracker.update({"memory": None}) == []
    assert tracker.update({"memory": False}) == []
    assert len(tracker.update({"memory": False})) == 1
    assert tracker.update({"memory": None}) == []
    assert tracker.update({"memory": False}) == []
    assert len(tracker.update({"memory": True})) == 1


def test_new_node_keys_have_independent_baselines(tmp_path):
    tracker = HeartbeatTracker(tmp_path / "heartbeat.json")
    assert tracker.update({"android": True}) == []
    assert tracker.update({"node:phone": True, "node:lan": False}) == []
    notices = tracker.update({"node:phone": True, "node:lan": False})
    assert len(notices) == 1 and "lan" in notices[0]
    assert tracker.update({}) == []
    assert tracker.update({"node:lan": False}) == []


@pytest.mark.parametrize("samples", [[], {"bad/key": True}, {"x" * 81: True}, {"android": 0}, {str(i): True for i in range(129)}])
def test_invalid_samples_fail_before_writing_state(tmp_path, samples):
    path = tmp_path / "heartbeat.json"
    tracker = HeartbeatTracker(path)
    with pytest.raises(ValueError, match="invalid heartbeat samples"):
        tracker.update(samples)
    assert not path.exists()


def test_cumulative_key_count_is_bounded(tmp_path):
    tracker = HeartbeatTracker(tmp_path / "heartbeat.json")
    assert tracker.update({str(i): True for i in range(128)}) == []
    with pytest.raises(ValueError, match="invalid heartbeat samples"):
        tracker.update({"another": True})


def test_corrupt_persisted_state_fails_closed_without_raw_content(tmp_path):
    path = tmp_path / "heartbeat.json"
    path.write_text('secret malformed json', encoding='utf-8')
    with pytest.raises(ValueError) as error:
        HeartbeatTracker(path)
    assert "secret" not in str(error.value)


def test_state_is_bounded_private_and_atomic_failure_does_not_advance_tracker(tmp_path, monkeypatch):
    import agent.heartbeat as heartbeat
    path = tmp_path / "heartbeat.json"
    tracker = HeartbeatTracker(path)
    tracker.update({"android": False})
    before = path.read_bytes()
    replace = heartbeat.os.replace
    def fail(*args):
        raise OSError("secret filesystem path")
    monkeypatch.setattr(heartbeat.os, "replace", fail)
    with pytest.raises(ValueError) as error:
        tracker.update({"android": False})
    assert "secret" not in str(error.value)
    assert path.read_bytes() == before
    monkeypatch.setattr(heartbeat.os, "replace", replace)
    assert len(tracker.update({"android": False})) == 1
    assert json.loads(path.read_text())["android"] == {"failures": 2, "notified": True}


def test_atomic_replacement_requests_private_permissions(tmp_path, monkeypatch):
    import agent.heartbeat as heartbeat
    modes = []
    chmod = heartbeat.os.chmod
    def record(path, mode):
        modes.append(mode)
        chmod(path, mode)
    monkeypatch.setattr(heartbeat.os, "chmod", record)
    HeartbeatTracker(tmp_path / "heartbeat.json").update({"n8n": False})
    assert modes == [0o600]


@pytest.mark.parametrize("content", [
    json.dumps({"android": {"failures": True, "notified": False}}),
    json.dumps({"android": {"failures": 3, "notified": False}}),
    " " * 32769,
    "[" * 2000 + "]" * 2000,
])
def test_invalid_or_excessive_state_is_sanitized(tmp_path, content):
    path = tmp_path / "heartbeat.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="heartbeat state unavailable"):
        HeartbeatTracker(path)
