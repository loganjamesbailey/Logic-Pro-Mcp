from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from daemon.rpc_client import (
    BridgeRpcClient,
    BridgeRpcProtocolError,
    BridgeRpcRemoteError,
    BridgeRpcTimeoutError,
)
from daemon.rpc_contract import (
    AUTH_PROTOCOL,
    authentication_request_proof,
    authentication_server_proof,
)

Response = dict[str, Any] | bytes | None
Responder = Callable[[dict[str, Any], int], Awaitable[Response]]


async def _with_server(
    responder: Responder,
    scenario: Callable[[int, list[dict[str, Any]]], Awaitable[None]],
    *,
    token: str = "x" * 32,
) -> None:
    requests: list[dict[str, Any]] = []
    connection_count = 0

    async def handle(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        nonlocal connection_count
        connection_count += 1
        connection_number = connection_count
        try:
            client_hello = json.loads((await reader.readline()).decode("utf-8"))
            client_nonce = client_hello["client_nonce"]
            server_nonce = f"{connection_number:064x}"
            server_hello = {
                "auth": AUTH_PROTOCOL,
                "client_nonce": client_nonce,
                "server_nonce": server_nonce,
                "server_proof": authentication_server_proof(
                    token,
                    client_nonce=client_nonce,
                    server_nonce=server_nonce,
                ),
            }
            writer.write(
                json.dumps(server_hello, separators=(",", ":")).encode("utf-8") + b"\n"
            )
            await writer.drain()
            request = json.loads((await reader.readline()).decode("utf-8"))
            requests.append(request)
            response = await responder(request, connection_number)
            if isinstance(response, bytes):
                writer.write(response)
                await writer.drain()
            elif response is not None:
                writer.write(
                    json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n"
                )
                await writer.drain()
            try:
                await asyncio.wait_for(reader.read(), timeout=0.2)
            except TimeoutError:
                pass
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = int(server.sockets[0].getsockname()[1])
    try:
        await scenario(port, requests)
    finally:
        server.close()
        await server.wait_closed()


def test_each_call_uses_a_fresh_connection_and_exact_envelope() -> None:
    async def responder(
        request: dict[str, Any],
        _connection: int,
    ) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {"method": request["method"]},
        }

    async def scenario(port: int, requests: list[dict[str, Any]]) -> None:
        client = BridgeRpcClient(
            host="127.0.0.1",
            port=port,
            token="x" * 32,
            timeout_seconds=1,
        )
        first, second = await asyncio.gather(
            client.call("bridge.health", {}),
            client.call("bridge.capabilities", {}),
        )
        assert first == {"method": "bridge.health"}
        assert second == {"method": "bridge.capabilities"}
        assert len(requests) == 2
        assert {item["method"] for item in requests} == {
            "bridge.health",
            "bridge.capabilities",
        }
        assert all(
            set(item) == {"jsonrpc", "id", "method", "params", "meta"}
            for item in requests
        )
        assert all(
            set(item["meta"])
            == {"auth", "client_nonce", "server_nonce", "request_proof"}
            for item in requests
        )
        assert all(
            item["meta"]["request_proof"]
            == authentication_request_proof("x" * 32, item)
            for item in requests
        )
        assert "x" * 32 not in json.dumps(requests)
        assert len({item["id"] for item in requests}) == 2

    asyncio.run(_with_server(responder, scenario))


