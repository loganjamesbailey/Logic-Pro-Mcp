# Security Model

The bridge authorizes local Logic mutations. Treat its token and machine-local
configuration with the same care as any credential that can change an open
creative project.

This document complements [Architecture](ARCHITECTURE.md),
[Setup](SETUP.md), [MCP clients](MCP_CLIENTS.md), and
[Live validation](LIVE_VALIDATION.md).

## Protected assets

The design protects:

- open Logic projects, channel strips, routing, and plug-in state;
- user presets and `.cst` files;
- the daemon authentication secret;
- private host paths and Accessibility hierarchy content;
- the integrity of the local allowlist policy;
- the user's macOS consent boundary.

The principal threats are an unauthorized local process, a compromised or
over-permissioned coding agent, malformed RPC input, policy-file replacement,
unsafe MIDI routing, and UI ambiguity after a partial action.

## Sole command authority

All mutations pass through one authenticated daemon bound to a literal loopback
IP. MCP is a client adapter and never a second Logic controller. Loopback reduces
network exposure but does not prevent another local process from connecting.
Every connection therefore requires mutual secret-possession proofs, and every
request passes the same strict method and parameter contract.

Authentication cannot be disabled. The shared secret is never sent over TCP.
Authentication failures occur before MIDI, JXA, Scripter, or Logic dispatch.

Each fresh connection uses the versioned `logic-bridge-hmac-sha256-v1` prelude:

1. the client sends a fresh 32-byte random nonce and no request material;
2. the daemon returns its own fresh nonce plus a domain-separated HMAC-SHA256
   server proof bound to both nonces;
3. only after constant-time verification does the client send one JSON-RPC
   envelope with a request proof bound to both nonces and the canonical request;
4. the daemon verifies the echoed nonces and proof in constant time, processes at
   most one request, and closes the connection.

Canonical request bytes are UTF-8 JSON with recursively sorted object keys,
compact separators, and no NaN or Infinity. Duplicate keys, invalid UTF-8,
excessive depth, unknown handshake fields, malformed nonces, and malformed proofs
fail closed. Domain separation prevents a captured server proof from being used
as a client proof, and the fresh daemon nonce makes a captured request proof
invalid on the next connection. A listener that merely steals the configured
port sees only a random client nonce and receives neither the secret nor an
authenticated Logic request.

## Credential requirements

