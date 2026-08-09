"use strict";

const state = { nodes: [], removedNodes: [], installations: [], profiles: [], interfaces: [], readings: {}, selectedDiscovery: null, selectedUsbBoard: null, selectedInstallation: null, selectedConfigNode: null, lifecycle: null, resetTarget: null, resetConfirmation: null, draftId: null, commissioningActive: false };
const $ = (id) => document.getElementById(id);
const el = (tag, text, className) => { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; };
function notice(message = "") { $("notice").textContent = message; }
function time(value) { return value ? new Date(value).toLocaleString() : "Never"; }

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
  if (!response.ok) { let detail = `Request failed (${response.status})`; let body = null; try { body = await response.json(); detail = typeof body.detail === "string" ? body.detail : body.detail?.message || JSON.stringify(body.detail); } catch (_) { /* no JSON */ } const error = new Error(detail); error.status = response.status; error.code = body?.detail?.code || body?.error; error.response = body; throw error; }
  return response.status === 204 ? null : response.json();
}

function renderCommissioningState(item = null, phase = "pending") {
  const view = MG24Onboarding.transition(item, phase, state.commissioningActive);
  state.selectedDiscovery = view.selectedDiscovery;
  $("new-node-id").value = view.nodeId;
  $("new-node-name").value = view.displayName;
  $("new-node-location").value = view.location;
  $("new-node-fields").classList.toggle("hidden", !view.showProvisioningFields);
  $("assignment-recovery").classList.toggle("hidden", !view.showRecovery);
  $("import-node-button").hidden = view.action !== "import";
  $("assignment-status").textContent = view.status;
  [$("new-node-id"), $("new-node-name"), $("new-node-location")].forEach((input) => { input.disabled = !view.canProvision; });
  const submit = $("provision-node-button");
  submit.hidden = !view.canProvision;
  submit.disabled = true;
  submit.setAttribute("aria-disabled", "true");
  submit.tabIndex = view.canProvision ? 0 : -1;
  notice("");
  updateCommissioningSubmit();
  return view;
}

function currentCommissioningEligible() {
  return MG24Onboarding.commissioningEligible(state.selectedDiscovery, true, state.commissioningActive);
}

function updateCommissioningSubmit() {
  const submit = $("provision-node-button");
  const fieldsValid = [$("new-node-id"), $("new-node-name"), $("new-node-location")]
    .every((input) => input.checkValidity());
  const eligible = currentCommissioningEligible();
  submit.hidden = !eligible;
  submit.disabled = !(eligible && fieldsValid);
  submit.setAttribute("aria-disabled", String(submit.disabled));
  submit.tabIndex = submit.hidden ? -1 : 0;
}

async function refresh() {
  [state.nodes, state.removedNodes, state.installations, state.profiles] = await Promise.all([api("/api/nodes"), api("/api/device-lifecycle/removed"), api("/api/sensor-installations"), api("/api/sensor-profiles")]);
  const readings = await Promise.all(state.nodes.map(async(node)=>{
    try { return [node.node_id, await api(`/api/devices/${encodeURIComponent(node.node_id)}/readings/latest`)]; }
    catch (_) { return [node.node_id, []]; }
  }));
  state.readings = Object.fromEntries(readings);
  renderNodes(); renderRemovedNodes(); renderInstallations();
}

function inputName(channel) { return channel.replaceAll("_"," ").replace(/\b\w/g,(letter)=>letter.toUpperCase()); }
function unitLabel(unit) { return ({g:"g (gravity)",dps:"°/s (degrees per second)",V:"V (volts)",adc_count:"ADC counts",percent:"% (percent)",pwm_count:"PWM counts",count:"count"})[unit] || unit || "unit not reported"; }
function readingText(reading) { const value=reading.normalized_value ?? reading.raw_value; const shown=typeof value==="number" ? Number(value.toFixed(3)) : value; return `${shown ?? "Unavailable"} ${unitLabel(reading.unit)}`; }
function inputOrder(channel) { const primary=["acceleration_x","acceleration_y","acceleration_z","angular_velocity_x","angular_velocity_y","angular_velocity_z"]; const index=primary.indexOf(channel); if(index>=0)return index; if(channel.startsWith("analog_"))return 100+Number(channel.slice(7)); return 20; }

