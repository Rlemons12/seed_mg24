import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "gateway" / "app" / "static"
TEMPLATES = ROOT / "gateway" / "app" / "templates"


def run_node(script: str):
    return json.loads(subprocess.check_output(["node", "-e", script], cwd=ROOT, text=True))


def test_condition_states_score_progress_disclosure_and_empty_error_states_are_present():
    source = (STATIC / "vibration_monitoring.js").read_text(encoding="utf-8")
    template = (TEMPLATES / "index.html").read_text(encoding="utf-8")
    for state in ("BASELINE_PENDING", "NORMAL", "ELEVATED", "SIGNIFICANT_CHANGE", "INVALID"):
        assert state in source
    assert "Baseline similarity" in source
    assert "Baseline learning progress" in source
    assert "Relative condition monitoring — not calibrated severity" in source
    assert "Waiting for the first vibration summary from this sensor" in source
    assert "vibration monitoring requires production firmware using protocol 1.1.0" in source
    assert "Vibration condition data could not be loaded" in source
    assert "Data is stale" in source
    assert "vibration_monitoring.js" in template


def test_condition_state_mapping_and_current_vs_baseline_calculation():
    module = (STATIC / "vibration_monitoring.js").as_posix()
    result = run_node(f"""
      const ui = require({json.dumps(module)});
      const baseline = {{statistics: {{accel_rms_z_g: {{mean: 0.04}}}}}};
      const current = {{accel_rms_z_g: 0.08}};
      console.log(JSON.stringify({{
        states: ["BASELINE_PENDING","NORMAL","ELEVATED","SIGNIFICANT_CHANGE","INVALID"]
          .map(value => ui.statePresentation(value).state),
        comparison: ui.comparison(current, baseline, "rms", "z")
      }}));
    """)
    assert result["states"] == ["BASELINE_PENDING", "NORMAL", "ELEVATED", "SIGNIFICANT_CHANGE", "INVALID"]
    assert result["comparison"]["current"] == 0.08
    assert result["comparison"]["mean"] == 0.04
    assert result["comparison"]["percent"] == 100


def test_imu_sensor_fault_is_distinguished_from_waiting_for_first_summary():
    source = (STATIC / "vibration_monitoring.js").read_text(encoding="utf-8")
    module = (STATIC / "vibration_monitoring.js").as_posix()
    result = run_node(f"""
      const ui = require({json.dumps(module)});
      console.log(JSON.stringify({{
        fault: ui.hasImuSensorFault([
          {{channel:"temperature", quality:"good"}},
          {{channel:"acceleration_x", quality:"sensor_fault"}}
        ]),
        healthy: ui.hasImuSensorFault([{{channel:"angular_velocity_x", quality:"good"}}])
      }}));
    """)
    assert result == {"fault": True, "healthy": False}
    assert "IMU sensor fault: the accelerometer/gyroscope is not responding" in source
    assert "/readings/latest" in source


def test_all_requested_chart_metrics_map_valid_axis_history_and_ignore_invalid_windows():
    module = (STATIC / "vibration_monitoring.js").as_posix()
    result = run_node(f"""
      const ui = require({json.dumps(module)});
      const valid = {{validity:"valid", observed_at:"2026-08-12T12:00:00Z",
        accel_rms_x_g:1, accel_peak_x_g:2, crest_x:3, kurtosis_x:4,
        dominant_frequency_x_hz:5, dominant_amplitude_x_g:6}};
      const invalid = {{...valid, validity:"invalid", accel_rms_x_g:99}};
      const mapped = Object.fromEntries(Object.keys(ui.METRICS)
        .map(metric => [metric, ui.mapSeries([invalid, valid], metric, "x").map(point => point.value)]));
      console.log(JSON.stringify(mapped));
    """)
    assert result == {
        "rms": [1], "peak": [2], "crest": [3], "kurtosis": [4],
        "frequency": [5], "amplitude": [6],
    }


