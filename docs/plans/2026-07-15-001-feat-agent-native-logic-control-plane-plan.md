---
title: Agent-Native Logic Pro Control Plane - Plan
type: feat
date: 2026-07-15
deepened: 2026-07-15
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
---

# Agent-Native Logic Pro Control Plane - Plan

## Goal Capsule

Build and ship a local-first control plane that lets coding agents operate Logic Pro through one authenticated command authority. The deliverable extends the existing JSON-RPC bridge with:

- a bounded and fail-closed Accessibility path for allowlisted channel-strip settings;
- declarative MIDI CC, note, and fixed/template-bounded SysEx actions over the IAC bus;
- a fixed Logic Scripter MIDI-FX protocol for learned downstream plug-in targets on software-instrument strips;
- an MCP stdio adapter that gives Codex, Claude, Cursor, and compatible clients typed tools and read-only resources without creating a second command authority; and
- honest evidence reporting that distinguishes dispatch from observation and scoped verification.

The live proof is intentionally concrete: load the allowlisted Jimmy Vocal Chain.cst into the Audio 2 strip in the disposable Logic project, then independently inspect the exact strip. The proof may claim only what the observation establishes. A visible expected plug-in signature may be scoped verification; a click or popup disappearance alone remains unknown.

“Full Logic control” remains the architectural direction, not a claim that Logic exposes a complete native API or source-state readback. This plan delivers a secure, extensible control plane across the available public macOS mechanisms and documents unsupported surfaces explicitly.

---

## Product Contract

### Summary

External coding agents need a stable developer interface to Logic Pro. Logic does not provide a complete public automation API, so the bridge must translate typed local requests into several constrained native control vectors while preserving one security boundary, one capability catalog, and one lifecycle vocabulary.

The current branch already contains the first secure slice: strict TOML configuration, loopback-only authenticated newline-delimited JSON-RPC, an injectable Mido/RtMidi transport, selected-track volume and mute commands, machine-readable capabilities, and a hardened allowlisted .cst loader. The first live .cst attempt timed out before the Setting button press because recursive System Events/JXA scans took longer than the configured ten-second process deadline. This plan treats that measured failure as the starting point.

### Problem Frame

Agents currently have three separate problems:

1. They can dispatch mapped MIDI, but only through two convenience operations and without a general allowlisted action surface.
2. The .cst workflow is secure at the RPC and filesystem boundaries, but its recursive Accessibility traversal is too slow for the live Logic Mixer hierarchy.
3. There is no MCP-native client surface, and Scripter has not yet been used as a fixed plug-in-target protocol.

The bridge must improve coverage without turning MIDI, JXA, Scripter, MCP, or the filesystem into arbitrary execution channels.

### Product Outcomes

- O1 — An agent can discover what is ready, invoke a configured Logic action through JSON-RPC or MCP, and interpret the result without learning MIDI bytes, macOS UI selectors, shell commands, or host paths.
- O2 — An operator can move from a fresh checkout to a diagnosable bridge through one ordered readiness flow. Every unmet prerequisite is labeled blocked or unverifiable and has one concrete next action.
- O3 — Destructive or state-sensitive workflows provide evidence proportionate to the claim. A channel-strip load never becomes “successful” merely because a UI gesture was dispatched.
- O4 — Scripter proves a real additive plug-in-control path on a disposable software-instrument track without being mistaken for audio-strip control or arbitrary JavaScript execution.

The release-defining workflow is: run the readiness flow, connect an MCP client to the authenticated daemon, discover the safe tools, verify the selected-track volume/mute path, load Jimmy Vocal Chain.cst into Audio 2 with a target-bound disposable-project confirmation, and inspect the scoped postcondition. The Scripter proof is a second bounded workflow using a disposable Retro Synth track and the two initial learned targets. Command authority and discovery requirements serve O1; readiness and documentation serve O2; Accessibility and lifecycle requirements serve O3; Scripter requirements serve O4; MIDI and MCP requirements support both O1 and the two proof workflows.

### Actors

- A1 — Local coding agent: discovers capabilities and invokes typed tools.
- A2 — Logic operator: configures IAC routing, Controller Assignments, Scripter targets, permissions, and destructive confirmations.
- A3 — Bridge maintainer: adds audited mappings and validates them against supported Logic/macOS versions.
- A4 — Logic Pro: receives MIDI, runs Scripter, exposes Accessibility state, and remains the authority for project state.

### Requirements

#### Command authority and discovery

- R1 — All mutations must pass through the existing authenticated loopback daemon. MCP is an adapter client, never a parallel MIDI or UI controller.
- R2 — JSON-RPC and MCP must expose the same allowlisted domain actions, parameter constraints, availability, and lifecycle semantics.
- R3 — Health, capabilities, MIDI output inventory, configured action IDs, configured preset IDs, configured target IDs, and safe observation metadata must be machine-readable without exposing tokens, absolute host paths, raw Accessibility dumps, or private musical content.
- R4 — Unknown methods, unknown fields, notifications, batches, arbitrary script bodies, arbitrary paths, arbitrary selectors, arbitrary URLs, and raw shell commands must fail before transport dispatch.

#### MIDI and Controller Assignments

- R5 — Existing typed volume and mute operations must remain compatible and retain honest unknown readback.
- R6 — A generic domain operation must invoke only a configured opaque MIDI action ID. Configuration may describe bounded CC, note, or SysEx messages; callers may not supply raw message bytes.
- R7 — Parameterized MIDI values must be validated by a declared mode. SysEx payloads must be bounded, seven-bit clean, configured locally, and may expose at most an explicitly configured value slot.
- R8 — MIDI routing must use the exact configured output endpoint and must document that IAC routing is not authentication. Scripter traffic must use a dedicated channel/control range that does not collide with Controller Assignments.

#### Accessibility and channel-strip settings

- R9 — The .cst loader must retain exact preset hash checks, path confinement, ownership and permission checks, fixed packaged scripts, explicit confirmation, cross-process serialization, sanitized subprocess handling, and no automatic retry after ambiguous dispatch.
- R10 — Mixer and popup resolution must be structurally bounded by role, depth, node count, collection size, and overall time. It must fail closed on missing, stale, duplicated, or ambiguous elements.
- R11 — The live loader must resolve exactly one standalone Mixer window, exactly one Mixer layout area, an ordered set of direct channel-strip layout items, one exact target identity, and one direct Setting button. The configured index and expected name must agree immediately before mutation.
- R12 — A read-only target inspector may return only configured opaque target IDs and allowlisted normalized evidence, such as visible plug-in names and a timestamp. Raw AX hierarchy, unrelated track names, menu text, project content, and host paths remain private.
- R13 — A .cst result may reach verified only when a separately executed approved inspector proves the configured scoped postcondition on the same exact target. Popup disappearance alone is observed; inability to inspect or a partial signature is unknown.

#### Scripter

