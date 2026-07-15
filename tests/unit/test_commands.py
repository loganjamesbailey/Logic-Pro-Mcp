from __future__ import annotations

from datetime import UTC, datetime

import pytest

import daemon.commands as commands
from daemon.commands import LogicCommandService
from daemon.config import BridgeConfig
from daemon.errors import (
    AccessibilityPermissionRequiredError,
    ConfirmationRequiredError,
    InvalidParametersError,
    MidiDispatchError,
    PresetLoadTimeoutError,
    PresetNotAllowlistedError,
    ScripterUnavailableError,
    UnknownActionError,
)
from daemon.rpc_contract import CONTRACT_VERSION, METHOD_SPECS, request_schema
from daemon.scripter import SCRIPT_SHA256
from daemon.ui_observation import MixerObservation
from tests.fakes import (
    RecordingCstPresetLoader,
    RecordingMidiTransport,
    valid_mapping,
)

_CST_CONFIRMATION = "load preset.lead-vocal into track.lead-vocal in disposable project"


def _cst_mapping() -> dict[str, object]:
    raw = valid_mapping()
    raw["accessibility"] = {
        "enabled": True,
        "preset_root": "/Users/test/Music/Channel Strip Settings/Track",
        "presets": [
            {
                "id": "preset.lead-vocal",
                "filename": "Lead Vocal.cst",
                "sha256": "a" * 64,
            }
        ],
        "targets": [
            {
                "id": "track.lead-vocal",
                "mixer_index": 0,
                "expected_accessibility_name": "Lead Vocal",
            }
        ],
    }
    return raw


def _scripter_mapping() -> dict[str, object]:
    raw = valid_mapping()
    raw["scripter"] = {
        "enabled": True,
        "protocol_version": 1,
        "channel": 15,
        "source_sha256": SCRIPT_SHA256,
        "placement": "software_instrument_midi_fx",
        "operator_attested": True,
        "learned_targets": [
            {
                "parameter_id": "scripter.retro-synth.filter-cutoff",
                "control": 102,
                "target_slot": 1,
                "target_name": "Retro Synth Filter Cutoff",
            },
            {
                "parameter_id": "scripter.retro-synth.filter-resonance",
                "control": 103,
                "target_slot": 2,
                "target_name": "Filter Resonance",
            },
        ],
    }
    return raw


def make_service() -> tuple[LogicCommandService, RecordingMidiTransport]:
    transport = RecordingMidiTransport()
    service = LogicCommandService(
        config=BridgeConfig.from_mapping(valid_mapping()),
        midi=transport,
    )
    return service, transport


def _all_midi_kinds_mapping() -> dict[str, object]:
    raw = valid_mapping()
    midi = raw["midi"]
    assert isinstance(midi, dict)
    assignments = midi["assignments"]
    assert isinstance(assignments, list)
    assignments.extend(
        [
            {
                "name": "mixer.selected.pan",
                "kind": "control_change",
                "channel": 1,
                "control": 10,
                "value_mode": "absolute",
            },
            {
                "name": "transport.marker.trigger",
                "kind": "note",
                "channel": 2,
                "note": 60,
                "velocity": 100,
                "release_velocity": 12,
                "value_mode": "trigger",
            },
            {
                "name": "device.fixed.command",
                "kind": "sysex",
                "data": [0, 32, 51, 1],
                "value_mode": "trigger",
            },
            {
                "name": "device.normalized.parameter",
                "kind": "sysex",
                "data": [0, 32, 51, 0, 9],
                "value_index": 3,
                "value_mode": "normalized",
            },
            {
                "name": "device.absolute.parameter",
                "kind": "sysex",
                "data": [0, 0, 8],
                "value_index": 1,
                "value_mode": "absolute",
            },
        ]
    )
    return raw


def test_set_volume_converts_normalized_value_and_reports_unknown_readback() -> None:
    service, transport = make_service()

    result = service.set_volume(track_id="selected", value=0.5)

    assert transport.sent == [(0, 7, 64)]
    assert [event["state"] for event in result["events"]] == [
        "requested",
        "dispatched",
        "unknown",
    ]
    assert (
        result["events"][-1]["reason"]
        == "midi_assignment_has_no_verified_logic_readback"
    )


