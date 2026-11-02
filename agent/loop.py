"""The agent loop: call the model, run the tools it asks for, repeat until it answers."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Callable

STEP_LIMIT_REPLY = "작업 단계 한도에 걸려 멈췄어요. 이어서 하려면 다시 말해 주세요."


@dataclass
class TurnMetrics:
    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    missing_usage: int = 0
    models: list[str] = field(default_factory=list)

    def record(self, model) -> None:
        metadata = getattr(model, "last_response_metadata", {})
        name = metadata.get("model") or f"{getattr(model, 'model', '미제공')} (요청값)"
        if name not in self.models:
            self.models.append(name)
        usage = metadata.get("usage") or {}
        if isinstance(usage.get("input_tokens"), int) and isinstance(usage.get("output_tokens"), int):
            self.input_tokens += usage["input_tokens"]
            self.output_tokens += usage["output_tokens"]
        else:
            self.missing_usage += 1

    def footer(self, elapsed: float) -> str:
        tokens = f"토큰 입력 {self.input_tokens:,} / 출력 {self.output_tokens:,}"
        if self.missing_usage == self.model_calls:
            tokens = "토큰 미제공"
        elif self.missing_usage:
            tokens += f" ({self.missing_usage}회 미제공)"
        return f"-# 처리 {elapsed:.1f}초 · {tokens} · 모델 호출 {self.model_calls}회 · 도구 {self.tool_calls}회 · 모델 {', '.join(self.models) or '미제공'}"


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    run: Callable[[dict], str]
    needs_approval: bool = False

    def schema(self) -> dict:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


def output_text(items: list[dict]) -> str:
    parts = []
    for item in items:
        if item.get("type") == "message":
            parts += [c["text"] for c in item.get("content", []) if c.get("type") == "output_text"]
    return "\n".join(parts).strip()


def execute(call: dict, tools: dict[str, Tool], approve, max_result_chars: int) -> str:
    tool = tools.get(call["name"])
    if tool is None:
        return f"error: unknown tool {call['name']}"
    try:
        args = json.loads(call.get("arguments") or "{}")
    except json.JSONDecodeError as error:
        return f"error: invalid arguments: {error}"
    if tool.needs_approval and not approve(tool.name, args):
        return "denied: the user did not approve this action"
    try:
        started = time.monotonic()
        logging.getLogger("agent.tools").info("tool start: %s", tool.name)
        result = str(tool.run(args))
    except Exception as error:
        logging.getLogger("agent.tools").warning("tool failed: %s (%s)", tool.name, type(error).__name__)
        return f"error: {error}"
    logging.getLogger("agent.tools").info("tool done: %s %.2fs", tool.name, time.monotonic() - started)
    if len(result) > max_result_chars:
        return f"{result[:max_result_chars]}\n[cut: {len(result)} chars in total]"
    return result


def run_turn(model, tools: list[Tool], instructions: str, history: list[dict], user_text: str, approve,
             max_steps: int = 12, max_result_chars: int = 8000, metrics: TurnMetrics | None = None) -> str:
    """Runs one user request. Appends everything that happened to history."""
    by_name = {tool.name: tool for tool in tools}
    schemas = [tool.schema() for tool in tools]
    history.append({"role": "user", "content": user_text})
    for _ in range(max_steps):
        if metrics is not None:
            metrics.model_calls += 1
        items = model.respond(instructions, history, schemas)
        if metrics is not None:
            metrics.record(model)
        history.extend(items)
        calls = [item for item in items if item.get("type") == "function_call"]
        if not calls:
            return output_text(items)
        for call in calls:
            if metrics is not None:
                metrics.tool_calls += 1
            history.append({
                "type": "function_call_output",
                "call_id": call["call_id"],
                "output": execute(call, by_name, approve, max_result_chars),
            })
    return STEP_LIMIT_REPLY


def trim_history(history: list[dict], max_items: int) -> list[dict]:
    """Keeps recent items. The result starts at a user message so no tool output loses its call."""
    if len(history) <= max_items:
        return history
    recent = history[-max_items:]
    for index, item in enumerate(recent):
        if item.get("role") == "user":
            return recent[index:]
    return []