- R14 — Ship one reviewed fixed Scripter source artifact that consumes a dedicated range of MIDI CC messages and converts them into TargetEvent writes for learned plug-in targets below Scripter on the same software-instrument strip.
- R15 — The daemon must expose typed, allowlisted Scripter parameter IDs with normalized values. No RPC or MCP caller may provide JavaScript, target names, CC numbers, MIDI channels, plug-in selectors, or arbitrary source text.
- R16 — Scripter is an additive software-instrument MIDI-FX transport. It must not be represented as direct Audio 2 audio-strip control, universal plug-in automation, a general JavaScript host, an authentication mechanism, or a readback/acknowledgement channel.
- R17 — Mapped Scripter control messages are consumed; unrelated MIDI is passed through. The fixed script performs no network, filesystem, RPC, or subprocess work in HandleMIDI or ProcessMIDI.

#### Agent-native MCP interface

- R18 — Provide an MCP stdio server using the stable official Python SDK line, with stdout reserved for protocol messages and diagnostics sent to stderr.
- R19 — MCP tools must be explicit typed wrappers around the daemon’s public methods. Read-only custom-URI resources must proxy daemon health and capability data.
- R20 — The MCP adapter must connect to the configured loopback daemon with the existing environment token, enforce bounded request timeouts, sanitize transport failures, and never auto-start another daemon or directly open MIDI/Accessibility transports.
- R21 — MCP cancellation or a lost response after dispatch must remain ambiguous. Clients must be told not to retry non-idempotent operations such as toggle, SysEx triggers, or .cst loads without independent state inspection.

#### Quality, safety, and operations

- R22 — Python 3.11+ remains mandatory; dependencies must be GPLv3-compatible and locked. No third-party GUI automation app or Logic binary modification is allowed.
- R23 — Unit and integration tests must cover schemas, transport dispatch, security boundaries, lifecycle truthfulness, MCP parity, JXA failure tokens, Scripter behavior, packaging, and no-secret/no-path leakage.
- R24 — Live Logic/IAC/Accessibility tests must be opt-in, version-recorded, safely repeatable, and refuse destructive execution without explicit environment gates and a disposable-project declaration.
- R25 — Documentation must provide setup, threat boundaries, Scripter installation, Controller Assignment routing, MCP client configuration, known limitations, and an evidence-backed live validation record.
- R26 — Doctor and capability discovery must implement an ordered operator-readiness model. Every prerequisite is ready, blocked, or unverifiable, includes one next action when incomplete, and has an explicit rule for whether the related tool is available.
- R27 — A .cst load must use one shared confirmation contract across JSON-RPC and MCP. Immediately before invocation the client presents the preset ID, target ID, destructive consequence, and disposable-project assertion; the request carries an exact confirmation phrase derived from those IDs. Cancel, malformed confirmation, and stale-target failures end before dispatch.
- R28 — LOGIC_BRIDGE_TOKEN must contain at least 32 encoded bytes from a high-entropy generator. Startup rejects a shorter token; rotation is performed by changing the environment value and restarting the daemon; the token never enters logs, errors, resources, capabilities, subprocess environments, or MCP output.
- R29 — The machine-local configuration file is part of the authorization policy. The daemon must open it fail-closed as a non-symlink regular file owned by the current user, reject group/world-writable mode or an attacker-writable parent directory, and parse from the validated open descriptor.
- R30 — Scripter protocol v1 has two current consumers on a disposable software-instrument track: Retro Synth Filter Cutoff and Filter Resonance, learned to opaque IDs scripter.retro-synth.filter-cutoff and scripter.retro-synth.filter-resonance. Expanding the fixed target set requires a new reviewed protocol/source revision.

### Scope

#### In scope

- Existing authenticated JSON-RPC foundation and its hardening.
- Typed volume, mute, generic allowlisted MIDI action, Scripter parameter, .cst load, health, capability, and target-inspection operations.
- CC, note, and bounded configured SysEx output.
- Pure System Events/JXA automation with strict traversal budgets.
- Official stable Python MCP SDK over stdio, defaulting to the v1 line unless a stable v2 passes the compatibility and parity gates in KTD8 and U5.
- Fixed Scripter source plus manual installation/learning workflow.
- Disposable-project live validation for Jimmy Vocal Chain.cst on Audio 2.

#### Out of scope

- Patching, injecting into, re-signing, or reverse-engineering the Logic Pro binary.
- Direct mutation of Logic project bundles, undocumented project plists, or .cst bytes.
- Arbitrary shell, AppleScript/JXA, JavaScript, MIDI bytes, paths, selectors, URLs, or filesystem resources supplied by clients.
- Scripter on audio strips, arbitrary Scripter runtime injection, Trace-based acknowledgements, or network work in the audio/MIDI processing path.
- Remote network exposure, HTTP MCP transport, WebSocket transport, SSE, experimental MCP tasks, or multi-user authorization.
- Claiming complete Logic project-state readback or universal verification.
- Saving over the user’s non-disposable Logic projects.

### Acceptance Examples

- AE1 — Given the configured IAC output and authenticated request, setting selected-track volume to 0.5 dispatches the configured CC and returns requested, dispatched, unknown. The same operation through MCP yields equivalent structured lifecycle data.
- AE2 — Given no token or a wrong token, every JSON-RPC mutation and every MCP-proxied mutation fails before MIDI, JXA, or Scripter dispatch and does not disclose the configured token.
- AE3 — Given a configured opaque action ID, an agent can invoke its CC, note, or bounded SysEx definition. Supplying a raw CC number, raw SysEx bytes, or an unknown action ID is rejected.
- AE4 — Given the allowlisted Jimmy Vocal Chain preset, target Audio 2, the exact target-bound disposable-project confirmation phrase, one standalone Mixer, and a disposable project, the loader resolves the exact direct strip and setting button within budget and presses only the exact preset item.
- AE5 — If the post-load inspector sees the configured Audio 2 plug-in signature, the result contains verified with scope mixer_plugin_signature. If only the menu interaction is attributable, the result ends unknown and never claims complete preset equivalence.
- AE6 — If any JXA process deadline is exceeded after launch, the daemon terminates and reaps the process group, performs only attributable cleanup, records unknown, and does not retry automatically.
- AE7 — Given a software-instrument strip with the fixed Scripter installed and target slots learned, set_scripter_parameter dispatches only the configured channel/CC/value. The response remains unknown unless an independent approved observation is later added.
- AE8 — Given Audio 2 is an audio strip, Scripter capability metadata explains that Scripter is unavailable for that target instead of pretending to control its audio plug-ins.
- AE9 — An MCP client can list tools and safe resources with no running Logic mutation. MCP stdout remains valid protocol output even when the daemon is unavailable.
- AE10 — Wheel inspection finds every fixed JXA and Scripter resource; clean-install smoke tests run without depending on the source tree.
- AE11 — From a fresh local setup, doctor walks permissions, IAC output, Controller Assignments, daemon authentication, Scripter attestation, and MCP connectivity in order. Each item reports ready, blocked, or unverifiable plus one next action; hard-blocked tools are unavailable, while unverifiable readback is disclosed rather than converted into readiness.
- AE12 — Canceling a .cst confirmation, sending the wrong target-bound phrase, or changing the visible target after confirmation produces a requested-only failure and performs no AXPress.
- AE13 — On a disposable software-instrument track containing Scripter before Retro Synth, the operator learns Filter Cutoff to CC102/target 1 and Filter Resonance to CC103/target 2. The two opaque daemon methods move only those learned targets and still return unknown because no acknowledgement exists.

---

## Planning Contract

