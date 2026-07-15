from __future__ import annotations

import asyncio
import hmac
import json
import secrets
from collections.abc import Mapping
from typing import Any

from daemon.commands import LogicCommandService
from daemon.config import ServerConfig, validate_authentication_token
from daemon.errors import (
    AuthenticationRequiredError,
    BridgeError,
    InvalidParametersError,
)
from daemon.net import (
    abort_writer,
    await_before_deadline,
    close_writer,
    request_writer_close,
)
from daemon.rpc_contract import (
    AUTH_HANDSHAKE_MAX_BYTES,
    AUTH_NONCE_BYTES,
    AUTH_PROTOCOL,
    METHOD_SPECS,
    InvalidJsonPayloadError,
    InvalidRequestContractError,
    authentication_request_proof,
    authentication_server_proof,
    decode_json_object,
    is_authentication_nonce,
    validate_params,
    validate_request,
)

_SERVICE_CLOSE_TIMEOUT_SECONDS = 5.0
"""Bound for offloading LogicCommandService.close() to a worker thread.

self.service.close() is synchronous and can block on native MIDI I/O or a
threading.Event with its own internal timeout. Running it via
asyncio.to_thread keeps a stalled close from freezing this event loop, but
cancelling the wrapping await does not stop the underlying OS thread -- a
genuinely wedged native call can still delay process exit even after this
bound is hit and close() has returned.
"""