def test_toggle_mute_sends_press_and_release_values() -> None:
    service, transport = make_service()

    service.toggle_mute(track_id="selected")

    assert transport.sent == [(0, 20, 127), (0, 20, 0)]


def test_invoke_action_rejects_unknown_assignment() -> None:
    service, _ = make_service()

    with pytest.raises(UnknownActionError, match="not allowlisted") as caught:
        service.invoke_action(action_id="track.99.volume", value=0.5)

    assert [event["state"] for event in caught.value.details["events"]] == ["requested"]


def test_typed_mutation_reports_requested_when_assignment_is_missing() -> None:
    raw = valid_mapping()
    midi = raw["midi"]
    assert isinstance(midi, dict)
    assignments = midi["assignments"]
    assert isinstance(assignments, list)
    midi["assignments"] = [
        assignment
        for assignment in assignments
        if isinstance(assignment, dict)
        if assignment["name"] != "track.selected.volume"
    ]
    service = LogicCommandService(
        config=BridgeConfig.from_mapping(raw),
        midi=RecordingMidiTransport(),
    )

    with pytest.raises(UnknownActionError, match="not allowlisted") as caught:
        service.set_volume(track_id="selected", value=0.5)

    assert [event["state"] for event in caught.value.details["events"]] == ["requested"]


@pytest.mark.parametrize("value", [-0.01, 1.01, True, "loud"])
def test_set_volume_rejects_invalid_normalized_values(value: object) -> None:
    service, _ = make_service()

    with pytest.raises(
        InvalidParametersError,
        match="number between 0.0 and 1.0",
    ) as caught:
        service.set_volume(track_id="selected", value=value)  # type: ignore[arg-type]

    assert [event["state"] for event in caught.value.details["events"]] == ["requested"]


def test_invalid_track_id_reports_requested_without_dispatch() -> None:
    service, transport = make_service()

    with pytest.raises(InvalidParametersError, match="track_id") as caught:
        service.set_volume(track_id=0, value=0.5)

    assert [event["state"] for event in caught.value.details["events"]] == ["requested"]
    assert transport.sent == []


def test_partial_trigger_failure_reports_successful_dispatch_count() -> None:
    class FailSecondSend(RecordingMidiTransport):
        def send_cc(
            self,
            *,
            channel: int,
            control: int,
            value: int,
            release_value: int | None = None,
        ) -> int:
            self.sent.append((channel, control, value))
            raise MidiDispatchError(
                "second send failed",
                details={"successful_send_count": 1, "message_count": 2},
            )

    transport = FailSecondSend()
    service = LogicCommandService(
        config=BridgeConfig.from_mapping(valid_mapping()),
        midi=transport,
    )

    with pytest.raises(MidiDispatchError) as caught:
        service.toggle_mute(track_id="selected")

    assert caught.value.retryable is False

    events = caught.value.details["events"]
    assert [event["state"] for event in events] == [
        "requested",
        "dispatched",
        "unknown",
    ]
    assert events[1]["message_count"] == 1
    assert events[1]["partial"] is True
    assert transport.sent == [(0, 20, 127)]


@pytest.mark.parametrize(
    ("action_id", "value", "expected_cc"),
    [
        ("track.selected.volume", 0.5, (0, 7, 64, None)),
        ("mixer.selected.pan", 65, (1, 10, 65, None)),
        ("track.selected.mute_toggle", None, (0, 20, 127, 0)),
    ],
)
def test_invoke_action_dispatches_configured_control_change_modes(
    action_id: str,
    value: object,
    expected_cc: tuple[int, int, int, int | None],
) -> None:
    transport = RecordingMidiTransport()
    service = LogicCommandService(
        config=BridgeConfig.from_mapping(_all_midi_kinds_mapping()),
        midi=transport,
    )

    result = service.invoke_action(action_id=action_id, value=value)

    assert transport.cc_calls == [expected_cc]
    assert [event["state"] for event in result["events"]] == [
        "requested",
        "dispatched",
        "unknown",
    ]
    assert result["events"][1]["message_count"] == (
        2 if expected_cc[-1] is not None else 1
    )


