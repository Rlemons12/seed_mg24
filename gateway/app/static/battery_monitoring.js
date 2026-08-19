"use strict";

window.MG24BatteryMonitoring = (() => {
  const node = (tag, text, className) => { const item = document.createElement(tag); if (text != null) item.textContent = text; if (className) item.className = className; return item; };
  const duration = (seconds) => {
    if (seconds == null) return "Not enough data";
    const days = Math.floor(seconds / 86400); const hours = Math.floor((seconds % 86400) / 3600); const minutes = Math.floor((seconds % 3600) / 60);
    return [days && `${days}d`, (days || hours) && `${hours}h`, `${minutes}m`].filter(Boolean).join(" ");
  };
  const date = (value) => value ? new Date(value).toLocaleString() : "Active";
  const ratio = (value) => value == null ? "Not enough data" : `${(value * 100).toFixed(1)}% of baseline`;

  function stat(label, value, help) {
    const item = node("div", null, "battery-stat"); item.append(node("span", label), node("strong", value), node("small", help)); return item;
  }

  function chart(points, valueKey, label, color) {
    const figure = node("figure", null, "battery-chart"); figure.append(node("figcaption", label));
    if (points.length < 2) { figure.append(node("p", "Not enough data", "muted")); return figure; }
    const values = points.map((item) => Number(item[valueKey])).filter(Number.isFinite); const min = Math.min(...values); const max = Math.max(...values); const span = max - min || 1;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"); svg.setAttribute("viewBox", "0 0 600 160"); svg.setAttribute("role", "img"); svg.setAttribute("aria-label", label);
    const polyline = document.createElementNS(svg.namespaceURI, "polyline");
    polyline.setAttribute("points", values.map((value, index) => `${index * 600 / (values.length - 1)},${145 - (value - min) * 125 / span}`).join(" "));
    polyline.setAttribute("fill", "none"); polyline.setAttribute("stroke", color); polyline.setAttribute("stroke-width", "3"); svg.append(polyline); figure.append(svg); return figure;
  }

  function historyTable(cycles) {
    const wrap = node("div", null, "battery-table-wrap"); const table = node("table", null, "battery-cycle-table");
    const head = node("tr"); ["Cycle", "Started", "Ended", "Runtime", "Start V", "End V", "Runtime health", "Eligible"].forEach((value) => head.append(node("th", value)));
    const thead = node("thead"); thead.append(head); const tbody = node("tbody");
    cycles.forEach((cycle) => { const row = node("tr"); [cycle.cycle_number, date(cycle.started_at), date(cycle.ended_at), duration(cycle.runtime_seconds), cycle.start_voltage == null ? "—" : `${cycle.start_voltage.toFixed(2)} V`, cycle.end_voltage == null ? "—" : `${cycle.end_voltage.toFixed(2)} V`, ratio(cycle.runtime_health_ratio), cycle.is_complete ? (cycle.is_baseline_eligible ? "Yes" : cycle.exclusion_reason || "No") : "—"].forEach((value) => row.append(node("td", String(value)))); tbody.append(row); });
    table.append(thead, tbody); wrap.append(table); return wrap;
  }

  async function load(container, deviceId, api, force = false) {
    if (container.dataset.loaded === "true" && !force) return;
    container.replaceChildren(node("p", "Loading battery history…", "muted"));
    const encoded = encodeURIComponent(deviceId);
    const [summary, cycles, history] = await Promise.all([
      api(`/api/devices/${encoded}/battery`), api(`/api/devices/${encoded}/battery/cycles`), api(`/api/devices/${encoded}/battery/history`),
    ]);
    container.replaceChildren(); container.dataset.loaded = "true";
    const voltage = summary.voltage.current_v == null ? "Unavailable" : `${summary.voltage.current_v.toFixed(2)} V`;
    const stats = node("div", null, "battery-stat-grid");
    [["Battery voltage", voltage, `Measured electrical value; percentage ${summary.voltage.calibration_status === "NOT_CALIBRATED" ? "not calibrated" : "unavailable"}.`],
      ["Current charge runtime", duration(summary.current_cycle_runtime_seconds), summary.current_cycle ? `Cycle ${summary.current_cycle.cycle_number}` : "Waiting for an observed charge cycle"],
      ["Previous charge runtime", duration(summary.history.latest_completed_runtime_seconds), `${summary.history.completed_cycles} completed cycle(s)`],
      ["Average last 5 charges", duration(summary.history.average_last_5_seconds), "Eligible comparable cycles only"],
      ["Average last 10 charges", duration(summary.history.average_last_10_seconds), "Eligible comparable cycles only"],
      ["Baseline runtime", duration(summary.history.baseline_runtime_seconds), "Median of initial eligible cycles"],
      ["Runtime health", ratio(summary.health.runtime_health_ratio), summary.health.status],
      ["Battery trend", summary.health.trend.replaceAll("_", " "), "Observed runtime trend, not state of charge"],
      ["Estimated recharge window", summary.prediction.lower_bound ? `${date(summary.prediction.lower_bound)} – ${date(summary.prediction.upper_bound)}` : "Not enough data", `Confidence: ${summary.prediction.confidence}`],
      ["Replacement status", summary.replacement.status.replaceAll("_", " "), summary.replacement.explanation],
    ].forEach(([label, value, help]) => stats.append(stat(label, value, help)));
    const actions = node("div", null, "actions"); const charged = node("button", "Battery charged now"); charged.type = "button";
    charged.addEventListener("click", async () => { if (!window.confirm("Record a completed charge now? This starts a new charge cycle and preserves history.")) return; await api(`/api/devices/${encoded}/battery/mark-charged`, {method: "POST", body: JSON.stringify({})}); container.dataset.loaded = "false"; await load(container, deviceId, api, true); });
    const replaced = node("button", "Battery replaced", "warning"); replaced.type = "button";
    replaced.addEventListener("click", async () => { const reason = window.prompt("Reason for battery replacement"); if (!reason) return; await api(`/api/devices/${encoded}/battery/replace`, {method: "POST", body: JSON.stringify({reason})}); container.dataset.loaded = "false"; await load(container, deviceId, api, true); });
    actions.append(charged, replaced);
    const charts = node("div", null, "battery-chart-grid"); charts.append(chart(history.voltage, "voltage", "Battery voltage vs time", "#2774ae"), chart([...cycles].reverse().filter((item) => item.is_complete), "runtime_seconds", "Runtime per charge cycle", "#598c35"));
    container.append(stats, node("p", summary.health.explanation, "summary"), actions, charts, node("h4", "Charge-cycle history"), historyTable(cycles));
  }
  return {load, duration};
})();
