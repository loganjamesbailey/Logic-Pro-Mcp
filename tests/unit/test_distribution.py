from __future__ import annotations

import tomllib
from pathlib import Path


def test_wheel_declares_all_runtime_resource_packages() -> None:
    with Path("pyproject.toml").open("rb") as stream:
        pyproject = tomllib.load(stream)

    packages = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]

    assert packages == ["daemon", "bridge_scripts", "scripter"]
    assert Path("scripter/__init__.py").is_file()
    assert Path("scripter/LogicBridgeTargets.js").is_file()
