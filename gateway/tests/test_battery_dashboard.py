from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "app"


def test_dashboard_has_dedicated_battery_tab_and_asset():
    app = (ROOT / "static" / "app.js").read_text()
    template = (ROOT / "templates" / "index.html").read_text()
    assert '["battery", "Battery"]' in app
    assert "MG24BatteryMonitoring.load" in app
    assert "battery_monitoring.js" in template


def test_battery_dashboard_labels_runtime_health_without_fake_percentage():
    source = (ROOT / "static" / "battery_monitoring.js").read_text()
    for label in (
        "Battery voltage", "Current charge runtime", "Previous charge runtime", "Average last 5 charges",
        "Average last 10 charges", "Baseline runtime", "Runtime health", "Battery trend",
        "Estimated recharge window", "Replacement status", "Charge-cycle history",
        "Estimated replacement window",
        "Voltage-based time until charge",
    ):
        assert label in source
    assert "not calibrated" in source
    assert "of baseline" in source
    assert "Battery charged now" in source
    assert "Battery replaced" in source


def test_battery_dashboard_uses_existing_dom_and_inline_svg_only():
    source = (ROOT / "static" / "battery_monitoring.js").read_text()
    assert 'createElementNS("http://www.w3.org/2000/svg"' in source
    assert "Chart.js" not in source
    assert "battery-cycle-table" in source


def test_wake_telemetry_refreshes_an_open_battery_tab():
    app = (ROOT / "static" / "app.js").read_text()
    assert "pendingBatteryRefreshIds.add(message.device_id)" in app
    assert "message.data?.channels?.battery_voltage" in app
    assert "MG24BatteryMonitoring.load(batteryMonitoring, node.node_id, api, true)" in app
