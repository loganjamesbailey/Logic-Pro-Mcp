# AI-to-Logic Pro IPC Bridge

## 1. Project Objective

The AI-to-Logic Pro IPC Bridge gives local coding agents a typed, authenticated
control plane for Apple Logic Pro. Logic does not expose a complete public
automation API, so the bridge translates allowlisted developer operations into
the native control vectors macOS and Logic make available:

- MIDI CC, note, and bounded SysEx messages over the macOS IAC bus for Logic
  Controller Assignments;
- a fixed Logic Scripter MIDI-FX protocol for two learned Retro Synth targets on
  a software-instrument strip; and
- fixed, bounded JXA/Accessibility workflows for read-only Mixer inspection and
  allowlisted `.cst` channel-strip imports.

"Full Logic control" is the architectural direction, not a claim of complete
project-state access or universal readback. Every shipped command reports what
the bridge actually knows: `requested`, `dispatched`, `observed`, `verified`, or
`unknown`.

## 2. Core Architecture & Communication Vectors

```text
Codex / Claude / Cursor / Grok
        |
        +-- MCP stdio adapter ---------+
        |                              |
        +-- NDJSON JSON-RPC client ----+--> authenticated loopback daemon
                                                |
             +----------------------------------+-------------------------+
             |                                  |                         |
             v                                  v                         v
      IAC MIDI transport             fixed Scripter protocol       fixed JXA scripts
      CC / note / SysEx               ch. 16, CC102/103          inspect + load `.cst`
             |                                  |                         |
             v                                  v                         v
     Controller Assignments          Logic MIDI FX targets       macOS Accessibility
```

The Python 3.11+ daemon is the sole command authority. It binds to a literal
loopback address, authenticates every request with a strong environment token,
validates the strict method schema, and dispatches only locally configured
resources. The MCP process is a stdio proxy to that daemon; it does not start a
second daemon or open MIDI and Accessibility transports itself. Remote HTTP,
WebSocket, SSE, and experimental MCP task transports are not implemented.

The environment token is a shared HMAC secret and is never sent over TCP. Each
fresh connection exchanges random client/server nonces, verifies the daemon
before releasing request data, and binds one request proof to both nonces and the
canonical JSON-RPC envelope.

The MCP surface contains exactly six tools:

| Tool | Purpose | Readback boundary |
| --- | --- | --- |
| `set_volume` | Set an allowlisted track volume | MIDI dispatch normally ends `unknown` |
| `toggle_mute` | Toggle an allowlisted mute target | Non-idempotent; never retry blindly |
| `invoke_action` | Invoke a configured CC, note, or SysEx action ID | Callers cannot provide raw MIDI |
| `set_scripter_parameter` | Set one fixed Retro Synth Scripter target | Output-only; no acknowledgement |
| `inspect_mixer_target` | Inspect one allowlisted Mixer strip | Read-only, bounded normalized evidence |
| `load_cst_preset` | Load one allowlisted setting into one allowlisted strip | Destructive; exact confirmation required |

Two read-only MCP resources, `logic-bridge://health` and
`logic-bridge://capabilities`, proxy safe daemon discovery data. JSON-RPC and MCP
share the same method contracts and command service; neither surface accepts
shell commands, source code, host paths, Accessibility selectors, or raw MIDI.

The MIDI transport sends only configured messages to the exact IAC output. Logic
Controller Assignments remain operator-owned and are not an authentication or
readback channel. Scripter is additive: the reviewed script consumes channel 16
CC102/103 and turns them into two learned TargetEvent writes below Scripter on a
software-instrument strip. It cannot run on the Audio 2 audio strip.

The Accessibility path resolves one standalone Mixer, one exact target, and one
direct Setting control within fixed traversal and time budgets. A `.cst` load is
followed by a separate bounded inspection. Only a complete configured plug-in
signature match may produce `verified` with scope `mixer_plugin_signature`;
otherwise the result remains `unknown`.

## 3. Strict Boundary Constraints

All work in this repository must preserve these boundaries:

1. **No third-party GUI automation applications.** UI control uses only native
   macOS Accessibility through the packaged `osascript`/JXA resources.
2. **Python 3.11+ is mandatory.** Node.js is used only for the offline Scripter
   harness; it is not another listener or command authority.
3. **Zero-touch Logic installation.** Never patch, inject into, re-sign, replace,
   or modify Logic Pro, its bundle, Apple plug-ins, TCC, SIP, or Gatekeeper.
