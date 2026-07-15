# Setup and Operator Readiness

This guide moves from a fresh checkout to a diagnosable local bridge. Complete
the steps in order. Do not attempt a live `.cst` load until the offline gates and
the read-only Mixer inspection pass.

Related documents: [Architecture](ARCHITECTURE.md), [Security](SECURITY.md),
[Scripter](SCRIPTER.md), [MCP clients](MCP_CLIENTS.md), and
[Live validation](LIVE_VALIDATION.md).

## Readiness vocabulary

Every prerequisite has one of three states:

- `ready` — the bridge proved the hard prerequisite.
- `blocked` — the operation cannot safely dispatch; perform the stated next action.
- `unverifiable` — Logic or macOS provides no approved non-mutating readback. The
  capability may remain dispatchable with an honest `unknown` result when all
  hard prerequisites are ready.

`logic-bridge doctor` exposes this model in `operator_readiness`. Every non-ready
item has one machine-readable `next_action`; legacy summary fields remain for
compatibility and must not be reinterpreted as proof of Logic state.

## 1. Install local prerequisites

Requirements:

- macOS with Logic Pro installed;
- Python 3.11 or newer;
- `uv` for the locked Python environment;
- Node.js only for the isolated Scripter behavior harness.

From the repository root:

```sh
uv sync
uv run pytest -q
uv run ruff check .
uv run mypy daemon
```

All default tests are offline and must not drive Logic, Accessibility, or a live
IAC endpoint.

## 2. Create and secure machine-local policy

Copy the example instead of modifying it:

```sh
cp config/bridge.example.toml config/bridge.toml
chmod go-w config
chmod 600 config/bridge.toml
```

`config/bridge.toml` is machine-local authorization policy and must remain
untracked. The daemon requires both the file and its immediate parent directory
to be owned by the current user, non-symlink objects of the expected type, and
not group- or world-writable. It opens and parses the same validated file
descriptor to prevent replacement between validation and use.

State: `blocked` until the policy file passes these checks. Next action: replace
any symlink with a user-owned regular file and remove group/world write access
from the file and parent directory.

## 3. Generate the authentication secret

Create a high-entropy value whose UTF-8 encoding is at least 32 bytes:

