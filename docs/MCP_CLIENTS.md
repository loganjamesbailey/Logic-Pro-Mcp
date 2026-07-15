# MCP Client Integration

The bridge's Model Context Protocol surface lets local coding agents discover and
invoke the same allowlisted operations as JSON-RPC. MCP is a stdio adapter to the
authenticated daemon, not another Logic service.

See [Architecture](ARCHITECTURE.md), [Setup](SETUP.md),
[Security](SECURITY.md), and [Live validation](LIVE_VALIDATION.md).

## Process model

The operator starts the loopback daemon first. An MCP-capable client then launches
one local stdio adapter process with the daemon host, port, and token in its
private environment.

```text
agent client <-> MCP stdio adapter <-> authenticated loopback daemon <-> Logic transports
```

The adapter does not auto-start the daemon, does not use HTTP/WebSocket/SSE, and
does not open MIDI or Accessibility resources. Each tool call creates one fresh
daemon connection, writes one request, accepts only the response with the same
ID, and closes. A timeout or cancellation cannot leave a stale response for a
later tool call.

Before the request is written, the adapter and daemon complete the versioned
nonce/HMAC prelude. The adapter verifies the daemon's proof first, so a rogue
process occupying the loopback port receives no token, method, parameters, or
reusable request authenticator. The subsequent request proof is unique to that
connection and canonical request.

## Trust boundary

Stdio trusts the local process that launches the adapter. There is no separate
MCP OAuth layer. Give `LOGIC_BRIDGE_TOKEN` only to a client and agent allowed to
invoke the listed tools. The daemon still enforces token strength, allowlists,
configuration integrity, and destructive confirmation.

Standard output is protocol-only. Diagnostics belong on standard error and must
not contain the token, absolute preset paths, raw Accessibility data, or private
project content.

## Fixed resources

| URI | Meaning | Mutation |
| --- | --- | --- |
| `logic-bridge://health` | Safe daemon health/readiness proxy | None |
| `logic-bridge://capabilities` | Safe public method, schema, availability, and opaque-resource metadata | None |

Reading a resource may fail safely when the daemon is unavailable. It must not
start Logic or dispatch a mutation.

## Fixed tools

| MCP tool | Daemon operation | Important semantic |
| --- | --- | --- |
| `set_volume` | `logic.set_volume` | Idempotent intent; dispatch has no native readback |
| `toggle_mute` | `logic.toggle_mute` | Non-idempotent; never retry blindly |
| `invoke_action` | `logic.invoke_action` | Opaque configured action only; no raw MIDI |
| `set_scripter_parameter` | `logic.set_scripter_parameter` | Fixed software-instrument route; output-only |
| `inspect_mixer_target` | `logic.inspect_mixer_target` | Read-only bounded evidence for one opaque target |
| `load_cst_preset` | `logic.load_cst_preset` | Destructive, non-idempotent, exact target-bound phrase required |

This is the settled parity contract. A tool is operational only after the daemon
method, schema, capability metadata, and stdio parity tests all ship together.
The presence of a wrapper alone is not proof that its daemon counterpart is ready.

## Client configuration

Codex, Claude, Cursor, Grok, and other clients may support local MCP stdio
servers, but their configuration file names and UI fields are version-specific.
The stable integration fields are:

- transport: stdio;
- command: `logic-bridge --config <absolute-config-path> mcp`, or the equivalent
  `uv` checkout invocation shown below;
- environment: the same `LOGIC_BRIDGE_TOKEN` used by the running daemon;
- working directory: not relied on by the packaged adapter.

The CLI exposes the MCP entry point now:

```sh
uv run logic-bridge --config config/bridge.toml mcp
```

For an agent client that should not depend on its working directory, use absolute
checkout and policy paths:

```json
{
  "transport": "stdio",
  "command": "uv",
  "args": [
    "--directory",
    "/absolute/path/to/Logic-Pro-Mcp",
    "run",
    "logic-bridge",
    "--config",
    "/absolute/path/to/Logic-Pro-Mcp/config/bridge.toml",
    "mcp"
  ],
  "env": {
    "LOGIC_BRIDGE_TOKEN": "provide-through-the-client-secret-environment"
  }
}
```

Replace both absolute paths with the local checkout path and inject the token
through the client's private secret mechanism. Never commit the substituted
token. A wheel installation may instead set `command` to the absolute installed
`logic-bridge` executable and use
`["--config", "/absolute/path/to/config/bridge.toml", "mcp"]` as `args`.

## Connection checklist

1. Start the authenticated daemon with the secure local policy.
2. Read daemon capabilities and confirm the intended tools are available.
3. Configure one MCP stdio server in the authorized client.
4. Initialize the MCP session.
5. List resources and read health/capabilities before calling a mutation.
6. List tools and compare the intended set with the daemon capability map.
7. Start with the read-only Mixer inspection or a harmless configured volume
   value; do not start with a `.cst` mutation.
8. For a destructive load, present both opaque IDs, the consequence, and the
   disposable-project assertion, then collect the exact phrase.

If tool or resource parity fails, mark MCP `blocked`, stop, and repair the adapter
or daemon contract. Do not dynamically reflect extra daemon methods to make the
lists match.

## Lifecycle handling for agents

Agents must interpret structured lifecycle events directly:

- `requested` alone means no transport dispatch was proved.
- `dispatched` means delivery to a transport, not Logic success.
- `observed` means one attributable signal, not complete state.
- `verified` is valid only for its named scope and proof source.
- `unknown` means inspect independently before deciding what to do next.

Retry guidance:

| Operation | Automatic retry allowed after timeout/cancel? |
| --- | --- |
| Read-only health/capabilities | Yes, on a fresh connection |
| Read-only Mixer inspection | Yes, if no mutation is coupled to it |
| Set volume | Do not auto-retry when dispatch timing is ambiguous; inspect or ask the operator |
| Toggle mute | No |
| Trigger/note/SysEx action | No |
| Scripter parameter | No blind retry; no acknowledgement exists |
| `.cst` load | No; inspect the exact target and obtain a new confirmation only after recovery |

The adapter's transport error cannot prove whether the daemon dispatched before
the connection failed. Sanitized errors preserve the daemon bridge code when
available without exposing credentials.

## Unsupported MCP surfaces

This project does not expose remote HTTP MCP, WebSocket, SSE, experimental tasks,
arbitrary resources, filesystem access, shell execution, dynamic JXA/Scripter
source, raw MIDI, or a tool that starts another daemon. Adding any such surface
requires a separate architectural and security review.