function renderNodes() {
  const list = $("node-list");
  const expandedNodeIds = MG24SensorDisclosure.expandedNodeIds(list);
  list.replaceChildren();
  state.nodes.forEach((node) => {
    const card = el("article", undefined, "mg-module-sensor-card");
    card.dataset.nodeId = node.node_id;
    const heading = el("h3", undefined, "mg-module-sensor-card__heading");
    const toggle = el("button", undefined, "mg-module-sensor-card__toggle");
    const detailsId = MG24SensorDisclosure.detailsId(node.node_id);
    const expanded = expandedNodeIds.has(node.node_id);
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", String(expanded));
    toggle.setAttribute("aria-controls", detailsId);
    const chevron = el("span", "", "mg-module-sensor-card__chevron");
    chevron.setAttribute("aria-hidden", "true");
    toggle.append(el("span", node.display_name, "mg-module-sensor-card__name"), chevron);
    heading.append(toggle);

    const details = el("div", undefined, "mg-module-sensor-card__details");
    details.id = detailsId;
    details.hidden = !expanded;
    details.append(el("div", node.node_id, "equipment-id"), el("p", node.connection_status, `state ${node.connection_status}`));
    const dl = el("dl");
    [["BLE name", node.ble_advertised_name || "Unknown"], ["BLE address", node.ble_address || "Unknown"],
      ["Firmware", node.firmware_version || "Unknown"], ["Sensor package", node.sensor_package_version || "Not reported"],
      ["Protocol", node.protocol_version || "Not reported"], ["Compatibility", node.compatibility_status || "unknown"]]
      .forEach(([label, value]) => dl.append(el("dt", label), el("dd", value)));
    details.append(dl);
    if (node.compatibility_message) details.append(el("p", node.compatibility_message, node.compatibility_status === "compatible" ? "muted" : "warning"));
    details.append(el("h4", "Live sensor inputs"));
    const inputGrid = el("div", undefined, "channel-grid live-input-grid");
    const rows = state.readings[node.node_id] || [];
    rows.filter((row) => !["buffer_utilization", "dropped_record_count", "processing_error_count", "sensor_error_count", "led_brightness"].includes(row.channel))
      .sort((left, right) => inputOrder(left.channel) - inputOrder(right.channel) || left.channel.localeCompare(right.channel))
      .forEach((row) => {
        const input = el("div", undefined, "channel");
        input.dataset.channel = row.channel;
        input.append(el("strong", inputName(row.channel)), el("div", readingText(row)), el("span", `Quality: ${row.quality}; updated ${time(row.received_at)}`));
        inputGrid.append(input);
      });
    if (!inputGrid.children.length) inputGrid.append(el("p", node.connection_status === "connected" ? "Waiting for the first sensor reading…" : "Connect this sensor to load live readings.", "muted"));
    details.append(inputGrid);
    const actions = el("div", undefined, "actions");
    const reconnect = el("button", "Open Sensor");
    reconnect.type = "button";
    reconnect.addEventListener("click", async () => { try { await api(`/api/devices/${encodeURIComponent(node.node_id)}/connect`, { method: "POST" }); notice(`Opened ${node.node_id}; live telemetry will reconnect automatically.`); await refresh(); } catch (error) { notice(error.message); } });
    const configure = el("button", "Configure");
    configure.type = "button";
    configure.addEventListener("click", () => openDeviceConfiguration(node).catch((error) => notice(error.message)));
    const remove = el("button", "Remove from network", "danger");
    remove.type = "button";
    remove.addEventListener("click", () => openLifecycle("remove", node));
    const factoryReset = el("button", "Factory Reset Sensor", "danger");
    factoryReset.type = "button";
    factoryReset.addEventListener("click", () => openFactoryReset(node).catch((error) => notice(error.message)));
    const resetReregister = el("button", "Reset and Re-register", "danger");
    resetReregister.type = "button";
    resetReregister.addEventListener("click", () => window.MG24ResetReregister.open(node));
    actions.append(reconnect, configure, remove, factoryReset, resetReregister);
    details.append(actions);
    card.append(heading, details);
    list.append(card);
  });
  if (!state.nodes.length) list.append(el("p", "No sensor nodes are registered.", "muted"));
}

function renderRemovedNodes() {
  const list = $("removed-node-list");
  list.replaceChildren();
  state.removedNodes.forEach((node) => {
    const card = el("article", undefined, "device-card");
    card.append(el("h3", node.display_name), el("div", node.device_id, "equipment-id"),
      el("p", `Removed ${time(node.removed_at)}. Historical telemetry is retained.`, "muted"));
    const restore = el("button", "Restore/Reapprove");
    restore.type = "button";
    restore.addEventListener("click", () => openLifecycle("restore", node));
    card.append(restore); list.append(card);
  });
  if (!state.removedNodes.length) list.append(el("p", "No removed sensor registrations.", "muted"));
}