### Key Technical Decisions

#### KTD1 — One authenticated loopback daemon is the sole command authority

session-settled: user-directed
Rejected alternative: unauthenticated or non-loopback control and independent MCP transports.
Reason: the user requested IPC access while the repository contract requires a local security boundary. MCP therefore proxies the daemon and cannot open Logic control transports itself.

#### KTD2 — Public operations are fixed and allowlisted

session-settled: user-directed
Rejected alternative: arbitrary shell, JXA, Accessibility selectors, paths, JavaScript, URLs, or raw MIDI payloads.
Reason: fixed typed operations are auditable, testable, least privilege, and compatible with agent discovery.

#### KTD3 — Logic Pro and macOS security controls remain untouched

session-settled: user-directed
Rejected alternative: binary patching, injection, private framework hooks, third-party GUI automation, TCC bypass, or disabling platform protections.
Reason: this is an external control bridge, not a sandbox bypass.

#### KTD4 — The first destructive live proof is Jimmy Vocal Chain.cst to Audio 2

session-settled: user-directed
Rejected alternative: another preset, target, or user project.
Reason: the user selected this preset and strip and identified the open project as disposable. The bridge still requires action-time confirmation and must not save over another project.

#### KTD5 — Scripter is a fixed additive MIDI-FX protocol

session-settled: user-directed
Rejected alternative: omitting Scripter or exposing arbitrary Scripter JavaScript.
Reason: the user explicitly asked to exploit Scripter as a plug-in option. Apple’s documented model permits a fixed script to turn incoming MIDI into learned TargetEvent writes on downstream plug-ins of a software-instrument strip. It does not make Scripter available on Audio 2 or provide a runtime source-injection API, so the implementation must expose that boundary.

#### KTD6 — Execute autonomously through the LFG shipping tail

session-settled: user-directed
Rejected alternative: incremental approval stops before implementation, review, or PR creation.
Reason: the user explicitly authorized autonomous build, test, adversarial review, adjustment, and shipping.

#### KTD7 — Dispatch is never silently promoted to success

session-settled: user-directed
Rejected alternative: returning success after a MIDI send or UI press without independent evidence.
Reason: Logic lacks comprehensive public readback. Results use requested, dispatched, observed, verified, and unknown, with verification scope named.

#### KTD8 — MCP v1 stdio is an adapter to the daemon

Rejected alternative: MCP v2 pre-release, WebSocket, HTTP listener, or a second in-process Logic service.
Reason: the official Python SDK v1.28.0 is the production line on the planning date; v2.0.0b2 is explicitly pre-release. Stdio is the standard local-client transport and keeps the existing daemon as the sole authority. Recheck stable SDK status at implementation time and remain on v1 unless v2 is stable and migration is demonstrably non-breaking.

#### KTD9 — Replace broad recursive JXA scans with a bounded Mixer contract

Rejected alternative: increasing the process timeout while retaining depth-14 whole-window scans, coordinate clicks, or blind retries.
Reason: the live trace showed whole-window scans consuming 11–25 seconds before mutation, while direct Mixer layout and filtered button collections resolved in roughly 0.5–3 seconds. Structural traversal is both faster and safer.

#### KTD10 — Scoped UI evidence can verify only the named postcondition

Rejected alternative: treating plugin-name visibility as proof that every .cst byte, send, routing choice, or plug-in parameter was applied.
Reason: Accessibility can verify visible normalized state, not opaque project internals. A matching configured plug-in signature earns verified only with scope mixer_plugin_signature.

#### KTD11 — Scripter uses a dedicated non-colliding MIDI namespace

Rejected alternative: reusing selected-track Controller Assignment CCs or treating the IAC bus as authenticated.
Reason: Logic may intercept Controller Assignment events before tracks and CC7/CC10 have special behavior. Protocol v1 uses MIDI channel 16 in user-facing notation, zero-based channel 15 in Mido, and CC102–103 for the two Scripter targets; validate no configured collision.

### High-Level Technical Design

The diagrams are architectural constraints, not exact class or method signatures.

~~~mermaid
flowchart TD
    Agent["Coding agent<br/>Codex, Claude, Cursor, Grok"] -->|stdio MCP| MCP["MCP adapter<br/>typed tools + safe resources"]
    Agent -->|authenticated NDJSON| RPC["Loopback JSON-RPC server"]
    MCP -->|authenticated NDJSON| RPC
    RPC --> Contract["Shared method contract<br/>validation + capabilities"]
    Contract --> Service["Logic command service<br/>lifecycle + evidence"]
    Service --> MIDI["MIDI transport<br/>CC / note / bounded SysEx"]
    MIDI --> IAC["IAC Driver AI_Logic_Bridge"]
    IAC --> CA["Logic Controller Assignments"]
    IAC --> SI["Software-instrument input<br/>channel 16"]
    SI --> Scripter["Fixed Scripter MIDI FX<br/>CC102-119 to TargetEvent"]
    Service --> AX["Fixed JXA UI transport<br/>bounded structural resolver"]
    AX --> Mixer["Exact standalone Mixer<br/>exact allowlisted strip"]
    Mixer --> Inspector["Scoped read-only inspector<br/>normalized evidence only"]
    Inspector --> Service
~~~

~~~mermaid
stateDiagram-v2
    [*] --> Requested
    Requested --> [*]: validation or authorization failure
    Requested --> Dispatched: transport accepted the operation
    Dispatched --> Observed: attributable post-dispatch signal
    Dispatched --> Unknown: no approved readback
    Observed --> Verified: scoped postcondition proven
    Observed --> Unknown: evidence incomplete or ambiguous
    Dispatched --> Unknown: timeout, cancellation, or partial failure
    Verified --> [*]
    Unknown --> [*]
~~~

~~~mermaid
sequenceDiagram
    participant A as Agent
    participant D as Authenticated daemon
    participant J as Fixed JXA loader
    participant L as Logic Mixer
    participant I as Fixed inspector
    A->>D: load preset_id + target_id + target-bound confirmation
    D->>D: validate token, allowlists, hash, target, lock
    D->>J: packaged script + fixed internal arguments
    J->>L: resolve exact strip and press exact preset
    L-->>J: attributable popup closes
    J-->>D: observed
    D->>I: inspect same configured target
    I->>L: bounded read-only plug-in snapshot
    alt configured signature matches
        I-->>D: scoped postcondition
        D-->>A: requested, dispatched, observed, verified
    else unavailable, partial, or mismatched
        I-->>D: insufficient evidence
        D-->>A: requested, dispatched, observed, unknown
    end
~~~

### Interface and Contract Notes

- JSON-RPC remains newline-delimited and requires an ID for every request.
- METHOD_SPECS remains the canonical public JSON-RPC registry. The generated JSON Schema and capability document must continue to derive from it.
- Explicit MCP wrappers map one-to-one to public domain methods and are checked against a parity table in tests.
- The generic MIDI method accepts action_id and the value permitted by its configured mode. It never accepts kind, channel, control, note, SysEx data, or routing.
- The Scripter method accepts parameter_id and a normalized value only.
- The inspector accepts one configured target_id only. It returns a safe observation object with target_id, observed_at, source, verification_scope, normalized plugin labels, and completeness; no raw AX properties.
- .cst configuration may include an optional expected_plugin_signature. Order semantics must be explicit: exact ordered match when Logic exposes stable slot order; otherwise a configured subset match with scope and match mode returned.
- Every mutation result includes the existing ordered lifecycle event list. A verified event names its proof source and scope.
- The .cst mutation parameter confirmation must exactly equal load <preset_id> into <target_id> in disposable project. It replaces the weaker bare boolean. Clients display the same IDs and consequence before collecting that phrase; the server re-resolves target identity after validation and immediately before AXPress.

