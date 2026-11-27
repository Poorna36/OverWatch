"""Unit tests for control_plane.agent.openai_adapter (mocked SDK, no live calls)."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import openai
import pytest
from control_plane.agent import (
    LlmAdapterError,
    LlmMessage,
    LlmToolCall,
    OpenAIResponsesAdapter,
    ToolDefinition,
    build_allowed_tools,
    run_session_loop,
)
from control_plane.audit.audit_writer import AuditWriter
from control_plane.db.models import SessionModel
from control_plane.db.session import create_db_engine, create_session_factory, init_db
from control_plane.events.event_bus import EventBus
from control_plane.gateway.approval_manager import ApprovalManager
from control_plane.gateway.argument_validation import ValidatedArguments
from control_plane.gateway.coordinator import AuditedGatewayCoordinator
from control_plane.gateway.pipeline import GatewayPreflightPipeline
from control_plane.gateway.policy_loader import load_policy
from control_plane.sessions.profile_loader import load_profile

_OPENAI_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
SESSION_ID = "s_01ABCDEFGHJKMNPTVW12345678"


def _make_fake_response(
    *,
    output: list | None = None,
    output_text: str = "",
    status: str = "completed",
    error: object | None = None,
    incomplete_details: object | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id="resp_1",
        status=status,
        error=error,
        incomplete_details=incomplete_details,
        output=output if output is not None else [],
        output_text=output_text,
    )


def _msg_item(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="message",
        content=[SimpleNamespace(type="output_text", text=text)],
    )


def _refusal_item(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="message",
        content=[SimpleNamespace(type="refusal", refusal=text)],
    )


def _fn_call_item(call_id: str, name: str, arguments: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="function_call",
        call_id=call_id,
        name=name,
        arguments=arguments,
    )


def test_from_env_validation_and_no_secret_logging(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Adapter requires LLM_API_KEY + LLM_MODEL and never logs credentials."""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("LLM_MODEL", "gpt-test")
    with pytest.raises(LlmAdapterError, match="LLM_API_KEY"):
        OpenAIResponsesAdapter.from_env()

    monkeypatch.setenv("LLM_API_KEY", "sk-dummy-secret-999")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    with pytest.raises(LlmAdapterError, match="LLM_MODEL"):
        OpenAIResponsesAdapter.from_env(client=MagicMock())

    monkeypatch.setenv("LLM_MODEL", "gpt-test")
    with caplog.at_level(logging.DEBUG):
        adapter = OpenAIResponsesAdapter.from_env(client=MagicMock())
    assert adapter is not None
    assert "sk-dummy-secret-999" not in "\n".join(
        r.getMessage() for r in caplog.records
    )


def test_strict_schemas_derived_from_argument_validation_for_all_tools() -> None:
    """All profile tools compile to OpenAI strict=True schemas with valid wire names."""
    policy = load_policy()
    for profile_name in ("coding", "general"):
        profile = load_profile(profile_name)
        tools = build_allowed_tools(profile, policy)

        mock_client = MagicMock()
        mock_client.responses.create.return_value = _make_fake_response(
            output=[_msg_item("done")]
        )
        adapter = OpenAIResponsesAdapter(model="gpt-test", client=mock_client)
        adapter.next_turn(
            system_prompt="sys",
            messages=[LlmMessage(role="user", content="hi")],
            tools=tools,
        )

        sent_tools = mock_client.responses.create.call_args.kwargs["tools"]
        assert len(sent_tools) == len(tools)
        for tool_def in sent_tools:
            assert tool_def["type"] == "function"
            assert tool_def["strict"] is True
            assert _OPENAI_NAME_RE.match(tool_def["name"])
            params = tool_def["parameters"]
            assert params["type"] == "object"
            assert params["additionalProperties"] is False
            assert set(params["required"]) == set(params["properties"].keys())


def test_request_construction_text_and_tool_call_mapping() -> None:
    """Adapter translates messages, function calls, and function_call_output."""
    policy = load_policy()
    coding = load_profile("coding")
    tools = build_allowed_tools(coding, policy)

    mock_client = MagicMock()
    mock_client.responses.create.side_effect = [
        _make_fake_response(
            output=[
                _fn_call_item(
                    "call_read_1",
                    "files__read",
                    '{"path":"README.md","max_bytes":null}',
                )
            ]
        ),
        _make_fake_response(output=[_msg_item("All read.")]),
    ]

    adapter = OpenAIResponsesAdapter(model="gpt-4.1-mini", client=mock_client)

    turn1 = adapter.next_turn(
        system_prompt="system role",
        messages=[LlmMessage(role="user", content="Read README")],
        tools=tools,
    )
    assert turn1.tool_calls == (
        LlmToolCall(
            call_id="call_read_1", tool="files.read", args={"path": "README.md"}
        ),
    )

    turn2 = adapter.next_turn(
        system_prompt="system role",
        messages=[
            LlmMessage(role="user", content="Read README"),
            LlmMessage(role="assistant", tool_calls=turn1.tool_calls),
            LlmMessage(
                role="tool",
                tool_call_id="call_read_1",
                tool_name="files.read",
                content='{"content":"hi"}',
            ),
        ],
        tools=tools,
    )
    assert turn2.tool_calls == ()
    assert turn2.assistant_text == "All read."

    second_kwargs = mock_client.responses.create.call_args_list[1].kwargs
    assert second_kwargs["model"] == "gpt-4.1-mini"
    assert second_kwargs["instructions"] == "system role"
    assert second_kwargs["input"] == [
        {"role": "user", "content": "Read README"},
        {
            "type": "function_call",
            "call_id": "call_read_1",
            "name": "files__read",
            "arguments": '{"path":"README.md"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_read_1",
            "output": '{"content":"hi"}',
        },
    ]


