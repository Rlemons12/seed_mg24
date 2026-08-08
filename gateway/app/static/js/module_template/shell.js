(() => {
  "use strict";

  const root = document.documentElement;
  const body = document.body;
  const toggle = document.querySelector("#shell-nav-toggle");
  const sidebar = document.querySelector("#shell-sidebar");
  const scrim = document.querySelector("#shell-scrim");
  const theme = document.querySelector("#shell-theme-select");
  const links = [...document.querySelectorAll("[data-module-target]")];
  const allowedThemes = new Set(["gateway", "gateway-dark"]);

  function setNavigation(open) {
    body.classList.toggle("is-nav-open", open);
    toggle?.setAttribute("aria-expanded", String(open));
    if (scrim) scrim.hidden = !open;
    if (!open) toggle?.focus({ preventScroll: true });
  }

  function activate(target) {
    links.forEach((link) => {
      const active = link.dataset.moduleTarget === target;
      link.classList.toggle("is-active", active);
      if (active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
  }

  toggle?.addEventListener("click", () => setNavigation(!body.classList.contains("is-nav-open")));
  scrim?.addEventListener("click", () => setNavigation(false));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && body.classList.contains("is-nav-open")) setNavigation(false);
  });
  sidebar?.addEventListener("click", (event) => {
    const link = event.target.closest("[data-module-target]");
    if (!link) return;
    activate(link.dataset.moduleTarget);
    if (matchMedia("(max-width: 860px)").matches) setNavigation(false);
  });

  document.querySelector('[data-shell-action="add-sensor"]')?.addEventListener("click", () => {
    document.querySelector("#add-button")?.click();
    if (matchMedia("(max-width: 860px)").matches) setNavigation(false);
  });
  document.querySelector('[data-shell-action="firmware"]')?.addEventListener("click", () => {
    document.querySelector("#add-button")?.click();
    requestAnimationFrame(() => document.querySelector("#usb-detect-button")?.focus());
  });

  const storedTheme = localStorage.getItem("seed-mg24-theme");
  const initialTheme = allowedThemes.has(storedTheme) ? storedTheme : "gateway";
  root.dataset.theme = initialTheme;
  if (theme) theme.value = initialTheme;
  theme?.addEventListener("change", () => {
    const selected = allowedThemes.has(theme.value) ? theme.value : "gateway";
    root.dataset.theme = selected;
    localStorage.setItem("seed-mg24-theme", selected);
  });

  const initialTarget = location.hash.slice(1);
  if (links.some((link) => link.dataset.moduleTarget === initialTarget)) activate(initialTarget);
})();