def test_rogue_listener_receives_no_token_or_authenticated_request() -> None:
    token = "never-send-this-token-to-a-listener"

    async def scenario() -> None:
        received: list[bytes] = []
        handler_task: asyncio.Task[None] | None = None

        async def handle(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            nonlocal handler_task
            handler_task = asyncio.current_task()
            try:
                client_hello_raw = await reader.readline()
                received.append(client_hello_raw)
                client_hello = json.loads(client_hello_raw)
                writer.write(
                    json.dumps(
                        {
                            "auth": AUTH_PROTOCOL,
                            "client_nonce": client_hello["client_nonce"],
                            "server_nonce": "1" * 64,
                            "server_proof": "0" * 64,
                        },
                        separators=(",", ":"),
                    ).encode("utf-8")
                    + b"\n"
                )
                await writer.drain()
                try:
                    received.append(await reader.read())
                except (ConnectionResetError, OSError):
                    # A bounded close on the client side may fall back to an
                    # abrupt transport.abort() under scheduling pressure,
                    # which delivers a TCP reset instead of a clean EOF. That
                    # still proves zero further bytes reached this listener
                    # (a strictly stronger guarantee than an empty read), so
                    # it satisfies the same "nothing more was sent" property.
                    received.append(b"")
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = int(server.sockets[0].getsockname()[1])
        client = BridgeRpcClient(
            host="127.0.0.1",
            port=port,
            token=token,
            timeout_seconds=1,
        )
        try:
            with pytest.raises(BridgeRpcProtocolError, match="server authentication"):
                await client.call("logic.toggle_mute", {"track_id": "selected"})
            # server.wait_closed() only stops new connections; it does not
            # wait for the already-accepted handler task to finish reading.
            # Without this, the assertions below race the handler's second
            # read on any scheduler slower than instant loopback delivery.
            assert handler_task is not None
            await asyncio.wait_for(handler_task, timeout=5)
        finally:
            server.close()
            await server.wait_closed()

        assert len(received) == 2
        assert token.encode("utf-8") not in received[0]
        assert b"logic.toggle_mute" not in received[0]
        assert received[1] == b""

    asyncio.run(scenario())


def test_constructor_rejects_unsafe_or_unbounded_transport_settings() -> None:
    with pytest.raises(ValueError, match="loopback"):
        BridgeRpcClient(
            host="bridge.example.com",
            port=8765,
            token="x" * 32,
            timeout_seconds=1,
        )
    with pytest.raises(ValueError, match="port"):
        BridgeRpcClient(
            host="127.0.0.1",
            port=True,
            token="x" * 32,
            timeout_seconds=1,
        )
    with pytest.raises(ValueError, match="32 encoded bytes"):
        BridgeRpcClient(
            host="127.0.0.1",
            port=8765,
            token="too-short",
            timeout_seconds=1,
        )
    for timeout in (float("nan"), float("inf")):
        with pytest.raises(ValueError, match="finite"):
            BridgeRpcClient(
                host="127.0.0.1",
                port=8765,
                token="x" * 32,
                timeout_seconds=timeout,
            )
    with pytest.raises(ValueError, match="finite"):
        BridgeRpcClient(
            host="127.0.0.1",
            port=8765,
            token="x" * 32,
            timeout_seconds=10**1_000,
        )
    with pytest.raises(ValueError, match="max_response_bytes"):
        BridgeRpcClient(
            host="127.0.0.1",
            port=8765,
            token="x" * 32,
            timeout_seconds=1,
            max_response_bytes=True,
        )


def test_params_reject_non_string_json_object_keys_before_connecting() -> None:
    client = BridgeRpcClient(
        host="127.0.0.1",
        port=1,
        token="x" * 32,
        timeout_seconds=1,
    )

    with pytest.raises(ValueError, match="JSON-compatible"):
        asyncio.run(client.call("bridge.health", {"nested": {1: "coerced"}}))


def test_params_depth_limit_cannot_be_bypassed_by_a_shared_container() -> None:
    client = BridgeRpcClient(
        host="127.0.0.1",
        port=1,
        token="x" * 32,
        timeout_seconds=1,
    )
    shared: list[Any] = []
    for _ in range(60):
        shared = [shared]
    deep: list[Any] = shared
    for _ in range(10):
        deep = [deep]

    with pytest.raises(ValueError, match="JSON-compatible"):
        asyncio.run(
            client.call(
                "bridge.health",
                {"deep": deep, "shared": shared},
            )
        )


def test_mismatched_response_id_is_a_protocol_error() -> None:
    async def responder(
        request: dict[str, Any],
        _connection: int,
    ) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": f"wrong-{request['id']}", "result": {}}

    async def scenario(port: int, _requests: list[dict[str, Any]]) -> None:
        client = BridgeRpcClient(
            host="127.0.0.1",
            port=port,
            token="x" * 32,
            timeout_seconds=1,
        )
        with pytest.raises(BridgeRpcProtocolError, match="invalid response"):
            await client.call("bridge.health", {})

    asyncio.run(_with_server(responder, scenario))


def test_duplicate_response_keys_are_rejected() -> None:
    async def responder(
        request: dict[str, Any],
        _connection: int,
    ) -> bytes:
        request_id = request["id"]
        assert isinstance(request_id, str)
        return (
            b'{"jsonrpc":"2.0","jsonrpc":"2.0","id":"'
            + request_id.encode("ascii")
            + b'","result":{}}\n'
        )

    async def scenario(port: int, _requests: list[dict[str, Any]]) -> None:
        client = BridgeRpcClient(
            host="127.0.0.1",
            port=port,
            token="x" * 32,
            timeout_seconds=1,
        )
        with pytest.raises(BridgeRpcProtocolError, match="malformed JSON"):
            await client.call("bridge.health", {})

    asyncio.run(_with_server(responder, scenario))


def test_nonstandard_json_constants_are_rejected() -> None:
    async def responder(
        request: dict[str, Any],
        _connection: int,
    ) -> bytes:
        request_id = request["id"]
        assert isinstance(request_id, str)
        return (
            b'{"jsonrpc":"2.0","id":"'
            + request_id.encode("ascii")
            + b'","result":{"value":NaN}}\n'
        )

    async def scenario(port: int, _requests: list[dict[str, Any]]) -> None:
        client = BridgeRpcClient(
            host="127.0.0.1",
            port=port,
            token="x" * 32,
            timeout_seconds=1,
        )
        with pytest.raises(BridgeRpcProtocolError, match="malformed JSON"):
            await client.call("bridge.health", {})

    asyncio.run(_with_server(responder, scenario))


def test_malformed_response_does_not_retain_a_token_bearing_parser_error() -> None:
    token = "never-show-this-token-value-123456"

    async def responder(
        _request: dict[str, Any],
        _connection: int,
    ) -> bytes:
        return f'{{"unsafe":"{token}",}}\n'.encode()

    async def scenario(port: int, _requests: list[dict[str, Any]]) -> None:
        client = BridgeRpcClient(
            host="127.0.0.1",
            port=port,
            token=token,
            timeout_seconds=1,
        )
        with pytest.raises(BridgeRpcProtocolError) as raised:
            await client.call("bridge.health", {})
        error = raised.value
        assert token not in str(error)
        assert token not in repr(error)
        assert error.__cause__ is None
        assert error.__context__ is None

    asyncio.run(_with_server(responder, scenario, token=token))


def test_deeply_nested_response_is_a_protocol_error() -> None:
    async def responder(
        request: dict[str, Any],
        _connection: int,
    ) -> bytes:
        request_id = request["id"]
        assert isinstance(request_id, str)
        depth = 1_200
        return (
            b'{"jsonrpc":"2.0","id":"'
            + request_id.encode("ascii")
            + b'","result":'
            + (b"[" * depth)
            + b"null"
            + (b"]" * depth)
            + b"}\n"
        )

    async def scenario(port: int, _requests: list[dict[str, Any]]) -> None:
        client = BridgeRpcClient(
            host="127.0.0.1",
            port=port,
            token="x" * 32,
            timeout_seconds=1,
        )
        with pytest.raises(BridgeRpcProtocolError, match="malformed JSON"):
            await client.call("bridge.health", {})

    asyncio.run(_with_server(responder, scenario))


def test_response_limit_is_an_exact_byte_bound() -> None:
    sample = {
        "jsonrpc": "2.0",
        "id": "0" * 32,
        "result": {"value": "ok"},
    }
    response_size = len(
        json.dumps(sample, separators=(",", ":")).encode("utf-8") + b"\n"
    )

    async def responder(
        request: dict[str, Any],
        _connection: int,
    ) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {"value": "ok"},
        }

    async def scenario(port: int, _requests: list[dict[str, Any]]) -> None:
        exact_client = BridgeRpcClient(
            host="127.0.0.1",
            port=port,
            token="x" * 32,
            timeout_seconds=1,
            max_response_bytes=response_size,
        )
        assert await exact_client.call("bridge.health", {}) == {"value": "ok"}

        undersized_client = BridgeRpcClient(
            host="127.0.0.1",
            port=port,
            token="x" * 32,
            timeout_seconds=1,
            max_response_bytes=response_size - 1,
        )
        with pytest.raises(BridgeRpcProtocolError, match="response too large"):
            await undersized_client.call("bridge.health", {})

    asyncio.run(_with_server(responder, scenario))


