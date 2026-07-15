from __future__ import annotations

import ctypes
import fcntl
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

from daemon.config import AccessibilityConfig, CstPresetConfig, CstTargetConfig
from daemon.errors import (
    AccessibilityPermissionRequiredError,
    AutomationPermissionRequiredError,
    BridgeError,
    CstLoadingDisabledError,
    LogicNotRunningError,
    MixerWindowNotFoundError,
    PresetAutomationFailedError,
    PresetFileRejectedError,
    PresetFileUnavailableError,
    PresetIntegrityMismatchError,
    PresetLoadTimeoutError,
    PresetMenuItemNotFoundError,
    SettingPopupNotFoundError,
    TargetMismatchError,
    UiOperationBusyError,
)
from daemon.ui_observation import MixerObservation, parse_mixer_inspection

_OBSERVED_EVIDENCE = "preset_menu_item_pressed_and_popup_dismissed"
_MAX_AUTOMATION_OUTPUT_BYTES = 65_536
_MAX_JXA_SOURCE_BYTES = 262_144
_CLEANUP_KILL_TIMEOUT_SECONDS = 6.0
"""Hard kill timeout for dispatching cleanup_cst.js.

Must stay comfortably above that script's own internal soft deadline
(DISMISS_BUDGET_MS = 3000 in bridge_scripts/cleanup_cst.js) so the script
gets a real chance to hit its own budget check and attempt a clean AXCancel
before Python's hard kill fires -- osascript launch, JXA compilation, and
AX-tree traversal all happen before the script's own clock starts checking
that budget.
"""
_SCRIPT_ERROR = re.compile(r"LOGIC_BRIDGE:(?:(PRE_DISPATCH|POST_DISPATCH):)?([A-Z_]+)")
_SCRIPT_FILENAMES = ("load_cst.js", "cleanup_cst.js", "inspect_mixer.js")
_PACKAGED_JXA_SHA256 = {
    "load_cst.js": "431246c8a06c3d1bd4bbafefa67edeef9c0e0695e8376c4b4a5a38afd3bad888",
    "cleanup_cst.js": "7f97c35fb3bb011ab3bc182bfcbc8c63906252000409754661c5c0c86daf85ce",
    "inspect_mixer.js": "370c6eb9a23b94ea3a2e0a55fcdede31c23f0fa73d17d2222549f9b4e95f11a2",
}

Runner = Callable[..., subprocess.CompletedProcess[Any]]


@dataclass(frozen=True, slots=True)
class CstLoadObservation:
    evidence: str
    inspection: MixerObservation | None = None
    inspection_reason: str | None = None


@dataclass(frozen=True, slots=True)
class _CleanupContext:
    preset_name: str
    target: CstTargetConfig


@dataclass(frozen=True, slots=True)
class _FixedJxaResource:
    filename: str
    source: bytes
    sha256: str


@dataclass(frozen=True, slots=True)
class _PresetFileIdentity:
    root: tuple[int, ...]
    file: tuple[int, ...]
    sha256: str

    @property
    def metadata(self) -> _PresetMetadataIdentity:
        return _PresetMetadataIdentity(root=self.root, file=self.file)


@dataclass(frozen=True, slots=True)
class _PresetMetadataIdentity:
    root: tuple[int, ...]
    file: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _OpenedCstPreset:
    root_path: Path
    filename: str
    root_descriptor: int
    preset_descriptor: int
    root_before: os.stat_result
    file_before: os.stat_result


class UnavailableCstPresetLoader:
    """Fail-closed service default used when no production loader was injected."""

    def status(self) -> dict[str, Any]:
        return {
            "supported": False,
            "trusted": False,
            "script_ready": False,
            "available_preset_ids": [],
            "unavailable_preset_ids": [],
            "reason": "accessibility_loader_not_configured",
        }

    def load_preset(
        self,
        *,
        preset: CstPresetConfig,
        target: CstTargetConfig,
    ) -> CstLoadObservation:
        del preset, target
        raise PresetAutomationFailedError(
            "The channel-strip preset loader is not configured"
        )

    def inspect_target(self, *, target: CstTargetConfig) -> MixerObservation:
        del target
        raise PresetAutomationFailedError("The Mixer inspector is not configured")

    def close(self) -> None:
        return None


