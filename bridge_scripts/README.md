# Fixed Logic Pro Accessibility resources

These packaged JXA programs are the bridge's complete Accessibility execution
surface. The Python daemon invokes them only after authentication, opaque-ID
allowlist resolution, confirmation where required, and local policy checks. No
script accepts a filesystem path, Accessibility selector, source program,
coordinate, shell command, or menu path from an RPC or MCP caller.

At loader construction, Python opens each fixed resource with `O_NOFOLLOW`,
validates its descriptor identity, owner, mode, bounded size, and strict UTF-8,
and compares its bytes with a source-specific pinned SHA-256. Those immutable
verified bytes are cached and supplied to `osascript -l JavaScript -` over
standard input. `osascript` is never given a validated script pathname to reopen,
so a later path replacement cannot change the program executed by that loader.

## Resources

- `load_cst.js` accepts an allowlisted preset basename, a configured zero-based
  Mixer index, and the configured exact Accessibility target name. It performs
  the two attributable actions required to open the Setting menu and choose the
  exact preset item.
- `cleanup_cst.js` accepts the same three fixed values. It is an independent,
  best-effort recovery path that can only cancel one still-open popup after
  re-resolving the exact target and proving that the popup contains the exact
  preset. Every uncertainty returns `SKIPPED`.
- `inspect_mixer.js` accepts only a configured zero-based Mixer index and exact
  target name. It is read-only: it does not activate Logic, invoke an
  Accessibility action, or click anything. Its only successful output is the
  `LOGIC_BRIDGE_INSPECT_V1:` marker followed by bounded JSON.

The daemon maps configured names and indexes to opaque public IDs. Script
arguments and raw script output are internal implementation details and are not
RPC parameters or public evidence.

## Bounded Mixer contract

The resolver is pinned to the standalone-Mixer hierarchy observed in Logic Pro
12.3:

~~~text
one AXWindow with a standalone Mixer title
  -> one AXLayoutArea described as Mixer within three container levels
    -> at most 256 direct AXLayoutItem channel strips
      -> exact direct target-name evidence
      -> one direct AXButton described as setting
~~~

Channel strips are ordered by finite on-screen position. One bounded OR query per
strip checks title, description, or value evidence. The resolver performs that
global uniqueness pass exactly once: exactly one strip must match, and it must
equal the configured index. Subsequent resolutions query only that indexed strip
and compare the Mixer-window, strip, and Setting-button signatures captured by
the first pass. Multiple raw name nodes inside the one matching strip are allowed,
up to the eight-evidence cap; a second matching strip is always ambiguous.
Missing, stale, duplicate, non-finite, or disagreeing evidence fails closed. No
coordinate action is permitted.

Protocol v1 enforces these hard caps in every resource:

- Mixer container depth: 3
- direct strips: 256
- direct elements or buttons in one collection: 64
- target-name evidence per strip: 8
- load pre-action phase: 5 seconds
- popup and exact-item phase: 8 seconds
- popup dismissal or independent cleanup: 3 seconds
- inspector phase: 5 seconds

Selector-critical collection reads are bounded, but not by one shared total: a
single global counter would let target resolution or popup polling starve
whatever budget popup dismissal has left, in exactly the case where dismissal
matters most. Each resource ceiling is sized from that resource's own
worst-case read count instead:

- `load_cst.js` tracks reads per phase -- pre-press target resolution: 400,
  popup wait and exact-item scan: 1200, popup dismissal (reserved,
  untouched by the other two phases): 60.
- `inspect_mixer.js` has one phase and one ceiling: 400.
- `cleanup_cst.js` has one phase and one ceiling: 128.

The wall-clock checkpoints do not claim to interrupt one blocked System Events
read. Python retains the outer process-group deadline and reaping obligation.
Increasing that outer deadline is not a substitute for widening these selector
budgets.

## Mutation and popup attribution

The loader snapshots bounded popup state before the first action, re-resolves
the strip and Setting button, then invokes the button exactly once. It accepts
only one newly exposed menu whose Accessibility parent chain reaches that exact
button. Before choosing the preset it re-resolves the target, re-resolves that
same popup, and requires exactly one exact preset-name item at the top level or
one submenu level. There is no blind retry.

If the loader has identified an attributable popup but has not yet chosen the
preset, an error triggers one constrained cancellation attempt. After preset
selection, cleanup does not claim that cancellation can undo a possibly applied
channel-strip change. The standalone cleanup program independently repeats the
target, popup, ownership, and preset checks; it never sends a global keystroke.

## Read-only inspection payload

The inspector validates the exact strip twice and then examines at most 64
direct strip elements. An occupied plug-in row is recognized structurally as a
direct `AXGroup` with a checkbox and at least two buttons. It emits at most 32
trimmed plug-in descriptions of at most 128 characters each. An occupied row
with an unreadable or unsafe label sets `complete` to `false` instead of being
treated as empty.

Example internal output:

~~~text
LOGIC_BRIDGE_INSPECT_V1:{"protocol":1,"mixer_index":1,"target_matched":true,"complete":true,"plugins":["Channel EQ","Compressor"]}
~~~

Python must require the marker, exact key set, protocol version, configured
index, `target_matched: true`, bounded list sizes, valid strings, and no trailing
output before mapping this evidence to an opaque target ID. A complete expected
plug-in signature can support only scoped Mixer-signature verification. It does
not prove `.cst` byte equivalence, hidden plug-in state, routing, project save
state, or complete Logic source state.

## Failure and lifecycle boundary

Mutation failures use stable
`LOGIC_BRIDGE:<PRE_DISPATCH|POST_DISPATCH>:<CODE>` tokens. The phase changes to
`POST_DISPATCH` immediately before the exact preset menu item's destructive
`AXPress` begins. Target drift, Setting-button press failure, popup ambiguity,
and preset lookup failure remain requested-only; a preset-press exception is
post-dispatch and ambiguous. Python maps the token to path-free public errors and
discards raw `osascript` diagnostics. An outer process timeout remains
conservatively post-dispatch because termination cannot prove which JXA statement
ran. Popup disappearance proves only an observed UI transition; it is never
silently promoted to verified state.

Default tests compile and inspect the fixed resources but never drive Logic. The
normalized Logic 12 fixture contains 24 strips and a Node harness executes the
real loader against it, covering exact Audio 2 selection, duplicate identity,
frame drift, selector-budget exhaustion, popup ambiguity, and a failing
destructive press. Any live mutation must remain opt-in, target-bound,
disposable-project gated, and separately confirmed by the daemon contract.