def test_call_returns_within_a_bounded_time_when_the_peer_never_responds() -> None:
    async def responder(
        request: dict[str, Any],
        _connection: int,
    ) -> dict[str, Any] | None:
        # Simulate a wedged daemon: the handshake completes and the request
        # is read, but the peer takes far longer than the client's timeout
        # to respond. The client's finally-block close must not depend on
        # the peer ever unblocking within its own deadline.
        await asyncio.sleep(1.0)
        return {"jsonrpc": "2.0", "id": request["id"], "result": {"late": True}}

    async def scenario(port: int, requests: list[dict[str, Any]]) -> None:
        client = BridgeRpcClient(
            host="127.0.0.1",
            port=port,
            token="x" * 32,
            timeout_seconds=0.05,
        )
        started = asyncio.get_running_loop().time()
        with pytest.raises(BridgeRpcTimeoutError):
            await asyncio.wait_for(
                client.call("bridge.health", {}),
                timeout=2,
            )
        elapsed = asyncio.get_running_loop().time() - started
        assert elapsed < 2
        assert len(requests) == 1

    asyncio.run(_with_server(responder, scenario))


def test_timeout_closes_its_connection_and_does_not_poison_the_next_call() -> None:
    async def responder(
        request: dict[str, Any],
        connection: int,
    ) -> dict[str, Any] | None:
        if connection == 1:
            await asyncio.sleep(0.1)
            return {
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {"stale": True},
            }
        return {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {"fresh": True},
        }

    async def scenario(port: int, requests: list[dict[str, Any]]) -> None:
        client = BridgeRpcClient(
            host="127.0.0.1",
            port=port,
            token="x" * 32,
            timeout_seconds=0.02,
        )
        with pytest.raises(BridgeRpcTimeoutError):
            await client.call("logic.toggle_mute", {"track_id": "selected"})
        client = BridgeRpcClient(
            host="127.0.0.1",
            port=port,
            token="x" * 32,
            timeout_seconds=1,
        )
        assert await client.call("bridge.health", {}) == {"fresh": True}
        assert len(requests) == 2

    asyncio.run(_with_server(responder, scenario))


