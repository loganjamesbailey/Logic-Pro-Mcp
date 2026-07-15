from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_RESOURCES = {
    "bridge_scripts/cleanup_cst.js",
    "bridge_scripts/inspect_mixer.js",
    "bridge_scripts/load_cst.js",
    "scripter/LogicBridgeTargets.js",
}


def test_built_wheel_installs_and_runs_outside_checkout(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    assert uv is not None, "the locked distribution gate requires uv"

    distribution_dir = tmp_path / "dist"
    _run(
        uv,
        "build",
        "--wheel",
        "--out-dir",
        str(distribution_dir),
        cwd=ROOT,
    )
    wheels = list(distribution_dir.glob("*.whl"))
    assert len(wheels) == 1
    wheel = wheels[0]

    with zipfile.ZipFile(wheel) as archive:
        members = set(archive.namelist())
    assert EXPECTED_RESOURCES <= members
    assert not any(
        member.endswith((".cst", ".logicx", ".logicx.zip"))
        or member == "config/bridge.toml"
        for member in members
    )

    environment_dir = tmp_path / "clean-environment"
    _run(uv, "venv", "--python", sys.executable, str(environment_dir), cwd=tmp_path)
    python = environment_dir / "bin" / "python"
    _run(
        uv,
        "pip",
        "install",
        "--python",
        str(python),
        str(wheel),
        cwd=tmp_path,
    )

    outside_checkout = tmp_path / "outside-checkout"
    outside_checkout.mkdir()
    smoke_environment = {**os.environ, "PYTHONPATH": ""}
    smoke = _run(
        str(python),
        "-c",
        (
            "from importlib.resources import files; "
            "import daemon; "
            "root = files('bridge_scripts'); "
            "assert (root / 'load_cst.js').read_text(encoding='utf-8'); "
            "assert (root / 'cleanup_cst.js').read_text(encoding='utf-8'); "
            "assert (root / 'inspect_mixer.js').read_text(encoding='utf-8'); "
            "assert (files('scripter') / 'LogicBridgeTargets.js').read_text(encoding='utf-8'); "
            "print(daemon.__file__)"
        ),
        cwd=outside_checkout,
        env=smoke_environment,
    )
    assert str(ROOT) not in smoke.stdout

    executable = environment_dir / "bin" / "logic-bridge"
    help_result = _run(
        str(executable),
        "--help",
        cwd=outside_checkout,
        env=smoke_environment,
    )
    assert "Local, allowlisted Logic Pro control bridge" in help_result.stdout


def _run(
    *argv: str,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
