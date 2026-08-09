(() => {
  "use strict";
  const { get } = MG24ModuleClient;
  const state = document.getElementById("health-state");
  const notice = document.getElementById("health-notice");
  const gatewayStatus = document.getElementById("gateway-status");

  async function refresh() {
    try {
      const health = await get("/api/health");
      state.textContent = health.status === "ok" ? "Healthy" : health.status;
      gatewayStatus.textContent = health.status === "ok" ? "Healthy" : health.status;
      document.getElementById("health-version").textContent = health.version;
      document.getElementById("health-devices").textContent = String(health.managed_devices);
      document.getElementById("health-time").textContent = new Date(health.time).toLocaleString();
      notice.textContent = "";
    } catch (error) {
      state.textContent = "Unavailable";
      gatewayStatus.textContent = "Unavailable";
      notice.textContent = `Gateway health could not be loaded. ${error.message}`;
    }
  }

  refresh();
  window.setInterval(refresh, 15000);
})();
