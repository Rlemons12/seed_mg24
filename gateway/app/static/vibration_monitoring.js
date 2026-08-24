(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.MG24VibrationMonitoring = api;
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const AXES = ["x", "y", "z"];
  const cache = new Map();
  const inflight = new Map();
  const views = new WeakMap();
  const RANGE_SECONDS = { "15m": 900, "1h": 3600, "6h": 21600 };
  const METRICS = {
    rms: { label: "RMS", unit: "g", field: "accel_rms_{axis}_g", definition: "Overall dynamic vibration level during one measurement window.", calculation: "After first-order high-pass conditioning removes gravity/DC, RMS = √(Σx²/N) for the selected acceleration axis.", significance: "A persistent increase from baseline means vibration is stronger than this sensor normally observes.", limitation: "Relative condition data; not calibrated ISO vibration severity." },
    peak: { label: "Peak", unit: "g", field: "accel_peak_{axis}_g", definition: "Largest absolute dynamic acceleration in one measurement window.", calculation: "After high-pass conditioning, Peak = max(|x|) for the selected axis.", significance: "Highlights short strong events that may not greatly raise RMS; rising peak with stable RMS indicates more impulsive behavior." },
    crest: { label: "Crest factor", unit: "dimensionless", field: "crest_{axis}", definition: "Compares the largest vibration peak with the overall RMS level.", calculation: "Crest factor = absolute peak / RMS; the zero-RMS result is safely reported as zero.", significance: "A persistent increase means peaks are becoming larger relative to average vibration, indicating more impulsive behavior." },
    kurtosis: { label: "Kurtosis", unit: "dimensionless", field: "kurtosis_{axis}", definition: "Describes unusually large or impulsive values in the vibration waveform.", calculation: "Population fourth central moment divided by population variance squared. This is kurtosis, not excess kurtosis.", significance: "A persistent increase from baseline means the waveform is more impulsive or heavy-tailed than normal." },
    frequency: { label: "Dominant frequency", unit: "Hz", field: "dominant_frequency_{axis}_hz", definition: "Strongest detected vibration-frequency component for the selected axis.", calculation: "A 256-point Hann-windowed FFT searches bins above the configured minimum; frequency = strongest bin × effective sample rate / 256.", significance: "Shows whether the primary vibration pattern remains stable or shifts over time.", limitation: "Uses the strongest FFT-bin center, not a calibrated sub-bin estimate." },
    amplitude: { label: "Dominant amplitude", unit: "g", field: "dominant_amplitude_{axis}_g", definition: "Strength of the dominant FFT frequency component.", calculation: "For non-DC bins, amplitude = 2 × FFT magnitude / sum of the Hann-window coefficients.", significance: "Shows whether the dominant vibration component is becoming stronger or weaker.", limitation: "Best used relative to this sensor's baseline; absolute amplitude has not had controlled bench calibration." },
  };
  const HELP = {
    conditionState: { title: "Condition state", definition: "The gateway's conservative classification relative to this sensor's learned baseline.", calculation: "BASELINE PENDING learns valid windows; NORMAL is close to baseline; ELEVATED or SIGNIFICANT CHANGE require persistent deviation; INVALID rejects the latest window; INSUFFICIENT DATA means evaluation is unavailable.", significance: "Summarizes sustained change without diagnosing a cause or declaring machine health." },
    conditionScore: { title: "Baseline similarity score", unit: "0–100 dimensionless score", definition: "How closely current vibration features match this sensor's frozen baseline.", calculation: "The gateway finds the largest normalized feature deviation and calculates clamp(100 − 10 × deviation, 0, 100). RMS, peak, crest factor, kurtosis, dominant frequency/amplitude, and gyro RMS participate.", significance: "A falling score means current behavior is less similar to baseline.", limitation: "Not a machine-health percentage or probability of failure." },
    baselineCount: { title: "Baseline sample count", unit: "valid windows", definition: "Number of valid vibration windows used to learn this sensor's baseline.", calculation: "One is added for each unique valid window whose transmitted features are all finite; invalid and duplicate windows are excluded. The required count comes from gateway configuration.", significance: "More eligible samples make the initial learned reference more representative." },
    baselineStatus: { title: "Baseline status", definition: "Whether baseline learning has not started, is learning, or has frozen an established reference.", calculation: "The gateway starts in building state and freezes the baseline after the configured number of eligible windows.", significance: "Condition comparisons are available only after a baseline is established." },
    validity: { title: "Data validity", definition: "Whether the latest vibration summary passed sensor acquisition and processing checks and is current enough to display.", calculation: "The sensor marks acquisition/processing validity; the gateway additionally identifies stale data from its observation time and connection state.", significance: "Invalid or stale windows are not presented as current condition evidence and do not update the baseline." },
    lastUpdated: { title: "Last updated", definition: "Observation time of the most recently persisted vibration window.", calculation: "The gateway assigns the UTC receive/observation timestamp when it accepts the summary.", significance: "Shows whether the displayed condition is current or stale." },
    baselineMean: { title: "Baseline mean", definition: "Average metric value during initial baseline learning.", calculation: "The gateway accumulates the mean with Welford's online algorithm, then freezes it when the required sample count is reached.", significance: "Current values are compared with this reference to measure change." },
    change: { title: "Current vs baseline change", unit: "percent where defined", definition: "Relative difference between the current metric and baseline mean.", calculation: "(Current − baseline mean) / baseline mean × 100. Near-zero means show an absolute difference instead.", significance: "An intuitive view of movement from normal; actual condition scoring also accounts for learned spread and numerical floors." },
    algorithmVersion: { title: "Algorithm version", definition: "Version of the vibration-processing method that generated the measurement.", calculation: "Recorded with every vibration summary and baseline scope.", significance: "Prevents materially different filters, FFT settings, or feature definitions from being treated as equivalent." },
    relearn: { title: "Relearn Baseline", definition: "Starts learning a new normal vibration baseline for this sensor.", calculation: "The gateway preserves and supersedes the current baseline, creates the next version, and accumulates new eligible windows from zero.", significance: "Use after remounting, maintenance, machine configuration changes, or when the old baseline no longer represents normal operation.", limitation: "Does not factory reset the sensor, erase telemetry history, or change identity, firmware, configuration, or provisioning." },
  };

  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const fieldName = (metric, axis) => METRICS[metric].field.replace("{axis}", axis);
  const finite = (value) => typeof value === "number" && Number.isFinite(value);
  const valueText = (value, unit = "") => finite(value) ? `${Number(value.toFixed(unit ? 3 : 2))}${unit ? ` ${unit}` : ""}` : "Unavailable";
  const keyed = (node, key) => { node.dataset.liveKey = key; return node; };
  let helpId = 0;

  function parseUtcTimestamp(value) {
    if (value instanceof Date) return value.getTime();
    if (typeof value === "number") return Number.isFinite(value) ? value : NaN;
    if (typeof value !== "string" || !value.trim()) return NaN;
    const text = value.trim();
    const explicitZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(text);
    return Date.parse(explicitZone ? text : `${text}Z`);
  }

  function buildHistoryRange(range, nowMs = Date.now(), newestObservedAt = null) {
    const durationMs = RANGE_SECONDS[range] * 1000;
    const endMs = Number(nowMs);
    const rangeStartMs = endMs - durationMs;
    const newestMs = parseUtcTimestamp(newestObservedAt);
    const incrementalMs = Number.isFinite(newestMs) ? newestMs + 1 : rangeStartMs;
    const startMs = Math.max(rangeStartMs, incrementalMs);
    if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || !Number.isFinite(durationMs)
        || durationMs <= 0 || startMs >= endMs) return null;
    return { startMs, endMs, start: new Date(startMs).toISOString(), end: new Date(endMs).toISOString() };
  }

  function metricHelp(key, content = METRICS[key] || HELP[key]) {
    const title = content.title || content.label;
    const wrapper = keyed(element("span", undefined, "metric-help"), `help-${key}`);
    const button = element("button", "ⓘ", "metric-help__trigger");
    const popover = element("span", undefined, "metric-help__popover");
    const id = `metric-help-${++helpId}`;
    button.type = "button"; button.dataset.metricHelp = key;
    button.setAttribute("aria-label", `Explain ${title}`); button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-controls", id); button.setAttribute("aria-describedby", id);
    popover.id = id; popover.setAttribute("role", "tooltip");
    [["What it is", content.definition], ["How calculated", content.calculation], ["Why it matters", content.significance],
      ["Units", content.unit], ["Important limitation", content.limitation]].forEach(([label, text]) => {
      if (!text) return;
      popover.append(element("strong", label), element("span", text));
    });
    wrapper.append(button, popover); return wrapper;
  }

  function metricLabel(key, text, content) {
    const label = element("span", undefined, "metric-label");
    label.append(document.createTextNode(text), metricHelp(key, content)); return label;
  }

  function closeHelp(container) {
    container.querySelectorAll(".metric-help.is-open").forEach((help) => {
      help.classList.remove("is-open"); help.querySelector("button")?.setAttribute("aria-expanded", "false");
    });
  }

  function openRelearnDialog(container, view) {
    if (!view?.api || !view.deviceId || document.querySelector(".baseline-relearn-dialog")) return;
    const baseline = view.data?.baseline;
    const dialog = element("dialog", undefined, "baseline-relearn-dialog");
    const form = element("form"); form.method = "dialog";
    const title = baseline?.status === "building" ? "Restart baseline learning?" : "Relearn baseline?";
    form.append(element("h3", title),
      element("p", "This replaces the active analytical baseline with a newly learned baseline. Historical vibration readings are preserved."),
      element("p", "Sensor identity, firmware, provisioning, configuration, and factory-reset state are not changed.", "muted"));
    const reasonLabel = element("label", "Reason (optional)");
    const reason = element("textarea"); reason.maxLength = 240; reason.rows = 3; reason.placeholder = "For example: Sensor remounted";
    reasonLabel.append(reason); form.append(reasonLabel);
    const confirmationLabel = element("label", "Type RELEARN BASELINE to confirm");
    const confirmation = element("input"); confirmation.required = true; confirmation.autocomplete = "off";
    confirmationLabel.append(confirmation); form.append(confirmationLabel);
    const status = element("p", "", "summary"); status.setAttribute("aria-live", "polite"); form.append(status);
    const actions = element("div", undefined, "baseline-relearn-dialog__actions");
    const cancel = element("button", "Cancel", "quiet"); cancel.type = "button";
    const submit = element("button", "Relearn Baseline", "warning"); submit.type = "submit"; submit.disabled = true;
    actions.append(cancel, submit); form.append(actions); dialog.append(form); document.body.append(dialog);
    const close = () => { dialog.close(); dialog.remove(); };
    cancel.addEventListener("click", close);
    dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
    confirmation.addEventListener("input", () => { submit.disabled = confirmation.value !== "RELEARN BASELINE"; });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (confirmation.value !== "RELEARN BASELINE" || submit.disabled) return;
      submit.disabled = true; cancel.disabled = true; status.textContent = "Starting baseline learning…";
      try {
        const requestId = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
        await view.api(`/api/devices/${encodeURIComponent(view.deviceId)}/vibration/baseline/relearn`, {
          method: "POST", body: JSON.stringify({ confirmation: "RELEARN BASELINE", reason: reason.value.trim() || null, request_id: requestId }),
        });
        cache.forEach((_value, key) => { if (key.startsWith(`${view.deviceId}:`)) cache.delete(key); });
        close();
        await view.reload?.(true);
      } catch (error) {
        status.textContent = `Unable to relearn baseline: ${error.message}`;
        submit.disabled = false; cancel.disabled = false;
      }
    });
    dialog.showModal(); confirmation.focus();
  }

  function compatible(current, next) {
    return current?.nodeType === next.nodeType
      && (current.nodeType !== Node.ELEMENT_NODE || current.tagName === next.tagName);
  }

  function reconcile(current, next) {
    if (current.nodeType === Node.TEXT_NODE) {
      if (current.nodeValue !== next.nodeValue) current.nodeValue = next.nodeValue;
      return current;
    }
    const preserveOpen = current.tagName === "DETAILS" && current.open;
    const preserveHelp = current.classList?.contains("metric-help") && current.classList.contains("is-open");
    [...current.attributes].forEach((attribute) => {
      if (!next.hasAttribute(attribute.name) && !(preserveOpen && attribute.name === "open")) current.removeAttribute(attribute.name);
    });
    [...next.attributes].forEach((attribute) => current.setAttribute(attribute.name, attribute.value));
    if (preserveOpen) current.open = true;
    if (preserveHelp) current.classList.add("is-open");
    reconcileChildren(current, next);
    if (preserveHelp) current.querySelector("button")?.setAttribute("aria-expanded", "true");
    return current;
  }

  function reconcileChildren(current, next) {
    const existingByKey = new Map([...current.children]
      .filter((child) => child.dataset.liveKey).map((child) => [child.dataset.liveKey, child]));
    [...next.childNodes].forEach((nextChild, index) => {
      const key = nextChild.nodeType === Node.ELEMENT_NODE ? nextChild.dataset.liveKey : null;
      let child = key ? existingByKey.get(key) : current.childNodes[index];
      if (!compatible(child, nextChild)) {
        const replacement = nextChild.cloneNode(true);
        if (child && !key) current.replaceChild(replacement, child); else current.append(replacement);
        child = replacement;
      } else reconcile(child, nextChild);
      const atIndex = current.childNodes[index];
      if (atIndex !== child) current.insertBefore(child, atIndex || null);
    });
    while (current.childNodes.length > next.childNodes.length) current.lastChild.remove();
  }

  function statePresentation(state) {
    const normalized = ["BASELINE_PENDING", "NORMAL", "ELEVATED", "SIGNIFICANT_CHANGE", "INVALID", "INSUFFICIENT_DATA"].includes(state)
      ? state : "INVALID";
    return {
      state: normalized,
      label: normalized.replaceAll("_", " "),
      className: `condition-state condition-state--${normalized.toLowerCase().replaceAll("_", "-")}`,
    };
  }

  function comparison(latest, baseline, metric, axis) {
    const field = fieldName(metric, axis);
    const current = latest?.[field];
    const statistics = baseline?.statistics?.[field];
    const mean = statistics?.mean;
    const delta = finite(current) && finite(mean) ? current - mean : null;
    const percent = finite(delta) && Math.abs(mean) > 1.0e-9 ? delta / mean * 100 : null;
    return { field, current, mean, delta, percent, unit: METRICS[metric].unit };
  }

  function mapSeries(history, metric, axis) {
    const field = fieldName(metric, axis);
    return (history || []).filter((row) => row.validity === "valid" && finite(row[field]) && row.observed_at)
      .map((row) => ({ time: parseUtcTimestamp(row.observed_at), value: row[field] }))
      .filter((point) => Number.isFinite(point.time)).sort((left, right) => left.time - right.time);
  }

  function createChart(history, metric, baseline) {
    const spec = METRICS[metric];
    const figure = keyed(element("figure", undefined, "vibration-chart"), `chart-${metric}`);
    const caption = element("figcaption");
    const title = element("strong"); title.append(metricLabel(metric, `${spec.label} trend`));
    caption.append(title, element("span", spec.definition, "vibration-chart__help"));
    figure.append(caption);
    const series = AXES.map((axis) => ({ axis, points: mapSeries(history, metric, axis) }));
    const points = series.flatMap((item) => item.points);
    if (!points.length) {
      figure.append(element("p", "No vibration history is available for this period.", "vibration-empty"));
      return figure;
    }
    const width = 720; const height = 220; const pad = 32;
    const minimumTime = Math.min(...points.map((point) => point.time));
    const maximumTime = Math.max(...points.map((point) => point.time));
    const baselineValues = AXES.map((axis) => baseline?.statistics?.[fieldName(metric, axis)]?.mean).filter(finite);
    const values = [...points.map((point) => point.value), ...baselineValues];
    let minimumValue = Math.min(...values); let maximumValue = Math.max(...values);
    if (minimumValue === maximumValue) { minimumValue -= 0.5; maximumValue += 0.5; }
    const x = (value) => pad + (value - minimumTime) / Math.max(1, maximumTime - minimumTime) * (width - pad * 2);
    const y = (value) => height - pad - (value - minimumValue) / (maximumValue - minimumValue) * (height - pad * 2);
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", `${spec.label} history for X, Y, and Z axes in ${spec.unit || "dimensionless units"}`);
    [minimumValue, maximumValue].forEach((tick) => {
      const line = document.createElementNS(svg.namespaceURI, "line");
      line.setAttribute("x1", pad); line.setAttribute("x2", width - pad);
      line.setAttribute("y1", y(tick)); line.setAttribute("y2", y(tick)); line.setAttribute("class", "vibration-chart__grid");
      svg.append(line);
      const label = document.createElementNS(svg.namespaceURI, "text");
      label.setAttribute("x", 2); label.setAttribute("y", y(tick) + 4); label.textContent = Number(tick.toFixed(3));
      svg.append(label);
    });
    baselineValues.forEach((mean, index) => {
      const line = document.createElementNS(svg.namespaceURI, "line");
      line.setAttribute("x1", pad); line.setAttribute("x2", width - pad);
      line.setAttribute("y1", y(mean)); line.setAttribute("y2", y(mean));
      line.setAttribute("class", `vibration-chart__baseline vibration-chart__axis-${AXES[index]}`);
      svg.append(line);
    });
    series.forEach(({ axis, points: axisPoints }) => {
      if (!axisPoints.length) return;
      const polyline = document.createElementNS(svg.namespaceURI, "polyline");
      polyline.setAttribute("points", axisPoints.map((point) => `${x(point.time)},${y(point.value)}`).join(" "));
      polyline.setAttribute("class", `vibration-chart__line vibration-chart__axis-${axis}`);
      svg.append(polyline);
    });
    figure.append(svg);
    const legend = element("div", undefined, "vibration-chart__legend");
    AXES.forEach((axis) => legend.append(element("span", `${axis.toUpperCase()} axis`, `vibration-chart__key vibration-chart__axis-${axis}`)));
    if (baselineValues.length) legend.append(element("span", "Dashed lines: baseline means", "vibration-chart__baseline-key"));
    figure.append(legend);
    return figure;
  }

  function renderOverview(container, latest, baseline, condition, node) {
    const presentation = statePresentation(condition?.state || (latest ? "INSUFFICIENT_DATA" : "INVALID"));
    const section = keyed(element("section", undefined, "condition-overview"), "overview");
    const heading = element("div", undefined, "condition-overview__heading");
    heading.append(element("div", "CONDITION MONITORING", "eyebrow"), element("h4", "Relative vibration condition"),
      element("p", "Relative condition monitoring — not calibrated severity", "condition-disclosure"));
    section.append(heading);
    const cards = element("div", undefined, "condition-stat-grid");
    const stateCard = element("div", undefined, "condition-stat");
    stateCard.append(metricLabel("conditionState", "Current condition"), element("strong", presentation.label, presentation.className));
    const scoreCard = element("div", undefined, "condition-stat");
    scoreCard.append(metricLabel("conditionScore", "Baseline similarity"), element("strong", finite(condition?.baseline_similarity_score) ? `${Math.round(condition.baseline_similarity_score)} / 100` : "Not available"),
      element("small", "Higher values mean closer agreement with this sensor's baseline."));
    const baselineCard = element("div", undefined, "condition-stat");
    const baselineStatus = baseline?.status === "frozen" ? "ESTABLISHED" : baseline?.status === "building" ? "LEARNING" : "NOT STARTED";
    baselineCard.append(metricLabel("baselineStatus", "Baseline"), element("strong", baselineStatus),
      element("small", `${baseline?.sample_count || 0} / ${baseline?.minimum_samples || "—"} valid samples`));
    const updatedCard = element("div", undefined, "condition-stat");
    const stale = latest?.stale === true || ["stale", "disconnected", "error"].includes(node.connection_status);
    updatedCard.append(metricLabel("lastUpdated", "Last updated"), element("strong", latest?.observed_at ? new Date(latest.observed_at).toLocaleString() : "Never"));
    const validityCard = element("div", undefined, "condition-stat");
    validityCard.append(metricLabel("validity", "Data validity"), element("strong", stale ? "STALE" : latest?.validity === "valid" ? "VALID" : "INVALID"),
      element("small", stale ? `Data is stale (${node.connection_status}).` : latest?.validity === "valid" ? "Processed window passed acquisition checks." : "Current vibration data is unavailable."));
    cards.append(stateCard, scoreCard, baselineCard, updatedCard, validityCard); section.append(cards);
    if (baseline?.status === "building" && baseline.minimum_samples) {
      const progress = element("progress"); progress.max = baseline.minimum_samples; progress.value = baseline.sample_count;
      progress.setAttribute("aria-label", "Baseline learning progress");
      section.append(element("p", "Learning normal vibration behavior", "condition-learning"), progress);
    }
    return section;
  }

  function renderComparison(latest, baseline, selectedAxis = "z") {
    const section = keyed(element("section", undefined, "vibration-section"), "comparison");
    section.append(element("h4", "Current vs baseline"));
    const axisControl = element("div", undefined, "axis-tabs");
    axisControl.setAttribute("role", "tablist"); axisControl.setAttribute("aria-label", "Comparison axis");
    const tableWrap = element("div", undefined, "vibration-table-wrap");
    const renderAxis = (axis) => {
      const table = element("table", undefined, "vibration-comparison");
      const head = element("thead"); const header = element("tr");
      [element("span", "Metric"), element("span", "Current"), metricLabel("baselineMean", "Baseline"), metricLabel("change", "Change")]
        .forEach((label) => { const cell = element("th"); cell.append(label); header.append(cell); });
      head.append(header); const body = element("tbody");
      Object.keys(METRICS).forEach((metric) => {
        const item = comparison(latest, baseline, metric, axis); const row = keyed(element("tr"), `metric-${metric}`);
        const change = finite(item.percent) ? `${item.percent >= 0 ? "+" : ""}${item.percent.toFixed(1)}%`
          : finite(item.delta) ? `${item.delta >= 0 ? "+" : ""}${item.delta.toFixed(3)} ${item.unit}`.trim() : "Unavailable";
        const labelCell = element("td"); labelCell.append(metricLabel(metric, METRICS[metric].label)); row.append(labelCell);
        [valueText(item.current, item.unit), valueText(item.mean, item.unit), change].forEach((value) => row.append(element("td", value)));
        body.append(row);
      });
      table.append(head, body); tableWrap.append(table);
      [...axisControl.children].forEach((button) => button.setAttribute("aria-selected", String(button.dataset.axis === axis)));
    };
    AXES.forEach((axis) => { const button = keyed(element("button", axis.toUpperCase()), `axis-${axis}`); button.type = "button"; button.dataset.axis = axis;
      button.setAttribute("role", "tab"); axisControl.append(button); });
    section.append(axisControl, tableWrap); renderAxis(selectedAxis); return section;
  }

  function renderFactors(condition) {
    const section = keyed(element("section", undefined, "vibration-section"), "factors"); section.append(element("h4", "Why this condition"));
    const factors = condition?.factors || [];
    if (!factors.length) section.append(element("p", "No significant deviations are currently reported by the gateway.", "muted"));
    else { const list = element("ul", undefined, "condition-factors"); factors.forEach((factor) => {
      const change = finite(factor.change_percent) ? ` (${factor.change_percent >= 0 ? "+" : ""}${factor.change_percent.toFixed(1)}%)` : "";
      list.append(element("li", `${factor.feature.replaceAll("_", " ")}: ${valueText(factor.current)} vs baseline ${valueText(factor.baseline)}${change}`));
    }); section.append(list); }
    return section;
  }

  function renderBaseline(baseline, history = []) {
    const section = keyed(element("section", undefined, "vibration-section baseline-details"), "baseline"); section.append(element("h4", "Baseline details"));
    const dl = element("dl");
    [["Status", baseline?.status || "not started", "baselineStatus"], ["Valid samples", baseline?.sample_count ?? 0, "baselineCount"],
      ["Required samples", baseline?.minimum_samples ?? "Unavailable"], ["Baseline version", baseline?.baseline_version ?? "Unavailable"],
      ["Algorithm version", baseline?.algorithm_version ?? "Unavailable", "algorithmVersion"], ["Created", baseline?.created_at ? new Date(baseline.created_at).toLocaleString() : "Unavailable"],
      ["Established", baseline?.established_at ? new Date(baseline.established_at).toLocaleString() : "Not established"]]
      .forEach(([label, value, help]) => { const term = element("dt"); term.append(help ? metricLabel(help, String(label)) : document.createTextNode(String(label))); dl.append(term, element("dd", String(value))); });
    const actions = element("div", undefined, "baseline-actions");
    const relearn = element("button", baseline?.status === "building" ? "Restart Baseline Learning" : "Relearn Baseline");
    relearn.type = "button"; relearn.dataset.relearnBaseline = "true"; actions.append(relearn, metricHelp("relearn"));
    section.append(dl, actions);
    if (history.length) {
      const disclosure = element("details", undefined, "baseline-history"); disclosure.append(element("summary", "Baseline history"));
      const list = element("ol"); history.forEach((item) => {
        const reason = item.reason ? ` — ${item.reason}` : "";
        list.append(element("li", `v${item.baseline_version} · ${item.status.toUpperCase()} · ${item.sample_count}/${item.minimum_samples}${reason}`));
      }); disclosure.append(list); section.append(disclosure);
    }
    section.append(element("p", "Measurements are compared with this sensor's established baseline. They are not calibrated ISO vibration-severity measurements or automatic fault diagnoses.", "muted"));
    return section;
  }

  function render(container, data, node) {
    const next = element("div");
    const view = views.get(container) || { axis: "z", data: null, node: null };
    if (!data.latest) {
      const protocol = node?.protocol_version || "not reported";
      const message = data.imuFault
        ? "IMU sensor fault: the accelerometer/gyroscope is not responding, so vibration summaries cannot be produced."
        : protocol.startsWith("1.1")
        ? "Waiting for the first vibration summary from this sensor."
        : `No vibration summaries have been received. This sensor reports protocol ${protocol}; vibration monitoring requires production firmware using protocol 1.1.0.`;
      next.append(keyed(element("div", message, "vibration-empty"), "empty"));
    } else if (data.latest.validity !== "valid" || data.condition?.state === "INVALID") {
      next.append(renderOverview(next, data.latest, data.baseline, data.condition, node),
        keyed(element("div", "Vibration data unavailable. Current measurements are hidden because the latest window is invalid.", "vibration-invalid"), "invalid"));
    } else {
      const mode = container.dataset.viewMode || "all";
      if (["all", "overview"].includes(mode)) next.append(renderOverview(next, data.latest, data.baseline, data.condition, node));
      if (["all", "baseline"].includes(mode) && data.baseline?.status !== "building") next.append(renderComparison(data.latest, data.baseline, view.axis));
      if (["all", "baseline"].includes(mode)) next.append(renderFactors(data.condition));
      if (["all", "vibration"].includes(mode)) {
      const primary = keyed(element("section", undefined, "vibration-section"), "trends"); primary.append(element("h4", "Vibration trends"));
      const primaryCharts = element("div", undefined, "vibration-chart-grid");
      primaryCharts.append(createChart(data.history, "rms", data.baseline), createChart(data.history, "frequency", data.baseline));
      primary.append(primaryCharts); next.append(primary);
      const secondary = keyed(element("details", undefined, "vibration-section vibration-secondary"), "secondary");
      secondary.append(element("summary", "Signal character and frequency amplitude"));
      const secondaryCharts = element("div", undefined, "vibration-chart-grid");
      ["peak", "crest", "kurtosis", "amplitude"].forEach((metric) => secondaryCharts.append(createChart(data.history, metric, data.baseline)));
      secondary.append(secondaryCharts); next.append(secondary);
      }
      if (["all", "baseline"].includes(mode)) next.append(renderBaseline(data.baseline, data.baselineHistory));
    }
    next.append(keyed(element("div", `Last updated: ${new Date().toLocaleTimeString()}`, "vibration-refresh-status muted"), "refresh-status"));
    reconcileChildren(container, next);
    container.dataset.hasVibrationData = "true";
    views.set(container, { ...view, data, node });
    container.dispatchEvent(new CustomEvent("mg24:vibration-summary", { bubbles: true, detail: {
      nodeId: node.node_id, condition: data.condition, baseline: data.baseline, latest: data.latest,
    } }));
  }

  function hasImuSensorFault(readings = []) {
    return readings.some((reading) =>
      (String(reading.channel || "").startsWith("acceleration_") || String(reading.channel || "").startsWith("angular_velocity_"))
      && reading.quality === "sensor_fault");
  }

  function ensureView(container) {
    if (views.has(container)) return views.get(container);
    const view = { axis: "z", data: null, node: null, api: null, deviceId: null, reload: null };
    views.set(container, view);
    container.addEventListener("click", (event) => {
      const helpButton = event.target.closest?.("button[data-metric-help]");
      if (helpButton) {
        event.stopPropagation();
        const help = helpButton.closest(".metric-help"); const opening = !help.classList.contains("is-open");
        closeHelp(container); help.classList.toggle("is-open", opening); helpButton.setAttribute("aria-expanded", String(opening));
        return;
      }
      const relearnButton = event.target.closest?.("button[data-relearn-baseline]");
      if (relearnButton) {
        event.stopPropagation(); openRelearnDialog(container, views.get(container)); return;
      }
      const button = event.target.closest?.(".axis-tabs button[data-axis]");
      const current = views.get(container);
      if (!button || !current?.data) return;
      current.axis = button.dataset.axis;
      render(container, current.data, current.node);
    });
    container.addEventListener("keydown", (event) => { if (event.key === "Escape") { closeHelp(container); event.stopPropagation(); } });
    document.addEventListener("click", (event) => { if (!container.contains(event.target)) closeHelp(container); });
    return view;
  }

  async function fetchHistory(api, deviceId, range, existing = []) {
    const newest = existing.reduce((value, row) => Math.max(value, parseUtcTimestamp(row.observed_at) || 0), 0);
    const historyRange = buildHistoryRange(range, Date.now(), newest || null);
    if (!historyRange) {
      console.warn("Blocked invalid vibration history range", { device: deviceId, range, newest_observed_at: newest || null });
      return existing;
    }
    const rangeStart = new Date(historyRange.endMs - RANGE_SECONDS[range] * 1000);
    const start = new Date(historyRange.startMs);
    const rows = [];
    let pageEnd = new Date(historyRange.endMs);
    for (let page = 0; page < 18; page += 1) {
      if (start.getTime() >= pageEnd.getTime()) break;
      const query = new URLSearchParams({ start: start.toISOString(), end: pageEnd.toISOString(), limit: "500" });
      const response = await api(`/api/devices/${encodeURIComponent(deviceId)}/vibration/history?${query}`);
      const items = response.items || []; rows.push(...items);
      if (items.length < 500) break;
      pageEnd = new Date(parseUtcTimestamp(items[items.length - 1].observed_at) - 1);
      if (pageEnd <= start) break;
    }
    const merged = new Map(existing.filter((row) => parseUtcTimestamp(row.observed_at) >= rangeStart.getTime())
      .map((row) => [row.id ?? `${row.session_id}:${row.window_sequence}:${row.algorithm_version}`, row]));
    rows.forEach((row) => merged.set(row.id ?? `${row.session_id}:${row.window_sequence}:${row.algorithm_version}`, row));
    return [...merged.values()].sort((left, right) => parseUtcTimestamp(right.observed_at) - parseUtcTimestamp(left.observed_at));
  }

  async function load(container, deviceId, node, api, range = "1h", force = false) {
    const view = ensureView(container);
    view.api = api; view.deviceId = deviceId; view.reload = (reloadForce = true) => load(container, deviceId, node, api, range, reloadForce);
    container.dataset.range = range;
    const cacheKey = `${deviceId}:${range}`; const cached = cache.get(cacheKey);
    if (!force && cached && Date.now() - cached.loadedAt < 5000) {
      if (container.dataset.vibrationRender === String(cached.loadedAt)) return;
      render(container, cached.data, node); container.dataset.vibrationRender = String(cached.loadedAt); return;
    }
    if (!container.dataset.hasVibrationData && !container.querySelector(".vibration-loading")) {
      container.replaceChildren(element("p", "Loading vibration condition data…", "vibration-loading"));
    } else container.classList.add("is-updating");
    try {
      let request = inflight.get(cacheKey);
      if (!request) {
        const latest = api(`/api/devices/${encodeURIComponent(deviceId)}/vibration/latest`).catch((error) => {
          if (error.status === 404 && error.message === "no vibration history") return null;
          throw error;
        });
        request = Promise.all([
          latest,
          api(`/api/devices/${encodeURIComponent(deviceId)}/vibration/baseline`),
          api(`/api/devices/${encodeURIComponent(deviceId)}/condition`), fetchHistory(api, deviceId, range, cached?.data?.history || []),
          api(`/api/devices/${encodeURIComponent(deviceId)}/vibration/baseline/history?limit=20`),
          api(`/api/devices/${encodeURIComponent(deviceId)}/readings/latest`),
        ]).finally(() => inflight.delete(cacheKey));
        inflight.set(cacheKey, request);
      }
      const [latest, baseline, condition, history, baselineHistory, readings] = await request;
      if (container.dataset.range !== range) return;
      const data = { latest, baseline, condition, history, baselineHistory: baselineHistory.items || [], imuFault: hasImuSensorFault(readings) };
      const loadedAt = Date.now();
      cache.set(cacheKey, { loadedAt, data }); render(container, data, node); container.dataset.vibrationRender = String(loadedAt);
    } catch (error) {
      if (error.status === 404) {
        const loadedAt = Date.now(); const data = { latest: null };
        cache.set(cacheKey, { loadedAt, data }); render(container, data, node); container.dataset.vibrationRender = String(loadedAt);
      }
      else if (container.dataset.hasVibrationData) {
        let status = container.querySelector('.vibration-refresh-status');
        if (!status) { status = keyed(element("div", undefined, "vibration-refresh-status muted"), "refresh-status"); container.append(status); }
        status.textContent = `Unable to refresh. Last successful values remain visible. ${error.message}`;
      } else container.replaceChildren(element("div", `Vibration condition data could not be loaded: ${error.message}`, "vibration-invalid"));
    } finally {
      container.classList.remove("is-updating");
    }
  }

  return { METRICS, HELP, parseUtcTimestamp, buildHistoryRange, fetchHistory, comparison, mapSeries, statePresentation, hasImuSensorFault, render, load };
}));
