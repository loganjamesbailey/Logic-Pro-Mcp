from __future__ import annotations

import asyncio
from typing import cast

from daemon.net import abort_writer, await_before_deadline, close_writer


class _FakeTransport:
    def __init__(self) -> None:
        self.aborted = False

    def abort(self) -> None:
        self.aborted = True


class _FakeWriter:
    def __init__(
        self,
        *,
        stall_close: bool = False,
        close_raises: Exception | None = None,
        wait_closed_raises: Exception | None = None,
    ) -> None:
        self.stall_close = stall_close
        self.close_raises = close_raises
        self.wait_closed_raises = wait_closed_raises
        self.close_release = asyncio.Event()
        self.close_calls = 0
        self.wait_closed_calls = 0
        self.transport = _FakeTransport()

    def close(self) -> None:
        self.close_calls += 1
        if self.close_raises is not None:
            raise self.close_raises

    async def wait_closed(self) -> None:
        self.wait_closed_calls += 1
        if self.wait_closed_raises is not None:
            raise self.wait_closed_raises
        if self.stall_close:
            await self.close_release.wait()


def _deadline(seconds: float) -> float:
    return asyncio.get_running_loop().time() + seconds


def test_close_writer_completes_normally_without_aborting() -> None:
    async def scenario() -> None:
        writer = _FakeWriter()
        await close_writer(cast("asyncio.StreamWriter", writer), deadline=_deadline(1))

        assert writer.close_calls == 1
        assert writer.wait_closed_calls == 1
        assert writer.transport.aborted is False

    asyncio.run(scenario())


def test_close_writer_aborts_when_wait_closed_hangs_past_deadline() -> None:
    async def scenario() -> None:
        writer = _FakeWriter(stall_close=True)
        task = asyncio.create_task(
            close_writer(cast("asyncio.StreamWriter", writer), deadline=_deadline(0.02))
        )

        done, _pending = await asyncio.wait({task}, timeout=0.5)

        assert task in done
        await task
        assert writer.close_calls == 1
        assert writer.wait_closed_calls == 1
        assert writer.transport.aborted is True

    asyncio.run(scenario())


def test_close_writer_aborts_when_close_raises() -> None:
    async def scenario() -> None:
        writer = _FakeWriter(close_raises=OSError("already gone"))
        await close_writer(cast("asyncio.StreamWriter", writer), deadline=_deadline(1))

        assert writer.close_calls == 1
        assert writer.wait_closed_calls == 0
        assert writer.transport.aborted is True

    asyncio.run(scenario())


def test_close_writer_aborts_when_wait_closed_raises_runtime_error() -> None:
    async def scenario() -> None:
        writer = _FakeWriter(wait_closed_raises=RuntimeError("transport gone"))
        await close_writer(cast("asyncio.StreamWriter", writer), deadline=_deadline(1))

        assert writer.close_calls == 1
        assert writer.wait_closed_calls == 1
        assert writer.transport.aborted is True

    asyncio.run(scenario())


def test_close_writer_aborts_immediately_when_deadline_already_passed() -> None:
    async def scenario() -> None:
        writer = _FakeWriter(stall_close=True)
        started = asyncio.get_running_loop().time()

        await close_writer(cast("asyncio.StreamWriter", writer), deadline=started - 1)

        elapsed = asyncio.get_running_loop().time() - started
        assert elapsed < 0.1
        assert writer.wait_closed_calls == 0
        assert writer.transport.aborted is True

    asyncio.run(scenario())


def test_await_before_deadline_raises_timeout_error_without_negative_wait() -> None:
    async def scenario() -> None:
        async def never() -> None:
            await asyncio.Event().wait()

        try:
            await await_before_deadline(never, deadline=_deadline(-1))
        except TimeoutError:
            pass
        else:
            raise AssertionError("expected TimeoutError")

    asyncio.run(scenario())


def test_abort_writer_tolerates_a_missing_transport() -> None:
    class _NoTransportWriter:
        pass

    abort_writer(cast("asyncio.StreamWriter", _NoTransportWriter()))
