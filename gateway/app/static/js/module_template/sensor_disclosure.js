(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.MG24SensorDisclosure = api;
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function detailsId(nodeId) {
    const encodedId = [...String(nodeId)].map((character) => character.codePointAt(0).toString(16)).join("-");
    return `mg-module-sensor-details-${encodedId || "unknown"}`;
  }

  function expandedNodeIds(container) {
    if (!container) return new Set();
    return new Set([...container.querySelectorAll('.mg-module-sensor-card__toggle[aria-expanded="true"]')]
      .map((toggle) => toggle.closest("[data-node-id]")?.dataset.nodeId)
      .filter(Boolean));
  }

  function toggleSensor(button) {
    const controlledId = button.getAttribute("aria-controls");
    const details = controlledId ? document.getElementById(controlledId) : null;
    if (!details) return;
    const expanded = button.getAttribute("aria-expanded") === "true";
    button.setAttribute("aria-expanded", String(!expanded));
    details.hidden = expanded;
  }

  function bind(container) {
    if (!container || container.dataset.disclosureBound === "true") return;
    container.dataset.disclosureBound = "true";
    container.addEventListener("click", (event) => {
      const toggle = event.target.closest(".mg-module-sensor-card__toggle");
      if (toggle && container.contains(toggle)) toggleSensor(toggle);
    });
  }

  function initialize() {
    bind(document.getElementById("node-list"));
  }

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize, { once: true });
    else initialize();
  }

  return { bind, detailsId, expandedNodeIds, toggleSensor };
}));