class JsonRpcServer:
    """Newline-delimited JSON-RPC 2.0 server restricted to loopback config."""

    def __init__(
        self,
        *,
        config: ServerConfig,
        service: LogicCommandService,
        auth_token: str | None,
    ) -> None:
        self.config = config
        self.service = service
        self._auth_token = validate_authentication_token(
            auth_token,
            token_env=config.token_env,
        )
        self._server: asyncio.Server | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        self._client_tasks: set[asyncio.Task[Any]] = set()
        self._closing = False
        self._service_closed = False

    @property
    def bound_port(self) -> int:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("JSON-RPC server has not started")
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("JSON-RPC server is already running")
        if self._service_closed:
            raise RuntimeError("JSON-RPC server cannot restart after closing")
        self._closing = False
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self.config.host,
            port=self.config.port,
            limit=self.config.max_request_bytes + 1,
        )

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def close(self) -> None:
        self._closing = True
        listening_server = self._server
        if self._server is not None:
            self._server.close()
            self._server = None

        for writer in tuple(self._clients):
            request_writer_close(writer)
        current_task = asyncio.current_task()
        client_tasks = [task for task in self._client_tasks if task is not current_task]
        for task in client_tasks:
            task.cancel()
        if client_tasks:
            await asyncio.gather(*client_tasks, return_exceptions=True)
        if listening_server is not None:
            await listening_server.wait_closed()

        if not self._service_closed:
            self._service_closed = True
            close_deadline = (
                asyncio.get_running_loop().time() + _SERVICE_CLOSE_TIMEOUT_SECONDS
            )
            try:
                await await_before_deadline(
                    lambda: asyncio.to_thread(self.service.close),
                    deadline=close_deadline,
                )
            except TimeoutError:
                pass

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        task = asyncio.current_task()
        if self._closing:
            _written, rejection_deadline = await self._write_response(
                writer,
                _server_closing(),
            )
            await close_writer(writer, deadline=rejection_deadline)
            return
        if len(self._clients) >= self.config.max_connections:
            _written, rejection_deadline = await self._write_response(
                writer,
                _connection_limit_reached(),
            )
            await close_writer(writer, deadline=rejection_deadline)
            return

        self._clients.add(writer)
        if task is not None:
            self._client_tasks.add(task)
        close_deadline: float | None = None
        try:
            deadline = (
                asyncio.get_running_loop().time() + self.config.read_timeout_seconds
            )
            try:
                client_hello_line = await self._read_line(
                    reader,
                    deadline=deadline,
                    max_bytes=AUTH_HANDSHAKE_MAX_BYTES,
                )
            except _ReadTimeout:
                _written, close_deadline = await self._write_response(
                    writer,
                    _request_timeout(self.config.read_timeout_seconds),
                )
                return
            except _LineTooLarge:
                _written, close_deadline = await self._write_response(
                    writer,
                    _request_too_large(),
                )
                return
            except _MalformedLine:
                _written, close_deadline = await self._write_response(
                    writer,
                    _bridge_error(
                        None,
                        -32001,
                        AuthenticationRequiredError(
                            "Authentication handshake is missing or invalid"
                        ),
                    ),
                )
                return
            if not client_hello_line:
                return

            try:
                client_nonce, server_nonce = self._authenticate_client_hello(
                    client_hello_line
                )
            except AuthenticationRequiredError as exc:
                _written, close_deadline = await self._write_response(
                    writer,
                    _bridge_error(None, -32001, exc),
                )
                return
            hello_written, close_deadline = await self._write_response(
                writer,
                {
                    "auth": AUTH_PROTOCOL,
                    "client_nonce": client_nonce,
                    "server_nonce": server_nonce,
                    "server_proof": authentication_server_proof(
                        self._auth_token,
                        client_nonce=client_nonce,
                        server_nonce=server_nonce,
                    ),
                },
            )
            if not hello_written:
                return
            close_deadline = None

            try:
                request_line = await self._read_line(
                    reader,
                    deadline=deadline,
                    max_bytes=self.config.max_request_bytes,
                )
            except _ReadTimeout:
                _written, close_deadline = await self._write_response(
                    writer,
                    _request_timeout(self.config.read_timeout_seconds),
                )
                return
            except _LineTooLarge:
                _written, close_deadline = await self._write_response(
                    writer,
                    _request_too_large(),
                )
                return
            except _MalformedLine:
                _written, close_deadline = await self._write_response(
                    writer,
                    _error(None, -32700, "Parse error"),
                )
                return
            if not request_line:
                return
            response = await self._handle_line(
                request_line,
                client_nonce=client_nonce,
                server_nonce=server_nonce,
            )
            _written, close_deadline = await self._write_response(writer, response)
        finally:
            self._clients.discard(writer)
            if task is not None:
                self._client_tasks.discard(task)
            if close_deadline is None:
                close_deadline = self._new_write_deadline()
            await close_writer(writer, deadline=close_deadline)

    @staticmethod
    async def _read_line(
        reader: asyncio.StreamReader,
        *,
        deadline: float,
        max_bytes: int,
    ) -> bytes:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise _ReadTimeout
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=remaining)
        except TimeoutError:
            raise _ReadTimeout from None
        except ValueError:
            raise _LineTooLarge from None
        if len(line) > max_bytes:
            raise _LineTooLarge
        if line and not line.endswith(b"\n"):
            raise _MalformedLine
        return line

    def _authenticate_client_hello(self, line: bytes) -> tuple[str, str]:
        try:
            client_hello = decode_json_object(line)
        except InvalidJsonPayloadError:
            raise AuthenticationRequiredError(
                "Authentication handshake is missing or invalid"
            ) from None
        if set(client_hello) != {"auth", "client_nonce"}:
            raise AuthenticationRequiredError(
                "Authentication handshake is missing or invalid"
            )
        client_nonce = client_hello.get("client_nonce")
        if client_hello.get("auth") != AUTH_PROTOCOL or not is_authentication_nonce(
            client_nonce
        ):
            raise AuthenticationRequiredError(
                "Authentication handshake is missing or invalid"
            )
        assert isinstance(client_nonce, str)
        return client_nonce, secrets.token_hex(AUTH_NONCE_BYTES)

    async def _handle_line(
        self,
        line: bytes,
        *,
        client_nonce: str,
        server_nonce: str,
    ) -> dict[str, Any]:
        try:
            request = decode_json_object(line)
        except InvalidJsonPayloadError:
            return _error(None, -32700, "Parse error")

        request_id = request.get("id")
        try:
            validate_request(request)
            self._authenticate(
                request,
                client_nonce=client_nonce,
                server_nonce=server_nonce,
            )
            result = await self._dispatch(request["method"], request["params"])
        except AuthenticationRequiredError as exc:
            return _bridge_error(request_id, -32001, exc)
        except InvalidParametersError as exc:
            return _bridge_error(request_id, -32602, exc)
        except BridgeError as exc:
            return _bridge_error(request_id, -32000, exc)
        except _MethodNotFound:
            return _error(request_id, -32601, "Method not found")
        except InvalidRequestContractError:
            return _error(None, -32600, "Invalid Request")
        except Exception:
            return _error(request_id, -32603, "Internal error")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _authenticate(
        self,
        request: Mapping[str, Any],
        *,
        client_nonce: str,
        server_nonce: str,
    ) -> None:
        meta = request["meta"]
        assert isinstance(meta, Mapping)
        provided_client_nonce = meta["client_nonce"]
        provided_server_nonce = meta["server_nonce"]
        provided_proof = meta["request_proof"]
        assert isinstance(provided_client_nonce, str)
        assert isinstance(provided_server_nonce, str)
        assert isinstance(provided_proof, str)
        expected_proof = authentication_request_proof(self._auth_token, request)
        client_nonce_matches = hmac.compare_digest(
            provided_client_nonce,
            client_nonce,
        )
        server_nonce_matches = hmac.compare_digest(
            provided_server_nonce,
            server_nonce,
        )
        proof_matches = hmac.compare_digest(provided_proof, expected_proof)
        if not (client_nonce_matches and server_nonce_matches and proof_matches):
            raise AuthenticationRequiredError(
                "Authentication proof is missing or invalid"
            )

    async def _dispatch(self, method: str, params: Mapping[str, Any]) -> Any:
        spec = METHOD_SPECS.get(method)
        if spec is None:
            raise _MethodNotFound
        validate_params(spec, params)
        handler = getattr(self.service, spec.service_method)
        arguments = spec.call_arguments(params)
        if spec.threaded:
            return await asyncio.to_thread(handler, **arguments)
        return handler(**arguments)

    async def _write_response(
        self,
        writer: asyncio.StreamWriter,
        response: Mapping[str, Any],
    ) -> tuple[bool, float]:
        deadline = self._new_write_deadline()
        try:
            payload = json.dumps(
                response,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError):
            payload = json.dumps(
                _error(response.get("id"), -32603, "Internal error"),
                separators=(",", ":"),
                ensure_ascii=False,
            )
        try:
            writer.write(payload.encode("utf-8") + b"\n")
            await await_before_deadline(writer.drain, deadline=deadline)
        except TimeoutError:
            abort_writer(writer)
            return False, deadline
        except (OSError, RuntimeError):
            abort_writer(writer)
            return False, deadline
        return True, deadline

    def _new_write_deadline(self) -> float:
        return asyncio.get_running_loop().time() + self.config.write_timeout_seconds


