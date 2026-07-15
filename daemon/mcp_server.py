from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Any, Protocol, TypeAlias

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ResourceError, ToolError
from mcp.types import ToolAnnotations
from pydantic import Field, StrictFloat, StrictInt, StrictStr, StringConstraints

from daemon.rpc_client import (
    BridgeRpcClient,
    BridgeRpcClientError,
    BridgeRpcProtocolError,
    BridgeRpcRemoteError,
    BridgeRpcTimeoutError,
    BridgeRpcTransportError,
    redact_secret,
    validate_rpc_endpoint_settings,
)
from daemon.rpc_contract import (
    TRACK_ID_MAX,
    TRACK_ID_MAX_DIGITS,
    TRACK_ID_OR_SELECTED_PATTERN,
)

MCP_TOOL_METHODS: Mapping[str, str] = MappingProxyType(
    {
        "set_volume": "logic.set_volume",
        "toggle_mute": "logic.toggle_mute",
        "invoke_action": "logic.invoke_action",
        "set_scripter_parameter": "logic.set_scripter_parameter",
        "inspect_mixer_target": "logic.inspect_mixer_target",
        "load_cst_preset": "logic.load_cst_preset",
    }
)
MCP_RESOURCE_URIS = (
    "logic-bridge://health",
    "logic-bridge://capabilities",
)

_RESOURCE_ID_PATTERN = r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$"
ResourceId: TypeAlias = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=128,
        pattern=_RESOURCE_ID_PATTERN,
    ),
]
TrackId: TypeAlias = (
    Annotated[
        StrictStr,
        StringConstraints(
            strict=True,
            max_length=TRACK_ID_MAX_DIGITS,
            pattern=TRACK_ID_OR_SELECTED_PATTERN.pattern,
        ),
    ]
    | Annotated[StrictInt, Field(ge=1, le=TRACK_ID_MAX)]
)
NormalizedValue: TypeAlias = (
    Annotated[
        StrictInt,
        Field(ge=0, le=1),
    ]
    | Annotated[
        StrictFloat,
        Field(ge=0, le=1, allow_inf_nan=False),
    ]
)
ActionValue: TypeAlias = (
    Annotated[
        StrictInt,
        Field(ge=0, le=127),
    ]
    | Annotated[
        StrictFloat,
        Field(ge=0, le=127, allow_inf_nan=False),
    ]
)
Confirmation: TypeAlias = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=400),
]

_MISSING_ACTION_VALUE = object()


def _missing_action_value() -> Any:
    return _MISSING_ACTION_VALUE


class RpcClientLike(Protocol):
    async def call(self, method: str, params: Mapping[str, Any]) -> Any: ...


class RpcClientFactory(Protocol):
    def __call__(
        self,
        *,
        host: str,
        port: int,
        token: str,
        timeout_seconds: float,
    ) -> RpcClientLike: ...


