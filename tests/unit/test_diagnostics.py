from __future__ import annotations

from pathlib import Path

from daemon.config import BridgeConfig
from daemon.diagnostics import run_diagnostics
from tests.fakes import RecordingCstPresetLoader, RecordingMidiTransport, valid_mapping


VALID_TOKEN = "v" * 32


def _cst_enabled_mapping() -> dict[str, object]:
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
                "mixer_index": 0,
                "expected_accessibility_name": "Lead Vocal",
            }
        ],
    }
    return raw


def test_diagnostics_derives_cst_presets_from_the_loaders_cache_when_provided() -> None:
    config = BridgeConfig.from_mapping(_cst_enabled_mapping())
    loader = RecordingCstPresetLoader(
        status_value={
            "supported": True,
            "trusted": True,
            "script_ready": True,
            "reason": None,
            "available_preset_ids": ["preset.lead-vocal"],
            "unavailable_preset_ids": [],
        }
    )

    result = run_diagnostics(
        config=config,
        midi=RecordingMidiTransport(),
        environ={"LOGIC_BRIDGE_TOKEN": VALID_TOKEN},
        accessibility_probe=lambda: True,
        cst_loader=loader,
    )

    assert result["checks"]["cst_presets"] == {
        "ok": True,
        "blocking": False,
        "enabled": True,
        "available": True,
        "available_ids": ["preset.lead-vocal"],
        "resource_ids": ["preset.lead-vocal"],
        "unavailable_ids": [],
        "reason": None,
    }


def test_diagnostics_marks_cst_not_ready_when_the_loader_reports_script_unavailable() -> (
    None
):
    config = BridgeConfig.from_mapping(_cst_enabled_mapping())
    loader = RecordingCstPresetLoader(
        status_value={
            "supported": True,
            "trusted": True,
            "script_ready": False,
            "reason": "accessibility_script_unavailable",
            "available_preset_ids": ["preset.lead-vocal"],
            "unavailable_preset_ids": [],
        }
    )

    result = run_diagnostics(
        config=config,
        midi=RecordingMidiTransport(),
        environ={"LOGIC_BRIDGE_TOKEN": VALID_TOKEN},
        accessibility_probe=lambda: True,
        cst_loader=loader,
    )

    cst_item = next(
        item
        for item in result["operator_readiness"]["items"]
        if item["id"] == "cst_resources"
    )
    assert cst_item["status"] != "ready"


def test_missing_required_token_is_blocking_and_secret_is_never_reported() -> None:
    config = BridgeConfig.from_mapping(valid_mapping())

    result = run_diagnostics(
        config=config,
        midi=RecordingMidiTransport(),
        environ={},
        accessibility_probe=lambda: True,
    )

    assert result["status"] == "not_ready"
    assert result["checks"]["authentication"]["ok"] is False
    assert "LOGIC_BRIDGE_TOKEN" in repr(result)
    assert "secret" not in repr(result)


def test_operator_readiness_has_fixed_states_and_actionable_non_ready_items() -> None:
    config = BridgeConfig.from_mapping(valid_mapping())

    result = run_diagnostics(
        config=config,
        midi=RecordingMidiTransport(outputs=[]),
        environ={},
        accessibility_probe=lambda: False,
    )

    readiness = result["operator_readiness"]
    assert readiness["contract_version"] == 1
    assert readiness["states"] == ["ready", "blocked", "unverifiable"]
    assert [item["id"] for item in readiness["items"]] == [
        "configuration_integrity",
        "authentication",
        "midi_output",
        "controller_assignments",
        "accessibility",
        "automation_consent",
        "cst_resources",
        "scripter",
    ]
    for item in readiness["items"]:
        if item["status"] == "ready":
            assert item["next_action"] is None
        else:
            assert set(item["next_action"]) == {"code", "instruction"}
            assert item["next_action"]["code"]
            assert item["next_action"]["instruction"]


def test_short_required_token_is_blocking_without_reporting_token_material() -> None:
    config = BridgeConfig.from_mapping(valid_mapping())
    token = "short-token-material"

    result = run_diagnostics(
        config=config,
        midi=RecordingMidiTransport(),
        environ={"LOGIC_BRIDGE_TOKEN": token},
        accessibility_probe=lambda: True,
    )

    assert result["status"] == "not_ready"
    assert result["checks"]["authentication"] == {
        "ok": False,
        "blocking": True,
        "required": True,
        "token_env": "LOGIC_BRIDGE_TOKEN",
        "minimum_encoded_bytes": 32,
        "reason": "required_token_is_too_short",
    }
    assert token not in repr(result)


def test_token_strength_uses_encoded_bytes_not_character_count() -> None:
    config = BridgeConfig.from_mapping(valid_mapping())

    too_short = run_diagnostics(
        config=config,
        midi=RecordingMidiTransport(),
        environ={"LOGIC_BRIDGE_TOKEN": "é" * 15},
        accessibility_probe=lambda: True,
    )
    accepted = run_diagnostics(
        config=config,
        midi=RecordingMidiTransport(),
        environ={"LOGIC_BRIDGE_TOKEN": "é" * 16},
        accessibility_probe=lambda: True,
    )

    assert too_short["checks"]["authentication"]["ok"] is False
    assert accepted["checks"]["authentication"]["ok"] is True


