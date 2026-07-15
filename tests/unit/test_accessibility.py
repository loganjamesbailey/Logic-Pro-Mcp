from __future__ import annotations

import hashlib
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from daemon.accessibility import (
    AccessibilityCstPresetLoader,
    _ProcessGroupRunner,
    accessibility_status,
    cst_resource_status,
)
from daemon.config import BridgeConfig
from daemon.errors import (
    AccessibilityPermissionRequiredError,
    MixerWindowNotFoundError,
    PresetAutomationFailedError,
    PresetIntegrityMismatchError,
    PresetLoadTimeoutError,
    TargetMismatchError,
    UiOperationBusyError,
)
from tests.fakes import valid_mapping


_TEST_JXA_SOURCES = {
    "load_cst.js": b"function run(argv) { return 'load'; }\n",
    "cleanup_cst.js": b"function run(argv) { return 'cleanup'; }\n",
    "inspect_mixer.js": b"function run(argv) { return 'inspect'; }\n",
}


def _test_resource_hashes(root: Path) -> dict[str, str]:
    return {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in _TEST_JXA_SOURCES
    }


def test_non_macos_platform_reports_unsupported_without_running_probe() -> None:
    called = False

    def probe() -> bool:
        nonlocal called
        called = True
        return True

    status = accessibility_status(platform_name="linux", trust_probe=probe)

    assert status == {
        "supported": False,
        "trusted": False,
        "reason": "macos_accessibility_api_unavailable",
    }
    assert called is False


def test_macos_platform_reports_probe_result() -> None:
    status = accessibility_status(platform_name="darwin", trust_probe=lambda: True)

    assert status == {"supported": True, "trusted": True, "reason": None}


def _loader_fixture(
    tmp_path: Path,
    *,
    runner: Any,
    trust_probe: Any = lambda: True,
    script_sources: dict[str, bytes] | None = None,
    timeout_seconds: float = 3.0,
) -> tuple[AccessibilityCstPresetLoader, object, object, Path]:
    root = tmp_path / "presets"
    root.mkdir()
    contents = b"opaque channel strip bytes"
    preset_path = root / "Lead Vocal.cst"
    preset_path.write_bytes(contents)
    sources = {**_TEST_JXA_SOURCES, **(script_sources or {})}
    for filename, source in sources.items():
        (tmp_path / filename).write_bytes(source)
    script_path = tmp_path / "load_cst.js"
    raw = valid_mapping()
    raw["accessibility"] = {
        "enabled": True,
        "preset_root": str(root),
        "timeout_seconds": timeout_seconds,
        "max_cst_bytes": 1024,
        "presets": [
            {
                "id": "preset.lead-vocal",
                "filename": preset_path.name,
                "sha256": hashlib.sha256(contents).hexdigest(),
            }
        ],
        "targets": [
            {
                "id": "track.lead-vocal",
                "mixer_index": 2,
                "expected_accessibility_name": "Lead Vocal",
            }
        ],
    }
    config = BridgeConfig.from_mapping(raw)
    loader = AccessibilityCstPresetLoader(
        config=config.accessibility,
        trust_probe=trust_probe,
        runner=runner,
        script_path=script_path,
        environ={"HOME": str(tmp_path), "LOGIC_BRIDGE_TOKEN": "must-not-leak"},
        lock_path=tmp_path / "runtime" / "ui-mutation.lock",
        resource_sha256=_test_resource_hashes(tmp_path),
    )
    return (
        loader,
        config.accessibility.presets["preset.lead-vocal"],
        config.accessibility.targets["track.lead-vocal"],
        preset_path,
    )


def test_loader_uses_fixed_osascript_argv_and_sanitized_environment(
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                '{"status":"observed",'
                '"evidence":"preset_menu_item_pressed_and_popup_dismissed"}\n'
            ),
            stderr="",
        )

    loader, preset, target, preset_path = _loader_fixture(tmp_path, runner=runner)

    observation = loader.load_preset(preset=preset, target=target)  # type: ignore[arg-type]

    argv, kwargs = calls[0]
    assert argv[:3] == ["/usr/bin/osascript", "-l", "JavaScript"]
    assert argv[3] == "-"
    assert argv[-3:] == ["Lead Vocal", "2", "Lead Vocal"]
    assert str(preset_path) not in argv
    assert str(tmp_path / "load_cst.js") not in argv
    assert kwargs["shell"] is False
    assert kwargs["input"] == _TEST_JXA_SOURCES["load_cst.js"]
    assert kwargs["text"] is False
    assert kwargs["timeout"] == 3.0
    assert kwargs["start_new_session"] is True
    assert "LOGIC_BRIDGE_TOKEN" not in kwargs["env"]
    assert observation.evidence == "preset_menu_item_pressed_and_popup_dismissed"


