from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from daemon.accessibility import _PACKAGED_JXA_SHA256


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "bridge_scripts"
SCRIPT_NAMES = ("load_cst.js", "cleanup_cst.js", "inspect_mixer.js")


def _source(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", SCRIPT_NAMES)
def test_fixed_jxa_resources_compile(name: str, tmp_path: Path) -> None:
    compiler = shutil.which("osacompile")
    if compiler is None:
        pytest.skip("osacompile is available only on macOS")

    result = subprocess.run(
        [
            compiler,
            "-l",
            "JavaScript",
            "-o",
            str(tmp_path / f"{name}.scpt"),
            str(SCRIPTS / name),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name", SCRIPT_NAMES)
def test_fixed_jxa_resources_pin_bounded_mixer_contract(name: str) -> None:
    source = _source(name)

    assert "MAX_MIXER_DEPTH = 3" in source
    assert "MAX_STRIPS = 256" in source
    assert "MAX_DIRECT_ELEMENTS = 64" in source
    assert "MAX_TARGET_EVIDENCE = 8" in source
    if name == "load_cst.js":
        # Per-phase selector-read budgets, not one shared counter: a phase
        # that starves the popup-dismissal reserve is exactly the failure
        # this pin exists to catch.
        assert "PREPRESS_TIMEOUT: 400" in source
        assert "POPUP_ITEM_TIMEOUT: 1200" in source
        assert "DISMISS_TIMEOUT: 60" in source
        assert "selectorReadsByPhase[budgetCode] = reads" in source
    elif name == "inspect_mixer.js":
        assert "MAX_SELECTOR_READS = 400" in source
        assert "selectorReads += 1" in source
    else:
        assert "MAX_SELECTOR_READS = 128" in source
        assert "selectorReads += 1" in source
    assert ".uiElements.whose(" in source
    assert ".buttons.whose(" in source
    assert "findAll" not in source
    assert "maximumDepth" not in source
    assert source.count("const named = records.filter") == 1
    assert source.count("_or: [") == 1
    # The global uniqueness pass (expectedTarget=null) must run exactly once,
    # regardless of whether resolveTarget takes 2 args (inspect_mixer.js,
    # cleanup_cst.js) or 4 (load_cst.js, where `null` lands on its own line).
    null_terminated_resolves = re.findall(
        r"resolveTarget\(\s*logic\s*,[^)]*?\bnull\s*\)", source, re.DOTALL
    )
    assert len(null_terminated_resolves) == 1


def test_packaged_jxa_digest_pins_match_reviewed_source_bytes() -> None:
    assert set(_PACKAGED_JXA_SHA256) == set(SCRIPT_NAMES)
    for name in SCRIPT_NAMES:
        assert (
            _PACKAGED_JXA_SHA256[name]
            == hashlib.sha256((SCRIPTS / name).read_bytes()).hexdigest()
        )


def test_jxa_accessibility_behavior_harness() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the deterministic JXA harness")

    result = subprocess.run(
        [node, str(ROOT / "tests/js/jxa_accessibility_harness.js")],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert "11 scenarios passed" in result.stdout


def test_loader_pins_phase_budgets_and_exactly_two_mutations() -> None:
    source = _source("load_cst.js")

    assert "PREPRESS_BUDGET_MS = 5000" in source
    assert "POPUP_ITEM_BUDGET_MS = 8000" in source
    assert "DISMISS_BUDGET_MS = 3000" in source
    assert source.count('actions.byName("AXPress").perform()') == 2
    assert ".click(" not in source
    assert "System Events" in source


def test_cleanup_is_independently_bounded_and_only_cancels_attributed_popup() -> None:
    source = _source("cleanup_cst.js")

    assert "DISMISS_BUDGET_MS = 3000" in source
    assert "AXPress" not in source
    assert source.count('actions.byName("AXCancel")') == 1
    assert ".click(" not in source


def test_inspector_is_read_only_and_emits_only_versioned_machine_payload() -> None:
    source = _source("inspect_mixer.js")

    assert "LOGIC_BRIDGE_INSPECT_V1:" in source
    assert "frontmost" not in source
    assert "AXPress" not in source
    assert "AXCancel" not in source
    assert ".perform()" not in source
    assert ".click(" not in source
    assert re.search(r"return\s+INSPECT_MARKER\s*\+\s*JSON\.stringify", source)


@pytest.mark.parametrize("name", SCRIPT_NAMES)
def test_jxa_inputs_remain_fixed_and_never_accept_execution_material(name: str) -> None:
    source = _source(name)

    assert "argv[3]" not in source
    assert "eval(" not in source
    assert "Function(" not in source
    assert "doShellScript" not in source
    assert "currentApplication" not in source
    assert "Path(" not in source
