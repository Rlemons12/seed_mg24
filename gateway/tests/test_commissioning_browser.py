import json
import os
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

playwright = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).parents[2]
TEMPLATE = ROOT / "gateway/app/templates/index.html"
TEMPLATES = TEMPLATE.parent
STATIC = ROOT / "gateway/app/static"


def rendered_dashboard() -> str:
    environment = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape())
    def url_for(name, path=None):
        if name == "static":
            return f"/static{path}"
        return {"dashboard": "/", "installations_page": "/installations", "system_health_page": "/system-health"}[name]

    return environment.get_template("index.html").render(
        dashboard_build="browser-test",
        current_module="overview",
        url_for=url_for,
    )


def chrome_path() -> Path:
    candidates = (
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    )
    executable = next((path for path in candidates if path.exists()), None)
    if executable is None:
        pytest.skip("Chrome or Edge is required for rendered commissioning-state tests")
    return executable


@pytest.fixture
def dashboard_page():
    posts = []
    with playwright.sync_playwright() as manager:
        browser = manager.chromium.launch(executable_path=str(chrome_path()), headless=True)
        page = browser.new_page()
        page.add_init_script(
            "class SilentSocket { addEventListener() {} send() {} } window.WebSocket = SilentSocket; "
            "Object.defineProperty(crypto, 'randomUUID', "
            "{value: () => '12345678-1234-1234-1234-123456789abc'});"
        )

        def route(request):
            path = request.request.url.split("?", 1)[0]
            if request.request.method == "POST" and path.endswith("/api/commissioning/nodes"):
                posts.append(json.loads(request.request.post_data or "{}"))
                request.fulfill(status=200, content_type="application/json", body=json.dumps({"device_id": "MG24-TEST"}))
            elif request.request.method == "POST" and path.endswith("/api/reset-reregister/start"):
                posts.append({"workflow_start": json.loads(request.request.post_data or "{}")})
                identity_mode = any(row.get("identity_ui") for row in posts)
                request.fulfill(status=200, content_type="application/json", body=json.dumps({
                    "operation_id":"0123456789abcdef0123456789abcdef",
                    "state":"searching_for_reset_sensor_ble" if identity_mode else "usb_connection_required",
                    "source_record_id":1, "source_device_id":"MG24-0002", "source_display_name":"XIAO MG24 Sense 01",
                    "hardware_id":"0x0123456789ABCDEF", "source_ble_address":"AA:BB:CC:DD:EE:02",
                    "selected_port":None, "backup_status":"pending", "registration_choice":None,
                    "target_device_id":None, "target_display_name":None, "target_location":None,
                    "target_ble_address":None, "progress":[], "result":{"firmware_version":"0.1.0"}, "error":None,
                    "started_at":"2026-08-09T00:00:00Z", "updated_at":"2026-08-09T00:00:00Z",
                }))
            elif request.request.method == "POST" and path.endswith("/scan-ble"):
                request.fulfill(status=200, content_type="application/json", body=json.dumps({
                    "operation_id":"0123456789abcdef0123456789abcdef", "state":"ble_identity_matched",
                    "source_record_id":1, "source_device_id":"MG24-0002", "source_display_name":"XIAO MG24 Sense 01",
                    "hardware_id":"0x0123456789ABCDEF", "source_ble_address":"AA:BB:CC:DD:EE:02",
                    "selected_port":"COM10", "backup_status":"complete", "registration_choice":"restore",
                    "target_device_id":"MG24-0002", "target_display_name":"XIAO MG24 Sense 01",
                    "target_location":"Boiler room", "target_ble_address":"AA:BB:CC:DD:EE:20", "progress":[],
                    "result":{"firmware_version":"0.1.0"}, "error":None,
                    "started_at":"2026-08-09T00:00:00Z", "updated_at":"2026-08-09T00:00:00Z",
                    "expected_onboarding_identity_hint":"7e6d1066…54a8", "candidates":[
                        {"address":"AA:BB:CC:DD:EE:20","name":"XIAO-MG24-Sense","rssi":-61,
                         "verification_status":"verified_match","label":"Verified physical sensor",
                         "reason":"BLE identity exactly matches the USB-verified sensor.","provisioning_allowed":True,
                         "firmware_version":"0.1.0","protocol_version":"1.0.0"},
                        {"address":"AA:BB:CC:DD:EE:99","name":"XIAO-MG24-Sense","rssi":-18,
                         "verification_status":"non_match","label":"Different sensor",
                         "reason":"BLE identity does not match the USB-verified sensor.","provisioning_allowed":False,
                         "firmware_version":"0.1.0","protocol_version":"1.0.0"}
                    ]
                }))
            elif "/static/css/" in path:
                relative = path.split("/static/", 1)[1]
                request.fulfill(path=STATIC / relative, content_type="text/css")
            elif path.endswith("/static/styles.css"):
                request.fulfill(path=STATIC / "styles.css", content_type="text/css")
            elif "/static/js/" in path:
                relative = path.split("/static/", 1)[1]
                request.fulfill(path=STATIC / relative, content_type="application/javascript")
            elif path.endswith("/static/onboarding_state.js"):
                request.fulfill(path=STATIC / "onboarding_state.js", content_type="application/javascript")
            elif path.endswith("/static/reset_reregister.js"):
                request.fulfill(path=STATIC / "reset_reregister.js", content_type="application/javascript")
            elif path.endswith("/static/vibration_monitoring.js"):
                request.fulfill(path=STATIC / "vibration_monitoring.js", content_type="application/javascript")
            elif path.endswith("/static/app.js"):
                request.fulfill(path=STATIC / "app.js", content_type="application/javascript")
            elif path.endswith("/api/nodes"):
                request.fulfill(status=200, content_type="application/json", body=json.dumps([{
                    "node_id": "MG24-0002", "display_name": "XIAO MG24 Sense 01",
                    "connection_status": "connected", "compatibility_status": "compatible",
                    "firmware_version": "0.1.0", "protocol_version": "1.0.0",
                    "hardware_id": "0x0123456789ABCDEF", "ble_address": "AA:BB:CC:DD:EE:02",
                    "lifecycle_state": "active", "factory_reset_status": "not_requested", "location": "Boiler room",
                }]))
            elif path.endswith("/api/device-lifecycle/removed"):
                request.fulfill(status=200, content_type="application/json", body="[]")
            elif path.endswith("/api/reset-reregister/incomplete"):
                request.fulfill(status=200, content_type="application/json", body="[]")
            elif path.endswith("/api/devices/MG24-0002/readings/latest"):
                request.fulfill(status=200, content_type="application/json", body=json.dumps([
                    {"channel": "analog_1", "normalized_value": 415, "unit": "adc_count",
                     "quality": "uncalibrated", "received_at": "2026-08-08T20:02:20Z"},
                    {"channel": "angular_velocity_x", "normalized_value": 0.28, "unit": "dps",
                     "quality": "good", "received_at": "2026-08-08T20:02:20Z"},
                    {"channel": "acceleration_x", "normalized_value": 0.896, "unit": "g",
                     "quality": "good", "received_at": "2026-08-08T20:02:20Z"},
                    {"channel": "battery_voltage", "normalized_value": 4.01, "unit": "V",
                     "quality": "good", "received_at": "2026-08-08T20:02:20Z"},
                    {"channel": "analog_0", "normalized_value": 662, "unit": "adc_count",
                     "quality": "uncalibrated", "received_at": "2026-08-08T20:02:20Z"},
                ]))
            elif path.endswith("/api/sensor-installations") or path.endswith("/api/sensor-profiles"):
                request.fulfill(status=200, content_type="application/json", body="[]")
            elif path.endswith("/preview"):
                request.fulfill(status=200, content_type="application/json", body=json.dumps({"channels": []}))
            elif path.endswith("/history"):
                request.fulfill(status=200, content_type="application/json", body="[]")
            elif path == "http://dashboard.test/":
                request.fulfill(status=200, content_type="text/html", body=rendered_dashboard())
            else:
                request.fulfill(status=404, body="")

        page.route("**/*", route)
        page.goto("http://dashboard.test/")
        page.click("#add-button")
        yield page, posts
        browser.close()