def test_invoke_action_dispatches_note_and_fixed_or_parameterized_sysex() -> None:
    transport = RecordingMidiTransport()
    service = LogicCommandService(
        config=BridgeConfig.from_mapping(_all_midi_kinds_mapping()),
        midi=transport,
    )

    note = service.invoke_action(action_id="transport.marker.trigger")
    fixed = service.invoke_action(action_id="device.fixed.command")
    normalized = service.invoke_action(
        action_id="device.normalized.parameter",
        value=0.5,
    )
    absolute = service.invoke_action(
        action_id="device.absolute.parameter",
        value=127,
    )

    assert transport.note_calls == [(2, 60, 100, 12)]
    assert transport.sysex_calls == [
        (0, 32, 51, 1),
        (0, 32, 51, 64, 9),
        (0, 127, 8),
    ]
    for result in (note, fixed, normalized, absolute):
        assert [event["state"] for event in result["events"]] == [
            "requested",
            "dispatched",
            "unknown",
        ]


@pytest.mark.parametrize(
    ("action_id", "value", "message"),
    [
        ("track.selected.volume", 64, "number between 0.0 and 1.0"),
        ("mixer.selected.pan", 1.5, "integer between 0 and 127"),
        ("transport.marker.trigger", 1, "trigger assignments do not accept"),
        ("device.fixed.command", 1, "trigger assignments do not accept"),
    ],
)
def test_invoke_action_enforces_the_configured_value_mode(
    action_id: str,
    value: object,
    message: str,
) -> None:
    transport = RecordingMidiTransport()
    service = LogicCommandService(
        config=BridgeConfig.from_mapping(_all_midi_kinds_mapping()),
        midi=transport,
    )

    with pytest.raises(InvalidParametersError, match=message) as caught:
        service.invoke_action(action_id=action_id, value=value)

    assert [event["state"] for event in caught.value.details["events"]] == ["requested"]
    assert transport.cc_calls == []
    assert transport.note_calls == []
    assert transport.sysex_calls == []


def test_invoke_action_accepts_an_integral_float_in_absolute_mode() -> None:
    # The published schema advertises `value` as any number 0-127 regardless
    # of an action's configured mode, so a schema-valid 64.0 must not be
    # rejected just because absolute mode ultimately dispatches an int.
    transport = RecordingMidiTransport()
    service = LogicCommandService(
        config=BridgeConfig.from_mapping(_all_midi_kinds_mapping()),
        midi=transport,
    )

    service.invoke_action(action_id="device.absolute.parameter", value=64.0)

    assert transport.sysex_calls == [(0, 64, 8)]


def test_invoke_action_rejects_an_unconfigured_opaque_id_before_dispatch() -> None:
    transport = RecordingMidiTransport()
    service = LogicCommandService(
        config=BridgeConfig.from_mapping(_all_midi_kinds_mapping()),
        midi=transport,
    )

    with pytest.raises(UnknownActionError, match="not allowlisted") as caught:
        service.invoke_action(action_id="device.unconfigured.command")

    assert caught.value.details["action"] == "device.unconfigured.command"
    assert [event["state"] for event in caught.value.details["events"]] == ["requested"]
    assert transport.cc_calls == []
    assert transport.note_calls == []
    assert transport.sysex_calls == []


def test_typed_volume_and_mute_share_the_atomic_typed_cc_executor() -> None:
    service, transport = make_service()

    volume = service.set_volume(track_id="selected", value=0.25)
    mute = service.toggle_mute(track_id="selected")

    assert transport.cc_calls == [
        (0, 7, 32, None),
        (0, 20, 127, 0),
    ]
    assert volume["events"][1]["message_count"] == 1
    assert mute["events"][1]["message_count"] == 2


def test_partial_transport_failure_uses_transport_success_count() -> None:
    class PartialNoteFailure(RecordingMidiTransport):
        def send_note(
            self,
            *,
            channel: int,
            note: int,
            velocity: int,
            release_velocity: int,
        ) -> int:
            raise MidiDispatchError(
                "note-off failed",
                details={"successful_send_count": 1, "message_count": 2},
            )

    service = LogicCommandService(
        config=BridgeConfig.from_mapping(_all_midi_kinds_mapping()),
        midi=PartialNoteFailure(),
    )

    with pytest.raises(MidiDispatchError) as caught:
        service.invoke_action(action_id="transport.marker.trigger")

    events = caught.value.details["events"]
    assert [event["state"] for event in events] == [
        "requested",
        "dispatched",
        "unknown",
    ]
    assert events[1]["message_count"] == 1
    assert events[1]["partial"] is True


