from __future__ import annotations

import threading
from typing import Any, cast

import pytest

from daemon.errors import MidiDispatchError, MidiPortNotFoundError
from daemon.midi_transport import MidiBackend, MidiTransport
from tests.fakes import FakeMidoBackend, FakePort


def test_list_outputs_holds_the_transport_lock_during_enumeration() -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingBackend:
        def get_output_names(self) -> list[str]:
            entered.set()
            assert release.wait(timeout=2), "test deadlocked"
            return ["IAC Driver Bus 1"]

        def open_output(self, name: str) -> Any:
            raise AssertionError("not exercised by this test")

        def message(self, message_type: str, **values: Any) -> object:
            raise AssertionError("not exercised by this test")

    transport = MidiTransport(
        output_name="IAC Driver Bus 1",
        backend=cast(MidiBackend, BlockingBackend()),
    )

    thread = threading.Thread(target=transport.list_outputs)
    thread.start()
    try:
        assert entered.wait(timeout=1), "list_outputs never started"
        # While enumeration is in flight, the transport's own lock must
        # already be held -- a concurrent dispatch would otherwise be free
        # to open/close the port at the same time.
        assert transport._lock.acquire(blocking=False) is False  # noqa: SLF001
    finally:
        release.set()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_send_cc_opens_configured_port_lazily_and_reuses_it() -> None:
    backend = FakeMidoBackend(["IAC Driver AI_Logic_Bridge"])
    transport = MidiTransport(
        output_name="IAC Driver AI_Logic_Bridge",
        backend=backend,
    )

    transport.send_cc(channel=0, control=7, value=64)
    transport.send_cc(channel=0, control=7, value=65)

    assert backend.opened_names == ["IAC Driver AI_Logic_Bridge"]
    assert backend.enumeration_count == 0
    assert backend.port.messages == [
        ("control_change", {"channel": 0, "control": 7, "value": 64}),
        ("control_change", {"channel": 0, "control": 7, "value": 65}),
    ]


def test_missing_configured_port_reports_available_outputs() -> None:
    backend = FakeMidoBackend(["IAC Driver Bus 1", "External Controller"])
    transport = MidiTransport(
        output_name="IAC Driver AI_Logic_Bridge",
        backend=backend,
    )

    with pytest.raises(MidiPortNotFoundError) as caught:
        transport.send_cc(channel=0, control=7, value=64)

    assert caught.value.details["expected"] == "IAC Driver AI_Logic_Bridge"
    assert caught.value.details["available"] == [
        "IAC Driver Bus 1",
        "External Controller",
    ]


def test_transport_validates_raw_midi_ranges() -> None:
    transport = MidiTransport(
        output_name="IAC Driver AI_Logic_Bridge",
        backend=FakeMidoBackend(["IAC Driver AI_Logic_Bridge"]),
    )

    with pytest.raises(ValueError, match="channel"):
        transport.send_cc(channel=16, control=7, value=64)


def test_send_failure_preserves_original_error_when_cleanup_also_fails() -> None:
    send_error = OSError("send failed")
    cleanup_error = RuntimeError("close failed")
    backend = FakeMidoBackend(
        ["IAC Driver AI_Logic_Bridge"],
        send_error=send_error,
        close_error=cleanup_error,
    )
    transport = MidiTransport(
        output_name="IAC Driver AI_Logic_Bridge",
        backend=backend,
    )

    with pytest.raises(MidiDispatchError) as caught:
        transport.send_cc(channel=0, control=7, value=64)

    assert caught.value.__cause__ is send_error
    assert caught.value.details == {
        "output_name": "IAC Driver AI_Logic_Bridge",
        "exception_type": "OSError",
        "successful_send_count": 0,
        "message_count": 1,
        "cleanup_exception_type": "RuntimeError",
    }


def test_fake_port_rejects_sends_after_close() -> None:
    port = FakePort()
    port.close()

    with pytest.raises(ValueError, match="closed port"):
        port.send(("control_change", {"channel": 0, "control": 7, "value": 64}))


def test_transport_reopens_with_a_fresh_port_after_close() -> None:
    backend = FakeMidoBackend(["IAC Driver AI_Logic_Bridge"])
    transport = MidiTransport(
        output_name="IAC Driver AI_Logic_Bridge",
        backend=backend,
    )

    transport.send_cc(channel=0, control=7, value=64)
    first_port = backend.port
    transport.close()
    transport.send_cc(channel=0, control=7, value=65)
    second_port = backend.port

    assert first_port.closed is True
    assert second_port is not first_port
    assert second_port.closed is False
    assert backend.opened_names == [
        "IAC Driver AI_Logic_Bridge",
        "IAC Driver AI_Logic_Bridge",
    ]
    assert first_port.messages == [
        ("control_change", {"channel": 0, "control": 7, "value": 64})
    ]
    assert second_port.messages == [
        ("control_change", {"channel": 0, "control": 7, "value": 65})
    ]


def test_control_change_trigger_pair_is_sent_inside_one_dispatch() -> None:
    backend = FakeMidoBackend(["IAC Driver AI_Logic_Bridge"])
    transport = MidiTransport(
        output_name="IAC Driver AI_Logic_Bridge",
        backend=backend,
    )

    count = transport.send_cc(
        channel=0,
        control=20,
        value=127,
        release_value=0,
    )

    assert count == 2
    assert backend.port.messages == [
        ("control_change", {"channel": 0, "control": 20, "value": 127}),
        ("control_change", {"channel": 0, "control": 20, "value": 0}),
    ]