class AccessibilityCstPresetLoader:
    """Fixed, allowlist-only JXA workflow for loading a `.cst` in Logic."""

    def __init__(
        self,
        *,
        config: AccessibilityConfig,
        trust_probe: Callable[[], bool] | None = None,
        runner: Runner | None = None,
        script_path: Path | None = None,
        environ: Mapping[str, str] | None = None,
        platform_name: str | None = None,
        lock_path: Path | None = None,
        resource_sha256: Mapping[str, str] | None = None,
    ) -> None:
        self.config = config
        self._trust_probe = trust_probe
        if runner is None:
            owned_runner = _ProcessGroupRunner()
            self._owned_runner: _ProcessGroupRunner | None = owned_runner
            self._runner: Runner = owned_runner
        else:
            self._owned_runner = None
            self._runner = runner
        self._script_path = script_path or (
            Path(__file__).resolve().parent.parent / "bridge_scripts" / "load_cst.js"
        )
        expected_hashes = dict(
            _PACKAGED_JXA_SHA256 if resource_sha256 is None else resource_sha256
        )
        loaded_resources: dict[str, _FixedJxaResource] = {}
        self._script_resource_error: BridgeError | None = None
        try:
            if set(expected_hashes) != set(_SCRIPT_FILENAMES):
                raise PresetAutomationFailedError(
                    "The fixed channel-strip automation scripts are unavailable"
                )
            for filename in _SCRIPT_FILENAMES:
                loaded_resources[filename] = _load_fixed_jxa_resource(
                    self._script_path.with_name(filename),
                    expected_sha256=expected_hashes[filename],
                )
        except BridgeError as exc:
            loaded_resources.clear()
            self._script_resource_error = exc
        self._script_resources: Mapping[str, _FixedJxaResource] = MappingProxyType(
            loaded_resources
        )
        self._subprocess_environment = _sanitized_environment(
            os.environ if environ is None else environ
        )
        self._platform_name = platform_name
        self._mutation_lock = threading.Lock()
        self._cross_process_lock = _CrossProcessUiLock(
            lock_path
            or (
                Path.home()
                / "Library"
                / "Caches"
                / "AI-Logic-Pro-Bridge"
                / "ui-mutation.lock"
            )
        )
        self._state_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._resource_cache_lock = threading.Lock()
        self._resource_validation_cache: dict[str, _PresetMetadataIdentity] = {}
        self._mutation_finished = threading.Event()
        self._mutation_finished.set()
        self._closing = False

    def status(self) -> dict[str, Any]:
        permission = accessibility_status(
            platform_name=self._platform_name,
            trust_probe=self._trust_probe,
        )
        resources = self._cached_cst_resource_status()
        script_ready = self._script_resource_error is None
        reason = permission["reason"]
        if reason is None and not script_ready:
            reason = "accessibility_script_unavailable"
        if reason is None and not resources["available"]:
            reason = resources["reason"]
        return {
            **permission,
            "script_ready": script_ready,
            "available_preset_ids": resources["available_ids"],
            "unavailable_preset_ids": resources["unavailable_ids"],
            "reason": reason,
        }

    def _cached_cst_resource_status(self) -> dict[str, Any]:
        if not self.config.enabled:
            return cst_resource_status(self.config)

        available: list[str] = []
        unavailable: list[dict[str, str]] = []
        configured_ids = set(self.config.presets)
        with self._resource_cache_lock:
            for preset_id in tuple(self._resource_validation_cache):
                if preset_id not in configured_ids:
                    del self._resource_validation_cache[preset_id]

            for preset in self.config.presets.values():
                try:
                    metadata = probe_cst_preset_metadata(
                        config=self.config,
                        preset=preset,
                    )
                except BridgeError:
                    self._resource_validation_cache.pop(preset.id, None)
                else:
                    if self._resource_validation_cache.get(preset.id) == metadata:
                        available.append(preset.id)
                        continue

                try:
                    identity = validate_cst_preset_file(
                        config=self.config,
                        preset=preset,
                    )
                except BridgeError as exc:
                    self._resource_validation_cache.pop(preset.id, None)
                    unavailable.append({"id": preset.id, "reason": exc.code})
                else:
                    self._resource_validation_cache[preset.id] = identity.metadata
                    available.append(preset.id)

        return _cst_resource_status_payload(
            config=self.config,
            available=available,
            unavailable=unavailable,
        )

    def load_preset(
        self,
        *,
        preset: CstPresetConfig,
        target: CstTargetConfig,
    ) -> CstLoadObservation:
        if not self.config.enabled:
            raise CstLoadingDisabledError("Channel-strip preset loading is disabled")
        with self._state_lock:
            if self._closing:
                raise PresetAutomationFailedError(
                    "The channel-strip preset loader is shutting down"
                )
        if not self._mutation_lock.acquire(blocking=False):
            raise UiOperationBusyError("Another Logic UI operation is already running")
        self._mutation_finished.clear()
        process_lock_acquired = False
        try:
            if not self._cross_process_lock.acquire():
                raise UiOperationBusyError(
                    "Another Logic UI operation is already running"
                )
            process_lock_acquired = True
            permission = accessibility_status(
                platform_name=self._platform_name,
                trust_probe=self._trust_probe,
            )
            if not permission["supported"] or not permission["trusted"]:
                raise AccessibilityPermissionRequiredError(
                    "Accessibility permission is required for this operation"
                )
            self._require_script_resources()
            identity_before = validate_cst_preset_file(
                config=self.config,
                preset=preset,
            )
            observation = self._dispatch(
                preset_name=Path(preset.filename).stem,
                target=target,
            )
            try:
                identity_after = validate_cst_preset_file(
                    config=self.config,
                    preset=preset,
                )
                if identity_after != identity_before:
                    raise PresetIntegrityMismatchError(
                        "The configured channel-strip preset changed during loading"
                    )
            except BridgeError as exc:
                raise exc.with_details(dispatch_started=True) from exc
            try:
                inspection = self._inspect_dispatch(target=target)
            except BridgeError as exc:
                return CstLoadObservation(
                    evidence=observation.evidence,
                    inspection_reason=exc.code,
                )
            return CstLoadObservation(
                evidence=observation.evidence,
                inspection=inspection,
            )
        finally:
            if process_lock_acquired:
                try:
                    self._cross_process_lock.release()
                except OSError:
                    # A failed OS-level unlock must not prevent releasing the
                    # in-process mutation lock below, or every subsequent
                    # operation on this loader would wedge on
                    # UiOperationBusyError forever.
                    pass
            # Signal finished before releasing the lock: otherwise a new
            # operation can acquire the lock and clear() the event before
            # this set() runs, and this stale set() would then undo that
            # clear(), letting a concurrent close() return early.
            self._mutation_finished.set()
            self._mutation_lock.release()

    def inspect_target(self, *, target: CstTargetConfig) -> MixerObservation:
        if not self.config.enabled:
            raise CstLoadingDisabledError(
                "Channel-strip preset loading and inspection are disabled"
            )
        with self._state_lock:
            if self._closing:
                raise PresetAutomationFailedError(
                    "The Mixer inspector is shutting down"
                )
        if not self._mutation_lock.acquire(blocking=False):
            raise UiOperationBusyError("Another Logic UI operation is already running")
        self._mutation_finished.clear()
        process_lock_acquired = False
        try:
            if not self._cross_process_lock.acquire():
                raise UiOperationBusyError(
                    "Another Logic UI operation is already running"
                )
            process_lock_acquired = True
            permission = accessibility_status(
                platform_name=self._platform_name,
                trust_probe=self._trust_probe,
            )
            if not permission["supported"] or not permission["trusted"]:
                raise AccessibilityPermissionRequiredError(
                    "Accessibility permission is required for this operation"
                )
            self._require_script_resources()
            return self._inspect_dispatch(target=target)
        finally:
            if process_lock_acquired:
                try:
                    self._cross_process_lock.release()
                except OSError:
                    # A failed OS-level unlock must not prevent releasing the
                    # in-process mutation lock below, or every subsequent
                    # operation on this loader would wedge on
                    # UiOperationBusyError forever.
                    pass
            # Signal finished before releasing the lock: otherwise a new
            # operation can acquire the lock and clear() the event before
            # this set() runs, and this stale set() would then undo that
            # clear(), letting a concurrent close() return early.
            self._mutation_finished.set()
            self._mutation_lock.release()

    def close(self) -> None:
        with self._state_lock:
            self._closing = True
        if self._owned_runner is not None:
            self._owned_runner.close()
        self._mutation_finished.wait(timeout=5.0)

    def _require_script_resources(self) -> None:
        if self._script_resource_error is not None:
            raise PresetAutomationFailedError(
                "The fixed channel-strip automation scripts are unavailable"
            ) from self._script_resource_error

    def _script_resource(self, filename: str) -> _FixedJxaResource:
        self._require_script_resources()
        try:
            return self._script_resources[filename]
        except KeyError as exc:  # pragma: no cover - constructor invariant
            raise PresetAutomationFailedError(
                "The fixed channel-strip automation scripts are unavailable"
            ) from exc

    def _dispatch(
        self,
        *,
        preset_name: str,
        target: CstTargetConfig,
    ) -> CstLoadObservation:
        cleanup = _CleanupContext(preset_name=preset_name, target=target)
        with self._state_lock:
            if self._closing:
                raise PresetAutomationFailedError(
                    "The channel-strip preset loader is shutting down"
                )
        argv = [
            "/usr/bin/osascript",
            "-l",
            "JavaScript",
            "-",
            preset_name,
            str(target.mixer_index),
            target.expected_accessibility_name,
        ]
        try:
            runner_arguments: dict[str, Any] = {
                "check": False,
                "shell": False,
                "input": self._script_resource("load_cst.js").source,
                "capture_output": True,
                "text": False,
                "timeout": self.config.timeout_seconds,
                "cwd": "/",
                "env": self._subprocess_environment,
                "start_new_session": True,
            }
            completed = self._runner(argv, **runner_arguments)
        except subprocess.TimeoutExpired as exc:
            self._best_effort_cleanup(cleanup)
            raise PresetLoadTimeoutError(
                "Channel-strip preset loading timed out",
                details={"dispatch_started": True},
            ) from exc
        except OSError as exc:
            raise PresetAutomationFailedError(
                "The channel-strip automation process could not be started"
            ) from exc

        stdout = _subprocess_text(completed.stdout)
        stderr = _subprocess_text(completed.stderr)
        output = f"{stdout}\n{stderr}"
        if len(output.encode("utf-8", errors="replace")) > (
            _MAX_AUTOMATION_OUTPUT_BYTES
        ):
            self._best_effort_cleanup(cleanup)
            raise PresetAutomationFailedError(
                "The channel-strip automation returned an invalid response",
                details={"dispatch_started": True},
            )
        if completed.returncode != 0:
            self._best_effort_cleanup(cleanup)
            raise _script_failure(output)
        try:
            payload = json.loads(stdout.strip())
        except (json.JSONDecodeError, UnicodeError) as exc:
            self._best_effort_cleanup(cleanup)
            raise PresetAutomationFailedError(
                "The channel-strip automation returned an invalid response",
                details={"dispatch_started": True},
            ) from exc
        if (
            not isinstance(payload, dict)
            or set(payload) != {"status", "evidence"}
            or payload.get("status") != "observed"
            or payload.get("evidence") != _OBSERVED_EVIDENCE
        ):
            self._best_effort_cleanup(cleanup)
            raise PresetAutomationFailedError(
                "The channel-strip automation returned an invalid response",
                details={"dispatch_started": True},
            )
        return CstLoadObservation(evidence=_OBSERVED_EVIDENCE)

    def _inspect_dispatch(self, *, target: CstTargetConfig) -> MixerObservation:
        argv = [
            "/usr/bin/osascript",
            "-l",
            "JavaScript",
            "-",
            str(target.mixer_index),
            target.expected_accessibility_name,
        ]
        try:
            completed = self._runner(
                argv,
                check=False,
                shell=False,
                input=self._script_resource("inspect_mixer.js").source,
                capture_output=True,
                text=False,
                timeout=self.config.timeout_seconds,
                cwd="/",
                env=self._subprocess_environment,
                start_new_session=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise PresetAutomationFailedError("Mixer inspection timed out") from exc
        except OSError as exc:
            raise PresetAutomationFailedError(
                "The Mixer inspection process could not be started"
            ) from exc
        stdout = _subprocess_text(completed.stdout)
        stderr = _subprocess_text(completed.stderr)
        output = f"{stdout}\n{stderr}"
        if len(output.encode("utf-8", errors="replace")) > (
            _MAX_AUTOMATION_OUTPUT_BYTES
        ):
            raise PresetAutomationFailedError(
                "The Mixer inspection returned an invalid response"
            )
        if completed.returncode != 0:
            error = _script_failure(output)
            error.details.pop("dispatch_started", None)
            raise error
        return parse_mixer_inspection(
            stdout,
            target=target,
            observed_at=datetime.now(UTC),
        )

    def _best_effort_cleanup(self, cleanup: _CleanupContext) -> None:
        with self._cleanup_lock:
            try:
                source = self._script_resource("cleanup_cst.js").source
                runner = _ProcessGroupRunner()
                try:
                    runner(
                        [
                            "/usr/bin/osascript",
                            "-l",
                            "JavaScript",
                            "-",
                            cleanup.preset_name,
                            str(cleanup.target.mixer_index),
                            cleanup.target.expected_accessibility_name,
                        ],
                        check=False,
                        shell=False,
                        input=source,
                        capture_output=True,
                        text=False,
                        timeout=min(
                            _CLEANUP_KILL_TIMEOUT_SECONDS, self.config.timeout_seconds
                        ),
                        cwd="/",
                        env=self._subprocess_environment,
                        start_new_session=True,
                    )
                finally:
                    runner.close()
            except (BridgeError, OSError, subprocess.SubprocessError):
                return


def accessibility_status(
    *,
    platform_name: str | None = None,
    trust_probe: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    platform_name = platform_name or sys.platform
    if platform_name != "darwin":
        return {
            "supported": False,
            "trusted": False,
            "reason": "macos_accessibility_api_unavailable",
        }
    probe = trust_probe or _ax_is_process_trusted
    try:
        trusted = bool(probe())
    except (OSError, AttributeError):
        return {
            "supported": True,
            "trusted": False,
            "reason": "accessibility_trust_probe_failed",
        }
    return {
        "supported": True,
        "trusted": trusted,
        "reason": None if trusted else "accessibility_permission_not_granted",
    }


def _ax_is_process_trusted() -> bool:
    framework = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
    )
    function = framework.AXIsProcessTrusted
    function.restype = ctypes.c_bool
    function.argtypes = []
    return bool(function())


def _stable_stat_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _load_fixed_jxa_resource(
    path: Path,
    *,
    expected_sha256: str,
) -> _FixedJxaResource:
    """Read, authenticate, and cache one fixed JXA program from one descriptor."""
    descriptor: int | None = None
    try:
        path_status = os.lstat(path)
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        before = os.fstat(descriptor)
        if (
            stat.S_ISLNK(path_status.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or (path_status.st_dev, path_status.st_ino)
            != (before.st_dev, before.st_ino)
            or before.st_uid not in {0, os.getuid()}
            or before.st_mode & 0o022
            or not 1 <= before.st_size <= _MAX_JXA_SOURCE_BYTES
            or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
        ):
            raise OSError
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65_536))
            if not chunk:
                raise OSError
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise OSError
        source = b"".join(chunks)
        after = os.fstat(descriptor)
        after_path = os.lstat(path)
        if _stable_stat_identity(before) != _stable_stat_identity(
            after
        ) or _stable_stat_identity(after_path) != _stable_stat_identity(after):
            raise OSError
        source.decode("utf-8", errors="strict")
        if b"\x00" in source:
            raise UnicodeError
        digest = hashlib.sha256(source).hexdigest()
        if digest != expected_sha256:
            raise OSError
        return _FixedJxaResource(
            filename=path.name,
            source=source,
            sha256=digest,
        )
    except (OSError, RuntimeError, UnicodeError) as exc:
        raise PresetAutomationFailedError(
            "The fixed channel-strip automation script is unavailable"
        ) from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _close_cst_descriptors(opened: _OpenedCstPreset) -> None:
    for descriptor in (opened.preset_descriptor, opened.root_descriptor):
        try:
            os.close(descriptor)
        except OSError:
            pass


