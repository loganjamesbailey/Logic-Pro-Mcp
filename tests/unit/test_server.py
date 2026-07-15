from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import time
from collections import deque
from typing import cast

from daemon.commands import LogicCommandService
from daemon.config import ServerConfig
from daemon.server import JsonRpcServer


VALID_TOKEN = "v" * 32


class _FakeReader:
    def __init__(self, *lines: bytes) -> None:
        self._lines = deque(lines)

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.popleft()
        return b""


class _StalledReader:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def readline(self) -> bytes:
        self.started.set()
        await self.release.wait()
        return b""


class _FakeTransport:
    def __init__(self) -> None:
        self.aborted = False

    def abort(self) -> None:
        self.aborted = True


class _FakeWriter:
    def __init__(self, *, stall_drain: bool, stall_close: bool) -> None:
        self.stall_drain = stall_drain
        self.stall_close = stall_close
        self.drain_release = asyncio.Event()
        self.close_release = asyncio.Event()
        self.writes: list[bytes] = []
        self.close_calls = 0
        self.wait_closed_calls = 0
        self.transport = _FakeTransport()

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        if self.stall_drain:
            await self.drain_release.wait()

    def close(self) -> None:
        self.close_calls += 1

    async def wait_closed(self) -> None:
        self.wait_closed_calls += 1
        if self.stall_close:
            await self.close_release.wait()


class _NoopService:
    def close(self) -> None:
        return None


def _server(*, write_timeout_seconds: float = 0.01) -> JsonRpcServer:
    return JsonRpcServer(
        config=ServerConfig(
            read_timeout_seconds=1.0,
            write_timeout_seconds=write_timeout_seconds,
            max_connections=1,
        ),
        service=cast(LogicCommandService, _NoopService()),
        auth_token=VALID_TOKEN,
    )


async def _wait_until_done(task: asyncio.Task[None], *, timeout: float) -> bool:
    done, _pending = await asyncio.wait({task}, timeout=timeout)
    return task in done


def test_stalled_response_drain_is_bounded_and_releases_connection_slots() -> None:
    async def scenario() -> None:
        server = _server()
        reader = _FakeReader(b"{\n")
        writer = _FakeWriter(stall_drain=True, stall_close=True)
        task = asyncio.create_task(
            server._handle_client(  # noqa: SLF001
                cast(asyncio.StreamReader, reader),
                cast(asyncio.StreamWriter, writer),
            )
        )

        try:
            assert await _wait_until_done(task, timeout=0.2)
            await task
            response = json.loads(writer.writes[0])
            assert response["error"]["code"] == -32001
            assert response["error"]["data"]["bridge_code"] == "AUTHENTICATION_REQUIRED"
            assert writer.close_calls == 1
            assert writer.wait_closed_calls == 0
            assert writer.transport.aborted is True
            assert server._clients == set()  # noqa: SLF001
            assert server._client_tasks == set()  # noqa: SLF001
        finally:
            writer.drain_release.set()
            writer.close_release.set()
            if not task.done():
                await task

    asyncio.run(scenario())


def test_stalled_wait_closed_is_bounded_and_releases_connection_slots() -> None:
    async def scenario() -> None:
        server = _server()
        reader = _FakeReader(b"")
        writer = _FakeWriter(stall_drain=False, stall_close=True)
        task = asyncio.create_task(
            server._handle_client(  # noqa: SLF001
                cast(asyncio.StreamReader, reader),
                cast(asyncio.StreamWriter, writer),
            )
        )

        try:
            assert await _wait_until_done(task, timeout=0.2)
            await task
            assert writer.writes == []
            assert writer.close_calls == 1
            assert writer.wait_closed_calls == 1
            assert writer.transport.aborted is True
            assert server._clients == set()  # noqa: SLF001
            assert server._client_tasks == set()  # noqa: SLF001
        finally:
            writer.close_release.set()
            if not task.done():
                await task

    asyncio.run(scenario())