def test_missing_accessibility_permission_is_warning_for_midi_only_slice() -> None:
    config = BridgeConfig.from_mapping(valid_mapping())

    result = run_diagnostics(
        config=config,
        midi=RecordingMidiTransport(),
        environ={"LOGIC_BRIDGE_TOKEN": VALID_TOKEN},
        accessibility_probe=lambda: False,
    )

    assert result["status"] == "degraded"
    assert result["checks"]["midi_output"]["ok"] is True
    assert result["checks"]["accessibility"]["blocking"] is False
    assert result["checks"]["accessibility"]["required_for"] == (
        "logic.load_cst_preset"
    )
    assert result["checks"]["automation"]["known"] is False
    assert result["checks"]["automation"]["status"] == "not_applicable"


def test_missing_expected_midi_output_is_blocking() -> None:
    config = BridgeConfig.from_mapping(valid_mapping())

    result = run_diagnostics(
        config=config,
        midi=RecordingMidiTransport(outputs=["IAC Driver Bus 1"]),
        environ={"LOGIC_BRIDGE_TOKEN": VALID_TOKEN},
        accessibility_probe=lambda: True,
    )

    assert result["status"] == "not_ready"
    assert result["checks"]["midi_output"] == {
        "ok": False,
        "blocking": True,
        "expected": "IAC Driver AI_Logic_Bridge",
        "available": ["IAC Driver Bus 1"],
        "reason": "configured_midi_output_not_found",
    }


def test_accessibility_probe_is_skipped_when_disabled() -> None:
    raw = valid_mapping()
    raw["permissions"] = {"require_accessibility_for_ui": False}
    config = BridgeConfig.from_mapping(raw)
    probe_called = False

    def probe() -> bool:
        nonlocal probe_called
        probe_called = True
        return False

    result = run_diagnostics(
        config=config,
        midi=RecordingMidiTransport(),
        environ={"LOGIC_BRIDGE_TOKEN": VALID_TOKEN},
        accessibility_probe=probe,
    )

    assert result["status"] == "ready"
    assert (
        result["checks"]["accessibility"]["reason"] == "accessibility_checks_disabled"
    )
    assert probe_called is False


def test_enabled_cst_resources_degrade_without_blocking_midi_or_leaking_paths(
    tmp_path: Path,
) -> None:
    raw = valid_mapping()
    raw["accessibility"] = {
        "enabled": True,
        "preset_root": str(tmp_path),
        "presets": [
            {
                "id": "preset.missing",
                "filename": "Missing.cst",
                "sha256": "a" * 64,
            }
        ],
        "targets": [
            {
                "id": "track.test",
                "mixer_index": 0,
                "expected_accessibility_name": "Test",
            }
        ],
    }
    config = BridgeConfig.from_mapping(raw)

    result = run_diagnostics(
        config=config,
        midi=RecordingMidiTransport(),
        environ={"LOGIC_BRIDGE_TOKEN": VALID_TOKEN},
        accessibility_probe=lambda: True,
    )

    assert result["status"] == "degraded"
    assert result["checks"]["midi_output"]["ok"] is True
    assert result["checks"]["cst_presets"] == {
        "ok": False,
        "blocking": False,
        "enabled": True,
        "available": False,
        "available_ids": [],
        "resource_ids": ["preset.missing"],
        "unavailable_ids": [
            {"id": "preset.missing", "reason": "PRESET_FILE_UNAVAILABLE"}
        ],
        "reason": "cst_resources_unavailable",
    }
    assert str(tmp_path) not in repr(result)


def test_enabled_cst_loading_never_skips_accessibility_probe(tmp_path: Path) -> None:
    raw = valid_mapping()
    raw["permissions"] = {"require_accessibility_for_ui": False}
    raw["accessibility"] = {
        "enabled": True,
        "preset_root": str(tmp_path),
        "presets": [
            {
                "id": "preset.missing",
                "filename": "Missing.cst",
                "sha256": "a" * 64,
            }
        ],
        "targets": [
            {
                "id": "track.test",
                "mixer_index": 0,
                "expected_accessibility_name": "Test",
            }
        ],
    }
    config = BridgeConfig.from_mapping(raw)
    probe_called = False

    def probe() -> bool:
        nonlocal probe_called
        probe_called = True
        return False

    result = run_diagnostics(
        config=config,
        midi=RecordingMidiTransport(),
        environ={"LOGIC_BRIDGE_TOKEN": VALID_TOKEN},
        accessibility_probe=probe,
    )

    assert probe_called is True
    assert result["checks"]["accessibility"]["ok"] is False
    assert result["checks"]["automation"]["status"] == "unknown_until_dispatch"