### Normative MIDI Action Schema

Every configured action has an opaque name, one kind, and only the fields allowed for that kind. Unknown or forbidden fields fail configuration. A public logic.invoke_action request contains action_id plus value only when the configured mode requires it.

| Kind | Required configuration | Allowed modes | Client value | Exact emitted sequence |
|---|---|---|---|---|
| control_change | channel 0–15; control 0–127 | normalized, absolute, trigger | normalized requires finite 0.0–1.0; absolute requires integer 0–127; trigger forbids value | normalized/absolute emit one control_change; trigger emits configured value and then optional release_value |
| note | channel 0–15; note 0–127; velocity 1–127; release_velocity 0–127 | trigger only | forbidden | one note_on followed immediately by one note_off; both messages remain inside the transport lock |
| sysex | data containing 1–256 integers from 0–127 | trigger when no value_index; normalized or absolute when one value_index exists | trigger forbids value; normalized requires finite 0.0–1.0; absolute requires integer 0–127 | one sysex message; Mido supplies F0/F7 framing and the configured payload is copied with only value_index substituted |

For control_change trigger, configured value is required and release_value is optional; both are 0–127. For normalized and absolute control_change, configured value/release_value are forbidden. For sysex, channel, control, note, velocity, release_value, literal F0/F7 status bytes, multiple value slots, and more than 256 data bytes are forbidden. An action emits at most two messages. Capabilities expose only action ID, kind, mode, and safe prerequisites; fixed SysEx bytes remain private configuration.

### Accessibility Resolver Contract

The JXA resolver must use the smallest stable hierarchy observed in the live Logic Mixer:

1. Find exactly one Logic process and exactly one standalone Mixer window.
2. Find exactly one direct or narrowly nested AXLayoutArea whose description identifies Mixer.
3. Read only a capped number of direct AXLayoutItem channel strips.
4. Resolve the configured strip by both sorted finite position/index and exact direct name evidence.
5. Resolve exactly one direct Setting button using filtered role/description collections.
6. Re-resolve and repeat identity checks immediately before AXPress.
7. Snapshot existing attributable popup/menu state, press once, and resolve the newly exposed menu within a separate bounded budget.
8. Press only the exact configured preset item.

Protocol v1 caps Mixer layout traversal at depth 3, direct strips at 256, direct elements/buttons read per strip at 64, target-name evidence per strip at 8, and total selector-critical collection reads at 128. The script checks a 5-second pre-press budget, an 8-second popup/item budget, and a 3-second dismissal budget; the Python process-group deadline is 20 seconds and cleanup gets a separate maximum of 3 seconds. Any cap, stale element, read exception, duplicate candidate, identity disagreement, or elapsed checkpoint produces a stable error. A single System Events call can still overrun a script checkpoint, so the Python deadline remains the outer kill boundary; pure System Events/JXA does not promise per-AX-call timeouts.

### Scripter Protocol Contract

- Human MIDI channel: 16; Mido channel field: 15.
- Protocol v1 controls: CC102 is target 1 / Retro Synth Filter Cutoff; CC103 is target 2 / Retro Synth Filter Resonance.
- Input: normalized daemon value converted to one integer from 0 through 127.
- Behavior: mapped control creates a TargetEvent for the associated learned target slot and does not forward that control event.
- Pass-through: all unrelated MIDI calls Send unchanged.
- State: output-only; Trace is diagnostic and throttled, not an acknowledgement.
- Installation: the operator pastes/runs the reviewed fixed source in Logic Scripter, learns downstream targets, and saves the plug-in setting or project/template. The bridge never edits the script at runtime.
- Availability: disabled by default and unavailable until configuration declares the dedicated route, both parameter IDs, and an explicit operator_attested flag for the installed script/learned targets. The loaded Logic state remains unverifiable and is reported as such.

### MCP Contract

- Dependency target: mcp>=1.28,<2 with the lockfile pinning the resolved release.
- Runtime: FastMCP with explicit stdio transport.
- stdout: protocol only; stderr: bounded diagnostic logs without token or private path disclosure.
- Resources: logic-bridge://health and logic-bridge://capabilities; optionally logic-bridge://midi/outputs if it remains path-free.
- Tools: set_volume, toggle_mute, invoke_action, set_scripter_parameter, inspect_mixer_target, and load_cst_preset.
- The adapter is a loopback JSON-RPC client and does not construct LogicCommandService, MidiTransport, or AccessibilityCstPresetLoader.
- Each MCP tool call opens one fresh daemon connection, writes exactly one authenticated request, reads exactly one matching response, and closes. Timeout or cancellation closes that connection so a stale reply cannot be consumed by a later tool.
- Stdio uses the launching client process as its trust boundary and does not add MCP-layer OAuth. Destructive authorization is still enforced by the daemon token, allowlists, and method-specific confirmation contract.
- Each tool returns structured lifecycle/error data. Compatibility text, if emitted by the SDK, must summarize without losing the structured result.

### Operator Readiness Contract

The ordered readiness flow is:

1. configuration integrity and token strength;
2. Accessibility and Automation consent for UI operations;
3. exact IAC endpoint availability;
4. Controller Assignment configuration/attestation;
5. Scripter source hash, placement, learned targets, and operator attestation;
6. daemon reachability/authentication; and
7. MCP initialization, resource discovery, and tool schema parity.

Doctor reports ready when a hard prerequisite is proven, blocked when the operation cannot safely dispatch, and unverifiable when Logic offers no approved readback. Every non-ready item includes one next_action code and human instruction. A hard-blocked prerequisite makes its tool unavailable. An unverifiable Controller Assignment may remain dispatchable with unknown lifecycle if the MIDI endpoint and local mapping are configured; Scripter additionally requires operator_attested because the script/learned targets cannot be inspected externally. The .cst method remains unavailable until file integrity, target configuration, Accessibility trust, fixed scripts, and explicit confirmation semantics are ready; Automation consent stays unknown until the first dispatch when macOS provides no non-mutating probe.

### System-Wide Impact

- Configuration: new MIDI kinds, bounded SysEx fields, Scripter route/parameters, optional .cst expected plug-in signatures, and UI traversal budgets.
- Authorization policy: token length and config-file integrity are validated before constructing transports or serving requests.
- Runtime: MIDI stays independently lock-protected; JXA mutation and inspection share the existing cross-process UI lock where needed to prevent target drift.
- Schema: contract version increments and generated config/command_schema.json changes in the same unit as runtime validation.
- Packaging: wheel includes bridge_scripts and scripter artifacts; source-tree-relative lookup is prohibited.
- Logging: MCP logs move to stderr; daemon errors remain sanitized and token/path-free.
- Failure propagation: adapter failures preserve daemon bridge codes; post-dispatch disconnects/cancellation become ambiguous and are never converted into safe-to-retry errors.
- User data: .cst load remains destructive to a channel strip and requires explicit confirmation plus disposable/backed-up project guidance.
- Compatibility: live selectors are version-sensitive. Record macOS and Logic versions and retain fixtures or normalized snapshots for each verified contract.

