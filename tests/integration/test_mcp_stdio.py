from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
from datetime import timedelta
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from daemon.commands import LogicCommandService
from daemon.config import BridgeConfig
from daemon.rpc_contract import (
    AUTH_PROTOCOL,
    METHOD_SPECS,
    authentication_request_proof,
    authentication_server_proof,
)
from daemon.server import JsonRpcServer
from tests.fakes import (
    RecordingCstPresetLoader,
    RecordingMidiTransport,
    valid_mapping,
)


class BlockingRecordingMidiTransport(RecordingMidiTransport):
    def __init__(self) -> None:
        super().__init__()
        self.dispatch_started = threading.Event()
        self.dispatch_release = threading.Event()

    def send_cc(
        self,
        *,
        channel: int,
        control: int,
        value: int,
        release_value: int | None = None,
    ) -> int:
        self.dispatch_started.set()
        if not self.dispatch_release.wait(timeout=2):
            raise AssertionError("test did not release the delayed MIDI dispatch")
        return super().send_cc(
            channel=channel,
            control=control,
            value=value,
            release_value=release_value,
        )


def _real_server_config(*, read_timeout_seconds: float) -> BridgeConfig:
    raw = valid_mapping()
    server = raw["server"]
    assert isinstance(server, dict)
    server["port"] = 0
    server["read_timeout_seconds"] = read_timeout_seconds
    return BridgeConfig.from_mapping(raw)


def test_official_stdio_client_discovers_resources_and_proxies_a_tool() -> None:
    async def scenario() -> None:
        requests: list[dict[str, Any]] = []
        connection_count = 0
        token = "integration-token-with-at-least-32-bytes"

        async def handle(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            nonlocal connection_count
            connection_count += 1
            try:
                client_hello = json.loads((await reader.readline()).decode("utf-8"))
                server_nonce = f"{connection_count:064x}"
                writer.write(
                    json.dumps(
                        {
                            "auth": AUTH_PROTOCOL,
                            "client_nonce": client_hello["client_nonce"],
                            "server_nonce": server_nonce,
                            "server_proof": authentication_server_proof(
                                token,
                                client_nonce=client_hello["client_nonce"],
                                server_nonce=server_nonce,
                            ),
                        },
                        separators=(",", ":"),
                    ).encode("utf-8")
                    + b"\n"
                )
                await writer.drain()
                request = json.loads((await reader.readline()).decode("utf-8"))
                requests.append(request)
                method = request["method"]
                if method == "bridge.health":
                    result: dict[str, Any] = {"status": "ready"}
                elif method == "bridge.capabilities":
                    result = {"rpc_methods": ["logic.set_volume"]}
                elif method == "logic.set_volume":
                    result = {
                        "action": "track.selected.volume",
                        "events": [
                            {"state": "requested"},
                            {"state": "dispatched"},
                            {"state": "unknown"},
                        ],
                    }
                else:  # pragma: no cover - fixed adapter contract
                    raise AssertionError(f"unexpected daemon method: {method}")
                writer.write(
                    json.dumps(
                        {"jsonrpc": "2.0", "id": request["id"], "result": result},
                        separators=(",", ":"),
                    ).encode("utf-8")
                    + b"\n"
                )
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        daemon = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = int(daemon.sockets[0].getsockname()[1])
        environment = {
            **os.environ,
            "LOGIC_BRIDGE_HOST": "127.0.0.1",
            "LOGIC_BRIDGE_PORT": str(port),
            "LOGIC_BRIDGE_TOKEN": token,
            "LOGIC_BRIDGE_MCP_TIMEOUT_SECONDS": "2",
        }
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "daemon.mcp_server"],
            env=environment,
            cwd=Path(__file__).parents[2],
        )

        try:
            with tempfile.TemporaryFile(mode="w+") as stderr:
                async with stdio_client(parameters, errlog=stderr) as (
                    read_stream,
                    write_stream,
                ):
                    async with ClientSession(
                        read_stream,
                        write_stream,
                        read_timeout_seconds=timedelta(seconds=3),
                    ) as session:
                        initialized = await session.initialize()
                        assert initialized.serverInfo.name == (
                            "AI-to-Logic Pro IPC Bridge"
                        )

                        tools = await session.list_tools()
                        assert {tool.name for tool in tools.tools} == {
                            "set_volume",
                            "toggle_mute",
                            "invoke_action",
                            "set_scripter_parameter",
                            "inspect_mixer_target",
                            "load_cst_preset",
                        }
                        assert all(
                            tool.inputSchema.get("additionalProperties") is False
                            for tool in tools.tools
                        )

                        resources = await session.list_resources()
                        assert {
                            str(resource.uri) for resource in resources.resources
                        } == {
                            "logic-bridge://health",
                            "logic-bridge://capabilities",
                        }
                        health = await session.read_resource("logic-bridge://health")
                        capabilities = await session.read_resource(
                            "logic-bridge://capabilities"
                        )
                        assert json.loads(health.contents[0].text) == {
                            "status": "ready"
                        }
                        assert json.loads(capabilities.contents[0].text) == {
                            "rpc_methods": ["logic.set_volume"]
                        }

                        called = await session.call_tool(
                            "set_volume",
                            {"track_id": "selected", "value": 0.5},
                        )
                        assert called.isError is False
                        assert called.structuredContent == {
                            "action": "track.selected.volume",
                            "events": [
                                {"state": "requested"},
                                {"state": "dispatched"},
                                {"state": "unknown"},
                            ],
                        }
        finally:
            daemon.close()
            await daemon.wait_closed()

        assert [request["method"] for request in requests] == [
            "bridge.health",
            "bridge.capabilities",
            "logic.set_volume",
        ]
        assert all(
            request["meta"]["request_proof"]
            == authentication_request_proof(token, request)
            for request in requests
        )
        assert token not in json.dumps(requests)
        assert len({request["id"] for request in requests}) == 3

    asyncio.run(scenario())


