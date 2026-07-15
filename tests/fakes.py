from __future__ import annotations

from dataclasses import dataclass, field

from daemon.accessibility import CstLoadObservation
from daemon.config import CstPresetConfig, CstTargetConfig
from daemon.ui_observation import MixerObservation


def valid_mapping() -> dict[str, object]:
    return {
        "server": {
            "host": "127.0.0.1",
            "port": 8765,
            "token_env": "LOGIC_BRIDGE_TOKEN",
            "max_request_bytes": 65536,
            "read_timeout_seconds": 10.0,
            "write_timeout_seconds": 2.0,
            "mcp_timeout_seconds": 75.0,
            "max_connections": 32,
        },
        "midi": {
            "output_name": "IAC Driver AI_Logic_Bridge",
            "assignments": [
                {
                    "name": "track.selected.volume",
                    "kind": "control_change",
                    "channel": 0,
                    "control": 7,
                    "value_mode": "normalized",
                },
                {
                    "name": "track.selected.mute_toggle",
                    "kind": "control_change",
                    "channel": 0,
                    "control": 20,
                    "value_mode": "trigger",
                    "value": 127,
                    "release_value": 0,
                },
            ],
        },
        "permissions": {"require_accessibility_for_ui": True},
    }


@dataclass
class RecordingMidiTransport:
    outputs: list[str] = field(default_factory=lambda: ["IAC Driver AI_Logic_Bridge"])
    sent: list[tuple[int, int, int]] = field(default_factory=list)
    cc_calls: list[tuple[int, int, int, int | None]] = field(default_factory=list)
    note_calls: list[tuple[int, int, int, int]] = field(default_factory=list)
    sysex_calls: list[tuple[int, ...]] = field(default_factory=list)

    def list_outputs(self) -> list[str]:
        return list(self.outputs)

    def send_cc(
        self,
        *,
        channel: int,
        control: int,
        value: int,
        release_value: int | None = None,
    ) -> int:
        self.cc_calls.append((channel, control, value, release_value))
        self.sent.append((channel, control, value))
        if release_value is not None:
            self.sent.append((channel, control, release_value))
        return 1 + int(release_value is not None)

    def send_note(
        self,
        *,
        channel: int,
        note: int,
        velocity: int,
        release_velocity: int,
    ) -> int:
        self.note_calls.append((channel, note, velocity, release_velocity))
        return 2

    def send_sysex(self, *, data: tuple[int, ...]) -> int:
        self.sysex_calls.append(data)
        return 1

    def close(self) -> None:
        return None


@dataclass
class RecordingCstPresetLoader:
    status_value: dict[str, object] = field(
        default_factory=lambda: {
            "supported": True,
            "trusted": True,
            "script_ready": True,
            "reason": None,
        }
    )
    error: Exception | None = None
    inspect_error: Exception | None = None
    mixer_observation: MixerObservation | None = None
    loaded: list[tuple[str, str]] = field(default_factory=list)
    inspected: list[str] = field(default_factory=list)
    closed: bool = False

    def status(self) -> dict[str, object]:
        return dict(self.status_value)

    def load_preset(
        self,
        *,
        preset: CstPresetConfig,
        target: CstTargetConfig,
    ) -> CstLoadObservation:
        if self.error is not None:
            raise self.error
        self.loaded.append((preset.id, target.id))
        return CstLoadObservation(
            evidence="preset_menu_item_pressed_and_popup_dismissed",
            inspection=self.mixer_observation,
        )

    def inspect_target(self, *, target: CstTargetConfig) -> MixerObservation:
        if self.inspect_error is not None:
            raise self.inspect_error
        self.inspected.append(target.id)
        if self.mixer_observation is not None:
            return self.mixer_observation
        return MixerObservation(
            target_id=target.id,
            observed_at="2026-07-15T04:00:00+00:00",
            source="macos_accessibility_mixer_inspector_v1",
            verification_scope="visible_plugin_slot_labels",
            plugins=(),
            complete=True,
        )

    def close(self) -> None:
        self.closed = True


class FakePort:
    def __init__(
        self,
        *,
        send_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.messages: list[object] = []
        self.closed = False
        self._send_error = send_error
        self._close_error = close_error

    def send(self, message: object) -> None:
        if self.closed:
            raise ValueError("send() called on closed port")
        if self._send_error is not None:
            raise self._send_error
        self.messages.append(message)

    def close(self) -> None:
        if self.closed:
            return
        if self._close_error is not None:
            raise self._close_error
        self.closed = True


class FakeMidoBackend:
    def __init__(
        self,
        outputs: list[str],
        *,
        send_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.outputs = outputs
        self._send_error = send_error
        self._close_error = close_error
        self.ports: list[FakePort] = []
        self.opened_names: list[str] = []
        self.enumeration_count = 0

    @property
    def port(self) -> FakePort:
        if not self.ports:
            raise RuntimeError("No fake MIDI output has been opened")
        return self.ports[-1]

    def get_output_names(self) -> list[str]:
        self.enumeration_count += 1
        return list(self.outputs)

    def open_output(self, name: str) -> FakePort:
        self.opened_names.append(name)
        if name not in self.outputs:
            raise OSError("MIDI output is unavailable")
        port = FakePort(
            send_error=self._send_error,
            close_error=self._close_error,
        )
        self.ports.append(port)
        return port

    def message(
        self, message_type: str, **values: object
    ) -> tuple[str, dict[str, object]]:
        return message_type, values
