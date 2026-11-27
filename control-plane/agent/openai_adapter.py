"""OpenAI Responses API adapter implementing ``LlmAdapter`` (Phase 4).

Translates provider-neutral ``LlmMessage``, ``ToolDefinition``, and
``LlmTurnResponse`` objects to/from the official OpenAI Python SDK's
``client.responses.create(...)`` API using ``strict: True`` function schemas
derived directly from ``control_plane.gateway.argument_validation``.

Security notes:
- Reads ``LLM_API_KEY`` and ``LLM_MODEL`` from configuration/environment and
  never hard-codes or logs credentials.
- Never executes tools itself; tool calls are returned as ``LlmToolCall``
  items for ``AgentLoop`` and ``AuditedGatewayCoordinator`` to enforce.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from typing import Any

import openai
from control_plane.agent.llm import (
    LlmAdapterError,
    LlmMessage,
    LlmToolCall,
    LlmTurnResponse,
    ToolDefinition,
)
from openai import OpenAI

DEFAULT_REQUEST_TIMEOUT_S: float = 60.0

_OPENAI_TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
# Keywords rejected by OpenAI Responses API strict=True JSON Schema subset.
_STRICT_UNSUPPORTED_KEYWORDS = frozenset(
    {
        "title",
        "default",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "minItems",
        "maxItems",
    }
)


def _encode_wire_tool_name(contract_name: str) -> str:
    """Convert a contract tool name (e.g. ``files.read``) into OpenAI's regex."""
    wire_name = contract_name.replace(".", "__")
    if not _OPENAI_TOOL_NAME_RE.match(wire_name):
        raise LlmAdapterError(
            f"Tool name {contract_name!r} cannot be mapped to a valid OpenAI tool name."
        )
    return wire_name


def _build_tool_name_maps(
    tools: Sequence[ToolDefinition],
) -> tuple[dict[str, str], dict[str, str]]:
    """Return ``(contract_to_wire, wire_to_contract)`` ensuring bijection."""
    contract_to_wire: dict[str, str] = {}
    wire_to_contract: dict[str, str] = {}
    for tool in tools:
        wire_name = _encode_wire_tool_name(tool.name)
        if wire_name in wire_to_contract and wire_to_contract[wire_name] != tool.name:
            raise LlmAdapterError(
                f"Tool name collision mapping '{tool.name}' -> '{wire_name}'."
            )
        contract_to_wire[tool.name] = wire_name
        wire_to_contract[wire_name] = tool.name
    return contract_to_wire, wire_to_contract


def _normalize_strict_schema_node(node: Any) -> Any:
    """Adapt an existing Pydantic JSON schema node for OpenAI ``strict=True``."""
    if isinstance(node, list):
        return [_normalize_strict_schema_node(item) for item in node]
    if not isinstance(node, dict):
        return node

    cleaned: dict[str, Any] = {}
    for key, val in node.items():
        if key in _STRICT_UNSUPPORTED_KEYWORDS:
            continue
        cleaned[key] = _normalize_strict_schema_node(val)

    if cleaned.get("type") == "object" or "properties" in cleaned:
        cleaned.setdefault("type", "object")
        props = cleaned.get("properties")
        if isinstance(props, dict):
            cleaned["required"] = list(props.keys())
        else:
            cleaned["properties"] = {}
            cleaned["required"] = []
        cleaned["additionalProperties"] = False

    return cleaned


def to_openai_strict_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    """Build an OpenAI ``strict=True`` parameter object from an existing schema."""
    if not parameters:
        return {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }
    normalized = _normalize_strict_schema_node(parameters)
    if not isinstance(normalized, dict):
        raise LlmAdapterError("Tool parameters schema must be a JSON object.")
    normalized.setdefault("type", "object")
    normalized.setdefault("properties", {})
    normalized["required"] = list(normalized["properties"].keys())
    normalized["additionalProperties"] = False
    return normalized