### Assumptions and Sequencing

- The current IAC Driver AI_Logic_Bridge port remains available.
- Selected-track volume CC7 and mute CC20 remain user-verified Controller Assignments.
- The disposable Logic project remains the only live .cst target during this run.
- Audio 2 remains the second visible track strip and is cross-checked by exact name; neither fact is trusted independently.
- The stable MCP v1 SDK remains available during implementation. Recheck before locking because the official project has announced a v2 transition.
- Scripter setup requires a software-instrument strip and manual Logic-side learning; it is tested independently from the Audio 2 .cst proof.

Implementation proceeds foundation/contract first, then the bounded UI resolver, then generalized MIDI and Scripter, then MCP, then packaging/docs/live validation. Each unit must leave the default offline test suite green.

### Risks and Mitigations

| Risk | Consequence | Mitigation |
|---|---|---|
| Logic Accessibility hierarchy changes | Wrong or missing target | Fail closed, dual index/name identity, strict caps, fixtures, version record |
| System Events call stalls | Process timeout after partial UI action | Outer process-group timeout, attributable cleanup, no retry, unknown lifecycle |
| Popup ownership is not exposed as expected | Preset cannot be selected safely | Inspect only bounded post-press deltas; never fall back to coordinates or global menu guesses |
| Plugin-name snapshot is incomplete | False verification | Require completeness and configured match mode; otherwise unknown |
| Controller Assignments intercept Scripter CCs | Scripter target does not move | Dedicated channel/range, collision validator, setup diagnostics, live negative test |
| MIDI feedback loop | Repeated or runaway events | One-way IAC setup, no echo route, bounded send count, opt-in no-loop live test |
| MCP process obtains user privileges | Broader local impact if tools are unsafe | Explicit tools/resources only, daemon auth, no arbitrary arguments, no direct transport access |
| MCP v2 transition | Dependency churn | Pin stable v1 range and lock; re-evaluate only after stable v2 and passing parity tests |
| Lost response after non-idempotent action | Unsafe duplicate retry | Ambiguous result documentation, no automatic retry, use independent inspection where available |
| Scripter mistaken for audio-strip automation | False capability claim | Capability prerequisite states software-instrument only; separate docs and tests |
| Weak token or replaced allowlist policy | Unauthorized local mutation after restart | Minimum token length, rotation guidance, validated config descriptor, safe parent permissions |
| Dirty worktree contains earlier work | Accidental overwrite | Preserve existing changes, make scoped patches, inspect diffs before each shipping step |

### Deferred Implementation Notes

- Exact popup ownership attributes may differ across Logic 12.x builds. Resolve from live bounded inspection during U2; do not weaken the structural contract.
- The expected Jimmy Vocal Chain plug-in signature must be recorded from the verified preset and compared only to normalized visible slot labels. If Logic exposes insufficient labels, the live result remains unknown.
- The MCP SDK status must be rechecked immediately before dependency modification. A stable v2 release alone is insufficient reason to migrate during this plan.
- Native AXUIElement helpers could provide per-element messaging timeouts, paged reads, multiple-attribute reads, and observer notifications. They are deferred unless pure JXA cannot meet the bounded live contract; introducing a signed native helper would require a separate design and security review.

---

## Implementation Units

### U1 — Harden the command authority and configuration policy root

**Goal:** Preserve the already implemented secure slice while making the bearer credential and machine-local allowlist policy strong enough to authorize destructive local operations.

**Traces:** R1–R4, R22–R23, R28–R29; AE2, AE9; KTD1–KTD3, KTD7.

**Files:**

- Modify daemon/__main__.py
- Modify daemon/config.py
- Modify daemon/diagnostics.py
- Modify daemon/server.py
- Modify daemon/errors.py
- Modify tests/unit/test_config.py
- Modify tests/unit/test_diagnostics.py
- Modify tests/integration/test_rpc_boundary.py

**Approach:**

- Validate the encoded LOGIC_BRIDGE_TOKEN length before server construction and reject fewer than 32 bytes with a path-free startup/doctor error.
- Document generation with a cryptographically secure local generator and rotation by daemon restart; never infer entropy from character variety.
- Open configuration with no-follow semantics, then fstat the open descriptor before parsing. Require a current-user-owned regular file, reject group/world-writable mode, and require the immediate parent to be current-user-owned and not group/world writable.
- Parse TOML from the same validated descriptor so validation and use cannot target different files.
- Preserve the exact loopback, request-size, read-timeout, connection-limit, token-comparison, and METHOD_SPECS dispatch boundaries.
- Leave generic MIDI, Scripter, inspector, and MCP public methods to their owning units; U1 adds no dispatch surface.

**Test scenarios:**

- Missing, empty, 31-byte, multibyte-under-32-byte, and wrong token values fail before server start or transport construction.
- A 32-byte or longer token authenticates; no returned error includes the token or a digest/substring of it.
- Config symlink, non-regular file, wrong owner, group/world-writable file, unsafe parent, and replacement between path lookup and open all fail closed.
- A current-user-owned 0644 regular config under a current-user-owned non-writable-by-others directory loads from its validated descriptor.
- Existing volume, mute, health, capabilities, request limits, and authentication tests remain green.

**Verification outcome:** The current secure slice is behaviorally preserved, weak credentials and replaceable policy files fail before serving, and no new domain method exists yet.

### U2 — Replace the slow Accessibility scans and add scoped evidence

**Goal:** Make the .cst path fast enough for the live Mixer while increasing selector precision and enabling honest scoped postcondition inspection.

**Traces:** R9–R13, R23–R27; AE4–AE6, AE10–AE12; KTD4, KTD7, KTD9, KTD10.

**Files:**

- Modify daemon/accessibility.py
- Add daemon/ui_observation.py
- Modify daemon/commands.py
- Modify daemon/rpc_contract.py
- Modify daemon/errors.py
- Modify bridge_scripts/load_cst.js
- Modify bridge_scripts/cleanup_cst.js
- Add bridge_scripts/inspect_mixer.js
- Modify bridge_scripts/README.md
- Modify tests/fakes.py
- Modify tests/unit/test_accessibility.py
- Add tests/unit/test_ui_observation.py
- Modify tests/unit/test_commands.py
- Modify tests/integration/test_rpc_boundary.py
- Add tests/fixtures/accessibility/logic-12-mixer-normalized.json
- Add tests/live/test_logic_cst_live.py

**Approach:**

