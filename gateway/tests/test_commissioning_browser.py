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
            elif path.endswith("/api/nodes") or path.endswith("/api/sensor-installations") or path.endswith("/api/sensor-profiles"):
                request.fulfill(status=200, content_type="application/json", body="[]")
            elif path == "http://dashboard.test/":
                request.fulfill(path=TEMPLATE, content_type="text/html")
            else:
                request.fulfill(status=404, body="")

        page.route("**/*", route)
        page.goto("http://dashboard.test/")
        page.click("#register-node-button")
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