def build_responses_tools(
    tools: Sequence[ToolDefinition],
    contract_to_wire: dict[str, str],
) -> list[dict[str, Any]]:
    """Build flat Responses API ``FunctionToolParam`` definitions."""
    out: list[dict[str, Any]] = []
    for tool in tools:
        out.append(
            {
                "type": "function",
                "name": contract_to_wire[tool.name],
                "description": tool.description or tool.name,
                "parameters": to_openai_strict_parameters(tool.parameters),
                "strict": True,
            }
        )
    return out


def build_responses_input(
    messages: Sequence[LlmMessage],
    contract_to_wire: dict[str, str],
) -> list[dict[str, Any]]:
    """Map provider-neutral ``LlmMessage`` history to Responses API input items."""
    items: list[dict[str, Any]] = []
    for msg in messages:
        if msg.role == "system":
            items.append({"role": "system", "content": msg.content})
        elif msg.role == "user":
            items.append({"role": "user", "content": msg.content})
        elif msg.role == "assistant":
            if msg.content:
                items.append({"role": "assistant", "content": msg.content})
            for tc in msg.tool_calls:
                wire_name = contract_to_wire.get(tc.tool) or _encode_wire_tool_name(
                    tc.tool
                )
                items.append(
                    {
                        "type": "function_call",
                        "call_id": tc.call_id,
                        "name": wire_name,
                        "arguments": json.dumps(
                            tc.args, ensure_ascii=False, separators=(",", ":")
                        ),
                    }
                )
        elif msg.role == "tool":
            if not msg.tool_call_id:
                raise LlmAdapterError("Tool result message is missing tool_call_id.")
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": msg.tool_call_id,
                    "output": msg.content,
                }
            )
        else:
            raise LlmAdapterError(f"Unsupported message role: {msg.role!r}.")
    return items


def _strip_optional_nulls(
    parsed_args: dict[str, Any], tool_def: ToolDefinition | None
) -> dict[str, Any]:
    """Omit keys set to ``None`` when the tool schema declares them nullable."""
    if tool_def is None or not tool_def.parameters:
        return parsed_args
    props = tool_def.parameters.get("properties", {})
    cleaned: dict[str, Any] = {}
    for key, val in parsed_args.items():
        if val is None:
            prop_schema = props.get(key)
            if isinstance(prop_schema, dict) and any(
                isinstance(opt, dict) and opt.get("type") == "null"
                for opt in prop_schema.get("anyOf", [])
            ):
                continue
        cleaned[key] = val
    return cleaned


def _parse_responses_output(
    response: Any,
    wire_to_contract: dict[str, str],
    tools_by_name: dict[str, ToolDefinition],
) -> LlmTurnResponse:
    """Translate an OpenAI ``Response`` object into ``LlmTurnResponse``."""
    resp_error = getattr(response, "error", None)
    if resp_error is not None:
        msg = getattr(resp_error, "message", None) or str(resp_error)
        raise LlmAdapterError(f"OpenAI response error: {msg}")

    status = getattr(response, "status", None)
    if status == "failed":
        raise LlmAdapterError("OpenAI response failed.")
    if status == "incomplete":
        details = getattr(response, "incomplete_details", None)
        reason = getattr(details, "reason", None) or "unknown"
        raise LlmAdapterError(f"OpenAI response incomplete: {reason}.")

    output_items = getattr(response, "output", None)
    if output_items is None:
        raise LlmAdapterError("OpenAI response missing 'output' items.")

    tool_calls: list[LlmToolCall] = []
    text_parts: list[str] = []

    for item in output_items:
        item_type = getattr(item, "type", None)
        if item_type == "refusal":
            refusal_text = getattr(item, "refusal", None) or "Request refused."
            raise LlmAdapterError(f"Provider refusal: {refusal_text}")

        if item_type == "function_call":
            wire_name = getattr(item, "name", None)
            call_id = getattr(item, "call_id", None)
            raw_arguments = getattr(item, "arguments", None)

            if not isinstance(wire_name, str) or wire_name not in wire_to_contract:
                raise LlmAdapterError(
                    f"Model requested unknown or unmapped tool {wire_name!r}."
                )
            if not isinstance(call_id, str) or not call_id.strip():
                raise LlmAdapterError("Model function_call missing valid call_id.")
            if not isinstance(raw_arguments, str) or not raw_arguments.strip():
                raise LlmAdapterError(
                    f"Malformed empty arguments for tool '{wire_name}'."
                )

            try:
                parsed = json.loads(raw_arguments)
            except json.JSONDecodeError as exc:
                raise LlmAdapterError(
                    f"Malformed JSON arguments for tool '{wire_name}': {exc}"
                ) from exc

            if not isinstance(parsed, dict):
                raise LlmAdapterError(
                    f"Arguments for tool '{wire_name}' must decode to a JSON object."
                )

            contract_name = wire_to_contract[wire_name]
            cleaned_args = _strip_optional_nulls(
                parsed, tools_by_name.get(contract_name)
            )
            tool_calls.append(
                LlmToolCall(call_id=call_id, tool=contract_name, args=cleaned_args)
            )
            continue

        if item_type == "message":
            content_list = getattr(item, "content", None) or []
            for part in content_list:
                part_type = getattr(part, "type", None)
                if part_type == "refusal":
                    refusal_text = getattr(part, "refusal", None) or "Request refused."
                    raise LlmAdapterError(f"Provider refusal: {refusal_text}")
                if part_type == "output_text":
                    part_text = getattr(part, "text", None)
                    if isinstance(part_text, str):
                        text_parts.append(part_text)

    assistant_text = "".join(text_parts)
    if not assistant_text and not tool_calls:
        fallback_text = getattr(response, "output_text", None)
        if isinstance(fallback_text, str):
            assistant_text = fallback_text

    return LlmTurnResponse(
        tool_calls=tuple(tool_calls),
        assistant_text=assistant_text or None,
    )


