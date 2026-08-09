(() => {
  "use strict";
  const { get, element } = MG24ModuleClient;
  const list = document.getElementById("installations-page-list");
  const notice = document.getElementById("installations-notice");
  const count = document.getElementById("installation-count");
  const gatewayStatus = document.getElementById("gateway-status");

  function labeledValues(item) {
    return [["Equipment ID", item.device_id], ["Sensor node", item.node_id], ["Profile", item.sensor_profile_id],
      ["Profile version", item.sensor_profile_version], ["Interface", item.interface_id],
      ["Verification", item.verification_status], ["State", item.provisioning_state]];
  }

  function render(items) {
    list.replaceChildren();
    count.textContent = `${items.length} installation${items.length === 1 ? "" : "s"}`;
    gatewayStatus.textContent = "Ready";
    items.forEach((item) => {
      const card = element("article", undefined, "device-card");
      card.append(element("h3", item.display_name), element("p", item.location || "No location provided", "muted"));
      const values = element("dl");
      labeledValues(item).forEach(([label, value]) => values.append(element("dt", label), element("dd", value || "Not reported")));
      card.append(values);
      list.append(card);
    });
    if (!items.length) list.append(element("p", "No sensor installations have been created.", "muted"));
  }

  get("/api/sensor-installations").then(render).catch((error) => {
    count.textContent = "Unavailable";
    gatewayStatus.textContent = "Unavailable";
    notice.textContent = `Installations could not be loaded. ${error.message}`;
  });
})();