async function openLifecycle(operation, node) {
  const prepared = await api("/api/device-lifecycle/confirm", {method:"POST", body:JSON.stringify({
    operation, device_id:node.node_id || node.device_id, expected_hardware_id:node.hardware_id || null,
  })});
  state.lifecycle = prepared;
  $("lifecycle-title").textContent = operation === "remove" ? "Remove from network" : "Restore/Reapprove sensor";
  $("lifecycle-identity").textContent = `${prepared.display_name} — ${prepared.device_id}; hardware ${prepared.hardware_id || "not yet reported"}; BLE ${prepared.ble_address || "unknown"}; ${prepared.connection_status}.`;
  $("lifecycle-warning").textContent = operation === "remove"
    ? "This removes gateway membership and stops reconnection. It does not factory-reset the physical sensor. The still-provisioned sensor may continue advertising. Historical telemetry remains available."
    : "This explicitly reapproves the existing registration and retained history. It does not alter or factory-reset the physical sensor.";
  $("lifecycle-confirm-id").value = ""; $("lifecycle-execute").disabled = true;
  $("lifecycle-dialog").showModal();
}

$("lifecycle-confirm-id").addEventListener("input", () => {
  $("lifecycle-execute").disabled = !state.lifecycle || $("lifecycle-confirm-id").value !== state.lifecycle.device_id;
});
$("lifecycle-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.lifecycle || $("lifecycle-confirm-id").value !== state.lifecycle.device_id) return;
  const pending = state.lifecycle; $("lifecycle-execute").disabled = true;
  try {
    const result = await api("/api/device-lifecycle/execute", {method:"POST", body:JSON.stringify({
      confirmation_token:pending.confirmation_token, operation:pending.operation, device_id:pending.device_id,
      expected_hardware_id:pending.hardware_id, expected_ble_address:pending.ble_address,
      reason:pending.operation === "remove" ? "operator_confirmed_dashboard_removal" : null,
    })});
    $("lifecycle-dialog").close(); state.lifecycle = null;
    notice(result.lifecycle_state === "removed" ? "Sensor removed from this gateway. Physical sensor and telemetry history were preserved." : "Sensor registration restored and reapproved.");
    await refresh();
  } catch (error) { notice(error.message); $("lifecycle-execute").disabled = false; }
});
document.querySelector('[data-action="close-lifecycle"]').addEventListener("click",async()=>{const pending=state.lifecycle;$("lifecycle-dialog").close();state.lifecycle=null;if(pending){try{await api("/api/device-lifecycle/cancel",{method:"POST",body:JSON.stringify({operation:pending.operation,device_id:pending.device_id,confirmation_token:pending.confirmation_token,expected_hardware_id:pending.hardware_id,expected_ble_address:pending.ble_address})});}catch(_error){/* token expires and remains single-use */}}});

async function openFactoryReset(node) {
  state.resetTarget = node; state.resetConfirmation = null;
  const boards = await api("/api/factory-reset/boards");
  const select = $("factory-reset-board"); select.replaceChildren();
  boards.forEach((board) => {
    const option = el("option", `${board.port} — ${board.node_id || "unprovisioned"} — ${board.hardware_id || "identity unavailable"}`);
    option.value = board.port; option.dataset.hardwareId = board.hardware_id || ""; option.dataset.nodeId = board.node_id || "";
    option.disabled = board.hardware_id !== node.hardware_id && (node.hardware_id || board.node_id !== node.node_id);
    select.append(option);
  });
  const match = [...select.options].find((option) => !option.disabled);
  if (!match) throw new Error("No positively identified USB sensor matches this gateway sensor.");
  select.value = match.value; $("factory-reset-confirm-id").value = ""; $("factory-reset-execute").disabled = true;
  $("factory-reset-identity").textContent = `${node.display_name}; sensor ID ${node.node_id}; hardware ${match.dataset.hardwareId}; port ${match.value}; firmware ${node.firmware_version || "unknown"}; currently ${node.connection_status}.`;
  $("factory-reset-progress").textContent = "Identity matched. Type the immutable hardware ID to request a device-bound challenge.";
  $("factory-reset-dialog").showModal();
}
$("factory-reset-confirm-id").addEventListener("input", () => {
  const option = $("factory-reset-board").selectedOptions[0];
  $("factory-reset-execute").disabled = !option || $("factory-reset-confirm-id").value !== option.dataset.hardwareId;
});
$("factory-reset-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const option = $("factory-reset-board").selectedOptions[0];
  if (!state.resetTarget || !option || $("factory-reset-confirm-id").value !== option.dataset.hardwareId) return;
  $("factory-reset-execute").disabled = true;
  try {
    const target = {device_id:state.resetTarget.node_id, hardware_id:option.dataset.hardwareId, port:option.value};
    const confirmation = await api("/api/factory-reset/confirm", {method:"POST", body:JSON.stringify(target)});
    $("factory-reset-progress").textContent = "Physical identity and firmware verified. Reset in progress…";
    const operation = await api("/api/factory-reset/execute", {method:"POST", body:JSON.stringify({...target, confirmation_token:confirmation.confirmation_token})});
    for (;;) {
      await new Promise((resolve) => setTimeout(resolve, 500));
      const current = await api(`/api/factory-reset/operations/${encodeURIComponent(operation.operation_id)}`);
      $("factory-reset-progress").textContent = [...current.progress, current.error || ""].filter(Boolean).join("\n");
      if (current.state === "complete") {
        $("factory-reset-dialog").close(); notice("Factory reset verified after reboot. Firmware and telemetry history were preserved. Use Add Sensor to onboard it with an explicitly selected identity."); await refresh(); break;
      }
      if (["failed","partial_failure"].includes(current.state)) { $("factory-reset-execute").disabled = false; break; }
    }
  } catch (error) { $("factory-reset-progress").textContent = error.message; $("factory-reset-execute").disabled = false; }
});
document.querySelector('[data-action="close-factory-reset"]').addEventListener("click",()=>{$("factory-reset-dialog").close();state.resetTarget=null;});

