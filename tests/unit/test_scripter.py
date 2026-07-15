from __future__ import annotations

import math

import pytest

from daemon.errors import InvalidParametersError, UnknownActionError
from daemon.scripter import (
    SCRIPT_SHA256,
    fixed_parameter_metadata,
    packaged_script_sha256,
    resolve_scripter_dispatch,
)


@pytest.mark.parametrize(
    ("parameter_id", "normalized", "control", "midi_value"),
    [
        ("scripter.retro-synth.filter-cutoff", 0.0, 102, 0),
        ("scripter.retro-synth.filter-cutoff", 0.5, 102, 64),
        ("scripter.retro-synth.filter-resonance", 1.0, 103, 127),
    ],
)
def test_fixed_scripter_routes_use_channel_16_only(
    parameter_id: str,
    normalized: float,
    control: int,
    midi_value: int,
) -> None:
    dispatch = resolve_scripter_dispatch(parameter_id, normalized)

    assert dispatch.parameter_id == parameter_id
    assert dispatch.channel == 15
    assert dispatch.control == control
    assert dispatch.value == midi_value


def test_scripter_public_metadata_is_fixed_and_path_free() -> None:
    metadata = fixed_parameter_metadata()

    assert metadata == [
        {
            "id": "scripter.retro-synth.filter-cutoff",
            "kind": "scripter_target",
            "value_mode": "normalized",
            "target": "Retro Synth Filter Cutoff",
        },
        {
            "id": "scripter.retro-synth.filter-resonance",
            "kind": "scripter_target",
            "value_mode": "normalized",
            "target": "Filter Resonance",
        },
    ]
    assert all("path" not in item and "script" not in item for item in metadata)


@pytest.mark.parametrize("value", [True, -0.01, 1.01, math.nan, math.inf, "1"])
def test_scripter_rejects_invalid_normalized_values(value: object) -> None:
    with pytest.raises(InvalidParametersError, match="between 0.0 and 1.0"):
        resolve_scripter_dispatch("scripter.retro-synth.filter-cutoff", value)


def test_scripter_rejects_a_third_parameter() -> None:
    with pytest.raises(UnknownActionError, match="not allowlisted") as raised:
        resolve_scripter_dispatch("scripter.retro-synth.oscillator-shape", 0.5)

    assert raised.value.details == {
        "parameter_id": "scripter.retro-synth.oscillator-shape"
    }


def test_published_hash_matches_the_packaged_fixed_source() -> None:
    assert packaged_script_sha256() == SCRIPT_SHA256
