from pathlib import Path

ROOT = Path(__file__).parents[2]
TEMPLATES = ROOT / "gateway" / "app" / "templates"
STATIC = ROOT / "gateway" / "app" / "static"


def test_dashboard_uses_one_shared_module_shell():
    index = (TEMPLATES / "index.html").read_text(encoding="utf-8")
    base = (TEMPLATES / "module_template" / "base.html").read_text(encoding="utf-8")
    sidebar = (TEMPLATES / "module_template" / "sidebar.html").read_text(encoding="utf-8")

    assert index.startswith('{% extends "module_template/base.html" %}')
    assert 'include "module_template/sidebar.html"' in base
    assert 'id="module-content"' in base
    assert 'aria-label="Primary navigation"' in sidebar
    assert "href=\"/" not in sidebar


def test_module_styles_and_script_are_namespaced_and_bounded():
    base_css = (STATIC / "css" / "module_template" / "base.css").read_text(encoding="utf-8")
    module_css = (STATIC / "styles.css").read_text(encoding="utf-8")
    shell_js = (STATIC / "js" / "module_template" / "shell.js").read_text(encoding="utf-8")
    disclosure_js = (STATIC / "js" / "module_template" / "sensor_disclosure.js").read_text(encoding="utf-8")

    assert ".mg-shell__sidebar" in base_css
    assert ".mg-shell button" in module_css
    assert "\nbutton {" not in module_css
    assert "allowedThemes" in shell_js
    assert "innerHTML" not in shell_js
    assert "eval(" not in shell_js
    assert "mg-module-sensor-card__toggle" in disclosure_js
    assert "addEventListener(\"click\"" in disclosure_js
    assert "innerHTML" not in disclosure_js
