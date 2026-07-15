'use strict';

var NeedsTimingInfo = false;

var PluginParameters = [
  { name: 'Retro Synth Filter Cutoff', type: 'target' },
  { name: 'Filter Resonance', type: 'target' },
];

var LOGIC_BRIDGE_CHANNEL = 16;
var FILTER_CUTOFF_CC = 102;
var FILTER_RESONANCE_CC = 103;

function HandleMIDI(event) {
  if (!(event instanceof ControlChange) || event.channel !== LOGIC_BRIDGE_CHANNEL) {
    event.send();
    return;
  }

  var targetName;
  if (event.number === FILTER_CUTOFF_CC) {
    targetName = 'Retro Synth Filter Cutoff';
  } else if (event.number === FILTER_RESONANCE_CC) {
    targetName = 'Filter Resonance';
  } else {
    event.send();
    return;
  }

  var targetEvent = new TargetEvent();
  targetEvent.target = targetName;
  targetEvent.value = Math.max(0, Math.min(127, event.value)) / 127;
  targetEvent.send();
}
