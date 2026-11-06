from datetime import datetime, timedelta, timezone

import pytest

from agent.power import PowerControl, idle_shutdown_due, power_command


@pytest.mark.parametrize("text,expected", [
    ("대비스 온라인", "on"),
    ("대비스 온", "on"),
    ("데비스 켜", "on"),
    ("대비스 ON", "on"),
    ("대비스온라인", "on"),
    ("  대비스 오프라인 ", "off"),
    ("대비쓰 오프", "off"),
    ("대비스 꺼줘", "off"),
    ("ㄷㅂㅅ off", "off"),
    ("대비스 상태", "status"),
    ("대비스 ?", "status"),
    ("자비스 온라인", None),
    ("대비스 온라인 해줘", None),
    ("온라인 대비스", None),
    ("대비스 온도 알려줘", None),
    ("대비스", None),
    ("오늘 날씨", None),
    ("서버 꺼줘", "off"),
    ("서버 켜줘", "on"),
    ("대비스 서버 상태", "status"),
    ("내 PC 꺼줘", None),
    ("서버 끄는 법 알려줘", None),
])
def test_power_command_matches_only_exact_phrases(text, expected):
    assert power_command(text) == expected


class FakeEc2:
    def __init__(self, state="stopped"):
        self.state = state
        self.calls = []

    def describe_instances(self, InstanceIds):
        return {"Reservations": [{"Instances": [{"State": {"Name": self.state}, "LaunchTime": datetime(2026, 11, 4, 1, 0, tzinfo=timezone.utc), "PublicIpAddress": "1.2.3.4"}]}]}

    def start_instances(self, InstanceIds):
        self.calls.append(("start", InstanceIds))
        self.state = "pending"

    def stop_instances(self, InstanceIds):
        self.calls.append(("stop", InstanceIds))
        self.state = "stopping"


def test_start_only_touches_the_configured_instance():
    ec2 = FakeEc2("stopped")
    assert PowerControl(ec2, "i-1", wait=lambda: None).start() == "started"
    assert ec2.calls == [("start", ["i-1"])]


def test_start_when_already_running_does_nothing():
    ec2 = FakeEc2("running")
    assert PowerControl(ec2, "i-1", wait=lambda: None).start() == "already_running"
    assert ec2.calls == []


def test_start_while_stopping_is_refused():
    ec2 = FakeEc2("stopping")
    assert PowerControl(ec2, "i-1", wait=lambda: None).start() == "busy:stopping"
    assert ec2.calls == []


def test_stop_when_already_stopped_does_nothing():
    ec2 = FakeEc2("stopped")
    assert PowerControl(ec2, "i-1").stop() == "already_stopped"
    assert ec2.calls == []


def test_stop_running_instance():
    ec2 = FakeEc2("running")
    assert PowerControl(ec2, "i-1").stop() == "stopping"
    assert ec2.calls == [("stop", ["i-1"])]


def test_status_reports_state():
    assert PowerControl(FakeEc2("running"), "i-1").status()["state"] == "running"


NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)


def test_idle_shutdown_after_limit_with_nothing_running():
    assert idle_shutdown_due(NOW - timedelta(minutes=121), NOW, 120, busy=False, scheduled_jobs=0) is True


def test_no_shutdown_before_limit_when_busy_or_with_jobs_or_disabled():
    assert idle_shutdown_due(NOW - timedelta(minutes=30), NOW, 120, False, 0) is False
    assert idle_shutdown_due(NOW - timedelta(minutes=500), NOW, 120, True, 0) is False
    assert idle_shutdown_due(NOW - timedelta(minutes=500), NOW, 120, False, 2) is False
    assert idle_shutdown_due(NOW - timedelta(minutes=500), NOW, 0, False, 0) is False
