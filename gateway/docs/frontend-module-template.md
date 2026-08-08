# Frontend module template

The gateway dashboard uses one server-rendered application shell for every
module. `templates/module_template/base.html` owns the document, responsive
sidebar, top bar, status region, content landmark, footer, and shared assets.
Individual pages extend that template and provide only their module content.

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
