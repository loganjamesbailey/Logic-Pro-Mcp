// Read-only, fixed inspection of one configured standalone-Mixer strip. The
// program never activates Logic or invokes an Accessibility action. It emits
// one versioned marker followed by a bounded JSON payload for Python validation.

function run(argv) {
  "use strict";

  const MAX_MIXER_DEPTH = 3;
  const MAX_STRIPS = 256;
  const MAX_DIRECT_ELEMENTS = 64;
  const MAX_TARGET_EVIDENCE = 8;
  // The first resolveTarget() pass costs one selector read per strip (its
  // per-strip target-evidence filter), so up to MAX_STRIPS=256 strips alone
  // approaches this budget; sized with headroom for that pass, the second
  // (cheaper, O(1)) resolveTarget() call, and pluginEvidence()'s bounded
  // per-row scan of the matched strip.
  const MAX_SELECTOR_READS = 400;
  const MAX_PLUGINS = 32;
  const MAX_INPUT_LENGTH = 128;
  const MAX_PLUGIN_NAME_LENGTH = 128;
  const INSPECT_BUDGET_MS = 5000;
  const INSPECT_MARKER = "LOGIC_BRIDGE_INSPECT_V1:";

  const mixerIndex = Number(argv[0]);
  const expectedTargetName = argv[1] || "";
  const deadline = Date.now() + INSPECT_BUDGET_MS;
  let selectorReads = 0;

  function fail(code) {
    throw new Error("LOGIC_BRIDGE:PRE_DISPATCH:" + code);
  }

  function checkpoint() {
    if (Date.now() > deadline) fail("INSPECT_TIMEOUT");
  }

  function collection(operation, limit) {
    checkpoint();
    selectorReads += 1;
    if (selectorReads > MAX_SELECTOR_READS) fail("SELECTOR_READ_LIMIT");
    let values;
    try {
      values = operation();
    } catch (error) {
      fail("AX_READ_FAILED");
    }
    checkpoint();
    if (!values || typeof values.length !== "number") fail("AX_READ_FAILED");
    if (values.length > limit) fail("COLLECTION_LIMIT");
    return values;
  }

  function attributeValue(element, attributeName, required) {
    try {
      const attribute = element.attributes.byName(attributeName);
      if (!attribute.exists()) {
        if (required) fail("AX_READ_FAILED");
        return null;
      }
      return attribute.value();
    } catch (error) {
      fail("AX_READ_FAILED");
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
    if (!frame.every(Number.isFinite) || frame[2] <= 0 || frame[3] <= 0) {
      fail("NON_FINITE_FRAME");
    }
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
    if (mixers.length !== 1) fail("MIXER_WINDOW_NOT_FOUND");
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
    if (matches.length !== 1) fail("MIXER_AREA_NOT_FOUND");
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
        fail("TARGET_MISMATCH");
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
    if (direct.length !== 1) fail("SETTING_BUTTON_NOT_FOUND");
    return direct[0];
  }

  function resolveTarget(logic, expectedTarget) {
    const mixerWindow = findMixerWindow(logic);
    const mixerArea = findMixerArea(mixerWindow);
    const areaChildren = directChildren(mixerArea, MAX_STRIPS);
    const strips = areaChildren.filter(child => roleOf(child) === "AXLayoutItem");
    if (strips.length === 0 || strips.length > MAX_STRIPS) {
      fail("STRIP_COLLECTION_LIMIT");
    }
    const records = strips.map(strip => ({ strip, frame: frameOf(strip) }));
    const signatures = new Set(records.map(record => record.frame.join(":")));
    if (signatures.size !== records.length) fail("AMBIGUOUS_STRIP_FRAME");
    records.sort((left, right) => {
      const horizontal = left.frame[0] - right.frame[0];
      return horizontal || left.frame[1] - right.frame[1];
    });
    if (!records[mixerIndex]) fail("TARGET_MISMATCH");
    const indexed = records[mixerIndex];
    const indexedSignature = indexed.frame.join(":");
    if (expectedTarget === null) {
      const named = records.filter(
        record => directTargetEvidence(record.strip) > 0
      );
      if (named.length !== 1) fail("TARGET_MISMATCH");
      if (named[0].frame.join(":") !== indexedSignature) {
        fail("TARGET_MISMATCH");
      }
    } else {
      if (indexedSignature !== expectedTarget.stripSignature) {
        fail("TARGET_DRIFTED");
      }
      if (directTargetEvidence(indexed.strip) === 0) {
        fail("TARGET_DRIFTED");
      }
    }
    const settingButton = directSettingButton(indexed.strip);
    const target = {
      windowSignature: signatureOf(mixerWindow),
      strip: indexed.strip,
      stripSignature: indexedSignature,
      buttonSignature: signatureOf(settingButton)
    };
    if (
      expectedTarget !== null &&
      (target.windowSignature !== expectedTarget.windowSignature ||
        target.buttonSignature !== expectedTarget.buttonSignature)
    ) {
      fail("TARGET_DRIFTED");
    }
    return target;
  }

  function normalizedPluginName(value) {
    const normalized = value.trim();
    if (
      !normalized ||
      normalized.length > MAX_PLUGIN_NAME_LENGTH ||
      /[\u0000-\u001f\u007f]/.test(normalized)
    ) {
      return null;
    }
    return normalized;
  }

  function pluginEvidence(strip) {
    const plugins = [];
    let complete = true;
    for (const child of directChildren(strip, MAX_DIRECT_ELEMENTS)) {
      if (roleOf(child) !== "AXGroup") continue;
      const rowChildren = directChildren(child, MAX_DIRECT_ELEMENTS);
      let checkboxCount = 0;
      let buttonCount = 0;
      for (const rowChild of rowChildren) {
        const role = roleOf(rowChild);
        if (role === "AXCheckBox") checkboxCount += 1;
        if (role === "AXButton") buttonCount += 1;
      }
      if (checkboxCount < 1 || buttonCount < 2) continue;
      if (plugins.length >= MAX_PLUGINS) fail("PLUGIN_COLLECTION_LIMIT");
      const pluginName = normalizedPluginName(descriptionOf(child));
      if (pluginName === null) {
        complete = false;
        continue;
      }
      plugins.push(pluginName);
    }
    return { complete, plugins };
  }

  if (
    !Number.isInteger(mixerIndex) ||
    mixerIndex < 0 ||
    String(argv[0]).trim() === ""
  ) {
    // Number("") and Number("   ") both coerce to 0, which would otherwise
    // pass the checks above and silently target strip 0.
    fail("INVALID_ARGUMENTS");
  }
  if (
    !expectedTargetName ||
    expectedTargetName.length > MAX_INPUT_LENGTH ||
    /[\u0000-\u001f]/.test(expectedTargetName)
  ) {
    fail("INVALID_ARGUMENTS");
  }
  const systemEvents = Application("System Events");
  const logic = systemEvents.processes["Logic Pro"];
  let logicExists;
  try {
    logicExists = logic.exists();
  } catch (error) {
    fail("AX_READ_FAILED");
  }
  if (!logicExists) fail("LOGIC_NOT_RUNNING");
  const firstTarget = resolveTarget(logic, null);
  const currentTarget = resolveTarget(logic, firstTarget);
  const evidence = pluginEvidence(currentTarget.strip);
  checkpoint();
  return INSPECT_MARKER + JSON.stringify({
    protocol: 1,
    mixer_index: mixerIndex,
    target_matched: true,
    complete: evidence.complete,
    plugins: evidence.plugins
  });
}
