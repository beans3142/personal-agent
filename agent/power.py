"""Turns the agent server on and off, and decides when an idle server should turn itself off."""

from __future__ import annotations

import re
import time
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta


def maintenance_active(path: Path, now: datetime) -> bool:
    try:
        modified=datetime.fromtimestamp(path.stat().st_mtime,timezone.utc)
        try:
            until=datetime.fromisoformat(json.loads(path.read_text())['until'])
            if until.tzinfo is None: raise ValueError()
        except (ValueError,KeyError,TypeError):
            until=modified+timedelta(minutes=30)
        return now < min(until,modified+timedelta(hours=4))
    except OSError:
        return False

NAMES = r"(?:대비스|데비스|대비쓰|대빗스|대비즈|ㄷㅂㅅ)"
WORDS = {
    "on": ("온라인", "온", "켜", "켜줘", "켜기", "on", "online"),
    "off": ("오프라인", "오프", "꺼", "꺼줘", "끄기", "off", "offline"),
    "status": ("상태", "상태확인", "status", "?"),
}
COMMAND_OF = {word.lower(): command for command, words in WORDS.items() for word in words}
POWER_PATTERN = re.compile(NAMES + r"\s*(\S+)", re.I)
BOOT_POLL_SECONDS = 10
BOOT_POLLS = 36


def power_command(text: str) -> str | None:
    """Only the exact phrases count, so normal requests never toggle the server."""
    match = POWER_PATTERN.fullmatch((text or "").strip())
    return COMMAND_OF.get(match.group(1).lower()) if match else None


def idle_shutdown_due(last_activity: datetime, now: datetime, limit_minutes: int, busy: bool, scheduled_jobs: int) -> bool:
    if limit_minutes <= 0 or busy or scheduled_jobs:
        return False
    return (now - last_activity).total_seconds() >= limit_minutes * 60


class PowerControl:
    def __init__(self, ec2, instance_id: str, wait=lambda: time.sleep(BOOT_POLL_SECONDS)):
        self.ec2 = ec2
        self.instance_id = instance_id
        self.wait = wait

    def _instance(self) -> dict:
        return self.ec2.describe_instances(InstanceIds=[self.instance_id])["Reservations"][0]["Instances"][0]

    def status(self) -> dict:
        instance = self._instance()
        return {"state": instance["State"]["Name"], "launched": instance.get("LaunchTime"), "ip": instance.get("PublicIpAddress")}

    def start(self) -> str:
        state = self.status()["state"]
        if state in ("running", "pending"):
            return "already_running"
        if state != "stopped":
            return f"busy:{state}"
        self.ec2.start_instances(InstanceIds=[self.instance_id])
        return "started"

    def wait_running(self) -> bool:
        for _ in range(BOOT_POLLS):
            if self.status()["state"] == "running":
                return True
            self.wait()
        return False

    def stop(self) -> str:
        state = self.status()["state"]
        if state in ("stopped", "stopping"):
            return "already_stopped"
        if state != "running":
            return f"busy:{state}"
        self.ec2.stop_instances(InstanceIds=[self.instance_id])
        return "stopping"
