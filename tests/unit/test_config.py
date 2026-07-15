from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from daemon.config import BridgeConfig, load_config
from daemon.errors import ConfigurationError
from daemon.scripter import MIDO_CHANNEL, SCRIPT_SHA256
from tests.fakes import valid_mapping


def _copy_example_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "bridge.toml"
    shutil.copyfile(Path("config/bridge.example.toml"), config_path)
    config_path.chmod(0o644)
    tmp_path.chmod(0o700)
    return config_path


def _config_with_assignments(
    *assignments: dict[str, object],
) -> BridgeConfig:
    raw = valid_mapping()
    midi = raw["midi"]
    assert isinstance(midi, dict)
    midi["assignments"] = list(assignments)
    return BridgeConfig.from_mapping(raw)


def _control_change_assignment(**overrides: object) -> dict[str, object]:
    return {
        "name": "track.test.control",
        "kind": "control_change",
        "channel": 0,
        "control": 21,
        "value_mode": "normalized",
        **overrides,
    }


def _note_assignment(**overrides: object) -> dict[str, object]:
    return {
        "name": "transport.test.note",
        "kind": "note",
        "channel": 2,
        "note": 60,
        "velocity": 100,
        "release_velocity": 0,
        "value_mode": "trigger",
        **overrides,
    }


def _sysex_assignment(**overrides: object) -> dict[str, object]:
    return {
        "name": "device.test.sysex",
        "kind": "sysex",
        "data": [0x00, 0x20, 0x33, 0x01],
        "value_mode": "trigger",
        **overrides,
    }


def _scripter_section(**overrides: object) -> dict[str, object]:
    return {
        "enabled": True,
        "protocol_version": 1,
        "channel": MIDO_CHANNEL,
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
        **overrides,
    }


def test_accepts_loopback_server_and_allowlisted_assignments() -> None:
    config = BridgeConfig.from_mapping(valid_mapping())

    assert config.server.host == "127.0.0.1"
    assert config.midi.output_name == "IAC Driver AI_Logic_Bridge"
    assert set(config.midi.assignments) == {
        "track.selected.volume",
        "track.selected.mute_toggle",
    }


def test_accepts_every_normative_midi_assignment_shape() -> None:
    config = _config_with_assignments(
        _control_change_assignment(name="cc.normalized"),
        _control_change_assignment(name="cc.absolute", value_mode="absolute"),
        _control_change_assignment(
            name="cc.trigger",
            value_mode="trigger",
            value=127,
            release_value=0,
        ),
        _note_assignment(name="note.trigger"),
        _sysex_assignment(name="sysex.fixed"),
        _sysex_assignment(
            name="sysex.normalized",
            value_mode="normalized",
            value_index=3,
        ),
        _sysex_assignment(
            name="sysex.absolute",
            value_mode="absolute",
            value_index=0,
        ),
    )

    assert config.midi.assignments["cc.normalized"].channel == 0
    assert config.midi.assignments["cc.absolute"].control == 21
    assert config.midi.assignments["cc.trigger"].value == 127
    assert config.midi.assignments["cc.trigger"].release_value == 0
    assert config.midi.assignments["note.trigger"].note == 60
    assert config.midi.assignments["note.trigger"].velocity == 100
    assert config.midi.assignments["note.trigger"].release_velocity == 0
    assert config.midi.assignments["sysex.fixed"].data == (0, 32, 51, 1)
    assert config.midi.assignments["sysex.fixed"].value_index is None
    assert config.midi.assignments["sysex.normalized"].value_index == 3
    assert config.midi.assignments["sysex.absolute"].value_index == 0


def test_assignment_public_metadata_is_exact_and_route_private() -> None:
    assignment = _config_with_assignments(
        _sysex_assignment(
            name="sysex.private",
            value_mode="normalized",
            value_index=3,
        )
    ).midi.assignments["sysex.private"]

    assert assignment.public_metadata() == {
        "id": "sysex.private",
        "kind": "sysex",
        "value_mode": "normalized",
        "prerequisites": {
            "midi_output": "required",
            "logic_routing": "operator_configured",
        },
    }
    rendered = repr(assignment.public_metadata())
    for private_field in (
        "channel",
        "control",
        "note",
        "data",
        "value_index",
        "release_value",
        "velocity",
    ):
        assert private_field not in rendered