def test_invoke_capability_exposes_safe_ids_but_not_raw_routes_or_sysex() -> None:
    service = LogicCommandService(
        config=BridgeConfig.from_mapping(_all_midi_kinds_mapping()),
        midi=RecordingMidiTransport(),
    )

    capabilities = service.capabilities()
    invoke = capabilities["contract"]["methods"]["logic.invoke_action"]
    rendered = repr(capabilities)

    assert invoke["availability"]["available"] is True
    assert invoke["availability"]["prerequisites"]["configured_action_ids"] == [
        "device.absolute.parameter",
        "device.fixed.command",
        "device.normalized.parameter",
        "mixer.selected.pan",
        "track.selected.mute_toggle",
        "track.selected.volume",
        "transport.marker.trigger",
    ]
    assert "(0, 32, 51, 1)" not in rendered
    assert "value_index" not in rendered
    assert "control" not in invoke["params"]["properties"]
    assert "channel" not in invoke["params"]["properties"]
    assert "data" not in invoke["params"]["properties"]


def test_health_separates_running_from_midi_readiness_and_logic_state() -> None:
    observed_at = datetime(2026, 7, 15, 1, 2, 3, tzinfo=UTC)
    config = BridgeConfig.from_mapping(valid_mapping())
    service = LogicCommandService(
        config=config,
        midi=RecordingMidiTransport(outputs=["IAC Driver Bus 1"]),
        clock=lambda: observed_at,
    )

    health = service.health()

    assert health["status"] == "running"
    assert health["readiness"]["status"] == "not_ready"
    assert health["readiness"]["source"] == "midi_output_probe"
    assert health["readiness"]["observed_at"] == observed_at.isoformat()
    assert health["logic_state"] == {
        "value": "unknown",
        "source": "unsupported_logic_readback",
        "observed_at": None,
        "reported_at": observed_at.isoformat(),
        "stale": True,
        "verification_status": "unverified",
        "reason": "logic_pro_has_no_supported_state_readback_api",
    }


def test_capabilities_expose_ids_and_metadata_without_host_paths() -> None:
    service, _ = make_service()

    capabilities = service.capabilities()
    rendered = repr(capabilities)

    assert "track.selected.volume" in rendered
    assert "logic.set_volume" in capabilities["rpc_methods"]
    assert capabilities["rpc_methods"] == list(METHOD_SPECS)
    assert "logic.execute" not in capabilities["rpc_methods"]
    assert capabilities["contract"]["version"] == CONTRACT_VERSION
    assert capabilities["contract"]["request_schema"] == request_schema()
    volume = capabilities["contract"]["methods"]["logic.set_volume"]
    assert volume["result"]["descriptor"] == "command_lifecycle"
    assert volume["lifecycle"]["states"] == [
        "requested",
        "dispatched",
        "unknown",
    ]
    cst = capabilities["contract"]["methods"]["logic.load_cst_preset"]
    assert cst["lifecycle"]["unverified_success_sequence"] == [
        "requested",
        "dispatched",
        "observed",
        "unknown",
    ]
    assert cst["lifecycle"]["verified_success_sequence"] == [
        "requested",
        "dispatched",
        "observed",
        "verified",
    ]
    assert volume["availability"] == {
        "available": True,
        "status": "unverifiable",
        "reason": "controller_assignments_unverifiable",
        "next_action": {
            "code": "verify_controller_assignments_in_logic",
            "instruction": (
                "Open Logic Controller Assignments with Option-Command-K and "
                "manually verify every configured opaque action mapping."
            ),
        },
        "prerequisites": {
            "authentication": "required",
            "midi_output": "IAC Driver AI_Logic_Bridge",
            "required_value_mode": "normalized",
            "controller_assignment_ids": ["track.selected.volume"],
        },
    }
    assert "/Users/" not in rendered
    assert "filesystem_path" not in rendered


def test_load_cst_preset_reports_observation_without_claiming_verification() -> None:
    loader = RecordingCstPresetLoader()
    service = LogicCommandService(
        config=BridgeConfig.from_mapping(_cst_mapping()),
        midi=RecordingMidiTransport(),
        cst_loader=loader,
    )

    result = service.load_cst_preset(
        preset_id="preset.lead-vocal",
        target_id="track.lead-vocal",
        confirmation=_CST_CONFIRMATION,
    )

    assert loader.loaded == [("preset.lead-vocal", "track.lead-vocal")]
    assert [event["state"] for event in result["events"]] == [
        "requested",
        "dispatched",
        "observed",
        "unknown",
    ]
    assert result["events"][2]["evidence"] == (
        "preset_menu_item_pressed_and_popup_dismissed"
    )
    assert "verified" not in {event["state"] for event in result["events"]}
    assert "/Users/" not in repr(result)