async function openDeviceConfiguration(node) { state.selectedConfigNode=node.node_id; const current=await api(`/api/nodes/${encodeURIComponent(node.node_id)}/configuration`); $("device-config-title").textContent=`Configure ${node.display_name}`; $("device-cfg-sample").value=current.sample_interval_ms; $("device-cfg-processing").value=current.processing_interval_ms; $("device-cfg-report").value=current.report_interval_ms; $("device-cfg-heartbeat").value=current.heartbeat_interval_ms; $("device-cfg-filter").value=current.filter_type; $("device-cfg-window").value=current.filter_window; $("device-config-summary").textContent="Current persisted settings read from the sensor. Apply performs one write followed by authoritative readback."; $("device-config-dialog").showModal(); }

function renderInstallations() {
  const list = $("installation-list");
  if (!list) return;
  list.replaceChildren();
  state.installations.forEach((item) => { const profile = state.profiles.find((p)=>p.profile_id===item.sensor_profile_id && p.profile_version===item.sensor_profile_version); const card=el("article",undefined,"device-card"); card.append(el("h3",item.display_name),el("div",item.device_id,"equipment-id"),el("p",item.provisioning_state,`state ${item.provisioning_state === "active" ? "connected" : item.provisioning_state}`)); const dl=el("dl"); [["Sensor node",item.node_id],["Profile",profile ? profile.display_name : item.sensor_profile_id],["Version",item.sensor_profile_version],["Interface",item.interface_id],["Calibration",item.calibration_status],["Verification",item.verification_status],["Last valid",time(item.last_valid_reading_at)]].forEach(([a,b])=>dl.append(el("dt",a),el("dd",String(b)))); card.append(dl); const button=el("button","Open details"); button.addEventListener("click",()=>openDetail(item.installation_id)); card.append(button); list.append(card); });
  if (!state.installations.length) list.append(el("p", "No optional equipment deployments have been created. Built-in sensors and live readings are shown below.", "muted"));
}

async function openDetail(id) {
  state.selectedInstallation=id;
  const item=state.installations.find((x)=>x.installation_id===id);
  $("detail-title").textContent=item.display_name;
  $("detail-id").textContent=`${item.device_id} on ${item.node_id} / ${item.interface_id}`;
  $("detail-status").textContent=`Profile ${item.sensor_profile_id} ${item.sensor_profile_version}; ${item.provisioning_state}; calibration ${item.calibration_status}; verification ${item.verification_status}${item.provisioning_error ? `; error ${item.provisioning_error}` : ""}`;
  $("edit-name").value=item.display_name;
  $("edit-location").value=item.location || "";
  $("edit-description").value=item.description || "";
  const retry=$("detail-panel").querySelector('[data-action="retry-installation"]');
  const retryAllowed=Boolean(item.provisioning_error?.startsWith("retry_allowed:"));
  retry.hidden=!retryAllowed;
  retry.disabled=!retryAllowed;
  $("detail-panel").classList.remove("hidden");
  await renderPreview(id,$("channel-list"));
  const history=await api(`/api/sensor-installations/${encodeURIComponent(id)}/history`);
  const box=$("provisioning-history");
  box.replaceChildren();
  history.forEach((row)=>box.append(el("p",`${time(row.created_at)} - ${row.state}${row.error ? ` (${row.error})` : ""}`)));
}
async function renderPreview(id,target) { target.replaceChildren(); try { const preview=await api(`/api/sensor-installations/${encodeURIComponent(id)}/preview`); preview.channels.forEach((row)=>{ const card=el("div",undefined,"channel"); card.append(el("strong",row.channel),el("div",`${row.value ?? row.raw_value ?? "Unavailable"} ${row.unit || ""}`.trim()),el("span",`Quality: ${row.quality}`)); if (preview.calibration_status==="not_configured") card.append(el("span","Calibration: Not configured; engineering value unavailable")); target.append(card); }); if (!preview.channels.length) target.append(el("p","No readings received for this interface.","muted")); } catch(error) { target.append(el("p",error.message,"warning")); } }

