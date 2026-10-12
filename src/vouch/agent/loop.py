"""The hand-written agentic loop. No frameworks.

It classifies each hunk by calling the model with read-only tools, enforces
the tool-call budget itself, validates the JSON verdict with pydantic (one
retry, then degrades), and tracks token cost.

The client is injected so tests can script a fake; nothing here requires the
Anthropic SDK to be installed.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from vouch.agent.prompts import (
    BUDGET_EXHAUSTED_PROMPT,
    DEFAULT_AGENTIC_CODES,
    build_initial_prompt,
    build_retry_prompt,
    build_system_prompt,
    hunk_range_label,
)
from vouch.agent.schema import AgentVerdict
from vouch.agent.tools import TOOL_DEFS, execute_tool
from vouch.core.report import AgentCost, AgentHunk, AgentSection, agent_findings_of
from vouch.core.types import CheckContext, Finding

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TOOL_BUDGET = 15
# A guard against a model that keeps answering without ever settling.
MAX_TURNS = 24


class AgentClient(Protocol):
    """Just the slice of the SDK we use, so tests can inject a fake."""

    @property
    def messages(self) -> Any: ...


class AgentError(Exception):
    """The agent could not run at all. The pipeline degrades to Pass 1."""


@dataclass
class AgentConfig:
    model: str = DEFAULT_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS
    tool_budget: int = DEFAULT_TOOL_BUDGET
    codes: tuple[str, ...] = DEFAULT_AGENTIC_CODES


@dataclass
class _Cost:
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0


@dataclass
class AgentRunResult:
    verdict: AgentVerdict
    # False when the model never produced valid JSON and we degraded.
    structured: bool
    cost: _Cost = field(default_factory=_Cost)


def run_agent(
    ctx: CheckContext,
    repo_root: str | Path,
    *,
    model: str | None = None,
    client: AgentClient | None = None,
    config: AgentConfig | None = None,
) -> AgentSection:
    """Run Pass 2 and return the report section."""
    settings = config or AgentConfig()
    if model:
        settings.model = model
    active = client or _default_client()

    result = run_loop(active, ctx, repo_root, settings)
    hunks = [
        AgentHunk(
            file=item.file,
            range=item.range,
            classification=item.classification,
            reason=item.reason,
        )
        for item in result.verdict.hunks
    ]
    model_findings = [
        Finding(
            check="agent",
            code=item.code,
            severity="warn",
            message=item.message,
            confidence=item.confidence,
            file=item.file,
            line=item.line,
        )
        for item in result.verdict.findings
    ]
    return AgentSection(
        ran=True,
        hunks=hunks,
        findings=agent_findings_of(hunks, model_findings),
        summary=result.verdict.summary,
        cost=AgentCost(
            input_tokens=result.cost.input_tokens,
            output_tokens=result.cost.output_tokens,
            tool_calls=result.cost.tool_calls,
        ),
    )


def _default_client() -> AgentClient:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise AgentError("ANTHROPIC_API_KEY is not set")
    try:
        from anthropic import Anthropic
    except ImportError as exc:
        raise AgentError("the anthropic package is not installed") from exc
    return Anthropic(api_key=api_key)


def run_loop(
    client: AgentClient,
    ctx: CheckContext,
    repo_root: str | Path,
    settings: AgentConfig,
) -> AgentRunResult:
    system = build_system_prompt(settings.codes)
    cost = _Cost()
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": build_initial_prompt(ctx.task, ctx.hunks)}
    ]
    asked_for_verdict = False

    for _ in range(MAX_TURNS):
        over_budget = cost.tool_calls >= settings.tool_budget
        if over_budget and not asked_for_verdict:
            messages.append({"role": "user", "content": BUDGET_EXHAUSTED_PROMPT})
            asked_for_verdict = True

        response = _create(client, settings, system, messages, with_tools=not over_budget)
        _add_usage(cost, response)
        content = _content_of(response)
        messages.append({"role": "assistant", "content": content})

        tool_uses = [block for block in content if _block_type(block) == "tool_use"]
        if not over_budget and tool_uses:
            results = [
                {
                    "type": "tool_result",
                    "tool_use_id": _block_get(block, "id"),
                    "content": execute_tool(
                        _block_get(block, "name"), _block_get(block, "input"), repo_root
                    ),
                }
                for block in tool_uses
            ]
            cost.tool_calls += len(tool_uses)
            messages.append({"role": "user", "content": results})
            continue
        break

    verdict, error = _parse_verdict(_last_text(messages))
    if verdict is None:
        messages.append({"role": "user", "content": build_retry_prompt(error or "unknown error")})
        response = _create(client, settings, system, messages, with_tools=False)
        _add_usage(cost, response)
        messages.append({"role": "assistant", "content": _content_of(response)})
        verdict, error = _parse_verdict(_last_text(messages))

    if verdict is not None:
        return AgentRunResult(
            verdict=_reconcile(verdict, ctx), structured=True, cost=cost
        )

    # Degrade: never crash on model output. Surface the raw text as a summary.
    raw = _last_text(messages).strip()
    return AgentRunResult(
        verdict=AgentVerdict(
            hunks=[],
            findings=[],
            summary=raw or "(agent produced no parseable verdict)",
        ),
        structured=False,
        cost=cost,
    )


def _create(
    client: AgentClient,
    settings: AgentConfig,
    system: str,
    messages: list[dict[str, Any]],
    *,
    with_tools: bool,
) -> Any:
    body: dict[str, Any] = {
        "model": settings.model,
        "max_tokens": settings.max_tokens,
        "system": system,
        "messages": messages,
    }
    # Dropping tools once over budget is what forces a verdict.
    if with_tools:
        body["tools"] = TOOL_DEFS
    return client.messages.create(**body)


def _add_usage(cost: _Cost, response: Any) -> None:
    usage = _attr(response, "usage")
    if usage is None:
        return
    cost.input_tokens += int(_attr(usage, "input_tokens") or 0)
    cost.output_tokens += int(_attr(usage, "output_tokens") or 0)


def _attr(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _content_of(response: Any) -> list[Any]:
    content = _attr(response, "content")
    if content is None:
        return []
    return list(content) if isinstance(content, list) else [content]


def _block_type(block: Any) -> str | None:
    return _attr(block, "type")


def _block_get(block: Any, name: str) -> Any:
    return _attr(block, name)


def _last_text(messages: list[dict[str, Any]]) -> str:
    """Concatenated text of the most recent assistant message."""
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        parts = [
            _block_get(block, "text") or ""
            for block in content or []
            if _block_type(block) == "text"
        ]
        return "\n".join(part for part in parts if part)
    return ""


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _parse_verdict(text: str) -> tuple[AgentVerdict | None, str | None]:
    payload = _extract_json(text)
    if payload is None:
        return None, "no JSON object found in response"
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"
    try:
        return AgentVerdict.model_validate(data), None
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        return None, details or str(exc)


def _extract_json(text: str) -> str | None:
    """Pull a JSON object out of model text, tolerating fences and prose."""
    fenced = _FENCE.search(text)
    candidate = fenced.group(1) if fenced else text
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    return candidate[start : end + 1]


def _reconcile(verdict: AgentVerdict, ctx: CheckContext) -> AgentVerdict:
    """Drop classifications for hunks we never sent.

    A model that invents a file is not evidence about the diff, and letting
    one through would put a fabricated path in the report.
    """
    known = {(hunk.file, hunk_range_label(hunk)) for hunk in ctx.hunks}
    known_files = {hunk.file for hunk in ctx.hunks}
    kept = [item for item in verdict.hunks if (item.file, item.range) in known]
    if not kept:
        # Ranges drift more than paths; fall back to matching on file alone
        # rather than discarding an otherwise usable verdict.
        kept = [item for item in verdict.hunks if item.file in known_files]
    return verdict.model_copy(update={"hunks": kept})
