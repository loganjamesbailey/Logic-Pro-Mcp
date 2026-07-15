# Scripter Plug-in Control

Logic Pro Scripter provides one additional, deliberately narrow control path for
learned downstream plug-in parameters. It is a software-instrument MIDI-FX
feature. It does not run on an audio channel strip such as Audio 2 and is not a
substitute for Controller Assignments or the `.cst` loader.

The reviewed source and concise install contract live in
[the Scripter asset directory](../scripter/README.md). See also
[Architecture](ARCHITECTURE.md), [Setup](SETUP.md),
[Security](SECURITY.md), and [Live validation](LIVE_VALIDATION.md).

## Protocol v1

Protocol v1 has exactly two consumers:

| Opaque parameter ID | Operator MIDI route | Scripter target slot | Learned Retro Synth parameter |
| --- | --- | --- | --- |
| `scripter.retro-synth.filter-cutoff` | Channel 16, CC102 | Target 1 | Retro Synth Filter Cutoff |
| `scripter.retro-synth.filter-resonance` | Channel 16, CC103 | Target 2 | Filter Resonance |

Mido represents channel 16 as zero-based channel `15`. A normalized daemon value
from 0.0 through 1.0 is converted to an integer from 0 through 127. The fixed
script consumes each mapped CC after producing exactly one TargetEvent. All
unrelated MIDI, including CC102/103 on another channel, passes through unchanged.

Adding another parameter, changing a route, or changing source behavior requires
a reviewed protocol/source revision. It is not a machine-local runtime extension.

## Manual Logic setup

Use a new disposable software-instrument track:

1. Insert **Retro Synth** as the instrument.
2. Insert **Scripter** in a MIDI FX slot before Retro Synth.
3. Open [`LogicBridgeTargets.js`](../scripter/LogicBridgeTargets.js), copy its
   complete reviewed contents into Scripter, and run the script.
4. Assign Scripter Target 1 to Retro Synth **Filter Cutoff**.
5. Assign Scripter Target 2 to Retro Synth **Filter Resonance**.
6. Route the dedicated IAC input to this strip on MIDI channel 16.
7. Confirm Logic Controller Assignments and other routes do not consume channel
   16 CC102 or CC103.
8. Test each target at a non-destructive middle value while watching only the
   intended control.
9. For the live gate, leave the disposable project unsaved. Creating a persistent
   template is a separate operator action after validation and is outside the test.

The bridge does not perform these UI steps, learn the targets, or paste source
into Logic. Manual installation is part of the security boundary.

## Availability and attestation

The intended daemon capability remains unavailable until local policy declares:

- the fixed protocol is enabled;
- the dedicated channel is channel 16;
- both exact opaque parameter IDs and controls are present;
- the packaged script hash matches the reviewed source;
- the operator attests that the script is installed before Retro Synth and both
  targets are learned.

Logic provides no approved external readback for Scripter source placement or
learned target assignments. The operator attestation makes dispatch available
but does not turn the result into verified state.

The machine-local policy must reproduce the fixed block from
`config/bridge.example.toml`:

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

Do not set `enabled` or `operator_attested` to true until the corresponding Logic
setup is visibly present. Configuration validation rejects any change to the
reviewed protocol, source digest, placement, routes, or learned target set.

## Lifecycle and limitations

A Scripter parameter call can establish only:

- `requested` — the parameter ID and normalized value passed validation;
- `dispatched` — the fixed channel/CC/value was sent to the IAC endpoint;
- `unknown` — no approved acknowledgement proves that Logic routed it or Retro
  Synth applied it.

Scripter `Trace` output is diagnostic and throttled; it is not a reliable
acknowledgement channel. The fixed script intentionally provides no network,
filesystem, JSON-RPC, or subprocess behavior. It cannot control Audio 2, run
arbitrary JavaScript, or expose universal plug-in automation.

## Offline verification

The Node harness stubs Logic's MIDI event classes and proves mapping,
consumption, and pass-through without launching Logic:

```sh
node tests/js/scripter_harness.js
```

Syntax and hash/package checks belong in the normal distribution gate. Passing
the harness proves the fixed script's behavior in isolation, not live Logic
routing.

## Live proof and recovery

The Scripter proof is separate from the Audio 2 `.cst` proof. It must use a
disposable software-instrument track and the opt-in gates described in
[Live validation](LIVE_VALIDATION.md). The live test dispatches exactly one
selected parameter/value after an exact operator attestation and exact one-shot
dispatch phrase. It then requires the lifecycle to end `unknown`; it never claims
that an output message proves the Retro Synth control moved.

If a target does not move:

1. Stop sending messages; do not repeatedly sweep values.
2. Confirm the track is a software-instrument track and Scripter precedes Retro Synth.
3. Confirm the script is running and both target selectors are learned.
4. Confirm the IAC route reaches that strip on channel 16.
5. Check Controller Assignments and other MIDI processors for CC102/103 collisions.
6. Retest one target at a middle value and record the result as `unknown` unless
   independent operator observation is explicitly recorded.