def test_cancellation_propagates_and_closes_the_connection() -> None:
    async def scenario() -> None:
        request_received = asyncio.Event()
        eof_received = asyncio.Event()
        handler_done = asyncio.Event()

        async def handle(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            try:
                await reader.readline()
                request_received.set()
                await reader.read()
                eof_received.set()
            finally:
                writer.close()
                await writer.wait_closed()
                handler_done.set()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = int(server.sockets[0].getsockname()[1])
        client = BridgeRpcClient(
            host="127.0.0.1",
            port=port,
            token="x" * 32,
            timeout_seconds=5,
        )
        task = asyncio.create_task(client.call("logic.toggle_mute", {}))
        try:
            await asyncio.wait_for(request_received.wait(), timeout=1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(eof_received.wait(), timeout=1)
        finally:
            if not task.done():
                task.cancel()
            server.close()
            await server.wait_closed()
            await asyncio.wait_for(handler_done.wait(), timeout=1)

    asyncio.run(scenario())


def test_standard_json_rpc_error_without_data_is_structured() -> None:
    async def responder(
        request: dict[str, Any],
        _connection: int,
    ) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request["id"],
            "error": {"code": -32603, "message": "Internal error"},
        }

    async def scenario(port: int, _requests: list[dict[str, Any]]) -> None:
        client = BridgeRpcClient(
            host="127.0.0.1",
            port=port,
            token="x" * 32,
            timeout_seconds=1,
        )
        with pytest.raises(BridgeRpcRemoteError) as raised:
            await client.call("bridge.health", {})
        assert raised.value.rpc_code == -32603
        assert raised.value.bridge_code is None
        assert raised.value.retryable is False
        assert raised.value.details == {}

    asyncio.run(_with_server(responder, scenario))


def test_bridge_error_data_rejects_unknown_or_missing_fields() -> None:
    invalid_data = (
        {
            "bridge_code": "INVALID",
            "retryable": False,
            "details": {},
            "unknown": True,
        },
        {"retryable": False, "details": {}},
    )

    async def scenario() -> None:
        for data in invalid_data:

            async def responder(
                request: dict[str, Any],
                _connection: int,
                *,
                response_data: dict[str, Any] = data,
            ) -> dict[str, Any]:
                return {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "error": {
                        "code": -32000,
                        "message": "Invalid bridge error",
                        "data": response_data,
                    },
                }

            async def check(
                port: int,
                _requests: list[dict[str, Any]],
            ) -> None:
                client = BridgeRpcClient(
                    host="127.0.0.1",
                    port=port,
                    token="x" * 32,
                    timeout_seconds=1,
                )
                with pytest.raises(
                    BridgeRpcProtocolError,
                    match="malformed error metadata",
                ):
                    await client.call("bridge.health", {})

            await _with_server(responder, check)

    asyncio.run(scenario())


def test_remote_bridge_error_is_structured_and_token_safe() -> None:
    token = "never-show-this-token-value-123456"

    async def responder(
        request: dict[str, Any],
        _connection: int,
    ) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request["id"],
            "error": {
                "code": -32000,
                "message": f"MIDI output is unavailable: {token}",
                "data": {
                    "bridge_code": "MIDI_PORT_NOT_FOUND",
                    "retryable": True,
                    "details": {
                        "available": [],
                        "credential": token,
                        "nested": [{token: token}],
                    },
                },
            },
        }

    async def scenario(port: int, _requests: list[dict[str, Any]]) -> None:
        client = BridgeRpcClient(
            host="127.0.0.1",
            port=port,
            token=token,
            timeout_seconds=1,
        )
        with pytest.raises(BridgeRpcRemoteError) as raised:
            await client.call("midi.list_outputs", {})
        error = raised.value
        assert error.rpc_code == -32000
        assert error.bridge_code == "MIDI_PORT_NOT_FOUND"
        assert error.retryable is True
        assert error.details == {
            "available": [],
            "credential": "<redacted>",
            "nested": [{"<redacted>": "<redacted>"}],
        }
        assert token not in str(error)
        assert token not in repr(error)
        assert token not in repr(error.details)
        assert token not in repr(client)

    asyncio.run(_with_server(responder, scenario, token=token))