def test_loader_attaches_scoped_post_load_mixer_inspection(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if kwargs["input"] == _TEST_JXA_SOURCES["inspect_mixer.js"]:
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=(
                    'LOGIC_BRIDGE_INSPECT_V1:{"protocol":1,"mixer_index":2,'
                    '"target_matched":true,"complete":true,'
                    '"plugins":["Channel EQ","Compressor"]}\n'
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                '{"status":"observed",'
                '"evidence":"preset_menu_item_pressed_and_popup_dismissed"}'
            ),
            stderr="",
        )

    loader, preset, target, _ = _loader_fixture(tmp_path, runner=runner)
    observation = loader.load_preset(preset=preset, target=target)  # type: ignore[arg-type]

    assert len(calls) == 2
    assert all(call[3] == "-" for call in calls)
    assert observation.inspection is not None
    assert observation.inspection.target_id == "track.lead-vocal"
    assert observation.inspection.plugins == ("Channel EQ", "Compressor")
    assert observation.inspection_reason is None


def test_failed_post_load_inspection_preserves_observed_mutation_boundary(
    tmp_path: Path,
) -> None:
    def runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if kwargs["input"] == _TEST_JXA_SOURCES["inspect_mixer.js"]:
            return subprocess.CompletedProcess(argv, 0, stdout="invalid", stderr="")
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                '{"status":"observed",'
                '"evidence":"preset_menu_item_pressed_and_popup_dismissed"}'
            ),
            stderr="",
        )

    loader, preset, target, _ = _loader_fixture(tmp_path, runner=runner)
    observation = loader.load_preset(preset=preset, target=target)  # type: ignore[arg-type]

    assert observation.evidence == "preset_menu_item_pressed_and_popup_dismissed"
    assert observation.inspection is None
    assert observation.inspection_reason == "PRESET_AUTOMATION_FAILED"


def test_loader_survives_a_failing_cross_process_lock_release(tmp_path: Path) -> None:
    def runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if kwargs["input"] == _TEST_JXA_SOURCES["inspect_mixer.js"]:
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=(
                    'LOGIC_BRIDGE_INSPECT_V1:{"protocol":1,"mixer_index":2,'
                    '"target_matched":true,"complete":false,"plugins":[]}\n'
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                '{"status":"observed",'
                '"evidence":"preset_menu_item_pressed_and_popup_dismissed"}'
            ),
            stderr="",
        )

    loader, preset, target, _ = _loader_fixture(tmp_path, runner=runner)

    class _FailingReleaseLock:
        def acquire(self) -> bool:
            return True

        def release(self) -> None:
            raise OSError("stale lock handle")

    loader._cross_process_lock = _FailingReleaseLock()  # type: ignore[assignment]

    # The failing OS-level unlock must not mask the successful result, and
    # must not leave the in-process mutation lock held: a second call must
    # not raise UiOperationBusyError.
    loader.load_preset(preset=preset, target=target)  # type: ignore[arg-type]
    loader.load_preset(preset=preset, target=target)  # type: ignore[arg-type]


def test_read_only_inspector_uses_fixed_target_arguments(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                'LOGIC_BRIDGE_INSPECT_V1:{"protocol":1,"mixer_index":2,'
                '"target_matched":true,"complete":false,"plugins":[]}\n'
            ),
            stderr="",
        )

    loader, _, target, _ = _loader_fixture(tmp_path, runner=runner)
    observation = loader.inspect_target(target=target)  # type: ignore[arg-type]

    assert calls[0][:3] == ["/usr/bin/osascript", "-l", "JavaScript"]
    assert calls[0][3] == "-"
    assert calls[0][-2:] == ["2", "Lead Vocal"]
    assert observation.target_id == "track.lead-vocal"
    assert observation.complete is False


