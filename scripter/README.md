# Logic Bridge Scripter protocol v1

`LogicBridgeTargets.js` is the fixed Logic Pro Scripter asset for two learned
Retro Synth parameters on a software-instrument channel strip. It is an
additive MIDI-FX path; it does not control audio channel strips such as Audio 2.

## Fixed mapping

| Incoming MIDI | Learned Scripter target | TargetEvent value |
| --- | --- | --- |
| Channel 16, CC102 | Target 1: Retro Synth Filter Cutoff | CC value divided by 127 |
| Channel 16, CC103 | Target 2: Filter Resonance | CC value divided by 127 |

The two mapped controls are consumed after emitting one `TargetEvent`. Every
other MIDI event, including either control on another channel, is forwarded
unchanged exactly once.

## Install and configure in Logic Pro

1. Use a disposable software-instrument channel strip and insert Retro Synth.
2. Insert Scripter in a MIDI FX slot before Retro Synth.
3. Open `LogicBridgeTargets.js`, copy the complete reviewed contents into
   Scripter, and run the script.
4. In Scripter, assign the first target selector, **Retro Synth Filter Cutoff**,
   to Retro Synth's filter cutoff parameter.
5. Assign the second target selector, **Filter Resonance**, to Retro Synth's
   filter resonance parameter.
6. Route the dedicated IAC input to this software-instrument strip and keep the
   bridge route on MIDI channel 16. Confirm CC102 and CC103 are not used by a
   Controller Assignment or another route.
7. Save the Scripter setting or the disposable project/template only after both
   targets are assigned as intended.

## Safety and evidence boundary

The script is fixed source. It has no runtime code evaluation, dynamic source
loading, network, filesystem, RPC, or subprocess path. Callers select neither
JavaScript nor target names at runtime.

This protocol is output-only. Emitting a `TargetEvent` shows only that Scripter
dispatched a normalized value to the configured learned target; it is not an
acknowledgement or readback from Retro Synth. The script intentionally emits no
`Trace` acknowledgement. Any validation record must describe independent
operator observation and must not promote dispatch alone to verified state.

Run the offline behavior harness from the repository root:

```sh
node tests/js/scripter_harness.js
```