def test_public_assignment_id_uses_shared_resource_id_length_limit() -> None:
    accepted = "a" * 128
    config = _config_with_assignments(_control_change_assignment(name=accepted))
    assert accepted in config.midi.assignments

    with pytest.raises(ConfigurationError, match="lowercase dotted identifier"):
        _config_with_assignments(_control_change_assignment(name="a" * 129))


@pytest.mark.parametrize(
    ("assignment", "field", "value"),
    [
        (_control_change_assignment(), "note", 60),
        (_control_change_assignment(), "velocity", 100),
        (_control_change_assignment(), "release_velocity", 0),
        (_control_change_assignment(), "data", [1]),
        (_control_change_assignment(), "value_index", 0),
        (_note_assignment(), "control", 1),
        (_note_assignment(), "value", 127),
        (_note_assignment(), "release_value", 0),
        (_note_assignment(), "data", [1]),
        (_note_assignment(), "value_index", 0),
        (_sysex_assignment(), "channel", 0),
        (_sysex_assignment(), "control", 1),
        (_sysex_assignment(), "note", 60),
        (_sysex_assignment(), "velocity", 100),
        (_sysex_assignment(), "release_velocity", 0),
        (_sysex_assignment(), "value", 127),
        (_sysex_assignment(), "release_value", 0),
    ],
)
def test_rejects_kind_specific_forbidden_fields(
    assignment: dict[str, object],
    field: str,
    value: object,
) -> None:
    assignment[field] = value

    with pytest.raises(ConfigurationError, match="unknown fields") as raised:
        _config_with_assignments(assignment)

    assert raised.value.details["unknown"] == [field]


@pytest.mark.parametrize(
    "assignment",
    [
        _control_change_assignment(value_mode="unsupported"),
        _note_assignment(value_mode="normalized"),
        _note_assignment(value_mode="absolute"),
        _sysex_assignment(value_mode="normalized"),
        _sysex_assignment(value_mode="absolute"),
        _sysex_assignment(value_mode="trigger", value_index=0),
    ],
)
def test_rejects_assignment_modes_incompatible_with_kind_or_value_slot(
    assignment: dict[str, object],
) -> None:
    with pytest.raises(ConfigurationError, match="value_mode"):
        _config_with_assignments(assignment)


@pytest.mark.parametrize(
    ("assignment", "message"),
    [
        (_control_change_assignment(value_mode="trigger"), "value is required"),
        (
            _control_change_assignment(value_mode="normalized", value=1),
            "only valid for trigger",
        ),
        (
            _control_change_assignment(value_mode="absolute", release_value=0),
            "only valid for trigger",
        ),
    ],
)
def test_enforces_control_change_configured_value_semantics(
    assignment: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ConfigurationError, match=message):
        _config_with_assignments(assignment)


@pytest.mark.parametrize(
    ("assignment", "message"),
    [
        (_control_change_assignment(channel=-1), "channel"),
        (_control_change_assignment(channel=16), "channel"),
        (_control_change_assignment(control=-1), "control"),
        (_control_change_assignment(control=128), "control"),
        (
            _control_change_assignment(value_mode="trigger", value=-1),
            "value",
        ),
        (
            _control_change_assignment(value_mode="trigger", value=128),
            "value",
        ),
        (
            _control_change_assignment(
                value_mode="trigger",
                value=127,
                release_value=128,
            ),
            "release_value",
        ),
        (_note_assignment(channel=16), "channel"),
        (_note_assignment(note=128), "note"),
        (_note_assignment(velocity=0), "velocity"),
        (_note_assignment(velocity=128), "velocity"),
        (_note_assignment(release_velocity=-1), "release_velocity"),
        (_note_assignment(release_velocity=128), "release_velocity"),
    ],
)
def test_rejects_out_of_range_midi_scalar_fields(
    assignment: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ConfigurationError, match=message):
        _config_with_assignments(assignment)


