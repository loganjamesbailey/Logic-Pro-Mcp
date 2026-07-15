"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "../..");
const SOURCE = fs.readFileSync(
  path.join(ROOT, "bridge_scripts/load_cst.js"),
  "utf8"
);
const FIXTURE = JSON.parse(
  fs.readFileSync(
    path.join(
      ROOT,
      "tests/fixtures/accessibility/logic-12-mixer-normalized.json"
    ),
    "utf8"
  )
);

class Attribute {
  constructor(value) {
    this.attributeValue = value;
  }

  exists() {
    return this.attributeValue !== undefined;
  }

  value() {
    return this.attributeValue;
  }
}

class Action {
  constructor(callback) {
    this.callback = callback;
  }

  exists() {
    return typeof this.callback === "function";
  }

  perform() {
    if (!this.exists()) throw new Error("missing action");
    this.callback();
  }
}

function queryValue(element, key) {
  if (key === "name") return element.title;
  if (key === "description") return element.description;
  if (key === "value") return element.value;
  return undefined;
}

function queryMatches(element, query) {
  if (Array.isArray(query._or)) {
    return query._or.some(candidate => queryMatches(element, candidate));
  }
  return Object.entries(query).every(
    ([key, expected]) => queryValue(element, key) === expected
  );
}

function collection(getter) {
  const result = () => getter().filter(element => element.exists());
  result.whose = query => () => result().filter(item => queryMatches(item, query));
  return result;
}

class Element {
  constructor({
    role,
    title = "",
    description = "",
    value = undefined,
    frame,
    parent = null,
    children = [],
    actions = {},
    onChildrenRead = null
  }) {
    this.role = role;
    this.title = title;
    this.description = description;
    this.value = value;
    this.frame = [...frame];
    this.parent = parent;
    this.children = children;
    this.present = true;
    this.onChildrenRead = onChildrenRead;
    this.actionCallbacks = actions;
    this.attributes = {
      byName: name => {
        const values = {
          AXRole: this.role,
          AXTitle: this.title,
          AXDescription: this.description,
          AXValue: this.value,
          AXParent: this.parent,
          AXPosition: [this.frame[0], this.frame[1]],
          AXSize: [this.frame[2], this.frame[3]]
        };
        return new Attribute(values[name]);
      }
    };
    this.actions = {
      byName: name => new Action(this.actionCallbacks[name])
    };
    this.uiElements = collection(() => {
      if (this.onChildrenRead) this.onChildrenRead();
      return this.children;
    });
    this.buttons = collection(() =>
      this.children.filter(child => child.role === "AXButton")
    );
  }

  exists() {
    return this.present;
  }
}

function attach(parent, ...children) {
  for (const child of children) child.parent = parent;
  parent.children.push(...children);
}

function buildWorld(options = {}) {
  const state = {
    settingPresses: 0,
    presetPresses: 0,
    cancelPresses: 0,
    driftApplied: false,
    pollCount: 0
  };
  const stripSpecs = options.stripSpecs || FIXTURE.strips;
  const window = new Element({
    ...FIXTURE.window,
    title: FIXTURE.window.title,
    frame: FIXTURE.window.frame
  });
  const mixerArea = new Element({
    ...FIXTURE.mixer_area,
    description: FIXTURE.mixer_area.description,
    frame: FIXTURE.mixer_area.frame
  });
  attach(window, mixerArea);

  let targetStrip;
  let pendingMenuAttach = null;
  for (const spec of stripSpecs) {
    const strip = new Element({role: "AXLayoutItem", frame: spec.frame});
    const name = new Element({
      role: "AXStaticText",
      title: spec.name,
      frame: [spec.frame[0] + 4, 110, 72, 18]
    });
    const setting = new Element({
      role: "AXButton",
      description: "setting",
      frame: [spec.frame[0] + 4, 760, 20, 20]
    });
    const attachMenus = () => {
      const menuCount = options.ambiguousPopup ? 2 : 1;
      for (let menuIndex = 0; menuIndex < menuCount; menuIndex += 1) {
        const menu = new Element({
          role: "AXMenu",
          frame: [spec.frame[0] + 4 + menuIndex, 500, 240, 300]
        });
        const presetItem = new Element({
          role: "AXMenuItem",
          title: "Jimmy Vocal Chain",
          frame: [spec.frame[0] + 8, 520, 220, 20]
        });
        presetItem.actionCallbacks.AXPress = () => {
          state.presetPresses += 1;
          if (options.throwPresetPress) throw new Error("simulated AX failure");
          menu.present = false;
          if (options.throwOnLogicExistsAfterPress) {
            logic.exists = () => {
              throw new Error("simulated post-press Apple Event failure");
            };
          }
        };
        menu.actionCallbacks.AXCancel = () => {
          state.cancelPresses += 1;
          menu.present = false;
        };
        attach(menu, presetItem);
        attach(setting, menu);
      }
    };
    setting.actionCallbacks.AXPress = () => {
      state.settingPresses += 1;
      if (options.popupAppearsAfterPolls !== undefined) {
        pendingMenuAttach = attachMenus;
      } else {
        attachMenus();
      }
    };
    attach(strip, name, setting);
    attach(mixerArea, strip);
    if (spec.index === 1) targetStrip = strip;
  }

  const logic = new Element({
    role: "AXApplication",
    frame: [0, 0, 2048, 900],
    children: [window]
  });
  window.parent = logic;
  if (options.extraWindowTitle) {
    const otherWindow = new Element({
      role: "AXWindow",
      title: options.extraWindowTitle,
      frame: [0, 0, 1024, 700]
    });
    attach(logic, otherWindow);
  }
  logic.windows = collection(() => logic.children.filter(child => child.role === "AXWindow"));
  if (options.driftOnFirstTreeRead) {
    logic.onChildrenRead = () => {
      if (!state.driftApplied) {
        state.driftApplied = true;
        targetStrip.frame[0] += 1;
      }
    };
  }
  const onDelay = () => {
    state.pollCount += 1;
    if (
      pendingMenuAttach &&
      options.popupAppearsAfterPolls !== undefined &&
      state.pollCount >= options.popupAppearsAfterPolls
    ) {
      const attachMenus = pendingMenuAttach;
      pendingMenuAttach = null;
      attachMenus();
    }
  };
  return {logic, state, onDelay};
}

