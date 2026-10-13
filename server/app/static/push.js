// Web Push auf diesem Gerät aktivieren/deaktivieren (Seite /settings).
(function () {
  const box = document.getElementById("push");
  if (!box) return;
  const status = document.getElementById("push-status");
  const btnOn = document.getElementById("push-on");
  const btnOff = document.getElementById("push-off");
  const csrf = box.dataset.csrf;
  const say = (t) => { status.textContent = t; };

  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
    say("Dieser Browser unterstützt keine Push-Benachrichtigungen. Auf dem iPhone: Seite erst zum Home-Bildschirm hinzufügen und von dort öffnen.");
    return;
  }

  function keyBytes(b64) {
    const pad = "=".repeat((4 - (b64.length % 4)) % 4);
    const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
  }

  async function post(url, body) {
    const r = await fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error("HTTP " + r.status);
  }

  async function current() {
    const reg = await navigator.serviceWorker.ready;
    return reg.pushManager.getSubscription();
  }

  async function show() {
    const sub = await current();
    btnOn.hidden = !!sub;
    btnOff.hidden = !sub;
    if (Notification.permission === "denied") say("Benachrichtigungen sind im Browser blockiert – bitte in den Website-Einstellungen erlauben.");
    else say(sub ? "Auf diesem Gerät aktiv." : "Auf diesem Gerät nicht aktiv.");
    if (sub) post("/push/subscribe", sub.toJSON()).catch(() => {}); // Abo serverseitig auffrischen
  }

  btnOn.addEventListener("click", async () => {
    try {
      if (await Notification.requestPermission() !== "granted") return show();
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.subscribe({
        userVisibleOnly: true, applicationServerKey: keyBytes(box.dataset.key),
      });
      await post("/push/subscribe", sub.toJSON());
      location.reload();
    } catch (e) { say("Aktivieren fehlgeschlagen: " + e.message); }
  });

  btnOff.addEventListener("click", async () => {
    try {
      const sub = await current();
      if (sub) {
        await post("/push/unsubscribe", { endpoint: sub.endpoint });
        await sub.unsubscribe();
      }
      location.reload();
    } catch (e) { say("Deaktivieren fehlgeschlagen: " + e.message); }
  });

  show();
})();
