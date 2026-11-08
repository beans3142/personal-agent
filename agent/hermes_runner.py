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
    stop_reason: str | None = None
    resets_at: int | None = None
    fallback: str | None = None

    def footer(self, elapsed):
        def count(n): return f"{n/1000:.1f}k" if n >= 1000 else str(n)
        tokens = f"토큰 입력 {count(self.input_tokens)} / 출력 {count(self.output_tokens)}" if self.input_tokens is not None and self.output_tokens is not None else "토큰 미제공"
        calls = str(self.model_calls) + "회" if self.model_calls is not None else "미제공"
        note = f" · {self.fallback}" if self.fallback else ""
        return f"-# 처리 {elapsed:.1f}초 · {tokens} · 모델 호출 {calls} · 도구 {self.tool_calls}회 · 모델 {self.model} · Hermes{note}"


def control_status(receipts):
    messages={'pc_shutdown':'PC 종료 요청을 접수했어요. 기기에서 받으면 60초 대기하며, 실제 종료는 아직 확인하지 않았어요.',
        'pc_cancel':'PC 종료 취소 요청을 접수했어요. 기기에서 취소됐는지는 아직 확인하지 않았어요.',
        'pc_wake':'PC 깨우기 신호를 보냈어요. 부팅 성공은 아직 확인하지 않았어요.',
        'server_stop':'30초 뒤 비서 서버 종료를 요청했어요. 사용자 PC를 끄는 요청과는 별개입니다.'}
    actions=list(dict.fromkeys(r.get('action') for r in receipts if r.get('tool')=='mcp__daevis__owner_control' and r.get('accepted')))
    output=[messages[a] for a in actions if a in messages]
    settings=[r for r in receipts if r.get('tool')=='mcp__daevis__owner_control' and r.get('action')=='preferences' and isinstance(r.get('preferences'),dict)]
    if settings:
        labels={'trend_daily':'일간 트렌드','trend_weekly':'주간 트렌드','tech':'기술 블로그','jobs':'채용 공고','events':'행사'}
        changed=list(dict.fromkeys(k for r in settings for k in r.get('changed',[]) if k in labels))
        latest=settings[-1]['preferences'];keys=changed or [k for k in labels if k in latest]
        output.append('수신 설정 — '+' · '.join(labels[k]+': '+('켜짐' if latest[k] else '꺼짐') for k in keys if k in latest))
    return '\n'.join(output) or None


def reset_label(epoch):
    from datetime import datetime, timedelta, timezone
    if not isinstance(epoch,int) or isinstance(epoch,bool) or epoch<=0:return None
    when=datetime.fromtimestamp(epoch,timezone(timedelta(hours=9)))
    return f"{when.month}/{when.day} {when:%H:%M}"


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
        outcome.stop_reason=reason if isinstance(reason,str) else None
        outcome.resets_at=integer('resets_at')
        if reason=='iteration_limit' and str(final.get('text') or '').strip():
            # Hermes asked the model to summarise at the cap; that summary is
            # the most useful answer, so show it instead of a generic failure.
            outcome.text='⚠️ 단계 한도에 도달해 여기까지 정리했어요.\n\n'+str(final['text']).strip()
            return outcome
        if reason in ('usage_limit_reached','model_auth_failed','model_rate_limited'):
            label=reset_label(outcome.resets_at)
            message={'usage_limit_reached':'ChatGPT 사용량 한도에 걸려 답변을 만들지 못했어요.'+(f' 한도는 {label}에 풀려요.' if label else ''),
                     'model_auth_failed':'모델 로그인 인증에 실패했어요.',
                     'model_rate_limited':'모델 요청이 일시적으로 제한됐어요.'}[reason]
            if outcome.tool_calls:message+=' 실행된 작업이 있을 수 있어 자동 재시도하지 않습니다.'
            raise HermesError(message,outcome)
        message={'input_token_limit':'입력 토큰 사용 한도에 도달해 작업을 중단했습니다.',
                 'owner_denied':'승인을 받지 못해 작업을 중단했습니다.',
                 'repeated_snapshot_error':'같은 화면 조작 오류가 3회 반복돼 중단했습니다.',
                 'repeated_tool_error':'같은 도구 오류가 3회 반복돼 중단했습니다.',
                 'iteration_limit':'작업 단계 한도에 도달해 정리하지 못하고 멈췄습니다.'}.get(reason,'작업 단계·시간 제한 또는 도구 오류로 완료하지 못했습니다.')
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
    supports_chat_scope=True
    def __init__(self, python: str, worker: str, *, data: Path, env=None, run=run_process, claude_token=None):
        self.python, self.worker, self.data = python, worker, Path(data)
        self.env = dict(os.environ if env is None else env)
        self.execute = run
        self.claude_token = claude_token

    def run(self, text, instructions, history, public=False, owner_request=True,chat_scope=None):
        try:
            return self._attempt(text, instructions, history, public, owner_request, chat_scope)
        except HermesError as error:
            first=error.outcome
            # Retry with Claude only when the model never acted: no tool ran and
            # no Kakao screen was touched, so a second run cannot duplicate a send.
            if (first is None or first.stop_reason not in ('usage_limit_reached','model_rate_limited')
                    or first.tool_calls or first.kakao_used or self.claude_token is None):
                raise
            try:
                token=self.claude_token()
            except Exception:
                raise error from None
            if not token:raise
            try:
                result=self._attempt(text, instructions, history, public, owner_request, chat_scope,
                                     {"DAEVIS_HERMES_PROVIDER":"anthropic","DAEVIS_CLAUDE_TOKEN":token})
            except HermesError as second:
                raise HermesError(str(error)+'\nClaude로 다시 시도했지만 실패했어요. '+str(second), second.outcome) from None
            label=reset_label(first.resets_at)
            result.fallback='ChatGPT 한도로 Claude가 처리'+(f' (ChatGPT {label} 복구)' if label else '')
            return result

    def _attempt(self, text, instructions, history, public, owner_request, chat_scope, provider=None):
        from .artifacts import current_outbox
        env = {**self.env, **(provider or {}), "DAEVIS_MCP_PUBLIC": "1" if public else "0",
               "DAEVIS_MCP_OWNER_REQUEST": self.env.get("OWNER_ID", "") if owner_request else "",
               "DAEVIS_MCP_READ_ONLY": "0" if owner_request else "1",
               "DAEVIS_MCP_REQUEST_ID": uuid.uuid4().hex,
               "DAEVIS_MCP_CURRENT_TEXT":text[:1800],
               "DAEVIS_MCP_CHAT_SCOPE":chat_scope or '',
               "DAEVIS_MCP_REQUEST_EXPIRES": str(time.time()+305)}
        env['AGENT_OUTBOX']=str(current_outbox(self.data/'outbox'))
        payload = {"text":text, "instructions":instructions, "history":history[-12:]}
        try:
            output = self.execute([self.python,self.worker], input=json.dumps(payload,ensure_ascii=False),
                                  text=True, encoding="utf-8", env=env, timeout=310, start_new_session=True)
        except (subprocess.TimeoutExpired, OSError):
            raise HermesError("Hermes 응답 제한 시간 또는 실행 오류입니다. 실행 결과 확인 없이 재전송하지 않습니다.") from None
        if output.returncode:
            raise HermesError("Hermes 실행에 실패했습니다. 자동 재시도하지 않습니다.")
        return parse_events(output.stdout)
