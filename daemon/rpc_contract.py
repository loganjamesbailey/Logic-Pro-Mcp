from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, NoReturn

from daemon.errors import AuthenticationRequiredError, InvalidParametersError

_REQUEST_FIELDS = {"jsonrpc", "id", "method", "params", "meta"}
_AUTH_META_FIELDS = {"auth", "client_nonce", "server_nonce", "request_proof"}
_MAX_JSON_DEPTH = 64
AUTH_PROTOCOL = "logic-bridge-hmac-sha256-v1"
AUTH_HANDSHAKE_MAX_BYTES = 512
AUTH_NONCE_BYTES = 32
MIN_SERVER_PORT = 1
MAX_SERVER_PORT = 65_535
AUTH_NONCE_HEX_LENGTH = AUTH_NONCE_BYTES * 2
AUTH_PROOF_HEX_LENGTH = hashlib.sha256().digest_size * 2
_AUTH_NONCE_PATTERN = re.compile(rf"^[0-9a-f]{{{AUTH_NONCE_HEX_LENGTH}}}$")
_AUTH_PROOF_PATTERN = re.compile(rf"^[0-9a-f]{{{AUTH_PROOF_HEX_LENGTH}}}$")
TRACK_ID_MAX_DIGITS = 20
TRACK_ID_MAX = (10**TRACK_ID_MAX_DIGITS) - 1
TRACK_ID_PATTERN = re.compile(rf"^[1-9][0-9]{{0,{TRACK_ID_MAX_DIGITS - 1}}}$")
TRACK_ID_OR_SELECTED_PATTERN = re.compile(
    rf"^(?:selected|[1-9][0-9]{{0,{TRACK_ID_MAX_DIGITS - 1}}})$"
)
_RESOURCE_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_TRACK_ID_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {"const": "selected"},
        {"type": "integer", "minimum": 1, "maximum": TRACK_ID_MAX},
        {
            "type": "string",
            "pattern": TRACK_ID_PATTERN.pattern,
            "maxLength": TRACK_ID_MAX_DIGITS,
        },
    ]
}
CONTRACT_VERSION = "2.0.0"


class InvalidJsonPayloadError(ValueError):
    """A protocol JSON value is malformed or outside the bounded subset."""


def decode_json_object(raw: bytes) -> dict[str, Any]:
    """Decode one strict, finite, duplicate-free, depth-bounded JSON object."""

    try:
        decoded = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_json_constant,
        )
        validate_json_value(decoded)
    except (RecursionError, UnicodeDecodeError, ValueError):
        raise InvalidJsonPayloadError("invalid JSON object") from None
    if not isinstance(decoded, dict):
        raise InvalidJsonPayloadError("invalid JSON object")
    return decoded


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    """Encode the authenticated request transcript deterministically."""

    validate_json_value(value)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def is_authentication_nonce(value: object) -> bool:
    return isinstance(value, str) and _AUTH_NONCE_PATTERN.fullmatch(value) is not None


def is_authentication_proof(value: object) -> bool:
    return isinstance(value, str) and _AUTH_PROOF_PATTERN.fullmatch(value) is not None


def authentication_server_proof(
    token: str,
    *,
    client_nonce: str,
    server_nonce: str,
) -> str:
    return _authentication_proof(
        token,
        purpose=b"server",
        client_nonce=client_nonce,
        server_nonce=server_nonce,
        payload=b"",
    )


def authentication_request_proof(
    token: str,
    request: Mapping[str, Any],
) -> str:
    meta = request.get("meta")
    if not isinstance(meta, Mapping):
        raise ValueError("request authentication metadata is invalid")
    client_nonce = meta.get("client_nonce")
    server_nonce = meta.get("server_nonce")
    if not is_authentication_nonce(client_nonce) or not is_authentication_nonce(
        server_nonce
    ):
        raise ValueError("request authentication metadata is invalid")
    assert isinstance(client_nonce, str)
    assert isinstance(server_nonce, str)
    unsigned_meta = dict(meta)
    unsigned_meta.pop("request_proof", None)
    unsigned_request = {**request, "meta": unsigned_meta}
    return _authentication_proof(
        token,
        purpose=b"request",
        client_nonce=client_nonce,
        server_nonce=server_nonce,
        payload=canonical_json_bytes(unsigned_request),
    )


