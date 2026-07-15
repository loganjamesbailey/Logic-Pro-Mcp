from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from daemon.config import CstTargetConfig
from daemon.errors import PresetAutomationFailedError, TargetMismatchError

_MARKER = "LOGIC_BRIDGE_INSPECT_V1:"
_MAX_OUTPUT_BYTES = 16_384
_MAX_PLUGINS = 32
_MAX_PLUGIN_NAME_LENGTH = 128
_PAYLOAD_KEYS = {
    "protocol",
    "mixer_index",
    "target_matched",
    "complete",
    "plugins",
}


@dataclass(frozen=True, slots=True)
class MixerObservation:
    target_id: str
    observed_at: str
    source: str
    verification_scope: str
    plugins: tuple[str, ...]
    complete: bool

    def public_evidence(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "observed_at": self.observed_at,
            "source": self.source,
            "verification_scope": self.verification_scope,
            "plugins": list(self.plugins),
            "complete": self.complete,
        }


def parse_mixer_inspection(
    output: str,
    *,
    target: CstTargetConfig,
    observed_at: datetime,
) -> MixerObservation:
    if not isinstance(output, str):
        raise _invalid_output()
    if len(output.encode("utf-8", errors="replace")) > _MAX_OUTPUT_BYTES:
        raise _invalid_output()
    if output.endswith("\r\n"):
        output = output[:-2]
    elif output.endswith("\n"):
        output = output[:-1]
    if not output.startswith(_MARKER):
        raise _invalid_output()
    encoded_payload = output[len(_MARKER) :]
    try:
        decoder = json.JSONDecoder()
        payload, end = decoder.raw_decode(encoded_payload)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise _invalid_output() from exc
    if end != len(encoded_payload):
        raise _invalid_output()
    if not isinstance(payload, dict) or set(payload) != _PAYLOAD_KEYS:
        raise _invalid_output()
    protocol = payload["protocol"]
    mixer_index = payload["mixer_index"]
    target_matched = payload["target_matched"]
    complete = payload["complete"]
    plugins = payload["plugins"]
    if protocol != 1 or isinstance(protocol, bool):
        raise _invalid_output()
    if (
        isinstance(mixer_index, bool)
        or not isinstance(mixer_index, int)
        or mixer_index != target.mixer_index
        or target_matched is not True
    ):
        raise TargetMismatchError(
            "The configured Mixer target did not match the inspection target"
        )
    if not isinstance(complete, bool) or not isinstance(plugins, list):
        raise _invalid_output()
    if len(plugins) > _MAX_PLUGINS:
        raise _invalid_output()
    normalized_plugins: list[str] = []
    for plugin in plugins:
        if (
            not isinstance(plugin, str)
            or not plugin
            or plugin != plugin.strip()
            or len(plugin) > _MAX_PLUGIN_NAME_LENGTH
            or any(ord(character) < 32 or ord(character) == 127 for character in plugin)
            or "/" in plugin
            or "\\" in plugin
        ):
            raise _invalid_output()
        normalized_plugins.append(plugin)
    return MixerObservation(
        target_id=target.id,
        observed_at=observed_at.isoformat(),
        source="macos_accessibility_mixer_inspector_v1",
        verification_scope="visible_plugin_slot_labels",
        plugins=tuple(normalized_plugins),
        complete=complete,
    )


def evaluate_plugin_signature(
    observation: MixerObservation,
    *,
    expected: Sequence[str],
    match_mode: str,
) -> bool:
    if match_mode not in {"exact", "subset"}:
        raise ValueError("match_mode must be exact or subset")
    if not observation.complete:
        return False
    expected_plugins = tuple(expected)
    if match_mode == "exact":
        return observation.plugins == expected_plugins
    expected_counts = Counter(expected_plugins)
    observed_counts = Counter(observation.plugins)
    return all(
        observed_counts[plugin] >= count for plugin, count in expected_counts.items()
    )


def _invalid_output() -> PresetAutomationFailedError:
    return PresetAutomationFailedError(
        "The Mixer inspection returned an invalid response"
    )
