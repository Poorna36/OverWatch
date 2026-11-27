"""Provider-neutral LLM adapter interface and deterministic scripted test adapter.

Keeps vendor SDKs and live network calls out of the Phase 4 agent-loop core so
model providers can be plugged in behind a narrow contract interface.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

MessageRole = Literal["system", "user", "assistant", "tool"]


class LlmAdapterError(Exception):
    """Raised when an LLM adapter fails or runs out of scripted turns."""


@dataclass(frozen=True)
class ToolDefinition:
    """Provider-neutral tool descriptor exposed to the LLM."""

    name: str
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LlmToolCall:
    """Single tool invocation requested by an LLM turn."""

    call_id: str
    tool: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LlmMessage:
    """In-memory conversation message exchanged with the LLM adapter."""

    role: MessageRole
    content: str = ""
    tool_calls: tuple[LlmToolCall, ...] = ()
    tool_call_id: str | None = None
    tool_name: str | None = None


@dataclass(frozen=True)
class LlmUsage:
    """Provider-reported token usage for one LLM inference turn."""

    input_tokens: int
    output_tokens: int
    model: str | None = None


@dataclass(frozen=True)
class LlmTurnResponse:
    """Structured result from one LLM inference turn."""

    tool_calls: tuple[LlmToolCall, ...] = ()
    assistant_text: str | None = None
    usage: LlmUsage | None = None


class LlmAdapter(Protocol):
    """Provider-neutral interface for generating one assistant/tool turn."""

    def next_turn(
        self,
        *,
        system_prompt: str,
        messages: Sequence[LlmMessage],
        tools: Sequence[ToolDefinition],
    ) -> LlmTurnResponse:
        """Return the next tool call batch or final assistant message."""


@dataclass(frozen=True)
class RecordedTurnCall:
    """Snapshot of inputs passed to ``ScriptedLlmAdapter.next_turn``."""

    system_prompt: str
    messages: tuple[LlmMessage, ...]
    tools: tuple[ToolDefinition, ...]


ScriptedTurnItem = (
    LlmTurnResponse
    | Callable[[str, Sequence[LlmMessage], Sequence[ToolDefinition]], LlmTurnResponse]
)


class ScriptedLlmAdapter:
    """Deterministic scripted LLM adapter for unit tests without network calls."""

    def __init__(self, turns: Sequence[ScriptedTurnItem]) -> None:
        self._turns: list[ScriptedTurnItem] = list(turns)
        self._index: int = 0
        self.recorded_calls: list[RecordedTurnCall] = []

    def next_turn(
        self,
        *,
        system_prompt: str,
        messages: Sequence[LlmMessage],
        tools: Sequence[ToolDefinition],
    ) -> LlmTurnResponse:
        """Record the received turn inputs and return the next scripted response."""
        self.recorded_calls.append(
            RecordedTurnCall(
                system_prompt=system_prompt,
                messages=tuple(messages),
                tools=tuple(tools),
            )
        )
        if self._index >= len(self._turns):
            raise LlmAdapterError(
                f"ScriptedLlmAdapter exhausted after {self._index} turn(s)."
            )
        item = self._turns[self._index]
        self._index += 1
        if callable(item):
            return item(system_prompt, messages, tools)
        return item