def validate_json_value(value: Any) -> None:
    """Reject values JSON would coerce and values beyond the protocol depth."""

    active_containers: set[int] = set()

    def validate(current: Any, depth: int) -> None:
        if depth > _MAX_JSON_DEPTH:
            raise ValueError("JSON value exceeds maximum nesting depth")
        if current is None or isinstance(current, (str, bool, int)):
            return
        if isinstance(current, float):
            if not math.isfinite(current):
                raise ValueError("JSON number must be finite")
            return
        if not isinstance(current, (Mapping, list, tuple)):
            raise ValueError("value is not JSON-compatible")

        identity = id(current)
        if identity in active_containers:
            raise ValueError("circular JSON value")
        active_containers.add(identity)
        try:
            if isinstance(current, Mapping):
                for key, item in current.items():
                    if not isinstance(key, str):
                        raise ValueError("JSON object keys must be strings")
                    validate(item, depth + 1)
            else:
                for item in current:
                    validate(item, depth + 1)
        finally:
            active_containers.remove(identity)

    validate(value, 0)


def _authentication_proof(
    token: str,
    *,
    purpose: bytes,
    client_nonce: str,
    server_nonce: str,
    payload: bytes,
) -> str:
    if not isinstance(token, str):
        raise ValueError("authentication token is invalid")
    try:
        key = token.encode("utf-8")
        client_nonce_bytes = bytes.fromhex(client_nonce)
        server_nonce_bytes = bytes.fromhex(server_nonce)
    except (UnicodeEncodeError, ValueError):
        raise ValueError("authentication transcript is invalid") from None
    if (
        len(key) < 32
        or not is_authentication_nonce(client_nonce)
        or not is_authentication_nonce(server_nonce)
    ):
        raise ValueError("authentication transcript is invalid")
    transcript = (
        b"logic-bridge-auth\0"
        + AUTH_PROTOCOL.encode("ascii")
        + b"\0"
        + purpose
        + b"\0"
        + client_nonce_bytes
        + server_nonce_bytes
        + len(payload).to_bytes(8, "big")
        + payload
    )
    return hmac.new(key, transcript, hashlib.sha256).hexdigest()


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


@dataclass(frozen=True, slots=True)
class MethodSpec:
    schema_name: str
    service_method: str
    params_schema: Mapping[str, Any]
    threaded: bool = False
    defaults: Mapping[str, Any] = field(default_factory=dict)
    result_descriptor: str = "object"
    bridge_errors: tuple[str, ...] = ()
    lifecycle_states: tuple[str, ...] = ()
    unverified_success_sequence: tuple[str, ...] = ()
    verified_success_sequence: tuple[str, ...] = ()

    def call_arguments(self, params: Mapping[str, Any]) -> dict[str, Any]:
        return {**self.defaults, **params}


def _empty_params_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }


