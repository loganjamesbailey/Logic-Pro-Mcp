from __future__ import annotations

import pytest

from daemon.errors import InvalidParametersError
from daemon.rpc_contract import _validate_value


def test_const_schema_accepts_a_decoded_value_equal_to_the_constant() -> None:
    # JSON-decoded values are never the same object as a schema literal, so
    # this must compare by value, not identity.
    _validate_value("mode", "absolute", {"const": "absolute"})
    _validate_value("mode", "".join(["abs", "olute"]), {"const": "absolute"})
    _validate_value("count", 7, {"const": 7})


def test_const_schema_rejects_a_mismatched_value() -> None:
    with pytest.raises(InvalidParametersError):
        _validate_value("mode", "relative", {"const": "absolute"})


def test_const_schema_does_not_conflate_booleans_with_equal_integers() -> None:
    with pytest.raises(InvalidParametersError):
        _validate_value("flag", True, {"const": 1})
    with pytest.raises(InvalidParametersError):
        _validate_value("flag", False, {"const": 0})
    with pytest.raises(InvalidParametersError):
        _validate_value("count", 1, {"const": True})
