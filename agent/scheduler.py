"""Scheduled jobs. A "message" job sends its text. A "prompt" job runs the agent first."""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

KINDS = ("message", "prompt")
DISCORD_LIMIT = 2000
MIN_REPEAT_MINUTES = {"prompt": 30}
log = logging.getLogger(__name__)


def load_jobs(store: Path) -> list[dict]:
    try:
        return json.loads(store.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        store.replace(store.with_name(f"{store.name}.corrupt-{int(time.time())}"))
        return []


def save_jobs(store: Path, jobs: list[dict]) -> None:
    store.parent.mkdir(parents=True, exist_ok=True)
    temp = store.with_name(f"{store.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(store)


def add_job(store: Path, kind: str, text: str, at: datetime, every_minutes: int | None = None) -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if not text.strip():
        raise ValueError("text is empty")
    if every_minutes is not None and every_minutes < MIN_REPEAT_MINUTES.get(kind, 1):
        raise ValueError(f"every_minutes must be at least {MIN_REPEAT_MINUTES.get(kind, 1)} for {kind} jobs")
    job = {
        "id": uuid.uuid4().hex[:8],
        "kind": kind,
        "text": text,
        "next_at": at.isoformat(),
        "every_minutes": every_minutes,
    }
    save_jobs(store, load_jobs(store) + [job])
    return job


def remove_job(store: Path, job_id: str) -> bool:
    jobs = load_jobs(store)
    kept = [job for job in jobs if job["id"] != job_id]
    save_jobs(store, kept)
    return len(kept) != len(jobs)


def next_run(job: dict, now: datetime) -> datetime:
    interval = timedelta(minutes=job["every_minutes"])
    upcoming = datetime.fromisoformat(job["next_at"])
    while upcoming <= now:
        upcoming += interval
    return upcoming


def finish_job(store: Path, job: dict, now: datetime) -> None:
    """Re-reads the store so that jobs added while this job ran are kept."""
    jobs = load_jobs(store)
    if job["every_minutes"]:
        for current in jobs:
            if current["id"] == job["id"]:
                current["next_at"] = next_run(job, now).isoformat()
    else:
        jobs = [current for current in jobs if current["id"] != job["id"]]
    save_jobs(store, jobs)


def run_due(store: Path, now: datetime, send, run_prompt) -> int:
    finished = 0
    for job in load_jobs(store):
        if datetime.fromisoformat(job["next_at"]) > now:
            continue
        try:
            send(run_prompt(job["text"]) if job["kind"] == "prompt" else job["text"])
        except Exception as error:
            log.warning("job %s failed: %s", job["id"], error)
            continue
        finish_job(store, job, now)
        finished += 1
    return finished


def split_text(text: str) -> list[str]:
    parts = []
    while len(text) > DISCORD_LIMIT:
        boundary = text.rfind("\n", 0, DISCORD_LIMIT + 1)
        cut = boundary + 1 if boundary >= DISCORD_LIMIT // 2 else DISCORD_LIMIT
        parts.append(text[:cut])
        text = text[cut:]
    if text:
        parts.append(text)
    return parts
