"""Bounded-close helpers shared by the RPC server and client transports.

A `finally`-block `await` is a fresh cancellation checkpoint: it is not
covered by an `asyncio.timeout`/`asyncio.wait_for` that has already fired
once further up the call stack. Every writer teardown in this codebase must
therefore carry its own explicit deadline rather than relying on an outer
timeout to still apply.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

_T = TypeVar("_T")


async def close_writer(writer: asyncio.StreamWriter, *, deadline: float) -> None:
    if not request_writer_close(writer):
        return
    try:
        await await_before_deadline(writer.wait_closed, deadline=deadline)
    except TimeoutError:
        abort_writer(writer)
    except (OSError, RuntimeError):
        abort_writer(writer)


async def await_before_deadline(
    operation: Callable[[], Awaitable[_T]],
    *,
    deadline: float,
) -> _T:
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise TimeoutError
    return await asyncio.wait_for(operation(), timeout=remaining)


def request_writer_close(writer: asyncio.StreamWriter) -> bool:
    try:
        writer.close()
    except (OSError, RuntimeError):
        abort_writer(writer)
        return False
    return True


def abort_writer(writer: asyncio.StreamWriter) -> None:
    try:
        writer.transport.abort()
    except (AttributeError, OSError, RuntimeError):
        return