def test_history_ranges_are_utc_ordered_across_timezones_boundaries_and_dst():
    module = (STATIC / "vibration_monitoring.js").as_posix()
    result = run_node(f"""
      const ui = require({json.dumps(module)});
      const cases = [
        ["UTC", "2026-08-12T18:33:47.993Z"],
        ["America/Chicago", "2026-08-12T18:33:47.993Z"],
        ["America/Chicago", "2026-01-01T00:05:00.000Z"],
        ["America/Chicago", "2026-03-08T08:05:00.000Z"],
        ["America/Chicago", "2026-11-01T07:05:00.000Z"],
        ["Asia/Kolkata", "2027-01-01T00:05:00.000Z"],
      ];
      const ranges = cases.map(([tz, end]) => {{
        process.env.TZ = tz;
        const result = ui.buildHistoryRange("15m", Date.parse(end));
        return {{tz, ...result, duration: result.endMs - result.startMs}};
      }});
      console.log(JSON.stringify({{
        ranges,
        naiveUtc: ui.parseUtcTimestamp("2026-08-12T18:21:02.009"),
        explicitUtc: ui.parseUtcTimestamp("2026-08-12T18:21:02.009Z"),
      }}));
    """)
    assert all(item["start"] < item["end"] and item["duration"] == 15 * 60 * 1000 for item in result["ranges"])
    assert all(item["start"].endswith("Z") and item["end"].endswith("Z") for item in result["ranges"])
    assert result["naiveUtc"] == result["explicitUtc"]


def test_future_incremental_anchor_is_blocked_before_history_api_call():
    module = (STATIC / "vibration_monitoring.js").as_posix()
    result = run_node(f"""
      const ui = require({json.dumps(module)});
      let calls = 0;
      const existing = [{{id: 1, observed_at: "2999-01-01T00:00:00"}}];
      ui.fetchHistory(async () => {{ calls += 1; return {{items: []}}; }}, "NODE-1", "1h", existing)
        .then(rows => console.log(JSON.stringify({{calls, rows: rows.length,
          invalid: ui.buildHistoryRange("1h", Date.parse("2026-08-12T18:33:47.993Z"), existing[0].observed_at)}})));
    """)
    assert result == {"calls": 0, "rows": 1, "invalid": None}


def test_metric_help_covers_operator_metrics_and_accessible_interactions():
    source = (STATIC / "vibration_monitoring.js").read_text(encoding="utf-8")
    module = (STATIC / "vibration_monitoring.js").as_posix()
    result = run_node(f"""
      const ui = require({json.dumps(module)});
      console.log(JSON.stringify({{metrics:Object.keys(ui.METRICS), help:Object.keys(ui.HELP)}}));
    """)
    assert result["metrics"] == ["rms", "peak", "crest", "kurtosis", "frequency", "amplitude"]
    for key in ("conditionState", "conditionScore", "baselineCount", "baselineStatus", "validity",
                "lastUpdated", "baselineMean", "change", "algorithmVersion", "relearn"):
        assert key in result["help"]
    for phrase in ("What it is", "How calculated", "Why it matters", "Units", "Important limitation"):
        assert phrase in source
    assert 'setAttribute("aria-label", `Explain ${title}`)' in source
    assert 'setAttribute("aria-expanded", "false")' in source
    assert 'setAttribute("aria-describedby", id)' in source
    assert 'setAttribute("role", "tooltip")' in source
    assert 'event.key === "Escape"' in source
    assert 'button[data-metric-help]' in source
    assert "MG24VibrationMonitoring.load" not in source[source.index("function metricHelp"):source.index("function closeHelp")]


