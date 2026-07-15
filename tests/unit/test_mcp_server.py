from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from daemon.mcp_server import MCP_RESOURCE_URIS, MCP_TOOL_METHODS, create_mcp_server
from daemon.rpc_client import BridgeRpcRemoteError


class RecordingClient:
    instances: list[RecordingClient] = []
    responses: dict[str, Any] = {}

    def __init__(
        self,
        *,
        host: str,
        port: int,
        token: str,
        timeout_seconds: float,
    ) -> None:
        self.settings = {
            "host": host,
            "port": port,
            "token": token,
            "timeout_seconds": timeout_seconds,
        }
        self.calls: list[tuple[str, dict[str, Any]]] = []
        type(self).instances.append(self)

    async def call(self, method: str, params: Mapping[str, Any]) -> Any:
        self.calls.append((method, dict(params)))
        return type(self).responses.get(
            method,
            {"proxied_method": method, "params": dict(params)},
        )


@pytest.fixture(autouse=True)
def _reset_recording_client() -> None:
    RecordingClient.instances = []
    RecordingClient.responses = {}


def _server():
    return create_mcp_server(
        host="127.0.0.1",
        port=8765,
        token="t" * 32,
        timeout_seconds=2.5,
        client_factory=RecordingClient,
    )


def test_constructor_rejects_non_finite_or_unrepresentable_timeout() -> None:
    for timeout in (float("inf"), 10**1000):
        with pytest.raises(ValueError, match="positive and finite"):
            create_mcp_server(
                host="127.0.0.1",
                port=8765,
                token="t" * 32,
                timeout_seconds=timeout,
                client_factory=RecordingClient,
            )


def test_registers_only_the_fixed_tools_and_resources_with_safe_annotations() -> None:
    async def scenario() -> None:
        server = _server()
        tools = {tool.name: tool for tool in await server.list_tools()}
        resources = {str(resource.uri) for resource in await server.list_resources()}

        assert set(tools) == set(MCP_TOOL_METHODS)
        assert resources == set(MCP_RESOURCE_URIS)
        assert all(
            tool.inputSchema.get("additionalProperties") is False
            for tool in tools.values()
        )
        assert all(tool.annotations is not None for tool in tools.values())
        assert all(tool.annotations.openWorldHint is False for tool in tools.values())
        assert tools["inspect_mixer_target"].annotations.readOnlyHint is True
        assert tools["load_cst_preset"].annotations.destructiveHint is True
        assert tools["toggle_mute"].annotations.idempotentHint is False
        assert tools["invoke_action"].annotations.idempotentHint is False
        assert tools["set_volume"].annotations.idempotentHint is True
        invoke_value = tools["invoke_action"].inputSchema["properties"]["value"]
        assert {option.get("type") for option in invoke_value["anyOf"]} == {
            "integer",
            "number",
        }
        track_id = tools["set_volume"].inputSchema["properties"]["track_id"]
        assert track_id["anyOf"][0]["maxLength"] == 20

    asyncio.run(scenario())


def test_tools_proxy_exact_daemon_methods_and_create_one_client_per_call() -> None:
    expected = [
        (
            "set_volume",
            {"track_id": "selected", "value": 0.5},
            {"track_id": "selected", "value": 0.5},
        ),
        ("toggle_mute", {"track_id": "2"}, {"track_id": "2"}),
        (
            "invoke_action",
            {"action_id": "transport.play"},
            {"action_id": "transport.play"},
        ),
        (
            "set_scripter_parameter",
            {
                "parameter_id": "scripter.retro-synth.filter-cutoff",
                "value": 0.25,
            },
            {
                "parameter_id": "scripter.retro-synth.filter-cutoff",
                "value": 0.25,
            },
        ),
        ("inspect_mixer_target", {"target_id": "audio-2"}, {"target_id": "audio-2"}),
        (
            "load_cst_preset",
            {
                "preset_id": "jimmy-vocal-chain",
                "target_id": "audio-2",
                "confirmation": (
                    "load jimmy-vocal-chain into audio-2 in disposable project"
                ),
            },
            {
                "preset_id": "jimmy-vocal-chain",
                "target_id": "audio-2",
                "confirmation": (
                    "load jimmy-vocal-chain into audio-2 in disposable project"
                ),
            },
        ),
    ]

    async def scenario() -> None:
        server = _server()
        for tool_name, arguments, daemon_params in expected:
            result = await server._tool_manager.call_tool(tool_name, arguments)
            assert result == {
                "proxied_method": MCP_TOOL_METHODS[tool_name],
                "params": daemon_params,
            }

        assert len(RecordingClient.instances) == len(expected)
        for client, (tool_name, _arguments, daemon_params) in zip(
            RecordingClient.instances,
            expected,
            strict=True,
        ):
            assert client.settings == {
                "host": "127.0.0.1",
                "port": 8765,
                "token": "t" * 32,
                "timeout_seconds": 2.5,
            }
            assert client.calls == [(MCP_TOOL_METHODS[tool_name], daemon_params)]

    asyncio.run(scenario())


