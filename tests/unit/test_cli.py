from __future__ import annotations

import asyncio
import json
import signal
from typing import Any

import daemon.__main__ as cli
from daemon.config import BridgeConfig
from tests.fakes import valid_mapping


def test_mcp_subcommand_runs_stdio_adapter_without_constructing_logic_service(
    monkeypatch: Any,
) -> None:
    config = BridgeConfig.from_mapping(valid_mapping())
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(cli, "load_config", lambda _path: config)
    monkeypatch.setenv("LOGIC_BRIDGE_TOKEN", "m" * 32)
    monkeypatch.setattr(
        cli,
        "_service",
        lambda _config: (_ for _ in ()).throw(
            AssertionError("MCP must not construct Logic transports")
        ),
    )
    monkeypatch.setattr(
        cli,
        "run_stdio",
        lambda **kwargs: calls.append(kwargs),
        raising=False,
    )

    assert cli.main(["mcp"]) == 0
    assert calls == [
        {
            "host": "127.0.0.1",
            "port": 8765,
            "token": "m" * 32,
            "timeout_seconds": 75.0,
        }
    ]


def test_mcp_subcommand_with_an_unconnectable_configured_port_exits_cleanly(
    monkeypatch: Any,
    capsys: Any,
) -> None:
    raw = valid_mapping()
    raw["server"] = {**raw["server"], "port": 0}  # type: ignore[arg-type]
    config = BridgeConfig.from_mapping(raw)

    monkeypatch.setattr(cli, "load_config", lambda _path: config)
    monkeypatch.setenv("LOGIC_BRIDGE_TOKEN", "m" * 32)
    monkeypatch.setattr(
        cli,
        "_service",
        lambda _config: (_ for _ in ()).throw(
            AssertionError("MCP must not construct Logic transports")
        ),
    )

    exit_code = cli.main(["mcp"])

    assert exit_code == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["bridge_code"] == "CONFIGURATION_ERROR"


def test_capabilities_subcommand_does_not_require_a_valid_token(
    monkeypatch: Any,
) -> None:
    config = BridgeConfig.from_mapping(valid_mapping())
    printed: list[object] = []

    monkeypatch.setattr(cli, "load_config", lambda _path: config)
    monkeypatch.delenv("LOGIC_BRIDGE_TOKEN", raising=False)
    monkeypatch.setattr(
        cli, "_print_json", lambda value, **_kwargs: printed.append(value)
    )

    exit_code = cli.main(["capabilities"])

    assert exit_code == 0
    assert len(printed) == 1
    assert "contract" in printed[0]


def test_doctor_reports_missing_token_instead_of_aborting_early(
    monkeypatch: Any,
) -> None:
    config = BridgeConfig.from_mapping(valid_mapping())
    observed: list[BridgeConfig] = []

    monkeypatch.setattr(cli, "load_config", lambda _path: config)
    monkeypatch.delenv("LOGIC_BRIDGE_TOKEN", raising=False)
    monkeypatch.setattr(
        cli,
        "_doctor",
        lambda received: observed.append(received) or 1,
    )

    assert cli.main(["doctor"]) == 1
    assert observed == [config]


def test_serve_wraps_a_bind_failure_as_a_structured_bridge_error(
    monkeypatch: Any,
) -> None:
    config = BridgeConfig.from_mapping(valid_mapping())

    class UnbindableServer:
        closed = False

        def __init__(self, **_kwargs: object) -> None:
            return None

        async def start(self) -> None:
            raise OSError("[Errno 48] Address already in use")

        async def close(self) -> None:
            self.closed = True

    server = UnbindableServer()
    monkeypatch.setattr(cli, "_service", lambda _config: object())
    monkeypatch.setattr(cli, "JsonRpcServer", lambda **_kwargs: server)

    try:
        asyncio.run(cli._serve(config, auth_token="s" * 32))
    except cli.ServerBindError as exc:
        assert exc.code == "SERVER_BIND_FAILED"
        assert exc.retryable is True
    else:
        raise AssertionError("expected ServerBindError")
    assert server.closed is True


def test_serve_sigterm_path_closes_server_before_return(
    monkeypatch: Any,
) -> None:
    config = BridgeConfig.from_mapping(valid_mapping())

    class FakeServer:
        bound_port = 8765
        closed = False

        def __init__(self, **_kwargs: object) -> None:
            return None

        async def start(self) -> None:
            return None

        async def serve_forever(self) -> None:
            await asyncio.Event().wait()

        async def close(self) -> None:
            self.closed = True

    server = FakeServer()
    monkeypatch.setattr(cli, "_service", lambda _config: object())
    monkeypatch.setattr(cli, "JsonRpcServer", lambda **_kwargs: server)

    def install(callback: Any) -> Any:
        asyncio.get_running_loop().call_soon(callback, signal.SIGTERM)
        return lambda: None

    monkeypatch.setattr(cli, "_install_signal_handlers", install)

    assert asyncio.run(cli._serve(config, auth_token="s" * 32)) == 143
    assert server.closed is True