- Replace findAll depth-14 scans with the bounded Mixer contract from KTD9.
- Use filtered System Events collections for direct Setting buttons and direct strip identity evidence.
- Add the protocol-v1 node/read/depth/collection/wall-clock budgets above, set the example outer UI timeout to 20 seconds, and emit stable error codes for each budget exhaustion.
- Re-resolve all selector-critical elements immediately before press.
- Keep cleanup independently bounded and attributable to the exact target/preset interaction.
- Implement a fixed read-only inspector that emits a stable machine token/payload parsed and sanitized by Python.
- Configure optional normalized expected plug-in signatures and match modes.
- Replace the bare confirm boolean with the exact target-bound confirmation phrase from R27 in METHOD_SPECS, JSON Schema, command handling, and MCP parity expectations.
- Treat cancel, malformed phrase, and any target identity drift after confirmation as requested-only failures with zero AXPress calls.
- Run inspection after observed .cst interaction. Emit verified only for a complete configured signature match; otherwise emit unknown with a non-sensitive reason.

**Test scenarios:**

- Normalized Logic 12 fixture resolves exactly the second Audio 2 strip and its one direct Setting button.
- Duplicate Mixer windows, duplicate Audio 2 strips, mismatched index/name, non-finite positions, missing layout area, missing button, stale element, or budget exhaustion fails before press.
- Popup lookup accepts one attributable new popup/menu and rejects pre-existing, adjacent-strip, or ambiguous menus.
- Timeout after launch remains non-retryable and ends unknown even if cleanup succeeds.
- Inspector output parser rejects extra fields, unexpected labels, excessive plugin counts, invalid UTF-8/control characters, raw paths, and malformed script markers.
- Exact and subset signature modes verify only when completeness and target identity are proven.
- A popup-disappearance-only result ends observed then unknown.
- Cancel, wrong confirmation phrase, extra confirmation fields, and post-confirmation target drift fail before mutation.
- Wheel/resource lookup works without the repository source tree.
- The opt-in live test performs read-only resolution first and requires the exact preset ID, target ID, target-bound phrase, and disposable-project gates before the one mutation.

**Verification outcome:** Synthetic and fixture tests prove bounded resolution and evidence scoping; instrumented live read-only resolution completes under the configured deadline before any mutation retry.

### U3 — Implement broad declarative MIDI actions

**Goal:** Let agents address the breadth of Logic Controller Assignments through safe opaque action IDs while retaining convenience methods and low latency.

**Dependencies:** U1 must be complete; U3 owns all generic MIDI configuration, message construction, public dispatch, compatibility routing, and collision enforcement.

**Traces:** R2–R8, R21–R24; AE1–AE3; KTD2, KTD7, KTD11.

**Files:**

- Modify daemon/midi_transport.py
- Modify daemon/commands.py
- Modify daemon/config.py
- Modify daemon/rpc_contract.py
- Modify daemon/diagnostics.py
- Modify config/bridge.example.toml
- Modify config/command_schema.json
- Modify tests/fakes.py
- Modify tests/unit/test_midi_transport.py
- Modify tests/unit/test_commands.py
- Modify tests/unit/test_config.py
- Modify tests/unit/test_diagnostics.py
- Modify tests/integration/test_rpc_boundary.py
- Add tests/live/test_iac_live.py

**Approach:**

- Implement the Normative MIDI Action Schema exactly in frozen configuration models; kind-specific forbidden fields are as important as required fields.
- Add internally typed message builders for CC, note, and SysEx and retain lazy exact-port opening.
- Serialize each full action through the transport lock and enforce the schema’s one- or two-message maximum.
- Register logic.invoke_action in METHOD_SPECS only in this unit, regenerate the checked-in schema, and expose it only for configured IDs; generic does not mean raw.
- Route set_volume and toggle_mute through the same internal validated action executor while preserving their public parameters, emitted bytes, and lifecycle.
- Enforce Scripter channel/control collision checks when U4 configuration is present; no generic action may occupy channel 15 CC102 or CC103 in protocol v1.
- Surface kind, value mode, and safe routing prerequisites in capabilities without revealing fixed SysEx bytes.
- Add opt-in IAC tests that check the exact endpoint, bounded delivery, and absence of an echo loop.

**Test scenarios:**

- Concurrent invokes reuse the exact configured port without interleaving a trigger pair.
- A disappeared or renamed IAC output yields MIDI_PORT_NOT_FOUND with a safe list of available endpoint names.
- Partial trigger failure reports the exact successful send count and unknown state.
- CC normalized/absolute/trigger, note trigger, fixed SysEx, and one-slot normalized/absolute SysEx follow the exact field, value, and message-sequence table.
- Status bytes, payloads over 256 bytes, multiple/out-of-range value slots, forbidden kind fields, unsupported modes, and more than two messages fail at configuration or request validation.
- Fixed SysEx cannot be changed by RPC/MCP input; parameterized data changes only its configured slot.
- No public method can address an unconfigured channel, CC, note, or SysEx manufacturer payload.
- Generated schema/capabilities include logic.invoke_action and exclude internal execute/raw-send methods and fixed SysEx bytes.
- Live no-loop probe receives no echoed copy during the bounded observation window.

**Verification outcome:** Offline transports are deterministic and safe; opt-in live IAC dispatch demonstrates the configured route without feedback.

### U4 — Add the fixed Scripter TargetEvent channel

**Goal:** Provide a useful Scripter plug-in option for learned downstream parameters on software-instrument strips without exposing runtime code injection.

**Dependencies:** U3 must provide the collision-safe MIDI transport and action primitives.

**Traces:** R14–R17, R21–R26, R30; AE7–AE8, AE10–AE11, AE13; KTD5, KTD7, KTD11.

**Files:**

- Add scripter/LogicBridgeTargets.js
- Add scripter/README.md
- Add daemon/scripter.py
- Modify daemon/config.py
- Modify daemon/commands.py
- Modify daemon/rpc_contract.py
- Modify daemon/diagnostics.py
- Modify pyproject.toml
- Add tests/js/scripter_harness.js
- Add tests/unit/test_scripter.py
- Modify tests/unit/test_config.py
- Modify tests/unit/test_commands.py
- Modify tests/unit/test_diagnostics.py
- Add tests/live/test_scripter_live.py

**Approach:**

- Implement protocol v1 with exactly two fixed target parameters: Retro Synth Filter Cutoff on CC102/target 1 and Filter Resonance on CC103/target 2, both on MIDI channel 16.
- Consume mapped CCs and pass every unrelated MIDI event through unchanged.
- Keep the two opaque parameter IDs and route attestation in daemon configuration; the public method accepts only parameter_id and normalized value.
- Package the script as data and publish its SHA-256 in capability/setup output so the operator can confirm the installed source revision.
- Report manual installation, software-instrument placement before Retro Synth, both learned targets, input routing, source hash, operator_attested, and output-only evidence as prerequisites.
- Keep live Scripter proof separate from the Audio 2 .cst proof.

**Test scenarios:**

- Node harness stubs Logic’s MIDI event classes and verifies CC102 and CC103 map to exactly one target slot/value.
- Values 0 and 127 map to the expected TargetEvent normalized endpoints.
- Mapped control messages are not forwarded; notes, pitch bend, and unrelated CCs are forwarded once.
- The script source contains no network, filesystem, eval, dynamic Function, subprocess, or source-loading primitive.
- The daemon emits only channel 15 with CC102 or CC103 for the two allowlisted parameter IDs.
- A third parameter ID, missing operator attestation, wrong source hash, partial learned-target declaration, or generic MIDI collision is unavailable/rejected.
- Audio-strip target declarations are rejected or reported unavailable with a software-instrument-only reason.
- Live opt-in test records the manual target mapping and confirms movement without claiming an acknowledgement.