@pytest.mark.parametrize(
    ("assignment", "message"),
    [
        (_sysex_assignment(data=[]), "between 1 and 256"),
        (_sysex_assignment(data=[0] * 257), "between 1 and 256"),
        (_sysex_assignment(data="0,1"), "array of integers"),
        (_sysex_assignment(data=[0, -1]), r"data\[1\]"),
        (_sysex_assignment(data=[0, 128]), r"data\[1\]"),
        (_sysex_assignment(data=[0, True]), r"data\[1\]"),
        (_sysex_assignment(data=[0xF0]), r"data\[0\]"),
        (_sysex_assignment(data=[0xF7]), r"data\[0\]"),
        (
            _sysex_assignment(value_mode="normalized", value_index=-1),
            "value_index",
        ),
        (
            _sysex_assignment(value_mode="normalized", value_index=4),
            "value_index",
        ),
        (
            _sysex_assignment(value_mode="normalized", value_index=[1, 2]),
            "value_index",
        ),
    ],
)
def test_rejects_unsafe_or_unbounded_sysex_configuration(
    assignment: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ConfigurationError, match=message):
        _config_with_assignments(assignment)


def test_accepts_only_the_complete_attested_scripter_v1_profile() -> None:
    raw = valid_mapping()
    raw["scripter"] = _scripter_section()

    config = BridgeConfig.from_mapping(raw)

    assert config.scripter.enabled is True
    assert config.scripter.operator_attested is True
    assert config.scripter.protocol_version == 1
    assert config.scripter.channel == MIDO_CHANNEL
    assert config.scripter.source_sha256 == SCRIPT_SHA256
    assert config.scripter.placement == "software_instrument_midi_fx"
    assert set(config.scripter.learned_targets) == {
        "scripter.retro-synth.filter-cutoff",
        "scripter.retro-synth.filter-resonance",
    }
    assert (
        config.scripter.learned_targets["scripter.retro-synth.filter-cutoff"].control
        == 102
    )
    assert (
        config.scripter.learned_targets[
            "scripter.retro-synth.filter-resonance"
        ].target_slot
        == 2
    )


def test_scripter_public_metadata_exposes_readiness_not_private_route() -> None:
    raw = valid_mapping()
    raw["scripter"] = _scripter_section()
    config = BridgeConfig.from_mapping(raw)

    assert config.scripter.public_metadata() == {
        "status": "ready",
        "reason": None,
        "next_action": None,
        "software_instrument_only": True,
    }
    rendered = repr(config.scripter.public_metadata())
    for private_value in (
        str(MIDO_CHANNEL),
        "102",
        "103",
        SCRIPT_SHA256,
        "scripter.retro-synth.filter-cutoff",
        "scripter.retro-synth.filter-resonance",
        "Retro Synth Filter Cutoff",
        "Filter Resonance",
    ):
        assert private_value not in rendered


def test_missing_scripter_section_is_safely_disabled() -> None:
    config = BridgeConfig.from_mapping(valid_mapping())

    assert config.scripter.enabled is False
    assert config.scripter.operator_attested is False
    assert config.scripter.public_metadata() == {
        "status": "blocked",
        "reason": "scripter_disabled",
        "next_action": "enable_reviewed_scripter_profile",
        "software_instrument_only": True,
    }


@pytest.mark.parametrize(
    "missing_field",
    [
        "enabled",
        "protocol_version",
        "channel",
        "source_sha256",
        "placement",
        "operator_attested",
        "learned_targets",
    ],
)
def test_rejects_partial_explicit_scripter_profile(missing_field: str) -> None:
    raw = valid_mapping()
    section = _scripter_section()
    del section[missing_field]
    raw["scripter"] = section

    with pytest.raises(ConfigurationError, match="fixed protocol fields") as raised:
        BridgeConfig.from_mapping(raw)

    assert raised.value.details["missing"] == [missing_field]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("protocol_version", 2, "protocol_version"),
        ("channel", 14, "channel"),
        ("source_sha256", "0" * 64, "source_sha256"),
        ("placement", "audio_channel_strip", "software-instrument"),
        ("operator_attested", "yes", "operator_attested"),
    ],
)
def test_rejects_modified_scripter_protocol_fields(
    field: str,
    value: object,
    message: str,
) -> None:
    raw = valid_mapping()
    raw["scripter"] = _scripter_section(**{field: value})

    with pytest.raises(ConfigurationError, match=message):
        BridgeConfig.from_mapping(raw)


def test_rejects_enabled_scripter_without_operator_attestation() -> None:
    raw = valid_mapping()
    raw["scripter"] = _scripter_section(operator_attested=False)

    with pytest.raises(ConfigurationError, match="operator_attested"):
        BridgeConfig.from_mapping(raw)


