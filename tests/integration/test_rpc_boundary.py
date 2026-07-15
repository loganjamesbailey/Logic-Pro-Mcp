from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest

import daemon.__main__ as cli
from daemon.commands import LogicCommandService
from daemon.config import BridgeConfig
from daemon.errors import ConfigurationError
from daemon.rpc_contract import (
    AUTH_PROTOCOL,
    CONTRACT_VERSION,
    METHOD_SPECS,
    authentication_request_proof,
    authentication_server_proof,
    request_schema,
)
from daemon.server import JsonRpcServer
from daemon.scripter import SCRIPT_SHA256
from tests.fakes import (
    RecordingCstPresetLoader,
    RecordingMidiTransport,
    valid_mapping,
)


VALID_TOKEN = "v" * 32
WRONG_TOKEN = "w" * 32


def _request(
    method: str,
    *,
    request_id: str | int = 1,
    params: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {},
        "meta": {},
    }


async def _begin_handshake(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    client_nonce: str = "c" * 64,
) -> tuple[str, str]:
    writer.write(
        json.dumps(
            {"auth": AUTH_PROTOCOL, "client_nonce": client_nonce},
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    await writer.drain()
    server_hello = json.loads(await reader.readline())
    assert server_hello["auth"] == AUTH_PROTOCOL
    assert server_hello["client_nonce"] == client_nonce
    assert server_hello["server_proof"] == authentication_server_proof(
        VALID_TOKEN,
        client_nonce=client_nonce,
        server_nonce=server_hello["server_nonce"],
    )
    return client_nonce, server_hello["server_nonce"]


def _authenticated_payload(
    payload: dict[str, object],
    *,
    token: str,
    client_nonce: str,
    server_nonce: str,
) -> dict[str, object]:
    meta = dict(payload.get("meta", {}))
    meta.update(
        {
            "auth": AUTH_PROTOCOL,
            "client_nonce": client_nonce,
            "server_nonce": server_nonce,
            "request_proof": "",
        }
    )
    authenticated = {**payload, "meta": meta}
    meta["request_proof"] = authentication_request_proof(token, authenticated)
    return authenticated


async def _rpc_request(
    port: int,
    payload: dict[str, object],
    *,
    token: str = VALID_TOKEN,
) -> dict[str, object]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        client_nonce, server_nonce = await _begin_handshake(reader, writer)
        authenticated = _authenticated_payload(
            payload,
            token=token,
            client_nonce=client_nonce,
            server_nonce=server_nonce,
        )
        writer.write(json.dumps(authenticated).encode("utf-8") + b"\n")
        await writer.drain()
        return json.loads(await reader.readline())
    finally:
        writer.close()
        await writer.wait_closed()


def _server_config(**overrides: object) -> BridgeConfig:
    raw = valid_mapping()
    raw["server"] = {  # type: ignore[assignment]
        **raw["server"],  # type: ignore[arg-type]
        "port": 0,
        **overrides,
    }
    return BridgeConfig.from_mapping(raw)


def _generic_action_server_config() -> BridgeConfig:
    raw = valid_mapping()
    raw["server"] = {**raw["server"], "port": 0}  # type: ignore[arg-type]
    midi = raw["midi"]
    assert isinstance(midi, dict)
    assignments = midi["assignments"]
    assert isinstance(assignments, list)
    assignments.append(
        {
            "name": "device.private.sysex",
            "kind": "sysex",
            "data": [0, 32, 51, 9],
            "value_mode": "trigger",
        }
    )
    return BridgeConfig.from_mapping(raw)


def _cst_server_config() -> BridgeConfig:
    raw = valid_mapping()
    raw["server"] = {**raw["server"], "port": 0}  # type: ignore[arg-type]
    raw["accessibility"] = {
        "enabled": True,
        "preset_root": "/Users/private/Music/Channel Strip Settings/Track",
        "presets": [
            {
                "id": "preset.lead-vocal",
                "filename": "Secret Session Vocal.cst",
                "sha256": "a" * 64,
            }
        ],
        "targets": [
            {
                "id": "track.lead-vocal",
                "mixer_index": 0,
                "expected_accessibility_name": "Secret Artist Vocal",
            }
        ],
    }
    return BridgeConfig.from_mapping(raw)


def _scripter_server_config() -> BridgeConfig:
    raw = valid_mapping()
    raw["server"] = {**raw["server"], "port": 0}  # type: ignore[arg-type]
    raw["scripter"] = {
        "enabled": True,
        "protocol_version": 1,
        "channel": 15,
        "source_sha256": SCRIPT_SHA256,
        "placement": "software_instrument_midi_fx",
        "operator_attested": True,
        "learned_targets": [
            {
                "parameter_id": "scripter.retro-synth.filter-cutoff",
                "control": 102,
                "target_slot": 1,
                "target_name": "Retro Synth Filter Cutoff",
            },
            {
                "parameter_id": "scripter.retro-synth.filter-resonance",
                "control": 103,
                "target_slot": 2,
                "target_name": "Filter Resonance",
            },
        ],
    }
    return BridgeConfig.from_mapping(raw)


def test_loopback_rpc_requires_token_before_mutating_state() -> None:
    async def scenario() -> None:
        config = _server_config()
        transport = RecordingMidiTransport()
        service = LogicCommandService(config=config, midi=transport)
        server = JsonRpcServer(
            config=config.server, service=service, auth_token=VALID_TOKEN
        )

        await server.start()
        try:
            response = await _rpc_request(
                server.bound_port,
                _request(
                    "logic.set_volume",
                    params={"track_id": "selected", "value": 0.25},
                ),
            )

            assert response["id"] == 1
            assert response["result"]["events"][-1]["state"] == "unknown"
            assert transport.sent == [(0, 7, 32)]

            response = await _rpc_request(
                server.bound_port,
                _request(
                    "logic.set_volume",
                    request_id=2,
                    params={"track_id": "selected", "value": 0.75},
                ),
                token=WRONG_TOKEN,
            )

            assert response["error"]["data"]["bridge_code"] == "AUTHENTICATION_REQUIRED"
            assert WRONG_TOKEN not in repr(response)
            assert transport.sent == [(0, 7, 32)]
        finally:
            await server.close()

    asyncio.run(scenario())


def test_replayed_request_proof_fails_on_a_fresh_connection() -> None:
    async def scenario() -> None:
        config = _server_config()
        transport = RecordingMidiTransport()
        server = JsonRpcServer(
            config=config.server,
            service=LogicCommandService(config=config, midi=transport),
            auth_token=VALID_TOKEN,
        )

        await server.start()
        first_reader, first_writer = await asyncio.open_connection(
            "127.0.0.1", server.bound_port
        )
        second_reader: asyncio.StreamReader | None = None
        second_writer: asyncio.StreamWriter | None = None
        try:
            client_nonce, first_server_nonce = await _begin_handshake(
                first_reader,
                first_writer,
            )
            captured_request = _authenticated_payload(
                _request("logic.set_volume", params={"value": 0.25}),
                token=VALID_TOKEN,
                client_nonce=client_nonce,
                server_nonce=first_server_nonce,
            )
            first_writer.write(json.dumps(captured_request).encode("utf-8") + b"\n")
            await first_writer.drain()
            first_response = json.loads(await first_reader.readline())
            assert first_response["result"]["events"][-1]["state"] == "unknown"

            second_reader, second_writer = await asyncio.open_connection(
                "127.0.0.1", server.bound_port
            )
            _, second_server_nonce = await _begin_handshake(
                second_reader,
                second_writer,
                client_nonce=client_nonce,
            )
            assert second_server_nonce != first_server_nonce
            second_writer.write(json.dumps(captured_request).encode("utf-8") + b"\n")
            await second_writer.drain()
            replay_response = json.loads(await second_reader.readline())
            assert replay_response["error"]["data"]["bridge_code"] == (
                "AUTHENTICATION_REQUIRED"
            )
            assert transport.sent == [(0, 7, 32)]
        finally:
            first_writer.close()
            await first_writer.wait_closed()
            if second_writer is not None:
                second_writer.close()
                await second_writer.wait_closed()
            await server.close()

    asyncio.run(scenario())


def test_wrong_request_proof_fails_before_dispatch() -> None:
    async def scenario() -> None:
        config = _server_config()
        transport = RecordingMidiTransport()
        server = JsonRpcServer(
            config=config.server,
            service=LogicCommandService(config=config, midi=transport),
            auth_token=VALID_TOKEN,
        )

        await server.start()
        reader, writer = await asyncio.open_connection("127.0.0.1", server.bound_port)
        try:
            client_nonce, server_nonce = await _begin_handshake(reader, writer)
            payload = _authenticated_payload(
                _request("logic.toggle_mute"),
                token=VALID_TOKEN,
                client_nonce=client_nonce,
                server_nonce=server_nonce,
            )
            payload["meta"]["request_proof"] = "0" * 64  # type: ignore[index]
            writer.write(json.dumps(payload).encode("utf-8") + b"\n")
            await writer.drain()
            response = json.loads(await reader.readline())
            assert response["error"]["data"]["bridge_code"] == (
                "AUTHENTICATION_REQUIRED"
            )
            assert transport.sent == []
        finally:
            writer.close()
            await writer.wait_closed()
            await server.close()

    asyncio.run(scenario())


def test_malformed_handshake_json_fails_closed() -> None:
    async def scenario() -> None:
        config = _server_config()
        transport = RecordingMidiTransport()
        server = JsonRpcServer(
            config=config.server,
            service=LogicCommandService(config=config, midi=transport),
            auth_token=VALID_TOKEN,
        )
        deep = (b"[" * 80) + b"null" + (b"]" * 80)
        malformed = (
            (
                b'{"auth":"'
                + AUTH_PROTOCOL.encode("ascii")
                + b'","client_nonce":"'
                + (b"c" * 64)
                + b'","client_nonce":"'
                + (b"c" * 64)
                + b'"}\n'
            ),
            (b'{"auth":"' + AUTH_PROTOCOL.encode("ascii") + b'","client_nonce":NaN}\n'),
            b"\xff\n",
            (
                b'{"auth":"'
                + AUTH_PROTOCOL.encode("ascii")
                + b'","client_nonce":"'
                + (b"c" * 64)
                + b'","deep":'
                + deep
                + b"}\n"
            ),
        )

        await server.start()
        try:
            for raw in malformed:
                reader, writer = await asyncio.open_connection(
                    "127.0.0.1", server.bound_port
                )
                try:
                    writer.write(raw)
                    await writer.drain()
                    response = json.loads(await reader.readline())
                    assert response["error"]["data"]["bridge_code"] == (
                        "AUTHENTICATION_REQUIRED"
                    )
                    assert await reader.readline() == b""
                finally:
                    writer.close()
                    await writer.wait_closed()
            assert transport.sent == []
        finally:
            await server.close()

    asyncio.run(scenario())


def test_malformed_authenticated_request_json_fails_closed() -> None:
    async def scenario() -> None:
        config = _server_config()
        transport = RecordingMidiTransport()
        server = JsonRpcServer(
            config=config.server,
            service=LogicCommandService(config=config, midi=transport),
            auth_token=VALID_TOKEN,
        )
        deep = (b"[" * 80) + b"null" + (b"]" * 80)
        malformed = (
            b'{"jsonrpc":"2.0","jsonrpc":"2.0","id":1}\n',
            b'{"jsonrpc":"2.0","id":1,"params":{"value":NaN}}\n',
            b"\xff\n",
            b'{"jsonrpc":"2.0","id":1,"params":{"deep":' + deep + b"}}\n",
        )

        await server.start()
        try:
            for raw in malformed:
                reader, writer = await asyncio.open_connection(
                    "127.0.0.1", server.bound_port
                )
                try:
                    await _begin_handshake(reader, writer)
                    writer.write(raw)
                    await writer.drain()
                    response = json.loads(await reader.readline())
                    assert response["error"]["code"] == -32700
                    assert await reader.readline() == b""
                finally:
                    writer.close()
                    await writer.wait_closed()
            assert transport.sent == []
        finally:
            await server.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("token", [None, "", "x" * 31, "é" * 15])
def test_server_refuses_to_start_without_a_strong_token(token: str | None) -> None:
    config = _server_config()

    with pytest.raises(ConfigurationError, match="at least 32 encoded bytes") as raised:
        JsonRpcServer(
            config=config.server,
            service=LogicCommandService(config=config, midi=RecordingMidiTransport()),
            auth_token=token,
        )

    assert not token or token not in repr(raised.value)


def test_server_accepts_a_32_encoded_byte_token() -> None:
    config = _server_config()
    service = LogicCommandService(config=config, midi=RecordingMidiTransport())
    server = JsonRpcServer(
        config=config.server,
        service=service,
        auth_token="é" * 16,
    )

    asyncio.run(server.close())


def test_cli_rejects_short_token_before_constructing_transports(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _server_config()
    service_constructed = False

    def service_must_not_be_constructed(_config: BridgeConfig) -> LogicCommandService:
        nonlocal service_constructed
        service_constructed = True
        raise AssertionError("transport-backed service was constructed")

    monkeypatch.setattr(cli, "load_config", lambda _path: config)
    monkeypatch.setattr(cli, "_service", service_must_not_be_constructed)
    monkeypatch.setenv("LOGIC_BRIDGE_TOKEN", "x" * 31)

    assert cli.main(["serve"]) == 2
    captured = capsys.readouterr()
    assert service_constructed is False
    assert "x" * 8 not in captured.err
    assert "at least 32 encoded bytes" in captured.err


def test_rpc_rejects_unknown_methods_without_exposing_internal_errors() -> None:
    async def scenario() -> None:
        config = _server_config()
        server = JsonRpcServer(
            config=config.server,
            service=LogicCommandService(config=config, midi=RecordingMidiTransport()),
            auth_token=VALID_TOKEN,
        )

        await server.start()
        try:
            response = await _rpc_request(
                server.bound_port,
                _request("shell.execute", request_id="x"),
            )

            assert response["error"]["code"] == -32601
            assert "traceback" not in repr(response).lower()
        finally:
            await server.close()

    asyncio.run(scenario())


def test_logic_execute_is_not_a_public_rpc_method() -> None:
    async def scenario() -> None:
        config = _server_config()
        transport = RecordingMidiTransport()
        server = JsonRpcServer(
            config=config.server,
            service=LogicCommandService(config=config, midi=transport),
            auth_token=VALID_TOKEN,
        )

        await server.start()
        try:
            response = await _rpc_request(
                server.bound_port,
                _request(
                    "logic.execute",
                    params={"action": "track.selected.mute_toggle"},
                ),
            )

            assert response["error"]["code"] == -32601
            assert transport.sent == []
        finally:
            await server.close()

    asyncio.run(scenario())


def test_invoke_action_is_opaque_and_rejects_raw_midi_fields() -> None:
    async def scenario() -> None:
        config = _generic_action_server_config()
        transport = RecordingMidiTransport()
        server = JsonRpcServer(
            config=config.server,
            service=LogicCommandService(config=config, midi=transport),
            auth_token=VALID_TOKEN,
        )

        await server.start()
        try:
            dispatched = await _rpc_request(
                server.bound_port,
                _request(
                    "logic.invoke_action",
                    params={"action_id": "device.private.sysex"},
                ),
            )
            assert dispatched["result"]["events"][-1]["state"] == "unknown"
            assert transport.sysex_calls == [(0, 32, 51, 9)]

            for forbidden in ("kind", "channel", "control", "note", "data"):
                response = await _rpc_request(
                    server.bound_port,
                    _request(
                        "logic.invoke_action",
                        request_id=forbidden,
                        params={
                            "action_id": "device.private.sysex",
                            forbidden: [99] if forbidden == "data" else 1,
                        },
                    ),
                )
                assert response["error"]["code"] == -32602
            assert transport.sysex_calls == [(0, 32, 51, 9)]
        finally:
            await server.close()

    asyncio.run(scenario())


def test_capabilities_and_published_schema_share_the_runtime_contract() -> None:
    async def scenario() -> None:
        config = _server_config()
        server = JsonRpcServer(
            config=config.server,
            service=LogicCommandService(config=config, midi=RecordingMidiTransport()),
            auth_token=VALID_TOKEN,
        )

        await server.start()
        try:
            response = await _rpc_request(
                server.bound_port,
                _request("bridge.capabilities"),
            )

            assert response["result"]["rpc_methods"] == list(METHOD_SPECS)
            assert response["result"]["contract"]["version"] == CONTRACT_VERSION
            methods = response["result"]["contract"]["methods"]
            assert set(methods) == set(METHOD_SPECS)
            assert methods["logic.set_volume"]["availability"]["prerequisites"][
                "controller_assignment_ids"
            ] == ["track.selected.volume"]
            assert methods["logic.toggle_mute"]["lifecycle"]["states"] == [
                "requested",
                "dispatched",
                "unknown",
            ]
        finally:
            await server.close()

    asyncio.run(scenario())

    published = json.loads(
        (Path(__file__).parents[2] / "config" / "command_schema.json").read_text()
    )
    assert published == request_schema()
    assert "logic.execute" not in json.dumps(published)
    for spec in METHOD_SPECS.values():
        assert callable(getattr(LogicCommandService, spec.service_method, None))


def test_capability_resource_checks_do_not_block_midi_rpc_dispatch() -> None:
    async def scenario() -> None:
        config = _server_config()
        entered = threading.Event()
        release = threading.Event()

        class BlockingStatusLoader(RecordingCstPresetLoader):
            def status(self) -> dict[str, object]:
                entered.set()
                assert release.wait(timeout=2)
                return super().status()

        loader = BlockingStatusLoader()
        transport = RecordingMidiTransport()
        server = JsonRpcServer(
            config=config.server,
            service=LogicCommandService(
                config=config,
                midi=transport,
                cst_loader=loader,
            ),
            auth_token=VALID_TOKEN,
        )

        await server.start()
        capabilities_task = asyncio.create_task(
            _rpc_request(server.bound_port, _request("bridge.capabilities"))
        )
        try:
            assert await asyncio.to_thread(entered.wait, 1.0)
            volume = await asyncio.wait_for(
                _rpc_request(
                    server.bound_port,
                    _request(
                        "logic.set_volume",
                        request_id=2,
                        params={"value": 0.5},
                    ),
                ),
                timeout=0.5,
            )
            assert volume["result"]["events"][-1]["state"] == "unknown"
            assert transport.sent == [(0, 7, 64)]
        finally:
            release.set()
            await capabilities_task
            await server.close()

    asyncio.run(scenario())


def test_runtime_requires_the_exact_request_and_meta_envelope() -> None:
    async def scenario() -> None:
        config = _server_config()
        server = JsonRpcServer(
            config=config.server,
            service=LogicCommandService(config=config, midi=RecordingMidiTransport()),
            auth_token=VALID_TOKEN,
        )

        await server.start()
        try:
            missing_params = _request("bridge.health")
            del missing_params["params"]
            extra_request_field = {**_request("bridge.health"), "debug": True}
            extra_meta_field = _request("bridge.health")
            extra_meta_field["meta"] = {"debug": True}
            empty_id = _request("bridge.health")
            empty_id["id"] = ""

            for payload in (
                missing_params,
                extra_request_field,
                extra_meta_field,
                empty_id,
            ):
                response = await _rpc_request(server.bound_port, payload)
                assert response["error"]["code"] == -32600
        finally:
            await server.close()

    asyncio.run(scenario())


def test_runtime_parameter_validation_matches_method_specs() -> None:
    async def scenario() -> None:
        config = _server_config()
        transport = RecordingMidiTransport()
        server = JsonRpcServer(
            config=config.server,
            service=LogicCommandService(config=config, midi=transport),
            auth_token=VALID_TOKEN,
        )

        await server.start()
        try:
            invalid_payloads = (
                _request("logic.set_volume", params={"value": 1.1}),
                _request("logic.set_volume", params={"track_id": False, "value": 0.5}),
                _request(
                    "logic.set_volume",
                    params={"track_id": "1" * 21, "value": 0.5},
                ),
                _request(
                    "logic.set_volume",
                    params={"track_id": 10**20, "value": 0.5},
                ),
                _request("logic.toggle_mute", params={"unknown": True}),
            )
            for payload in invalid_payloads:
                response = await _rpc_request(server.bound_port, payload)
                assert response["error"]["data"]["bridge_code"] == "INVALID_PARAMETERS"
                assert [
                    event["state"]
                    for event in response["error"]["data"]["details"]["events"]
                ] == ["requested"]
            assert transport.sent == []
        finally:
            await server.close()

    asyncio.run(scenario())


def test_authenticated_cst_load_uses_only_opaque_ids_at_rpc_boundary() -> None:
    async def scenario() -> None:
        config = _cst_server_config()
        loader = RecordingCstPresetLoader()
        server = JsonRpcServer(
            config=config.server,
            service=LogicCommandService(
                config=config,
                midi=RecordingMidiTransport(),
                cst_loader=loader,
            ),
            auth_token=VALID_TOKEN,
        )

        await server.start()
        try:
            response = await _rpc_request(
                server.bound_port,
                _request(
                    "logic.load_cst_preset",
                    params={
                        "preset_id": "preset.lead-vocal",
                        "target_id": "track.lead-vocal",
                        "confirmation": (
                            "load preset.lead-vocal into track.lead-vocal in "
                            "disposable project"
                        ),
                    },
                ),
            )

            assert [event["state"] for event in response["result"]["events"]] == [
                "requested",
                "dispatched",
                "observed",
                "unknown",
            ]
            assert loader.loaded == [("preset.lead-vocal", "track.lead-vocal")]
            rendered = repr(response)
            assert "/Users/" not in rendered
            assert "Secret Session" not in rendered
            assert "Secret Artist" not in rendered
        finally:
            await server.close()

    asyncio.run(scenario())


def test_authenticated_mixer_inspection_returns_only_safe_scoped_evidence() -> None:
    async def scenario() -> None:
        config = _cst_server_config()
        loader = RecordingCstPresetLoader()
        server = JsonRpcServer(
            config=config.server,
            service=LogicCommandService(
                config=config,
                midi=RecordingMidiTransport(),
                cst_loader=loader,
            ),
            auth_token=VALID_TOKEN,
        )

        await server.start()
        try:
            response = await _rpc_request(
                server.bound_port,
                _request(
                    "logic.inspect_mixer_target",
                    params={"target_id": "track.lead-vocal"},
                ),
            )
            assert response["result"]["target_id"] == "track.lead-vocal"
            assert response["result"]["verification_scope"] == (
                "visible_plugin_slot_labels"
            )
            assert loader.inspected == ["track.lead-vocal"]
            assert "Secret Artist" not in repr(response)
            assert "/Users/" not in repr(response)
        finally:
            await server.close()

    asyncio.run(scenario())


def test_authenticated_scripter_rpc_uses_only_fixed_route() -> None:
    async def scenario() -> None:
        config = _scripter_server_config()
        transport = RecordingMidiTransport()
        server = JsonRpcServer(
            config=config.server,
            service=LogicCommandService(config=config, midi=transport),
            auth_token=VALID_TOKEN,
        )

        await server.start()
        try:
            response = await _rpc_request(
                server.bound_port,
                _request(
                    "logic.set_scripter_parameter",
                    params={
                        "parameter_id": "scripter.retro-synth.filter-resonance",
                        "value": 0.5,
                    },
                ),
            )
            assert response["result"]["events"][-1]["state"] == "unknown"
            assert transport.cc_calls == [(15, 103, 64, None)]

            rejected = await _rpc_request(
                server.bound_port,
                _request(
                    "logic.set_scripter_parameter",
                    request_id=2,
                    params={
                        "parameter_id": "scripter.retro-synth.filter-resonance",
                        "value": 0.5,
                        "script": "evil()",
                    },
                ),
            )
            assert rejected["error"]["code"] == -32602
            assert transport.cc_calls == [(15, 103, 64, None)]
        finally:
            await server.close()

    asyncio.run(scenario())


def test_cst_rpc_rejects_paths_and_missing_confirmation_before_dispatch() -> None:
    async def scenario() -> None:
        config = _cst_server_config()
        loader = RecordingCstPresetLoader()
        server = JsonRpcServer(
            config=config.server,
            service=LogicCommandService(
                config=config,
                midi=RecordingMidiTransport(),
                cst_loader=loader,
            ),
            auth_token=VALID_TOKEN,
        )

        await server.start()
        try:
            invalid_payloads = (
                _request(
                    "logic.load_cst_preset",
                    params={
                        "preset_id": "preset.lead-vocal",
                        "target_id": "track.lead-vocal",
                        "confirmation": "cancel",
                    },
                ),
                _request(
                    "logic.load_cst_preset",
                    params={
                        "preset_id": "preset.lead-vocal",
                        "target_id": "track.lead-vocal",
                        "confirmation": (
                            "load preset.lead-vocal into track.lead-vocal in "
                            "disposable project"
                        ),
                        "path": "/tmp/evil.cst",
                    },
                ),
            )
            for payload, expected_code in zip(
                invalid_payloads,
                (-32000, -32602),
                strict=True,
            ):
                response = await _rpc_request(server.bound_port, payload)
                assert response["error"]["code"] == expected_code
                assert [
                    event["state"]
                    for event in response["error"]["data"]["details"]["events"]
                ] == ["requested"]
            assert loader.loaded == []
        finally:
            await server.close()

    asyncio.run(scenario())


def test_idle_connection_is_timed_out_and_closed() -> None:
    async def scenario() -> None:
        config = _server_config(read_timeout_seconds=0.05)
        server = JsonRpcServer(
            config=config.server,
            service=LogicCommandService(config=config, midi=RecordingMidiTransport()),
            auth_token=VALID_TOKEN,
        )

        await server.start()
        reader, writer = await asyncio.open_connection("127.0.0.1", server.bound_port)
        try:
            response = json.loads(await asyncio.wait_for(reader.readline(), timeout=1))
            assert response["error"]["data"]["bridge_code"] == "REQUEST_TIMEOUT"
            assert await asyncio.wait_for(reader.readline(), timeout=1) == b""
        finally:
            writer.close()
            await writer.wait_closed()
            await server.close()

    asyncio.run(scenario())


def test_connection_limit_rejects_excess_clients() -> None:
    async def scenario() -> None:
        config = _server_config(max_connections=1, read_timeout_seconds=1.0)
        server = JsonRpcServer(
            config=config.server,
            service=LogicCommandService(config=config, midi=RecordingMidiTransport()),
            auth_token=VALID_TOKEN,
        )

        await server.start()
        first_reader, first_writer = await asyncio.open_connection(
            "127.0.0.1", server.bound_port
        )
        first_writer.write(b"{")
        await first_writer.drain()
        await asyncio.sleep(0.01)
        second_reader, second_writer = await asyncio.open_connection(
            "127.0.0.1", server.bound_port
        )
        try:
            response = json.loads(
                await asyncio.wait_for(second_reader.readline(), timeout=1)
            )
            assert (
                response["error"]["data"]["bridge_code"] == "CONNECTION_LIMIT_REACHED"
            )
            assert await asyncio.wait_for(second_reader.readline(), timeout=1) == b""
        finally:
            first_writer.close()
            second_writer.close()
            await first_writer.wait_closed()
            await second_writer.wait_closed()
            await server.close()

    asyncio.run(scenario())


def test_server_close_closes_active_clients_cleanly() -> None:
    async def scenario() -> None:
        config = _server_config(read_timeout_seconds=60.0)
        server = JsonRpcServer(
            config=config.server,
            service=LogicCommandService(config=config, midi=RecordingMidiTransport()),
            auth_token=VALID_TOKEN,
        )

        await server.start()
        reader, writer = await asyncio.open_connection("127.0.0.1", server.bound_port)
        writer.write(b"{")
        await writer.drain()
        await asyncio.sleep(0.01)

        await asyncio.wait_for(server.close(), timeout=1)
        assert await asyncio.wait_for(reader.readline(), timeout=1) == b""
        writer.close()
        await writer.wait_closed()

    asyncio.run(scenario())
