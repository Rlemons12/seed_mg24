(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.MG24Onboarding = api;
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function commissioningEligible(item, classificationComplete = false, operationActive = false) {
    return Boolean(
      item
      && classificationComplete
      && item.commissioning_state === "unassigned"
      && item.commissioning_eligible === true
      && item.temporary_id
      && !item.assigned_node_id
      && item.compatible === true
      && !operationActive
    );
  }

  function transition(item = null, phase = "pending", operationActive = false) {
    const base = {
      state: item ? "checking_identity" : "idle",
      selectedDiscovery: null,
      nodeId: "",
      displayName: "",
      location: "",
      canProvision: false,
      showProvisioningFields: false,
      showRecovery: false,
      status: "Checking assignment state…",
      action: "none", primaryAction: null, writesPermitted: false,
    };
    if (!item) return { ...base, status: "Scan for a nearby sensor or supported USB board." };
    if (phase === "pending") return base;
    if (commissioningEligible(item, true, operationActive) && item.action === "commission") {
      return { ...base, state: operationActive ? "connecting" : "unassigned", selectedDiscovery: item,
        canProvision: !operationActive, showProvisioningFields: !operationActive, writesPermitted: !operationActive,
        status: "Unassigned sensor confirmed. Give it a name to finish setup.", action: "commission",
        primaryAction: operationActive ? null : "Set up sensor" };
    }
    if (item.commissioning_state === "registered_here") {
      return { ...base, state: "assigned_local", selectedDiscovery: item,
        status: `Already registered here as ${item.reported_node_id}.`,
        action: "view_or_reconnect", primaryAction: "Open Sensor" };
    }
    if (item.commissioning_state === "assigned_elsewhere") {
      return { ...base, state: "assigned_external", showRecovery: true,
        status: `Already assigned as ${item.reported_node_id}.`, selectedDiscovery: item,
        action: "import", primaryAction: "Import Sensor" };
    }
    if (item.commissioning_state === "incompatible") return { ...base, state: "blocked_error", showRecovery: true,
      status: item.message || "This device is incompatible.", action: "diagnose", primaryAction: "View reason" };
    return { ...base, state: "recoverable_error", showRecovery: true,
      status: item.message || "The sensor state could not be checked. Nothing was changed.",
      action: "retry_scan", primaryAction: "Scan again" };
  }

  return { commissioningEligible, transition };
}));