function invalidateDraft() { state.draftId=null; $("preview-button").disabled=true; $("apply-button").disabled=true; $("apply-button").hidden=true; $("apply-confirmed").checked=false; $("verification-result").textContent="Not applied."; }
async function populateWizard() { $("wizard-form").reset(); invalidateDraft(); const nodeSelect=$("wizard-node"); nodeSelect.replaceChildren(); state.nodes.forEach((node)=>{ const option=el("option",`${node.display_name} (${node.node_id})`); option.value=node.node_id; nodeSelect.append(option); }); if (!state.nodes.length) { notice("Register a sensor node before adding an attached sensor."); return; } await nodeChanged(); $("wizard-dialog").showModal(); }
async function nodeChanged() { const nodeId=$("wizard-node").value; const node=state.nodes.find((item)=>item.node_id===nodeId); $("wizard-node-info").textContent=`${node.connection_status}; firmware ${node.firmware_version || "not reported"}; compatibility ${node.compatibility_status || "unknown"}`; state.interfaces=await api(`/api/nodes/${encodeURIComponent(nodeId)}/interfaces`); const select=$("wizard-interface"); select.replaceChildren(); state.interfaces.forEach((item)=>{ const unavailable=item.available ? "" : ` (assigned to ${item.assigned_device_id})`; const unsupported=item.configuration_supported ? "" : " (telemetry only; persistent configuration unsupported)"; const option=el("option",`${item.interface_id} - ${item.type}${unavailable}${unsupported}`); option.value=item.interface_id; option.disabled=!item.available || !item.configuration_supported; select.append(option); }); await interfaceChanged(); }
async function interfaceChanged() { const item=state.interfaces.find((entry)=>entry.interface_id===$("wizard-interface").value); if (!item) return; $("wizard-interface-info").textContent=`Capabilities: ${item.capabilities.join(", ")}. Configuration persistence: ${item.configuration_persistence}.`; renderProfileOptions(); }
function eligibleProfiles() { const item=state.interfaces.find((entry)=>entry.interface_id===$("wizard-interface").value); const search=$("profile-search").value.toLowerCase(); return state.profiles.filter((profile)=>profile.interface.type===item?.type && profile.firmware.required_capabilities.every((cap)=>item.capabilities.includes(cap)) && `${profile.manufacturer} ${profile.model} ${profile.category}`.toLowerCase().includes(search)); }
function renderProfileOptions() { const select=$("wizard-profile"); select.replaceChildren(); eligibleProfiles().forEach((profile)=>{ const option=el("option",`${profile.manufacturer} ${profile.model} [${profile.status}]`); option.value=`${profile.profile_id}|${profile.profile_version}`; select.append(option); }); profileChanged(); }
function selectedProfile() { const [id,version]=($("wizard-profile").value || "|").split("|"); return state.profiles.find((profile)=>profile.profile_id===id && profile.profile_version===version); }
function profileChanged() { const profile=selectedProfile(); if (!profile) { $("profile-details").textContent="No compatible profile."; return; } $("profile-details").textContent=`${profile.display_name}. Status: ${profile.status}. Source: ${profile.provenance.source}. ${profile.description}`; const filters=$("cfg-filter"); filters.replaceChildren(); profile.filter.supported.forEach((name)=>{ const option=el("option",name); option.value=name; option.selected=name===profile.filter.default; filters.append(option); }); const raw=profile.conversion.type==="unconfigured"; $("generic-hardware-info").classList.toggle("hidden",!raw); $("calibration-boundary").textContent=raw ? "Generic raw path: provide known hardware information below. Calibration, engineering conversion, and alarms remain disabled." : `Conversion: ${profile.conversion.type}; engineering units are profile-defined.`; const wiring=$("wiring-notes"); wiring.replaceChildren(); if (profile.interface.wiring_notes.length) profile.interface.wiring_notes.forEach((note)=>wiring.append(el("p",note))); else wiring.append(el("p","No wiring guidance is available for this unknown sensor. Consult its manufacturer datasheet and validated signal-conditioning design.","warning")); updateReview(); }
function configuration() { const raw=selectedProfile()?.conversion.type==="unconfigured"; return { sample_interval_ms:Number($("cfg-sample").value), processing_interval_ms:Number($("cfg-processing").value), report_interval_ms:Number($("cfg-report").value), heartbeat_interval_ms:Number($("cfg-heartbeat").value), filter_type:$("cfg-filter").value, filter_window:Number($("cfg-window").value), change_deadband:null, calibration_enabled:false, calibration_gain:null, calibration_offset:null, alarms_enabled:false, warning_low:null, warning_high:null, alarm_low:null, alarm_high:null, hardware_information:raw ? {manufacturer:$("generic-manufacturer").value || null,model:$("generic-model").value || null,datasheet:$("generic-datasheet").value || null,electrical_interface:$("generic-interface").value || null,measurement_range:$("generic-range").value || null,signal_conditioning:$("generic-conditioning").value || null} : null }; }
function updateReview() { const profile=selectedProfile(); if (!profile) return; $("review-summary").textContent=`Node: ${$("wizard-node").value}; interface: ${$("wizard-interface").value}; equipment ID: ${$("wizard-device-id").value || "not entered"}; profile: ${profile.profile_id} ${profile.profile_version}; raw unit: ${profile.measurement_channels.map((c)=>c.raw_unit).join(", ")}; engineering: ${profile.conversion.type === "unconfigured" ? "Unavailable / uncalibrated" : profile.measurement_channels.map((c)=>c.engineering_unit).join(", ")}; sampling/reporting: ${$("cfg-sample").value}/${$("cfg-report").value} ms; filter: ${$("cfg-filter").value}; alarms: disabled.`; }

