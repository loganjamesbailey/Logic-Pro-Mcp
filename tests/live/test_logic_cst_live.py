from __future__ import annotations

import os
from pathlib import Path

import pytest

from daemon.accessibility import AccessibilityCstPresetLoader
from daemon.commands import LogicCommandService
from daemon.config import load_config
from daemon.midi_transport import MidiTransport


_LIVE_GATE = "LOGIC_BRIDGE_RUN_LIVE_CST"
_DISPOSABLE_GATE = "LOGIC_BRIDGE_DISPOSABLE_PROJECT"
_PRESET_ID_ENV = "LOGIC_BRIDGE_LIVE_PRESET_ID"
_TARGET_ID_ENV = "LOGIC_BRIDGE_LIVE_TARGET_ID"
_CONFIRMATION_ENV = "LOGIC_BRIDGE_LIVE_CST_CONFIRMATION"
_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "bridge.toml"

pytestmark = pytest.mark.skipif(
    os.environ.get(_LIVE_GATE) != "1",
    reason=f"set {_LIVE_GATE}=1 to enable the destructive live CST gate",
)


def test_read_only_target_preflight_then_loads_allowlisted_cst_once() -> None:
    """Inspect the exact target, mutate once, inspect after load, and never save."""
    _require_exact(_DISPOSABLE_GATE, "1")
    preset_id = _required_environment_value(_PRESET_ID_ENV)
    target_id = _required_environment_value(_TARGET_ID_ENV)
    confirmation = f"load {preset_id} into {target_id} in disposable project"
    _require_exact(_CONFIRMATION_ENV, confirmation)

    config = load_config(_CONFIG_PATH)
    if not config.accessibility.enabled:
        pytest.fail("the machine-local Accessibility profile must be enabled")
    if preset_id not in config.accessibility.presets:
        pytest.fail(f"{_PRESET_ID_ENV} must select an allowlisted local preset ID")
    if target_id not in config.accessibility.targets:
        pytest.fail(f"{_TARGET_ID_ENV} must select an allowlisted local target ID")

    loader = AccessibilityCstPresetLoader(config=config.accessibility)
    midi = MidiTransport(output_name=config.midi.output_name)
    service = LogicCommandService(config=config, midi=midi, cst_loader=loader)
    try:
        preflight = service.inspect_mixer_target(target_id=target_id)
        if preflight.get("target_id") != target_id:
            pytest.fail("read-only inspection did not resolve the exact target")
        if preflight.get("source") != "macos_accessibility_mixer_inspector_v1":
            pytest.fail("read-only inspection returned an unapproved evidence source")
        if preflight.get("complete") is not True:
            pytest.fail("read-only Mixer inspection was incomplete; mutation refused")

        result = service.load_cst_preset(
            preset_id=preset_id,
            target_id=target_id,
            confirmation=confirmation,
        )
    finally:
        service.close()

    states = [event["state"] for event in result["events"]]
    assert states[:3] == ["requested", "dispatched", "observed"]
    assert states[-1] in {"verified", "unknown"}
    assert states.count("dispatched") == 1
    assert result["preset_id"] == preset_id
    assert result["target_id"] == target_id


def _required_environment_value(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.fail(f"{name} must be set to one configured opaque ID")
    return value


def _require_exact(name: str, expected: str) -> None:
    if os.environ.get(name) != expected:
        pytest.fail(f"{name} must exactly equal {expected!r}")