@pytest.mark.parametrize(
    ("target_index", "field", "value", "message"),
    [
        (0, "parameter_id", "scripter.retro-synth.oscillator-shape", "parameter_id"),
        (0, "control", 101, "control"),
        (0, "target_slot", 2, "target_slot"),
        (0, "target_name", "Oscillator Shape", "target_name"),
        (1, "parameter_id", "scripter.retro-synth.filter-cutoff", "duplicate"),
    ],
)
def test_rejects_wrong_or_duplicate_scripter_learned_target_declarations(
    target_index: int,
    field: str,
    value: object,
    message: str,
) -> None:
    raw = valid_mapping()
    section = _scripter_section()
    targets = section["learned_targets"]
    assert isinstance(targets, list)
    target = targets[target_index]
    assert isinstance(target, dict)
    target[field] = value
    raw["scripter"] = section

    with pytest.raises(ConfigurationError, match=message):
        BridgeConfig.from_mapping(raw)


def test_rejects_partial_scripter_learned_target_declarations() -> None:
    raw = valid_mapping()
    section = _scripter_section()
    targets = section["learned_targets"]
    assert isinstance(targets, list)
    section["learned_targets"] = targets[:1]
    raw["scripter"] = section

    with pytest.raises(ConfigurationError, match="exactly two"):
        BridgeConfig.from_mapping(raw)


def test_rejects_extra_scripter_or_learned_target_fields() -> None:
    raw = valid_mapping()
    section = _scripter_section(extra_route="unsafe")
    raw["scripter"] = section
    with pytest.raises(ConfigurationError, match="unknown fields"):
        BridgeConfig.from_mapping(raw)

    section = _scripter_section()
    targets = section["learned_targets"]
    assert isinstance(targets, list)
    target = targets[0]
    assert isinstance(target, dict)
    target["script"] = "arbitrary.js"
    raw["scripter"] = section
    with pytest.raises(ConfigurationError, match="unknown fields"):
        BridgeConfig.from_mapping(raw)


@pytest.mark.parametrize("control", [102, 103])
def test_rejects_generic_midi_collisions_with_reserved_scripter_route(
    control: int,
) -> None:
    assignment = _control_change_assignment(
        name=f"generic.collision-{control}",
        channel=MIDO_CHANNEL,
        control=control,
    )

    with pytest.raises(ConfigurationError, match="reserved Scripter"):
        _config_with_assignments(assignment)


def test_accepts_generic_midi_assignments_outside_scripter_reserved_route() -> None:
    config = _config_with_assignments(
        _control_change_assignment(
            name="generic.other-channel",
            channel=MIDO_CHANNEL - 1,
            control=102,
        ),
        _control_change_assignment(
            name="generic.other-control",
            channel=MIDO_CHANNEL,
            control=101,
        ),
    )

    assert set(config.midi.assignments) == {
        "generic.other-channel",
        "generic.other-control",
    }


def test_repository_example_config_is_loadable() -> None:
    config = load_config(Path("config/bridge.example.toml"))

    assert config.server.token_env == "LOGIC_BRIDGE_TOKEN"
    assert config.server.read_timeout_seconds == 10.0
    assert config.server.write_timeout_seconds == 2.0
    assert config.server.mcp_timeout_seconds == 75.0
    assert config.server.max_connections == 32
    assert config.midi.output_name == "IAC Driver AI_Logic_Bridge"
    assert config.accessibility.enabled is False
    assert config.accessibility.presets == {}
    assert config.accessibility.targets == {}
    assert config.scripter.enabled is False
    assert config.scripter.operator_attested is False


