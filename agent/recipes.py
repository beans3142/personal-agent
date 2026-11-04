"""Bounded JSON plans: plan once, validate everything, execute without eval or retries.

Recipes are owner-managed JSON, never learned or persisted from untrusted requests.
Only tools explicitly supplied to the executor are reachable.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import re
import time

import jsonschema

from agent.loop import Tool


READ_TOOLS = frozenset({"memory_search", "schedule_list", "map_search", "route_search", "web_search",
                        "web_fetch", "mail_list", "mail_read", "android_screen", "device_nodes", "device_jobs", "n8n_list"})
PLAN_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["steps"], "properties": {
    "steps": {"type": "array", "minItems": 1, "maxItems": 8, "items": {
        "type": "object", "additionalProperties": False, "required": ["tool", "args"], "properties": {
            "tool": {"type": "string"}, "args": {"type": "object"}}}}}}


def _validate(value, schema):
    try:
        jsonschema.validate(value, schema)
    except (jsonschema.ValidationError, jsonschema.SchemaError) as error:
        raise ValueError("invalid plan or recipe arguments") from error


def _strict(schema):
    schema = deepcopy(schema)
    if schema.get("type") == "object":
        schema["additionalProperties"] = False
    return schema


class RecipeExecutor:
    def __init__(self, tools: list[Tool], approve=None, registry: Path | None = None):
        self.tools = {tool.name: tool for tool in tools}
        self.approve = approve or (lambda *_: False)
        self.registry = Path(registry) if registry else None

    def execute(self, plan: dict) -> dict:
        plan = deepcopy(plan)
        _validate(plan, PLAN_SCHEMA)
        # Preflight the complete plan before performing even the first action.
        for step in plan["steps"]:
            tool = self.tools.get(step["tool"])
            if tool is None or tool.name in {"plan_execute", "recipe_run", "recipe_list"}:
                raise ValueError("unknown or recursive plan tool")
            _validate(step["args"], _strict(tool.parameters))
        results = []
        for step in plan["steps"]:
            tool, args = self.tools[step["tool"]], step["args"]
            if (tool.needs_approval or tool.name not in READ_TOOLS) and not self.approve(tool.name, deepcopy(args)):
                return {"status": "denied", "results": results, "stopped_at": len(results)}
            started = time.monotonic()
            try:
                output = str(tool.run(deepcopy(args)))
                failed = output.lstrip().lower().startswith(("error:", "denied:", "blocked:"))
                try:
                    payload = json.loads(output)
                    if isinstance(payload, dict):
                        failed = (failed or bool(payload.get("error")) or payload.get("ok") is False
                                  or payload.get("success") is False
                                  or payload.get("status") in {"error", "failed", "denied", "uncertain", "blocked"})
                        if tool.name == "auth_ensure":
                            failed = failed or payload.get("status") != "ok"
                except (ValueError, TypeError):
                    pass
            except Exception:
                # A failed write may already have happened; do not retry or continue.
                output, failed = "error: tool failed; outcome may be uncertain", True
            results.append({"tool": tool.name, "output": output, "elapsed_seconds": time.monotonic() - started})
            if failed:
                return {"status": "error", "results": results, "stopped_at": len(results) - 1}
        return {"status": "ok", "results": results}

    def _recipes(self):
        if self.registry is None or not self.registry.exists():
            return {}
        recipes = json.loads(self.registry.read_text(encoding="utf-8"))
        if not isinstance(recipes, dict):
            raise ValueError("recipe registry must be an object")
        return recipes

    def run_recipe(self, name: str, parameters: dict) -> dict:
        recipe = self._recipes().get(name)
        if not isinstance(recipe, dict) or set(recipe) != {"parameters", "plan"}:
            raise ValueError("unknown or invalid owner recipe")
        _validate(parameters, _strict(recipe["parameters"]))

        def resolve(value):
            if isinstance(value, dict):
                if "$param" in value:
                    if set(value) != {"$param"} or not isinstance(value["$param"], str) or value["$param"] not in parameters:
                        raise ValueError("invalid recipe parameter reference")
                    return deepcopy(parameters[value["$param"]])
                return {key: resolve(item) for key, item in value.items()}
            if isinstance(value, list):
                return [resolve(item) for item in value]
            return value

        return self.execute(resolve(recipe["plan"]))


def build_recipe_tools(executor: RecipeExecutor) -> list[Tool]:
    def render(fn):
        def run(args):
            return json.dumps(fn(args), ensure_ascii=False)
        return run
    return [
        Tool("plan_execute", "Execute one bounded JSON plan. No code or shell. Each write requires owner approval; failures stop without retry.",
             PLAN_SCHEMA, render(executor.execute)),
        Tool("recipe_list", "List owner-managed reusable recipe names and parameter schemas.",
             {"type": "object", "properties": {}, "additionalProperties": False},
             render(lambda _: {name: recipe["parameters"] for name, recipe in executor._recipes().items()})),
        Tool("recipe_run", "Run a named owner-managed recipe with typed literal parameters.",
             {"type": "object", "additionalProperties": False, "required": ["name", "parameters"], "properties": {
                 "name": {"type": "string"}, "parameters": {"type": "object"}}},
             render(lambda args: executor.run_recipe(args["name"], args["parameters"]))),
    ]


def fixed_plan(text: str) -> dict | None:
    """Only an explicit fixed command is routed; all ambiguous language falls back.

    Grammar: 경로 "origin" → "destination" [ISO-departure-with-timezone]
    Relative dates deliberately remain with the regular agent.
    """
    match = re.fullmatch(r'경로 "([^"\r\n]{1,200})" → "([^"\r\n]{1,200})"(?: (\S+))?', text.strip())
    if not match:
        return None
    origin, destination, departure = match.groups()
    if not origin.strip() or not destination.strip():
        return None
    args = {"origin": origin, "destination": destination}
    if departure:
        try:
            parsed = datetime.fromisoformat(departure)
            if parsed.tzinfo is None or "T" not in departure:
                return None
        except ValueError:
            return None
        args["depart_at"] = departure
    return {"steps": [{"tool": "route_search", "args": args}]}
