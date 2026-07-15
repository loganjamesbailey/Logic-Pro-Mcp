from __future__ import annotations

from datetime import UTC, datetime

import pytest

from daemon.config import CstTargetConfig
from daemon.errors import PresetAutomationFailedError, TargetMismatchError
from daemon.ui_observation import (
    MixerObservation,
    evaluate_plugin_signature,
    parse_mixer_inspection,
)


TARGET = CstTargetConfig(
    id="track.audio-2",
    mixer_index=1,
    expected_accessibility_name="Audio 2",
)
NOW = datetime(2026, 7, 15, 4, 0, tzinfo=UTC)


def test_parse_mixer_inspection_returns_only_scoped_safe_evidence() -> None:
    output = (
        'LOGIC_BRIDGE_INSPECT_V1:{"protocol":1,"mixer_index":1,'
        '"target_matched":true,"complete":true,'
        '"plugins":["Channel EQ","Compressor"]}\n'
    )

    observation = parse_mixer_inspection(output, target=TARGET, observed_at=NOW)

    assert observation == MixerObservation(
        target_id="track.audio-2",
        observed_at=NOW.isoformat(),
        source="macos_accessibility_mixer_inspector_v1",
        verification_scope="visible_plugin_slot_labels",
        plugins=("Channel EQ", "Compressor"),
        complete=True,
    )
    assert observation.public_evidence() == {
        "target_id": "track.audio-2",
        "observed_at": NOW.isoformat(),
        "source": "macos_accessibility_mixer_inspector_v1",
        "verification_scope": "visible_plugin_slot_labels",
        "plugins": ["Channel EQ", "Compressor"],
        "complete": True,
    }


@pytest.mark.parametrize(
    "output",
    [
        "{}",
        'LOGIC_BRIDGE_INSPECT_V1:{"protocol":2,"mixer_index":1,'
        '"target_matched":true,"complete":true,"plugins":[]}',
        'LOGIC_BRIDGE_INSPECT_V1:{"protocol":1,"mixer_index":1,'
        '"target_matched":true,"complete":true,"plugins":[],"raw":"x"}',
        'LOGIC_BRIDGE_INSPECT_V1:{"protocol":1,"mixer_index":1,'
        '"target_matched":true,"complete":true,"plugins":[" Bad "]}',
        'LOGIC_BRIDGE_INSPECT_V1:{"protocol":1,"mixer_index":1,'
        '"target_matched":true,"complete":true,"plugins":["/Users/private"]}',
        'LOGIC_BRIDGE_INSPECT_V1:{"protocol":1,"mixer_index":1,'
        '"target_matched":true,"complete":true,"plugins":["Bad\\nName"]}',
    ],
)
def test_parse_mixer_inspection_rejects_malformed_or_unsafe_output(
    output: str,
) -> None:
    with pytest.raises(PresetAutomationFailedError, match="invalid response"):
        parse_mixer_inspection(output, target=TARGET, observed_at=NOW)


def test_parse_mixer_inspection_rejects_target_index_disagreement() -> None:
    output = (
        'LOGIC_BRIDGE_INSPECT_V1:{"protocol":1,"mixer_index":2,'
        '"target_matched":true,"complete":true,"plugins":[]}'
    )

    with pytest.raises(TargetMismatchError, match="target"):
        parse_mixer_inspection(output, target=TARGET, observed_at=NOW)


def test_signature_evaluation_requires_complete_evidence() -> None:
    complete = MixerObservation(
        target_id="track.audio-2",
        observed_at=NOW.isoformat(),
        source="macos_accessibility_mixer_inspector_v1",
        verification_scope="visible_plugin_slot_labels",
        plugins=("Channel EQ", "Compressor", "Gain"),
        complete=True,
    )
    incomplete = MixerObservation(
        target_id=complete.target_id,
        observed_at=complete.observed_at,
        source=complete.source,
        verification_scope=complete.verification_scope,
        plugins=complete.plugins,
        complete=False,
    )

    assert (
        evaluate_plugin_signature(
            complete,
            expected=("Channel EQ", "Compressor", "Gain"),
            match_mode="exact",
        )
        is True
    )
    assert (
        evaluate_plugin_signature(
            complete,
            expected=("Compressor", "Gain"),
            match_mode="subset",
        )
        is True
    )
    assert (
        evaluate_plugin_signature(
            complete,
            expected=("Gain", "Compressor"),
            match_mode="exact",
        )
        is False
    )
    assert (
        evaluate_plugin_signature(
            incomplete,
            expected=("Compressor",),
            match_mode="subset",
        )
        is False
    )


def test_signature_evaluation_rejects_unknown_match_mode() -> None:
    observation = MixerObservation(
        target_id="track.audio-2",
        observed_at=NOW.isoformat(),
        source="macos_accessibility_mixer_inspector_v1",
        verification_scope="visible_plugin_slot_labels",
        plugins=(),
        complete=True,
    )

    with pytest.raises(ValueError, match="match_mode"):
        evaluate_plugin_signature(observation, expected=(), match_mode="fuzzy")