$("create-draft-button").addEventListener("click",async()=>{ invalidateDraft(); try { const profile=selectedProfile(); if (!profile || !$("wiring-confirmed").checked) throw new Error("Select a compatible profile and confirm wiring/safety first."); const body={node_id:$("wizard-node").value,device_id:$("wizard-device-id").value,display_name:$("wizard-display-name").value,location:$("wizard-location").value || null,description:$("wizard-description").value || null,sensor_profile_id:profile.profile_id,sensor_profile_version:profile.profile_version,interface_id:$("wizard-interface").value,configuration:configuration()}; const draft=await api("/api/sensor-installations",{method:"POST",body:JSON.stringify(body)}); const validated=await api(`/api/sensor-installations/${encodeURIComponent(draft.installation_id)}/validate`,{method:"POST"}); if(validated.provisioning_state!=="ready_to_apply")throw new Error("Server did not issue a current validated draft."); state.draftId=draft.installation_id; $("preview-button").disabled=false; $("apply-button").disabled=false; $("apply-button").hidden=false; $("preview-results").textContent="Draft validated. Preview live data before applying."; await refresh(); } catch(error) { invalidateDraft(); $("preview-results").textContent=error.message; } });
$("preview-button").addEventListener("click",()=>renderPreview(state.draftId,$("preview-results")));
$("wizard-form").addEventListener("submit",async(event)=>{ event.preventDefault(); if (!state.draftId || !$("apply-confirmed").checked || $("apply-button").disabled)return; $("apply-button").disabled=true; try { const result=await api(`/api/sensor-installations/${encodeURIComponent(state.draftId)}/apply`,{method:"POST"}); invalidateDraft(); $("verification-result").textContent=`Verified and active: ${result.display_name} (${result.device_id}).`; await refresh(); } catch(error) { const detail=error.response?.detail; const uncertain=detail?.next_action==="authoritative_readback"; invalidateDraft(); $("verification-result").textContent=uncertain ? "Reconciling device state. Retry is disabled until authoritative readback classifies the previous transaction." : `Configuration failed: ${detail?.message || error.message}. Revalidate before another Apply.`; } });