def test_resources_proxy_daemon_and_return_json_without_token() -> None:
    token = "secret-token-that-must-never-be-returned"
    RecordingClient.responses = {
        "bridge.health": {"status": "ready"},
        "bridge.capabilities": {"rpc_methods": ["logic.set_volume"]},
    }

    async def scenario() -> None:
        server = create_mcp_server(
            host="127.0.0.1",
            port=8765,
            token=token,
            timeout_seconds=1,
            client_factory=RecordingClient,
        )
        health = await server.read_resource("logic-bridge://health")
        capabilities = await server.read_resource("logic-bridge://capabilities")

        assert json.loads(health[0].content) == {"status": "ready"}
        assert json.loads(capabilities[0].content) == {
            "rpc_methods": ["logic.set_volume"]
        }
        assert token not in health[0].content
        assert token not in capabilities[0].content
        assert len(RecordingClient.instances) == 2

    asyncio.run(scenario())


def test_unknown_tool_fields_are_rejected_before_daemon_dispatch() -> None:
    async def scenario() -> None:
        server = _server()
        with pytest.raises(ToolError, match="Extra inputs are not permitted"):
            await server._tool_manager.call_tool(
                "set_volume",
                {"track_id": "selected", "value": 0.5, "raw_midi": [176, 7, 64]},
            )
        assert RecordingClient.instances == []

    asyncio.run(scenario())


def test_invoke_action_rejects_explicit_null_without_daemon_dispatch() -> None:
    async def scenario() -> None:
        server = _server()
        with pytest.raises(ToolError):
            await server._tool_manager.call_tool(
                "invoke_action",
                {"action_id": "track.selected.mute_toggle", "value": None},
            )
        assert RecordingClient.instances == []

    asyncio.run(scenario())


def test_remote_daemon_errors_retain_safe_bridge_metadata() -> None:
    token = "token-never-echoed-in-tool-errors-123"

    class FailingClient(RecordingClient):
        async def call(self, method: str, params: Mapping[str, Any]) -> Any:
            raise BridgeRpcRemoteError(
                f"MIDI output is unavailable: {token}",
                rpc_code=-32000,
                bridge_code="MIDI_PORT_NOT_FOUND",
                retryable=True,
                details={"available": [], "unsafe_debug": token},
            )

    async def scenario() -> None:
        server = create_mcp_server(
            host="127.0.0.1",
            port=8765,
            token=token,
            timeout_seconds=1,
            client_factory=FailingClient,
        )
        with pytest.raises(ToolError) as raised:
            await server._tool_manager.call_tool(
                "set_volume",
                {"track_id": "selected", "value": 0.5},
            )
        payload = json.loads(str(raised.value).split(": ", maxsplit=1)[1])
        assert payload == {
            "error": {
                "rpc_code": -32000,
                "bridge_code": "MIDI_PORT_NOT_FOUND",
                "message": "MIDI output is unavailable: <redacted>",
                "retryable": True,
                "details": {"available": [], "unsafe_debug": "<redacted>"},
            }
        }
        assert token not in str(raised.value)

    asyncio.run(scenario())


def test_factory_rejects_remote_hosts_and_weak_tokens() -> None:
    with pytest.raises(ValueError, match="literal loopback"):
        create_mcp_server(
            host="example.com",
            port=8765,
            token="t" * 32,
            timeout_seconds=1,
        )
    with pytest.raises(ValueError, match="32 encoded bytes"):
        create_mcp_server(
            host="127.0.0.1",
            port=8765,
            token="weak",
            timeout_seconds=1,
        )
    with pytest.raises(ValueError, match="port must be between"):
        create_mcp_server(
            host="127.0.0.1",
            port=True,
            token="t" * 32,
            timeout_seconds=1,
        )
    with pytest.raises(ValueError, match="positive and finite"):
        create_mcp_server(
            host="127.0.0.1",
            port=8765,
            token="t" * 32,
            timeout_seconds=float("nan"),
        )