4. **Loopback and authentication only.** The daemon accepts literal loopback
   addresses and requires `LOGIC_BRIDGE_TOKEN` to contain at least 32 UTF-8 bytes;
   only nonce-bound HMAC proofs, never the token, cross the socket.
5. **Machine-local policy is authorization.** `config/bridge.toml` must remain
   untracked, user-owned, non-symlinked, and non-group/world-writable, with a
   protected parent directory.
6. **Allowlisted domain commands only.** Agents cannot submit raw MIDI bytes,
   JavaScript/JXA, shell text, paths, selectors, URLs, or unbounded values.
7. **Opaque `.cst` assets.** The loader confines basenames to the configured root
   and rechecks ownership, mode, size, and SHA-256 on every invocation. It does
   not edit `.cst` bytes or Logic project bundles.
8. **Exact destructive confirmation.** A load is accepted only when the client
   sends `load <preset_id> into <target_id> in disposable project` using the same
   configured opaque IDs. An ambiguous outcome is never retried automatically.
9. **No save authority.** Live tests operate only in an explicitly disposable
   project and never save it.
10. **Truthful evidence.** Transport delivery is not Logic state. Output-only
    MIDI and Scripter calls must not be represented as verified.
11. **GPLv3 compatibility.** Repository code is GPLv3; contributions and
    dependencies must remain compatible with its obligations.

## 4. Target Directory Structure

```text
.
├── LICENSE
├── README.md
├── pyproject.toml
├── uv.lock
├── daemon/
│   ├── __main__.py             # doctor, capabilities, serve, and mcp CLI
│   ├── server.py               # authenticated NDJSON JSON-RPC daemon
│   ├── rpc_client.py           # one-request-per-connection adapter client
│   ├── mcp_server.py           # fixed six-tool MCP stdio surface
│   ├── rpc_contract.py         # shared method schemas and lifecycle contract
│   ├── commands.py             # sole allowlisted command service
│   ├── config.py               # fail-closed machine-local TOML policy
│   ├── midi_transport.py       # CC, note, and bounded SysEx transport
│   ├── scripter.py             # fixed Scripter parameter protocol
│   ├── accessibility.py        # fixed `.cst` loader and inspector runner
│   ├── ui_observation.py       # bounded normalized Mixer evidence
│   └── diagnostics.py          # readiness and next-action checks
├── bridge_scripts/
│   ├── inspect_mixer.js        # read-only exact-target inspection
│   ├── load_cst.js             # exact-target `.cst` mutation
│   └── cleanup_cst.js          # attributable popup cleanup only
├── scripter/
│   ├── LogicBridgeTargets.js   # reviewed protocol-v1 Logic script
│   └── README.md
├── config/
│   ├── bridge.example.toml     # versioned policy example
│   ├── bridge.toml             # ignored machine-local policy
│   └── command_schema.json     # generated public JSON-RPC schema
├── docs/
│   ├── ARCHITECTURE.md
│   ├── SECURITY.md
│   ├── SETUP.md
│   ├── SCRIPTER.md
│   ├── MCP_CLIENTS.md
│   └── LIVE_VALIDATION.md
└── tests/
    ├── unit/
    ├── integration/
    ├── js/
    └── live/                   # opt-in and skipped by default
```

## 5. Next Action Step

Set up the local policy and verify the offline bridge before touching Logic:

```sh
uv sync
cp config/bridge.example.toml config/bridge.toml
chmod 600 config/bridge.toml
export LOGIC_BRIDGE_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
uv run pytest -q
uv run logic-bridge --config config/bridge.toml doctor
uv run logic-bridge --config config/bridge.toml capabilities
```

Then follow [Setup](docs/SETUP.md) to configure the IAC Driver and Logic
Controller Assignments. Use **Option-Command-K** for Controller Assignments;
plain **Command-K** opens Musical Typing.

Start the authenticated daemon in one terminal:

```sh
uv run logic-bridge --config config/bridge.toml serve
```

An MCP client launches the separate stdio adapter with:

```sh
uv run logic-bridge --config config/bridge.toml mcp
```

Before any `.cst` mutation, enable only exact local preset and target entries,
open one standalone Logic Mixer, perform the read-only target inspection, and
follow the explicit opt-in gates in [Live Validation](docs/LIVE_VALIDATION.md).
The release proof is the allowlisted Jimmy Vocal Chain setting on Audio 2 in a
disposable, unsaved project; dispatch alone is not proof that Logic applied it.