METHOD_SPECS: Mapping[str, MethodSpec] = MappingProxyType(
    {
        "bridge.health": MethodSpec(
            schema_name="health",
            service_method="health",
            params_schema=_empty_params_schema(),
            threaded=True,
            result_descriptor="bridge_health",
        ),
        "bridge.capabilities": MethodSpec(
            schema_name="capabilities",
            service_method="capabilities",
            params_schema=_empty_params_schema(),
            threaded=True,
            result_descriptor="bridge_capabilities",
        ),
        "midi.list_outputs": MethodSpec(
            schema_name="listOutputs",
            service_method="list_outputs",
            params_schema=_empty_params_schema(),
            threaded=True,
            result_descriptor="midi_output_inventory",
            bridge_errors=("MIDI_BACKEND_UNAVAILABLE",),
        ),
        "logic.set_volume": MethodSpec(
            schema_name="setVolume",
            service_method="set_volume",
            params_schema={
                "type": "object",
                "properties": {
                    "track_id": {"$ref": "#/$defs/trackId"},
                    "value": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                    },
                },
                "required": ["value"],
                "additionalProperties": False,
            },
            threaded=True,
            defaults=MappingProxyType({"track_id": "selected"}),
            result_descriptor="command_lifecycle",
            bridge_errors=(
                "INVALID_PARAMETERS",
                "UNKNOWN_ACTION",
                "MIDI_BACKEND_UNAVAILABLE",
                "MIDI_PORT_NOT_FOUND",
                "MIDI_DISPATCH_FAILED",
            ),
            lifecycle_states=("requested", "dispatched", "unknown"),
        ),
        "logic.toggle_mute": MethodSpec(
            schema_name="toggleMute",
            service_method="toggle_mute",
            params_schema={
                "type": "object",
                "properties": {
                    "track_id": {"$ref": "#/$defs/trackId"},
                },
                "additionalProperties": False,
            },
            threaded=True,
            defaults=MappingProxyType({"track_id": "selected"}),
            result_descriptor="command_lifecycle",
            bridge_errors=(
                "INVALID_PARAMETERS",
                "UNKNOWN_ACTION",
                "MIDI_BACKEND_UNAVAILABLE",
                "MIDI_PORT_NOT_FOUND",
                "MIDI_DISPATCH_FAILED",
            ),
            lifecycle_states=("requested", "dispatched", "unknown"),
        ),
        "logic.invoke_action": MethodSpec(
            schema_name="invokeAction",
            service_method="invoke_action",
            params_schema={
                "type": "object",
                "properties": {
                    "action_id": {"$ref": "#/$defs/resourceId"},
                    "value": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 127,
                    },
                },
                "required": ["action_id"],
                "additionalProperties": False,
            },
            threaded=True,
            result_descriptor="command_lifecycle",
            bridge_errors=(
                "INVALID_PARAMETERS",
                "UNKNOWN_ACTION",
                "MIDI_BACKEND_UNAVAILABLE",
                "MIDI_PORT_NOT_FOUND",
                "MIDI_DISPATCH_FAILED",
            ),
            lifecycle_states=("requested", "dispatched", "unknown"),
        ),
        "logic.set_scripter_parameter": MethodSpec(
            schema_name="setScripterParameter",
            service_method="set_scripter_parameter",
            params_schema={
                "type": "object",
                "properties": {
                    "parameter_id": {"$ref": "#/$defs/resourceId"},
                    "value": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                    },
                },
                "required": ["parameter_id", "value"],
                "additionalProperties": False,
            },
            threaded=True,
            result_descriptor="command_lifecycle",
            bridge_errors=(
                "INVALID_PARAMETERS",
                "UNKNOWN_ACTION",
                "SCRIPTER_UNAVAILABLE",
                "MIDI_BACKEND_UNAVAILABLE",
                "MIDI_PORT_NOT_FOUND",
                "MIDI_DISPATCH_FAILED",
            ),
            lifecycle_states=("requested", "dispatched", "unknown"),
        ),
        "logic.inspect_mixer_target": MethodSpec(
            schema_name="inspectMixerTarget",
            service_method="inspect_mixer_target",
            params_schema={
                "type": "object",
                "properties": {
                    "target_id": {"$ref": "#/$defs/resourceId"},
                },
                "required": ["target_id"],
                "additionalProperties": False,
            },
            threaded=True,
            result_descriptor="mixer_target_observation",
            bridge_errors=(
                "CST_LOADING_DISABLED",
                "TARGET_NOT_ALLOWLISTED",
                "ACCESSIBILITY_PERMISSION_REQUIRED",
                "AUTOMATION_PERMISSION_REQUIRED",
                "LOGIC_NOT_RUNNING",
                "MIXER_WINDOW_NOT_FOUND",
                "TARGET_MISMATCH",
                "SETTING_POPUP_NOT_FOUND",
                "UI_OPERATION_BUSY",
                "PRESET_AUTOMATION_FAILED",
            ),
        ),
        "logic.load_cst_preset": MethodSpec(
            schema_name="loadCstPreset",
            service_method="load_cst_preset",
            params_schema={
                "type": "object",
                "properties": {
                    "preset_id": {"$ref": "#/$defs/resourceId"},
                    "target_id": {"$ref": "#/$defs/resourceId"},
                    "confirmation": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 400,
                    },
                },
                "required": ["preset_id", "target_id", "confirmation"],
                "additionalProperties": False,
            },
            threaded=True,
            result_descriptor="command_lifecycle",
            bridge_errors=(
                "INVALID_PARAMETERS",
                "CONFIRMATION_REQUIRED",
                "CST_LOADING_DISABLED",
                "PRESET_NOT_ALLOWLISTED",
                "TARGET_NOT_ALLOWLISTED",
                "PRESET_FILE_UNAVAILABLE",
                "PRESET_FILE_REJECTED",
                "PRESET_INTEGRITY_MISMATCH",
                "ACCESSIBILITY_PERMISSION_REQUIRED",
                "AUTOMATION_PERMISSION_REQUIRED",
                "LOGIC_NOT_RUNNING",
                "MIXER_WINDOW_NOT_FOUND",
                "TARGET_MISMATCH",
                "SETTING_POPUP_NOT_FOUND",
                "PRESET_MENU_ITEM_NOT_FOUND",
                "UI_OPERATION_BUSY",
                "PRESET_LOAD_TIMEOUT",
                "PRESET_AUTOMATION_FAILED",
            ),
            lifecycle_states=(
                "requested",
                "dispatched",
                "observed",
                "verified",
                "unknown",
            ),
            unverified_success_sequence=(
                "requested",
                "dispatched",
                "observed",
                "unknown",
            ),
            verified_success_sequence=(
                "requested",
                "dispatched",
                "observed",
                "verified",
            ),
        ),
    }
)


