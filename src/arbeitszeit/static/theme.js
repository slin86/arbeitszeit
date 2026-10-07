// Farbschema: gespeicherte Wahl (hell/dunkel) oder Systemeinstellung. Läuft vor dem Rendern (kein Aufblitzen).
(function () {
  var KEY = "az-theme";
  var root = document.documentElement;
  function stored() { try { return localStorage.getItem(KEY); } catch (e) { return null; } }
  function system() { return window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"; }
  function apply(t) {
    root.setAttribute("data-theme", t);
    document.querySelectorAll("[data-theme-toggle]").forEach(function (b) {
      b.textContent = t === "dark" ? "☀️" : "🌙";
      b.setAttribute("aria-label", t === "dark" ? "Helles Design aktivieren" : "Dunkles Design aktivieren");
      b.title = b.getAttribute("aria-label");
    });
  }
  apply(stored() || system());
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-theme-toggle]");
    if (!b) return;
    var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
    try { localStorage.setItem(KEY, next); } catch (err) {}
    apply(next);
  });
  // Nach htmx-Seitenwechseln Knopfsymbol neu setzen; Systemwechsel nur ohne gespeicherte Wahl folgen
  ["htmx:afterSwap", "htmx:afterSettle", "htmx:load"].forEach(function (ev) { document.addEventListener(ev, function () { apply(root.getAttribute("data-theme")); }); });
  document.addEventListener("DOMContentLoaded", function () { apply(root.getAttribute("data-theme")); });
  if (window.matchMedia) matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () { if (!stored()) apply(system()); });
})();
