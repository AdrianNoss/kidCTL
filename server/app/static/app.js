// Service Worker registrieren (für Installation als App und Web Push).
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

// Rückfragen: data-confirm an Formular oder Knopf (keine Inline-Handler, die CSP verbietet sie).
document.addEventListener("submit", (ev) => {
  const text = ev.target.dataset && ev.target.dataset.confirm;
  if (text && !confirm(text)) ev.preventDefault();
});
document.addEventListener("click", (ev) => {
  const button = ev.target.closest && ev.target.closest("button[data-confirm]");
  if (button && !confirm(button.dataset.confirm)) ev.preventDefault();
});

// Darstellung: automatisch (System) → hell → dunkel. Gespeichert als Cookie, damit der Server die Seite gleich
// richtig ausliefert (kein Aufblitzen); ohne JavaScript gilt die Systemeinstellung.
(function () {
  const button = document.querySelector("[data-theme-toggle]");
  if (!button) return;
  const root = document.documentElement;
  const order = ["auto", "light", "dark"];
  const colors = { light: "#f3f3fa", dark: "#1e1f25" };
  function apply(theme) {
    root.dataset.theme = theme;
    const label = button.dataset["label" + theme[0].toUpperCase() + theme.slice(1)];
    button.title = label;
    button.setAttribute("aria-label", label);
    document.querySelectorAll('meta[name="theme-color"]').forEach((m) => m.remove());
    document.querySelector('meta[name="color-scheme"]').content = theme === "auto" ? "light dark" : theme;
    for (const scheme of theme === "auto" ? ["light", "dark"] : [theme]) {
      const meta = document.createElement("meta");
      meta.name = "theme-color";
      meta.content = colors[scheme];
      if (theme === "auto") meta.media = `(prefers-color-scheme: ${scheme})`;
      document.head.appendChild(meta);
    }
  }
  apply(order.includes(root.dataset.theme) ? root.dataset.theme : "auto");
  button.hidden = false;
  button.addEventListener("click", () => {
    const next = order[(order.indexOf(root.dataset.theme) + 1) % order.length];
    document.cookie = `kidctl_theme=${next}; path=/; max-age=31536000; SameSite=Lax` +
      (location.protocol === "https:" ? "; Secure" : "");
    apply(next);
  });
})();
