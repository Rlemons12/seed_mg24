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
      selectedDiscovery: null,
      nodeId: "",
      displayName: "",
      location: "",
      canProvision: false,
      showProvisioningFields: false,
      showRecovery: false,
      status: "Checking assignment state…",
      action: "none",
    };
    if (phase === "pending" || !item) return base;
    if (commissioningEligible(item, true, operationActive) && item.action === "commission") {
      return { ...base, selectedDiscovery: item, canProvision: true, showProvisioningFields: true,
        status: "Unassigned MG24 confirmed by device readback. Enter a new permanent identity.", action: "commission" };
    }
    if (item.commissioning_state === "registered_here") {
      return { ...base, status: `Already registered here as ${item.reported_node_id}.`,
        action: "view_or_reconnect" };
    }
    if (item.commissioning_state === "assigned_elsewhere") {
      return { ...base, showRecovery: true, status: `Already assigned as ${item.reported_node_id}.`,
        action: "recovery_or_import" };
    }
    return { ...base, showRecovery: true, status: item.message || "Assignment state is unavailable; rescan before continuing.",
      action: item.action || "retry_scan" };
  }

  return { commissioningEligible, transition };
}));