$("register-node-button").addEventListener("click",()=>{ $("node-form").reset(); renderCommissioningState(); $("discovery-list").replaceChildren(); $("node-dialog").showModal(); });
$("scan-button").addEventListener("click",async()=>{ const list=$("discovery-list"); renderCommissioningState(); list.replaceChildren(el("p","Checking assignment state...","muted")); try { const scan=await api("/api/devices/scan",{method:"POST"}); await new Promise((resolve)=>setTimeout(resolve,(scan.scan_duration_seconds+0.5)*1000)); const discoveries=await api("/api/commissioning/discoveries"); list.replaceChildren(); discoveries.forEach((item)=>{ const assigned=item.reported_node_id && item.reported_node_id!=="UNASSIGNED-MG24"; const label=`${item.name || "Unnamed"} - ${item.address} - ${item.rssi ?? "?"} dBm\n${assigned ? `Assigned: ${item.reported_node_id}` : item.commissioning_state}`; const button=el("button",label,`discovery ${item.action === "commission" ? "" : "incompatible"}`); button.type="button"; button.addEventListener("click",async()=>{ document.querySelectorAll(".discovery").forEach((node)=>node.classList.remove("selected")); button.classList.add("selected"); const view=renderCommissioningState(item,"classified"); if(view.action==="view_or_reconnect"){ await api(`/api/devices/${encodeURIComponent(item.local_device_id)}/connect`,{method:"POST"}); $("node-dialog").close(); await refresh(); $("node-list").scrollIntoView({behavior:"smooth"}); notice(`Reconnected ${item.local_device_id}.`); } }); list.append(button); }); if(!discoveries.some((item)=>item.action==="commission")) list.append(el("p","No unassigned sensor is available. Assigned devices retain identity across firmware installation; use Reconnect/View Sensor or the documented recovery/import path.","warning")); } catch(error) { renderCommissioningState(null,"unavailable"); list.replaceChildren(el("p",error.message,"warning")); } });
[$("new-node-id"), $("new-node-name"), $("new-node-location")].forEach((input)=>input.addEventListener("input",updateCommissioningSubmit));
$("node-form").addEventListener("submit",async(event)=>{ event.preventDefault(); if(!currentCommissioningEligible() || !$("node-form").checkValidity()){notice("Authoritative unassigned state and valid fields are required before provisioning.");updateCommissioningSubmit();return;} state.commissioningActive=true; updateCommissioningSubmit(); try { const key=crypto.randomUUID().replaceAll("-","").slice(0,16); await api("/api/commissioning/nodes",{method:"POST",body:JSON.stringify({node_id:$("new-node-id").value,display_name:$("new-node-name").value,location:$("new-node-location").value || null,discovery_address:state.selectedDiscovery.address,idempotency_key:key,configuration:{sample_interval_ms:100,processing_interval_ms:100,report_interval_ms:100,heartbeat_interval_ms:30000,filter_type:"ema",filter_window:2,change_deadband:null,calibration_enabled:false,calibration_gain:null,calibration_offset:null,alarms_enabled:false,warning_low:null,warning_high:null,alarm_low:null,alarm_high:null,hardware_information:null}})}); $("node-dialog").close(); await refresh(); } catch(error){notice(error.message);} finally { state.commissioningActive=false; updateCommissioningSubmit(); } });
$("import-node-button").addEventListener("click",async()=>{ const selected=state.selectedDiscovery; if(!selected || selected.commissioning_state!=="assigned_elsewhere")return; $("import-node-button").disabled=true; try { const node=await api("/api/commissioning/import",{method:"POST",body:JSON.stringify({discovery_address:selected.address,display_name:selected.reported_node_id})}); $("node-dialog").close(); await refresh(); notice(`Imported ${node.device_id} without changing the sensor.`); } catch(error){notice(error.message);} finally { $("import-node-button").disabled=false; } });

$("usb-detect-button").addEventListener("click",async()=>{ const list=$("usb-board-list"); list.replaceChildren(el("p","Detecting supported local boards...","muted")); try { const boards=await api("/api/firmware/boards"); list.replaceChildren(); boards.forEach((item)=>{ const button=el("button",`${item.board_type} - ${item.hardware_serial} - ${item.com_port || "no COM port"}`,"discovery"); button.type="button"; button.addEventListener("click",()=>{state.selectedUsbBoard=item;$("firmware-install-button").disabled=false;});list.append(button);}); if(!boards.length)list.append(el("p","No supported USB board detected.","muted")); } catch(error){list.replaceChildren(el("p",error.message,"warning"));} });
$("firmware-install-button").addEventListener("click",async()=>{ if(!state.selectedUsbBoard)return; const packages=await api("/api/firmware/packages"); if(packages.length!==1)throw new Error("Exactly one approved firmware package is required for this workflow."); const box=$("firmware-progress"); box.textContent=`Validating ${packages[0].package_id}\nSHA-256 ${packages[0].sha256}`; try { let op=await api("/api/firmware/install",{method:"POST",body:JSON.stringify({hardware_serial:state.selectedUsbBoard.hardware_serial,package_id:packages[0].package_id})}); while(!["complete","failed"].includes(op.state)){await new Promise((resolve)=>setTimeout(resolve,500));op=await api(`/api/firmware/operations/${op.operation_id}`);box.textContent=op.progress.join("\n");} if(op.state!=="complete")throw new Error(op.error); box.textContent+=`\nFirmware installed and same board re-enumerated. Existing identity and configuration were preserved. Scan BLE to determine whether it is assigned or unassigned.`; }catch(error){box.textContent+=`\nFAILED: ${error.message}`;} });

