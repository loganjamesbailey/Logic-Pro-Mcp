from __future__ import annotations

import math
import os
from pathlib import Path

import pytest

from daemon.commands import LogicCommandService
from daemon.config import load_config
from daemon.midi_transport import MidiTransport


_LIVE_GATE = "LOGIC_BRIDGE_RUN_LIVE_SCRIPTER"
_DISPOSABLE_GATE = "LOGIC_BRIDGE_DISPOSABLE_PROJECT"
_PARAMETER_ID_ENV = "LOGIC_BRIDGE_LIVE_SCRIPTER_PARAMETER_ID"
_VALUE_ENV = "LOGIC_BRIDGE_LIVE_SCRIPTER_VALUE"
_ATTESTATION_ENV = "LOGIC_BRIDGE_LIVE_SCRIPTER_ATTESTATION"
_DISPATCH_ENV = "LOGIC_BRIDGE_LIVE_SCRIPTER_DISPATCH"
_ATTESTATION = (
    "protocol v1 installed before Retro Synth on a disposable software-instrument "
    "track; both targets learned; channel 16 CC102/CC103 isolated"
)
_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "bridge.toml"

pytestmark = pytest.mark.skipif(
    os.environ.get(_LIVE_GATE) != "1",
    reason=f"set {_LIVE_GATE}=1 to enable the output-only live Scripter gate",
)


def test_dispatches_one_fixed_scripter_parameter_without_claiming_readback() -> None:
    """Send one allowlisted value and require an honest output-only lifecycle."""
    _require_exact(_DISPOSABLE_GATE, "1")
    _require_exact(_ATTESTATION_ENV, _ATTESTATION)
    parameter_id = _required_environment_value(_PARAMETER_ID_ENV)
    raw_value = _required_environment_value(_VALUE_ENV)
    value = _normalized_value(raw_value)
    dispatch_phrase = (
        f"send {parameter_id} value {raw_value} once to disposable Scripter track"
    )
    _require_exact(_DISPATCH_ENV, dispatch_phrase)

    config = load_config(_CONFIG_PATH)
    if not config.scripter.enabled:
        pytest.fail("the machine-local fixed Scripter profile must be enabled")
    if not config.scripter.operator_attested:
        pytest.fail("the machine-local fixed Scripter profile must be attested")
    if parameter_id not in config.scripter.learned_targets:
        pytest.fail(f"{_PARAMETER_ID_ENV} must select one fixed learned parameter ID")

    midi = MidiTransport(output_name=config.midi.output_name)
    service = LogicCommandService(config=config, midi=midi)
    try:
        result = service.set_scripter_parameter(
            parameter_id=parameter_id,
            value=value,
        )
    finally:
        service.close()

    states = [event["state"] for event in result["events"]]
    assert states == ["requested", "dispatched", "unknown"]
    assert result["events"][-1]["reason"] == (
        "scripter_target_has_no_verified_logic_readback"
    )
    assert all(state not in {"observed", "verified"} for state in states)


def _required_environment_value(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.fail(f"{name} must be set")
    return value


def _require_exact(name: str, expected: str) -> None:
    if os.environ.get(name) != expected:
        pytest.fail(f"{name} must exactly equal {expected!r}")


def _normalized_value(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError:
        pytest.fail(f"{_VALUE_ENV} must be a number from 0.0 through 1.0")
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        pytest.fail(f"{_VALUE_ENV} must be a number from 0.0 through 1.0")
    return value
