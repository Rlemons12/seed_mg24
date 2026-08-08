import json
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).parents[2]
TEMPLATE = ROOT / "gateway/app/templates/index.html"
STATIC = ROOT / "gateway/app/static"


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
            elif path.endswith("/static/styles.css"):
                request.fulfill(path=STATIC / "styles.css", content_type="text/css")
            elif path.endswith("/static/onboarding_state.js"):
                request.fulfill(path=STATIC / "onboarding_state.js", content_type="application/javascript")
            elif path.endswith("/static/app.js"):
                request.fulfill(path=STATIC / "app.js", content_type="application/javascript")
            elif path.endswith("/api/nodes"):
                request.fulfill(status=200, content_type="application/json", body=json.dumps([{
                    "node_id": "MG24-0002", "display_name": "XIAO MG24 Sense 01",
                    "connection_status": "connected", "compatibility_status": "compatible",
                    "firmware_version": "0.1.0", "protocol_version": "1.0.0",
                }]))
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
                request.fulfill(path=TEMPLATE, content_type="text/html")
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


def test_connected_node_renders_live_sensor_inputs_and_clear_empty_deployment_message(dashboard_page):
    page, _posts = dashboard_page
    assert page.get_by_text("Acceleration X", exact=True).is_visible()
    assert page.get_by_text("0.896 g (gravity)", exact=True).is_visible()
    assert page.get_by_text("0.28 °/s (degrees per second)", exact=True).is_visible()
    assert page.get_by_text("415 ADC counts", exact=True).is_visible()
    assert page.get_by_text("Quality: good", exact=False).first.is_visible()
    assert page.get_by_text("No optional equipment deployments", exact=False).is_visible()
    assert page.get_by_text("No attached sensors have been installed.", exact=True).count() == 0
    channels = page.locator(".live-input-grid .channel").evaluate_all(
        "nodes => nodes.map(node => node.dataset.channel)"
    )
    assert channels == ["acceleration_x", "angular_velocity_x", "battery_voltage", "analog_0", "analog_1"]


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