def _open_cst_preset_descriptors(
    *,
    config: AccessibilityConfig,
    preset: CstPresetConfig,
) -> _OpenedCstPreset:
    root_descriptor: int | None = None
    preset_descriptor: int | None = None
    try:
        filename = Path(preset.filename)
        if filename.name != preset.filename or filename in {Path("."), Path("..")}:
            raise PresetFileRejectedError(
                "The configured channel-strip preset file was rejected"
            )
        root_path_before = os.lstat(config.preset_root)
        root_descriptor = os.open(
            config.preset_root,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        root_before = os.fstat(root_descriptor)
        current_uid = os.getuid()
        if (
            stat.S_ISLNK(root_path_before.st_mode)
            or not stat.S_ISDIR(root_before.st_mode)
            or (root_path_before.st_dev, root_path_before.st_ino)
            != (root_before.st_dev, root_before.st_ino)
            or root_before.st_uid != current_uid
            or root_before.st_mode & 0o022
        ):
            raise PresetFileRejectedError(
                "The configured channel-strip preset file was rejected"
            )
        path_before = os.stat(
            preset.filename,
            dir_fd=root_descriptor,
            follow_symlinks=False,
        )
        preset_descriptor = os.open(
            preset.filename,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            dir_fd=root_descriptor,
        )
        file_before = os.fstat(preset_descriptor)
        if (
            not stat.S_ISREG(file_before.st_mode)
            or _stable_stat_identity(path_before) != _stable_stat_identity(file_before)
            or file_before.st_uid != current_uid
            or file_before.st_mode & 0o022
            or not 1 <= file_before.st_size <= config.max_cst_bytes
        ):
            raise PresetFileRejectedError(
                "The configured channel-strip preset file was rejected"
            )
        opened = _OpenedCstPreset(
            root_path=config.preset_root,
            filename=preset.filename,
            root_descriptor=root_descriptor,
            preset_descriptor=preset_descriptor,
            root_before=root_before,
            file_before=file_before,
        )
        root_descriptor = None
        preset_descriptor = None
        return opened
    except BridgeError:
        raise
    except FileNotFoundError as exc:
        raise PresetFileUnavailableError(
            "The configured channel-strip preset file is unavailable"
        ) from exc
    except (OSError, RuntimeError) as exc:
        raise PresetFileRejectedError(
            "The configured channel-strip preset file was rejected"
        ) from exc
    finally:
        for descriptor in (preset_descriptor, root_descriptor):
            if descriptor is None:
                continue
            try:
                os.close(descriptor)
            except OSError:
                pass


def _finish_cst_preset_metadata(
    opened: _OpenedCstPreset,
) -> _PresetMetadataIdentity:
    try:
        file_after = os.fstat(opened.preset_descriptor)
        path_after = os.stat(
            opened.filename,
            dir_fd=opened.root_descriptor,
            follow_symlinks=False,
        )
        root_after = os.fstat(opened.root_descriptor)
        root_path_after = os.lstat(opened.root_path)
    except FileNotFoundError as exc:
        raise PresetFileUnavailableError(
            "The configured channel-strip preset file is unavailable"
        ) from exc
    except OSError as exc:
        raise PresetFileRejectedError(
            "The configured channel-strip preset file was rejected"
        ) from exc
    if (
        _stable_stat_identity(opened.file_before) != _stable_stat_identity(file_after)
        or _stable_stat_identity(path_after) != _stable_stat_identity(file_after)
        or _stable_stat_identity(opened.root_before)
        != _stable_stat_identity(root_after)
        or _stable_stat_identity(root_path_after) != _stable_stat_identity(root_after)
    ):
        raise PresetFileRejectedError(
            "The configured channel-strip preset file changed during validation"
        )
    return _PresetMetadataIdentity(
        root=_stable_stat_identity(root_after),
        file=_stable_stat_identity(file_after),
    )


def probe_cst_preset_metadata(
    *,
    config: AccessibilityConfig,
    preset: CstPresetConfig,
) -> _PresetMetadataIdentity:
    """Securely probe path-free preset identity without reading preset bytes."""
    opened = _open_cst_preset_descriptors(config=config, preset=preset)
    try:
        return _finish_cst_preset_metadata(opened)
    finally:
        _close_cst_descriptors(opened)


def validate_cst_preset_file(
    *,
    config: AccessibilityConfig,
    preset: CstPresetConfig,
) -> _PresetFileIdentity:
    """Revalidate an allowlisted preset and return stable, path-free identity."""
    opened = _open_cst_preset_descriptors(config=config, preset=preset)
    try:
        digest = hashlib.sha256()
        while chunk := os.read(opened.preset_descriptor, 1_048_576):
            digest.update(chunk)
        metadata = _finish_cst_preset_metadata(opened)
    except BridgeError:
        raise
    except OSError as exc:
        raise PresetFileRejectedError(
            "The configured channel-strip preset file was rejected"
        ) from exc
    finally:
        _close_cst_descriptors(opened)
    actual_digest = digest.hexdigest()
    if actual_digest != preset.sha256:
        raise PresetIntegrityMismatchError(
            "The configured channel-strip preset failed its integrity check"
        )
    return _PresetFileIdentity(
        root=metadata.root,
        file=metadata.file,
        sha256=actual_digest,
    )


def cst_resource_status(config: AccessibilityConfig) -> dict[str, Any]:
    if not config.enabled:
        return {
            "ok": True,
            "blocking": False,
            "enabled": False,
            "available": False,
            "available_ids": [],
            "resource_ids": [],
            "unavailable_ids": [],
            "reason": "cst_loading_disabled",
        }
    available: list[str] = []
    unavailable: list[dict[str, str]] = []
    for preset in config.presets.values():
        try:
            validate_cst_preset_file(config=config, preset=preset)
        except BridgeError as exc:
            unavailable.append({"id": preset.id, "reason": exc.code})
        else:
            available.append(preset.id)
    return _cst_resource_status_payload(
        config=config,
        available=available,
        unavailable=unavailable,
    )


def _cst_resource_status_payload(
    *,
    config: AccessibilityConfig,
    available: list[str],
    unavailable: list[dict[str, str]],
) -> dict[str, Any]:
    reason: str | None = None
    if unavailable:
        reason = (
            "cst_resources_partially_unavailable"
            if available
            else "cst_resources_unavailable"
        )
    return {
        "ok": not unavailable,
        "blocking": False,
        "enabled": True,
        "available": bool(available),
        "available_ids": sorted(available),
        "resource_ids": sorted(config.presets),
        "unavailable_ids": unavailable,
        "reason": reason,
    }


def _sanitized_environment(source: Mapping[str, str]) -> dict[str, str]:
    environment = {"PATH": "/usr/bin:/bin"}
    for key in ("HOME", "TMPDIR", "LANG", "LC_ALL", "__CF_USER_TEXT_ENCODING"):
        value = source.get(key)
        if value:
            environment[key] = value
    return environment


def _subprocess_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value
    return ""


def _script_failure(output: str) -> BridgeError:
    match = _SCRIPT_ERROR.search(output)
    phase = match.group(1) if match else None
    code = match.group(2) if match else None
    if match is None:
        # Only sniff raw AppleEvent error text when no structured
        # LOGIC_BRIDGE marker is present at all -- otherwise UI-derived text
        # elsewhere in the output (a window position, a track name) could
        # contain "-1719" and override a stable, already-parsed marker.
        if "-1719" in output or "not allowed assistive access" in output:
            return AccessibilityPermissionRequiredError(
                "Accessibility permission is required for this operation"
            )
        if "-1743" in output or "Not authorized to send Apple events" in output:
            return AutomationPermissionRequiredError(
                "Automation permission is required for this operation"
            )
    error_type: type[BridgeError]
    message: str
    if code == "LOGIC_NOT_RUNNING":
        error_type = LogicNotRunningError
        message = "Logic Pro is not running"
    elif code == "MIXER_WINDOW_NOT_FOUND":
        error_type = MixerWindowNotFoundError
        message = "Exactly one standalone Logic Mixer window is required"
    elif code in {"TARGET_MISMATCH", "TARGET_DRIFTED"}:
        error_type = TargetMismatchError
        message = "The configured Mixer target could not be matched exactly"
    elif code == "SETTING_POPUP_NOT_FOUND":
        error_type = SettingPopupNotFoundError
        message = "The target channel-strip Setting popup was not found"
    elif code == "PRESET_MENU_ITEM_NOT_FOUND":
        error_type = PresetMenuItemNotFoundError
        message = "The configured channel-strip preset menu item was not found"
    elif code == "AUTOMATION_PERMISSION_REQUIRED":
        error_type = AutomationPermissionRequiredError
        message = "Automation permission is required for this operation"
    else:
        error_type = PresetAutomationFailedError
        message = "The channel-strip automation failed"
    details = {"dispatch_started": True} if phase == "POST_DISPATCH" else None
    return error_type(message, details=details)


class _ProcessGroupRunner:
    """Own and synchronously terminate the one subprocess it may be running."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._closing = False

    @property
    def active(self) -> bool:
        with self._lock:
            process = self._process
            if process is not None and process.poll() is not None:
                self._process = None
                process = None
            return process is not None

    def __call__(
        self,
        argv: Sequence[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[Any]:
        timeout = float(kwargs.pop("timeout"))
        input_data = kwargs.pop("input", None)
        kwargs.pop("capture_output", None)
        kwargs.pop("check", None)
        if input_data is not None:
            kwargs.pop("stdin", None)
            kwargs["stdin"] = subprocess.PIPE
        with self._lock:
            if self._closing:
                raise OSError("process runner is closed")
            if self._process is not None:
                if self._process.poll() is None:
                    raise OSError("process runner already owns a live process")
                self._process = None
            process = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **kwargs,
            )
            self._process = process
        try:
            try:
                stdout, stderr = process.communicate(
                    input=input_data,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired as timeout_error:
                _signal_process_group(process, signal.SIGTERM)
                try:
                    process.communicate(timeout=0.5)
                except subprocess.TimeoutExpired:
                    _signal_process_group(process, signal.SIGKILL)
                    try:
                        process.communicate(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        pass
                raise timeout_error
            return subprocess.CompletedProcess(
                argv,
                process.returncode,
                stdout,
                stderr,
            )
        finally:
            with self._lock:
                if self._process is process and process.poll() is not None:
                    self._process = None

    def close(self) -> None:
        with self._lock:
            self._closing = True
            process = self._process
        if process is None:
            return
        _signal_process_group(process, signal.SIGTERM)
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            _signal_process_group(process, signal.SIGKILL)
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                return
        with self._lock:
            if self._process is process:
                self._process = None


class _CrossProcessUiLock:
    """Nonblocking, per-user advisory lock for Logic UI mutations."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._descriptor: int | None = None

    def acquire(self) -> bool:
        if self._descriptor is not None:
            return False
        try:
            self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory_status = os.lstat(self._path.parent)
            if (
                stat.S_ISLNK(directory_status.st_mode)
                or not stat.S_ISDIR(directory_status.st_mode)
                or directory_status.st_uid != os.getuid()
                or directory_status.st_mode & 0o077
            ):
                raise OSError
            descriptor = os.open(
                self._path,
                os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            file_status = os.fstat(descriptor)
            if (
                not stat.S_ISREG(file_status.st_mode)
                or file_status.st_uid != os.getuid()
                or file_status.st_mode & 0o077
            ):
                os.close(descriptor)
                raise OSError
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(descriptor)
                return False
        except OSError as exc:
            raise PresetAutomationFailedError(
                "The Logic UI operation lock is unavailable"
            ) from exc
        self._descriptor = descriptor
        return True

    def release(self) -> None:
        descriptor = self._descriptor
        self._descriptor = None
        if descriptor is None:
            return
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _signal_process_group(
    process: subprocess.Popen[str],
    requested_signal: signal.Signals,
) -> None:
    try:
        os.killpg(process.pid, requested_signal)
    except OSError:
        try:
            process.kill()
        except OSError:
            pass
