from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from typing import Any, Protocol, cast

from daemon.errors import (
    BridgeError,
    MidiBackendUnavailableError,
    MidiDispatchError,
    MidiPortNotFoundError,
)


class OutputPort(Protocol):
    def send(self, message: object) -> None: ...

    def close(self) -> None: ...


class MidiBackend(Protocol):
    def get_output_names(self) -> list[str]: ...

    def open_output(self, name: str) -> OutputPort: ...

    def message(self, message_type: str, **values: Any) -> object: ...


class MidoBackend:
    """Small adapter that keeps the optional native backend behind one seam."""

    def __init__(self) -> None:
        try:
            import mido
        except ImportError as exc:
            raise MidiBackendUnavailableError(
                "Mido is not installed; install the project dependencies"
            ) from exc
        self._mido: Any = mido
        self._backend = mido.Backend("mido.backends.rtmidi")

    def get_output_names(self) -> list[str]:
        return list(self._backend.get_output_names())

    def open_output(self, name: str) -> OutputPort:
        return cast(OutputPort, self._backend.open_output(name))

    def message(self, message_type: str, **values: Any) -> object:
        return self._mido.Message(message_type, **values)


class MidiTransport:
    """Thread-safe, lazy connection to one configured MIDI output."""

    def __init__(self, *, output_name: str, backend: MidiBackend | None = None) -> None:
        self.output_name = output_name
        self._backend = backend or MidoBackend()
        self._port: OutputPort | None = None
        self._lock = threading.RLock()

    def list_outputs(self) -> list[str]:
        with self._lock:
            try:
                return list(self._backend.get_output_names())
            except BridgeError:
                raise
            except Exception as exc:
                raise MidiBackendUnavailableError(
                    "Unable to enumerate MIDI output ports",
                    details={"exception_type": type(exc).__name__},
                ) from exc

    def send_cc(
        self,
        *,
        channel: int,
        control: int,
        value: int,
        release_value: int | None = None,
    ) -> int:
        _validate_range("channel", channel, 0, 15)
        _validate_range("control", control, 0, 127)
        _validate_range("value", value, 0, 127)
        if release_value is not None:
            _validate_range("release_value", release_value, 0, 127)
        messages: list[tuple[str, Mapping[str, Any]]] = [
            (
                "control_change",
                {"channel": channel, "control": control, "value": value},
            )
        ]
        if release_value is not None:
            messages.append(
                (
                    "control_change",
                    {
                        "channel": channel,
                        "control": control,
                        "value": release_value,
                    },
                )
            )
        return self._send_messages(messages)

    def send_note(
        self,
        *,
        channel: int,
        note: int,
        velocity: int,
        release_velocity: int,
    ) -> int:
        _validate_range("channel", channel, 0, 15)
        _validate_range("note", note, 0, 127)
        _validate_range("velocity", velocity, 1, 127)
        _validate_range("release_velocity", release_velocity, 0, 127)
        return self._send_messages(
            (
                (
                    "note_on",
                    {"channel": channel, "note": note, "velocity": velocity},
                ),
                (
                    "note_off",
                    {
                        "channel": channel,
                        "note": note,
                        "velocity": release_velocity,
                    },
                ),
            )
        )

    def send_sysex(self, *, data: tuple[int, ...]) -> int:
        if not 1 <= len(data) <= 256:
            raise ValueError("SysEx data must contain between 1 and 256 bytes")
        for value in data:
            try:
                _validate_range("SysEx data byte", value, 0, 127)
            except ValueError as exc:
                raise ValueError("SysEx data bytes must be between 0 and 127") from exc
        return self._send_messages((("sysex", {"data": data}),))

    def _send_messages(
        self,
        messages: Sequence[tuple[str, Mapping[str, Any]]],
    ) -> int:
        if not 1 <= len(messages) <= 2:
            raise ValueError("A MIDI action must contain one or two messages")
        with self._lock:
            port = self._port or self._open_configured_port()
            successful_send_count = 0
            try:
                for message_type, values in messages:
                    message = self._backend.message(message_type, **values)
                    port.send(message)
                    successful_send_count += 1
            except Exception as exc:
                details = {
                    "output_name": self.output_name,
                    "exception_type": type(exc).__name__,
                    "successful_send_count": successful_send_count,
                    "message_count": len(messages),
                }
                try:
                    self._close_unlocked()
                except Exception as cleanup_exc:
                    details["cleanup_exception_type"] = type(cleanup_exc).__name__
                if (
                    successful_send_count == 1
                    and len(messages) == 2
                    and messages[0][0] == "note_on"
                    and messages[1][0] == "note_off"
                ):
                    # note_on landed but note_off did not: best-effort turn
                    # the stuck note off rather than leaving it held forever.
                    # This is cleanup for a note already sent, not a retry of
                    # the failed dispatch, so it does not affect retryable.
                    details["stuck_note_recovery"] = self._best_effort_note_off(
                        messages[1][1]
                    )
                raise MidiDispatchError(
                    "MIDI dispatch failed",
                    details=details,
                ) from exc
            return successful_send_count

    def _best_effort_note_off(self, note_off_values: Mapping[str, Any]) -> str:
        try:
            port = self._open_configured_port()
            message = self._backend.message("note_off", **note_off_values)
            port.send(message)
        except Exception:
            return "failed"
        return "sent"

    def close(self) -> None:
        with self._lock:
            self._close_unlocked()

    def _open_configured_port(self) -> OutputPort:
        try:
            self._port = self._backend.open_output(self.output_name)
        except Exception as exc:
            available = self.list_outputs()
            if self.output_name not in available:
                raise MidiPortNotFoundError(
                    "Configured MIDI output port is not available",
                    details={"expected": self.output_name, "available": available},
                ) from exc
            raise MidiDispatchError(
                "Configured MIDI output port could not be opened",
                details={
                    "output_name": self.output_name,
                    "exception_type": type(exc).__name__,
                },
            ) from exc
        return self._port

    def _close_unlocked(self) -> None:
        if self._port is None:
            return
        try:
            self._port.close()
        finally:
            self._port = None


def _validate_range(name: str, value: int, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