**Verification outcome:** The fixed two-parameter script passes its isolated behavior harness, package checks, daemon contract tests, and a disposable Retro Synth live proof when the operator completes the opt-in setup.

### U5 — Add the MCP stdio adapter with action parity

**Goal:** Give coding agents a standard typed interface while keeping the daemon as the only Logic command authority.

**Dependencies:** U1 provides credential/config hardening; U2–U4 define the daemon methods that MCP wraps.

**Traces:** R1–R4, R18–R23, R25–R28; AE1–AE3, AE7, AE9–AE12; KTD1, KTD2, KTD7, KTD8.

**Files:**

- Add daemon/rpc_client.py
- Add daemon/mcp_server.py
- Modify daemon/__main__.py
- Modify pyproject.toml
- Modify uv.lock
- Add tests/unit/test_rpc_client.py
- Add tests/unit/test_mcp_server.py
- Add tests/integration/test_mcp_stdio.py
- Modify tests/integration/test_rpc_boundary.py

**Approach:**

- Recheck the official Python MCP SDK production release and retain mcp>=1.28,<2 unless a stable, compatible v2 is proven by all parity tests.
- Add a bounded authenticated NDJSON client that uses one fresh loopback connection per tool/resource request, writes one request, validates one matching response envelope, and closes on success, timeout, cancellation, or parse failure.
- Register explicit FastMCP tools and fixed custom-URI resources; never dynamically expose arbitrary daemon methods.
- Add a logic-bridge mcp CLI mode that uses stdio and sends logs only to stderr.
- Preserve daemon bridge codes and structured lifecycle events in tool results.
- Test an actual MCP stdio session rather than only calling wrapper functions.

**Test scenarios:**

- MCP initialize, tools/list, resources/list, resource read, and tool call succeed against a temporary authenticated daemon.
- Tool schemas reject unknown fields, raw paths, scripts, selectors, URLs, message bytes, and missing explicit .cst confirmation.
- Every mutation tool calls the daemon exactly once and never constructs or touches a MIDI/UI transport.
- Two concurrent calls use separate daemon connections; a timed-out/canceled post-write call cannot leave a response that any later tool can consume.
- Wrong token, daemon unavailable, malformed daemon response, timeout, and cancellation return sanitized MCP errors without corrupting stdout.
- Capability parity test proves every intended public mutation has one MCP tool and every MCP tool maps to one METHOD_SPECS entry.
- Clean subprocess stderr may contain diagnostics while stdout parses as MCP protocol only.

**Verification outcome:** A real stdio MCP client can discover and exercise the safe bridge surface through the daemon with schema and lifecycle parity.

### U6 — Harden packaging, documentation, and live validation

**Goal:** Produce a reproducible operator and agent handoff, then validate the complete path in the disposable Logic project.

**Dependencies:** U1–U5 must pass their offline gates before destructive live validation.

**Traces:** R22–R30; AE1–AE13; all KTDs.

**Files:**

- Modify README.md
- Add docs/ARCHITECTURE.md
- Add docs/SETUP.md
- Add docs/SECURITY.md
- Add docs/SCRIPTER.md
- Add docs/LIVE_VALIDATION.md
- Add docs/MCP_CLIENTS.md
- Modify bridge_scripts/README.md
- Modify scripter/README.md
- Modify config/bridge.example.toml
- Modify pyproject.toml
- Modify tests/integration/test_rpc_boundary.py
- Add tests/integration/test_distribution.py
- Modify .gitignore only if new generated/local artifacts require it

**Approach:**

- Document the authority boundary, transport matrix, state/evidence model, and unsupported claims.
- Provide one ordered readiness flow for config/token, Accessibility/Automation, IAC, Controller Assignments, Scripter, daemon, and MCP. Doctor and the guides use the same ready/blocked/unverifiable vocabulary, next-action codes, and tool-availability rules.
- Provide the shared destructive confirmation flow: display preset ID, target ID, consequence, and disposable-project assertion; collect the exact target-bound phrase; define cancel and stale-target outcomes.
- Keep machine-local config and tokens ignored; example config contains no user path, hash, or private target.
- Build a wheel, install it into an isolated environment, and run CLI/resource smoke tests outside the checkout.
- Execute the live sequence only after offline gates pass:
  1. run doctor and read-only Mixer inspection;
  2. verify exactly one disposable project and the Audio 2 target;
  3. capture the pre-load normalized plug-in snapshot;
  4. invoke Jimmy Vocal Chain.cst once with explicit confirmation;
  5. capture the post-load snapshot and lifecycle;
  6. do not save the project;
  7. record exact Logic/macOS versions and observed evidence.
- If a live selector fails, tighten the fixed structural contract and repeat read-only inspection before another mutation. Never mask failure with coordinates or broad selectors.

**Test scenarios:**

- Wheel includes all JXA and Scripter resources and excludes config/bridge.toml, caches, Logic projects, .cst user assets, tokens, and local validation captures with private content.
- Installed doctor, capabilities, serve, and mcp entry points run from outside the checkout.
- README examples validate against the generated schema.
- Fresh-setup fixtures exercise every readiness state and prove each incomplete state has one next action and the correct tool availability.
- JSON-RPC and MCP examples present and validate the same target-bound .cst confirmation phrase; cancellation dispatches nothing.
- Live validation record distinguishes observed, scoped verified, and unknown evidence.
- A repository secret/path scan finds no token, Jimmy preset absolute path, user home expansion, or raw private AX capture in tracked artifacts.
- Git diff contains no unrelated user changes and no dead experimental scripts.

**Verification outcome:** The distribution is self-contained, the docs are executable, and the disposable-project record either proves the scoped Audio 2 plug-in signature or honestly records the remaining unknown with diagnostic evidence.

---

## Verification Contract

Run the following from the repository root after each relevant unit and again before shipping:

~~~sh
uv sync --python 3.13
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy daemon
git diff --check
~~~

Validate packaged JavaScript syntax and behavior:

~~~sh
osacompile -l JavaScript -o /tmp/logic-bridge-load.scpt bridge_scripts/load_cst.js
osacompile -l JavaScript -o /tmp/logic-bridge-cleanup.scpt bridge_scripts/cleanup_cst.js
osacompile -l JavaScript -o /tmp/logic-bridge-inspect.scpt bridge_scripts/inspect_mixer.js
node tests/js/scripter_harness.js
~~~

Validate schema, packaging, and clean installation:

~~~sh
uv build
uv run python -m daemon.__main__ --config config/bridge.example.toml capabilities
uv run pytest -q tests/integration/test_installed_distribution.py tests/integration/test_mcp_stdio.py
~~~

The distribution test must inspect the built wheel programmatically and install it into an isolated temporary environment; it must not assume source-tree resources.

Opt-in live tests require all destructive gates:

