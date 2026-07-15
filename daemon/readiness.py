from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from daemon.config import BridgeConfig
from daemon.errors import BridgeError
from daemon.rpc_contract import TRACK_ID_PATTERN

ReadinessStatus = Literal["ready", "blocked", "unverifiable"]
READINESS_STATES: tuple[ReadinessStatus, ...] = (
    "ready",
    "blocked",
    "unverifiable",
)


class MidiReadinessLike(Protocol):
    def list_outputs(self) -> list[str]: ...


@dataclass(frozen=True, slots=True)
class _CstCapabilityState:
    configured_preset_ids: tuple[str, ...]
    configured_target_ids: tuple[str, ...]
    available_preset_ids: tuple[str, ...]
    accessibility_ready: bool
    inspect_available: bool
    load_available: bool
    reason: str | None


def midi_output_check(
    *,
    config: BridgeConfig,
    midi: MidiReadinessLike,
) -> dict[str, Any]:
    """Probe the exact configured MIDI output once and return safe readiness data."""
    try:
        outputs = midi.list_outputs()
    except BridgeError as exc:
        return {
            "ok": False,
            "blocking": True,
            "expected": config.midi.output_name,
            "available": [],
            "reason": exc.code,
        }
    midi_ok = config.midi.output_name in outputs
    return {
        "ok": midi_ok,
        "blocking": True,
        "expected": config.midi.output_name,
        "available": outputs,
        "reason": None if midi_ok else "configured_midi_output_not_found",
    }


def operator_readiness_snapshot(
    *,
    config: BridgeConfig,
    authentication_ready: bool,
    authentication_reason: str | None,
    midi_check: Mapping[str, Any],
    accessibility_ready: bool,
    accessibility_reason: str | None,
    cst_ready: bool,
    cst_reason: str | None,
) -> dict[str, Any]:
    """Build the canonical ordered ready/blocked/unverifiable policy snapshot."""
    midi_ready = midi_check.get("ok") is True
    midi_reason = _nonempty_reason(
        midi_check.get("reason"),
        fallback="configured_midi_output_not_found",
    )
    midi_action = _midi_next_action(config=config, midi_check=midi_check)

    if not midi_ready:
        controller_status: ReadinessStatus = "blocked"
        controller_reason = midi_reason
        controller_action = midi_action
    elif config.midi.assignments:
        controller_status = "unverifiable"
        controller_reason = "controller_assignments_unverifiable"
        controller_action = _next_action(
            code="verify_controller_assignments_in_logic",
            instruction=(
                "Open Logic Controller Assignments with Option-Command-K and "
                "manually verify every configured opaque action mapping."
            ),
        )
    else:
        controller_status = "blocked"
        controller_reason = "no_controller_assignments_configured"
        controller_action = _next_action(
            code="configure_controller_assignments_in_logic",
            instruction=(
                "Configure at least one reviewed opaque Controller Assignment in "
                "Logic, then verify its exact mapping."
            ),
        )

    scripter_configured = bool(
        config.scripter.enabled and config.scripter.operator_attested
    )
    if not midi_ready:
        scripter_status: ReadinessStatus = "blocked"
        scripter_reason = midi_reason
        scripter_action = midi_action
    elif scripter_configured:
        scripter_status = "unverifiable"
        scripter_reason = "scripter_route_unverifiable"
        scripter_action = _next_action(
            code="verify_scripter_route_in_logic",
            instruction=(
                "Manually verify the reviewed Scripter source, software-instrument "
                "placement, learned targets, and channel 16 routing in Logic."
            ),
        )
    else:
        scripter_status = "blocked"
        scripter_reason = "scripter_disabled_or_unattested"
        scripter_action = _next_action(
            code="install_and_attest_fixed_scripter_profile",
            instruction=(
                "Install the reviewed fixed script before Retro Synth, learn both "
                "targets, verify channel 16 routing, and set operator_attested."
            ),
        )

    items = [
        _readiness_item(
            item_id="configuration_integrity",
            status="ready",
            blocking=True,
            required_for="all_commands",
        ),
        _readiness_item(
            item_id="authentication",
            status="ready" if authentication_ready else "blocked",
            blocking=True,
            required_for="all_commands",
            reason=(
                None
                if authentication_ready
                else authentication_reason or "authentication_not_ready"
            ),
            next_action=_next_action(
                code="generate_and_export_strong_token",
                instruction=(
                    "Generate a fresh token with at least 32 UTF-8 bytes and restart "
                    "the daemon and MCP adapter."
                ),
            ),
        ),
        _readiness_item(
            item_id="midi_output",
            status="ready" if midi_ready else "blocked",
            blocking=True,
            required_for="midi_and_scripter_commands",
            reason=None if midi_ready else midi_reason,
            next_action=midi_action,
        ),
        _readiness_item(
            item_id="controller_assignments",
            status=controller_status,
            blocking=False,
            required_for="configured_controller_actions",
            reason=controller_reason,
            next_action=controller_action,
        ),
        _readiness_item(
            item_id="accessibility",
            status="ready" if accessibility_ready else "blocked",
            blocking=config.accessibility.enabled,
            required_for="mixer_inspection_and_cst_loading",
            reason=(
                None
                if accessibility_ready
                else accessibility_reason or "accessibility_permission_required"
            ),
            next_action=_next_action(
                code="grant_accessibility_to_launcher",
                instruction=(
                    "Grant Accessibility permission to the application launching "
                    "the bridge, then restart that launcher if macOS requires it."
                ),
            ),
        ),
        _readiness_item(
            item_id="automation_consent",
            status="unverifiable" if config.accessibility.enabled else "ready",
            blocking=False,
            required_for="cst_loading",
            reason=(
                "automation_consent_unverifiable"
                if config.accessibility.enabled
                else None
            ),
            next_action=_next_action(
                code="approve_first_logic_automation_prompt",
                instruction=(
                    "At the first confirmed UI action, approve the native macOS "
                    "Automation prompt for System Events and Logic Pro."
                ),
            ),
        ),
        _readiness_item(
            item_id="cst_resources",
            status="ready" if cst_ready else "blocked",
            blocking=False,
            required_for="logic.load_cst_preset",
            reason=(None if cst_ready else cst_reason or "cst_resources_unavailable"),
            next_action=_next_action(
                code="configure_and_probe_cst_allowlists",
                instruction=(
                    "Enable the reviewed Accessibility profile, correct the preset "
                    "hash/target allowlists, and pass read-only Mixer inspection."
                ),
            ),
        ),
        _readiness_item(
            item_id="scripter",
            status=scripter_status,
            blocking=False,
            required_for="logic.set_scripter_parameter",
            reason=scripter_reason,
            next_action=scripter_action,
        ),
    ]
    return {
        "contract_version": 1,
        "states": list(READINESS_STATES),
        "items": items,
    }


