from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import math
import secrets
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from daemon.net import close_writer
from daemon.rpc_contract import (
    AUTH_HANDSHAKE_MAX_BYTES,
    AUTH_NONCE_BYTES,
    AUTH_PROTOCOL,
    MAX_SERVER_PORT,
    MIN_SERVER_PORT,
    InvalidJsonPayloadError,
    authentication_request_proof,
    authentication_server_proof,
    decode_json_object,
    is_authentication_nonce,
    is_authentication_proof,
    validate_json_value,
)

_CLOSE_TIMEOUT_SECONDS = 2.0
"""Bound for tearing down the connection after the request/response exchange.

Deliberately independent of ``timeout_seconds`` (which bounds the exchange
itself): reusing the full request timeout here would let a single ``call()``
take up to twice its configured budget before raising. Matches the server's
own default ``write_timeout_seconds``.
"""


class BridgeRpcClientError(Exception):
    """Base class for failures while proxying a bridge request."""


class BridgeRpcProtocolError(BridgeRpcClientError):
    """The daemon returned an invalid or mismatched JSON-RPC response."""


class BridgeRpcTimeoutError(BridgeRpcClientError):
    """The daemon did not complete the request within the configured deadline."""


class BridgeRpcTransportError(BridgeRpcClientError):
    """The daemon connection failed before a valid response was received."""