def render(page, item=None, phase="classified"):
    page.evaluate("([item, phase]) => renderCommissioningState(item, phase)", [item, phase])


def assert_no_commissioning_action(page, posts):
    button = page.locator("#provision-node-button")
    assert button.count() == 1
    assert button.is_hidden()
    assert button.is_disabled()
    properties = button.evaluate(
        "node => ({hidden: node.hidden, aria: node.getAttribute('aria-disabled'), "
        "tabIndex: node.tabIndex, display: getComputedStyle(node).display, "
        "visibility: getComputedStyle(node).visibility, "
        "pointerEvents: getComputedStyle(node).pointerEvents})"
    )
    assert properties == {"hidden": True, "aria": "true", "tabIndex": -1,
                          "display": "none", "visibility": "visible", "pointerEvents": "auto"}
    page.locator("#new-node-id").press("Enter")
    page.locator("#node-form").evaluate("form => form.dispatchEvent(new SubmitEvent('submit', {bubbles: true, cancelable: true}))")
    page.wait_for_timeout(50)
    assert posts == []


def test_reset_reregister_opens_one_accessible_guided_workflow(dashboard_page):
    page, posts = dashboard_page
    page.get_by_role("button", name="Close").click()
    page.click('[data-node-id="MG24-0002"] .mg-module-sensor-card__toggle')
    page.get_by_role("button", name="Reset and Re-register").click()
    dialog = page.locator("#reset-reregister-dialog")
    dialog.wait_for(state="visible")
    assert dialog.is_visible()
    assert dialog.get_by_role("heading", name="Reset and Re-register Sensor").is_visible()
    assert dialog.locator(".workflow-stepper li").count() == 16
    assert dialog.get_by_role("status").count() == 1
    assert dialog.get_by_role("button", name="Detect USB sensor").is_enabled()
    assert posts == [{"workflow_start": {"device_id": "MG24-0002"}}]