def test_note_trigger_emits_one_on_and_one_off() -> None:
    backend = FakeMidoBackend(["IAC Driver AI_Logic_Bridge"])
    transport = MidiTransport(
        output_name="IAC Driver AI_Logic_Bridge",
        backend=backend,
    )

    count = transport.send_note(
        channel=2,
        note=60,
        velocity=100,
        release_velocity=12,
    )

    assert count == 2
    assert backend.port.messages == [
        ("note_on", {"channel": 2, "note": 60, "velocity": 100}),
        ("note_off", {"channel": 2, "note": 60, "velocity": 12}),
    ]


def test_sysex_payload_uses_mido_framing_without_status_bytes() -> None:
    backend = FakeMidoBackend(["IAC Driver AI_Logic_Bridge"])
    transport = MidiTransport(
        output_name="IAC Driver AI_Logic_Bridge",
        backend=backend,
    )

    count = transport.send_sysex(data=(1, 2, 127))

    assert count == 1
    assert backend.port.messages == [("sysex", {"data": (1, 2, 127)})]


@pytest.mark.parametrize("payload", [(), (0xF0, 1), tuple(range(127)) * 3])
def test_sysex_rejects_empty_status_byte_or_oversize_payload(
    payload: tuple[int, ...],
) -> None:
    transport = MidiTransport(
        output_name="IAC Driver AI_Logic_Bridge",
        backend=FakeMidoBackend(["IAC Driver AI_Logic_Bridge"]),
    )

    with pytest.raises(ValueError, match="SysEx"):
        transport.send_sysex(data=payload)


def test_partial_pair_failure_reports_successful_send_count() -> None:
    class SecondSendFailsPort(FakePort):
        def send(self, message: object) -> None:
            if len(self.messages) == 1:
                raise OSError("second send failed")
            super().send(message)

    class SecondSendFailsBackend(FakeMidoBackend):
        def open_output(self, name: str) -> FakePort:
            self.opened_names.append(name)
            port = SecondSendFailsPort()
            self.ports.append(port)
            return port

    backend = SecondSendFailsBackend(["IAC Driver AI_Logic_Bridge"])
    transport = MidiTransport(
        output_name="IAC Driver AI_Logic_Bridge",
        backend=backend,
    )

    with pytest.raises(MidiDispatchError) as raised:
        transport.send_cc(
            channel=0,
            control=20,
            value=127,
            release_value=0,
        )

    assert raised.value.details["successful_send_count"] == 1
    assert raised.value.retryable is False
    assert raised.value.details["message_count"] == 2
    assert backend.port.closed is True


def test_send_note_attempts_a_best_effort_note_off_after_a_stuck_note() -> None:
    class NoteOffFailsPort(FakePort):
        def send(self, message: object) -> None:
            if len(self.messages) == 1:
                raise OSError("note_off failed")
            super().send(message)

    class RecoveringBackend(FakeMidoBackend):
        def open_output(self, name: str) -> FakePort:
            self.opened_names.append(name)
            # First port: note_on succeeds, note_off fails. Second port
            # (opened by the recovery attempt): accepts the note_off.
            port = NoteOffFailsPort() if not self.ports else FakePort()
            self.ports.append(port)
            return port

    backend = RecoveringBackend(["IAC Driver AI_Logic_Bridge"])
    transport = MidiTransport(
        output_name="IAC Driver AI_Logic_Bridge",
        backend=backend,
    )

    with pytest.raises(MidiDispatchError) as raised:
        transport.send_note(channel=0, note=60, velocity=100, release_velocity=0)

    assert raised.value.details["successful_send_count"] == 1
    assert raised.value.details["stuck_note_recovery"] == "sent"
    assert backend.opened_names == [
        "IAC Driver AI_Logic_Bridge",
        "IAC Driver AI_Logic_Bridge",
    ]
    assert backend.ports[1].messages == [
        ("note_off", {"channel": 0, "note": 60, "velocity": 0}),
    ]


def test_send_note_records_a_failed_recovery_when_the_port_stays_unavailable() -> None:
    class NoteOffFailsPort(FakePort):
        def send(self, message: object) -> None:
            if len(self.messages) == 1:
                raise OSError("note_off failed")
            super().send(message)

    class NeverRecoveringBackend(FakeMidoBackend):
        def open_output(self, name: str) -> FakePort:
            if self.ports:
                raise OSError("device unavailable")
            self.opened_names.append(name)
            port = NoteOffFailsPort()
            self.ports.append(port)
            return port

    backend = NeverRecoveringBackend(["IAC Driver AI_Logic_Bridge"])
    transport = MidiTransport(
        output_name="IAC Driver AI_Logic_Bridge",
        backend=backend,
    )

    with pytest.raises(MidiDispatchError) as raised:
        transport.send_note(channel=0, note=60, velocity=100, release_velocity=0)

    assert raised.value.details["successful_send_count"] == 1
    assert raised.value.details["stuck_note_recovery"] == "failed"
