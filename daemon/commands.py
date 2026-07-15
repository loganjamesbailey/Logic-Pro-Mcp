from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from daemon.accessibility import CstLoadObservation, UnavailableCstPresetLoader
from daemon.config import (
    AssignmentConfig,
    BridgeConfig,
    CstPresetConfig,
    CstTargetConfig,
)
from daemon.errors import (
    BridgeError,
    ConfirmationRequiredError,
    CstLoadingDisabledError,
    InvalidParametersError,
    PresetNotAllowlistedError,
    ScripterUnavailableError,
    TargetNotAllowlistedError,
    UnknownActionError,
)
from daemon.readiness import (
    capability_method_availability,
    capability_operator_readiness,
    midi_output_check,
)
from daemon.rpc_contract import (
    CONTRACT_VERSION,
    METHOD_SPECS,
    TRACK_ID_MAX,
    TRACK_ID_PATTERN,
    capability_method_specs,
    request_schema,
)
from daemon.scripter import (
    SCRIPT_SHA256,
    fixed_parameter_metadata,
    packaged_script_sha256,
    resolve_scripter_dispatch,
)
from daemon.ui_observation import MixerObservation, evaluate_plugin_signature


class MidiTransportLike(Protocol):
    def list_outputs(self) -> list[str]: ...

    def send_cc(
        self,
        *,
        channel: int,
        control: int,
        value: int,
        release_value: int | None = None,
    ) -> int: ...

    def send_note(
        self,
        *,
        channel: int,
        note: int,
        velocity: int,
        release_velocity: int,
    ) -> int: ...

    def send_sysex(self, *, data: tuple[int, ...]) -> int: ...

    def close(self) -> None: ...


class CstPresetLoaderLike(Protocol):
    def status(self) -> dict[str, Any]: ...

    def load_preset(
        self,
        *,
        preset: CstPresetConfig,
        target: CstTargetConfig,
    ) -> CstLoadObservation: ...

    def inspect_target(self, *, target: CstTargetConfig) -> MixerObservation: ...

    def close(self) -> None: ...


