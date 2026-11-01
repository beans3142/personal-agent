"""The agent loop: call the model, run the tools it asks for, repeat until it answers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

STEP_LIMIT_REPLY = "작업 단계 한도에 걸려 멈췄어요. 이어서 하려면 다시 말해 주세요."


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
        result = str(tool.run(args))
    except Exception as error:
        return f"error: {error}"
    if len(result) > max_result_chars:
        return f"{result[:max_result_chars]}\n[cut: {len(result)} chars in total]"
    return result


def run_turn(model, tools: list[Tool], instructions: str, history: list[dict], user_text: str, approve,
             max_steps: int = 12, max_result_chars: int = 8000) -> str:
    """Runs one user request. Appends everything that happened to history."""
    by_name = {tool.name: tool for tool in tools}
    schemas = [tool.schema() for tool in tools]
    history.append({"role": "user", "content": user_text})
    for _ in range(max_steps):
        items = model.respond(instructions, history, schemas)
        history.extend(items)
        calls = [item for item in items if item.get("type") == "function_call"]
        if not calls:
            return output_text(items)
        for call in calls:
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
