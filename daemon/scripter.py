from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from importlib.resources import files
from types import MappingProxyType

from daemon.errors import InvalidParametersError, UnknownActionError

SCRIPT_FILENAME = "LogicBridgeTargets.js"
SCRIPT_SHA256 = "1452add3dc76a19223fb6301e39ffd9c4c3c8c5915f072ae3ee029ffd1a563ab"
MIDO_CHANNEL = 15


@dataclass(frozen=True, slots=True)
class ScripterParameter:
    id: str
    control: int
    target: str

    def public_metadata(self) -> dict[str, str]:
        return {
            "id": self.id,
            "kind": "scripter_target",
            "value_mode": "normalized",
            "target": self.target,
        }


@dataclass(frozen=True, slots=True)
class ScripterDispatch:
    parameter_id: str
    channel: int
    control: int
    value: int


_PARAMETERS = (
    ScripterParameter(
        id="scripter.retro-synth.filter-cutoff",
        control=102,
        target="Retro Synth Filter Cutoff",
    ),
    ScripterParameter(
        id="scripter.retro-synth.filter-resonance",
        control=103,
        target="Filter Resonance",
    ),
)
PARAMETERS = MappingProxyType({parameter.id: parameter for parameter in _PARAMETERS})


def fixed_parameter_metadata() -> list[dict[str, str]]:
    return [parameter.public_metadata() for parameter in _PARAMETERS]


def packaged_script_sha256() -> str:
    source = files("scripter").joinpath(SCRIPT_FILENAME).read_bytes()
    return hashlib.sha256(source).hexdigest()


def resolve_scripter_dispatch(
    parameter_id: object,
    value: object,
) -> ScripterDispatch:
    if not isinstance(parameter_id, str) or not parameter_id:
        raise InvalidParametersError("parameter_id must be a non-empty string")
    parameter = PARAMETERS.get(parameter_id)
    if parameter is None:
        raise UnknownActionError(
            "Scripter parameter is not allowlisted",
            details={"parameter_id": parameter_id},
        )
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0.0 <= value <= 1.0
    ):
        raise InvalidParametersError("value must be a number between 0.0 and 1.0")
    return ScripterDispatch(
        parameter_id=parameter.id,
        channel=MIDO_CHANNEL,
        control=parameter.control,
        value=round(float(value) * 127),
    )
