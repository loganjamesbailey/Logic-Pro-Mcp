from __future__ import annotations

import ipaddress
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from daemon.errors import ConfigurationError
from daemon.config_security import (
    MIN_AUTH_TOKEN_BYTES as MIN_AUTH_TOKEN_BYTES,
    authentication_token_reason as authentication_token_reason,
    load_secure_config_mapping,
    validate_authentication_token as validate_authentication_token,
)
from daemon.scripter import MIDO_CHANNEL, PARAMETERS, SCRIPT_SHA256

_ACTION_NAME = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SCRIPTER_PROTOCOL_VERSION = 1
_SCRIPTER_PLACEMENT = "software_instrument_midi_fx"
_SCRIPTER_TARGETS = {
    parameter.id: (parameter.control, target_slot, parameter.target)
    for target_slot, parameter in enumerate(PARAMETERS.values(), start=1)
}
_SCRIPTER_RESERVED_CONTROLS = frozenset(
    control for control, _slot, _target in _SCRIPTER_TARGETS.values()
)


@dataclass(frozen=True, slots=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    token_env: str = "LOGIC_BRIDGE_TOKEN"
    max_request_bytes: int = 65_536
    read_timeout_seconds: float = 10.0
    write_timeout_seconds: float = 2.0
    mcp_timeout_seconds: float = 75.0
    max_connections: int = 32


@dataclass(frozen=True, slots=True)
class AssignmentConfig:
    name: str
    kind: str
    value_mode: str
    channel: int = -1
    control: int = -1
    note: int = -1
    velocity: int = -1
    release_velocity: int = -1
    data: tuple[int, ...] = ()
    value_index: int | None = None
    value: int | None = None
    release_value: int | None = None

    def public_metadata(self) -> dict[str, Any]:
        return {
            "id": self.name,
            "kind": self.kind,
            "value_mode": self.value_mode,
            "prerequisites": {
                "midi_output": "required",
                "logic_routing": "operator_configured",
            },
        }


@dataclass(frozen=True, slots=True)
class MidiConfig:
    output_name: str
    assignments: Mapping[str, AssignmentConfig]


@dataclass(frozen=True, slots=True)
class PermissionConfig:
    require_accessibility_for_ui: bool = True


@dataclass(frozen=True, slots=True)
class CstPresetConfig:
    id: str
    filename: str
    sha256: str
    expected_plugin_signature: tuple[str, ...] = ()
    signature_match_mode: str = "exact"

    def public_metadata(self) -> dict[str, str]:
        return {
            "id": self.id,
            "kind": "channel_strip_setting",
            "load_method": "logic.load_cst_preset",
        }


@dataclass(frozen=True, slots=True)
class CstTargetConfig:
    id: str
    mixer_index: int
    expected_accessibility_name: str

    def public_metadata(self) -> dict[str, str]:
        return {
            "id": self.id,
            "kind": "mixer_channel_strip_target",
            "load_method": "logic.load_cst_preset",
            "inspect_method": "logic.inspect_mixer_target",
        }


@dataclass(frozen=True, slots=True)
class AccessibilityConfig:
    enabled: bool
    preset_root: Path
    timeout_seconds: float
    max_cst_bytes: int
    presets: Mapping[str, CstPresetConfig]
    targets: Mapping[str, CstTargetConfig]


@dataclass(frozen=True, slots=True)
class ScripterLearnedTargetConfig:
    parameter_id: str
    control: int
    target_slot: int
    target_name: str


@dataclass(frozen=True, slots=True)
class ScripterConfig:
    enabled: bool
    protocol_version: int
    channel: int
    source_sha256: str
    placement: str
    operator_attested: bool
    learned_targets: Mapping[str, ScripterLearnedTargetConfig]

    def public_metadata(self) -> dict[str, bool | str | None]:
        if self.enabled:
            return {
                "status": "ready",
                "reason": None,
                "next_action": None,
                "software_instrument_only": True,
            }
        return {
            "status": "blocked",
            "reason": "scripter_disabled",
            "next_action": "enable_reviewed_scripter_profile",
            "software_instrument_only": True,
        }


@dataclass(frozen=True, slots=True)
class BridgeConfig:
    server: ServerConfig
    midi: MidiConfig
    permissions: PermissionConfig
    accessibility: AccessibilityConfig
    scripter: ScripterConfig

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> BridgeConfig:
        root = _mapping(raw, "config")
        _reject_unknown(
            root,
            {"server", "midi", "permissions", "accessibility", "scripter"},
            "config",
        )

        server_raw = _mapping(root.get("server", {}), "server")
        _reject_unknown(
            server_raw,
            {
                "host",
                "port",
                "token_env",
                "max_request_bytes",
                "read_timeout_seconds",
                "write_timeout_seconds",
                "mcp_timeout_seconds",
                "max_connections",
            },
            "server",
        )
        host = _string(server_raw.get("host", "127.0.0.1"), "server.host")
        try:
            is_loopback = ipaddress.ip_address(host).is_loopback
        except ValueError as exc:
            raise ConfigurationError(
                "server.host must be a literal loopback IP address",
                details={"field": "server.host"},
            ) from exc
        if not is_loopback:
            raise ConfigurationError(
                "server.host must be a loopback address",
                details={"field": "server.host", "value": host},
            )

        port = _integer(server_raw.get("port", 8765), "server.port")
        if not 0 <= port <= 65_535:
            raise ConfigurationError("server.port must be between 0 and 65535")
        token_env = _string(
            server_raw.get("token_env", "LOGIC_BRIDGE_TOKEN"),
            "server.token_env",
        )
        if not token_env:
            raise ConfigurationError("server.token_env cannot be empty")
        max_request_bytes = _integer(
            server_raw.get("max_request_bytes", 65_536),
            "server.max_request_bytes",
        )
        if not 1_024 <= max_request_bytes <= 1_048_576:
            raise ConfigurationError(
                "server.max_request_bytes must be between 1024 and 1048576"
            )
        read_timeout_seconds = _number(
            server_raw.get("read_timeout_seconds", 10.0),
            "server.read_timeout_seconds",
        )
        if not 0.01 <= read_timeout_seconds <= 300.0:
            raise ConfigurationError(
                "server.read_timeout_seconds must be between 0.01 and 300"
            )
        write_timeout_seconds = _number(
            server_raw.get("write_timeout_seconds", 2.0),
            "server.write_timeout_seconds",
        )
        if not 0.01 <= write_timeout_seconds <= 30.0:
            raise ConfigurationError(
                "server.write_timeout_seconds must be between 0.01 and 30"
            )
        mcp_timeout_seconds = _number(
            server_raw.get("mcp_timeout_seconds", 75.0),
            "server.mcp_timeout_seconds",
        )
        if not 1.0 <= mcp_timeout_seconds <= 300.0:
            raise ConfigurationError(
                "server.mcp_timeout_seconds must be between 1 and 300"
            )
        max_connections = _integer(
            server_raw.get("max_connections", 32),
            "server.max_connections",
        )
        if not 1 <= max_connections <= 1_024:
            raise ConfigurationError(
                "server.max_connections must be between 1 and 1024"
            )
        server = ServerConfig(
            host=host,
            port=port,
            token_env=token_env,
            max_request_bytes=max_request_bytes,
            read_timeout_seconds=read_timeout_seconds,
            write_timeout_seconds=write_timeout_seconds,
            mcp_timeout_seconds=mcp_timeout_seconds,
            max_connections=max_connections,
        )

        midi_raw = _mapping(root.get("midi"), "midi")
        _reject_unknown(midi_raw, {"output_name", "assignments"}, "midi")
        output_name = _string(midi_raw.get("output_name"), "midi.output_name")
        if not output_name.strip():
            raise ConfigurationError("midi.output_name cannot be empty")
        assignments_raw = midi_raw.get("assignments", [])
        if not isinstance(assignments_raw, list):
            raise ConfigurationError("midi.assignments must be an array of tables")

        assignments: dict[str, AssignmentConfig] = {}
        for index, item in enumerate(assignments_raw):
            assignment = _assignment(
                _mapping(item, f"midi.assignments[{index}]"), index
            )
            if assignment.name in assignments:
                raise ConfigurationError(
                    f"Duplicate MIDI assignment: {assignment.name}",
                    details={"field": f"midi.assignments[{index}].name"},
                )
            assignments[assignment.name] = assignment
        for assignment in assignments.values():
            if (
                assignment.kind == "control_change"
                and assignment.channel == MIDO_CHANNEL
                and assignment.control in _SCRIPTER_RESERVED_CONTROLS
            ):
                raise ConfigurationError(
                    "Generic MIDI assignment collides with the reserved Scripter protocol route",
                    details={"field": "midi.assignments", "name": assignment.name},
                )
        midi = MidiConfig(
            output_name=output_name,
            assignments=MappingProxyType(assignments),
        )

        if "scripter" in root:
            scripter = _scripter_config(_mapping(root["scripter"], "scripter"))
        else:
            scripter = _default_scripter_config()

        permissions_raw = _mapping(root.get("permissions", {}), "permissions")
        _reject_unknown(
            permissions_raw,
            {"require_accessibility_for_ui"},
            "permissions",
        )
        permissions = PermissionConfig(
            require_accessibility_for_ui=_boolean(
                permissions_raw.get("require_accessibility_for_ui", True),
                "permissions.require_accessibility_for_ui",
            )
        )

        accessibility_raw = _mapping(
            root.get("accessibility", {}),
            "accessibility",
        )
        _reject_unknown(
            accessibility_raw,
            {
                "enabled",
                "preset_root",
                "timeout_seconds",
                "max_cst_bytes",
                "presets",
                "targets",
            },
            "accessibility",
        )
        enabled = _boolean(
            accessibility_raw.get("enabled", False),
            "accessibility.enabled",
        )
        default_preset_root = (
            Path.home()
            / "Music"
            / "Audio Music Apps"
            / "Channel Strip Settings"
            / "Track"
        )
        preset_root = Path(
            _string(
                accessibility_raw.get("preset_root", str(default_preset_root)),
                "accessibility.preset_root",
            )
        ).expanduser()
        if not preset_root.is_absolute():
            raise ConfigurationError(
                "accessibility.preset_root must be an absolute or home-relative path"
            )
        preset_root = preset_root.resolve(strict=False)
        timeout_seconds = _number(
            accessibility_raw.get("timeout_seconds", 20.0),
            "accessibility.timeout_seconds",
        )
        if not 1.0 <= timeout_seconds <= 30.0:
            raise ConfigurationError(
                "accessibility.timeout_seconds must be between 1 and 30"
            )
        max_cst_bytes = _integer(
            accessibility_raw.get("max_cst_bytes", 16_777_216),
            "accessibility.max_cst_bytes",
        )
        if not 1 <= max_cst_bytes <= 67_108_864:
            raise ConfigurationError(
                "accessibility.max_cst_bytes must be between 1 and 67108864"
            )

        presets_raw = accessibility_raw.get("presets", [])
        if not isinstance(presets_raw, list):
            raise ConfigurationError("accessibility.presets must be an array of tables")
        if len(presets_raw) > 128:
            raise ConfigurationError(
                "accessibility.presets cannot contain more than 128 entries"
            )
        presets: dict[str, CstPresetConfig] = {}
        for index, item in enumerate(presets_raw):
            preset = _cst_preset(
                _mapping(item, f"accessibility.presets[{index}]"),
                index,
            )
            if preset.id in presets:
                raise ConfigurationError(
                    f"Duplicate channel-strip preset ID: {preset.id}",
                    details={"field": f"accessibility.presets[{index}].id"},
                )
            presets[preset.id] = preset

        targets_raw = accessibility_raw.get("targets", [])
        if not isinstance(targets_raw, list):
            raise ConfigurationError("accessibility.targets must be an array of tables")
        if len(targets_raw) > 1_024:
            raise ConfigurationError(
                "accessibility.targets cannot contain more than 1024 entries"
            )
        targets: dict[str, CstTargetConfig] = {}
        for index, item in enumerate(targets_raw):
            target = _cst_target(
                _mapping(item, f"accessibility.targets[{index}]"),
                index,
            )
            if target.id in targets:
                raise ConfigurationError(
                    f"Duplicate channel-strip target ID: {target.id}",
                    details={"field": f"accessibility.targets[{index}].id"},
                )
            targets[target.id] = target

        if enabled and not presets:
            raise ConfigurationError(
                "Enabled .cst loading requires at least one preset allowlist entry"
            )
        if enabled and not targets:
            raise ConfigurationError(
                "Enabled .cst loading requires at least one target allowlist entry"
            )
        accessibility = AccessibilityConfig(
            enabled=enabled,
            preset_root=preset_root,
            timeout_seconds=timeout_seconds,
            max_cst_bytes=max_cst_bytes,
            presets=MappingProxyType(presets),
            targets=MappingProxyType(targets),
        )
        minimum_mcp_timeout = (2.0 * timeout_seconds) + 5.0
        if enabled and mcp_timeout_seconds < minimum_mcp_timeout:
            raise ConfigurationError(
                "server.mcp_timeout_seconds must exceed two Accessibility "
                "operation deadlines plus 5 seconds",
                details={"field": "server.mcp_timeout_seconds"},
            )
        return cls(
            server=server,
            midi=midi,
            permissions=permissions,
            accessibility=accessibility,
            scripter=scripter,
        )


def load_config(path: str | Path) -> BridgeConfig:
    return BridgeConfig.from_mapping(load_secure_config_mapping(path))


def _assignment(raw: Mapping[str, Any], index: int) -> AssignmentConfig:
    prefix = f"midi.assignments[{index}]"
    name = _resource_id(raw.get("name"), f"{prefix}.name")
    kind = _string(raw.get("kind"), f"{prefix}.kind")
    if kind == "control_change":
        return _control_change_assignment(raw, prefix=prefix, name=name)
    if kind == "note":
        return _note_assignment(raw, prefix=prefix, name=name)
    if kind == "sysex":
        return _sysex_assignment(raw, prefix=prefix, name=name)
    raise ConfigurationError(f"{prefix}.kind must be control_change, note, or sysex")


def _control_change_assignment(
    raw: Mapping[str, Any],
    *,
    prefix: str,
    name: str,
) -> AssignmentConfig:
    _reject_unknown(
        raw,
        {
            "name",
            "kind",
            "channel",
            "control",
            "value_mode",
            "value",
            "release_value",
        },
        prefix,
    )
    channel = _integer(raw.get("channel"), f"{prefix}.channel")
    if not 0 <= channel <= 15:
        raise ConfigurationError(f"{prefix}.channel must be between 0 and 15")
    control = _integer(raw.get("control"), f"{prefix}.control")
    if not 0 <= control <= 127:
        raise ConfigurationError(f"{prefix}.control must be between 0 and 127")
    value_mode = _string(raw.get("value_mode"), f"{prefix}.value_mode")
    if value_mode not in {"normalized", "absolute", "trigger"}:
        raise ConfigurationError(
            f"{prefix}.value_mode must be normalized, absolute, or trigger"
        )

    if value_mode == "trigger":
        if "value" not in raw:
            raise ConfigurationError(
                f"{prefix}.value is required for trigger assignments"
            )
        value = _midi_value(raw.get("value"), f"{prefix}.value")
        release_value = (
            _midi_value(raw.get("release_value"), f"{prefix}.release_value")
            if "release_value" in raw
            else None
        )
    elif "value" in raw or "release_value" in raw:
        raise ConfigurationError(
            f"{prefix}.value and release_value are only valid for trigger assignments"
        )
    else:
        value = None
        release_value = None
    return AssignmentConfig(
        name=name,
        kind="control_change",
        value_mode=value_mode,
        channel=channel,
        control=control,
        value=value,
        release_value=release_value,
    )


def _note_assignment(
    raw: Mapping[str, Any],
    *,
    prefix: str,
    name: str,
) -> AssignmentConfig:
    _reject_unknown(
        raw,
        {
            "name",
            "kind",
            "channel",
            "note",
            "velocity",
            "release_velocity",
            "value_mode",
        },
        prefix,
    )
    value_mode = _string(raw.get("value_mode"), f"{prefix}.value_mode")
    if value_mode != "trigger":
        raise ConfigurationError(f"{prefix}.value_mode must be trigger for note")
    channel = _integer(raw.get("channel"), f"{prefix}.channel")
    if not 0 <= channel <= 15:
        raise ConfigurationError(f"{prefix}.channel must be between 0 and 15")
    note = _integer(raw.get("note"), f"{prefix}.note")
    if not 0 <= note <= 127:
        raise ConfigurationError(f"{prefix}.note must be between 0 and 127")
    velocity = _integer(raw.get("velocity"), f"{prefix}.velocity")
    if not 1 <= velocity <= 127:
        raise ConfigurationError(f"{prefix}.velocity must be between 1 and 127")
    release_velocity = _integer(
        raw.get("release_velocity"),
        f"{prefix}.release_velocity",
    )
    if not 0 <= release_velocity <= 127:
        raise ConfigurationError(f"{prefix}.release_velocity must be between 0 and 127")
    return AssignmentConfig(
        name=name,
        kind="note",
        value_mode=value_mode,
        channel=channel,
        note=note,
        velocity=velocity,
        release_velocity=release_velocity,
    )


def _sysex_assignment(
    raw: Mapping[str, Any],
    *,
    prefix: str,
    name: str,
) -> AssignmentConfig:
    _reject_unknown(
        raw,
        {"name", "kind", "data", "value_index", "value_mode"},
        prefix,
    )
    data_raw = raw.get("data")
    if not isinstance(data_raw, list):
        raise ConfigurationError(f"{prefix}.data must be an array of integers")
    if not 1 <= len(data_raw) <= 256:
        raise ConfigurationError(
            f"{prefix}.data must contain between 1 and 256 integers"
        )
    data = tuple(
        _midi_value(item, f"{prefix}.data[{item_index}]")
        for item_index, item in enumerate(data_raw)
    )

    value_mode = _string(raw.get("value_mode"), f"{prefix}.value_mode")
    has_value_index = "value_index" in raw
    if has_value_index:
        value_index = _integer(
            raw.get("value_index"),
            f"{prefix}.value_index",
        )
        if not 0 <= value_index < len(data):
            raise ConfigurationError(
                f"{prefix}.value_index must address exactly one configured data byte"
            )
        if value_mode not in {"normalized", "absolute"}:
            raise ConfigurationError(
                f"{prefix}.value_mode must be normalized or absolute when value_index is configured"
            )
    else:
        value_index = None
        if value_mode != "trigger":
            raise ConfigurationError(
                f"{prefix}.value_mode must be trigger when value_index is absent"
            )

    return AssignmentConfig(
        name=name,
        kind="sysex",
        value_mode=value_mode,
        data=data,
        value_index=value_index,
    )


def _default_scripter_config() -> ScripterConfig:
    learned_targets = {
        parameter_id: ScripterLearnedTargetConfig(
            parameter_id=parameter_id,
            control=control,
            target_slot=target_slot,
            target_name=target_name,
        )
        for parameter_id, (
            control,
            target_slot,
            target_name,
        ) in _SCRIPTER_TARGETS.items()
    }
    return ScripterConfig(
        enabled=False,
        protocol_version=_SCRIPTER_PROTOCOL_VERSION,
        channel=MIDO_CHANNEL,
        source_sha256=SCRIPT_SHA256,
        placement=_SCRIPTER_PLACEMENT,
        operator_attested=False,
        learned_targets=MappingProxyType(learned_targets),
    )


def _scripter_config(raw: Mapping[str, Any]) -> ScripterConfig:
    required_fields = {
        "enabled",
        "protocol_version",
        "channel",
        "source_sha256",
        "placement",
        "operator_attested",
        "learned_targets",
    }
    _reject_unknown(raw, required_fields, "scripter")
    missing = sorted(required_fields - set(raw))
    if missing:
        raise ConfigurationError(
            "scripter requires all fixed protocol fields",
            details={"field": "scripter", "missing": missing},
        )

    enabled = _boolean(raw["enabled"], "scripter.enabled")
    protocol_version = _integer(
        raw["protocol_version"],
        "scripter.protocol_version",
    )
    if protocol_version != _SCRIPTER_PROTOCOL_VERSION:
        raise ConfigurationError("scripter.protocol_version must be 1")
    channel = _integer(raw["channel"], "scripter.channel")
    if channel != MIDO_CHANNEL:
        raise ConfigurationError(
            "scripter.channel must be Mido channel 15 (human MIDI channel 16)"
        )
    source_sha256 = _string(raw["source_sha256"], "scripter.source_sha256")
    if source_sha256 != SCRIPT_SHA256:
        raise ConfigurationError(
            "scripter.source_sha256 must match the reviewed packaged source"
        )
    placement = _string(raw["placement"], "scripter.placement")
    if placement != _SCRIPTER_PLACEMENT:
        raise ConfigurationError(
            "scripter.placement must be software-instrument MIDI FX only"
        )
    operator_attested = _boolean(
        raw["operator_attested"],
        "scripter.operator_attested",
    )

    learned_targets_raw = raw["learned_targets"]
    if not isinstance(learned_targets_raw, list) or len(learned_targets_raw) != 2:
        raise ConfigurationError(
            "scripter.learned_targets must declare exactly two fixed targets"
        )
    learned_targets: dict[str, ScripterLearnedTargetConfig] = {}
    target_fields = {"parameter_id", "control", "target_slot", "target_name"}
    for index, item in enumerate(learned_targets_raw):
        prefix = f"scripter.learned_targets[{index}]"
        target_raw = _mapping(item, prefix)
        _reject_unknown(target_raw, target_fields, prefix)
        missing_target_fields = sorted(target_fields - set(target_raw))
        if missing_target_fields:
            raise ConfigurationError(
                f"{prefix} requires all fixed target fields",
                details={"field": prefix, "missing": missing_target_fields},
            )

        parameter_id = _string(
            target_raw["parameter_id"],
            f"{prefix}.parameter_id",
        )
        if parameter_id in learned_targets:
            raise ConfigurationError(
                f"{prefix}.parameter_id is a duplicate fixed Scripter parameter"
            )
        expected = _SCRIPTER_TARGETS.get(parameter_id)
        if expected is None:
            raise ConfigurationError(
                f"{prefix}.parameter_id is not part of Scripter protocol v1"
            )
        expected_control, expected_slot, expected_name = expected
        control = _integer(target_raw["control"], f"{prefix}.control")
        if control != expected_control:
            raise ConfigurationError(
                f"{prefix}.control does not match Scripter protocol v1"
            )
        target_slot = _integer(
            target_raw["target_slot"],
            f"{prefix}.target_slot",
        )
        if target_slot != expected_slot:
            raise ConfigurationError(
                f"{prefix}.target_slot does not match Scripter protocol v1"
            )
        target_name = _string(
            target_raw["target_name"],
            f"{prefix}.target_name",
        )
        if target_name != expected_name:
            raise ConfigurationError(
                f"{prefix}.target_name does not match Scripter protocol v1"
            )
        learned_targets[parameter_id] = ScripterLearnedTargetConfig(
            parameter_id=parameter_id,
            control=control,
            target_slot=target_slot,
            target_name=target_name,
        )

    if set(learned_targets) != set(_SCRIPTER_TARGETS):
        raise ConfigurationError(
            "scripter.learned_targets must declare exactly the protocol v1 parameters"
        )
    if enabled and not operator_attested:
        raise ConfigurationError(
            "Enabled Scripter requires operator_attested after manual installation and target learning"
        )
    return ScripterConfig(
        enabled=enabled,
        protocol_version=protocol_version,
        channel=channel,
        source_sha256=source_sha256,
        placement=placement,
        operator_attested=operator_attested,
        learned_targets=MappingProxyType(learned_targets),
    )


def _cst_preset(raw: Mapping[str, Any], index: int) -> CstPresetConfig:
    prefix = f"accessibility.presets[{index}]"
    _reject_unknown(
        raw,
        {
            "id",
            "filename",
            "sha256",
            "expected_plugin_signature",
            "signature_match_mode",
        },
        prefix,
    )
    preset_id = _resource_id(raw.get("id"), f"{prefix}.id")
    filename = _string(raw.get("filename"), f"{prefix}.filename")
    if (
        not filename
        or Path(filename).name != filename
        or "/" in filename
        or ":" in filename
        or any(ord(character) < 32 for character in filename)
        or len(Path(filename).stem) > 128
    ):
        raise ConfigurationError(f"{prefix}.filename must be a filename only")
    if not filename.endswith(".cst") or filename == ".cst":
        raise ConfigurationError(f"{prefix}.filename must end in .cst")
    sha256 = _string(raw.get("sha256"), f"{prefix}.sha256")
    if not _SHA256.fullmatch(sha256):
        raise ConfigurationError(
            f"{prefix}.sha256 must be 64 lowercase hexadecimal characters"
        )
    signature_raw = raw.get("expected_plugin_signature", [])
    if not isinstance(signature_raw, list) or len(signature_raw) > 32:
        raise ConfigurationError(
            f"{prefix}.expected_plugin_signature must contain at most 32 names"
        )
    expected_plugin_signature: list[str] = []
    for plugin_index, value in enumerate(signature_raw):
        field = f"{prefix}.expected_plugin_signature[{plugin_index}]"
        plugin = _string(value, field)
        if (
            not plugin
            or plugin != plugin.strip()
            or len(plugin) > 128
            or any(ord(character) < 32 or ord(character) == 127 for character in plugin)
            or "/" in plugin
            or "\\" in plugin
        ):
            raise ConfigurationError(f"{field} must be a safe normalized plug-in name")
        expected_plugin_signature.append(plugin)
    signature_match_mode = _string(
        raw.get("signature_match_mode", "exact"),
        f"{prefix}.signature_match_mode",
    )
    if signature_match_mode not in {"exact", "subset"}:
        raise ConfigurationError(
            f"{prefix}.signature_match_mode must be exact or subset"
        )
    if not expected_plugin_signature and "signature_match_mode" in raw:
        raise ConfigurationError(
            f"{prefix}.signature_match_mode requires expected_plugin_signature"
        )
    return CstPresetConfig(
        id=preset_id,
        filename=filename,
        sha256=sha256,
        expected_plugin_signature=tuple(expected_plugin_signature),
        signature_match_mode=signature_match_mode,
    )


def _cst_target(raw: Mapping[str, Any], index: int) -> CstTargetConfig:
    prefix = f"accessibility.targets[{index}]"
    _reject_unknown(
        raw,
        {"id", "mixer_index", "expected_accessibility_name"},
        prefix,
    )
    target_id = _resource_id(raw.get("id"), f"{prefix}.id")
    mixer_index = _integer(raw.get("mixer_index"), f"{prefix}.mixer_index")
    # Must match MAX_STRIPS in bridge_scripts/{load_cst,cleanup_cst,inspect_mixer}.js
    # (zero-based, so the highest valid index is MAX_STRIPS - 1); a config value
    # above that ceiling can never be satisfied and would fail at dispatch time
    # with a misleading target-mismatch error instead of failing at load time.
    if not 0 <= mixer_index <= 255:
        raise ConfigurationError(f"{prefix}.mixer_index must be between 0 and 255")
    expected_name = _string(
        raw.get("expected_accessibility_name"),
        f"{prefix}.expected_accessibility_name",
    )
    if (
        not expected_name.strip()
        or len(expected_name) > 128
        or any(ord(character) < 32 for character in expected_name)
    ):
        raise ConfigurationError(
            f"{prefix}.expected_accessibility_name must be a non-empty UI name"
        )
    return CstTargetConfig(
        id=target_id,
        mixer_index=mixer_index,
        expected_accessibility_name=expected_name,
    )


def _resource_id(value: Any, field: str) -> str:
    resource_id = _string(value, field)
    if len(resource_id) > 128 or not _ACTION_NAME.fullmatch(resource_id):
        raise ConfigurationError(
            f"{field} must be a lowercase dotted identifier",
            details={"field": field, "value": resource_id},
        )
    return resource_id


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"{field} must be a table")
    return value


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ConfigurationError(f"{field} must be a string")
    return value


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(f"{field} must be an integer")
    return int(value)


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(f"{field} must be a number")
    try:
        parsed = float(value)
    except OverflowError as exc:
        raise ConfigurationError(f"{field} must be finite") from exc
    if not math.isfinite(parsed):
        raise ConfigurationError(f"{field} must be finite")
    return parsed


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigurationError(f"{field} must be a boolean")
    return value


def _midi_value(value: Any, field: str) -> int:
    parsed = _integer(value, field)
    if not 0 <= parsed <= 127:
        raise ConfigurationError(f"{field} must be between 0 and 127")
    return parsed


def _reject_unknown(
    raw: Mapping[str, Any],
    allowed: set[str],
    field: str,
) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ConfigurationError(
            f"{field} contains unknown fields",
            details={"field": field, "unknown": unknown},
        )