~~~sh
LOGIC_BRIDGE_RUN_LIVE_IAC=1 LOGIC_BRIDGE_IAC_SENTINEL_ATTESTATION='send one unassigned channel 16 cc 119 sentinel' uv run pytest -q tests/live/test_iac_live.py
LOGIC_BRIDGE_RUN_LIVE_SCRIPTER=1 LOGIC_BRIDGE_DISPOSABLE_PROJECT=1 LOGIC_BRIDGE_LIVE_SCRIPTER_PARAMETER_ID='scripter.retro-synth.filter-cutoff' LOGIC_BRIDGE_LIVE_SCRIPTER_VALUE='0.5' LOGIC_BRIDGE_LIVE_SCRIPTER_ATTESTATION='protocol v1 installed before Retro Synth on a disposable software-instrument track; both targets learned; channel 16 CC102/CC103 isolated' LOGIC_BRIDGE_LIVE_SCRIPTER_DISPATCH='send scripter.retro-synth.filter-cutoff value 0.5 once to disposable Scripter track' uv run pytest -q tests/live/test_scripter_live.py
LOGIC_BRIDGE_RUN_LIVE_CST=1 LOGIC_BRIDGE_DISPOSABLE_PROJECT=1 LOGIC_BRIDGE_LIVE_PRESET_ID='preset.jimmy-vocal-chain' LOGIC_BRIDGE_LIVE_TARGET_ID='track.audio-2' LOGIC_BRIDGE_LIVE_CST_CONFIRMATION='load preset.jimmy-vocal-chain into track.audio-2 in disposable project' uv run pytest -q tests/live/test_logic_cst_live.py
~~~

Live tests must skip by default. The .cst test must additionally require the exact allowlisted preset ID, target ID, and target-bound disposable-project confirmation phrase from machine-local configuration; environment flags alone may not select a path or target.

Before the final commit:

- regenerate config/command_schema.json and prove no diff from runtime generation;
- run a token, private path, raw AX dump, and accidental .cst/project artifact scan;
- inspect the complete staged diff;
- confirm every mutation response uses the lifecycle vocabulary and no test asserts unverified success;
- confirm MCP stdout has no logs;
- confirm all fixed script resources have package coverage;
- confirm no deprecated WebSocket or experimental MCP task API is present.

---

## Definition of Done

- [ ] R1–R30 are implemented or explicitly marked infeasible with evidence; no requirement is silently dropped.
- [ ] AE1–AE13 have passing automated coverage or a recorded opt-in live result.
- [ ] The existing secure JSON-RPC slice remains backward compatible for volume and mute.
- [ ] Tokens shorter than 32 encoded bytes and unsafe/replaced config policy files fail before serving; valid credentials and safe config still work.
- [ ] The broad MIDI action surface supports configured CC, note, and bounded SysEx without raw client-controlled messages.
- [ ] The Accessibility resolver is structurally bounded, live read-only timing passes, and ambiguous selection fails closed.
- [ ] Jimmy Vocal Chain.cst is attempted at most once per confirmed live run on Audio 2 in the disposable project, followed by independent scoped inspection and no project save.
- [ ] The result claims verified only if the configured Audio 2 plug-in signature is independently proven; otherwise it ends unknown.
- [ ] The fixed two-target Retro Synth Scripter artifact, daemon mapping, behavior harness, setup guide, and disposable-track live proof are complete, with software-instrument-only limitations explicit.
- [ ] MCP stdio tools/resources work through the authenticated daemon and demonstrate contract parity.
- [ ] Concurrent/canceled MCP calls are isolated by one daemon connection per request; no stale response can reach another tool.
- [ ] Doctor and setup docs implement the ordered ready/blocked/unverifiable flow with one next action and explicit tool availability for every prerequisite.
- [ ] JSON-RPC and MCP enforce the same target-bound .cst confirmation phrase; cancel, malformed phrase, and stale target dispatch nothing.
- [ ] Tests, Ruff, formatting, strict mypy, JavaScript compilation/harness, schema generation, wheel inspection, and clean-install smoke tests pass.
- [ ] Security review confirms no arbitrary execution/path/selector/MIDI surface, no token or private-path leak, and no Logic/TCC modification.
- [ ] Documentation covers setup, architecture, security, MCP clients, Scripter, live validation, supported versions, and unsupported claims.
- [ ] Experimental broad-scan scripts, temporary traces, compiled scripts, private AX captures, and dead-end code are removed from the repository.
- [ ] Existing user changes are preserved and the final diff contains only project work.
- [ ] Changes are simplified, independently reviewed, committed intentionally, pushed, and submitted as a PR when a reachable remote exists.

---

## Sources and References

### Repository evidence

- README.md — architectural contract, safety boundaries, lifecycle model, and live target.
- daemon/rpc_contract.py — current canonical JSON-RPC registry and generated schema.
- daemon/commands.py — current lifecycle and typed command service.
- daemon/accessibility.py and bridge_scripts/ — current allowlist, subprocess, lock, and broad-scan implementation.
- tests/ — existing fake-injection, exact contract, loopback boundary, and leakage-testing conventions.
- Live 2026-07-15 trace — broad Mixer scans took approximately 11–25 seconds; direct Mixer layout/direct Setting button filtering resolved in approximately 0.5–3 seconds.

### Primary external references

- Apple, Transfer MIDI information between apps:
  https://support.apple.com/en-gb/guide/audio-midi-setup/ams1013/mac
- Apple, Use the Scripter plug-in:
  https://support.apple.com/en-mt/guide/logicpro/lgcef1c11e8f/mac
- Apple, Use the Script Editor:
  https://support.apple.com/guide/logicpro/use-the-script-editor-lgcecc16550d/mac
- Apple, JavaScript event object and TargetEvent:
  https://support.apple.com/guide/logicpro/use-the-javascript-event-object-lgce0d0efc5a/10.7/mac/11.0
- Apple, HandleMIDI and ProcessMIDI:
  https://support.apple.com/en-ca/guide/logicpro/lgce12088271/mac
  https://support.apple.com/en-in/guide/logicpro/lgce225e4d89/10.7/mac/11.0
- Apple, Retro Synth filter controls:
  https://support.apple.com/en-ie/guide/logicpro/lgsi213c45bb/10.7/mac/11.0
- Apple, Channel strip settings:
  https://support.apple.com/en-ie/guide/logicpro/lgcp35966da6/10.7/mac/11.0
- Apple, Logic remote-control event flow and input filtering:
  https://support.apple.com/en-mide/guide/logicpro/lgcp79d3333a/10.7/mac/11.0
  https://support.apple.com/guide/logicpro/input-filter-settings-lgcp94b70f34/10.7/mac/11.0
- Apple, Automate the user interface:
  https://developer.apple.com/library/archive/documentation/LanguagesUtilities/Conceptual/MacAutomationScriptingGuide/AutomatetheUserInterface.html
- Apple, Accessibility messaging timeout and bounded collection APIs:
  https://developer.apple.com/documentation/applicationservices/1459345-axuielementsetmessagingtimeout
  https://developer.apple.com/documentation/applicationservices/1462060-axuielementcopyattributevalues
  https://developer.apple.com/documentation/applicationservices/1462051-axuielementcopymultipleattribute
- Model Context Protocol Python SDK v1.28.0:
  https://github.com/modelcontextprotocol/python-sdk/releases/tag/v1.28.0
- Model Context Protocol transports, tools, resources, and security:
  https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
  https://modelcontextprotocol.io/specification/2025-11-25/server/tools
  https://modelcontextprotocol.io/specification/2025-11-25/server/resources
  https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices
