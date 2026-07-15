'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const scriptPath = path.resolve(
  __dirname,
  '../../scripter/LogicBridgeTargets.js',
);
const source = fs.readFileSync(scriptPath, 'utf8');

const forbiddenPatterns = [
  ['eval', /\beval\s*\(/],
  ['dynamic Function', /\bFunction\s*\(/],
  ['module or source loading', /\b(?:require|importScripts|load)\s*\(/],
  ['ES module import', /(?:^|\n)\s*import\s/m],
  ['network API', /\b(?:fetch|XMLHttpRequest|WebSocket)\b/],
  ['filesystem API', /\b(?:File|FileHandle|readFile|writeFile)\b/],
  ['subprocess API', /\b(?:child_process|spawn|execFile|execSync)\b/],
  ['RPC API', /\b(?:rpc|jsonrpc)\b/i],
  ['Trace acknowledgement', /\bTrace\s*\(/],
];

for (const [name, pattern] of forbiddenPatterns) {
  assert.equal(pattern.test(source), false, `fixed script contains ${name}`);
}

const forwardedEvents = [];
const sentTargetEvents = [];

class MidiEvent {
  constructor(properties = {}) {
    Object.assign(this, properties);
    this.sendCount = 0;
  }

  send() {
    this.sendCount += 1;
    forwardedEvents.push(this);
  }
}

class ControlChange extends MidiEvent {}
class NoteOn extends MidiEvent {}
class PitchBend extends MidiEvent {}

class TargetEvent {
  constructor() {
    this.target = null;
    this.value = null;
    this.sendCount = 0;
  }

  send() {
    this.sendCount += 1;
    sentTargetEvents.push(this);
  }
}

const context = vm.createContext({
  ControlChange,
  NoteOn,
  PitchBend,
  TargetEvent,
});
vm.runInContext(source, context, { filename: scriptPath });

assert.equal(typeof context.HandleMIDI, 'function');
assert.deepEqual(
  Array.from(context.PluginParameters, ({ name, type }) => ({ name, type })),
  [
    { name: 'Retro Synth Filter Cutoff', type: 'target' },
    { name: 'Filter Resonance', type: 'target' },
  ],
  'protocol v1 must expose exactly two learned target slots',
);

function dispatch(event) {
  forwardedEvents.length = 0;
  sentTargetEvents.length = 0;
  context.HandleMIDI(event);
  return {
    forwarded: [...forwardedEvents],
    targets: [...sentTargetEvents],
  };
}

function assertMappedControl({ number, value, target, normalized }) {
  const control = new ControlChange({ channel: 16, number, value });
  const result = dispatch(control);

  assert.equal(control.sendCount, 0, `CC${number} must be consumed`);
  assert.equal(result.forwarded.length, 0, `CC${number} must not be forwarded`);
  assert.equal(result.targets.length, 1, `CC${number} must emit one TargetEvent`);
  assert.equal(result.targets[0].sendCount, 1);
  assert.equal(result.targets[0].target, target);
  assert.equal(result.targets[0].value, normalized);
}

assertMappedControl({
  number: 102,
  value: 0,
  target: 'Retro Synth Filter Cutoff',
  normalized: 0,
});
assertMappedControl({
  number: 102,
  value: 64,
  target: 'Retro Synth Filter Cutoff',
  normalized: 64 / 127,
});
assertMappedControl({
  number: 103,
  value: 127,
  target: 'Filter Resonance',
  normalized: 1,
});

for (const event of [
  new ControlChange({ channel: 16, number: 104, value: 72 }),
  new ControlChange({ channel: 15, number: 102, value: 72 }),
  new NoteOn({ channel: 16, pitch: 60, velocity: 100 }),
  new PitchBend({ channel: 16, value: 4096 }),
]) {
  const result = dispatch(event);
  assert.equal(event.sendCount, 1, 'unrelated MIDI must be forwarded once');
  assert.deepEqual(result.forwarded, [event]);
  assert.equal(result.targets.length, 0);
}

console.log('Scripter harness: all assertions passed');
