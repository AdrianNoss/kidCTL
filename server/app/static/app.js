// Service Worker registrieren (für Installation als App und Web Push).
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