def test_server_close_recovers_active_task_when_writer_close_stalls() -> None:
    async def scenario() -> None:
        server = _server()
        reader = _StalledReader()
        writer = _FakeWriter(stall_drain=False, stall_close=True)
        task = asyncio.create_task(
            server._handle_client(  # noqa: SLF001
                cast(asyncio.StreamReader, reader),
                cast(asyncio.StreamWriter, writer),
            )
        )

        try:
            await asyncio.wait_for(reader.started.wait(), timeout=0.2)
            assert len(server._clients) == 1  # noqa: SLF001
            assert len(server._client_tasks) == 1  # noqa: SLF001

            await asyncio.wait_for(server.close(), timeout=0.2)

            assert task.done()
            assert writer.close_calls == 2
            assert writer.wait_closed_calls == 1
            assert writer.transport.aborted is True
            assert server._clients == set()  # noqa: SLF001
            assert server._client_tasks == set()  # noqa: SLF001
        finally:
            reader.release.set()
            writer.close_release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_connect_during_shutdown_reports_a_non_retryable_closing_error() -> None:
    async def scenario() -> None:
        server = _server()
        server._closing = True  # noqa: SLF001
        reader = _FakeReader(b"{}\n")
        writer = _FakeWriter(stall_drain=False, stall_close=False)

        await server._handle_client(  # noqa: SLF001
            cast(asyncio.StreamReader, reader),
            cast(asyncio.StreamWriter, writer),
        )

        response = json.loads(writer.writes[0])
        assert response["error"]["data"]["bridge_code"] == "SERVER_CLOSING"
        assert response["error"]["data"]["retryable"] is False

    asyncio.run(scenario())


def test_write_response_falls_back_to_internal_error_for_non_finite_floats() -> None:
    async def scenario() -> None:
        server = _server()
        writer = _FakeWriter(stall_drain=False, stall_close=False)

        written, _deadline = await server._write_response(  # noqa: SLF001
            cast(asyncio.StreamWriter, writer),
            {"jsonrpc": "2.0", "id": 7, "result": {"value": float("nan")}},
        )

        assert written is True
        response = json.loads(writer.writes[0])
        assert response == {
            "jsonrpc": "2.0",
            "id": 7,
            "error": {"code": -32603, "message": "Internal error"},
        }

    asyncio.run(scenario())


def test_write_response_falls_back_to_internal_error_for_unserializable_result() -> (
    None
):
    async def scenario() -> None:
        server = _server()
        writer = _FakeWriter(stall_drain=False, stall_close=False)

        written, _deadline = await server._write_response(  # noqa: SLF001
            cast(asyncio.StreamWriter, writer),
            {"jsonrpc": "2.0", "id": 9, "result": {"value": object()}},
        )

        assert written is True
        response = json.loads(writer.writes[0])
        assert response == {
            "jsonrpc": "2.0",
            "id": 9,
            "error": {"code": -32603, "message": "Internal error"},
        }

    asyncio.run(scenario())


class _SlowClosingService:
    def __init__(self, *, block_seconds: float) -> None:
        self.block_seconds = block_seconds
        self.close_started = threading.Event()

    def close(self) -> None:
        self.close_started.set()
        time.sleep(self.block_seconds)


def test_close_offloads_a_stalled_service_close_and_keeps_the_loop_responsive() -> None:
    async def scenario() -> None:
        service = _SlowClosingService(block_seconds=0.3)
        server = _server()
        server.service = cast(LogicCommandService, service)

        heartbeats = 0

        async def heartbeat_loop() -> None:
            nonlocal heartbeats
            while True:
                await asyncio.sleep(0.02)
                heartbeats += 1

        heartbeat_task = asyncio.create_task(heartbeat_loop())
        try:
            await asyncio.wait_for(server.close(), timeout=2)
        finally:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task

        assert service.close_started.is_set()
        # A synchronous service.close() call would freeze this event loop for
        # the full 0.3s block, starving every concurrently scheduled task; an
        # offloaded close lets the heartbeat keep ticking during that window.
        assert heartbeats >= 5

    asyncio.run(scenario())