def test_reset_reregister_renders_verified_identity_and_ignores_stronger_wrong_rssi(dashboard_page):
    page, posts = dashboard_page
    posts.append({"identity_ui": True})
    page.get_by_role("button", name="Close").click()
    page.click('[data-node-id="MG24-0002"] .mg-module-sensor-card__toggle')
    page.get_by_role("button", name="Reset and Re-register").click()
    page.locator("#reset-reregister-dialog").wait_for(state="visible")
    page.get_by_role("button", name="Scan for reset sensor").click()
    page.get_by_text("Verified physical sensor", exact=True).wait_for()
    assert page.get_by_text("Different sensor", exact=True).is_visible()
    assert page.get_by_text("RSSI -18 dBm (informational)").is_visible()
    provision = page.get_by_role("button", name="Provision This Sensor")
    assert provision.is_enabled()
    assert page.get_by_text("Expected onboarding identity: 7e6d1066…54a8").is_visible()
    posts.remove({"identity_ui": True})


@pytest.mark.parametrize("item,phase", [
    (None, "pending"),
    ({"commissioning_state": "incompatible", "commissioning_eligible": False, "compatible": False}, "classified"),
    ({"commissioning_state": "assigned_elsewhere", "commissioning_eligible": False, "compatible": True,
      "assigned_node_id": "MG24-0002", "reported_node_id": "MG24-0002"}, "classified"),
    ({"commissioning_state": "registered_here", "commissioning_eligible": False, "compatible": True,
      "assigned_node_id": "MG24-0002", "reported_node_id": "MG24-0002"}, "classified"),
    ({"commissioning_state": "unknown", "commissioning_eligible": False, "compatible": True}, "classified"),
    ({"commissioning_state": "state_unavailable", "commissioning_eligible": False, "compatible": True}, "classified"),
])
def test_ineligible_rendered_states_have_no_action_or_submission(dashboard_page, item, phase):
    page, posts = dashboard_page
    render(page, item, phase)
    assert_no_commissioning_action(page, posts)