def capability_operator_readiness(
    *,
    config: BridgeConfig,
    midi_check: Mapping[str, Any],
    cst_status: Mapping[str, Any],
) -> dict[str, Any]:
    """Project live service probes into the canonical readiness snapshot."""
    cst = _cst_capability_state(config=config, cst_status=cst_status)
    accessibility_required = bool(
        config.permissions.require_accessibility_for_ui or config.accessibility.enabled
    )
    accessibility_ready = cst.accessibility_ready if accessibility_required else True
    return operator_readiness_snapshot(
        config=config,
        authentication_ready=True,
        authentication_reason=None,
        midi_check=midi_check,
        accessibility_ready=accessibility_ready,
        accessibility_reason=None if accessibility_ready else cst.reason,
        cst_ready=cst.load_available,
        cst_reason=cst.reason,
    )


def capability_method_availability(
    *,
    method: str,
    config: BridgeConfig,
    midi_check: Mapping[str, Any],
    cst_status: Mapping[str, Any],
    operator_readiness: Mapping[str, Any],
    scripter_source_sha256: str,
) -> dict[str, Any]:
    """Derive one method's availability from the canonical readiness snapshot."""
    prerequisites: dict[str, Any] = {"authentication": "required"}
    readiness = _readiness_items_by_id(operator_readiness)
    midi_item = readiness["midi_output"]

    if method == "logic.load_cst_preset":
        cst = _cst_capability_state(config=config, cst_status=cst_status)
        prerequisites.update(
            {
                "target_bound_confirmation_phrase": "required",
                "accessibility_trust": "required",
                "preset_integrity": "required",
                "fixed_automation_script": "required",
                "automation_consent": "required_unknown_until_dispatch",
                "standalone_mixer_window": "required",
                "configured_preset_ids": list(cst.configured_preset_ids),
                "configured_target_ids": list(cst.configured_target_ids),
                "available_preset_ids": list(cst.available_preset_ids),
            }
        )
        if cst.load_available:
            return _availability(
                status="ready",
                reason=None,
                next_action=None,
                prerequisites=prerequisites,
            )
        return _availability(
            status="blocked",
            reason=cst.reason or "cst_resources_unavailable",
            next_action=readiness["cst_resources"]["next_action"],
            prerequisites=prerequisites,
        )

    if method == "logic.inspect_mixer_target":
        cst = _cst_capability_state(config=config, cst_status=cst_status)
        prerequisites.update(
            {
                "accessibility_trust": "required",
                "fixed_inspector_script": "required",
                "standalone_mixer_window": "required",
                "configured_target_ids": list(cst.configured_target_ids),
            }
        )
        if cst.inspect_available:
            return _availability(
                status="ready",
                reason=None,
                next_action=None,
                prerequisites=prerequisites,
            )
        return _availability(
            status="blocked",
            reason=cst.reason or "mixer_inspection_unavailable",
            next_action=readiness["cst_resources"]["next_action"],
            prerequisites=prerequisites,
        )

    if method == "logic.set_scripter_parameter":
        prerequisites.update(
            {
                "midi_output": config.midi.output_name,
                "fixed_source_sha256": scripter_source_sha256,
                "operator_attestation": "required",
                "software_instrument_only": True,
                "configured_parameter_ids": sorted(config.scripter.learned_targets),
            }
        )
        return _availability_from_item(
            readiness["scripter"],
            prerequisites=prerequisites,
        )

    if method == "logic.invoke_action":
        assignment_ids = sorted(config.midi.assignments)
        prerequisites.update(
            {
                "midi_output": config.midi.output_name,
                "configured_action_ids": assignment_ids,
                "caller_supplied_routing": "forbidden",
            }
        )
        if midi_item["status"] == "blocked":
            return _availability_from_item(
                midi_item,
                prerequisites=prerequisites,
            )
        if not assignment_ids:
            return _missing_controller_assignment_availability(
                prerequisites=prerequisites
            )
        return _availability_from_item(
            readiness["controller_assignments"],
            prerequisites=prerequisites,
        )

    action_contract = {
        "logic.set_volume": ("volume", "normalized"),
        "logic.toggle_mute": ("mute_toggle", "trigger"),
    }.get(method)
    if action_contract is not None:
        action_suffix, required_value_mode = action_contract
        assignment_ids = sorted(
            name
            for name, assignment in config.midi.assignments.items()
            if _matches_track_action(name, action_suffix)
            and assignment.value_mode == required_value_mode
        )
        prerequisites.update(
            {
                "midi_output": config.midi.output_name,
                "required_value_mode": required_value_mode,
                "controller_assignment_ids": assignment_ids,
            }
        )
        if midi_item["status"] == "blocked":
            return _availability_from_item(
                midi_item,
                prerequisites=prerequisites,
            )
        if not assignment_ids:
            return _missing_controller_assignment_availability(
                prerequisites=prerequisites
            )
        return _availability_from_item(
            readiness["controller_assignments"],
            prerequisites=prerequisites,
        )

    if method == "midi.list_outputs":
        prerequisites["midi_backend"] = "required"
        reason = midi_check.get("reason")
        if reason not in (None, "configured_midi_output_not_found"):
            return _availability_from_item(
                midi_item,
                prerequisites=prerequisites,
            )
        return _availability(
            status="ready",
            reason=None,
            next_action=None,
            prerequisites=prerequisites,
        )

    if method in {"bridge.health", "bridge.capabilities"}:
        return _availability(
            status="ready",
            reason=None,
            next_action=None,
            prerequisites=prerequisites,
        )
    raise RuntimeError(f"Public method has no readiness policy: {method}")


