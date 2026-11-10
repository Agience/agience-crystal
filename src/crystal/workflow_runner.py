"""A workflow's steps, run by the gateway (John, 2026-11-10, BUG-048).

A workflow is a Transform whose run block lists steps, each an operator invoked with arguments: its
own values, `$.previous` / `$.steps.<name>` for what an earlier step returned, and `@link.<name>`
for an artifact the workflow keeps as a typed link. Lumen ran them, handing each step to the gateway;
but a request reaches Lumen through the gateway as a hop's result, which Origin never exchanges
again (review 12: depth 1), so no step served by another persona could run, on a timer's firing or
on "Run now". The gateway runs them instead: each step is the operator's own `invoke`, dispatched
here on the caller's own delegation, and so reaches the operator's persona by the one hop the
gateway already makes.

The steps are sealed under their owner's key, which neither the gateway nor a hop's result opens.
They come with the request as the caller's own reading of the workflow (`body.context`): the
person's editor sends what it holds, and a timer's firing sends what Lumen read on the timer's job
grant. They are used only when the workflow as Mantle shows the caller carries none, and every step
still runs on the caller's own grants, operator by operator.

The step semantics are Lumen's (`agience_chorus.lumen.runs`): resolution of `$.` paths and
`@link.` names, a step's output, and what counts as a failed step.
"""
from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional

LINK = "@link."
_PART = re.compile(r"([^.\[\]]+)|\[(\d+)\]")
_MISSING = object()


class StepFailed(Exception):
    """A step that could not run, or ran and answered an error."""


def context_of(artifact: dict) -> dict:
    raw = (artifact or {}).get("context") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = {}
    return raw if isinstance(raw, dict) else {}


def run_block(ctx: dict) -> dict:
    """A card's run block, where the transform runner looks for it."""
    ctx = ctx or {}
    run = ctx.get("run") or (ctx.get("transform") or {}).get("run") or (ctx.get("order") or {}).get("run")
    return run if isinstance(run, dict) else {}


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def workflow_of(artifact: dict, body: Optional[dict]) -> Optional[dict]:
    """The workflow's run block when it is one this runner takes, else None, and Lumen runs it as
    before: a single step, no steps at all, a step that is not an operator (a Transform, or one from
    before steps were operators), a step with a `condition`, a `retry` block, or a run against a
    state artifact. Only what this runner does exactly as Lumen did is taken."""
    run = run_block(context_of(artifact))
    given = (body or {}).get("context")
    if not run and isinstance(given, dict):
        run = run_block(given)
    if run.get("type") != "workflow" or run.get("retry"):
        return None
    params = (body or {}).get("params")
    if isinstance(params, dict) and params.get("state_artifact_id"):
        return None
    steps = run.get("steps") or []
    if not isinstance(steps, list) or not steps:
        return None
    for s in steps:
        if (not isinstance(s, dict) or not _ID.match(str(s.get("operator_id") or ""))
                or s.get("condition") or s.get("transform_id")):
            return None
    return run


def uses_links(run: dict) -> bool:
    """Whether any step names a link (`@link.`), so the links are read only when needed."""
    return any(isinstance(v, str) and v.startswith(LINK)
               for s in run.get("steps") or [] for v in (s.get("arguments") or {}).values())


def resolve(path: str, scope: Dict[str, Any]) -> Any:
    cur: Any = scope
    for key, index in _PART.findall(path):
        if key:
            if not isinstance(cur, dict) or key not in cur:
                return _MISSING
            cur = cur[key]
        else:
            i = int(index)
            if not isinstance(cur, list) or i >= len(cur):
                return _MISSING
            cur = cur[i]
    return cur


def resolve_mapping(mapping: Dict[str, Any], scope: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in (mapping or {}).items():
        if isinstance(value, str) and value.startswith("$."):
            found = resolve(value[2:], scope)
            if found is not _MISSING and found is not None:
                out[key] = found
        else:
            out[key] = value
    return out


def resolve_links(mapping: Dict[str, Any], links: Dict[str, str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in (mapping or {}).items():
        if isinstance(value, str) and value.startswith(LINK):
            name = value[len(LINK):]
            if name not in links:
                raise StepFailed(f"argument '{key}': the workflow has no link '{name}' you may read")
            out[key] = links[name]
        else:
            out[key] = value
    return out


def output_of(result: Any) -> Any:
    for _ in range(3):
        if not (isinstance(result, dict) and isinstance(result.get("content"), list)):
            break
        blocks = result["content"]
        text = blocks[0].get("text", "") if blocks and isinstance(blocks[0], dict) else ""
        try:
            result = json.loads(text)
        except (TypeError, ValueError):
            return text
    if isinstance(result, str):
        try:
            return json.loads(result)
        except ValueError:
            return result
    return result


def step_failure(result: Any) -> Optional[str]:
    if not isinstance(result, dict):
        return None
    blocks = result.get("content") or []
    text = blocks[0].get("text", "") if blocks and isinstance(blocks[0], dict) else ""
    if result.get("isError"):
        return text[:300] or "the tool reported an error"
    if text.lstrip().startswith("{"):
        try:
            payload = json.loads(text)
        except ValueError:
            return None
        if isinstance(payload, dict) and payload.get("error"):
            return str(payload["error"])[:300]
        if isinstance(payload, dict) and isinstance(payload.get("content"), list):
            return step_failure(payload)
    return None


async def run_steps(run: dict, params: Dict[str, Any], links: Dict[str, str],
                    invoke: Callable[[str, Dict[str, Any]], Awaitable[Any]]) -> dict:
    """Run `run`'s steps in order, each by `invoke(operator_id, arguments)`. Returns the MCP-shaped
    result a tool answers: the steps' results and the last output, or `isError` with the step that
    failed and why. Stops at the first failure."""
    outputs: Dict[str, Any] = {}
    previous: Dict[str, Any] = {}
    results: List[dict] = []

    def answer(payload: dict, error: bool = False) -> dict:
        return {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": error}

    for i, step in enumerate(run.get("steps") or []):
        name = step.get("name") or f"step_{i}"
        scope = {**(params or {}), "steps": dict(outputs), **({"previous": previous["v"]} if "v" in previous else {})}
        try:
            args = resolve_mapping(resolve_links(step.get("arguments") or {}, links), scope)
            result = await invoke(str(step["operator_id"]), args)
            failure = step_failure(result)
            if failure:
                raise StepFailed(failure)
        except Exception as exc:  # noqa: BLE001 — a step's failure is the run's answer, in words
            detail = getattr(exc, "detail", None) or str(exc) or type(exc).__name__
            results.append({"step": i, "name": name, "status": "error", "error": str(detail)[:300]})
            return answer({"error": f"Step {i} ({name}) failed: {str(detail)[:300]}", "step": i,
                           "name": name, "results": results}, error=True)
        value = output_of(result)
        outputs[name] = value
        previous["v"] = value
        results.append({"step": i, "name": name, "status": "completed", "output": value})
    return answer({"status": "completed", "results": results, "output": previous.get("v")})