def test_agent_loop_end_to_end_with_mocked_openai_adapter(tmp_path: Path) -> None:
    """OpenAIResponsesAdapter runs tool calls via AuditedGatewayCoordinator."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    engine = create_db_engine(f"sqlite:///{tmp_path / 'loop_openai.db'}")
    init_db(engine)
    factory = create_session_factory(engine)
    with factory() as db:
        db.add(
            SessionModel(
                session_id=SESSION_ID,
                state="ACTIVE",
                profile="coding",
                owner_id="owner_test",
                dry_run=0,
                created_at="2026-11-27T10:00:00Z",
                updated_at="2026-11-27T10:00:00Z",
            )
        )
        db.commit()

    policy = load_policy()
    profile = load_profile("coding")
    event_bus = EventBus(factory)
    approval_mgr = ApprovalManager(factory, event_bus)
    coordinator = AuditedGatewayCoordinator(
        GatewayPreflightPipeline(policy=policy, workspace_root=workspace),
        AuditWriter(factory),
    )

    mock_client = MagicMock()
    mock_client.responses.create.side_effect = [
        _make_fake_response(
            output=[
                _fn_call_item(
                    "call_w1",
                    "files__write",
                    '{"path":"hello.txt","content":"world"}',
                )
            ]
        ),
        _make_fake_response(output=[_msg_item("Created hello.txt")]),
    ]
    adapter = OpenAIResponsesAdapter(model="gpt-4.1-mini", client=mock_client)

    executed: list[ValidatedArguments] = []

    def fake_executor(v: ValidatedArguments) -> dict:
        executed.append(v)
        return {"bytes": 5}

    try:
        res = run_session_loop(
            session_id=SESSION_ID,
            request_text="Write hello.txt",
            llm=adapter,
            coordinator=coordinator,
            approval_manager=approval_mgr,
            event_bus=event_bus,
            policy=policy,
            profile=profile,
            executor=fake_executor,
            workspace_root=workspace,
        )
        assert res.status == "completed"
        assert res.assistant_message == "Created hello.txt"
        assert res.calls_used == 1
        assert len(executed) == 1
        assert executed[0].args == {"path": "hello.txt", "content": "world"}
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "bad_response",
    [
        # Malformed JSON arguments
        _make_fake_response(output=[_fn_call_item("c1", "files__list", "{not-json")]),
        # Non-object JSON arguments
        _make_fake_response(output=[_fn_call_item("c1", "files__list", "[1, 2]")]),
        # Unknown/unmapped tool name
        _make_fake_response(output=[_fn_call_item("c1", "evil__tool", "{}")]),
        # Provider refusal item
        _make_fake_response(output=[SimpleNamespace(type="refusal", refusal="Nope")]),
        # Message-level refusal
        _make_fake_response(output=[_refusal_item("Policy refusal")]),
        # Failed response status
        _make_fake_response(status="failed", output=[]),
        # Incomplete response status
        _make_fake_response(
            status="incomplete",
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            output=[],
        ),
    ],
)
def test_malformed_outputs_and_refusals_raise_adapter_error(
    bad_response: SimpleNamespace,
) -> None:
    """Malformed args, unknown tools, refusals, and errors raise LlmAdapterError."""
    mock_client = MagicMock()
    mock_client.responses.create.return_value = bad_response
    adapter = OpenAIResponsesAdapter(model="gpt-test", client=mock_client)

    tools = [
        ToolDefinition(
            name="files.list",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        )
    ]

    with pytest.raises(LlmAdapterError):
        adapter.next_turn(
            system_prompt="sys",
            messages=[LlmMessage(role="user", content="hi")],
            tools=tools,
        )


def test_sdk_timeout_auth_and_api_errors_wrap_as_adapter_error() -> None:
    """OpenAI SDK exceptions map to LlmAdapterError without leaking keys."""
    req = httpx.Request("POST", "https://api.openai.com/v1/responses")
    resp = httpx.Response(401, request=req)

    for exc in [
        openai.APITimeoutError(request=req),
        openai.AuthenticationError("bad sk-secret-key", response=resp, body=None),
        openai.APIError("upstream failure", request=req, body=None),
    ]:
        mock_client = MagicMock()
        mock_client.responses.create.side_effect = exc
        adapter = OpenAIResponsesAdapter(model="gpt-test", client=mock_client)

        with pytest.raises(LlmAdapterError) as err_info:
            adapter.next_turn(
                system_prompt="sys",
                messages=[LlmMessage(role="user", content="hi")],
                tools=[],
            )
        assert "sk-secret-key" not in str(err_info.value)