function execute(options = {}) {
  const world = buildWorld(options);
  const systemEvents = {processes: {"Logic Pro": world.logic}};
  const context = vm.createContext({
    Application: name => {
      assert.equal(name, "System Events");
      return systemEvents;
    },
    delay: () => world.onDelay()
  });
  vm.runInContext(SOURCE, context, {filename: "load_cst.js"});
  try {
    const output = context.run(
      options.argv || ["Jimmy Vocal Chain", "1", "Audio 2"]
    );
    return {output, state: world.state, error: null};
  } catch (error) {
    return {output: null, state: world.state, error: String(error.message)};
  }
}

assert.ok(FIXTURE.strips.length >= 21, "fixture must model a large Mixer");
assert.equal(FIXTURE.strips[1].name, "Audio 2");

const success = execute();
assert.deepEqual(JSON.parse(success.output), {
  status: "observed",
  evidence: "preset_menu_item_pressed_and_popup_dismissed"
});
assert.equal(success.error, null);
assert.equal(success.state.settingPresses, 1);
assert.equal(success.state.presetPresses, 1);

const duplicateSpecs = FIXTURE.strips.map(spec => ({...spec}));
duplicateSpecs[12].name = "Audio 2";
const duplicate = execute({stripSpecs: duplicateSpecs});
assert.match(duplicate.error, /LOGIC_BRIDGE:PRE_DISPATCH:TARGET_MISMATCH/);
assert.equal(duplicate.state.settingPresses, 0);
assert.equal(duplicate.state.presetPresses, 0);

const drift = execute({driftOnFirstTreeRead: true});
assert.match(drift.error, /LOGIC_BRIDGE:PRE_DISPATCH:TARGET_DRIFTED/);
assert.equal(drift.state.settingPresses, 0);
assert.equal(drift.state.presetPresses, 0);

const maxStripSpecs = Array.from({length: 256}, (_unused, index) => ({
  index,
  name: index === 1 ? "Audio 2" : `Strip ${index}`,
  frame: [index * 80, 100, 80, 700]
}));
const maxStrips = execute({stripSpecs: maxStripSpecs});
assert.deepEqual(JSON.parse(maxStrips.output), {
  status: "observed",
  evidence: "preset_menu_item_pressed_and_popup_dismissed"
});
assert.equal(maxStrips.error, null);
assert.equal(maxStrips.state.settingPresses, 1);
assert.equal(maxStrips.state.presetPresses, 1);

const delayedPopup = execute({popupAppearsAfterPolls: 5});
assert.deepEqual(JSON.parse(delayedPopup.output), {
  status: "observed",
  evidence: "preset_menu_item_pressed_and_popup_dismissed"
});
assert.equal(delayedPopup.error, null);
assert.ok(
  delayedPopup.state.pollCount >= 5,
  "popup must be found only after several poll iterations"
);

const stalledPopup = execute({popupAppearsAfterPolls: Infinity});
assert.match(
  stalledPopup.error,
  /LOGIC_BRIDGE:PRE_DISPATCH:SELECTOR_READ_LIMIT/
);
assert.equal(stalledPopup.state.settingPresses, 1);
assert.equal(stalledPopup.state.presetPresses, 0);

const popup = execute({ambiguousPopup: true});
assert.match(popup.error, /LOGIC_BRIDGE:PRE_DISPATCH:SETTING_POPUP_NOT_FOUND/);
assert.equal(popup.state.settingPresses, 1);
assert.equal(popup.state.presetPresses, 0);

const postDispatch = execute({throwPresetPress: true});
assert.match(
  postDispatch.error,
  /LOGIC_BRIDGE:POST_DISPATCH:PRESET_AUTOMATION_FAILED/
);
assert.equal(postDispatch.state.settingPresses, 1);
assert.equal(postDispatch.state.presetPresses, 1);

const postPressCheckFailure = execute({throwOnLogicExistsAfterPress: true});
assert.match(
  postPressCheckFailure.error,
  /LOGIC_BRIDGE:POST_DISPATCH:PRESET_AUTOMATION_FAILED/
);
assert.equal(postPressCheckFailure.state.settingPresses, 1);
assert.equal(postPressCheckFailure.state.presetPresses, 1);

const blankIndex = execute({argv: ["Jimmy Vocal Chain", "", "Audio 2"]});
assert.match(blankIndex.error, /LOGIC_BRIDGE:PRE_DISPATCH:INVALID_ARGUMENTS/);
assert.equal(blankIndex.state.settingPresses, 0);
assert.equal(blankIndex.state.presetPresses, 0);

const namedLikeMixer = execute({extraWindowTitle: "Dub Mixer"});
assert.deepEqual(JSON.parse(namedLikeMixer.output), {
  status: "observed",
  evidence: "preset_menu_item_pressed_and_popup_dismissed"
});
assert.equal(namedLikeMixer.error, null);

process.stdout.write("JXA Accessibility harness: 11 scenarios passed\n");