def test_connected_node_renders_live_sensor_inputs_in_disclosure(dashboard_page):
    page, _posts = dashboard_page
    page.locator("#node-dialog").evaluate("dialog => dialog.close()")
    toggle = page.locator(".mg-module-sensor-card__toggle")
    details = page.locator(".mg-module-sensor-card__details")
    assert toggle.get_attribute("aria-expanded") == "false"
    assert details.is_hidden()
    assert page.get_by_text("XIAO MG24 Sense 01", exact=True).is_visible()
    assert page.locator(".sensor-summary__identity .equipment-id").is_visible()
    assert page.get_by_role("button", name="Open Sensor").is_hidden()
    assert page.get_by_role("button", name="Configure", exact=True).is_hidden()
    card_width = page.locator(".mg-module-sensor-card").bounding_box()["width"]
    list_width = page.locator("#node-list").bounding_box()["width"]
    assert abs(card_width - list_width) <= 2
    toggle.click()
    page.get_by_role("tab", name="Live Inputs").click()
    assert page.get_by_text("Acceleration X", exact=True).is_visible()
    assert page.get_by_text("0.896 g (gravity)", exact=True).is_visible()
    assert page.get_by_text("0.28 °/s (degrees per second)", exact=True).is_visible()
    assert page.get_by_text("415 ADC counts", exact=True).is_visible()
    assert page.get_by_text("Quality: good", exact=False).first.is_visible()
    channels = page.locator(".live-input-grid .channel").evaluate_all(
        "nodes => nodes.map(node => node.dataset.channel)"
    )
    assert channels == ["acceleration_x", "angular_velocity_x", "battery_voltage", "analog_0", "analog_1"]


def test_sensor_disclosure_toggles_with_mouse_and_keyboard(dashboard_page):
    page, posts = dashboard_page
    page.locator("#node-dialog").evaluate("dialog => dialog.close()")
    toggle = page.locator(".mg-module-sensor-card__toggle")
    details = page.locator(".mg-module-sensor-card__details")
    assert toggle.get_attribute("aria-controls") == details.get_attribute("id")
    toggle.press("Enter")
    assert toggle.get_attribute("aria-expanded") == "true" and details.is_visible()
    assert toggle.evaluate("node => document.activeElement === node")
    toggle.press("Space")
    assert toggle.get_attribute("aria-expanded") == "false" and details.is_hidden()
    assert toggle.evaluate("node => document.activeElement === node")
    assert posts == []


def test_sensor_disclosures_use_single_expanded_row_and_unique_ids(dashboard_page):
    page, _posts = dashboard_page
    page.locator("#node-dialog").evaluate("dialog => dialog.close()")
    page.evaluate("""
      state.nodes.push({...state.nodes[0], node_id: 'MG24-0003', display_name: 'Loading dock sensor'});
      state.readings['MG24-0003'] = [];
      renderNodes();
    """)
    toggles = page.locator(".mg-module-sensor-card__toggle")
    details = page.locator(".mg-module-sensor-card__details")
    cards = page.locator(".mg-module-sensor-card")
    assert toggles.count() == details.count() == 2
    assert len(set(details.evaluate_all("nodes => nodes.map(node => node.id)"))) == 2
    collapsed_boxes = cards.evaluate_all(
        "nodes => nodes.map(node => ({width: node.getBoundingClientRect().width, height: node.getBoundingClientRect().height}))"
    )
    assert all(60 <= box["height"] <= 90 for box in collapsed_boxes)
    assert len({round(box["width"]) for box in collapsed_boxes}) == 1
    toggles.nth(0).click()
    assert toggles.nth(0).get_attribute("aria-expanded") == "true"
    assert toggles.nth(1).get_attribute("aria-expanded") == "false"
    assert details.nth(0).is_visible() and details.nth(1).is_hidden()
    toggles.nth(1).click()
    assert toggles.nth(0).get_attribute("aria-expanded") == "false"
    assert toggles.nth(1).get_attribute("aria-expanded") == "true"
    assert details.nth(0).is_hidden() and details.nth(1).is_visible()


