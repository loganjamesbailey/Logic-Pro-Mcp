from __future__ import annotations

from typing import NoReturn

from daemon.commands import LogicCommandService
from daemon.config import BridgeConfig
from daemon.diagnostics import run_diagnostics
from daemon.errors import MidiBackendUnavailableError
from daemon.readiness import _cst_capability_state
from daemon.scripter import SCRIPT_SHA256
from tests.fakes import RecordingMidiTransport, valid_mapping


VALID_TOKEN = "v" * 32
MIDI_BACKED_METHODS = (
    "logic.set_volume",
    "logic.toggle_mute",
    "logic.invoke_action",
    "logic.set_scripter_parameter",
)


class CountingMidiTransport(RecordingMidiTransport):
    def __init__(self, *, outputs: list[str]) -> None:
        super().__init__(outputs=outputs)
        self.output_probe_count = 0

    def list_outputs(self) -> list[str]:
        self.output_probe_count += 1
        return super().list_outputs()


class UnavailableMidiTransport(CountingMidiTransport):
    def list_outputs(self) -> NoReturn:
        self.output_probe_count += 1
        raise MidiBackendUnavailableError("MIDI backend is unavailable")


def _scripter_mapping() -> dict[str, object]:
    raw = valid_mapping()
    raw["permissions"] = {"require_accessibility_for_ui": False}
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


def test_health_doctor_and_capabilities_probe_midi_once_per_response() -> None:
    config = BridgeConfig.from_mapping(_scripter_mapping())
    midi = CountingMidiTransport(outputs=[config.midi.output_name])
    service = LogicCommandService(config=config, midi=midi)

    service.health()
    assert midi.output_probe_count == 1

    service.capabilities()
    assert midi.output_probe_count == 2

    run_diagnostics(
        config=config,
        midi=midi,
        environ={config.server.token_env: VALID_TOKEN},
        accessibility_probe=lambda: True,
    )
    assert midi.output_probe_count == 3


def test_capabilities_and_doctor_share_ordered_readiness_and_next_actions() -> None:
    config = BridgeConfig.from_mapping(_scripter_mapping())
    midi = CountingMidiTransport(outputs=[config.midi.output_name])
    service = LogicCommandService(config=config, midi=midi)

    capabilities = service.capabilities()
    diagnostics = run_diagnostics(
        config=config,
        midi=midi,
        environ={config.server.token_env: VALID_TOKEN},
        accessibility_probe=lambda: True,
    )

    assert capabilities["operator_readiness"] == diagnostics["operator_readiness"]
    items = capabilities["operator_readiness"]["items"]
    assert [
        (item["id"], item["status"], _next_action_code(item)) for item in items
    ] == [
        ("configuration_integrity", "ready", None),
        ("authentication", "ready", None),
        ("midi_output", "ready", None),
        (
            "controller_assignments",
            "unverifiable",
            "verify_controller_assignments_in_logic",
        ),
        ("accessibility", "ready", None),
        ("automation_consent", "ready", None),
        (
            "cst_resources",
            "blocked",
            "configure_and_probe_cst_allowlists",
        ),
        ("scripter", "unverifiable", "verify_scripter_route_in_logic"),
    ]
    for item in items:
        assert item["status"] in capabilities["operator_readiness"]["states"]
        if item["status"] == "ready":
            assert item["reason"] is None
            assert item["next_action"] is None
        else:
            assert isinstance(item["reason"], str) and item["reason"]
            assert set(item["next_action"]) == {"code", "instruction"}


def test_missing_iac_blocks_every_midi_backed_capability_with_one_action() -> None:
    config = BridgeConfig.from_mapping(_scripter_mapping())
    midi = CountingMidiTransport(outputs=["IAC Driver Bus 1"])
    capabilities = LogicCommandService(config=config, midi=midi).capabilities()

    assert midi.output_probe_count == 1
    methods = capabilities["contract"]["methods"]
    for method in MIDI_BACKED_METHODS:
        availability = methods[method]["availability"]
        assert availability["available"] is False
        assert availability["status"] == "blocked"
        assert availability["reason"] == "configured_midi_output_not_found"
        assert availability["next_action"]["code"] == "enable_exact_iac_output"
        assert availability["prerequisites"]["midi_output"] == (
            config.midi.output_name
        )

    readiness = {
        item["id"]: item for item in capabilities["operator_readiness"]["items"]
    }
    for item_id in ("midi_output", "controller_assignments", "scripter"):
        assert readiness[item_id]["status"] == "blocked"
        assert readiness[item_id]["reason"] == "configured_midi_output_not_found"
        assert readiness[item_id]["next_action"]["code"] == (
            "enable_exact_iac_output"
        )


def test_midi_backend_code_and_recovery_action_flow_to_capabilities() -> None:
    config = BridgeConfig.from_mapping(_scripter_mapping())
    midi = UnavailableMidiTransport(outputs=[])
    capabilities = LogicCommandService(config=config, midi=midi).capabilities()

    assert midi.output_probe_count == 1
    methods = capabilities["contract"]["methods"]
    for method in MIDI_BACKED_METHODS:
        availability = methods[method]["availability"]
        assert availability["available"] is False
        assert availability["status"] == "blocked"
        assert availability["reason"] == "MIDI_BACKEND_UNAVAILABLE"
        assert availability["next_action"]["code"] == "repair_midi_backend"


def _accessibility_enabled_mapping() -> dict[str, object]:
    raw = valid_mapping()
    raw["accessibility"] = {
        "enabled": True,
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
                "mixer_index": 2,
                "expected_accessibility_name": "Lead Vocal",
            }
        ],
    }
    return raw


def test_cst_capability_state_defaults_script_ready_closed_when_key_is_missing() -> (
    None
):
    config = BridgeConfig.from_mapping(_accessibility_enabled_mapping())

    # A CstPresetLoaderLike implementation that omits script_ready entirely
    # must not be treated as ready -- fail-closed matches the supported/
    # trusted fields, which already default to False when missing.
    incomplete_status = {"supported": True, "trusted": True}

    state = _cst_capability_state(config=config, cst_status=incomplete_status)

    assert state.inspect_available is False
    assert state.load_available is False
    assert state.reason == "accessibility_script_unavailable"


def _next_action_code(item: dict[str, object]) -> object:
    action = item["next_action"]
    if not isinstance(action, dict):
        return None
    return action["code"]
