"use strict";

window.MG24ResetReregister = (() => {
  const labels = [
    ["sensor_selected", "Sensor selected"], ["usb_connection_required", "Connect USB"],
    ["physical_identity_verified", "Identity verified"], ["configuration_backup_ready", "Backup ready"],
    ["reset_confirmation_required", "Confirm reset"], ["reset_in_progress", "Resetting"],
    ["waiting_for_usb_reenumeration", "Waiting for USB"], ["post_reset_verification", "Verify reset"],
    ["unprovisioned_ready_for_registration", "Ready to register"],
    ["registration_details_required", "Registration details"], ["searching_for_reset_sensor_ble", "Find over BLE"],
    ["ble_identity_matched", "BLE candidate selected"], ["provisioning_in_progress", "Provisioning"],
    ["gateway_registration_in_progress", "Adding to gateway"],
    ["network_verification_in_progress", "Verify network"], ["complete", "Complete"],
  ];
  let dialog; let operation; let selectedNode; let pending = false; let pollTimer; let detectedBoards = [];
  const id = (name) => document.getElementById(name);
  const api = (...args) => window.MG24Dashboard.api(...args);
  const post = (path, body = {}) => api(path, {method: "POST", body: JSON.stringify(body)});
  const config = () => ({sample_interval_ms:100, processing_interval_ms:100, report_interval_ms:100,
    heartbeat_interval_ms:30000, filter_type:"ema", filter_window:2, change_deadband:null,
    calibration_enabled:false, calibration_gain:null, calibration_offset:null, alarms_enabled:false,
    warning_low:null, warning_high:null, alarm_low:null, alarm_high:null, hardware_information:null});

  function markup() {
    return `<dialog id="reset-reregister-dialog" class="wizard" aria-labelledby="rr-title">
      <div class="panel-header"><div><span class="eyebrow">GUIDED RECOVERY</span><h2 id="rr-title">Reset and Re-register Sensor</h2></div>
      <button type="button" id="rr-close" class="quiet" aria-label="Close reset and re-register workflow">Close</button></div>
      <p>This guided workflow resets the physical sensor over USB, verifies its out-of-box state, then provisions and adds it back. Remove, Restore/Reapprove, and Factory Reset remain separate actions.</p>
      <ol id="rr-steps" class="workflow-stepper" aria-label="Workflow progress"></ol>
      <div id="rr-live" class="workflow-progress" role="status" aria-live="polite"></div>
      <div id="rr-summary" class="summary"></div><div id="rr-error" class="warning" role="alert"></div>
      <section id="rr-controls"></section>
      <div class="actions"><button type="button" id="rr-primary" class="primary"></button>
      <button type="button" id="rr-retry">Retry</button><button type="button" id="rr-finish">Finish later</button></div>
    </dialog>`;
  }
  function field(label, input) { const wrap = document.createElement("label"); wrap.textContent = label; wrap.append(input); return wrap; }
  function input(name, value = "") { const node = document.createElement("input"); node.id = name; node.value = value || ""; node.maxLength = name.includes("name") ? 160 : 240; return node; }
  function safeResult() { return operation?.result || {}; }
  function renderSteps() {
    const list = id("rr-steps"); list.replaceChildren(); const current = labels.findIndex(([key]) => key === operation.state);
    labels.forEach(([key, text], index) => { const item = document.createElement("li"); item.textContent = text;
      if (index < current || operation.state === "complete") item.className = "complete";
      else if (index === current) item.className = "current";
      if (["recoverable_error", "manual_recovery_required"].includes(operation.state) && index === Math.max(0, current)) item.className = "failed";
      list.append(item); });
  }
  function setPrimary(label, action, enabled = true) { const button = id("rr-primary"); button.textContent = label;
    button.disabled = pending || !enabled; button.onclick = () => run(action); }
  async function run(action) { if (pending) return; pending = true; render(); try { await action(); }
    catch (error) { id("rr-error").textContent = error.message; } finally { pending = false; render(); } }
  function summary() {
    const result = safeResult();
    return `${selectedNode?.display_name || operation.source_display_name} (${operation.source_device_id})\n` +
      `Immutable hardware ID: ${operation.hardware_id}\nBLE address: ${operation.source_ble_address || "Unknown"}\n` +
      `Firmware: ${result.post_reset?.firmware_version || result.firmware_version || "Unknown"}\n` +
      `Connectivity: ${selectedNode?.connection_status || "Reconcile on resume"}\n` +
      `Lifecycle: ${selectedNode?.lifecycle_state || "Saved operation"}; physical reset: ${selectedNode?.factory_reset_status || operation.state}\n` +
      `Last telemetry: ${selectedNode?.last_seen_at ? window.MG24Dashboard.time(selectedNode.last_seen_at) : "Never"}\n` +
      `Installation/location: ${selectedNode?.location || "Not assigned"}\n` +
      `State: ${operation.state.replaceAll("_", " ")}\nBackup: ${operation.backup_status}`;
  }
  function render() {
    if (!operation) return; renderSteps(); id("rr-summary").textContent = summary();
    id("rr-live").textContent = operation.progress?.at(-1) || "Ready";
    id("rr-error").textContent = operation.error?.message || ""; id("rr-controls").replaceChildren();
    id("rr-retry").hidden = !["recoverable_error", "network_verification_in_progress"].includes(operation.state);
    id("rr-retry").onclick = () => refreshOperation(); id("rr-finish").onclick = () => dialog.close();
    const controls = id("rr-controls");
    if (operation.state === "usb_connection_required") {
      const box = document.createElement("div"); box.id = "rr-usb-list"; controls.append(Object.assign(document.createElement("p"),
        {textContent:"Connect the selected XIAO MG24 Sense over USB. Detection is loopback-only and matches immutable hardware identity."}), box);
      detectedBoards.forEach((board)=>{const button=document.createElement("button");button.type="button";
        button.textContent=`${board.port} — ${board.hardware_id||"identity unavailable"} — ${board.node_id||"unprovisioned"} — firmware ${board.firmware_version||"unknown"}`;
        button.disabled=!board.identity_match;button.setAttribute("aria-label",`${board.identity_match?"Select":"Mismatched"} USB sensor on ${board.port}`);
        button.onclick=()=>run(async()=>{operation=await post(`/api/reset-reregister/${operation.operation_id}/select-usb`,{port:board.port,expected_hardware_id:operation.hardware_id});});box.append(button);});
      setPrimary("Detect USB sensor", detectUsb);
    } else if (operation.state === "physical_identity_verified") {
      controls.append(Object.assign(document.createElement("p"), {textContent:"Back up supported application configuration before reset. Secret material is excluded."}));
      setPrimary("Create secure backup", async () => { operation = await post(`/api/reset-reregister/${operation.operation_id}/backup`); });
    } else if (operation.state === "configuration_backup_ready") {
      const confirm = input("rr-hardware-confirm"); confirm.autocomplete = "off";
      controls.append(Object.assign(document.createElement("p"), {className:"warning", textContent:
        "Factory reset clears sensor ID, name, provisioning, and application configuration. Firmware, bootloader, immutable identity, calibration, and historical gateway telemetry remain."}),
        field(`Type ${operation.hardware_id} to confirm the selected physical sensor`, confirm));
      setPrimary("Prepare device-bound reset", prepareReset, false);
      confirm.addEventListener("input",()=>{id("rr-primary").disabled=pending||confirm.value!==operation.hardware_id;});
    } else if (operation.state === "reset_confirmation_required") {
      controls.append(Object.assign(document.createElement("p"), {className:"warning", textContent:"Reset is ready. Cancellation is safe only before execution."}));
      setPrimary("Execute USB factory reset", executeReset);
      const cancel=document.createElement("button");cancel.type="button";cancel.textContent="Cancel prepared reset";cancel.onclick=()=>run(cancelReset);controls.append(cancel);
    } else if (["reset_in_progress", "waiting_for_usb_reenumeration", "post_reset_verification"].includes(operation.state)) {
      controls.append(Object.assign(document.createElement("p"), {textContent:"The destructive command will not be repeated. This screen only polls durable workflow status."}));
      setPrimary("Reset in progress", async()=>{}, false); schedulePoll();
      const reconcile=document.createElement("button");reconcile.type="button";reconcile.textContent="Reconcile after gateway restart";
      reconcile.onclick=()=>run(async()=>{operation=await post(`/api/reset-reregister/${operation.operation_id}/reconcile-reset`,{expected_hardware_id:operation.hardware_id});});controls.append(reconcile);
    } else if (operation.state === "unprovisioned_ready_for_registration" || operation.state === "registration_details_required") {
      const choice = document.createElement("select"); choice.id="rr-choice"; choice.append(new Option("Reuse previous registration (Restore/Reapprove)","restore"), new Option("Register as a new sensor","new"));
      const sensorId=input("rr-device-id",operation.source_device_id), name=input("rr-device-name",operation.source_display_name), location=input("rr-location",selectedNode?.location || "");
      controls.append(field("Registration choice",choice),field("Sensor ID",sensorId),field("Human-readable name",name),field("Installation/location",location),
        Object.assign(document.createElement("p"),{className:"muted",textContent:"New registration leaves old telemetry attached to the archived record. Previous identity is never reused automatically."}));
      choice.addEventListener("change",()=>{ if(choice.value==="restore"){sensorId.value=operation.source_device_id;name.value=operation.source_display_name;} });
      setPrimary("Confirm registration details", async()=>{ operation=await post(`/api/reset-reregister/${operation.operation_id}/registration`,
        {choice:choice.value,device_id:sensorId.value,display_name:name.value,location:location.value||null,configuration:config()}); });
    } else if (operation.state === "searching_for_reset_sensor_ble") {
      const box=document.createElement("div");box.id="rr-ble-list";controls.append(Object.assign(document.createElement("p"),{textContent:"Scan for the reset sensor in unprovisioned onboarding mode. Multiple candidates require explicit selection."}),box);
      setPrimary("Scan for reset sensor", scanBle);
    } else if (operation.state === "ble_identity_matched") {
      controls.append(Object.assign(document.createElement("p"),{textContent:"Provisioning writes configuration first and identity last, then verifies read-back before gateway registration."}));
      setPrimary("Provision and add to network", async()=>{operation=await post(`/api/reset-reregister/${operation.operation_id}/provision`);});
    } else if (operation.state === "network_verification_in_progress") {
      controls.append(Object.assign(document.createElement("p"),{textContent:"Registered—waiting for first telemetry. You may retry the connection check or finish later."}));
      setPrimary("Retry connection verification", async()=>{operation=await post(`/api/reset-reregister/${operation.operation_id}/verify-network`);});
    } else if (operation.state === "complete") {
      const result=safeResult(); controls.append(Object.assign(document.createElement("p"),{textContent:
        `Factory reset: Verified\nRegistration: ${operation.registration_choice === "restore" ? "Restored" : "New"}\nGateway: ${result.gateway_registration}\nBLE: ${result.ble_connection}\nFirst telemetry: ${result.first_telemetry}\nLifecycle operation: ${operation.operation_id}`}));
      setPrimary("Open Sensor Dashboard", async()=>{dialog.close();await window.MG24Dashboard.refresh();});
      const download=document.createElement("button");download.type="button";download.textContent="Download operation summary";
      download.onclick=()=>{const clean={operation_id:operation.operation_id,hardware_id:operation.hardware_id,
        firmware_version:result.post_reset?.firmware_version,registration_choice:operation.registration_choice,
        sensor_id:result.sensor_id,sensor_name:result.sensor_name,location:result.location,gateway_registration:result.gateway_registration,
        ble_connection:result.ble_connection,first_telemetry:result.first_telemetry,backup:operation.backup_status};
        const url=URL.createObjectURL(new Blob([JSON.stringify(clean,null,2)],{type:"application/json"}));const anchor=document.createElement("a");anchor.href=url;anchor.download=`sensor-operation-${operation.operation_id}.json`;anchor.click();URL.revokeObjectURL(url);};controls.append(download);
    } else { setPrimary("Resume setup", refreshOperation); }
  }
  async function detectUsb() { const found=await post(`/api/reset-reregister/${operation.operation_id}/detect-usb`);detectedBoards=found.boards;
    const matches=found.boards.filter((board)=>board.identity_match);if(matches.length===1)operation=await post(`/api/reset-reregister/${operation.operation_id}/select-usb`,{port:matches[0].port,expected_hardware_id:operation.hardware_id});
    else if(!matches.length)throw new Error("No USB sensor matches the selected immutable hardware ID. Detected devices are shown for diagnosis."); }
  async function prepareReset(){operation=await post(`/api/reset-reregister/${operation.operation_id}/prepare-reset`,{port:operation.selected_port,expected_hardware_id:operation.hardware_id,typed_hardware_id:operation.hardware_id});}
  async function executeReset(){operation=await post(`/api/reset-reregister/${operation.operation_id}/execute-reset`,{port:operation.selected_port,expected_hardware_id:operation.hardware_id,typed_hardware_id:operation.hardware_id});schedulePoll();}
  async function cancelReset(){operation=await post(`/api/reset-reregister/${operation.operation_id}/cancel-reset`,{port:operation.selected_port,expected_hardware_id:operation.hardware_id,typed_hardware_id:operation.hardware_id});}
  async function scanBle(){const found=await post(`/api/reset-reregister/${operation.operation_id}/scan-ble`);const box=id("rr-ble-list");box.replaceChildren();
    found.candidates.forEach((candidate)=>{const button=document.createElement("button");button.type="button";button.textContent=`${candidate.name||"Unnamed"} — ${candidate.address} — ${candidate.rssi??"?"} dBm`;button.onclick=()=>run(async()=>{operation=await post(`/api/reset-reregister/${operation.operation_id}/select-ble`,{address:candidate.address});});box.append(button);});
    if(!found.candidates.length) throw new Error("No unprovisioned MG24 sensor was found. Retry scan without repeating reset."); }
  async function refreshOperation(){operation=await api(`/api/reset-reregister/${operation.operation_id}`);render();}
  function schedulePoll(){clearTimeout(pollTimer);pollTimer=setTimeout(async()=>{await refreshOperation();if(["reset_in_progress","waiting_for_usb_reenumeration","post_reset_verification"].includes(operation.state))schedulePoll();},750);}
  async function open(node){selectedNode=node;operation=await post("/api/reset-reregister/start",{device_id:node.node_id});dialog.showModal();render();}
  async function resume(){const rows=await api("/api/reset-reregister/incomplete");if(rows.length===1){operation=rows[0];selectedNode=null;dialog.showModal();render();}}
  function init(){document.body.insertAdjacentHTML("beforeend",markup());dialog=id("reset-reregister-dialog");id("rr-close").onclick=()=>dialog.close();dialog.addEventListener("close",()=>clearTimeout(pollTimer));resume().catch(()=>{});}
  return {init,open};
})();
