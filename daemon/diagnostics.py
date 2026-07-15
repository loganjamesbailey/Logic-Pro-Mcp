from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any

from daemon.accessibility import accessibility_status, cst_resource_status
from daemon.commands import CstPresetLoaderLike
from daemon.config import (
    MIN_AUTH_TOKEN_BYTES,
    BridgeConfig,
    authentication_token_reason,
)
from daemon.readiness import (
    MidiReadinessLike,
    midi_output_check,
    operator_readiness_snapshot,
)


def run_diagnostics(
    *,
    config: BridgeConfig,
    midi: MidiReadinessLike,
    environ: Mapping[str, str] | None = None,
    accessibility_probe: Callable[[], bool] | None = None,
    cst_loader: CstPresetLoaderLike | None = None,
) -> dict[str, Any]:
    environment = os.environ if environ is None else environ
    checks: dict[str, dict[str, Any]] = {}

    token_reason = authentication_token_reason(environment.get(config.server.token_env))
    auth_ok = token_reason is None
    checks["authentication"] = {
        "ok": auth_ok,
        "blocking": True,
        "required": True,
        "token_env": config.server.token_env,
        "minimum_encoded_bytes": MIN_AUTH_TOKEN_BYTES,
        "reason": token_reason,
    }

    checks["midi_output"] = midi_output_check(config=config, midi=midi)

    accessibility_required = (
        config.permissions.require_accessibility_for_ui or config.accessibility.enabled
    )
    if accessibility_required:
        permission = accessibility_status(trust_probe=accessibility_probe)
        checks["accessibility"] = {
            "ok": bool(permission["trusted"]),
            "blocking": False,
            "required_for": "logic.load_cst_preset",
            **permission,
        }
    else:
        checks["accessibility"] = {
            "ok": True,
            "blocking": False,
            "required_for": None,
            "supported": True,
            "trusted": False,
            "reason": "accessibility_checks_disabled",
        }

    script_ready = True
    if cst_loader is not None:
        # Reuse the loader's cached preset validation and its script-pinning
        # check instead of re-hashing every allowlisted preset file on every
        # diagnostics call.
        loader_status = cst_loader.status()
        script_ready = bool(loader_status.get("script_ready"))
        checks["cst_presets"] = _cst_resource_status_from_loader_status(
            config=config,
            loader_status=loader_status,
        )
    else:
        checks["cst_presets"] = cst_resource_status(config.accessibility)
    if config.accessibility.enabled:
        checks["automation"] = {
            "ok": True,
            "blocking": False,
            "known": False,
            "required_for": "logic.load_cst_preset",
            "status": "unknown_until_dispatch",
            "reason": "automation_consent_cannot_be_probed_without_controlling_logic",
        }
    else:
        checks["automation"] = {
            "ok": True,
            "blocking": False,
            "known": False,
            "required_for": None,
            "status": "not_applicable",
            "reason": "cst_loading_disabled",
        }

    blocking_failure = any(
        not check["ok"] and check["blocking"] for check in checks.values()
    )
    warning = any(not check["ok"] for check in checks.values())
    status = "not_ready" if blocking_failure else "degraded" if warning else "ready"
    accessibility_ready = bool(checks["accessibility"]["ok"])
    cst_ready = bool(
        config.accessibility.enabled
        and checks["cst_presets"]["available"]
        and accessibility_ready
        and script_ready
    )
    readiness = operator_readiness_snapshot(
        config=config,
        authentication_ready=auth_ok,
        authentication_reason=token_reason,
        midi_check=checks["midi_output"],
        accessibility_ready=accessibility_ready,
        accessibility_reason=_reason(checks["accessibility"].get("reason")),
        cst_ready=cst_ready,
        cst_reason=_reason(checks["cst_presets"].get("reason")),
    )
    return {
        "status": status,
        "checks": checks,
        "operator_readiness": readiness,
    }


def _reason(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _cst_resource_status_from_loader_status(
    *,
    config: BridgeConfig,
    loader_status: Mapping[str, Any],
) -> dict[str, Any]:
    if not config.accessibility.enabled:
        return cst_resource_status(config.accessibility)
    raw_available_ids = loader_status.get("available_preset_ids")
    available_ids = (
        sorted(item for item in raw_available_ids if isinstance(item, str))
        if isinstance(raw_available_ids, list)
        else []
    )
    raw_unavailable_ids = loader_status.get("unavailable_preset_ids")
    unavailable_ids = (
        raw_unavailable_ids if isinstance(raw_unavailable_ids, list) else []
    )
    return {
        "ok": not unavailable_ids,
        "blocking": False,
        "enabled": True,
        "available": bool(available_ids),
        "available_ids": available_ids,
        "resource_ids": sorted(config.accessibility.presets),
        "unavailable_ids": unavailable_ids,
        "reason": _reason(loader_status.get("reason")),
    }