def _readiness_item(
    *,
    item_id: str,
    status: ReadinessStatus,
    blocking: bool,
    required_for: str,
    reason: str | None = None,
    next_action: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if status == "ready":
        reason = None
        next_action = None
    else:
        assert reason is not None and next_action is not None
    return {
        "id": item_id,
        "status": status,
        "blocking": blocking,
        "required_for": required_for,
        "reason": reason,
        "next_action": None if next_action is None else dict(next_action),
    }


def _availability_from_item(
    item: Mapping[str, Any],
    *,
    prerequisites: Mapping[str, Any],
) -> dict[str, Any]:
    status = item["status"]
    assert status in READINESS_STATES
    return _availability(
        status=status,
        reason=item.get("reason"),
        next_action=item.get("next_action"),
        prerequisites=prerequisites,
    )


def _availability(
    *,
    status: ReadinessStatus,
    reason: object,
    next_action: object,
    prerequisites: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "available": status != "blocked",
        "status": status,
        "reason": reason,
        "next_action": next_action,
        "prerequisites": dict(prerequisites),
    }


def _missing_controller_assignment_availability(
    *,
    prerequisites: Mapping[str, Any],
) -> dict[str, Any]:
    return _availability(
        status="blocked",
        reason="controller_assignment_not_configured",
        next_action=_next_action(
            code="configure_controller_assignments_in_logic",
            instruction=(
                "Configure the required reviewed Controller Assignment in Logic, "
                "then verify its exact opaque action mapping."
            ),
        ),
        prerequisites=prerequisites,
    )


def _midi_next_action(
    *,
    config: BridgeConfig,
    midi_check: Mapping[str, Any],
) -> dict[str, str]:
    if midi_check.get("reason") == "configured_midi_output_not_found":
        return _next_action(
            code="enable_exact_iac_output",
            instruction=(
                f"Enable the exact MIDI output {config.midi.output_name!r} or "
                "correct the configured output name."
            ),
        )
    return _next_action(
        code="repair_midi_backend",
        instruction=(
            "Restore the local MIDI backend, then rerun readiness before "
            "dispatching any MIDI-backed command."
        ),
    )


def _next_action(*, code: str, instruction: str) -> dict[str, str]:
    return {"code": code, "instruction": instruction}


def _nonempty_reason(value: object, *, fallback: str) -> str:
    return value if isinstance(value, str) and value else fallback


def _readiness_items_by_id(
    snapshot: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    raw_items = snapshot.get("items")
    if not isinstance(raw_items, list):  # pragma: no cover - internal invariant
        raise RuntimeError("Readiness snapshot items are invalid")
    items: dict[str, Mapping[str, Any]] = {}
    for item in raw_items:
        if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
            raise RuntimeError("Readiness snapshot item is invalid")
        items[item["id"]] = item
    return items


def _cst_capability_state(
    *,
    config: BridgeConfig,
    cst_status: Mapping[str, Any],
) -> _CstCapabilityState:
    preset_ids = tuple(sorted(config.accessibility.presets))
    target_ids = tuple(sorted(config.accessibility.targets))
    raw_available_ids = cst_status.get("available_preset_ids")
    if isinstance(raw_available_ids, list):
        available_ids = tuple(
            sorted(item for item in raw_available_ids if isinstance(item, str))
        )
    else:
        available_ids = preset_ids

    enabled = config.accessibility.enabled
    supported = cst_status.get("supported") is True
    trusted = cst_status.get("trusted") is True
    script_ready = cst_status.get("script_ready") is True
    accessibility_ready = supported and trusted
    inspect_available = bool(
        enabled and target_ids and accessibility_ready and script_ready
    )
    load_available = bool(inspect_available and available_ids)

    reason: str | None = None
    if not enabled:
        reason = "cst_loading_disabled"
    elif not supported or not trusted:
        reason = _nonempty_reason(
            cst_status.get("reason"),
            fallback="accessibility_permission_required",
        )
    elif not script_ready:
        reason = _nonempty_reason(
            cst_status.get("reason"),
            fallback="accessibility_script_unavailable",
        )
    elif not target_ids:
        reason = "no_cst_targets_configured"
    elif not available_ids:
        reason = _nonempty_reason(
            cst_status.get("reason"),
            fallback="cst_resources_unavailable",
        )

    return _CstCapabilityState(
        configured_preset_ids=preset_ids,
        configured_target_ids=target_ids,
        available_preset_ids=available_ids,
        accessibility_ready=accessibility_ready,
        inspect_available=inspect_available,
        load_available=load_available,
        reason=reason,
    )


def _matches_track_action(name: str, action_suffix: str) -> bool:
    parts = name.split(".")
    if len(parts) != 3 or parts[0] != "track" or parts[2] != action_suffix:
        return False
    track = parts[1]
    return track == "selected" or TRACK_ID_PATTERN.fullmatch(track) is not None
