"""One isolated Hermes worker per owner request; no extra Discord connection."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import uuid


class HermesError(RuntimeError):
    def __init__(self,message,outcome=None):
        super().__init__(message)
        self.outcome=outcome


@dataclass
class HermesResult:
    text: str
    kakao_used: bool = False
    input_tokens: int | None = None
    output_tokens: int | None = None
    model_calls: int | None = None
    tool_calls: int = 0
    model: str = "미제공"
    receipts: list[dict] = field(default_factory=list)

    def footer(self, elapsed):
        def count(n): return f"{n/1000:.1f}k" if n >= 1000 else str(n)
        tokens = f"토큰 입력 {count(self.input_tokens)} / 출력 {count(self.output_tokens)}" if self.input_tokens is not None and self.output_tokens is not None else "토큰 미제공"
        calls = str(self.model_calls) + "회" if self.model_calls is not None else "미제공"
        return f"-# 처리 {elapsed:.1f}초 · {tokens} · 모델 호출 {calls} · 도구 {self.tool_calls}회 · 모델 {self.model} · Hermes"


def parse_events(raw: str) -> HermesResult:
    kakao = False
    final = None
    for line in raw.splitlines():
        try: event = json.loads(line)
        except ValueError: continue
        kakao = kakao or bool(event.get("kakao_used"))
        if event.get("type") == "result": final = event
    if not final:
        raise HermesError("Hermes 실행 결과를 확인하지 못했습니다. 전송 작업은 자동 재시도하지 않습니다.")
    def integer(key):
        value=final.get(key)
        return value if isinstance(value,int) and not isinstance(value,bool) and value >= 0 else None
    outcome=HermesResult(str(final.get("text") or ""), kakao, integer("input_tokens"), integer("output_tokens"), integer("model_calls"), integer("tool_calls") or 0, str(final.get("model") or "미제공"), final.get("receipts") if isinstance(final.get("receipts"),list) else [])
    if final.get("completed") is not True:
        outcome.text=""
        reason=final.get('stop_reason')
        message={'input_token_limit':'입력 토큰 사용 한도에 도달해 작업을 중단했습니다.',
                 'owner_denied':'승인을 받지 못해 작업을 중단했습니다.',
                 'repeated_snapshot_error':'같은 화면 조작 오류가 3회 반복돼 중단했습니다.',
                 'repeated_tool_error':'같은 도구 오류가 3회 반복돼 중단했습니다.'}.get(reason,'작업 단계·시간 제한 또는 도구 오류로 완료하지 못했습니다.')
        raise HermesError(message+' 전송 여부를 확인하기 전에는 재시도하지 않습니다.',outcome)
    return outcome


def _kill_tree(pid):
    descendants = {pid}
    if sys_platform_linux():
        parents = {}
        for path in Path("/proc").glob("[0-9]*/stat"):
            try:
                fields = path.read_text().rsplit(")", 1)[1].split()
                parents[int(path.parent.name)] = int(fields[1])
            except (OSError, ValueError, IndexError):
                continue
        while True:
            found = {child for child,parent in parents.items() if parent in descendants}
            if found <= descendants: break
            descendants |= found
    for child in sorted(descendants - {pid}, reverse=True):
        try: os.kill(child, signal.SIGKILL)
        except ProcessLookupError: pass
    try: os.kill(pid, signal.SIGKILL)
    except ProcessLookupError: pass


def sys_platform_linux():
    import sys
    return sys.platform == "linux"


def run_process(argv, *, input, timeout, **kwargs):
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
    try:
        stdout, stderr = process.communicate(input, timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "posix": _kill_tree(process.pid)
        else: process.kill()
        try: process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            for pipe in (process.stdin,process.stdout,process.stderr):
                if pipe:
                    try: pipe.close()
                    except OSError: pass
        raise
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


class HermesRunner:
    def __init__(self, python: str, worker: str, *, data: Path, env=None, run=run_process):
        self.python, self.worker, self.data = python, worker, Path(data)
        self.env = dict(os.environ if env is None else env)
        self.execute = run

    def run(self, text, instructions, history, public=False, owner_request=True):
        from .artifacts import current_outbox
        env = {**self.env, "DAEVIS_MCP_PUBLIC": "1" if public else "0",
               "DAEVIS_MCP_OWNER_REQUEST": self.env.get("OWNER_ID", "") if owner_request else "",
               "DAEVIS_MCP_READ_ONLY": "0" if owner_request else "1",
               "DAEVIS_MCP_REQUEST_ID": uuid.uuid4().hex,
               "DAEVIS_MCP_REQUEST_EXPIRES": str(time.time()+175)}
        env['AGENT_OUTBOX']=str(current_outbox(self.data/'outbox'))
        payload = {"text":text, "instructions":instructions, "history":history[-6:]}
        try:
            output = self.execute([self.python,self.worker], input=json.dumps(payload,ensure_ascii=False),
                                  text=True, encoding="utf-8", env=env, timeout=180, start_new_session=True)
        except (subprocess.TimeoutExpired, OSError):
            raise HermesError("Hermes 응답 제한 시간 또는 실행 오류입니다. 실행 결과 확인 없이 재전송하지 않습니다.") from None
        if output.returncode:
            raise HermesError("Hermes 실행에 실패했습니다. 자동 재시도하지 않습니다.")
        return parse_events(output.stdout)