def test_collapsed_sensor_keeps_latest_telemetry_and_rerender_has_one_handler(dashboard_page):
    page, _posts = dashboard_page
    page.locator("#node-dialog").evaluate("dialog => dialog.close()")
    toggle = page.locator(".mg-module-sensor-card__toggle")
    assert toggle.get_attribute("aria-expanded") == "false"
    page.evaluate("""
      state.readings['MG24-0002'][2].normalized_value = 0.777;
      renderNodes(); renderNodes();
    """)
    toggle = page.locator(".mg-module-sensor-card__toggle")
    assert toggle.get_attribute("aria-expanded") == "false"
    toggle.click()
    assert page.get_by_text("0.777 g (gravity)", exact=True).is_visible()
    assert toggle.get_attribute("aria-expanded") == "true"
    page.evaluate("renderNodes()")
    assert page.locator(".mg-module-sensor-card__toggle").get_attribute("aria-expanded") == "true"
    page.locator(".mg-module-sensor-card__toggle").click()
    assert page.locator(".mg-module-sensor-card__toggle").get_attribute("aria-expanded") == "false"


def test_sensor_disclosure_mobile_and_reduced_motion(dashboard_page):
    page, _posts = dashboard_page
    page.set_viewport_size({"width": 390, "height": 844})
    toggle = page.locator(".mg-module-sensor-card__toggle")
    assert toggle.bounding_box()["width"] <= 390
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.emulate_media(reduced_motion="reduce")
    duration = page.locator(".mg-module-sensor-card__chevron").evaluate(
        "node => getComputedStyle(node).transitionDuration"
    )
    assert duration == "0s"


