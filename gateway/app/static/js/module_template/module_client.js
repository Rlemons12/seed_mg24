(function (root) {
  "use strict";
  root.MG24ModuleClient = Object.freeze({
    async get(path) {
      const response = await fetch(path, { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`Request failed (${response.status})`);
      return response.json();
    },
    element(tag, text, className) {
      const node = document.createElement(tag);
      if (text !== undefined) node.textContent = text;
      if (className) node.className = className;
      return node;
    },
  });
}(typeof globalThis !== "undefined" ? globalThis : this));