class _MethodNotFound(Exception):
    pass


class _ReadTimeout(Exception):
    pass


class _LineTooLarge(Exception):
    pass


class _MalformedLine(Exception):
    pass


def _error(
    request_id: str | int | None,
    code: int,
    message: str,
    *,
    data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = dict(data)
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _bridge_error(
    request_id: str | int | None,
    rpc_code: int,
    error: BridgeError,
) -> dict[str, Any]:
    return _error(
        request_id,
        rpc_code,
        error.message,
        data=error.to_rpc_data(),
    )


def _request_too_large() -> dict[str, Any]:
    return _error(
        None,
        -32004,
        "Request exceeds configured byte limit",
        data={
            "bridge_code": "REQUEST_TOO_LARGE",
            "retryable": False,
            "details": {},
        },
    )


def _request_timeout(timeout_seconds: float) -> dict[str, Any]:
    return _error(
        None,
        -32005,
        "Request read timed out",
        data={
            "bridge_code": "REQUEST_TIMEOUT",
            "retryable": True,
            "details": {"timeout_seconds": timeout_seconds},
        },
    )


def _connection_limit_reached() -> dict[str, Any]:
    return _error(
        None,
        -32006,
        "Connection limit reached",
        data={
            "bridge_code": "CONNECTION_LIMIT_REACHED",
            "retryable": True,
            "details": {},
        },
    )


def _server_closing() -> dict[str, Any]:
    return _error(
        None,
        -32007,
        "Server is shutting down",
        data={
            "bridge_code": "SERVER_CLOSING",
            "retryable": False,
            "details": {},
        },
    )
