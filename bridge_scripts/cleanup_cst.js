// Best-effort recovery for one interrupted preset interaction. This fixed
// helper cancels only one popup that is re-attributed to the exact configured
// Mixer strip, Setting button, and preset. Every uncertainty returns SKIPPED.

function run(argv) {
  "use strict";

  const MAX_MIXER_DEPTH = 3;
  const MAX_STRIPS = 256;
  const MAX_DIRECT_ELEMENTS = 64;
  const MAX_TARGET_EVIDENCE = 8;
  const MAX_SELECTOR_READS = 128;
  const MAX_POPUP_DEPTH = 6;
  const MAX_INPUT_LENGTH = 128;
  const DISMISS_BUDGET_MS = 3000;

  const presetName = argv[0] || "";
  const mixerIndex = Number(argv[1]);
  const expectedTargetName = argv[2] || "";
  const deadline = Date.now() + DISMISS_BUDGET_MS;
  let selectorReads = 0;

  function abort() {
    throw new Error("SKIPPED");
  }

  function checkpoint() {
    if (Date.now() > deadline) abort();
  }

  function collection(operation, limit) {
    checkpoint();
    selectorReads += 1;
    if (selectorReads > MAX_SELECTOR_READS) abort();
    let values;
    try {
      values = operation();
    } catch (error) {
      abort();
    }
    checkpoint();
    if (!values || typeof values.length !== "number" || values.length > limit) {
      abort();
    }
    return values;
  }

  function attributeValue(element, attributeName, required) {
    try {
      const attribute = element.attributes.byName(attributeName);
      if (!attribute.exists()) {
        if (required) abort();
        return null;
      }
      return attribute.value();
    } catch (error) {
      abort();
    }
  }

  function roleOf(element) {
    return String(attributeValue(element, "AXRole", true) || "");
  }

  function nameOf(element) {
    return String(attributeValue(element, "AXTitle", false) || "");
  }

  function descriptionOf(element) {
    return String(attributeValue(element, "AXDescription", false) || "");
  }

  function parentOf(element) {
    return attributeValue(element, "AXParent", false);
  }

  function frameOf(element) {
    const position = attributeValue(element, "AXPosition", true);
    const size = attributeValue(element, "AXSize", true);
    const frame = [
      Number(position[0]),
      Number(position[1]),
      Number(size[0]),
      Number(size[1])
    ];
    if (!frame.every(Number.isFinite) || frame[2] <= 0 || frame[3] <= 0) abort();
    return frame;
  }

  function signatureOf(element) {
    return frameOf(element).join(":");
  }

  function sameElementFrame(left, right) {
    return signatureOf(left) === signatureOf(right);
  }

  function directChildren(element, limit) {
    return collection(() => element.uiElements(), limit);
  }

  function filteredDirect(element, query, limit) {
    return collection(() => element.uiElements.whose(query)(), limit);
  }

  function isMixerTitle(value) {
    // Anchored to the start: a project literally named e.g. "Dub Mixer"
    // must not match its own main window as the standalone Mixer.
    return /^Mixer(?::.*)?$/.test(value);
  }

  function findMixerWindow(logic) {
    const windows = collection(() => logic.windows(), MAX_DIRECT_ELEMENTS);
    const mixers = windows.filter(window => isMixerTitle(nameOf(window)));
    if (mixers.length !== 1) abort();
    return mixers[0];
  }

  function isMixerContainer(role) {
    return (
      role === "AXGroup" ||
      role === "AXScrollArea" ||
      role === "AXSplitGroup" ||
      role === "AXLayoutArea"
    );
  }

  function findMixerArea(window) {
    const matches = [];
    function visit(element, depth) {
      checkpoint();
      if (depth >= MAX_MIXER_DEPTH) return;
      for (const child of directChildren(element, MAX_DIRECT_ELEMENTS)) {
        const role = roleOf(child);
        if (
          role === "AXLayoutArea" &&
          descriptionOf(child).trim().toLowerCase() === "mixer"
        ) {
          matches.push(child);
          continue;
        }
        if (isMixerContainer(role)) visit(child, depth + 1);
      }
    }
    visit(window, 0);
    if (matches.length !== 1) abort();
    return matches[0];
  }

  function directTargetEvidence(strip) {
    const matches = filteredDirect(
      strip,
      {
        _or: [
          { name: expectedTargetName },
          { description: expectedTargetName },
          { value: expectedTargetName }
        ]
      },
      MAX_TARGET_EVIDENCE
    );
    for (const match of matches) {
      const parent = parentOf(match);
      if (
        !parent ||
        roleOf(parent) !== "AXLayoutItem" ||
        !sameElementFrame(parent, strip)
      ) {
        abort();
      }
    }
    return matches.length;
  }

  function directSettingButton(strip) {
    const buttons = collection(
      () => strip.buttons.whose({ description: "setting" })(),
      MAX_DIRECT_ELEMENTS
    );
    const direct = [];
    for (const button of buttons) {
      const parent = parentOf(button);
      if (
        roleOf(button) === "AXButton" &&
        parent &&
        roleOf(parent) === "AXLayoutItem" &&
        sameElementFrame(parent, strip)
      ) {
        direct.push(button);
      }
    }
    if (direct.length !== 1) abort();
    return direct[0];
  }

  function resolveTarget(logic, expectedTarget) {
    const mixerWindow = findMixerWindow(logic);
    const mixerArea = findMixerArea(mixerWindow);
    const areaChildren = directChildren(mixerArea, MAX_STRIPS);
    const strips = areaChildren.filter(child => roleOf(child) === "AXLayoutItem");
    if (strips.length === 0 || strips.length > MAX_STRIPS) abort();
    const records = strips.map(strip => ({ strip, frame: frameOf(strip) }));
    const signatures = new Set(records.map(record => record.frame.join(":")));
    if (signatures.size !== records.length) abort();
    records.sort((left, right) => {
      const horizontal = left.frame[0] - right.frame[0];
      return horizontal || left.frame[1] - right.frame[1];
    });
    if (!records[mixerIndex]) abort();
    const indexed = records[mixerIndex];
    const indexedSignature = indexed.frame.join(":");
    if (expectedTarget === null) {
      const named = records.filter(
        record => directTargetEvidence(record.strip) > 0
      );
      if (named.length !== 1) abort();
      if (named[0].frame.join(":") !== indexedSignature) abort();
    } else {
      if (indexedSignature !== expectedTarget.stripSignature) abort();
      if (directTargetEvidence(indexed.strip) === 0) abort();
    }
    const settingButton = directSettingButton(indexed.strip);
    const target = {
      mixerWindow,
      windowSignature: signatureOf(mixerWindow),
      stripSignature: indexedSignature,
      settingButton,
      buttonSignature: signatureOf(settingButton)
    };
    if (
      expectedTarget !== null &&
      (target.windowSignature !== expectedTarget.windowSignature ||
        target.buttonSignature !== expectedTarget.buttonSignature)
    ) {
      abort();
    }
    return target;
  }

  function targetBranchMenus(logic, target) {
    const matches = [];
    function visit(element, depth, inTargetBranch) {
      checkpoint();
      if (depth > MAX_POPUP_DEPTH) return;
      for (const child of directChildren(element, MAX_DIRECT_ELEMENTS)) {
        const role = roleOf(child);
        if (role === "AXMenuBar") continue;
        if (role === "AXMenu") {
          frameOf(child);
          matches.push(child);
          continue;
        }
        if (role === "AXWindow") {
          if (sameElementFrame(child, target.mixerWindow)) {
            visit(child, depth + 1, false);
          }
          continue;
        }
        if (role === "AXLayoutItem") {
          if (signatureOf(child) === target.stripSignature) {
            visit(child, depth + 1, true);
          }
          continue;
        }
        if (role === "AXButton") {
          if (signatureOf(child) === target.buttonSignature) {
            visit(child, depth + 1, true);
          }
          continue;
        }
        if (inTargetBranch || isMixerContainer(role)) {
          visit(child, depth + 1, inTargetBranch);
        }
      }
    }
    visit(logic, 0, false);
    return matches;
  }

  function menuIsOwnedByButton(menu, button) {
    let ancestor = parentOf(menu);
    for (let depth = 0; depth <= MAX_POPUP_DEPTH && ancestor; depth += 1) {
      checkpoint();
      if (
        roleOf(ancestor) === "AXButton" &&
        sameElementFrame(ancestor, button)
      ) {
        return true;
      }
      ancestor = parentOf(ancestor);
    }
    return false;
  }

  function menuContainsPreset(menu) {
    for (const item of directChildren(menu, MAX_DIRECT_ELEMENTS)) {
      if (nameOf(item) === presetName) return true;
      const submenus = directChildren(item, MAX_DIRECT_ELEMENTS).filter(
        child => roleOf(child) === "AXMenu"
      );
      for (const submenu of submenus) {
        for (const nestedItem of directChildren(submenu, MAX_DIRECT_ELEMENTS)) {
          if (nameOf(nestedItem) === presetName) return true;
        }
      }
    }
    return false;
  }

  if (
    presetName.length > MAX_INPUT_LENGTH ||
    !/^[^\/:\u0000-\u001f]+$/.test(presetName)
  ) {
    return "SKIPPED";
  }
  if (
    !Number.isInteger(mixerIndex) ||
    mixerIndex < 0 ||
    String(argv[1]).trim() === ""
  ) {
    // Number("") and Number("   ") both coerce to 0, which would otherwise
    // pass the checks above and silently target strip 0.
    return "SKIPPED";
  }
  if (
    !expectedTargetName ||
    expectedTargetName.length > MAX_INPUT_LENGTH ||
    /[\u0000-\u001f]/.test(expectedTargetName)
  ) {
    return "SKIPPED";
  }

  try {
    const systemEvents = Application("System Events");
    const logic = systemEvents.processes["Logic Pro"];
    if (!logic.exists()) return "SKIPPED";
    const firstTarget = resolveTarget(logic, null);
    const firstCandidates = targetBranchMenus(logic, firstTarget).filter(
      menu =>
        menuIsOwnedByButton(menu, firstTarget.settingButton) &&
        menuContainsPreset(menu)
    );
    if (firstCandidates.length !== 1) return "SKIPPED";
    const popupSignature = signatureOf(firstCandidates[0]);

    const currentTarget = resolveTarget(logic, firstTarget);
    const candidates = targetBranchMenus(logic, currentTarget).filter(
      menu =>
        signatureOf(menu) === popupSignature &&
        menuIsOwnedByButton(menu, currentTarget.settingButton) &&
        menuContainsPreset(menu)
    );
    if (candidates.length !== 1 || !candidates[0].exists()) return "SKIPPED";
    checkpoint();
    const cancelAction = candidates[0].actions.byName("AXCancel");
    if (!cancelAction.exists()) return "SKIPPED";
    cancelAction.perform();
    while (true) {
      checkpoint();
      if (candidates[0].exists() === false) return "CLEANED";
      delay(0.05);
    }
  } catch (error) {
    return "SKIPPED";
  }
}