`LOGIC_BRIDGE_TOKEN` must contain at least 32 bytes after UTF-8 encoding and
should come from a cryptographically secure random generator. A suitable
generation command is documented in
[Setup](SETUP.md#3-generate-the-authentication-secret).

The token must never appear in:

- repository files or example configuration;
- logs, error details, health data, capability data, or MCP resources;
- subprocess arguments or JXA environments;
- screenshots, validation records, or bug reports;
- shell history as a literal command-line value;
- loopback handshake or JSON-RPC request bytes.

Rotate the token by generating a new value, updating only the authorized daemon
and MCP launcher environments, and restarting both. Rotate immediately if it is
printed, committed, pasted into a report, or exposed to an untrusted process.

## Configuration is authorization policy

`config/bridge.toml` chooses the network bind, MIDI endpoint, actions, UI feature
state, preset allowlist, and target allowlist. It is not ordinary preference data.
The daemon opens it fail-closed and requires:

- an immediate parent that is a real directory owned by the current user;
- a real, regular file owned by the current user;
- no symlink for the parent or file;
- no group- or world-write bit on either object;
- parsing from the same file descriptor whose metadata was validated.

The file is ignored by Git. Keep it out of packages, archives, and support
bundles. A policy failure is `blocked`; repair ownership or mode rather than
relaxing the check.

## Input and capability boundary

The public boundary is an allowlist of typed domain methods. Unknown request
fields, methods, batches, notifications, and malformed JSON-RPC envelopes fail
before dispatch. Callers cannot submit arbitrary:

- executable source, shell commands, or subprocess values;
- paths, filenames, URLs, or filesystem traversal;
- Accessibility selectors, coordinates, menu names, or timeouts;
- MIDI channels, controls, note numbers, or SysEx bytes;
- Scripter target names or JavaScript.

Generic MIDI calls accept only an opaque configured action ID and the value mode
declared by local policy. Capability data may expose the ID, kind, mode, and safe
prerequisites; fixed routes and SysEx bytes remain private.

## MIDI and IAC considerations

The IAC bus is unauthenticated MIDI routing. Any local application with access to
the bus may transmit to it. Keep the daemon token as the authorization boundary,
minimize other senders, and avoid using the IAC port as evidence that a request
was authorized.

Use a one-way route and check for loops before live testing. A configured action
emits at most two bounded messages. Protocol v1 reserves human MIDI channel 16
(Mido channel 15), CC102 and CC103 for Scripter; declarative Controller
Assignments must not collide with that namespace.

## Accessibility and `.cst` controls

The bridge does not edit `.cst` bytes. Before invoking Logic, it revalidates that
the configured preset is confined beneath the approved root, is a regular
non-symlink file owned by the current user, is not group/world writable, is
within the size limit, and matches the configured SHA-256 digest. Validation uses
no-follow root and file descriptors and records stable file and root identity,
mode, owner, size, mtime, ctime, and digest. The same identity is required again
after an observed dispatch, which detects replacement and same-digest ABA
mutation rather than accepting restored bytes as unchanged.

Only reviewed, packaged JXA resources may run. RPC and MCP callers cannot supply
scripts or selectors. Each resource is opened with no-follow semantics, checked
for stable descriptor identity, safe ownership/mode, bounded size and strict
UTF-8, verified against its own pinned SHA-256, cached, and executed from those
bytes through `osascript` standard input. No validated script path is reopened.
The resolver uses bounded roles and structure, checks the configured index and
exact name, rechecks identity before action, presses once, and fails closed on
ambiguity. It has a hard process deadline and sanitized subprocess handling. Raw
`osascript` diagnostics and Accessibility trees are not returned to clients.

Logic's channel-strip menu API accepts a named on-disk preset; it cannot consume
the bridge's already-open preset descriptor. Consequently, a process running as
the same user can still race the on-disk preset during Logic's menu operation.
The post-dispatch digest, ctime, file identity, and root identity checks detect
such mutation where macOS metadata exposes it, but they do not cryptographically
prevent Logic from briefly consuming raced bytes. A detected change is reported
as ambiguous post-dispatch and is never retried automatically.

The exact destructive phrase binds authorization to both opaque IDs and a
disposable project:

```text
load <preset_id> into <target_id> in disposable project
```

A boolean, partial phrase, substituted ID, extra field, cancellation, or stale
target dispatches nothing. After an ambiguous post-dispatch failure, the bridge
does not automatically retry.

## macOS and Logic integrity

The project never:

- patches, injects into, re-signs, or modifies the Logic Pro application;
- edits Logic's core binary, undocumented project bundles, or private plists;
- disables System Integrity Protection or Gatekeeper;
- edits or bypasses the TCC database;
- simulates consent or silently grants Accessibility/Automation access;
- depends on Keyboard Maestro, BetterTouchTool, Hammerspoon, SoundFlow, or
  another third-party GUI automation application.

The operator grants native macOS permissions explicitly to the process that
launches the bridge. Missing consent is a safe failure, not an invitation to
broaden privileges.

## Scripter boundary

The reviewed Scripter source is fixed and manually installed by the operator on
a disposable software-instrument strip. It performs no network, filesystem,
RPC, dynamic code, or subprocess work. The bridge cannot inject or replace
source at runtime. Scripter is not authentication and provides no acknowledgement
that a downstream parameter changed.

## MCP stdio boundary

The process that launches the MCP server is the stdio trust boundary. There is
no additional MCP-layer OAuth for this local transport; mutation authorization
still comes from the daemon token and allowlists. Give the token only to an MCP
client and agent you trust to invoke the exposed tools.

The adapter:

- reserves stdout for MCP protocol frames and sends sanitized diagnostics to stderr;
- exposes a fixed tool and resource set rather than dynamic daemon reflection;
- opens one fresh daemon connection per call;
- closes that connection on success, timeout, error, or cancellation;
- never starts the daemon or directly opens Logic transports.

A cancellation or lost response after dispatch remains ambiguous. The adapter
must not label non-idempotent work retryable merely because the client did not
receive the response.

## Evidence and privacy

Health and capability responses may expose opaque IDs, safe endpoint names, and
readiness metadata. They must not expose the token, absolute preset paths, raw
Accessibility dumps, unrelated track names, private musical content, or fixed
SysEx payloads.

`verified` always names its narrow proof source and scope. It must never be used
as shorthand for a complete Logic project assertion.

## Incident response

If authorization material or behavior is suspect:

1. Stop the MCP adapter and daemon.
2. Do not save the open Logic project.
3. Rotate `LOGIC_BRIDGE_TOKEN`.
4. inspect ownership, mode, and contents of the local policy;
5. inspect IAC routing for unknown senders or feedback;
6. review the last lifecycle record without publishing private paths;
7. rerun offline tests and read-only diagnostics before another mutation.