def test_loader_requires_accessibility_trust_before_subprocess(tmp_path: Path) -> None:
    called = False

    def runner(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        raise AssertionError("runner must not execute")

    loader, preset, target, _ = _loader_fixture(
        tmp_path,
        runner=runner,
        trust_probe=lambda: False,
    )

    with pytest.raises(AccessibilityPermissionRequiredError):
        loader.load_preset(preset=preset, target=target)  # type: ignore[arg-type]

    assert called is False


def test_loader_rechecks_preset_integrity_on_every_invocation(tmp_path: Path) -> None:
    called = False

    def runner(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        raise AssertionError("runner must not execute")

    loader, preset, target, preset_path = _loader_fixture(tmp_path, runner=runner)
    preset_path.write_bytes(b"modified after configuration")

    with pytest.raises(PresetIntegrityMismatchError):
        loader.load_preset(preset=preset, target=target)  # type: ignore[arg-type]

    assert called is False


def test_loader_executes_cached_script_bytes_after_path_is_replaced(
    tmp_path: Path,
) -> None:
    inputs: list[bytes] = []

    def runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        inputs.append(kwargs["input"])
        if kwargs["input"] == _TEST_JXA_SOURCES["inspect_mixer.js"]:
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=(
                    'LOGIC_BRIDGE_INSPECT_V1:{"protocol":1,"mixer_index":2,'
                    '"target_matched":true,"complete":false,"plugins":[]}\n'
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                '{"status":"observed",'
                '"evidence":"preset_menu_item_pressed_and_popup_dismissed"}'
            ),
            stderr="",
        )

    loader, preset, target, _ = _loader_fixture(tmp_path, runner=runner)
    for filename in _TEST_JXA_SOURCES:
        path = tmp_path / filename
        path.unlink()
        path.write_bytes(b"tampered after descriptor verification\n")

    status = loader.status()
    loader.load_preset(preset=preset, target=target)  # type: ignore[arg-type]

    assert status["script_ready"] is True
    assert inputs == [
        _TEST_JXA_SOURCES["load_cst.js"],
        _TEST_JXA_SOURCES["inspect_mixer.js"],
    ]


def test_loader_fails_closed_when_script_bytes_do_not_match_pinned_digest(
    tmp_path: Path,
) -> None:
    def runner(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise AssertionError("unverified JXA must never execute")

    first_loader, preset, target, _ = _loader_fixture(tmp_path, runner=runner)
    expected_hashes = {
        name: hashlib.sha256(source).hexdigest()
        for name, source in _TEST_JXA_SOURCES.items()
    }
    (tmp_path / "load_cst.js").write_bytes(b"tampered before validation\n")
    loader = AccessibilityCstPresetLoader(
        config=first_loader.config,
        trust_probe=lambda: True,
        runner=runner,
        script_path=tmp_path / "load_cst.js",
        environ={"HOME": str(tmp_path)},
        lock_path=tmp_path / "runtime-2" / "ui-mutation.lock",
        resource_sha256=expected_hashes,
    )

    assert loader.status()["script_ready"] is False
    with pytest.raises(PresetAutomationFailedError):
        loader.load_preset(preset=preset, target=target)  # type: ignore[arg-type]


def test_module_and_cached_resource_status_payloads_match_shape(
    tmp_path: Path,
) -> None:
    def runner(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise AssertionError("resource status must not dispatch JXA")

    loader, _preset, _target, preset_path = _loader_fixture(tmp_path, runner=runner)

    direct = cst_resource_status(loader.config)
    cached = loader._cached_cst_resource_status()

    assert direct == cached
    assert direct["ok"] is True
    assert direct["available_ids"] == ["preset.lead-vocal"]

    preset_path.write_bytes(b"tampered after fixture setup")

    direct_after_tamper = cst_resource_status(loader.config)
    cached_after_tamper = loader._cached_cst_resource_status()

    assert direct_after_tamper == cached_after_tamper
    assert direct_after_tamper["ok"] is False
    assert direct_after_tamper["unavailable_ids"] == [
        {"id": "preset.lead-vocal", "reason": "PRESET_INTEGRITY_MISMATCH"}
    ]


@pytest.mark.parametrize(
    "unsafe_case",
    ["symlink", "writable", "invalid_utf8", "oversized"],
)
def test_loader_rejects_unsafe_fixed_script_resources(
    tmp_path: Path,
    unsafe_case: str,
) -> None:
    def runner(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise AssertionError("unsafe JXA must never execute")

    first_loader, preset, target, _ = _loader_fixture(tmp_path, runner=runner)
    load_path = tmp_path / "load_cst.js"
    if unsafe_case == "symlink":
        load_path.unlink()
        load_path.symlink_to(tmp_path / "cleanup_cst.js")
    elif unsafe_case == "writable":
        load_path.chmod(0o666)
    elif unsafe_case == "invalid_utf8":
        load_path.write_bytes(b"\xff\xfe")
    else:
        load_path.write_bytes(b"x" * 262_145)
    expected_hashes = _test_resource_hashes(tmp_path)
    if unsafe_case == "symlink":
        expected_hashes["load_cst.js"] = expected_hashes["cleanup_cst.js"]
    loader = AccessibilityCstPresetLoader(
        config=first_loader.config,
        trust_probe=lambda: True,
        runner=runner,
        script_path=load_path,
        environ={"HOME": str(tmp_path)},
        lock_path=tmp_path / "unsafe-runtime" / "ui-mutation.lock",
        resource_sha256=expected_hashes,
    )

    assert loader.status()["script_ready"] is False
    with pytest.raises(PresetAutomationFailedError):
        loader.load_preset(preset=preset, target=target)  # type: ignore[arg-type]


def test_loader_timeout_is_non_retryable_and_marks_dispatch_started(
    tmp_path: Path,
) -> None:
    def runner(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(argv, timeout=3.0)

    loader, preset, target, _ = _loader_fixture(tmp_path, runner=runner)

    with pytest.raises(PresetLoadTimeoutError) as caught:
        loader.load_preset(preset=preset, target=target)  # type: ignore[arg-type]

    assert caught.value.retryable is False
    assert caught.value.details == {"dispatch_started": True}


def test_loader_maps_stable_script_error_without_raw_stderr(tmp_path: Path) -> None:
    def runner(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv,
            1,
            stdout="",
            stderr=(
                "execution error: Error: "
                "LOGIC_BRIDGE:PRE_DISPATCH:MIXER_WINDOW_NOT_FOUND "
                "private/path/session-name (-2700)"
            ),
        )

    loader, preset, target, _ = _loader_fixture(tmp_path, runner=runner)

    with pytest.raises(MixerWindowNotFoundError) as caught:
        loader.load_preset(preset=preset, target=target)  # type: ignore[arg-type]

    rendered = repr(caught.value.to_rpc_data())
    assert caught.value.details == {}
    assert "private/path" not in rendered
    assert "session-name" not in rendered


def test_structured_marker_outranks_a_coincidental_permission_substring(
    tmp_path: Path,
) -> None:
    def runner(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv,
            1,
            stdout="",
            stderr=(
                "execution error: Error: "
                "LOGIC_BRIDGE:PRE_DISPATCH:TARGET_MISMATCH "
                # A window title or coordinate that happens to contain the
                # -1719 permission-error substring must not override the
                # structured marker that was already parsed above.
                "at window position -1719,200 (-2700)"
            ),
        )

    loader, preset, target, _ = _loader_fixture(tmp_path, runner=runner)

    with pytest.raises(TargetMismatchError):
        loader.load_preset(preset=preset, target=target)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("phase", "expected_details"),
    [
        ("PRE_DISPATCH", {}),
        ("POST_DISPATCH", {"dispatch_started": True}),
    ],
)
def test_loader_preserves_destructive_preset_press_phase(
    tmp_path: Path,
    phase: str,
    expected_details: dict[str, bool],
) -> None:
    def runner(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv,
            1,
            stdout="",
            stderr=(
                "execution error: Error: "
                f"LOGIC_BRIDGE:{phase}:PRESET_AUTOMATION_FAILED (-2700)"
            ),
        )

    loader, preset, target, _ = _loader_fixture(tmp_path, runner=runner)

    with pytest.raises(PresetAutomationFailedError) as caught:
        loader.load_preset(preset=preset, target=target)  # type: ignore[arg-type]

    assert caught.value.details == expected_details


def test_loader_serializes_only_ui_mutations(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()

    def runner(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        entered.set()
        assert release.wait(timeout=2)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                '{"status":"observed",'
                '"evidence":"preset_menu_item_pressed_and_popup_dismissed"}'
            ),
            stderr="",
        )

    loader, preset, target, _ = _loader_fixture(tmp_path, runner=runner)
    first_error: list[BaseException] = []

    def first_load() -> None:
        try:
            loader.load_preset(preset=preset, target=target)  # type: ignore[arg-type]
        except BaseException as exc:  # pragma: no cover - assertion aid
            first_error.append(exc)

    thread = threading.Thread(target=first_load)
    thread.start()
    assert entered.wait(timeout=2)
    try:
        with pytest.raises(UiOperationBusyError):
            loader.load_preset(preset=preset, target=target)  # type: ignore[arg-type]
    finally:
        release.set()
        thread.join(timeout=2)

    assert first_error == []


def test_two_loader_instances_share_a_cross_process_ui_lock(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()

    def first_runner(
        argv: list[str],
        **_kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        entered.set()
        assert release.wait(timeout=2)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                '{"status":"observed",'
                '"evidence":"preset_menu_item_pressed_and_popup_dismissed"}'
            ),
            stderr="",
        )

    loader, preset, target, _ = _loader_fixture(tmp_path, runner=first_runner)

    def second_runner(
        argv: list[str],
        **_kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    second_loader = AccessibilityCstPresetLoader(
        config=loader.config,
        trust_probe=lambda: True,
        runner=second_runner,
        script_path=tmp_path / "load_cst.js",
        environ={"HOME": str(tmp_path)},
        lock_path=tmp_path / "runtime" / "ui-mutation.lock",
        resource_sha256=_test_resource_hashes(tmp_path),
    )
    first_error: list[BaseException] = []

    def first_load() -> None:
        try:
            loader.load_preset(preset=preset, target=target)  # type: ignore[arg-type]
        except BaseException as exc:  # pragma: no cover - assertion aid
            first_error.append(exc)

    thread = threading.Thread(target=first_load)
    thread.start()
    assert entered.wait(timeout=2)
    try:
        with pytest.raises(UiOperationBusyError):
            second_loader.load_preset(  # type: ignore[arg-type]
                preset=preset,
                target=target,
            )
    finally:
        release.set()
        thread.join(timeout=2)

    assert first_error == []


def test_loader_detects_preset_change_after_observed_dispatch(tmp_path: Path) -> None:
    preset_path: Path | None = None

    def runner(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert preset_path is not None
        preset_path.write_bytes(b"changed while osascript was running")
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                '{"status":"observed",'
                '"evidence":"preset_menu_item_pressed_and_popup_dismissed"}'
            ),
            stderr="",
        )

    loader, preset, target, created_path = _loader_fixture(tmp_path, runner=runner)
    preset_path = created_path

    with pytest.raises(PresetIntegrityMismatchError) as caught:
        loader.load_preset(preset=preset, target=target)  # type: ignore[arg-type]

    assert caught.value.details == {"dispatch_started": True}


def test_loader_detects_same_digest_preset_aba_after_dispatch(tmp_path: Path) -> None:
    preset_path: Path | None = None

    def runner(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert preset_path is not None
        original = preset_path.read_bytes()
        preset_path.write_bytes(b"temporary same-uid replacement")
        preset_path.write_bytes(original)
        os.chmod(preset_path, 0o600)
        os.chmod(preset_path, 0o644)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                '{"status":"observed",'
                '"evidence":"preset_menu_item_pressed_and_popup_dismissed"}'
            ),
            stderr="",
        )

    loader, preset, target, created_path = _loader_fixture(tmp_path, runner=runner)
    preset_path = created_path

    with pytest.raises(PresetIntegrityMismatchError) as caught:
        loader.load_preset(preset=preset, target=target)  # type: ignore[arg-type]

    assert caught.value.details == {"dispatch_started": True}


def test_process_runner_close_terminates_and_reaps_active_process() -> None:
    runner = _ProcessGroupRunner()
    result: list[subprocess.CompletedProcess[str] | BaseException] = []

    def execute() -> None:
        try:
            result.append(
                runner(
                    ["/bin/sleep", "5"],
                    check=False,
                    shell=False,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                    cwd="/",
                    env={"PATH": "/usr/bin:/bin"},
                    start_new_session=True,
                )
            )
        except BaseException as exc:  # pragma: no cover - assertion aid
            result.append(exc)

    thread = threading.Thread(target=execute)
    thread.start()
    deadline = time.monotonic() + 1.0
    while not runner.active and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runner.active is True

    runner.close()
    thread.join(timeout=2)

    assert thread.is_alive() is False
    assert runner.active is False
    assert len(result) == 1


def test_process_runner_forced_kill_never_uses_unbounded_communicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NeverReapedProcess:
        pid = 987_654_321
        returncode: int | None = None

        def __init__(self) -> None:
            self.communicate_timeouts: list[float | None] = []
            self.wait_timeouts: list[float | None] = []

        def communicate(
            self,
            input: object = None,
            timeout: float | None = None,
        ) -> tuple[str, str]:
            del input
            self.communicate_timeouts.append(timeout)
            raise subprocess.TimeoutExpired(["osascript"], timeout=timeout)

        def poll(self) -> None:
            return None

        def kill(self) -> None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            self.wait_timeouts.append(timeout)
            raise subprocess.TimeoutExpired(["osascript"], timeout=timeout)

    process = NeverReapedProcess()
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    runner = _ProcessGroupRunner()

    with pytest.raises(subprocess.TimeoutExpired):
        runner(
            ["/usr/bin/osascript", "-l", "JavaScript", "-"],
            input=b"function run() { return ''; }\n",
            capture_output=True,
            text=False,
            timeout=0.1,
            start_new_session=True,
        )

    assert process.communicate_timeouts == [0.1, 0.5, 0.5]
    assert all(timeout is not None for timeout in process.communicate_timeouts)
    assert runner.active is True
    with pytest.raises(OSError, match="already owns a live process"):
        runner(
            ["/usr/bin/osascript", "-l", "JavaScript", "-"],
            input=b"function run() { return ''; }\n",
            capture_output=True,
            text=False,
            timeout=0.1,
            start_new_session=True,
        )
    runner.close()
    assert process.wait_timeouts == [0.5, 0.5]


def test_best_effort_cleanup_uses_a_kill_timeout_with_dismissal_margin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_runner(
        argv: list[str],
        **_kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")

    loader, preset, target, _ = _loader_fixture(
        tmp_path,
        runner=failing_runner,
        timeout_seconds=10.0,
    )

    class _StubProcess:
        returncode = 0

        def communicate(
            self,
            input: object = None,
            timeout: float | None = None,
        ) -> tuple[bytes, bytes]:
            captured_timeouts.append(timeout)
            return b"", b""

        def poll(self) -> int:
            return 0

    captured_timeouts: list[float | None] = []
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: _StubProcess())

    with pytest.raises(PresetAutomationFailedError):
        loader.load_preset(preset=preset, target=target)  # type: ignore[arg-type]

    # Below the DISMISS_BUDGET_MS=3000 in bridge_scripts/cleanup_cst.js would
    # leave the script no margin to hit its own soft deadline before Python's
    # hard kill fires.
    assert captured_timeouts == [6.0]


@pytest.mark.skipif(
    not Path("/usr/bin/osascript").exists(),
    reason="requires the macOS osascript executable",
)
def test_loader_shutdown_keeps_global_lock_through_bounded_cleanup(
    tmp_path: Path,
) -> None:
    loader, preset, target, _ = _loader_fixture(
        tmp_path,
        runner=None,
        script_sources={
            "load_cst.js": b"function run(argv) { delay(5); return '{}'; }\n",
            "cleanup_cst.js": (
                b"function run(argv) { delay(0.8); return 'SKIPPED'; }\n"
            ),
        },
    )

    def second_runner(
        argv: list[str],
        **_kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                '{"status":"observed",'
                '"evidence":"preset_menu_item_pressed_and_popup_dismissed"}'
            ),
            stderr="",
        )

    second_loader = AccessibilityCstPresetLoader(
        config=loader.config,
        trust_probe=lambda: True,
        runner=second_runner,
        script_path=tmp_path / "load_cst.js",
        environ={"HOME": str(tmp_path)},
        lock_path=tmp_path / "runtime" / "ui-mutation.lock",
        resource_sha256=_test_resource_hashes(tmp_path),
    )
    first_error: list[BaseException] = []

    def first_load() -> None:
        try:
            loader.load_preset(preset=preset, target=target)  # type: ignore[arg-type]
        except BaseException as exc:  # pragma: no cover - assertion aid
            first_error.append(exc)

    load_thread = threading.Thread(target=first_load)
    load_thread.start()
    assert loader._owned_runner is not None  # noqa: SLF001
    deadline = time.monotonic() + 1.0
    while not loader._owned_runner.active and time.monotonic() < deadline:  # noqa: SLF001
        time.sleep(0.01)
    assert loader._owned_runner.active is True  # noqa: SLF001

    close_thread = threading.Thread(target=loader.close)
    close_thread.start()
    time.sleep(0.1)
    with pytest.raises(UiOperationBusyError):
        second_loader.load_preset(  # type: ignore[arg-type]
            preset=preset,
            target=target,
        )

    close_thread.join(timeout=3)
    load_thread.join(timeout=3)
    assert close_thread.is_alive() is False
    assert load_thread.is_alive() is False
    assert first_error

    result = second_loader.load_preset(  # type: ignore[arg-type]
        preset=preset,
        target=target,
    )
    assert result.evidence == "preset_menu_item_pressed_and_popup_dismissed"