class BridgeRpcRemoteError(BridgeRpcClientError):
    """Structured error returned by the bridge daemon."""

    def __init__(
        self,
        message: str,
        *,
        rpc_code: int,
        bridge_code: str | None,
        retryable: bool,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.rpc_code = rpc_code
        self.bridge_code = bridge_code
        self.retryable = retryable
        self.details = dict(details or {})


class BridgeRpcClient:
    """One-request-per-connection client for the local bridge daemon.

    Deliberately avoiding connection reuse prevents a timed-out response from being
    consumed by a later MCP tool call. Authentication material is retained only for
    request construction and is never included in the object representation.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        token: str,
        timeout_seconds: float,
        max_response_bytes: int = 1_048_576,
    ) -> None:
        validate_rpc_endpoint_settings(
            host=host,
            port=port,
            token=token,
            timeout_seconds=timeout_seconds,
        )
        if (
            isinstance(max_response_bytes, bool)
            or not isinstance(max_response_bytes, int)
            or max_response_bytes < 2
        ):
            raise ValueError("max_response_bytes must be at least 2")
        self._host = host
        self._port = port
        self._token = token
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(host={self._host!r}, port={self._port!r}, "
            f"token=<redacted>, timeout_seconds={self._timeout_seconds!r})"
        )

    async def call(
        self,
        method: str,
        params: Mapping[str, Any],
    ) -> Any:
        if not isinstance(method, str) or not method:
            raise ValueError("method must be a non-empty string")
        if not isinstance(params, Mapping):
            raise ValueError("params must be a mapping")

        try:
            params_dict = dict(params)
            validate_json_value(params_dict)
            request_id = uuid4().hex
            request = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params_dict,
                "meta": {},
            }
        except (RecursionError, TypeError, ValueError):
            raise ValueError(
                "params must contain only JSON-compatible values"
            ) from None

        try:
            async with asyncio.timeout(self._timeout_seconds):
                raw = await self._exchange(request)
        except TimeoutError as exc:
            raise BridgeRpcTimeoutError("bridge request timed out") from exc
        except BridgeRpcClientError:
            raise
        except (OSError, asyncio.IncompleteReadError) as exc:
            raise BridgeRpcTransportError("bridge transport failed") from exc

        response = self._decode_response(raw)
        self._validate_response(response, request_id)
        if "error" in response:
            raise self._remote_error(response["error"])
        return response["result"]

    async def _exchange(self, request: Mapping[str, Any]) -> bytes:
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await asyncio.open_connection(
                self._host,
                self._port,
                limit=max(self._max_response_bytes, AUTH_HANDSHAKE_MAX_BYTES) + 1,
            )
            client_nonce = secrets.token_hex(AUTH_NONCE_BYTES)
            client_hello = {
                "auth": AUTH_PROTOCOL,
                "client_nonce": client_nonce,
            }
            writer.write(_encode_json_line(client_hello))
            await writer.drain()
            server_hello_raw = await self._read_line(
                reader,
                max_bytes=AUTH_HANDSHAKE_MAX_BYTES,
                label="server authentication handshake",
            )
            server_nonce = self._validate_server_hello(
                server_hello_raw,
                client_nonce=client_nonce,
            )

            meta = {
                "auth": AUTH_PROTOCOL,
                "client_nonce": client_nonce,
                "server_nonce": server_nonce,
                "request_proof": "",
            }
            authenticated_request = {**request, "meta": meta}
            meta["request_proof"] = authentication_request_proof(
                self._token,
                authenticated_request,
            )
            writer.write(_encode_json_line(authenticated_request))
            await writer.drain()
            return await self._read_line(
                reader,
                max_bytes=self._max_response_bytes,
                label="response",
            )
        finally:
            if writer is not None:
                deadline = asyncio.get_running_loop().time() + _CLOSE_TIMEOUT_SECONDS
                await close_writer(writer, deadline=deadline)

    @staticmethod
    async def _read_line(
        reader: asyncio.StreamReader,
        *,
        max_bytes: int,
        label: str,
    ) -> bytes:
        try:
            raw = await reader.readline()
        except ValueError:
            raise BridgeRpcProtocolError(
                f"invalid {label}: {label} too large"
            ) from None
        if not raw:
            raise BridgeRpcProtocolError(f"invalid {label}: connection closed")
        if len(raw) > max_bytes:
            raise BridgeRpcProtocolError(f"invalid {label}: {label} too large")
        if not raw.endswith(b"\n"):
            raise BridgeRpcProtocolError(f"invalid {label}: missing line terminator")
        return raw

    def _validate_server_hello(
        self,
        raw: bytes,
        *,
        client_nonce: str,
    ) -> str:
        try:
            server_hello = decode_json_object(raw)
        except InvalidJsonPayloadError:
            raise BridgeRpcProtocolError(
                "invalid server authentication handshake"
            ) from None
        if set(server_hello) != {
            "auth",
            "client_nonce",
            "server_nonce",
            "server_proof",
        }:
            raise BridgeRpcProtocolError("invalid server authentication handshake")
        provided_client_nonce = server_hello.get("client_nonce")
        server_nonce = server_hello.get("server_nonce")
        server_proof = server_hello.get("server_proof")
        if (
            server_hello.get("auth") != AUTH_PROTOCOL
            or not is_authentication_nonce(provided_client_nonce)
            or not is_authentication_nonce(server_nonce)
            or not is_authentication_proof(server_proof)
        ):
            raise BridgeRpcProtocolError("invalid server authentication handshake")
        assert isinstance(provided_client_nonce, str)
        assert isinstance(server_nonce, str)
        assert isinstance(server_proof, str)
        expected_proof = authentication_server_proof(
            self._token,
            client_nonce=client_nonce,
            server_nonce=server_nonce,
        )
        client_nonce_matches = hmac.compare_digest(
            provided_client_nonce,
            client_nonce,
        )
        proof_matches = hmac.compare_digest(server_proof, expected_proof)
        if not (client_nonce_matches and proof_matches):
            raise BridgeRpcProtocolError("server authentication failed")
        return server_nonce

    @staticmethod
    def _decode_response(raw: bytes) -> Mapping[str, Any]:
        response: Mapping[str, Any] | None = None
        try:
            response = decode_json_object(raw)
        except InvalidJsonPayloadError:
            pass
        if response is None:
            raise BridgeRpcProtocolError("invalid response: malformed JSON")
        return response

    @staticmethod
    def _validate_response(response: Mapping[str, Any], request_id: str) -> None:
        allowed = {"jsonrpc", "id", "result", "error"}
        if set(response) - allowed:
            raise BridgeRpcProtocolError("invalid response: unknown fields")
        if response.get("jsonrpc") != "2.0" or response.get("id") != request_id:
            raise BridgeRpcProtocolError("invalid response: version or id mismatch")
        has_result = "result" in response
        has_error = "error" in response
        if has_result == has_error:
            raise BridgeRpcProtocolError("invalid response: expected result or error")
        if has_error and not isinstance(response["error"], Mapping):
            raise BridgeRpcProtocolError("invalid response: malformed error")

    def _remote_error(self, error: object) -> BridgeRpcRemoteError:
        if not isinstance(error, Mapping):
            raise BridgeRpcProtocolError("invalid response: malformed error")
        if not {"code", "message"} <= set(error) <= {"code", "message", "data"}:
            raise BridgeRpcProtocolError("invalid response: malformed error")
        code = error["code"]
        message = error["message"]
        if isinstance(code, bool) or not isinstance(code, int):
            raise BridgeRpcProtocolError("invalid response: malformed error code")
        if not isinstance(message, str):
            raise BridgeRpcProtocolError("invalid response: malformed error message")

        bridge_code: str | None = None
        retryable = False
        details: Mapping[str, Any] = {}
        if "data" in error:
            data = error["data"]
            if not isinstance(data, Mapping):
                raise BridgeRpcProtocolError("invalid response: malformed error data")
            if set(data) != {"bridge_code", "retryable", "details"}:
                raise BridgeRpcProtocolError(
                    "invalid response: malformed error metadata"
                )
            bridge_code = data["bridge_code"]
            retryable = data["retryable"]
            details = data["details"]
            if not isinstance(bridge_code, str) or not bridge_code:
                raise BridgeRpcProtocolError("invalid response: malformed bridge code")
            if not isinstance(retryable, bool) or not isinstance(details, Mapping):
                raise BridgeRpcProtocolError(
                    "invalid response: malformed error metadata"
                )

        safe_message = redact_secret(message, secret=self._token)
        safe_bridge_code = redact_secret(bridge_code, secret=self._token)
        safe_details = redact_secret(details, secret=self._token)
        assert isinstance(safe_message, str)
        assert safe_bridge_code is None or isinstance(safe_bridge_code, str)
        assert isinstance(safe_details, Mapping)
        return BridgeRpcRemoteError(
            safe_message,
            rpc_code=code,
            bridge_code=safe_bridge_code,
            retryable=retryable,
            details=safe_details,
        )


def _encode_json_line(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def validate_rpc_endpoint_settings(
    *,
    host: object,
    port: object,
    token: object,
    timeout_seconds: object,
) -> None:
    if not isinstance(host, str):
        raise ValueError("host must be a literal loopback IP address")
    try:
        loopback_host = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError("host must be a literal loopback IP address") from None
    if not loopback_host.is_loopback:
        raise ValueError("host must be a literal loopback IP address")
    if (
        isinstance(port, bool)
        or not isinstance(port, int)
        or not MIN_SERVER_PORT <= port <= MAX_SERVER_PORT
    ):
        raise ValueError(
            f"port must be between {MIN_SERVER_PORT} and {MAX_SERVER_PORT}"
        )
    if not isinstance(token, str):
        raise ValueError("token must contain at least 32 encoded bytes")
    try:
        token_bytes = token.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("token must contain at least 32 encoded bytes") from None
    if len(token_bytes) < 32:
        raise ValueError("token must contain at least 32 encoded bytes")
    try:
        timeout_is_finite = math.isfinite(timeout_seconds)  # type: ignore[arg-type]
    except (OverflowError, TypeError):
        timeout_is_finite = False
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not timeout_is_finite
        or timeout_seconds <= 0
    ):
        raise ValueError("timeout_seconds must be positive and finite")


def redact_secret(value: Any, *, secret: str) -> Any:
    if isinstance(value, str):
        return value.replace(secret, "<redacted>")
    if isinstance(value, Mapping):
        return {
            redact_secret(key, secret=secret): redact_secret(item, secret=secret)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_secret(item, secret=secret) for item in value]
    return value