def test_accepts_opaque_cst_preset_and_mixer_target_allowlists(tmp_path: Path) -> None:
    preset_bytes = b"opaque-cst-fixture"
    raw = valid_mapping()
    raw["accessibility"] = {
        "enabled": True,
        "preset_root": str(tmp_path),
        "timeout_seconds": 8.0,
        "max_cst_bytes": 1_048_576,
        "presets": [
            {
                "id": "preset.lead-vocal",
                "filename": "Lead Vocal.cst",
                "sha256": hashlib.sha256(preset_bytes).hexdigest(),
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

    config = BridgeConfig.from_mapping(raw)

    assert config.accessibility.enabled is True
    assert config.accessibility.preset_root == tmp_path.resolve()
    assert config.accessibility.presets["preset.lead-vocal"].filename == (
        "Lead Vocal.cst"
    )
    assert config.accessibility.targets["track.lead-vocal"].mixer_index == 2


def test_mixer_index_bound_matches_the_jxa_scripts_max_strips(tmp_path: Path) -> None:
    def raw_with_index(index: int) -> dict[str, object]:
        preset_bytes = b"opaque-cst-fixture"
        raw = valid_mapping()
        raw["accessibility"] = {
            "enabled": True,
            "preset_root": str(tmp_path),
            "presets": [
                {
                    "id": "preset.lead-vocal",
                    "filename": "Lead Vocal.cst",
                    "sha256": hashlib.sha256(preset_bytes).hexdigest(),
                }
            ],
            "targets": [
                {
                    "id": "track.lead-vocal",
                    "mixer_index": index,
                    "expected_accessibility_name": "Lead Vocal",
                }
            ],
        }
        return raw

    config = BridgeConfig.from_mapping(raw_with_index(255))
    assert config.accessibility.targets["track.lead-vocal"].mixer_index == 255

    with pytest.raises(
        ConfigurationError, match="mixer_index must be between 0 and 255"
    ):
        BridgeConfig.from_mapping(raw_with_index(256))


def test_accepts_scoped_cst_plugin_signature_policy(tmp_path: Path) -> None:
    raw = valid_mapping()
    raw["accessibility"] = {
        "enabled": True,
        "preset_root": str(tmp_path),
        "presets": [
            {
                "id": "preset.jimmy-vocal",
                "filename": "Jimmy Vocal Chain.cst",
                "sha256": "a" * 64,
                "expected_plugin_signature": [
                    "Channel EQ",
                    "Compressor",
                    "Gain",
                    "Noise Gate",
                ],
                "signature_match_mode": "subset",
            }
        ],
        "targets": [
            {
                "id": "track.audio-2",
                "mixer_index": 1,
                "expected_accessibility_name": "Audio 2",
            }
        ],
    }

    config = BridgeConfig.from_mapping(raw)
    preset = config.accessibility.presets["preset.jimmy-vocal"]

    assert preset.expected_plugin_signature == (
        "Channel EQ",
        "Compressor",
        "Gain",
        "Noise Gate",
    )
    assert preset.signature_match_mode == "subset"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("expected_plugin_signature", "Channel EQ", "at most 32 names"),
        ("expected_plugin_signature", ["/Users/private"], "safe normalized"),
        ("expected_plugin_signature", [" Bad "], "safe normalized"),
        ("signature_match_mode", "fuzzy", "exact or subset"),
    ],
)
def test_rejects_unsafe_cst_plugin_signature_policy(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    preset: dict[str, object] = {
        "id": "preset.jimmy-vocal",
        "filename": "Jimmy Vocal Chain.cst",
        "sha256": "a" * 64,
        "expected_plugin_signature": ["Channel EQ"],
        "signature_match_mode": "exact",
    }
    preset[field] = value
    raw = valid_mapping()
    raw["accessibility"] = {
        "enabled": True,
        "preset_root": str(tmp_path),
        "presets": [preset],
        "targets": [
            {
                "id": "track.audio-2",
                "mixer_index": 1,
                "expected_accessibility_name": "Audio 2",
            }
        ],
    }

    with pytest.raises(ConfigurationError, match=message):
        BridgeConfig.from_mapping(raw)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("id", "../preset", "lowercase dotted identifier"),
        ("filename", "../Lead Vocal.cst", "filename only"),
        ("filename", "Lead Vocal.logicx", "must end in .cst"),
        ("sha256", "not-a-digest", "64 lowercase hexadecimal"),
    ],
)
def test_rejects_unsafe_cst_preset_entries(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    raw = valid_mapping()
    preset = {
        "id": "preset.lead-vocal",
        "filename": "Lead Vocal.cst",
        "sha256": "0" * 64,
    }
    preset[field] = value
    raw["accessibility"] = {
        "enabled": True,
        "preset_root": str(tmp_path),
        "presets": [preset],
        "targets": [
            {
                "id": "track.lead-vocal",
                "mixer_index": 0,
                "expected_accessibility_name": "Lead Vocal",
            }
        ],
    }

    with pytest.raises(ConfigurationError, match=message):
        BridgeConfig.from_mapping(raw)


def test_cst_fixed_jxa_input_lengths_match_128_character_boundary(
    tmp_path: Path,
) -> None:
    raw = valid_mapping()
    raw["accessibility"] = {
        "enabled": True,
        "preset_root": str(tmp_path),
        "presets": [
            {
                "id": "preset.boundary",
                "filename": f"{'a' * 129}.cst",
                "sha256": "0" * 64,
            }
        ],
        "targets": [
            {
                "id": "track.boundary",
                "mixer_index": 0,
                "expected_accessibility_name": "b" * 129,
            }
        ],
    }

    with pytest.raises(ConfigurationError):
        BridgeConfig.from_mapping(raw)


def test_enabled_cst_loading_requires_both_allowlists(tmp_path: Path) -> None:
    raw = valid_mapping()
    raw["accessibility"] = {
        "enabled": True,
        "preset_root": str(tmp_path),
        "presets": [],
        "targets": [],
    }

    with pytest.raises(ConfigurationError, match="at least one preset"):
        BridgeConfig.from_mapping(raw)


def test_rejects_removed_require_token_switch() -> None:
    raw = valid_mapping()
    raw["server"] = {  # type: ignore[assignment]
        **raw["server"],  # type: ignore[arg-type]
        "require_token": False,
    }

    with pytest.raises(ConfigurationError, match="unknown fields"):
        BridgeConfig.from_mapping(raw)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("read_timeout_seconds", True),
        ("read_timeout_seconds", 0),
        ("read_timeout_seconds", float("inf")),
        ("read_timeout_seconds", "10"),
        ("write_timeout_seconds", True),
        ("write_timeout_seconds", 0),
        ("write_timeout_seconds", 31),
        ("mcp_timeout_seconds", True),
        ("mcp_timeout_seconds", 0),
        ("mcp_timeout_seconds", 301),
        ("max_connections", True),
        ("max_connections", 0),
        ("max_connections", 1_025),
        ("max_connections", 1.5),
    ],
)
def test_rejects_invalid_server_resource_limits(field: str, value: object) -> None:
    raw = valid_mapping()
    raw["server"] = {  # type: ignore[assignment]
        **raw["server"],  # type: ignore[arg-type]
        field: value,
    }

    with pytest.raises(ConfigurationError, match=field):
        BridgeConfig.from_mapping(raw)