class InvalidRequestContractError(Exception):
    """Request envelope does not match the supported JSON-RPC subset."""


def validate_request(request: Mapping[str, Any]) -> None:
    if set(request) != _REQUEST_FIELDS:
        raise InvalidRequestContractError
    if request["jsonrpc"] != "2.0":
        raise InvalidRequestContractError

    request_id = request["id"]
    if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
        raise InvalidRequestContractError
    if isinstance(request_id, str) and not request_id:
        raise InvalidRequestContractError
    if not isinstance(request["method"], str):
        raise InvalidRequestContractError
    if not isinstance(request["params"], Mapping):
        raise InvalidRequestContractError

    meta = request["meta"]
    if not isinstance(meta, Mapping):
        raise InvalidRequestContractError
    if set(meta) - _AUTH_META_FIELDS:
        raise InvalidRequestContractError
    if set(meta) != _AUTH_META_FIELDS:
        raise AuthenticationRequiredError("Authentication proof is missing or invalid")
    if (
        meta.get("auth") != AUTH_PROTOCOL
        or not is_authentication_nonce(meta.get("client_nonce"))
        or not is_authentication_nonce(meta.get("server_nonce"))
        or not is_authentication_proof(meta.get("request_proof"))
    ):
        raise AuthenticationRequiredError("Authentication proof is missing or invalid")


def validate_params(spec: MethodSpec, params: Mapping[str, Any]) -> None:
    try:
        schema = spec.params_schema
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        unknown = sorted(set(params) - set(properties))
        if unknown:
            raise InvalidParametersError(
                "params contains unknown fields",
                details={"unknown": unknown},
            )
        missing = sorted(required - set(params))
        if missing:
            raise InvalidParametersError(
                "params is missing required fields",
                details={"missing": missing},
            )
        for name, value in params.items():
            _validate_value(name, value, properties[name])
    except InvalidParametersError as exc:
        if not spec.lifecycle_states or "events" in exc.details:
            raise
        requested = {
            "state": "requested",
            "at": datetime.now(UTC).isoformat(),
            "operation": f"logic.{spec.service_method}",
        }
        raise exc.with_details(events=[requested]) from exc


def capability_method_specs() -> dict[str, dict[str, Any]]:
    method_specs: dict[str, dict[str, Any]] = {}
    for method, spec in METHOD_SPECS.items():
        errors = [
            {
                "rpc_code": -32001,
                "bridge_code": "AUTHENTICATION_REQUIRED",
            },
            {
                "rpc_code": -32602,
                "bridge_code": "INVALID_PARAMETERS",
            },
        ]
        errors.extend(
            {
                "rpc_code": -32000,
                "bridge_code": bridge_code,
            }
            for bridge_code in spec.bridge_errors
            if bridge_code != "INVALID_PARAMETERS"
        )
        lifecycle: dict[str, Any]
        if spec.lifecycle_states:
            unverified_sequence = (
                spec.unverified_success_sequence or spec.lifecycle_states
            )
            lifecycle = {
                "applicable": True,
                "states": list(spec.lifecycle_states),
                "unverified_success_sequence": list(unverified_sequence),
                "validation_error_sequence": ["requested"],
            }
            if spec.verified_success_sequence:
                lifecycle["verified_success_sequence"] = list(
                    spec.verified_success_sequence
                )
        else:
            lifecycle = {"applicable": False, "states": []}
        method_specs[method] = {
            "params": deepcopy(dict(spec.params_schema)),
            "result": {
                "type": "object",
                "descriptor": spec.result_descriptor,
            },
            "errors": errors,
            "lifecycle": lifecycle,
        }
    return method_specs