def test_sensor_disclosure_visual_states(dashboard_page):
    page, _posts = dashboard_page
    page.locator("#node-dialog").evaluate("dialog => dialog.close()")
    page.evaluate("""
      state.nodes.push({...state.nodes[0], node_id: 'MG24-0003', display_name: 'Loading dock sensor'});
      state.readings['MG24-0003'] = [];
      renderNodes();
    """)
    output = os.getenv("MG24_VISUAL_OUTPUT")
    if output:
        destination = Path(output)
        destination.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=destination / "desktop-light-collapsed.png", full_page=True)
        page.locator(".mg-module-sensor-card__toggle").first.focus()
        page.screenshot(path=destination / "desktop-light-keyboard-focus.png", full_page=True)
        page.locator(".mg-module-sensor-card__toggle").first.click()
        page.screenshot(path=destination / "desktop-light-expanded.png", full_page=True)
        page.select_option("#shell-theme-select", "gateway-dark")
        page.screenshot(path=destination / "desktop-dark-expanded.png", full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(250)
        page.screenshot(path=destination / "mobile-dark-expanded.png", full_page=True)
        page.locator(".mg-module-sensor-card__toggle").first.click()
        page.screenshot(path=destination / "mobile-dark-collapsed.png", full_page=True)

    assert page.locator(".mg-module-sensor-card").count() == 2


def test_authoritative_assignment_clears_stale_unassigned_action(dashboard_page):
    page, posts = dashboard_page
    unassigned = {"address": "88:0F:62:37:6A:58", "temporary_id": "unassigned:88:0f:62:37:6a:58",
                  "commissioning_state": "unassigned", "commissioning_eligible": True,
                  "compatible": True, "action": "commission"}
    render(page, unassigned)
    page.fill("#new-node-id", "MG24-TEST")
    page.fill("#new-node-name", "Test node")
    assert page.locator("#provision-node-button").is_visible()
    assert page.locator("#provision-node-button").is_enabled()

    assigned = {**unassigned, "temporary_id": None, "commissioning_state": "assigned_elsewhere",
                "commissioning_eligible": False, "assigned_node_id": "MG24-0002",
                "reported_node_id": "MG24-0002", "action": "recovery_or_import"}
    render(page, assigned)
    assert page.input_value("#new-node-id") == ""
    assert page.input_value("#new-node-name") == ""
    assert_no_commissioning_action(page, posts)


def test_unassigned_action_waits_for_fields_and_submits_exactly_once(dashboard_page):
    page, posts = dashboard_page
    item = {"address": "AA:BB:CC:DD:EE:01", "temporary_id": "unassigned:aa:bb:cc:dd:ee:01",
            "commissioning_state": "unassigned", "commissioning_eligible": True,
            "compatible": True, "action": "commission"}
    render(page, item)
    button = page.locator("#provision-node-button")
    assert button.is_visible() and button.is_disabled()
    page.fill("#new-node-id", "MG24-TEST")
    page.fill("#new-node-name", "Test node")
    assert button.is_enabled()
    button.click()
    page.wait_for_timeout(100)
    assert len(posts) == 1


def test_invalidating_a_validated_draft_hides_apply_and_clears_confirmation(dashboard_page):
    page, posts = dashboard_page
    page.evaluate(
        "state.draftId = 'server-issued-draft'; "
        "document.querySelector('#apply-button').hidden = false; "
        "document.querySelector('#apply-button').disabled = false; "
        "document.querySelector('#apply-confirmed').checked = true"
    )
    page.locator("#wizard-display-name").evaluate(
        "input => { input.value = 'changed after validation'; input.dispatchEvent(new Event('input', {bubbles: true})); }"
    )
    apply = page.locator("#apply-button")
    assert page.evaluate("state.draftId") is None
    assert apply.is_hidden() and apply.is_disabled()
    assert not page.locator("#apply-confirmed").is_checked()
    page.locator("#wizard-form").evaluate(
        "form => form.dispatchEvent(new SubmitEvent('submit', {bubbles: true, cancelable: true}))"
    )
    page.wait_for_timeout(50)
    assert posts == []


def test_failed_installation_does_not_offer_blind_retry(dashboard_page):
    page, _posts = dashboard_page
    page.evaluate(
        "state.installations = [{installation_id: 'failed-draft', node_id: 'MG24-0002', "
        "device_id: 'A0001-00', display_name: 'Test_PDM', interface_id: 'IMU0', "
        "sensor_profile_id: 'seeed.xiao_mg24_sense.accelerometer', sensor_profile_version: '1.0.0', "
        "provisioning_state: 'failed', calibration_status: 'profile_configured', "
        "verification_status: 'failed', provisioning_error: 'ValueError', location: '', description: ''}]"
    )
    page.evaluate("openDetail('failed-draft')")
    page.wait_for_timeout(100)
    retry = page.locator('[data-action="retry-installation"]')
    assert retry.is_hidden() and retry.is_disabled()


def test_shared_shell_navigation_and_theme_are_accessible(dashboard_page):
    page, _posts = dashboard_page
    page.locator("#node-dialog").evaluate("dialog => dialog.close()")
    assert page.locator("#shell-sidebar").get_attribute("aria-label") == "Gateway modules"
    assert page.locator(".mg-shell__skip-link").get_attribute("href") == "#module-content"
    links = page.locator("[data-module-target]")
    assert links.count() == 4
    page.locator('[data-module-target="devices"]').click()
    assert page.locator('[data-module-target="devices"]').get_attribute("aria-current") == "page"
    page.select_option("#shell-theme-select", "gateway-dark")
    assert page.locator("html").get_attribute("data-theme") == "gateway-dark"
