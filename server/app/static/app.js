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