def request_schema() -> dict[str, Any]:
    method_definitions = {
        spec.schema_name: {
            "allOf": [
                {"$ref": "#/$defs/base"},
                {
                    "properties": {
                        "method": {"const": method},
                        "params": deepcopy(dict(spec.params_schema)),
                    }
                },
            ]
        }
        for method, spec in METHOD_SPECS.items()
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://local.logic-bridge.invalid/schemas/command.json",
        "title": "AI-to-Logic Pro Bridge JSON-RPC Request",
        "description": (
            "One authenticated newline-delimited JSON-RPC 2.0 request for "
            f"{AUTH_PROTOCOL}. The per-connection nonce exchange precedes this "
            "envelope; meta carries only the bound request proof, never the "
            "shared secret. Batch requests and notifications are unsupported."
        ),
        "oneOf": [
            {"$ref": f"#/$defs/{spec.schema_name}"} for spec in METHOD_SPECS.values()
        ],
        "$defs": {
            "id": {
                "oneOf": [
                    {"type": "integer"},
                    {"type": "string", "minLength": 1},
                ]
            },
            "meta": {
                "type": "object",
                "properties": {
                    "auth": {"const": AUTH_PROTOCOL},
                    "client_nonce": {
                        "type": "string",
                        "pattern": _AUTH_NONCE_PATTERN.pattern,
                        "minLength": AUTH_NONCE_HEX_LENGTH,
                        "maxLength": AUTH_NONCE_HEX_LENGTH,
                    },
                    "server_nonce": {
                        "type": "string",
                        "pattern": _AUTH_NONCE_PATTERN.pattern,
                        "minLength": AUTH_NONCE_HEX_LENGTH,
                        "maxLength": AUTH_NONCE_HEX_LENGTH,
                    },
                    "request_proof": {
                        "type": "string",
                        "pattern": _AUTH_PROOF_PATTERN.pattern,
                        "minLength": AUTH_PROOF_HEX_LENGTH,
                        "maxLength": AUTH_PROOF_HEX_LENGTH,
                    },
                },
                "required": sorted(_AUTH_META_FIELDS),
                "additionalProperties": False,
            },
            "trackId": deepcopy(_TRACK_ID_SCHEMA),
            "resourceId": {
                "type": "string",
                "pattern": _RESOURCE_ID_PATTERN.pattern,
                "maxLength": 128,
            },
            "base": {
                "type": "object",
                "properties": {
                    "jsonrpc": {"const": "2.0"},
                    "id": {"$ref": "#/$defs/id"},
                    "method": {"type": "string"},
                    "params": {"type": "object"},
                    "meta": {"$ref": "#/$defs/meta"},
                },
                "required": ["jsonrpc", "id", "method", "params", "meta"],
                "additionalProperties": False,
            },
            **method_definitions,
        },
    }


def _validate_value(name: str, value: Any, schema: Mapping[str, Any]) -> None:
    if schema.get("$ref") == "#/$defs/trackId":
        if value == "selected":
            return
        if isinstance(value, bool):
            _invalid_value(name)
        if isinstance(value, int) and 1 <= value <= TRACK_ID_MAX:
            return
        if isinstance(value, str) and TRACK_ID_PATTERN.fullmatch(value):
            return
        _invalid_value(name)

    if schema.get("$ref") == "#/$defs/resourceId":
        if (
            isinstance(value, str)
            and len(value) <= 128
            and _RESOURCE_ID_PATTERN.fullmatch(value)
        ):
            return
        _invalid_value(name)

    if "const" in schema:
        const_value = schema["const"]
        same_bool_kind = isinstance(value, bool) == isinstance(const_value, bool)
        if same_bool_kind and value == const_value:
            return
        _invalid_value(name)

    if schema.get("type") == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _invalid_value(name)
        try:
            numeric = float(value)
        except OverflowError:
            _invalid_value(name)
        if not math.isfinite(numeric):
            _invalid_value(name)
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and numeric < minimum:
            _invalid_value(name)
        if maximum is not None and numeric > maximum:
            _invalid_value(name)
        return

    if schema.get("type") == "string":
        if not isinstance(value, str):
            _invalid_value(name)
        minimum_length = schema.get("minLength")
        maximum_length = schema.get("maxLength")
        if minimum_length is not None and len(value) < minimum_length:
            _invalid_value(name)
        if maximum_length is not None and len(value) > maximum_length:
            _invalid_value(name)
        return

    raise RuntimeError(f"Unsupported RPC parameter schema for {name}")


def _invalid_value(name: str) -> NoReturn:
    raise InvalidParametersError(
        f"params.{name} does not match the method contract",
        details={"field": name},
    )