$("add-button").addEventListener("click",()=>{ $("node-form").reset(); renderCommissioningState(); $("discovery-list").replaceChildren(); $("node-dialog").showModal(); });
const requestedDashboardAction = new URLSearchParams(location.search).get("action");
if (requestedDashboardAction === "add-sensor" || requestedDashboardAction === "firmware") {
  $("add-button").click();
  if (requestedDashboardAction === "firmware") requestAnimationFrame(() => $("usb-detect-button").focus());
}
$("wizard-node").addEventListener("change",()=>nodeChanged().catch((error)=>notice(error.message)));
$("wizard-interface").addEventListener("change",()=>interfaceChanged().catch((error)=>notice(error.message)));
$("wizard-profile").addEventListener("change",profileChanged); $("profile-search").addEventListener("input",renderProfileOptions); $("wizard-form").addEventListener("input",()=>{if(state.draftId)invalidateDraft();updateReview();});
document.querySelector('[data-action="close-node"]').addEventListener("click",()=>$("node-dialog").close()); document.querySelector('[data-action="close-wizard"]').addEventListener("click",()=>$("wizard-dialog").close()); document.querySelector('[data-action="close-detail"]').addEventListener("click",()=>$("detail-panel").classList.add("hidden"));
document.querySelector('[data-action="close-device-config"]').addEventListener("click",()=>$("device-config-dialog").close());
$("device-config-form").addEventListener("submit",async(event)=>{ event.preventDefault(); if(!state.selectedConfigNode || !event.currentTarget.checkValidity())return; const button=$("device-config-apply"); button.disabled=true; const transactionId=crypto.randomUUID().replaceAll("-","").slice(0,16); try { const result=await api(`/api/nodes/${encodeURIComponent(state.selectedConfigNode)}/configuration`,{method:"POST",body:JSON.stringify({transaction_id:transactionId,sample_interval_ms:Number($("device-cfg-sample").value),processing_interval_ms:Number($("device-cfg-processing").value),report_interval_ms:Number($("device-cfg-report").value),heartbeat_interval_ms:Number($("device-cfg-heartbeat").value),filter_type:$("device-cfg-filter").value,filter_window:Number($("device-cfg-window").value),change_deadband:null,calibration_enabled:false,calibration_gain:null,calibration_offset:null,alarms_enabled:false,warning_low:null,warning_high:null,alarm_low:null,alarm_high:null,hardware_information:null})}); $("device-config-summary").textContent=`Verified by sensor readback (${result.acknowledgement.code}). Live telemetry is reconnecting.`; } catch(error){ $("device-config-summary").textContent=error.response?.detail?.message || error.message; } finally { button.disabled=false; } });
$("edit-form").addEventListener("submit",async(event)=>{ event.preventDefault(); try { await api(`/api/sensor-installations/${encodeURIComponent(state.selectedInstallation)}`,{method:"PATCH",body:JSON.stringify({display_name:$("edit-name").value,location:$("edit-location").value || null,description:$("edit-description").value || null})}); await refresh(); await openDetail(state.selectedInstallation); } catch(error){notice(error.message);} });
document.querySelector('[data-action="validate-installation"]').addEventListener("click",async()=>{try{await api(`/api/sensor-installations/${encodeURIComponent(state.selectedInstallation)}/validate`,{method:"POST"});await refresh();await openDetail(state.selectedInstallation);}catch(error){notice(error.message);}});
document.querySelector('[data-action="retry-installation"]').addEventListener("click",async()=>{try{await api(`/api/sensor-installations/${encodeURIComponent(state.selectedInstallation)}/apply`,{method:"POST"});await refresh();await openDetail(state.selectedInstallation);}catch(error){notice(error.message);}});
document.querySelector('[data-action="disable-installation"]').addEventListener("click",async()=>{try{await api(`/api/sensor-installations/${encodeURIComponent(state.selectedInstallation)}/disable`,{method:"POST"});await refresh();await openDetail(state.selectedInstallation);}catch(error){notice(error.message);}});

let refreshTimer=null;
function scheduleRefresh() { if(refreshTimer)return; refreshTimer=setTimeout(()=>{refreshTimer=null;refresh().catch(()=>{});},500); }
function websocket() { const protocol=location.protocol==="https:"?"wss":"ws"; const socket=new WebSocket(`${protocol}://${location.host}/ws/telemetry`); socket.addEventListener("open",()=>{$("gateway-status").textContent="Live";socket.send("ready");}); socket.addEventListener("message",scheduleRefresh); socket.addEventListener("close",()=>{$("gateway-status").textContent="Reconnecting...";setTimeout(websocket,2000);}); }
window.MG24Dashboard = { api, refresh, notice, time };
window.MG24ResetReregister.init();
refresh().then(websocket).catch((error)=>{notice(error.message);$("gateway-status").textContent="Unavailable";});