class LogicCommandService:
    """Allowlisted Logic actions with honest, event-based lifecycle reporting."""

    def __init__(
        self,
        *,
        config: BridgeConfig,
        midi: MidiTransportLike,
        cst_loader: CstPresetLoaderLike | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self.midi = midi
        self.cst_loader = cst_loader or UnavailableCstPresetLoader()
        self._clock = clock or (lambda: datetime.now(UTC))
        try:
            # Report the hash actually computed from the packaged file, not
            # just the reviewed constant, so a repackaged or patched
            # distribution is visible to an operator comparing this value
            # against the reviewed source instead of silently trusted.
            self._packaged_scripter_sha256 = packaged_script_sha256()
        except OSError:
            self._packaged_scripter_sha256 = SCRIPT_SHA256

    def health(self) -> dict[str, Any]:
        midi_check = midi_output_check(config=self.config, midi=self.midi)
        observed_at = self._clock().isoformat()
        return {
            "status": "running",
            "readiness": {
                "status": "ready" if midi_check["ok"] else "not_ready",
                "source": "midi_output_probe",
                "observed_at": observed_at,
                "checks": {"midi_output": midi_check},
            },
            "midi_output": self.config.midi.output_name,
            "midi_connection": "available" if midi_check["ok"] else "unavailable",
            "logic_state": {
                "value": "unknown",
                "source": "unsupported_logic_readback",
                "observed_at": None,
                "reported_at": observed_at,
                "stale": True,
                "verification_status": "unverified",
                "reason": "logic_pro_has_no_supported_state_readback_api",
            },
        }

    def capabilities(self) -> dict[str, Any]:
        methods = capability_method_specs()
        midi_check = midi_output_check(config=self.config, midi=self.midi)
        cst_status = self.cst_loader.status()
        operator_readiness = capability_operator_readiness(
            config=self.config,
            midi_check=midi_check,
            cst_status=cst_status,
        )
        for method, descriptor in methods.items():
            descriptor["availability"] = capability_method_availability(
                method=method,
                config=self.config,
                midi_check=midi_check,
                cst_status=cst_status,
                operator_readiness=operator_readiness,
                scripter_source_sha256=self._packaged_scripter_sha256,
            )
        configured_preset_ids = set(self.config.accessibility.presets)
        raw_available_preset_ids = cst_status.get("available_preset_ids")
        available_preset_ids = (
            {item for item in raw_available_preset_ids if isinstance(item, str)}
            if isinstance(raw_available_preset_ids, list)
            else configured_preset_ids
        )
        raw_unavailable_preset_ids = cst_status.get("unavailable_preset_ids")
        unavailable_reasons = {
            item["id"]: item["reason"]
            for item in (
                raw_unavailable_preset_ids
                if isinstance(raw_unavailable_preset_ids, list)
                else []
            )
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and isinstance(item.get("reason"), str)
        }
        return {
            "contract": {
                "version": CONTRACT_VERSION,
                "methods": methods,
                "request_schema": request_schema(),
            },
            "rpc_methods": list(METHOD_SPECS),
            "operator_readiness": operator_readiness,
            "assignments": [
                assignment.public_metadata()
                for assignment in sorted(
                    self.config.midi.assignments.values(),
                    key=lambda item: item.name,
                )
            ],
            "scripter": {
                **self.config.scripter.public_metadata(),
                "source_sha256": self._packaged_scripter_sha256,
                "parameters": fixed_parameter_metadata(),
            },
            "readback": {
                "supported": False,
                "unverified_dispatch_state": "unknown",
                "source": "unsupported_logic_readback",
                "observed_at": None,
                "stale": True,
                "verification_status": "unverified",
            },
            "resources": [
                *(
                    {
                        **preset.public_metadata(),
                        "availability": {
                            "available": preset.id in available_preset_ids,
                            "reason": unavailable_reasons.get(preset.id),
                        },
                    }
                    for preset in sorted(
                        self.config.accessibility.presets.values(),
                        key=lambda item: item.id,
                    )
                ),
                *(
                    {
                        **target.public_metadata(),
                        "availability": {
                            "available": self.config.accessibility.enabled,
                            "reason": (
                                None
                                if self.config.accessibility.enabled
                                else "cst_loading_disabled"
                            ),
                        },
                    }
                    for target in sorted(
                        self.config.accessibility.targets.values(),
                        key=lambda item: item.id,
                    )
                ),
            ],
        }

    def list_outputs(self) -> dict[str, Any]:
        return {
            "configured": self.config.midi.output_name,
            "available": self.midi.list_outputs(),
        }

    def set_volume(self, *, track_id: str | int, value: object) -> dict[str, Any]:
        requested = self._event("requested", operation="logic.set_volume")
        try:
            track = _track_identifier(track_id)
        except BridgeError as exc:
            raise self._with_requested(exc, requested) from exc
        action = f"track.{track}.volume"
        requested["action"] = action
        return self._execute(action=action, value=value, requested=requested)

    def toggle_mute(self, *, track_id: str | int) -> dict[str, Any]:
        requested = self._event("requested", operation="logic.toggle_mute")
        try:
            track = _track_identifier(track_id)
        except BridgeError as exc:
            raise self._with_requested(exc, requested) from exc
        action = f"track.{track}.mute_toggle"
        requested["action"] = action
        return self._execute(action=action, value=None, requested=requested)

    def invoke_action(
        self,
        *,
        action_id: str,
        value: object = None,
    ) -> dict[str, Any]:
        requested = self._event("requested", operation="logic.invoke_action")
        if isinstance(action_id, str) and action_id:
            requested["action"] = action_id
        return self._execute(
            action=action_id,
            value=value,
            requested=requested,
        )

    def set_scripter_parameter(
        self,
        *,
        parameter_id: str,
        value: object,
    ) -> dict[str, Any]:
        requested = self._event(
            "requested",
            operation="logic.set_scripter_parameter",
        )
        if isinstance(parameter_id, str) and parameter_id:
            requested["action"] = parameter_id
        try:
            if not (
                self.config.scripter.enabled and self.config.scripter.operator_attested
            ):
                raise ScripterUnavailableError(
                    "The fixed Scripter route is not enabled and attested"
                )
            dispatch = resolve_scripter_dispatch(parameter_id, value)
            if parameter_id not in self.config.scripter.learned_targets:
                raise UnknownActionError(
                    "Scripter parameter is not allowlisted",
                    details={"parameter_id": parameter_id},
                )
        except BridgeError as exc:
            raise self._with_requested(exc, requested, action=parameter_id) from exc

        try:
            message_count = self.midi.send_cc(
                channel=dispatch.channel,
                control=dispatch.control,
                value=dispatch.value,
            )
        except BridgeError as exc:
            events = [
                requested,
                self._event(
                    "unknown",
                    action=parameter_id,
                    reason="scripter_midi_dispatch_failed_before_state_readback",
                ),
            ]
            raise exc.with_details(action=parameter_id, events=events) from exc
        return {
            "action": parameter_id,
            "events": [
                requested,
                self._event(
                    "dispatched",
                    action=parameter_id,
                    transport="midi_scripter",
                    message_count=message_count,
                ),
                self._event(
                    "unknown",
                    action=parameter_id,
                    reason="scripter_target_has_no_verified_logic_readback",
                ),
            ],
        }

    def inspect_mixer_target(self, *, target_id: str) -> dict[str, Any]:
        if not self.config.accessibility.enabled:
            raise CstLoadingDisabledError("Mixer target inspection is disabled")
        target = self.config.accessibility.targets.get(target_id)
        if target is None:
            raise TargetNotAllowlistedError(
                "The requested Mixer target is not allowlisted"
            )
        observation = self.cst_loader.inspect_target(target=target)
        return observation.public_evidence()

    def load_cst_preset(
        self,
        *,
        preset_id: str,
        target_id: str,
        confirmation: str,
    ) -> dict[str, Any]:
        action = "channel_strip.preset.load"
        requested = self._event(
            "requested",
            operation="logic.load_cst_preset",
            action=action,
            preset_id=preset_id,
            target_id=target_id,
        )
        try:
            expected_confirmation = (
                f"load {preset_id} into {target_id} in disposable project"
            )
            if confirmation != expected_confirmation:
                raise ConfirmationRequiredError(
                    "The exact target-bound disposable-project confirmation is required"
                )
            if not self.config.accessibility.enabled:
                raise CstLoadingDisabledError(
                    "Channel-strip preset loading is disabled"
                )
            preset = self.config.accessibility.presets.get(preset_id)
            if preset is None:
                raise PresetNotAllowlistedError(
                    "The requested channel-strip preset is not allowlisted"
                )
            target = self.config.accessibility.targets.get(target_id)
            if target is None:
                raise TargetNotAllowlistedError(
                    "The requested Mixer target is not allowlisted"
                )
            observation = self.cst_loader.load_preset(
                preset=preset,
                target=target,
            )
        except BridgeError as exc:
            events = [requested]
            if exc.details.get("dispatch_started") is True:
                events.extend(
                    [
                        self._event(
                            "dispatched",
                            action=action,
                            transport="accessibility",
                        ),
                        self._event(
                            "unknown",
                            action=action,
                            reason=(
                                "ui_automation_failed_after_dispatch_and_logic_state_"
                                "cannot_be_verified"
                            ),
                        ),
                    ]
                )
            raise exc.with_details(
                preset_id=preset_id,
                target_id=target_id,
                events=events,
            ) from exc

        dispatched = self._event(
            "dispatched",
            action=action,
            transport="accessibility",
        )
        observed = self._event(
            "observed",
            action=action,
            evidence=observation.evidence,
        )
        events = [requested, dispatched, observed]
        inspection = observation.inspection
        if (
            inspection is not None
            and preset.expected_plugin_signature
            and evaluate_plugin_signature(
                inspection,
                expected=preset.expected_plugin_signature,
                match_mode=preset.signature_match_mode,
            )
        ):
            events.append(
                self._event(
                    "verified",
                    action=action,
                    scope="mixer_plugin_signature",
                    proof_source=inspection.source,
                    match_mode=preset.signature_match_mode,
                    target_id=target_id,
                )
            )
        else:
            if inspection is None:
                reason = (
                    "post_load_mixer_inspection_unavailable"
                    if observation.inspection_reason is None
                    else "post_load_mixer_inspection_failed"
                )
            elif not inspection.complete:
                reason = "mixer_plugin_signature_incomplete"
            elif not preset.expected_plugin_signature:
                reason = "configured_plugin_signature_not_available"
            else:
                reason = "mixer_plugin_signature_mismatch"
            events.append(
                self._event(
                    "unknown",
                    action=action,
                    reason=reason,
                )
            )
        result: dict[str, Any] = {
            "action": action,
            "preset_id": preset_id,
            "target_id": target_id,
            "events": events,
        }
        if inspection is not None:
            result["post_load_observation"] = inspection.public_evidence()
        return result

    def _execute(
        self,
        *,
        action: str,
        value: object,
        requested: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            if not isinstance(action, str) or not action:
                raise InvalidParametersError("action must be a non-empty string")
            assignment = self.config.midi.assignments.get(action)
            if assignment is None:
                raise UnknownActionError(
                    f"Action is not allowlisted: {action}",
                    details={"action": action},
                )
            resolved_value = _resolve_value(assignment, value)
        except BridgeError as exc:
            raise self._with_requested(exc, requested, action=action) from exc

        try:
            message_count = self._dispatch_assignment(
                assignment=assignment,
                resolved_value=resolved_value,
            )
        except BridgeError as exc:
            successful_sends = _successful_send_count(exc)
            events = [requested]
            if successful_sends:
                events.append(
                    self._event(
                        "dispatched",
                        action=action,
                        transport="midi",
                        message_count=successful_sends,
                        partial=True,
                    )
                )
            unknown = self._event(
                "unknown",
                action=action,
                reason=(
                    "midi_dispatch_partially_failed_before_logic_state_could_be_verified"
                    if successful_sends
                    else "midi_dispatch_failed_before_logic_state_could_be_observed"
                ),
            )
            events.append(unknown)
            raise exc.with_details(action=action, events=events) from exc

        dispatched = self._event(
            "dispatched",
            action=action,
            transport="midi",
            message_count=message_count,
        )
        unknown = self._event(
            "unknown",
            action=action,
            reason="midi_assignment_has_no_verified_logic_readback",
        )
        return {
            "action": action,
            "events": [requested, dispatched, unknown],
        }

    def _dispatch_assignment(
        self,
        *,
        assignment: AssignmentConfig,
        resolved_value: int | None,
    ) -> int:
        if assignment.kind == "control_change":
            value = (
                assignment.value
                if assignment.value_mode == "trigger"
                else resolved_value
            )
            if value is None:
                raise RuntimeError("validated control-change assignment has no value")
            return self.midi.send_cc(
                channel=assignment.channel,
                control=assignment.control,
                value=value,
                release_value=assignment.release_value,
            )
        if assignment.kind == "note":
            return self.midi.send_note(
                channel=assignment.channel,
                note=assignment.note,
                velocity=assignment.velocity,
                release_velocity=assignment.release_velocity,
            )
        if assignment.kind == "sysex":
            data = assignment.data
            if assignment.value_index is not None:
                if resolved_value is None:
                    raise RuntimeError("validated parameterized SysEx has no value")
                mutable_data = list(data)
                mutable_data[assignment.value_index] = resolved_value
                data = tuple(mutable_data)
            return self.midi.send_sysex(data=data)
        raise RuntimeError("validated MIDI assignment has an unsupported kind")

    def close(self) -> None:
        try:
            self.midi.close()
        finally:
            self.cst_loader.close()

    def _event(self, state: str, **metadata: Any) -> dict[str, Any]:
        return {
            "state": state,
            "at": self._clock().isoformat(),
            **metadata,
        }

    def _with_requested(
        self,
        error: BridgeError,
        requested: dict[str, Any],
        *,
        action: object | None = None,
    ) -> BridgeError:
        if "events" in error.details:
            return error
        details: dict[str, Any] = {"events": [requested]}
        if isinstance(action, str) and action:
            details["action"] = action
        return error.with_details(**details)


def _resolve_value(assignment: AssignmentConfig, value: object) -> int | None:
    if assignment.value_mode == "normalized":
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0.0 <= value <= 1.0
        ):
            raise InvalidParametersError("value must be a number between 0.0 and 1.0")
        return round(float(value) * 127)
    if assignment.value_mode == "absolute":
        # The published schema advertises `value` as any number 0-127 for
        # every value mode (it cannot vary by assignment, since a caller may
        # not know an action's configured mode in advance); accept integral
        # floats here too so a schema-valid request like 64.0 is not rejected.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InvalidParametersError("value must be an integer between 0 and 127")
        if isinstance(value, float):
            if not value.is_integer():
                raise InvalidParametersError(
                    "value must be an integer between 0 and 127"
                )
            value = int(value)
        if not 0 <= value <= 127:
            raise InvalidParametersError("value must be an integer between 0 and 127")
        return value
    if value is not None:
        raise InvalidParametersError("trigger assignments do not accept a value")
    return None


def _successful_send_count(error: BridgeError) -> int:
    value = error.details.get("successful_send_count", 0)
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    if not 0 <= value <= 2:
        return 0
    return int(value)


def _track_identifier(track_id: str | int) -> str:
    if isinstance(track_id, bool):
        raise InvalidParametersError("track_id must be selected or a positive integer")
    if isinstance(track_id, int):
        if not 1 <= track_id <= TRACK_ID_MAX:
            raise InvalidParametersError(
                "track_id must be selected or a positive integer with at most 20 digits"
            )
        return str(track_id)
    if track_id == "selected":
        return track_id
    if isinstance(track_id, str) and TRACK_ID_PATTERN.fullmatch(track_id):
        return track_id
    raise InvalidParametersError(
        "track_id must be selected or a positive integer with at most 20 digits"
    )