def test_load_cst_preset_requires_explicit_confirmation_before_resolution() -> None:
    loader = RecordingCstPresetLoader()
    service = LogicCommandService(
        config=BridgeConfig.from_mapping(_cst_mapping()),
        midi=RecordingMidiTransport(),
        cst_loader=loader,
    )

    with pytest.raises(ConfirmationRequiredError) as caught:
        service.load_cst_preset(
            preset_id="preset.missing",
            target_id="track.lead-vocal",
            confirmation="cancel",
        )

    assert [event["state"] for event in caught.value.details["events"]] == ["requested"]
    assert loader.loaded == []


def test_load_cst_preset_verifies_only_configured_visible_plugin_signature() -> None:
    raw = _cst_mapping()
    accessibility = raw["accessibility"]
    assert isinstance(accessibility, dict)
    presets = accessibility["presets"]
    assert isinstance(presets, list)
    preset = presets[0]
    assert isinstance(preset, dict)
    preset["expected_plugin_signature"] = ["Compressor", "Channel EQ"]
    preset["signature_match_mode"] = "subset"
    observation = MixerObservation(
        target_id="track.lead-vocal",
        observed_at="2026-07-15T04:00:00+00:00",
        source="macos_accessibility_mixer_inspector_v1",
        verification_scope="visible_plugin_slot_labels",
        plugins=("Channel EQ", "Compressor", "Gain"),
        complete=True,
    )
    service = LogicCommandService(
        config=BridgeConfig.from_mapping(raw),
        midi=RecordingMidiTransport(),
        cst_loader=RecordingCstPresetLoader(mixer_observation=observation),
    )

    result = service.load_cst_preset(
        preset_id="preset.lead-vocal",
        target_id="track.lead-vocal",
        confirmation=_CST_CONFIRMATION,
    )

    assert [event["state"] for event in result["events"]] == [
        "requested",
        "dispatched",
        "observed",
        "verified",
    ]
    assert result["events"][-1]["scope"] == "mixer_plugin_signature"
    assert result["events"][-1]["proof_source"] == observation.source
    assert result["post_load_observation"]["plugins"] == [
        "Channel EQ",
        "Compressor",
        "Gain",
    ]


def test_load_cst_preset_keeps_mismatched_signature_unknown() -> None:
    raw = _cst_mapping()
    accessibility = raw["accessibility"]
    assert isinstance(accessibility, dict)
    presets = accessibility["presets"]
    assert isinstance(presets, list)
    preset = presets[0]
    assert isinstance(preset, dict)
    preset["expected_plugin_signature"] = ["Compressor"]
    preset["signature_match_mode"] = "exact"
    service = LogicCommandService(
        config=BridgeConfig.from_mapping(raw),
        midi=RecordingMidiTransport(),
        cst_loader=RecordingCstPresetLoader(
            mixer_observation=MixerObservation(
                target_id="track.lead-vocal",
                observed_at="2026-07-15T04:00:00+00:00",
                source="macos_accessibility_mixer_inspector_v1",
                verification_scope="visible_plugin_slot_labels",
                plugins=("Channel EQ",),
                complete=True,
            )
        ),
    )

    result = service.load_cst_preset(
        preset_id="preset.lead-vocal",
        target_id="track.lead-vocal",
        confirmation=_CST_CONFIRMATION,
    )

    assert result["events"][-1]["state"] == "unknown"
    assert result["events"][-1]["reason"] == "mixer_plugin_signature_mismatch"


def test_inspect_mixer_target_returns_bounded_opaque_evidence() -> None:
    loader = RecordingCstPresetLoader()
    service = LogicCommandService(
        config=BridgeConfig.from_mapping(_cst_mapping()),
        midi=RecordingMidiTransport(),
        cst_loader=loader,
    )

    result = service.inspect_mixer_target(target_id="track.lead-vocal")

    assert result["target_id"] == "track.lead-vocal"
    assert result["verification_scope"] == "visible_plugin_slot_labels"
    assert loader.inspected == ["track.lead-vocal"]
    assert "expected_accessibility_name" not in repr(result)