def test_relearn_baseline_is_confirmed_idempotent_and_refreshes_in_place():
    source = (STATIC / "vibration_monitoring.js").read_text(encoding="utf-8")
    assert 'relearn.dataset.relearnBaseline = "true"' in source
    assert "Type RELEARN BASELINE to confirm" in source
    assert 'confirmation: "RELEARN BASELINE"' in source
    assert "request_id: requestId" in source
    assert "/vibration/baseline/relearn" in source
    assert "Historical vibration readings are preserved" in source
    assert "Sensor identity, firmware, provisioning, configuration, and factory-reset state are not changed" in source
    assert "submit.disabled = true; cancel.disabled = true" in source
    assert "await view.reload?.(true)" in source
    assert "location.reload" not in source
    assert 'baseline?.status === "building" ? "Restart Baseline Learning"' in source
    assert "Baseline history" in source


def test_dashboard_css_keeps_charts_responsive_and_status_text_is_not_color_only():
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    source = (STATIC / "vibration_monitoring.js").read_text(encoding="utf-8")
    assert ".vibration-chart-grid" in css
    assert "@media (max-width: 650px)" in css
    assert 'setAttribute("role", "img")' in source
    assert 'setAttribute("aria-label"' in source
    assert "presentation.label" in source


def test_live_telemetry_does_not_rebuild_vibration_dashboard_or_duplicate_history_requests():
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    vibration = (STATIC / "vibration_monitoring.js").read_text(encoding="utf-8")
    assert "refreshLive().catch" in app
    assert "renderLiveInputs(inputGrid" in app
    assert "setTimeout(()=>{refreshTimer=null;refresh().catch" not in app
    assert "const inflight = new Map()" in vibration
    assert "inflight.get(cacheKey)" in vibration
    assert "container.dataset.vibrationRender" in vibration
    assert "reconcileChildren(container, next)" in vibration
    assert "views = new WeakMap()" in vibration
    assert 'current.axis = button.dataset.axis' in vibration
    assert 'if (!container.dataset.hasVibrationData &&' in vibration
    assert 'existing = [])' in vibration
    assert "Last successful values remain visible" in vibration
    render_body = vibration[vibration.index("function render(container"):vibration.index("async function fetchHistory")]
    assert "container.replaceChildren" not in render_body


def test_sensor_details_are_compact_tabbed_and_preserve_tab_state_during_live_refresh():
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    disclosure = (STATIC / "js" / "module_template" / "sensor_disclosure.js").read_text(encoding="utf-8")
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    for name in ("Overview", "Live Inputs", "Vibration", "Baseline", "Device Info"):
        assert name in app
    assert "const sensorTabState = new Map()" in app
    assert "sensorTabState.set(card.dataset.nodeId, name)" in app
    assert 'setAttribute("role", "tab")' in app
    assert 'setAttribute("role", "tabpanel")' in app
    assert 'event.key === "ArrowRight"' in app
    assert 'data-sensor-panel]:not([hidden])' in app
    assert "refreshConditionSummaries" in app
    assert "mg24:vibration-summary" in app
    assert "other.setAttribute(\"aria-expanded\", \"false\")" in disclosure
    assert ".sensor-tabs" in css and ".sensor-summary__status" in css
    assert 'el("button", "Identify Sensor")' in app
    assert "/identify" in app
    assert "blinked three times quickly and once slowly" in app
    assert 'el("button", "Use Low Power")' in app
    assert 'command:"MODE LOW_POWER"' in app
    assert 'el("button", "Go Live on Next Wake")' in app
    assert 'command:"MODE LIVE_NEXT_WAKE"' in app
    assert "Next low-power wake in" in app
    assert "return to Edge Summary automatically" in app
    assert "vibration windows are paused" in app
    assert ".live-input-grid--compact" in css


def test_sensor_card_shows_live_battery_voltage_without_inventing_percentage():
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    assert 'row.channel === "battery_voltage"' in app
    assert '`Battery ${value.toFixed(2)} V`' in app
    assert '"sensor-summary__battery"' in app
    assert 'card.querySelector(".sensor-summary__battery")' in app
    assert ".sensor-summary__battery" in css
    assert "Battery ${value.toFixed(2)} %" not in app
