# Frontend module template

The gateway dashboard uses one server-rendered application shell for every
module. `templates/module_template/base.html` owns the document, responsive
sidebar, top bar, status region, content landmark, footer, and shared assets.
Individual pages extend that template and provide only their module content.
The overview and physical-device grid live at `/`; installations and system
health are separate modules at `/installations` and `/system-health`.

## Structure

```text
gateway/app/
├── templates/module_template/
│   ├── base.html
│   └── sidebar.html
├── static/css/module_template/
│   ├── base.css
│   ├── layouts.css
│   └── themes/gateway.css
└── static/js/module_template/shell.js
```

Shared shell selectors use the `mg-shell` namespace. Module-specific styles
must be scoped beneath `.mg-shell` and must not redefine global `body`,
`header`, `button`, or form-control behavior. Themes override semantic custom
properties rather than copying the complete component stylesheet.

## Adding a module

1. Add a semantic section with a stable ID and `data-module` value, or create a
   template that extends `module_template/base.html`.
2. Add navigation with `data-module-target`; use `url_for` for server routes.
3. Keep API and device state in the owning module script. The shell owns only
   responsive navigation, active navigation state, and the allowlisted theme.
4. Reuse `module-hero`, `module-panel`, and the dashboard cards before adding a
   new layout primitive.
5. Provide a heading, landmark, keyboard focus order, loading state, empty
   state, and error state.
6. Add a rendered-browser test for interactive behavior.

Sidebar actions delegate to existing dashboard actions. They do not duplicate
firmware, commissioning, telemetry, or configuration state.

Physical sensor cards use the delegated disclosure helper in
`static/js/module_template/sensor_disclosure.js`. Cards render collapsed on a
full page load, preserve their individual open state during an in-page
telemetry render, and expose technical details only through a native button
whose `aria-expanded` and controlled region remain synchronized.

# Vibration condition monitoring

The overview keeps vibration analytics inside each expanded sensor disclosure;
it is not a separate application or navigation module. `vibration_monitoring.js`
uses the existing `api()` client and renders dependency-free SVG charts with the
shared shell theme variables. Data is fetched only for an expanded sensor and is
cached for five seconds. The 15-minute, one-hour, and six-hour controls apply to
all charts together.

Operator wording must preserve the backend states `BASELINE_PENDING`, `NORMAL`,
`ELEVATED`, `SIGNIFICANT_CHANGE`, `INSUFFICIENT_DATA`, and `INVALID`. The
baseline similarity score describes agreement with that sensor/installation's
frozen baseline; it is not machine-health percentage or failure probability.
Acceleration RMS, peak, and dominant amplitude use g; dominant frequency uses
Hz; crest factor and kurtosis are dimensionless. Gyroscope data remains angular
velocity, not angular acceleration.

Keep the disclosure visible in the overview:

> Relative condition monitoring — not calibrated severity

The longer explanation must state that these are not calibrated ISO severity
measurements or automatic fault diagnoses. Invalid current windows hide current
metric values. Unsupported protocol-1.0 sensors receive a bounded empty state,
and stale state comes from the latest-window API rather than a client-only timer.
