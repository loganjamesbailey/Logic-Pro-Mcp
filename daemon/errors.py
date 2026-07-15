from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class BridgeError(Exception):
    """Base error safe to expose through the local RPC boundary."""

    code = "BRIDGE_ERROR"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = dict(details or {})

    def with_details(self, **details: Any) -> BridgeError:
        merged = {**self.details, **details}
        return type(self)(self.message, details=merged)

    def to_rpc_data(self) -> dict[str, Any]:
        return {
            "bridge_code": self.code,
            "retryable": self.retryable,
            "details": self.details,
        }


class ConfigurationError(BridgeError):
    code = "CONFIGURATION_ERROR"


class ServerBindError(BridgeError):
    code = "SERVER_BIND_FAILED"
    retryable = True


class InvalidParametersError(BridgeError):
    code = "INVALID_PARAMETERS"


class UnknownActionError(BridgeError):
    code = "UNKNOWN_ACTION"


class AuthenticationRequiredError(BridgeError):
    code = "AUTHENTICATION_REQUIRED"


class MidiBackendUnavailableError(BridgeError):
    code = "MIDI_BACKEND_UNAVAILABLE"


class MidiPortNotFoundError(BridgeError):
    code = "MIDI_PORT_NOT_FOUND"
    retryable = True


class MidiDispatchError(BridgeError):
    code = "MIDI_DISPATCH_FAILED"
    retryable = True

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)
        successful_send_count = self.details.get("successful_send_count", 0)
        self.retryable = not (
            isinstance(successful_send_count, int)
            and not isinstance(successful_send_count, bool)
            and successful_send_count > 0
        )


class CstLoadingDisabledError(BridgeError):
    code = "CST_LOADING_DISABLED"


class ConfirmationRequiredError(BridgeError):
    code = "CONFIRMATION_REQUIRED"


class PresetNotAllowlistedError(BridgeError):
    code = "PRESET_NOT_ALLOWLISTED"


class TargetNotAllowlistedError(BridgeError):
    code = "TARGET_NOT_ALLOWLISTED"


class PresetFileUnavailableError(BridgeError):
    code = "PRESET_FILE_UNAVAILABLE"


class PresetFileRejectedError(BridgeError):
    code = "PRESET_FILE_REJECTED"


class PresetIntegrityMismatchError(BridgeError):
    code = "PRESET_INTEGRITY_MISMATCH"


class AccessibilityPermissionRequiredError(BridgeError):
    code = "ACCESSIBILITY_PERMISSION_REQUIRED"


class AutomationPermissionRequiredError(BridgeError):
    code = "AUTOMATION_PERMISSION_REQUIRED"


class LogicNotRunningError(BridgeError):
    code = "LOGIC_NOT_RUNNING"


class MixerWindowNotFoundError(BridgeError):
    code = "MIXER_WINDOW_NOT_FOUND"


class TargetMismatchError(BridgeError):
    code = "TARGET_MISMATCH"


class SettingPopupNotFoundError(BridgeError):
    code = "SETTING_POPUP_NOT_FOUND"


class PresetMenuItemNotFoundError(BridgeError):
    code = "PRESET_MENU_ITEM_NOT_FOUND"


class UiOperationBusyError(BridgeError):
    code = "UI_OPERATION_BUSY"
    retryable = True


class PresetLoadTimeoutError(BridgeError):
    code = "PRESET_LOAD_TIMEOUT"


class PresetAutomationFailedError(BridgeError):
    code = "PRESET_AUTOMATION_FAILED"


class ScripterUnavailableError(BridgeError):
    code = "SCRIPTER_UNAVAILABLE"
