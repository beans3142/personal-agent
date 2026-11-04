"""Owner-driven comparison harness; dry-run by default, never manufactures metrics.

Config JSON maps existing/hermes to argv arrays. Each runner receives a single
task JSON on stdin and must print one JSON object with success, evidence and
optional input_tokens/output_tokens/model_calls/tool_calls/model. Runners must
use independent fresh contexts. Secrets and message bodies should not be logged.
Only owner-controlled commands are accepted; model output is never executed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time

TASKS = (
    {"id": "kakao_self", "side_effect": True, "prompt": "카카오톡 나와의 채팅에만 javis 테스트 전송"},
    {"id": "transit", "side_effect": False, "prompt": "내일 오전 11시 행신역에서 선릉역 경로 조회"},
    {"id": "mail_self", "side_effect": True, "prompt": "네이버 메일 나에게 쓰기: 제목 javis 테스트, 본문 javis 테스트"},
    {"id": "discord_private_qa", "side_effect": False, "prompt": "소유자와 봇의 비공개 Discord DM 왕복과 접근 제한을 외부 UI QA로 확인; 공개 전송 금지"},
)


def compare(runners: dict, trace: Path, *, execute=False, allow_side_effects=False, timeout=180) -> list[dict]:
    if not isinstance(runners, dict) or not runners or set(runners) - {"existing", "hermes"}:
        raise ValueError("runners must contain existing and/or hermes")
    for argv in runners.values():
        if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) and arg for arg in argv):
            raise ValueError("runner command must be a nonempty argv array")
    trace = Path(trace)
    attempted = set()
    if execute and trace.exists():
        for line in trace.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("status") == "started":
                attempted.add((row["agent"], row["task"]))

    def append(row):
        trace.parent.mkdir(parents=True, exist_ok=True)
        with trace.open("a", encoding="utf-8") as file:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
            file.flush()

    rows = []
    for agent, argv in runners.items():
        for task in TASKS:
            row = {"agent": agent, "task": task["id"], "side_effect": task["side_effect"], "status": "planned"}
            if not execute:
                rows.append(row)
                continue
            if task["side_effect"] and not allow_side_effects:
                row["status"] = "blocked_side_effect"
                rows.append(row)
                continue
            if (agent, task["id"]) in attempted:
                row["status"] = "already_attempted"
                rows.append(row)
                continue
            # Persist intent before starting: a crash/timeout must never silently resend.
            append({**row, "status": "started", "started_at": datetime.now(timezone.utc).isoformat()})
            started = time.monotonic()
            try:
                result = subprocess.run(argv, input=json.dumps(task, ensure_ascii=False), text=True,
                                        encoding="utf-8", capture_output=True, timeout=timeout, shell=False)
                row["exit_code"] = result.returncode
                try:
                    reported = json.loads(result.stdout)
                except ValueError:
                    reported = {}
                if not isinstance(reported, dict):
                    reported = {}
                evidence = reported.get("evidence")
                row["status"] = "passed" if result.returncode == 0 and reported.get("success") is True and isinstance(evidence, str) and evidence.strip() else "unverified"
                # Evidence is runner-reported; owner QA must confirm actual delivery.
                row["evidence"] = evidence[:2000] if isinstance(evidence, str) else None
                for key in ("input_tokens", "output_tokens", "model_calls", "tool_calls"):
                    value = reported.get(key)
                    row[key] = value if type(value) is int and value >= 0 else None
                row["model"] = reported.get("model") if isinstance(reported.get("model"), str) else None
            except subprocess.TimeoutExpired:
                row["status"] = "timeout_uncertain"
            except OSError:
                row["status"] = "runner_error"
            row["elapsed_seconds"] = time.monotonic() - started
            append(row)
            rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="Owner-authored runner argv JSON")
    parser.add_argument("--trace", type=Path, default=Path("data/comparison.jsonl"))
    parser.add_argument("--execute", action="store_true", help="Actually call runners; otherwise only show task matrix")
    parser.add_argument("--allow-side-effects", action="store_true", help="Explicitly permit each agent's self-chat and self-mail test once")
    args = parser.parse_args()
    print(json.dumps(compare(json.loads(args.config.read_text(encoding="utf-8")), args.trace,
                             execute=args.execute, allow_side_effects=args.allow_side_effects), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