def test_enabled_accessibility_requires_distinct_bounded_mcp_deadline(
    tmp_path: Path,
) -> None:
    raw = valid_mapping()
    server = raw["server"]
    assert isinstance(server, dict)
    server["mcp_timeout_seconds"] = 44.9
    raw["accessibility"] = {
        "enabled": True,
        "preset_root": str(tmp_path),
        "timeout_seconds": 20.0,
        "presets": [
            {
                "id": "preset.test",
                "filename": "Test.cst",
                "sha256": "0" * 64,
            }
        ],
        "targets": [
            {
                "id": "track.audio-2",
                "mixer_index": 1,
                "expected_accessibility_name": "Audio 2",
            }
        ],
    }

    with pytest.raises(ConfigurationError, match="mcp_timeout_seconds"):
        BridgeConfig.from_mapping(raw)


def test_wraps_non_missing_config_read_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "bridge.toml"

    real_open = os.open

    def fail_to_open(
        path: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if Path(path) == config_path or (
            dir_fd is not None and Path(path) == Path(config_path.name)
        ):
            raise PermissionError("permission denied")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", fail_to_open)

    with pytest.raises(ConfigurationError, match="could not be read") as raised:
        load_config(config_path)

    assert raised.value.details == {
        "path": str(config_path),
        "error": "PermissionError",
    }


def test_load_config_rejects_a_symlink_without_following_it(tmp_path: Path) -> None:
    target = _copy_example_config(tmp_path)
    link = tmp_path / "linked.toml"
    link.symlink_to(target)

    with pytest.raises(ConfigurationError, match="security policy") as raised:
        load_config(link)

    assert raised.value.details["reason"] == "config_file_symlink_not_allowed"


@pytest.mark.parametrize("mode", [0o664, 0o666, 0o646])
def test_load_config_rejects_group_or_world_writable_file(
    tmp_path: Path,
    mode: int,
) -> None:
    config_path = _copy_example_config(tmp_path)
    config_path.chmod(mode)

    with pytest.raises(ConfigurationError, match="security policy") as raised:
        load_config(config_path)

    assert raised.value.details["reason"] == "config_file_permissions_unsafe"


@pytest.mark.parametrize("mode", [0o720, 0o702, 0o777])
def test_load_config_rejects_attacker_writable_immediate_parent(
    tmp_path: Path,
    mode: int,
) -> None:
    config_path = _copy_example_config(tmp_path)
    tmp_path.chmod(mode)

    try:
        with pytest.raises(ConfigurationError, match="security policy") as raised:
            load_config(config_path)
    finally:
        tmp_path.chmod(0o700)

    assert raised.value.details["reason"] == "config_parent_permissions_unsafe"


def test_load_config_rejects_non_regular_file(tmp_path: Path) -> None:
    config_path = tmp_path / "bridge.toml"
    config_path.mkdir()

    with pytest.raises(ConfigurationError, match="security policy") as raised:
        load_config(config_path)

    assert raised.value.details["reason"] == "config_file_not_regular"


def test_load_config_rejects_file_not_owned_by_current_user(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = _copy_example_config(tmp_path)
    real_fstat = os.fstat

    def wrong_file_owner(fd: int) -> os.stat_result | SimpleNamespace:
        value = real_fstat(fd)
        if stat.S_ISREG(value.st_mode):
            return SimpleNamespace(st_mode=value.st_mode, st_uid=value.st_uid + 1)
        return value

    monkeypatch.setattr(os, "fstat", wrong_file_owner)

    with pytest.raises(ConfigurationError, match="security policy") as raised:
        load_config(config_path)

    assert raised.value.details["reason"] == "config_file_owner_mismatch"


def test_load_config_rejects_parent_not_owned_by_current_user(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = _copy_example_config(tmp_path)
    real_fstat = os.fstat

    def wrong_parent_owner(fd: int) -> os.stat_result | SimpleNamespace:
        value = real_fstat(fd)
        if stat.S_ISDIR(value.st_mode):
            return SimpleNamespace(st_mode=value.st_mode, st_uid=value.st_uid + 1)
        return value

    monkeypatch.setattr(os, "fstat", wrong_parent_owner)

    with pytest.raises(ConfigurationError, match="security policy") as raised:
        load_config(config_path)

    assert raised.value.details["reason"] == "config_parent_owner_mismatch"


def test_load_config_parses_the_already_validated_descriptor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = _copy_example_config(tmp_path)
    original_load = tomllib.load

    def replace_path_after_validation(stream: object) -> dict[str, object]:
        replacement = tmp_path / "replacement.toml"
        replacement.write_text("not valid toml = [", encoding="utf-8")
        replacement.chmod(0o644)
        os.replace(replacement, config_path)
        return original_load(stream)  # type: ignore[arg-type]

    monkeypatch.setattr(tomllib, "load", replace_path_after_validation)

    config = load_config(config_path)

    assert config.server.host == "127.0.0.1"


def test_rejects_non_loopback_bind_address() -> None:
    raw = valid_mapping()
    raw["server"] = {**raw["server"], "host": "0.0.0.0"}  # type: ignore[arg-type]

    with pytest.raises(ConfigurationError, match="loopback"):
        BridgeConfig.from_mapping(raw)


def test_rejects_duplicate_assignment_names() -> None:
    raw = valid_mapping()
    assignments = raw["midi"]["assignments"]  # type: ignore[index]
    assignments.append(dict(assignments[0]))

    with pytest.raises(ConfigurationError, match="Duplicate MIDI assignment"):
        BridgeConfig.from_mapping(raw)


@pytest.mark.parametrize(
    ("field", "value"),
    [("channel", 16), ("control", 128)],
)
def test_rejects_midi_values_outside_protocol_range(field: str, value: int) -> None:
    raw = valid_mapping()
    raw["midi"]["assignments"][0][field] = value  # type: ignore[index]

    with pytest.raises(ConfigurationError, match=field):
        BridgeConfig.from_mapping(raw)
