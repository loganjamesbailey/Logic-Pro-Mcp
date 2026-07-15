from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from daemon.accessibility import AccessibilityCstPresetLoader
from daemon.commands import LogicCommandService
from daemon.config import BridgeConfig, load_config, validate_authentication_token
from daemon.diagnostics import run_diagnostics
from daemon.errors import BridgeError, ConfigurationError, ServerBindError
from daemon.mcp_server import run_stdio
from daemon.midi_transport import MidiTransport
from daemon.server import JsonRpcServer

DEFAULT_CONFIG = Path("config/bridge.toml")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == "doctor":
            return _doctor(config)
        if args.command == "capabilities":
            # Purely local capability introspection: like doctor, this must
            # not require a valid auth token before an operator can even see
            # what the bridge offers.
            service = _service(config)
            try:
                _print_json(service.capabilities())
            finally:
                service.close()
            return 0
        token = validate_authentication_token(
            os.environ.get(config.server.token_env),
            token_env=config.server.token_env,
        )
        if args.command == "serve":
            try:
                return asyncio.run(_serve(config, auth_token=token))
            except KeyboardInterrupt:
                return 130
        if args.command == "mcp":
            try:
                run_stdio(
                    host=config.server.host,
                    port=config.server.port,
                    token=token,
                    timeout_seconds=config.server.mcp_timeout_seconds,
                )
            except ValueError as exc:
                raise ConfigurationError(str(exc)) from exc
            return 0
    except BridgeError as exc:
        _print_json(
            {
                "error": exc.message,
                **exc.to_rpc_data(),
            },
            stream=sys.stderr,
        )
        return 2
    parser.error("unknown command")
    return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="logic-bridge",
        description="Local, allowlisted Logic Pro control bridge",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(os.environ.get("LOGIC_BRIDGE_CONFIG", DEFAULT_CONFIG)),
        help="TOML configuration path (default: config/bridge.toml)",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("doctor", help="Check token, MIDI port, and permissions")
    subcommands.add_parser(
        "capabilities", help="Print the agent-readable capability map"
    )
    subcommands.add_parser("serve", help="Run the loopback JSON-RPC daemon")
    subcommands.add_parser("mcp", help="Run the authenticated MCP stdio adapter")
    return parser


def _service(config: BridgeConfig) -> LogicCommandService:
    return LogicCommandService(
        config=config,
        midi=MidiTransport(output_name=config.midi.output_name),
        cst_loader=AccessibilityCstPresetLoader(config=config.accessibility),
    )


def _doctor(config: BridgeConfig) -> int:
    service = _service(config)
    try:
        result = run_diagnostics(
            config=config,
            midi=service.midi,
            cst_loader=service.cst_loader,
        )
    finally:
        service.close()
    _print_json(result)
    return 1 if result["status"] == "not_ready" else 0


async def _serve(config: BridgeConfig, *, auth_token: str) -> int:
    service = _service(config)
    server = JsonRpcServer(
        config=config.server,
        service=service,
        auth_token=auth_token,
    )
    try:
        await server.start()
    except OSError as exc:
        await server.close()
        raise ServerBindError(
            f"could not bind {config.server.host}:{config.server.port}",
            details={"host": config.server.host, "port": config.server.port},
        ) from exc
    except BaseException:
        await server.close()
        raise
    _print_json(
        {
            "event": "listening",
            "host": config.server.host,
            "port": server.bound_port,
            "transport": "newline-delimited-json-rpc-2.0",
        }
    )
    stop_event = asyncio.Event()
    received_signal: signal.Signals | None = None

    def request_stop(received: signal.Signals) -> None:
        nonlocal received_signal
        if received_signal is None:
            received_signal = received
            stop_event.set()

    remove_signal_handlers = _install_signal_handlers(request_stop)
    serve_task = asyncio.create_task(server.serve_forever())
    stop_task = asyncio.create_task(stop_event.wait())
    try:
        done, _pending = await asyncio.wait(
            {serve_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if serve_task in done:
            await serve_task
            return 0
        serve_task.cancel()
        await asyncio.gather(serve_task, return_exceptions=True)
        assert received_signal is not None
        return 128 + int(received_signal)
    finally:
        stop_task.cancel()
        await asyncio.gather(stop_task, return_exceptions=True)
        remove_signal_handlers()
        await server.close()


def _install_signal_handlers(
    callback: Callable[[signal.Signals], None],
) -> Callable[[], None]:
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    for received in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(received, callback, received)
        except (NotImplementedError, RuntimeError):
            continue
        installed.append(received)

    def remove() -> None:
        for received in installed:
            loop.remove_signal_handler(received)

    return remove


def _print_json(value: Any, *, stream: Any = sys.stdout) -> None:
    print(json.dumps(value, indent=2, sort_keys=True), file=stream, flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
