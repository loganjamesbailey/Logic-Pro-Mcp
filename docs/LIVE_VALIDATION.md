# Live Validation and Recovery

Live validation is opt-in. It can change an open Logic project and therefore runs
only after all offline checks pass, the project is explicitly disposable, and
the operator has confirmed the exact target at action time.

Read [Setup](SETUP.md), [Security](SECURITY.md),
[Architecture](ARCHITECTURE.md), and [Scripter](SCRIPTER.md) first.

## Evidence rules

Record what was proved, not what was intended:

| Last lifecycle state | What may be claimed | Retry rule |
| --- | --- | --- |
| `requested` | The request reached validation; no transport dispatch was proved | Fix the stated pre-dispatch cause, then re-evaluate |
| `dispatched` | The transport accepted the operation | Do not assume Logic applied it |
| `observed` | One attributable UI or operator signal occurred | Do not promote to complete state verification |
| `verified` | A named observer proved only its named scope | Do not generalize beyond the scope |
| `unknown` | The resulting Logic state could not be proved | Never blindly retry a non-idempotent action |

MIDI and Scripter are output-only and normally end `unknown`. A `.cst` operation
can be scoped-verified only by a separate bounded inspection of the same target
that completely matches the configured visible plug-in signature. The scope is
`mixer_plugin_signature`, not full preset or project equivalence.

## Preflight gates

Do not begin a live run unless all of the following are true:

- the complete offline test, lint, type, script, schema, and distribution gates pass;
- `doctor` reports no hard blocker;
- the configured IAC output exists and has no echo loop;
- Controller Assignments were manually checked;
- Accessibility is granted to the launching application;
- exactly one standalone Logic Mixer is visible;
- the project is newly disposable or safely backed up and will not be saved;
- the allowlisted target resolves read-only by both index and exact name;
- the preset file passes root, ownership, mode, size, and SHA-256 checks;
- no previous timeout or ambiguous attempt remains unresolved.

Record the Logic and macOS versions before mutation. UI validation is valid only
for the recorded structural contract and versions.

## Opt-in live-test entry points

All three live files exist and are skipped by default. The IAC gate first proves
the bus is quiet, then sends one operator-attested unassigned sentinel and
requires exactly one delivery with no traffic-induced echo:

```sh
LOGIC_BRIDGE_RUN_LIVE_IAC=1 \
LOGIC_BRIDGE_IAC_SENTINEL_ATTESTATION='send one unassigned channel 16 cc 119 sentinel' \
uv run pytest -q tests/live/test_iac_live.py
```

The Scripter gate sends exactly one allowlisted parameter value. It requires the
machine-local fixed profile to be enabled and attested plus these exact
action-time values:

```sh
export LOGIC_BRIDGE_RUN_LIVE_SCRIPTER=1
export LOGIC_BRIDGE_DISPOSABLE_PROJECT=1
export LOGIC_BRIDGE_LIVE_SCRIPTER_PARAMETER_ID='scripter.retro-synth.filter-cutoff'
export LOGIC_BRIDGE_LIVE_SCRIPTER_VALUE='0.5'
export LOGIC_BRIDGE_LIVE_SCRIPTER_ATTESTATION='protocol v1 installed before Retro Synth on a disposable software-instrument track; both targets learned; channel 16 CC102/CC103 isolated'
export LOGIC_BRIDGE_LIVE_SCRIPTER_DISPATCH="send ${LOGIC_BRIDGE_LIVE_SCRIPTER_PARAMETER_ID} value ${LOGIC_BRIDGE_LIVE_SCRIPTER_VALUE} once to disposable Scripter track"
uv run pytest -q tests/live/test_scripter_live.py
```

The `.cst` gate loads only IDs already present in the ignored
`config/bridge.toml`. It performs a complete read-only exact-target inspection
before one mutation, invokes the command service once, runs the packaged
post-load inspection, and contains no project-save action:

```sh
export LOGIC_BRIDGE_RUN_LIVE_CST=1
export LOGIC_BRIDGE_DISPOSABLE_PROJECT=1
export LOGIC_BRIDGE_LIVE_PRESET_ID='preset.jimmy-vocal-chain'
export LOGIC_BRIDGE_LIVE_TARGET_ID='track.audio-2'
export LOGIC_BRIDGE_LIVE_CST_CONFIRMATION="load ${LOGIC_BRIDGE_LIVE_PRESET_ID} into ${LOGIC_BRIDGE_LIVE_TARGET_ID} in disposable project"
uv run pytest -q tests/live/test_logic_cst_live.py
```

Use the actual opaque IDs from the local policy; the examples above are not path
inputs. The tests always load the repository's own ignored `config/bridge.toml`
and never accept an environment-supplied config or preset path. Unset the live
variables after each run. Environment gates do not make a project disposable or
prove the visible Logic state; the operator must verify both immediately before
execution.