class OpenAIResponsesAdapter:
    """``LlmAdapter`` implementation backed by the OpenAI Responses API."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        client: Any | None = None,
        timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise LlmAdapterError("LLM_MODEL must be a non-empty string.")
        self._model = model.strip()
        self._timeout_s = timeout_s

        if client is not None:
            self._client = client
        else:
            if not isinstance(api_key, str) or not api_key.strip():
                raise LlmAdapterError("LLM_API_KEY is not configured.")
            self._client = OpenAI(api_key=api_key, timeout=timeout_s)

    @classmethod
    def from_env(
        cls,
        *,
        client: Any | None = None,
        timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S,
    ) -> OpenAIResponsesAdapter:
        """Construct an adapter from ``LLM_API_KEY`` and ``LLM_MODEL`` env vars."""
        api_key = os.environ.get("LLM_API_KEY")
        model = os.environ.get("LLM_MODEL")
        if client is None and (not api_key or not api_key.strip()):
            raise LlmAdapterError("LLM_API_KEY environment variable is not set.")
        if not model or not model.strip():
            raise LlmAdapterError("LLM_MODEL environment variable is not set.")
        return cls(
            model=model,
            api_key=api_key,
            client=client,
            timeout_s=timeout_s,
        )

    def next_turn(
        self,
        *,
        system_prompt: str,
        messages: Sequence[LlmMessage],
        tools: Sequence[ToolDefinition],
    ) -> LlmTurnResponse:
        """Invoke ``client.responses.create`` and return ``LlmTurnResponse``."""
        contract_to_wire, wire_to_contract = _build_tool_name_maps(tools)
        tools_by_name = {tool.name: tool for tool in tools}
        request_tools = build_responses_tools(tools, contract_to_wire)
        request_input = build_responses_input(messages, contract_to_wire)

        kwargs: dict[str, Any] = {
            "model": self._model,
            "instructions": system_prompt,
            "input": request_input,
        }
        if request_tools:
            kwargs["tools"] = request_tools

        try:
            response = self._client.responses.create(**kwargs)
        except openai.APITimeoutError as exc:
            raise LlmAdapterError("OpenAI Responses API request timed out.") from exc
        except openai.AuthenticationError as exc:
            raise LlmAdapterError(
                "OpenAI authentication failed; verify LLM_API_KEY."
            ) from exc
        except openai.OpenAIError as exc:
            raise LlmAdapterError(
                f"OpenAI SDK error ({type(exc).__name__}): {exc}"
            ) from exc

        return _parse_responses_output(response, wire_to_contract, tools_by_name)