def test_scripter_is_blocked_without_explicit_operator_attestation() -> None:
    service, transport = make_service()

    with pytest.raises(ScripterUnavailableError) as caught:
        service.set_scripter_parameter(
            parameter_id="scripter.retro-synth.filter-cutoff",
            value=0.5,
        )

    assert [event["state"] for event in caught.value.details["events"]] == ["requested"]
    assert transport.sent == []


def test_scripter_dispatches_only_the_fixed_channel_16_target_route() -> None:
    transport = RecordingMidiTransport()
    service = LogicCommandService(
        config=BridgeConfig.from_mapping(_scripter_mapping()),
        midi=transport,
    )

    result = service.set_scripter_parameter(
        parameter_id="scripter.retro-synth.filter-cutoff",
        value=0.5,
    )

    assert transport.cc_calls == [(15, 102, 64, None)]
    assert [event["state"] for event in result["events"]] == [
        "requested",
        "dispatched",
        "unknown",
    ]
    assert result["events"][-1]["reason"] == (
        "scripter_target_has_no_verified_logic_readback"
    )
    capabilities = service.capabilities()
    assert capabilities["scripter"]["source_sha256"] == SCRIPT_SHA256
    assert "channel" not in repr(capabilities["scripter"])
    assert "control" not in repr(capabilities["scripter"])


def test_scripter_hash_reflects_the_packaged_file_not_just_the_pinned_constant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A repackaged or patched distribution must be visible to an operator
    # comparing this reported value against the reviewed source, instead of
    # the daemon silently reporting the pinned constant regardless of what
    # is actually on disk.
    monkeypatch.setattr(commands, "packaged_script_sha256", lambda: "f" * 64)
    service = LogicCommandService(
        config=BridgeConfig.from_mapping(_scripter_mapping()),
        midi=RecordingMidiTransport(),
    )

    capabilities = service.capabilities()

    assert capabilities["scripter"]["source_sha256"] == "f" * 64
    assert capabilities["scripter"]["source_sha256"] != SCRIPT_SHA256


def test_scripter_hash_falls_back_to_the_pinned_constant_when_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise() -> str:
        raise OSError("packaged resource missing")

    monkeypatch.setattr(commands, "packaged_script_sha256", _raise)
    service = LogicCommandService(
        config=BridgeConfig.from_mapping(_scripter_mapping()),
        midi=RecordingMidiTransport(),
    )

    assert service.capabilities()["scripter"]["source_sha256"] == SCRIPT_SHA256


def test_load_cst_preset_rejects_unknown_resource_without_path_disclosure() -> None:
    loader = RecordingCstPresetLoader()
    service = LogicCommandService(
        config=BridgeConfig.from_mapping(_cst_mapping()),
        midi=RecordingMidiTransport(),
        cst_loader=loader,
    )

    with pytest.raises(PresetNotAllowlistedError) as caught:
        service.load_cst_preset(
            preset_id="preset.missing",
            target_id="track.lead-vocal",
            confirmation=(
                "load preset.missing into track.lead-vocal in disposable project"
            ),
        )

    assert [event["state"] for event in caught.value.details["events"]] == ["requested"]
    assert "/Users/" not in repr(caught.value.to_rpc_data())
    assert loader.loaded == []


def test_load_cst_timeout_is_ambiguous_after_dispatch_and_not_retryable() -> None:
    loader = RecordingCstPresetLoader(
        error=PresetLoadTimeoutError(
            "Channel-strip preset loading timed out",
            details={"dispatch_started": True},
        )
    )
    service = LogicCommandService(
        config=BridgeConfig.from_mapping(_cst_mapping()),
        midi=RecordingMidiTransport(),
        cst_loader=loader,
    )

    with pytest.raises(PresetLoadTimeoutError) as caught:
        service.load_cst_preset(
            preset_id="preset.lead-vocal",
            target_id="track.lead-vocal",
            confirmation=_CST_CONFIRMATION,
        )

    assert caught.value.retryable is False
    assert [event["state"] for event in caught.value.details["events"]] == [
        "requested",
        "dispatched",
        "unknown",
    ]