```sh
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Set the resulting value in the environment as `LOGIC_BRIDGE_TOKEN`. Avoid
committing it, putting it in command arguments, or copying it into client config
that other users can read. The daemon rejects missing, empty, or shorter tokens
before constructing transports or serving requests.

The value is used only as an HMAC-SHA256 key. The client first verifies a daemon
proof bound to fresh client/server nonces, then sends a request-specific proof;
the secret itself is never transmitted to the loopback listener.

To rotate the credential, generate a new value, update the daemon and MCP
launcher environment, then restart both processes. Existing daemon connections
do not survive the restart.

State: `blocked` until a token of at least 32 encoded bytes is present. Next
action: generate a fresh token and export it only to the daemon and authorized
local client launchers.

## 4. Enable the IAC bus

1. Open **Audio MIDI Setup**.
2. Choose **Window > Show MIDI Studio** if the MIDI devices are hidden.
3. Open **IAC Driver**.
4. Enable **Device is online**.
5. Add one port named exactly `AI_Logic_Bridge`.
6. Confirm the output appears to MIDI software as
   `IAC Driver AI_Logic_Bridge`.

The example configuration uses that full output name. If the port has a different
name, update the machine-local configuration to the exact enumerated name instead
of guessing.

Keep the route one-way. Do not echo the IAC input back to the same output, because
that can create a MIDI feedback loop.

State: `blocked` when the exact output is absent. Next action: bring the IAC
device online or correct the configured output name.

## 5. Configure Logic Controller Assignments

In Logic Pro, open **Logic Pro > Control Surfaces > Controller Assignments**.
The default shortcut is **Option-Command-K**. Plain **Command-K** opens Musical
Typing/on-screen keyboard and is not the Controller Assignments command.

Create or learn the mappings declared in the local configuration. The supplied
example reserves:

| Domain action | Incoming message | Intended Logic target |
| --- | --- | --- |
| `track.selected.volume` | Channel 1, CC7 | Selected-track volume |
| `track.selected.mute_toggle` | Channel 1, CC20 | Selected-track mute toggle |

Logic and Mido use different channel notation: the example's Mido channel `0`
is displayed to operators as MIDI channel 1. During learning, confirm the input
source/message reflects the IAC bridge. Exit Learn mode after each assignment to
avoid accidentally remapping later gestures.

Controller Assignments are Logic-owned state and the bridge does not inspect
them. State: `unverifiable` after the operator completes and tests the mappings.
Next action when nothing moves: reopen Controller Assignments, confirm the exact
input message and target, confirm Learn mode is off, and check for duplicate or
conflicting assignments.

## 6. Grant native macOS consent for UI operations

MIDI-only operations do not need Accessibility. The `.cst` loader does.

1. Open **System Settings > Privacy & Security > Accessibility**.
2. Enable the application that launches the bridge, such as Terminal or the
   authorized IDE/agent host.
3. On the first confirmed UI operation, respond to the macOS Automation prompt
   that allows the launcher to control System Events and Logic Pro.

Grant only the required launcher. Do not disable TCC, edit its database, or use
a permission-bypass utility. Automation consent may remain `unverifiable` until
macOS presents the first action-time prompt.

State: `blocked` for `.cst` tools when Accessibility trust is absent. Next
action: grant the launching application Accessibility access and restart that
application if macOS requires it.

## 7. Configure allowlisted `.cst` resources

Keep `[accessibility].enabled = false` until all values are known. In the
machine-local policy:

1. Set the preset root to the existing Logic Channel Strip Settings `Track`
   directory.
2. Add one preset with an opaque ID, basename only, and lowercase SHA-256 digest.
3. Add one target with an opaque ID, the zero-based visible Mixer strip index,
   and the exact Accessibility name.
4. Enable the Accessibility feature only after read-only inspection is ready.

The preset must be a user-owned, regular, non-symlink file under the configured
root, within the configured size bound, and unchanged from its configured hash.
The client never supplies its path.

State: `blocked` until file integrity, target identity, fixed packaged resources,
and Accessibility trust are ready. Next action: correct the single failing policy
item reported by diagnostics; never broaden the root or selector to hide an
identity failure.

## 8. Install and attest Scripter if needed

Scripter is optional and separate from the Audio 2 `.cst` workflow. Follow
[Scripter](SCRIPTER.md) on a disposable software-instrument strip. It requires
the fixed source, Retro Synth after Scripter, the two learned targets, channel 16
routing, and a collision check for CC102/CC103.

The versioned example contains the complete protocol-v1 policy. Copy it exactly
and change only `enabled` and `operator_attested` after the manual Logic setup is
true:

```toml
[scripter]
enabled = true
protocol_version = 1
channel = 15
source_sha256 = "1452add3dc76a19223fb6301e39ffd9c4c3c8c5915f072ae3ee029ffd1a563ab"
placement = "software_instrument_midi_fx"
operator_attested = true

[[scripter.learned_targets]]
parameter_id = "scripter.retro-synth.filter-cutoff"
control = 102
target_slot = 1
target_name = "Retro Synth Filter Cutoff"

[[scripter.learned_targets]]
parameter_id = "scripter.retro-synth.filter-resonance"
control = 103
target_slot = 2
target_name = "Filter Resonance"
```

The parser rejects any protocol, hash, placement, channel, control, target slot,
target name, or target set that differs from the reviewed profile. Attestation
permits output dispatch; it does not prove Logic installation or readback.

## 9. Run diagnostics and start the daemon

These commands exist in the current CLI:

```sh
uv run logic-bridge --config config/bridge.toml doctor
uv run logic-bridge --config config/bridge.toml capabilities
uv run logic-bridge --config config/bridge.toml serve
uv run logic-bridge --config config/bridge.toml mcp
```

Run them with `LOGIC_BRIDGE_TOKEN` already present in the environment. The server
must bind to a literal loopback address. Keep the daemon terminal private; do not
paste its environment or local policy into issue reports.

State: `blocked` until the authenticated daemon is reachable. Next action: fix
the first failing diagnostic and restart the daemon; do not start a second daemon
on another transport.

## 10. Connect an MCP client

The stdio adapter and client configuration are described in
[MCP clients](MCP_CLIENTS.md). Start `serve` first, then configure the agent client
to launch `logic-bridge --config <absolute-machine-local-config> mcp` as a stdio
process with the same `LOGIC_BRIDGE_TOKEN` in its private environment. The MCP
process never starts another daemon.

State: `blocked` until MCP initialization, both resource reads, the fixed six-tool
list, and an authenticated daemon proxy call pass. Next action: confirm the daemon
is running on the configured loopback host/port and that both processes received
the same token.

## Setup completion criteria

Setup is complete for a tool only when every hard prerequisite for that tool is
`ready`. An `unverifiable` Logic mapping may permit MIDI dispatch, but the result
must remain `unknown`. A live mutation is a separate validation step and must
follow [the opt-in live procedure](LIVE_VALIDATION.md).