def test_official_stdio_client_composes_with_real_authenticated_daemon() -> None:
    async def scenario() -> None:
        token = "real-composition-token-with-at-least-32-bytes"
        server_read_timeout = 0.1
        mcp_timeout = 1.0
        config = _real_server_config(read_timeout_seconds=server_read_timeout)
        midi = BlockingRecordingMidiTransport()
        cst_loader = RecordingCstPresetLoader()
        service = LogicCommandService(
            config=config,
            midi=midi,
            cst_loader=cst_loader,
        )
        daemon = JsonRpcServer(
            config=config.server,
            service=service,
            auth_token=token,
        )
        await daemon.start()

        environment = {
            **os.environ,
            "LOGIC_BRIDGE_HOST": config.server.host,
            "LOGIC_BRIDGE_PORT": str(daemon.bound_port),
            "LOGIC_BRIDGE_TOKEN": token,
            "LOGIC_BRIDGE_MCP_TIMEOUT_SECONDS": str(mcp_timeout),
        }
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "daemon.mcp_server"],
            env=environment,
            cwd=Path(__file__).parents[2],
        )

        stderr_text = ""
        exposed_payloads: list[object] = []
        mutation_task: asyncio.Task[Any] | None = None
        try:
            with tempfile.TemporaryFile(mode="w+") as stderr:
                async with stdio_client(parameters, errlog=stderr) as (
                    read_stream,
                    write_stream,
                ):
                    async with ClientSession(
                        read_stream,
                        write_stream,
                        read_timeout_seconds=timedelta(seconds=2),
                    ) as session:
                        initialized = await session.initialize()
                        assert initialized.serverInfo.name == (
                            "AI-to-Logic Pro IPC Bridge"
                        )

                        health_response = await session.read_resource(
                            "logic-bridge://health"
                        )
                        capability_response = await session.read_resource(
                            "logic-bridge://capabilities"
                        )
                        health = json.loads(health_response.contents[0].text)
                        capabilities = json.loads(capability_response.contents[0].text)
                        exposed_payloads.extend((health, capabilities))

                        assert health["status"] == "running"
                        assert health["readiness"]["status"] == "ready"
                        assert health["midi_connection"] == "available"
                        assert capabilities["rpc_methods"] == list(METHOD_SPECS)
                        assert set(capabilities["contract"]["methods"]) == set(
                            METHOD_SPECS
                        )
                        assert (
                            capabilities["contract"]["methods"]["logic.set_volume"][
                                "availability"
                            ]["available"]
                            is True
                        )

                        mutation_task = asyncio.create_task(
                            session.call_tool(
                                "set_volume",
                                {"track_id": "selected", "value": 0.5},
                            )
                        )
                        assert await asyncio.to_thread(
                            midi.dispatch_started.wait,
                            mcp_timeout / 2,
                        )
                        await asyncio.sleep(server_read_timeout * 1.5)
                        assert mutation_task.done() is False
                        midi.dispatch_release.set()
                        called = await mutation_task
                        exposed_payloads.append(called.structuredContent)

                        assert called.isError is False
                        assert called.structuredContent is not None
                        assert called.structuredContent["action"] == (
                            "track.selected.volume"
                        )
                        assert [
                            event["state"]
                            for event in called.structuredContent["events"]
                        ] == ["requested", "dispatched", "unknown"]
                        assert midi.sent == [(0, 7, 64)]

                stderr.seek(0)
                stderr_text = stderr.read()
        finally:
            midi.dispatch_release.set()
            if mutation_task is not None and not mutation_task.done():
                mutation_task.cancel()
                await asyncio.gather(mutation_task, return_exceptions=True)
            await daemon.close()

        assert cst_loader.closed is True
        assert token not in json.dumps(exposed_payloads)
        assert token not in stderr_text
        assert "traceback" not in stderr_text.lower()

    asyncio.run(scenario())