def test_accessibility_denial_is_predispatch_and_does_not_affect_midi() -> None:
    loader = RecordingCstPresetLoader(
        error=AccessibilityPermissionRequiredError(
            "Accessibility permission is required"
        )
    )
    transport = RecordingMidiTransport()
    service = LogicCommandService(
        config=BridgeConfig.from_mapping(_cst_mapping()),
        midi=transport,
        cst_loader=loader,
    )

    with pytest.raises(AccessibilityPermissionRequiredError) as caught:
        service.load_cst_preset(
            preset_id="preset.lead-vocal",
            target_id="track.lead-vocal",
            confirmation=_CST_CONFIRMATION,
        )

    service.set_volume(track_id="selected", value=0.5)
    assert [event["state"] for event in caught.value.details["events"]] == ["requested"]
    assert transport.sent == [(0, 7, 64)]


def test_cst_capabilities_expose_only_opaque_resource_ids() -> None:
    service = LogicCommandService(
        config=BridgeConfig.from_mapping(_cst_mapping()),
        midi=RecordingMidiTransport(),
        cst_loader=RecordingCstPresetLoader(),
    )

    capabilities = service.capabilities()
    rendered = repr(capabilities)

    assert (
        capabilities["contract"]["methods"]["logic.load_cst_preset"]["availability"][
            "available"
        ]
        is True
    )
    assert capabilities["contract"]["methods"]["logic.load_cst_preset"]["availability"][
        "prerequisites"
    ]["automation_consent"] == ("required_unknown_until_dispatch")
    assert {resource["id"] for resource in capabilities["resources"]} == {
        "preset.lead-vocal",
        "track.lead-vocal",
    }
    assert "Lead Vocal.cst" not in rendered
    assert "expected_accessibility_name" not in rendered
    assert "/Users/" not in rendered


def test_capabilities_degrades_instead_of_crashing_on_malformed_loader_status() -> None:
    # A CstPresetLoaderLike implementation that returns present-but-wrong-typed
    # fields (as opposed to just omitting them) must not crash the read-only
    # bridge.capabilities RPC method.
    service = LogicCommandService(
        config=BridgeConfig.from_mapping(_cst_mapping()),
        midi=RecordingMidiTransport(),
        cst_loader=RecordingCstPresetLoader(
            status_value={
                "supported": True,
                "trusted": True,
                "script_ready": True,
                "reason": None,
                "available_preset_ids": None,
                "unavailable_preset_ids": None,
            }
        ),
    )

    capabilities = service.capabilities()

    assert {resource["id"] for resource in capabilities["resources"]} == {
        "preset.lead-vocal",
        "track.lead-vocal",
    }


def test_cst_capability_remains_available_when_one_of_two_presets_is_valid() -> None:
    raw = _cst_mapping()
    accessibility = raw["accessibility"]
    assert isinstance(accessibility, dict)
    presets = accessibility["presets"]
    assert isinstance(presets, list)
    presets.append(
        {
            "id": "preset.missing",
            "filename": "Missing.cst",
            "sha256": "b" * 64,
        }
    )
    loader = RecordingCstPresetLoader(
        status_value={
            "supported": True,
            "trusted": True,
            "script_ready": True,
            "available_preset_ids": ["preset.lead-vocal"],
            "unavailable_preset_ids": [
                {"id": "preset.missing", "reason": "PRESET_FILE_UNAVAILABLE"}
            ],
            "reason": "cst_resources_partially_unavailable",
        }
    )
    service = LogicCommandService(
        config=BridgeConfig.from_mapping(raw),
        midi=RecordingMidiTransport(),
        cst_loader=loader,
    )

    capabilities = service.capabilities()
    method = capabilities["contract"]["methods"]["logic.load_cst_preset"]
    resources = {
        resource["id"]: resource
        for resource in capabilities["resources"]
        if resource["kind"] == "channel_strip_setting"
    }

    assert method["availability"]["available"] is True
    assert resources["preset.lead-vocal"]["availability"]["available"] is True
    assert resources["preset.missing"]["availability"] == {
        "available": False,
        "reason": "PRESET_FILE_UNAVAILABLE",
    }
    result = service.load_cst_preset(
        preset_id="preset.lead-vocal",
        target_id="track.lead-vocal",
        confirmation=_CST_CONFIRMATION,
    )
    assert result["events"][-1]["state"] == "unknown"
