"""A scripted fake Anthropic client, so the agent loop is testable offline."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Usage:
    input_tokens: int = 10
    output_tokens: int = 5


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    name: str
    input: dict[str, Any]
    id: str = "tool_1"
    type: str = "tool_use"


@dataclass
class Response:
    content: list[Any]
    usage: Usage = field(default_factory=Usage)
    stop_reason: str = "end_turn"


def text_response(payload: Any, *, fenced: bool = False, prose: str = "") -> Response:
    body = payload if isinstance(payload, str) else json.dumps(payload)
    if fenced:
        body = f"```json\n{body}\n```"
    return Response(content=[TextBlock(text=f"{prose}{body}")])


def tool_response(name: str, tool_input: dict[str, Any], block_id: str = "tool_1") -> Response:
    return Response(
        content=[ToolUseBlock(name=name, input=tool_input, id=block_id)],
        stop_reason="tool_use",
    )


class FakeMessages:
    def __init__(self, script: list[Response]) -> None:
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []

    def create(self, **body: Any) -> Response:
        # Snapshot the request: the loop mutates one messages list in place,
        # so storing a reference would make every recorded call show the
        # final state. A real SDK serializes the body at send time.
        self.calls.append(copy.deepcopy(body))
        if not self._script:
            raise AssertionError("fake client ran out of scripted responses")
        return self._script.pop(0)


class FakeClient:
    """Returns the scripted responses in order and records every request."""

    def __init__(self, *script: Response) -> None:
        self.messages = FakeMessages(list(script))

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self.messages.calls

    @property
    def call_count(self) -> int:
        return len(self.messages.calls)