## Ordered validation sequence

### 1. Prove the offline distribution

Run the default test suite, compile the packaged JXA resources, run the Scripter
harness, build the wheel, and verify resources from outside the checkout. Stop at
the first failure. Offline support tooling is not live Logic evidence.

### 2. Run diagnostics and discovery

Run `doctor` and capabilities using the secure machine-local configuration and
strong token. Confirm the exact IAC output, feature availability, and opaque IDs.
Do not include tokens or absolute paths in the validation record.

### 3. Resolve the Mixer read-only

Open one standalone Mixer and perform the fixed inspection for the allowlisted
target. For the release proof, the intended visible target is **Audio 2**. Both
the configured finite position/index and exact direct name evidence must agree.
Capture the bounded pre-load normalized plug-in list and completeness flag.

If read-only resolution is missing, slow, ambiguous, stale, or incomplete, stop.
Tighten the fixed structural selector and rerun read-only inspection. Never use a
coordinate click, broad window scan, longer timeout as a substitute for identity,
or blind mutation to discover the target.

### 4. Validate IAC actions

With a harmless selected track and known Controller Assignments, dispatch the
configured selected-track volume and mute actions once. Delivery can prove only
`requested`, `dispatched`, `unknown` unless a separate approved observation is
recorded. Do not infer global routing from a successful MIDI send.

### 5. Validate Scripter separately

On the disposable Retro Synth software-instrument track described in
[Scripter](SCRIPTER.md), run the one-shot gate for one fixed target. If both
targets are being validated, run a separate explicitly confirmed gate for each;
never sweep values. Do not use Audio 2 for this proof and do not report Scripter
as audio-strip control.

### 6. Present destructive `.cst` confirmation

Immediately before the load, display:

- the configured preset ID for **Jimmy Vocal Chain.cst**;
- the configured target ID for **Audio 2**;
- that the action replaces or changes the target channel-strip state;
- that the currently open project is disposable and will not be saved.

Collect exactly:

```text
load <preset_id> into <target_id> in disposable project
```

Substitute the same displayed opaque IDs. Cancellation, whitespace or text that
does not exactly satisfy the server contract, an ID mismatch, or target drift
must leave the lifecycle at `requested` and perform zero AXPress calls.

### 7. Mutate at most once and inspect independently

Invoke the allowlisted preset once. Do not automatically retry. After the
attributable UI transition, run the separate read-only inspector against the
same target and capture its bounded post-load plug-in snapshot.

If the configured complete signature matches under its declared match mode, the
record may contain `verified` with scope `mixer_plugin_signature`. Otherwise the
final state is `unknown`, with the safe reason preserved. Do not save the project.

## Recovery states

### Pre-dispatch rejection

Examples include a wrong phrase, missing token, hash mismatch, unavailable preset,
stale target, or missing Accessibility trust. No mutation should have occurred.
Correct only the reported prerequisite, rerun read-only checks, and obtain a new
action-time confirmation.

### Popup open but no item selected

The packaged cleanup may cancel only an attributable popup after re-resolving the
exact target and proving popup ownership. Cleanup is best effort and reports when
it skipped. Never send a global Escape keystroke or click an arbitrary menu.

### Timeout, cancellation, or disconnect after launch

Treat the state as `unknown`. The JXA process must be terminated and reaped, but
process termination does not prove Logic made no change. Inspect the target
read-only and visually assess the disposable project. Do not retry a toggle,
SysEx trigger, Scripter call, or `.cst` load merely because no response arrived.

### Selector contract failure

Stop mutation. Preserve only sanitized timing/error evidence, inspect the current
Logic hierarchy within the existing bounds, update fixtures and the reviewed
structural contract, and rerun all offline and read-only gates. Do not add screen
coordinates, global selectors, or a broad recursive fallback.

### Unexpected project or save prompt

Stop immediately, decline any save that would overwrite user work, and close or
revert only through normal Logic UI under operator control. The bridge does not
authorize project saving.

## Validation record template

Store only sanitized data:

```text
macOS version:
Logic Pro version:
bridge commit/package version:
disposable project asserted: yes/no
configured IAC endpoint present: yes/no
controller assignments attested: yes/no/unverifiable
preset opaque ID:
target opaque ID:
read-only target identity result:
pre-load normalized plug-in signature and completeness:
confirmation accepted or canceled:
ordered lifecycle events:
post-load normalized plug-in signature and completeness:
verification scope, if any:
final state and safe reason:
project saved: no
```

Do not record the token, absolute preset path, raw Accessibility tree, unrelated
track names, private musical content, or unbounded `osascript` output.
