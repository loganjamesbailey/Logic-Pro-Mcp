from __future__ import annotations

import math
import os
import time

import pytest


_LIVE_GATE = "LOGIC_BRIDGE_RUN_LIVE_IAC"
_EXPECTED_OUTPUT_ENV = "LOGIC_BRIDGE_MIDI_OUTPUT"
_OBSERVATION_SECONDS_ENV = "LOGIC_BRIDGE_IAC_QUIET_SECONDS"
_ATTESTATION_ENV = "LOGIC_BRIDGE_IAC_SENTINEL_ATTESTATION"
_ATTESTATION = "send one unassigned channel 16 cc 119 sentinel"

pytestmark = pytest.mark.skipif(
    os.environ.get(_LIVE_GATE) != "1",
    reason=f"set {_LIVE_GATE}=1 to run the output-only live IAC gate",
)


def test_iac_endpoint_delivers_once_without_traffic_induced_echo() -> None:
    """Send one operator-attested sentinel and reject missing/repeated delivery."""
    import mido

    assert os.environ.get(_ATTESTATION_ENV) == _ATTESTATION, (
        f"set {_ATTESTATION_ENV} exactly to {_ATTESTATION!r} only after confirming "
        "that channel 16 CC119 is unassigned in Logic and every connected MIDI app"
    )

    expected = os.environ.get(
        _EXPECTED_OUTPUT_ENV,
        "IAC Driver AI_Logic_Bridge",
    )
    observation_seconds = _bounded_observation_seconds(
        os.environ.get(_OBSERVATION_SECONDS_ENV, "0.25")
    )
    backend = mido.Backend("mido.backends.rtmidi")

    assert expected in backend.get_output_names()
    assert expected in backend.get_input_names()

    observed_before: list[object] = []
    observed_after: list[object] = []
    input_port = backend.open_input(expected)
    output_port = backend.open_output(expected)
    try:
        deadline = time.monotonic() + observation_seconds
        while time.monotonic() < deadline:
            observed_before.extend(input_port.iter_pending())
            time.sleep(0.01)
        assert observed_before == [], (
            "The IAC bridge was not quiet before the sentinel; inspect routing "
            "before sending any bridge action"
        )

        sentinel = mido.Message(
            "control_change",
            channel=15,
            control=119,
            value=1,
        )
        output_port.send(sentinel)
        deadline = time.monotonic() + observation_seconds
        while time.monotonic() < deadline:
            observed_after.extend(input_port.iter_pending())
            time.sleep(0.01)
    finally:
        output_port.close()
        input_port.close()

    matching = [
        message
        for message in observed_after
        if getattr(message, "type", None) == "control_change"
        and getattr(message, "channel", None) == 15
        and getattr(message, "control", None) == 119
        and getattr(message, "value", None) == 1
    ]
    assert len(matching) == 1, (
        "Expected exactly one IAC sentinel delivery; zero means routing failed and "
        "more than one means traffic triggered a feedback loop"
    )
    assert len(observed_after) == 1, (
        "Unexpected MIDI traffic appeared after the sentinel; isolate the IAC route"
    )


def _bounded_observation_seconds(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError:
        pytest.fail(f"{_OBSERVATION_SECONDS_ENV} must be a number")
    if not math.isfinite(value) or not 0.05 <= value <= 1.0:
        pytest.fail(f"{_OBSERVATION_SECONDS_ENV} must be between 0.05 and 1.0 seconds")
    return value