class _McpBridgeAdapter:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        token: str,
        timeout_seconds: float,
        client_factory: RpcClientFactory,
    ) -> None:
        self._host = host
        self._port = port
        self._token = token
        self._timeout_seconds = timeout_seconds
        self._client_factory = client_factory

    async def call(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        client = self._client_factory(
            host=self._host,
            port=self._port,
            token=self._token,
            timeout_seconds=self._timeout_seconds,
        )
        try:
            result = await client.call(method, params)
        except BridgeRpcRemoteError as exc:
            raise ToolError(_remote_error_json(exc, secret=self._token)) from None
        except BridgeRpcTimeoutError:
            raise ToolError(
                _adapter_error_json(
                    bridge_code="BRIDGE_TIMEOUT",
                    message=(
                        "The bridge request timed out and its Logic outcome may be "
                        "unknown; do not retry without independent inspection"
                    ),
                )
            ) from None
        except BridgeRpcTransportError:
            raise ToolError(
                _adapter_error_json(
                    bridge_code="BRIDGE_TRANSPORT_FAILED",
                    message=(
                        "The bridge transport failed and the Logic outcome may be "
                        "unknown; do not retry without independent inspection"
                    ),
                )
            ) from None
        except BridgeRpcProtocolError:
            raise ToolError(
                _adapter_error_json(
                    bridge_code="BRIDGE_PROTOCOL_ERROR",
                    message="The bridge returned an invalid response",
                )
            ) from None
        except BridgeRpcClientError:
            raise ToolError(
                _adapter_error_json(
                    bridge_code="BRIDGE_CLIENT_ERROR",
                    message="The bridge client failed",
                )
            ) from None
        if not isinstance(result, Mapping):
            raise ToolError(
                _adapter_error_json(
                    bridge_code="BRIDGE_PROTOCOL_ERROR",
                    message="The bridge returned an invalid result",
                )
            )
        return dict(result)

    async def read_resource(
        self,
        method: str,
    ) -> dict[str, Any]:
        try:
            return await self.call(method, {})
        except ToolError as exc:
            raise ResourceError(str(exc)) from None


def create_mcp_server(
    *,
    host: str,
    port: int,
    token: str,
    timeout_seconds: float,
    client_factory: RpcClientFactory | None = None,
) -> FastMCP[None]:
    """Create the fixed stdio MCP adapter for one authenticated bridge daemon."""

    validate_rpc_endpoint_settings(
        host=host,
        port=port,
        token=token,
        timeout_seconds=timeout_seconds,
    )

    factory = client_factory or BridgeRpcClient
    adapter = _McpBridgeAdapter(
        host=host,
        port=port,
        token=token,
        timeout_seconds=timeout_seconds,
        client_factory=factory,
    )
    server: FastMCP[None] = FastMCP(
        "AI-to-Logic Pro IPC Bridge",
        instructions=(
            "Operate only the fixed local Logic bridge tools. A timeout or lost "
            "response can be post-dispatch and must not be retried unless an "
            "independent inspection proves the current state."
        ),
        log_level="WARNING",
    )

    @server.tool(
        name="set_volume",
        description=(
            "Set an allowlisted track volume through the authenticated daemon. "
            "Dispatch does not imply Logic readback."
        ),
        annotations=_annotations(idempotent=True),
        structured_output=True,
    )
    async def set_volume(
        value: NormalizedValue,
        track_id: TrackId = "selected",
    ) -> dict[str, Any]:
        return await adapter.call(
            MCP_TOOL_METHODS["set_volume"],
            {"track_id": track_id, "value": value},
        )

    @server.tool(
        name="toggle_mute",
        description=(
            "Toggle mute for an allowlisted track. This is non-idempotent; after "
            "timeout or cancellation, inspect state before any retry."
        ),
        annotations=_annotations(idempotent=False),
        structured_output=True,
    )
    async def toggle_mute(
        track_id: TrackId = "selected",
    ) -> dict[str, Any]:
        return await adapter.call(
            MCP_TOOL_METHODS["toggle_mute"],
            {"track_id": track_id},
        )

    @server.tool(
        name="invoke_action",
        description=(
            "Invoke one configured opaque MIDI action ID. Callers cannot supply "
            "raw MIDI. Actions may be non-idempotent; do not blindly retry."
        ),
        annotations=_annotations(idempotent=False),
        structured_output=True,
    )
    async def invoke_action(
        action_id: ResourceId,
        value: ActionValue = Field(default_factory=_missing_action_value),
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"action_id": action_id}
        if value is not _MISSING_ACTION_VALUE:
            params["value"] = value
        return await adapter.call(MCP_TOOL_METHODS["invoke_action"], params)

    @server.tool(
        name="set_scripter_parameter",
        description=(
            "Set one fixed allowlisted Scripter target parameter on the dedicated "
            "software-instrument route. No acknowledgement is implied."
        ),
        annotations=_annotations(idempotent=True),
        structured_output=True,
    )
    async def set_scripter_parameter(
        parameter_id: ResourceId,
        value: NormalizedValue,
    ) -> dict[str, Any]:
        return await adapter.call(
            MCP_TOOL_METHODS["set_scripter_parameter"],
            {"parameter_id": parameter_id, "value": value},
        )

    @server.tool(
        name="inspect_mixer_target",
        description=(
            "Read the bounded allowlisted evidence for one configured Mixer target."
        ),
        annotations=_annotations(read_only=True, idempotent=True),
        structured_output=True,
    )
    async def inspect_mixer_target(
        target_id: ResourceId,
    ) -> dict[str, Any]:
        return await adapter.call(
            MCP_TOOL_METHODS["inspect_mixer_target"],
            {"target_id": target_id},
        )

    @server.tool(
        name="load_cst_preset",
        description=(
            "Destructively load one allowlisted channel-strip preset into one "
            "allowlisted target. The exact target-bound disposable-project "
            "confirmation is mandatory. Never retry after an ambiguous outcome."
        ),
        annotations=_annotations(destructive=True, idempotent=False),
        structured_output=True,
    )
    async def load_cst_preset(
        preset_id: ResourceId,
        target_id: ResourceId,
        confirmation: Confirmation,
    ) -> dict[str, Any]:
        return await adapter.call(
            MCP_TOOL_METHODS["load_cst_preset"],
            {
                "preset_id": preset_id,
                "target_id": target_id,
                "confirmation": confirmation,
            },
        )

    @server.resource(
        MCP_RESOURCE_URIS[0],
        name="bridge-health",
        description="Safe readiness and health data from the authenticated daemon.",
        mime_type="application/json",
    )
    async def bridge_health() -> dict[str, Any]:
        return await adapter.read_resource("bridge.health")

    @server.resource(
        MCP_RESOURCE_URIS[1],
        name="bridge-capabilities",
        description="Safe capability metadata from the authenticated daemon.",
        mime_type="application/json",
    )
    async def bridge_capabilities() -> dict[str, Any]:
        return await adapter.read_resource("bridge.capabilities")

    _forbid_extra_tool_arguments(server)
    return server


def run_stdio(
    *,
    host: str,
    port: int,
    token: str,
    timeout_seconds: float,
) -> None:
    """Run the adapter with stdout reserved exclusively for MCP stdio frames."""

    server = create_mcp_server(
        host=host,
        port=port,
        token=token,
        timeout_seconds=timeout_seconds,
    )
    server.run(transport="stdio")


def main() -> None:
    """Environment-only module entrypoint used by MCP client subprocesses."""

    try:
        host = os.environ.get("LOGIC_BRIDGE_HOST", "127.0.0.1")
        port = int(os.environ.get("LOGIC_BRIDGE_PORT", "8765"))
        timeout_seconds = float(
            os.environ.get("LOGIC_BRIDGE_MCP_TIMEOUT_SECONDS", "75")
        )
        token = os.environ["LOGIC_BRIDGE_TOKEN"]
        run_stdio(
            host=host,
            port=port,
            token=token,
            timeout_seconds=timeout_seconds,
        )
    except (KeyError, ValueError):
        print("MCP adapter configuration is invalid", file=sys.stderr)
        raise SystemExit(2) from None


def _annotations(
    *,
    read_only: bool = False,
    destructive: bool = False,
    idempotent: bool,
) -> ToolAnnotations:
    return ToolAnnotations(
        readOnlyHint=read_only,
        destructiveHint=destructive,
        idempotentHint=idempotent,
        openWorldHint=False,
    )


def _forbid_extra_tool_arguments(server: FastMCP[Any]) -> None:
    """Make the SDK-generated Pydantic argument models reject unknown fields."""

    for name in MCP_TOOL_METHODS:
        tool = server._tool_manager.get_tool(name)
        if tool is None:  # pragma: no cover - registration invariant
            raise RuntimeError(f"MCP tool registration failed: {name}")
        model = tool.fn_metadata.arg_model
        model.model_config["extra"] = "forbid"
        model.model_rebuild(force=True)
        tool.parameters = model.model_json_schema()


def _remote_error_json(exc: BridgeRpcRemoteError, *, secret: str) -> str:
    payload = {
        "error": {
            "rpc_code": exc.rpc_code,
            "bridge_code": exc.bridge_code,
            "message": redact_secret(str(exc), secret=secret),
            "retryable": exc.retryable,
            "details": redact_secret(exc.details, secret=secret),
        }
    }
    try:
        return json.dumps(
            payload,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return _adapter_error_json(
            bridge_code="BRIDGE_PROTOCOL_ERROR",
            message="The bridge returned invalid error metadata",
        )


def _adapter_error_json(*, bridge_code: str, message: str) -> str:
    return json.dumps(
        {
            "error": {
                "rpc_code": -32000,
                "bridge_code": bridge_code,
                "message": message,
                "retryable": False,
                "details": {},
            }
        },
        separators=(",", ":"),
        ensure_ascii=False,
    )


if __name__ == "__main__":
    main()
