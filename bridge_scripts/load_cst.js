// Load one allowlisted Track channel-strip setting onto one configured Mixer strip.
// Inputs are fixed to: preset basename, zero-based Mixer index, and exact target
// name. The program accepts no path, selector, source text, or menu path.

function run(argv) {
  "use strict";

  const MAX_MIXER_DEPTH = 3;
  const MAX_STRIPS = 256;
  const MAX_DIRECT_ELEMENTS = 64;
  const MAX_TARGET_EVIDENCE = 8;
  const MAX_POPUP_DEPTH = 6;
  const MAX_INPUT_LENGTH = 128;
  const PREPRESS_BUDGET_MS = 5000;
  const POPUP_ITEM_BUDGET_MS = 8000;
  const DISMISS_BUDGET_MS = 3000;

  // Selector-read budgets are tracked per phase (keyed by the same budgetCode
  // used for wall-clock checkpoints), not as one shared counter. A single
  // global counter lets the target-resolution and popup-wait phases starve
  // the dismissal phase of the reads it needs to attempt a clean AXCancel --
  // exactly the case where cleanup matters most. Each phase's ceiling is
  // sized from its own worst-case AX read count, not an arbitrary shared
  // total:
  //   PREPRESS_TIMEOUT: one full resolveTarget() pass against up to
  //     MAX_STRIPS=256 strips costs one selector read per strip (the
  //     per-strip target-evidence filter) plus a small constant for window
  //     and mixer-area traversal; the phase runs that pass once plus two
  //     cheaper O(1) resolveTarget()/targetBranchMenus() calls.
  //   POPUP_ITEM_TIMEOUT: the popup-wait loop polls every 100ms for up to
  //     POPUP_ITEM_BUDGET_MS, and each poll re-walks the target's menu
  //     branch; budgeted for the full wall-clock window at several reads per
  //     poll, plus the post-found target/menu/item re-scan.
  //   DISMISS_TIMEOUT: reserved and untouched by the other two phases so a
  //     failure elsewhere in the script always leaves cleanup enough budget
  //     to re-resolve the target and cancel the open Setting popup.
  const SELECTOR_READ_BUDGETS = {
    PREPRESS_TIMEOUT: 400,
    POPUP_ITEM_TIMEOUT: 1200,
    DISMISS_TIMEOUT: 60
  };

  const presetName = argv[0] || "";
  const mixerIndex = Number(argv[1]);
  const expectedTargetName = argv[2] || "";
  const prepressDeadline = Date.now() + PREPRESS_BUDGET_MS;
  const selectorReadsByPhase = Object.create(null);
  let presetItemPressStarted = false;

  function fail(code) {
    const phase = presetItemPressStarted ? "POST_DISPATCH" : "PRE_DISPATCH";
    throw new Error("LOGIC_BRIDGE:" + phase + ":" + code);
  }

  function checkpoint(deadline, code) {
    if (Date.now() > deadline) fail(code);
  }

  function collection(operation, limit, deadline, budgetCode) {
    checkpoint(deadline, budgetCode);
    const reads = (selectorReadsByPhase[budgetCode] || 0) + 1;
    selectorReadsByPhase[budgetCode] = reads;
    if (reads > SELECTOR_READ_BUDGETS[budgetCode]) fail("SELECTOR_READ_LIMIT");
    let values;
    try {
      values = operation();
    } catch (error) {
      fail("AX_READ_FAILED");
    }
    checkpoint(deadline, budgetCode);
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

  function directChildren(element, limit, deadline, budgetCode) {
    return collection(
      () => element.uiElements(),
      limit,
      deadline,
      budgetCode
    );
  }

  function filteredDirect(element, query, limit, deadline, budgetCode) {
    return collection(
      () => element.uiElements.whose(query)(),
      limit,
      deadline,
      budgetCode
    );
  }

  function isMixerTitle(value) {
    // Anchored to the start: a project literally named e.g. "Dub Mixer"
    // must not match its own main window as the standalone Mixer.
    return /^Mixer(?::.*)?$/.test(value);
  }

  function findMixerWindow(logic, deadline, budgetCode) {
    const windows = collection(
      () => logic.windows(),
      MAX_DIRECT_ELEMENTS,
      deadline,
      budgetCode
    );
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

  function findMixerArea(window, deadline, budgetCode) {
    const matches = [];
    function visit(element, depth) {
      checkpoint(deadline, budgetCode);
      if (depth >= MAX_MIXER_DEPTH) return;
      const children = directChildren(
        element,
        MAX_DIRECT_ELEMENTS,
        deadline,
        budgetCode
      );
      for (const child of children) {
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

  function directTargetEvidence(strip, deadline, budgetCode) {
    const matches = filteredDirect(
      strip,
      {
        _or: [
          { name: expectedTargetName },
          { description: expectedTargetName },
          { value: expectedTargetName }
        ]
      },
      MAX_TARGET_EVIDENCE,
      deadline,
      budgetCode
    );
    for (const match of matches) {
      const parent = parentOf(match);
      if (!parent || roleOf(parent) !== "AXLayoutItem") {
        fail("TARGET_MISMATCH");
      }
      if (!sameElementFrame(parent, strip)) fail("TARGET_MISMATCH");
    }
    return matches.length;
  }

  function directSettingButton(strip, deadline, budgetCode) {
    const buttons = collection(
      () => strip.buttons.whose({ description: "setting" })(),
      MAX_DIRECT_ELEMENTS,
      deadline,
      budgetCode
    );
    const direct = [];
    for (const button of buttons) {
      if (roleOf(button) !== "AXButton") continue;
      const parent = parentOf(button);
      if (
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

  function resolveTarget(logic, deadline, budgetCode, expectedTarget) {
    const mixerWindow = findMixerWindow(logic, deadline, budgetCode);
    const mixerArea = findMixerArea(mixerWindow, deadline, budgetCode);
    const areaChildren = directChildren(
      mixerArea,
      MAX_STRIPS,
      deadline,
      budgetCode
    );
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
        record => directTargetEvidence(record.strip, deadline, budgetCode) > 0
      );
      if (named.length !== 1) fail("TARGET_MISMATCH");
      if (named[0].frame.join(":") !== indexedSignature) {
        fail("TARGET_MISMATCH");
      }
    } else {
      if (indexedSignature !== expectedTarget.stripSignature) {
        fail("TARGET_DRIFTED");
      }
      if (directTargetEvidence(indexed.strip, deadline, budgetCode) === 0) {
        fail("TARGET_DRIFTED");
      }
    }
    const settingButton = directSettingButton(
      indexed.strip,
      deadline,
      budgetCode
    );
    const target = {
      mixerWindow,
      strip: indexed.strip,
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
      fail("TARGET_DRIFTED");
    }
    return target;
  }

  function targetBranchMenus(logic, target, deadline, budgetCode) {
    const matches = [];
    function visit(element, depth, inTargetBranch) {
      checkpoint(deadline, budgetCode);
      if (depth > MAX_POPUP_DEPTH) return;
      // The mixer area is itself a visited container here and can legally
      // have up to MAX_STRIPS direct children (one per strip); capping this
      // walk at MAX_DIRECT_ELEMENTS would make any mixer with more than 64
      // strips fail closed well under the documented MAX_STRIPS contract.
      const children = directChildren(
        element,
        MAX_STRIPS,
        deadline,
        budgetCode
      );
      for (const child of children) {
        const role = roleOf(child);
        if (role === "AXMenuBar") continue;
        if (role === "AXMenu") {
          // Keep duplicate nodes. Collapsing by frame could hide two distinct
          // overlapping menus and turn an ambiguous UI into a false match.
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

  function menuIsOwnedByButton(menu, button, deadline, budgetCode) {
    let ancestor = parentOf(menu);
    for (let depth = 0; depth <= MAX_POPUP_DEPTH && ancestor; depth += 1) {
      checkpoint(deadline, budgetCode);
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

  function exactPresetItems(menu, deadline, budgetCode) {
    const matches = [];
    const items = directChildren(
      menu,
      MAX_DIRECT_ELEMENTS,
      deadline,
      budgetCode
    );
    for (const item of items) {
      if (nameOf(item) === presetName) matches.push(item);
      const nested = directChildren(
        item,
        MAX_DIRECT_ELEMENTS,
        deadline,
        budgetCode
      ).filter(child => roleOf(child) === "AXMenu");
      for (const submenu of nested) {
        const nestedItems = directChildren(
          submenu,
          MAX_DIRECT_ELEMENTS,
          deadline,
          budgetCode
        );
        for (const nestedItem of nestedItems) {
          if (nameOf(nestedItem) === presetName) matches.push(nestedItem);
        }
      }
    }
    return matches;
  }

  if (
    presetName.length > MAX_INPUT_LENGTH ||
    !/^[^\/:\u0000-\u001f]+$/.test(presetName)
  ) {
    fail("INVALID_ARGUMENTS");
  }
  if (
    !Number.isInteger(mixerIndex) ||
    mixerIndex < 0 ||
    String(argv[1]).trim() === ""
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

  try {
    logic.frontmost = true;
  } catch (error) {
    fail("AX_READ_FAILED");
  }
  const firstTarget = resolveTarget(
    logic,
    prepressDeadline,
    "PREPRESS_TIMEOUT",
    null
  );
  const menusBeforePress = new Set(
    targetBranchMenus(
      logic,
      firstTarget,
      prepressDeadline,
      "PREPRESS_TIMEOUT"
    ).map(signatureOf)
  );
  const currentTarget = resolveTarget(
    logic,
    prepressDeadline,
    "PREPRESS_TIMEOUT",
    firstTarget
  );
  checkpoint(prepressDeadline, "PREPRESS_TIMEOUT");

  let popupSignature = null;
  function dismissKnownPopup() {
    if (!popupSignature || presetItemPressStarted) return;
    const cleanupDeadline = Date.now() + DISMISS_BUDGET_MS;
    try {
      const cleanupTarget = resolveTarget(
        logic,
        cleanupDeadline,
        "DISMISS_TIMEOUT",
        currentTarget
      );
      const candidates = targetBranchMenus(
        logic,
        cleanupTarget,
        cleanupDeadline,
        "DISMISS_TIMEOUT"
      ).filter(
        menu =>
          signatureOf(menu) === popupSignature &&
          menuIsOwnedByButton(
            menu,
            cleanupTarget.settingButton,
            cleanupDeadline,
            "DISMISS_TIMEOUT"
          )
      );
      if (candidates.length !== 1 || !candidates[0].exists()) return;
      const cancelAction = candidates[0].actions.byName("AXCancel");
      if (cancelAction.exists()) cancelAction.perform();
    } catch (cleanupError) {
      // Preserve the stable error from the failed mutation attempt.
    }
  }

  try {
    try {
      currentTarget.settingButton.actions.byName("AXPress").perform();
    } catch (error) {
      fail("PRESET_AUTOMATION_FAILED");
    }

    const popupDeadline = Date.now() + POPUP_ITEM_BUDGET_MS;
    let popup = null;
    while (!popup) {
      checkpoint(popupDeadline, "POPUP_ITEM_TIMEOUT");
      const newMenus = targetBranchMenus(
        logic,
        currentTarget,
        popupDeadline,
        "POPUP_ITEM_TIMEOUT"
      ).filter(menu => !menusBeforePress.has(signatureOf(menu)));
      const ownedMenus = newMenus.filter(menu =>
        menuIsOwnedByButton(
          menu,
          currentTarget.settingButton,
          popupDeadline,
          "POPUP_ITEM_TIMEOUT"
        )
      );
      if (newMenus.length > 1 || ownedMenus.length > 1) {
        fail("SETTING_POPUP_NOT_FOUND");
      }
      if (newMenus.length === 1 && ownedMenus.length === 1) {
        popup = ownedMenus[0];
        popupSignature = signatureOf(popup);
        break;
      }
      if (newMenus.length === 1 && ownedMenus.length === 0) {
        fail("SETTING_POPUP_NOT_FOUND");
      }
      delay(0.1);
    }

    const refreshedTarget = resolveTarget(
      logic,
      popupDeadline,
      "POPUP_ITEM_TIMEOUT",
      currentTarget
    );
    const currentPopups = targetBranchMenus(
      logic,
      refreshedTarget,
      popupDeadline,
      "POPUP_ITEM_TIMEOUT"
    ).filter(
      menu =>
        signatureOf(menu) === popupSignature &&
        menuIsOwnedByButton(
          menu,
          refreshedTarget.settingButton,
          popupDeadline,
          "POPUP_ITEM_TIMEOUT"
        )
    );
    if (currentPopups.length !== 1) fail("SETTING_POPUP_NOT_FOUND");
    const presetMatches = exactPresetItems(
      currentPopups[0],
      popupDeadline,
      "POPUP_ITEM_TIMEOUT"
    );
    if (presetMatches.length !== 1) fail("PRESET_MENU_ITEM_NOT_FOUND");
    const target = presetMatches[0];
    checkpoint(popupDeadline, "POPUP_ITEM_TIMEOUT");

    presetItemPressStarted = true;
    try {
      target.actions.byName("AXPress").perform();
    } catch (error) {
      fail("PRESET_AUTOMATION_FAILED");
    }

    const dismissDeadline = Date.now() + DISMISS_BUDGET_MS;
    while (true) {
      checkpoint(dismissDeadline, "SETTING_POPUP_NOT_DISMISSED");
      let popupExists;
      try {
        popupExists = currentPopups[0].exists();
      } catch (error) {
        fail("PRESET_AUTOMATION_FAILED");
      }
      if (popupExists === false) {
        let logicStillExists;
        try {
          logicStillExists = logic.exists();
        } catch (error) {
          fail("PRESET_AUTOMATION_FAILED");
        }
        if (!logicStillExists) fail("PRESET_AUTOMATION_FAILED");
        return JSON.stringify({
          status: "observed",
          evidence: "preset_menu_item_pressed_and_popup_dismissed"
        });
      }
      delay(0.1);
    }
  } catch (error) {
    dismissKnownPopup();
    throw error;
  }
}
